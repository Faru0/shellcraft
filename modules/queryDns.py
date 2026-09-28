"""queryDns — DNS records, IP owners and netblocks for a domain, via the DnsDumpster API."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from core.modkit import ArgParser, EnvSetting, ModuleError

SUMMARY = "Look up a domain's DNS records, IPs, ASNs and netblocks (DnsDumpster API)"
SPINNER_TEXT = "asking DnsDumpster…"

API = "https://api.dnsdumpster.com/domain/"
KEY_VAR = "DNSDUMPSTER_API_KEY"
ENV_SETTINGS = [
    EnvSetting(KEY_VAR, "DnsDumpster API key",
               "Free key from your dashboard at https://dnsdumpster.com (sign in, then look under API)."),
]

TYPES = ("a", "mx", "ns", "txt", "cname")
OUTPUTS = ("table", "hosts", "ips", "json")
RATE_DELAY = 2.0  # the API allows 1 request per 2 seconds

# Seam for tests: stubbed so the rate-limit wait doesn't slow them down.
sleep = time.sleep


def run(args: list[str], stdin: str) -> str:
    parser = ArgParser("queryDns")
    parser.add_argument("domains", nargs="*", metavar="DOMAIN")
    parser.add_argument("-t", "--type", action="append", metavar="TYPE")
    parser.add_argument("-o", "--output", default="table", choices=OUTPUTS)
    parser.add_argument("--page", type=int, metavar="N")
    parser.add_argument("--timeout", type=float, default=30.0)
    opts = parser.parse_args(args)

    types = _types(opts.type)
    if opts.page is not None and opts.page < 1:
        raise ModuleError("queryDns: --page must be 1 or more")
    domains = [_domain(d) for d in (opts.domains or _stdin_domains(stdin))]
    if not domains:
        raise ModuleError("queryDns: give a domain, e.g. `queryDns example.com` (or pipe domains in, one per line)")
    key = os.environ.get(KEY_VAR, "").strip()
    if not key:
        raise ModuleError(f"queryDns: no DnsDumpster API key. Get a free one at https://dnsdumpster.com, "
                          f"then run: settings {KEY_VAR}")

    results = {}
    for i, domain in enumerate(dict.fromkeys(domains)):
        if i:
            sleep(RATE_DELAY)
        results[domain] = _get(domain, key, opts.page, opts.timeout)

    if opts.output == "json":
        data = {d: {t: r.get(t, []) for t in types} | _extra(r) for d, r in results.items()}
        return json.dumps(data if len(data) > 1 else next(iter(data.values())), indent=2, ensure_ascii=False) + "\n"
    if opts.output == "hosts":
        return _unique(h for r in results.values() for t in types for h in _hosts(r, t))
    if opts.output == "ips":
        return _unique(ip.get("ip", "") for r in results.values() for t in types
                       for rec in _records(r, t) for ip in rec.get("ips") or [])
    return "\n".join(_table(d, r, types, len(results) > 1) for d, r in results.items())


def _types(chosen: list[str] | None) -> list[str]:
    if not chosen:
        return list(TYPES)
    types = []
    for item in chosen:
        for t in item.lower().split(","):
            if t not in TYPES:
                raise ModuleError(f"queryDns: unknown record type '{t}' (choose from: {', '.join(TYPES)})")
            if t not in types:
                types.append(t)
    return types


def _stdin_domains(stdin: str) -> list[str]:
    return [ln.split()[0] for ln in stdin.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


def _domain(text: str) -> str:
    domain = text.strip().lower()
    if "://" in domain:
        domain = urllib.parse.urlsplit(domain).hostname or ""
    domain = domain.strip(".")
    if not domain or "." not in domain or any(c in domain for c in "/ @:"):
        raise ModuleError(f"queryDns: '{text}' is not a domain name")
    return domain


def _get(domain: str, key: str, page: int | None, timeout: float) -> dict:
    url = API + urllib.parse.quote(domain) + (f"?page={page}" if page else "")
    request = urllib.request.Request(url, headers={"X-API-Key": key, "Accept": "application/json",
                                                   "User-Agent": "ShellCraft-queryDns/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ModuleError(_http_error(exc, domain)) from None
    except urllib.error.URLError as exc:
        raise ModuleError(f"queryDns: cannot reach api.dnsdumpster.com: {exc.reason}") from None
    except TimeoutError:
        raise ModuleError(f"queryDns: DnsDumpster timed out after {timeout:g}s for {domain}") from None
    except json.JSONDecodeError:
        raise ModuleError("queryDns: DnsDumpster returned an unexpected (non-JSON) response") from None
    if not isinstance(data, dict):
        raise ModuleError("queryDns: DnsDumpster returned an unexpected response")
    if data.get("error"):
        raise ModuleError(f"queryDns: DnsDumpster: {data['error']} ({domain})")
    return data


def _http_error(exc: urllib.error.HTTPError, domain: str) -> str:
    try:
        detail = json.loads(exc.read().decode("utf-8")).get("error") or exc.reason
    except (ValueError, AttributeError):
        detail = exc.reason
    if exc.code in (401, 403):
        return (f"queryDns: DnsDumpster rejected the API key ({exc.code} {detail}). "
                f"Check it on your dnsdumpster.com dashboard, then run: settings {KEY_VAR}")
    if exc.code == 429:
        return "queryDns: DnsDumpster rate limit hit (1 request per 2 seconds); wait a moment and retry"
    return f"queryDns: DnsDumpster HTTP {exc.code} for {domain}: {detail}"


def _records(result: dict, rtype: str) -> list[dict]:
    """a/mx/ns entries are {host, ips: [...]}; cname entries are normalized to the same shape."""
    items = result.get(rtype) or []
    if rtype == "txt":
        return []
    records = []
    for item in items:
        if isinstance(item, dict):
            records.append(item)
        elif isinstance(item, str):
            records.append({"host": item})
    return records


def _hosts(result: dict, rtype: str) -> list[str]:
    return [str(r.get("host", "")) for r in _records(result, rtype)]


def _extra(result: dict) -> dict:
    return {"total_a_recs": result["total_a_recs"]} if "total_a_recs" in result else {}


def _unique(values) -> str:
    return "".join(f"{v}\n" for v in dict.fromkeys(v for v in values if v))


def _table(domain: str, result: dict, types: list[str], titled: bool) -> str:
    rows = []
    for rtype in (t for t in types if t != "txt"):
        for rec in _records(result, rtype):
            target = str(rec.get("target") or rec.get("value") or "")
            for ip in rec.get("ips") or [{}]:
                rows.append((rtype.upper(), str(rec.get("host", "")), str(ip.get("ip") or target),
                             _asn(ip), str(ip.get("country_code") or ip.get("country") or ""),
                             str(ip.get("asn_range", ""))))
    txt = [str(t) for t in result.get("txt") or []] if "txt" in types else []
    out = f"# {domain}\n" if titled else ""
    if rows:
        header = ("TYPE", "HOST", "IP", "ASN", "CC", "NETBLOCK")
        widths = [max(len(r[i]) for r in rows + [header]) for i in range(len(header))]
        out += "".join("  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip() + "\n" for row in [header] + rows)
    if txt:
        out += ("\n" if rows else "") + "".join(f"TXT  {t}\n" for t in txt)
    if not rows and not txt:
        out += "(no records found)\n"
    return out


def _asn(ip: dict) -> str:
    asn, name = str(ip.get("asn") or ""), str(ip.get("asn_name") or "")
    if not asn:
        return name
    return f"AS{asn} {name}".strip() if not asn.upper().startswith("AS") else f"{asn} {name}".strip()
