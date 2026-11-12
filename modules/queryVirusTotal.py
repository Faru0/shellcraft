"""queryVirusTotal — what the antivirus engines say about an IP, a domain, a URL or a file hash (VirusTotal, key)."""

from __future__ import annotations

import base64
from typing import Any

import _intel

from shellcraft.modkit import EnvSetting, ModuleError

SUMMARY = "What the antivirus engines say about an IP, a domain, a URL or a file hash (VirusTotal, needs a free key)"
SPINNER_TEXT = "asking VirusTotal…"

SOURCE = "virustotal"
KINDS = ("ipv4", "ipv6", "domain", "url", "md5", "sha1", "sha256")
KEY_VAR = "VIRUSTOTAL_API_KEY"
KEY_WHERE = 'A free account at https://www.virustotal.com gives you one: open your profile, then "API key".'
ENV_SETTINGS = [
    EnvSetting(KEY_VAR, "VirusTotal API key",
               'Free with an account at https://www.virustotal.com (your profile, then "API key"). '
               "It allows 4 lookups a minute and 500 a day."),
]

API = "https://www.virustotal.com/api/v3/"  # only reports are read: nothing is uploaded or sent in for a scan
GUI = "https://www.virustotal.com/gui/"  # the page a person opens
WHAT = "an IP address, a domain name, a URL or a file hash (MD5, SHA-1, SHA-256)"
MALICIOUS_AT = 5  # this many engines saying "malicious": the verdict is malicious
SUSPICIOUS_AT = 1  # this many engines saying "malicious": the verdict is suspicious
SUSPICIOUS_VOTES_AT = 2  # this many engines saying "suspicious": the verdict is suspicious too
LIMIT_WAIT = 60  # seconds: a free key allows 4 lookups a minute
LIMIT_TEXT = ("queryVirusTotal: VirusTotal rate limit reached (a free key allows 4 lookups a minute, 500 a day); "
              "try again in a minute")
PRIVATE = "a private address: VirusTotal only knows the public internet"
NAMES_SHOWN = 5  # a file is sent in under many names; the first few say enough


def run(args: list[str], stdin: str) -> str:
    opts = _intel.parser("queryVirusTotal", "INDICATOR").parse_args(args)
    wanted = _intel.targets("queryVirusTotal", opts.targets, stdin, KINDS, WHAT)
    _key()  # once, before any lookup: a missing key is one error, not one for every indicator
    records = _intel.each("queryVirusTotal", SOURCE, wanted,
                          lambda target: lookup(target, timeout=opts.timeout, fresh=opts.fresh))
    return _intel.show(records, opts.output, _rows)


def lookup(indicator: str, *, timeout: float = 10.0, fresh: bool = False) -> dict[str, Any]:
    """One record for an IP address, a domain, a URL or a file hash (see _intel.record)."""
    indicator, kind = _intel.clean(indicator)
    if kind not in KINDS:
        raise ModuleError(f"queryVirusTotal: '{indicator}' is not {WHAT}")
    key = _key()
    if kind in ("ipv4", "ipv6") and not _intel.is_public(indicator):  # nothing to ask, and a lookup is not spent on it
        return _intel.record(SOURCE, indicator, kind, "unknown", PRIVATE)
    raw, cached = _intel.answer(SOURCE, indicator, lambda: _fetch(indicator, kind, key, timeout), fresh)
    if raw is None:
        return _intel.record(SOURCE, indicator, kind, "unknown", "VirusTotal has never seen it", cached=cached)
    data = _read(raw, indicator, kind)
    verdict, summary = _judge(data)
    return _intel.record(SOURCE, indicator, kind, verdict, summary, cached=cached, data=data, raw=raw)


def _key() -> str:
    return _intel.need_key("queryVirusTotal", KEY_VAR, "VirusTotal", KEY_WHERE)


def _url_id(url: str) -> str:
    """How VirusTotal names a URL in its API: the URL in base64, without the padding."""
    return base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")


def _path(indicator: str, kind: str) -> str:
    if kind in ("ipv4", "ipv6"):
        return f"ip_addresses/{indicator}"
    if kind == "domain":
        return f"domains/{indicator}"
    if kind == "url":
        return f"urls/{_url_id(indicator)}"
    return f"files/{indicator}"


