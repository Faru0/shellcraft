"""enrich — ask every lookup service that fits an indicator, and sum the answers up in one verdict."""

from __future__ import annotations

import contextvars
import importlib
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import _intel

from shellcraft.modkit import ArgParser, Cancelled, ModuleError, check_cancel

SUMMARY = "Look an IP, domain, URL, hash or email address up in every service that fits, and give one verdict"
SPINNER_TEXT = "asking the lookup services…"

# The lookup modules, in the order their lines are shown: facts first, opinions after. Each keeps
# the contract in _intel.py (SOURCE, KINDS, KEY_VAR, lookup()); one that is missing is left out.
MODULES = ("ip2geo", "queryRdap", "queryShodan", "queryVirusTotal", "queryAbuseIPDB", "queryGreyNoise")
OUTPUTS = ("text", "json")
WORKERS = 4  # services asked at the same time, for one indicator
WHAT = "an indicator (an IP address, a domain, a URL, a file hash or an email address)"


def run(args: list[str], stdin: str) -> str:
    parser = ArgParser("enrich")
    parser.add_argument("targets", nargs="*", metavar="INDICATOR")
    parser.add_argument("--only", action="append", metavar="SOURCES")
    parser.add_argument("-o", "--output", default="text", choices=OUTPUTS)
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--timeout", type=float, default=10.0)
    opts = parser.parse_args(args)

    sources = _chosen(sources_found(), opts.only)
    wanted = _intel.targets("enrich", opts.targets, stdin, _intel.KINDS, WHAT)
    # what each source does about its rate limit in this run (in a list, a short one is waited out)
    patience = {name: _intel.Patience(len(wanted) > 1) for name in sources}
    results = []
    for indicator, kind in wanted:
        check_cancel()
        results.append(_enrich(indicator, kind, sources, opts, patience))
    if opts.output == "json":
        return _intel.dump(results)
    return _text(results)


def sources_found() -> dict[str, Any]:
    """The lookup modules that are installed, by their short name: {"rdap": <module queryRdap>, ...}."""
    found = {}
    for name in MODULES:
        try:
            module = importlib.import_module(f"{__package__ or 'shellcraft_modules'}.{name}")
        except Exception:  # noqa: BLE001, S112 (a missing or broken lookup module must not take the others down)
            continue
        if callable(getattr(module, "lookup", None)) and getattr(module, "SOURCE", "") and hasattr(module, "KINDS"):
            found[module.SOURCE] = module
    return found


def _chosen(sources: dict[str, Any], only: list[str] | None) -> dict[str, Any]:
    if not only:
        return sources
    names = [name for item in only for name in (part.strip().lower() for part in item.split(",")) if name]
    for name in names:
        if name not in sources:
            raise ModuleError(f"enrich: unknown source '{name}' (choose from: {', '.join(sources)})")
    return {name: module for name, module in sources.items() if name in names}


def plan(indicator: str, kind: str, sources: dict[str, Any]) -> tuple[list[tuple[str, str, str]], str]:
    """Which source is asked what: ([(source, value, kind of value)], why nothing can be asked).
    A source that takes the indicator's kind gets the indicator. For a URL the others get its
    host, for an email address its domain. An address that is not on the internet is never sent
    out, and neither is a URL on such an address."""
    host = _intel.host_of(indicator, kind) if kind in ("url", "email") else ""
    host_kind = _intel.kind_of(host) if host else None
    asks = []
    private = False
    for name, module in sources.items():
        if host_kind in ("ipv4", "ipv6") and not _intel.is_public(host):
            private = True  # a URL on your own network: its path is nobody's business either
            continue
        if kind in module.KINDS:
            value, value_kind = indicator, kind
        elif host_kind in module.KINDS:
            value, value_kind = host, str(host_kind)
        else:
            continue
        if value_kind in ("ipv4", "ipv6") and not _intel.is_public(value):
            private = True
            continue
        asks.append((name, value, value_kind))
    if asks:
        return asks, ""
    if private or (kind in ("ipv4", "ipv6") and not _intel.is_public(indicator)):
        return [], "a private address: the lookup services only know the public internet"
    return [], f"no source takes {_A.get(kind, kind)}"


_A = {"ipv4": "an IPv4 address", "ipv6": "an IPv6 address", "domain": "a domain", "url": "a URL",
      "md5": "an MD5 hash", "sha1": "a SHA-1 hash", "sha256": "a SHA-256 hash", "email": "an email address"}


