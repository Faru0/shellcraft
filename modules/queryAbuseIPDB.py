"""queryAbuseIPDB — how often an IP address was reported for abuse, and how recently (AbuseIPDB, needs a key)."""

from __future__ import annotations

import urllib.parse
from typing import Any

import _intel

from shellcraft.modkit import EnvSetting, ModuleError

SUMMARY = "How often an IP address was reported for abuse: score, reports, ISP, Tor (AbuseIPDB, needs a key)"
SPINNER_TEXT = "asking AbuseIPDB…"

SOURCE = "abuseipdb"
KINDS = ("ipv4", "ipv6")
KEY_VAR = "ABUSEIPDB_API_KEY"
KEY_WHERE = 'A free account at https://www.abuseipdb.com gives you one, under "API".'
ENV_SETTINGS = [
    EnvSetting(KEY_VAR, "AbuseIPDB API key",
               "Free key from your account at https://www.abuseipdb.com (sign in, then look under API). "
               "It allows 1,000 checks a day."),
]
API = "https://api.abuseipdb.com/api/v2/check"
DAYS = 90  # how far back reports count, unless --days says otherwise
MAX_DAYS = 365  # the longest window the API takes
MALICIOUS_AT = 75  # abuse score from which an address is `malicious`
SUSPICIOUS_AT = 25  # abuse score from which an address is `suspicious`
PRIVATE = "a private address: AbuseIPDB only tracks public ones"


def run(args: list[str], stdin: str) -> str:
    parser = _intel.parser("queryAbuseIPDB", "IP")
    parser.add_argument("--days", type=int, default=DAYS, metavar="N")
    opts = parser.parse_args(args)
    _window(opts.days)
    wanted = _intel.targets("queryAbuseIPDB", opts.targets, stdin, KINDS, "an IP address")
    _key()  # once, before any lookup: a missing key is one error, not one per target
    records = _intel.each("queryAbuseIPDB", SOURCE, wanted,
                          lambda ip: lookup(ip, timeout=opts.timeout, fresh=opts.fresh, days=opts.days))
    return _intel.show(records, opts.output, _rows)


def lookup(indicator: str, *, timeout: float = 10.0, fresh: bool = False, days: int = DAYS) -> dict[str, Any]:
    """One record for an IP address (see _intel.record). `days` is how far back reports count."""
    indicator, kind = _intel.clean(indicator)
    if kind not in KINDS:
        raise ModuleError(f"queryAbuseIPDB: '{indicator}' is not an IP address")
    _window(days)
    key = _key()
    if not _intel.is_public(indicator):  # nothing to ask: the answer is known, and a check is not spent on it
        return _intel.record(SOURCE, indicator, kind, "unknown", PRIVATE, data={"public": False})
    # The answer depends on the window, so every window but the usual one is stored under its own name.
    stored_as = indicator if days == DAYS else f"{indicator}?days={days}"
    raw, cached = _intel.answer(SOURCE, stored_as, lambda: _fetch(indicator, key, days, timeout), fresh)
    if raw is None:
        return _intel.record(SOURCE, indicator, kind, "unknown", "AbuseIPDB has nothing on it", cached=cached)
    data = _read(raw, days)
    verdict, summary, known_good = _judge(data)
    return _intel.record(SOURCE, indicator, kind, verdict, summary, known_good=known_good, cached=cached, data=data,
                         raw=raw)


def _window(days: int) -> None:
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_DAYS:
        raise ModuleError(f"queryAbuseIPDB: --days takes 1 to {MAX_DAYS}")


def _key() -> str:
    return _intel.need_key("queryAbuseIPDB", KEY_VAR, "AbuseIPDB", KEY_WHERE)


def _fetch(ip: str, key: str, days: int, timeout: float) -> dict[str, Any]:
    query = urllib.parse.urlencode({"ipAddress": ip, "maxAgeInDays": days})  # an IPv6 address has colons
    raw = _intel.get_json(f"{API}?{query}", module="queryAbuseIPDB", service="AbuseIPDB", what=ip, timeout=timeout,
                          headers={"Key": key, "Accept": "application/json"}, key_var=KEY_VAR)
    if not isinstance(raw, dict) or not isinstance(raw.get("data"), dict):
        raise ModuleError("queryAbuseIPDB: AbuseIPDB returned an unexpected response")  # not stored
    return raw


# ── reading an AbuseIPDB answer ──────────────────────────────────────────────

def _number(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _read(raw: dict[str, Any], days: int) -> dict[str, Any]:
    info = raw["data"]
    return {
        "score": _number(info.get("abuseConfidenceScore")),
        "reports": _number(info.get("totalReports")),
        "users": _number(info.get("numDistinctUsers")),
        "last_reported": _intel.day(info.get("lastReportedAt")),
        "country": info.get("countryCode") or "",
        "usage": info.get("usageType") or "",
        "isp": info.get("isp") or "",
        "domain": info.get("domain") or "",
        "hostnames": [h for h in info.get("hostnames") or [] if isinstance(h, str)],
        "tor": bool(info.get("isTor")),
        "whitelisted": bool(info.get("isWhitelisted")),  # null when AbuseIPDB has no opinion
        "public": info.get("isPublic") is not False,
        "days": days,
    }


def _count(number: int, word: str) -> str:
    return f"{number:,} {word}{'' if number == 1 else 's'}"


def _facts(data: dict[str, Any]) -> str:
    """abuse score 100, 1,234 reports from 300 users, last 2026-11-12"""
    text = f"abuse score {data['score']}, {_count(data['reports'], 'report')} from {_count(data['users'], 'user')}"
    return text + (f", last {data['last_reported']}" if data["last_reported"] else "")


def _judge(data: dict[str, Any]) -> tuple[str, str, bool]:
    """(verdict, summary, known good). The first rule that fits decides."""
    score, reports = data["score"], data["reports"]
    if not data["public"]:
        return "unknown", PRIVATE, False
    if data["whitelisted"]:
        return "clean", "on AbuseIPDB's whitelist" + (f", {_facts(data)}" if score or reports else ""), True
    if score >= SUSPICIOUS_AT:
        more = (["Tor exit"] if data["tor"] else []) + ([data["usage"]] if data["usage"] else [])
        return "malicious" if score >= MALICIOUS_AT else "suspicious", ", ".join([_facts(data), *more]), False
    if not score and not reports:
        window = "day" if data["days"] == 1 else f"{data['days']} days"
        return "clean", f"no reports in the last {window}", False
    return "clean", _facts(data), False


def _rows(data: dict[str, Any]) -> list[tuple[str, Any]]:
    return [(field, value) for field, value in data.items() if field != "public"]
