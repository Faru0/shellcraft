"""queryShodan — what an IP shows to the internet: ports, services, known vulnerabilities (Shodan, optional key)."""

from __future__ import annotations

import os
import urllib.parse
from typing import Any

import _intel

from shellcraft.modkit import EnvSetting, ModuleError

SUMMARY = "What an IP shows to the internet: open ports, services, known vulnerabilities (Shodan, key optional)"
SPINNER_TEXT = "asking Shodan…"

SOURCE = "shodan"
KINDS = ("ipv4", "ipv6")
KEY_VAR = "SHODAN_API_KEY"  # optional: without it the keyless InternetDB answers
ENV_SETTINGS = [
    EnvSetting(KEY_VAR, "Shodan API key",
               "Optional. Without it you get the open ports, host names, tags and known vulnerabilities. With it "
               "you also get the services with their versions, the organisation and the location. Copy it from "
               "https://account.shodan.io (the free account's key works for host lookups)."),
]

API = "https://api.shodan.io/shodan/host/"  # needs the key, in the URL
INTERNETDB = "https://internetdb.shodan.io/"  # free, no key, fewer fields
BACK_ENDS = {"api": "Shodan API (with your key)", "internetdb": "InternetDB (free, no key)"}
BAD_TAGS = ("c2", "malware", "compromised")  # Shodan saw it serve or carry malware
ODD_TAGS = ("tor", "scanner", "proxy")  # not bad by itself, worth a second look in your logs
API_WAIT = 1  # seconds: the API takes one request a second
PORTS_SHOWN = 8  # in the summary line
LIST_SHOWN = 10  # vulns and cpes in the table; json has them all


def run(args: list[str], stdin: str) -> str:
    parser = _intel.parser("queryShodan", "IP")
    parser.add_argument("--internetdb", action="store_true")
    opts = parser.parse_args(args)
    wanted = _intel.targets("queryShodan", opts.targets, stdin, KINDS, "an IP address")
    records = _intel.each("queryShodan", SOURCE, wanted,
                          lambda ip: lookup(ip, timeout=opts.timeout, fresh=opts.fresh, internetdb=opts.internetdb))
    return _intel.show(records, opts.output, _rows)


def lookup(indicator: str, *, timeout: float = 10.0, fresh: bool = False, internetdb: bool = False) -> dict[str, Any]:
    """One record for an IP address (see _intel.record). `internetdb` asks the keyless InternetDB
    even when a key is set."""
    indicator, kind = _intel.clean(indicator)
    if kind not in KINDS:
        raise ModuleError(f"queryShodan: '{indicator}' is not an IP address")
    if not _intel.is_public(indicator):
        return _intel.record(SOURCE, indicator, kind, "unknown",
                             "a private address: Shodan only sees the public internet")
    key = "" if internetdb else os.environ.get(KEY_VAR, "").strip()
    via = "api" if key else "internetdb"
    raw, cached = _intel.answer(SOURCE if key else "internetdb", indicator,
                                lambda: _fetch(indicator, key, timeout), fresh)
    if raw is None:
        return _intel.record(SOURCE, indicator, kind, "unknown", "Shodan has not seen it", cached=cached,
                             data={"via": via})
    data = _host(raw, indicator) if key else _internetdb(raw, indicator)
    verdict, summary = _judge(data)
    return _intel.record(SOURCE, indicator, kind, verdict, summary, cached=cached, data=data, raw=raw)


def _fetch(ip: str, key: str, timeout: float) -> dict:
    """Shodan's answer: from the API with a key, else from InternetDB. The key travels in the URL,
    which get_json never shows."""
    if key:
        try:
            raw = _intel.get_json(f"{API}{ip}?{urllib.parse.urlencode({'key': key})}", module="queryShodan",
                                  service="Shodan", what=ip, timeout=timeout, key_var=KEY_VAR)
        except _intel.RateLimited as exc:
            if exc.wait is not None:
                raise
            # the API takes one request a second and says no more than "429": a list waits that second
            raise _intel.RateLimited("queryShodan: Shodan rate limit reached (one lookup a second); "
                                     "try again in a moment", API_WAIT) from None
    else:
        raw = _intel.get_json(INTERNETDB + ip, module="queryShodan", service="Shodan InternetDB", what=ip,
                              timeout=timeout, key_var="")
    if not isinstance(raw, dict):
        raise ModuleError(f"queryShodan: {'Shodan' if key else 'Shodan InternetDB'} returned an unexpected response")
    return raw


# ── reading an answer ────────────────────────────────────────────────────────