def _enrich(indicator: str, kind: str, sources: dict[str, Any], opts: Any,
            patience: dict[str, Any]) -> dict[str, Any]:
    asks, nothing = plan(indicator, kind, sources)
    answers: list[dict[str, Any]] = []
    if asks:
        with ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="shellcraft:enrich") as pool:
            # each lookup in a copy of this context, so it sees the cancel event of this command
            futures = [pool.submit(contextvars.copy_context().run, _ask, sources[name], name, value, value_kind,
                                   opts, patience[name]) for name, value, value_kind in asks]
            answers = [future.result() for future in futures]
    records = [a for a in answers if "skipped" not in a]
    skipped = [{"source": a["source"], "why": a["skipped"]} for a in answers if "skipped" in a]
    verdict, reasons = judge(records)
    if not records:
        needs = f": {', '.join(s['source'] for s in skipped)} has no API key" if skipped else ""
        reasons = [nothing or f"no source could be asked{needs}"]
    return {"indicator": indicator, "kind": kind, "verdict": verdict, "reasons": reasons,
            "sources": [{k: v for k, v in r.items() if k != "raw"} for r in records], "skipped": skipped}


def _ask(module: Any, source: str, value: str, kind: str, opts: Any, patience: Any) -> dict[str, Any]:
    """One source's record. Its failure is a record with an `error`, never the command's error."""
    def lookup() -> dict[str, Any]:
        return dict(module.lookup(value, timeout=opts.timeout, fresh=opts.fresh))

    try:
        return dict(patience.ask(lookup))
    except Cancelled:
        raise
    except _intel.NoKey:
        return {"source": source, "skipped": f"no key: settings {module.KEY_VAR}"}
    except ModuleError as exc:
        said = str(exc).removeprefix(f"{module.__name__.rpartition('.')[2]}: ")
    except Exception as exc:  # noqa: BLE001 (a broken lookup module must not take the others down)
        said = f"{type(exc).__name__}: {exc}"
    failed = _intel.record(source, value, kind)
    failed["error"] = said
    return failed


def judge(records: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """One verdict from the sources' records, and the lines that say why.

    malicious   a source calls it malicious (and no source knows it as a good service)
    suspicious  a source calls it suspicious, or one says malicious while another knows it as good
    clean       a source knows it as a good service, or sources looked and none flags it
    unknown     no source has a judgement (facts such as owner and location are not one)
    """
    said = [r for r in records if not r.get("error")]

    def why(wanted: list[dict[str, Any]]) -> list[str]:
        return [f"{r['source']}: {r['summary']}" if r.get("summary") else str(r["source"]) for r in wanted]

    bad = [r for r in said if r["verdict"] == "malicious"]
    good = [r for r in said if r.get("known_good")]
    odd = [r for r in said if r["verdict"] == "suspicious"]
    fine = [r for r in said if r["verdict"] == "clean"]
    if bad and good:
        return "suspicious", why(bad) + [f"but {line}" for line in why(good)]
    if bad:
        return "malicious", why(bad) + why(odd)
    if good:
        return "clean", why(good)
    if odd:
        return "suspicious", why(odd)
    if fine:
        return "clean", [f"not flagged by {', '.join(str(r['source']) for r in fine)}"]
    if said:
        return "unknown", ["no source has a judgement on it"]
    return "unknown", ["no source answered"] if records else []


def _text(results: list[dict[str, Any]]) -> str:
    blocks = []
    skipped: dict[str, str] = {}
    for result in results:
        lines = [f"VERDICT {result['verdict']}  {result['indicator']}  {'; '.join(result['reasons'])}"]
        width = max((len(r["source"]) for r in result["sources"]), default=0)
        for rec in result["sources"]:
            about = f"for {rec['indicator']}: " if rec["indicator"] != result["indicator"] else ""
            if rec.get("error"):
                state, said = "error", rec["error"]
            else:
                state, said = rec["verdict"], rec["summary"] + (" (stored)" if rec.get("cached") else "")
            lines.append(f"SOURCE: {rec['source'].ljust(width)}  {state.ljust(10)}  {about}{said}".rstrip())
        blocks.append("\n".join(lines) + "\n")
        for skip in result["skipped"]:
            skipped.setdefault(skip["source"], skip["why"])
    if skipped:  # once for the whole run, not under every indicator
        blocks.append("".join(f"SKIPPED: {source} ({why})\n" for source, why in skipped.items()))
    return "\n".join(blocks)