def _fetch(indicator: str, kind: str, key: str, timeout: float) -> Any:
    """The report VirusTotal already has. VirusTotal sends no Retry-After, so the wait is the free key's minute."""
    try:
        return _intel.get_json(API + _path(indicator, kind), module="queryVirusTotal", service="VirusTotal",
                               what=indicator, headers={"x-apikey": key, "Accept": "application/json"},
                               timeout=timeout, key_var=KEY_VAR)
    except _intel.RateLimited:
        raise _intel.RateLimited(LIMIT_TEXT, LIMIT_WAIT) from None


# ── reading a VirusTotal report ──────────────────────────────────────────────

def _part(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _number(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _texts(value: Any) -> list[str]:
    return [v for v in value if isinstance(v, str) and v] if isinstance(value, list) else []


def _categories(value: Any) -> list[str]:
    """`categories` is one answer per vendor ({"Sophos": "phishing", ...}): each answer once."""
    return list(dict.fromkeys(_texts(list(_part(value).values()))))


def _read(raw: Any, indicator: str, kind: str) -> dict[str, Any]:
    node = _part(_part(raw).get("data"))
    attrs = _part(node.get("attributes"))
    stats = _part(attrs.get("last_analysis_stats"))
    counts = {name: _number(stats.get(name)) or 0 for name in ("malicious", "suspicious", "harmless", "undetected")}
    data: dict[str, Any] = {
        **counts,
        "engines": sum(counts.values()) + (_number(stats.get("timeout")) or 0),
        "reputation": _number(attrs.get("reputation")),
        "tags": _texts(attrs.get("tags")),
        "last_analysis": _intel.day(attrs.get("last_analysis_date")),
    }
    if kind in ("ipv4", "ipv6"):
        page = f"ip-address/{indicator}"
        data |= {"as_owner": _text(attrs.get("as_owner")), "asn": _number(attrs.get("asn")),
                 "country": _text(attrs.get("country")), "network": _text(attrs.get("network"))}
    elif kind == "domain":
        page = f"domain/{indicator}"
        data |= {"registrar": _text(attrs.get("registrar")), "created": _intel.day(attrs.get("creation_date")),
                 "categories": _categories(attrs.get("categories"))}
    elif kind == "url":
        # The page takes VirusTotal's own name for the URL (a SHA-256, the `id` of the report) for certain.
        own = _text(node.get("id"))
        page = f"url/{own if _intel.kind_of(own) == 'sha256' else _url_id(indicator)}"
        data |= {"url": _text(attrs.get("url")), "final_url": _text(attrs.get("last_final_url")),
                 "title": _text(attrs.get("title")), "categories": _categories(attrs.get("categories"))}
    else:
        page = f"file/{indicator}"
        threat = _part(attrs.get("popular_threat_classification"))
        data |= {"threat_label": _text(threat.get("suggested_threat_label")),
                 "name": _text(attrs.get("meaningful_name")), "type": _text(attrs.get("type_description")),
                 "size": _number(attrs.get("size")), "first_seen": _intel.day(attrs.get("first_submission_date")),
                 "names": _texts(attrs.get("names"))[:NAMES_SHOWN], "sha256": _text(attrs.get("sha256")),
                 "md5": _text(attrs.get("md5")), "sha1": _text(attrs.get("sha1"))}
    data["link"] = GUI + page
    return data


def _judge(data: dict[str, Any]) -> tuple[str, str]:
    """The verdict from how many engines flag it, and one line that says so with the numbers."""
    bad, odd, engines = data["malicious"], data["suspicious"], data["engines"]
    if engines <= 0:
        return "unknown", "no engine has checked it yet"
    if bad >= MALICIOUS_AT:
        verdict = "malicious"
    elif bad >= SUSPICIOUS_AT or odd >= SUSPICIOUS_VOTES_AT:
        verdict = "suspicious"
    else:
        verdict = "clean"
    if bad:
        said = f"{bad} of {engines} engines call{'s' if bad == 1 else ''} it malicious"
        said += f", {odd} suspicious" if odd else ""
    elif odd:
        said = f"{odd} of {engines} engines call{'s' if odd == 1 else ''} it suspicious"
    else:
        said = f"none of {engines} engines flag it"
    checked = f"last checked {data['last_analysis']}" if data.get("last_analysis") else ""
    return verdict, ", ".join(part for part in (said, data.get("threat_label"), checked) if part)


def _rows(data: dict[str, Any]) -> list[tuple[str, Any]]:
    return list(data.items())