def _texts(*lists: Any) -> list[str]:
    """The text items of the given lists, each once, in the order first seen."""
    out: list[str] = []
    for items in lists:
        for item in (items if isinstance(items, (list, tuple, dict)) else []):
            if isinstance(item, str) and item.strip() and item.strip() not in out:
                out.append(item.strip())
    return out


def _ports(*lists: Any) -> list[int]:
    return sorted({p for ports in lists for p in (ports if isinstance(ports, list) else [])
                   if isinstance(p, int) and not isinstance(p, bool)})


def _blank(ip: str, via: str) -> dict[str, Any]:
    """Every field of `data`, empty: both back ends give the same keys."""
    return {"ip": ip, "ports": [], "hostnames": [], "org": "", "isp": "", "asn": "", "os": "", "country": "",
            "city": "", "tags": [], "vulns": [], "cpes": [], "last_update": "", "services": [], "via": via}


def _internetdb(raw: dict, ip: str) -> dict[str, Any]:
    return _blank(ip, "internetdb") | {
        "ports": _ports(raw.get("ports")),
        "hostnames": _texts(raw.get("hostnames")),
        "tags": _texts(raw.get("tags")),
        "vulns": sorted(_texts(raw.get("vulns"))),
        "cpes": sorted(_texts(raw.get("cpes"))),
    }


def _service(banner: dict) -> str:
    """One service as text: `22/tcp OpenSSH 8.9`, or just `22/tcp` when Shodan did not name it."""
    where = "/".join(str(banner[f]) for f in ("port", "transport") if banner.get(f))
    return " ".join(str(part) for part in (where, banner.get("product"), banner.get("version")) if part)


def _host(raw: dict, ip: str) -> dict[str, Any]:
    banners = [b for b in raw.get("data") or [] if isinstance(b, dict) and isinstance(b.get("port"), int)]
    banners.sort(key=lambda b: (b["port"], str(b.get("transport") or "")))
    return _blank(ip, "api") | {
        "ip": str(raw.get("ip_str") or ip),
        "ports": _ports(raw.get("ports"), [b["port"] for b in banners]),
        "hostnames": _texts(raw.get("hostnames")),
        "org": str(raw.get("org") or ""),
        "isp": str(raw.get("isp") or ""),
        "asn": str(raw.get("asn") or ""),
        "os": str(raw.get("os") or ""),
        "country": str(raw.get("country_name") or ""),
        "city": str(raw.get("city") or ""),
        "tags": _texts(raw.get("tags"), *(b.get("tags") for b in banners)),
        "vulns": sorted(_texts(raw.get("vulns"), *(b.get("vulns") for b in banners))),
        "cpes": sorted(_texts(*(b.get("cpe23") or b.get("cpe") for b in banners))),
        "last_update": _intel.day(raw.get("last_update")),
        "services": _texts([_service(b) for b in banners]),
    }


def _judge(data: dict[str, Any]) -> tuple[str, str]:
    """Shodan says what a host shows, not good or bad. What it can flag is a tag: malware and
    command servers are `malicious`, Tor nodes, scanners and open proxies are `suspicious`."""
    tags = [t.lower() for t in data["tags"]]
    bad = [t for t in tags if t in BAD_TAGS]
    odd = [t for t in tags if t in ODD_TAGS]
    flagged = bad or odd
    ports, vulns = data["ports"], data["vulns"]
    facts = [f"tagged {' and '.join(flagged)}" if flagged else ""]
    if ports:
        shown = ", ".join(str(p) for p in ports[:PORTS_SHOWN]) + (", ..." if len(ports) > PORTS_SHOWN else "")
        facts.append(f"{len(ports)} open port{'s' if len(ports) != 1 else ''} ({shown})")
    else:
        facts.append("no open ports seen")
    others = [t for t in data["tags"] if t.lower() not in flagged]
    facts.append(f"tags: {', '.join(others)}" if others else "")
    facts.append(f"{len(vulns)} known vulnerabilit{'ies' if len(vulns) != 1 else 'y'}" if vulns else "")
    verdict = "malicious" if bad else "suspicious" if odd else "unknown"
    return verdict, ", ".join(f for f in facts if f)


def _rows(data: dict[str, Any]) -> list[tuple[str, Any]]:
    rows: list[tuple[str, Any]] = []
    for field, value in data.items():
        if field == "ip":
            continue  # the indicator line has it
        if field == "via":
            value = BACK_ENDS.get(value, value)
        elif field in ("vulns", "cpes") and len(value) > LIST_SHOWN:
            value = f"{', '.join(value[:LIST_SHOWN])} and {len(value) - LIST_SHOWN} more (-o json lists them all)"
        rows.append((field, value))
    return rows
