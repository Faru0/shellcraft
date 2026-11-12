"""queryGreyNoise — whether an IPv4 address scans the whole internet or is a known service (GreyNoise, key optional)."""

from __future__ import annotations

import os
from typing import Any

import _intel

from shellcraft.modkit import EnvSetting, ModuleError

SUMMARY = "Is an IPv4 address a known internet scanner or a known business service (GreyNoise, key optional)"
SPINNER_TEXT = "asking GreyNoise…"

SOURCE = "greynoise"
KINDS = ("ipv4",)  # the community API takes IPv4 only
KEY_VAR = "GREYNOISE_API_KEY"  # optional: it raises the daily limit
ENV_SETTINGS = [
    EnvSetting(KEY_VAR, "GreyNoise API key",
               "Optional. Without it GreyNoise answers a small number of lookups a day. The key of a free account "
               "raises the limit: sign in at https://viz.greynoise.io, open Account, copy the API key."),
]
API = "https://api.greynoise.io/v3/community/"
PRIVATE = "a private address: GreyNoise only watches the public internet"


def run(args: list[str], stdin: str) -> str:
    opts = _intel.parser("queryGreyNoise", "IP").parse_args(args)
    wanted = _intel.targets("queryGreyNoise", opts.targets, stdin, KINDS, "an IPv4 address")
    records = _intel.each("queryGreyNoise", SOURCE, wanted,
                          lambda ip: lookup(ip, timeout=opts.timeout, fresh=opts.fresh))
    return _intel.show(records, opts.output, _rows)


def lookup(indicator: str, *, timeout: float = 10.0, fresh: bool = False) -> dict[str, Any]:
    """One record for an IPv4 address (see _intel.record)."""
    indicator, kind = _intel.clean(indicator)
    if kind not in KINDS:
        raise ModuleError(f"queryGreyNoise: '{indicator}' is not an IPv4 address")
    if not _intel.is_public(indicator):
        return _intel.record(SOURCE, indicator, kind, "unknown", PRIVATE)  # nothing to ask: no request
    raw, cached = _intel.answer(SOURCE, indicator, lambda: _fetch(indicator, timeout), fresh)
    data = _data(raw or {})  # None: GreyNoise has nothing on it, which reads as "not seen, not known"
    verdict, summary, known_good = _judge(data)
    return _intel.record(SOURCE, indicator, kind, verdict, summary, known_good=known_good, cached=cached, data=data,
                         raw=raw)


def _fetch(ip: str, timeout: float) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if key := os.environ.get(KEY_VAR, "").strip():
        headers["key"] = key
    raw = _intel.get_json(API + ip, module="queryGreyNoise", service="GreyNoise", what=ip, headers=headers,
                          timeout=timeout, key_var=KEY_VAR)
    if not isinstance(raw, dict):
        raise ModuleError("queryGreyNoise: GreyNoise returned an unexpected response")
    return raw


def _data(raw: dict[str, Any]) -> dict[str, Any]:
    name = str(raw.get("name") or "")
    return {
        "noise": raw.get("noise") is True,
        "riot": raw.get("riot") is True,
        "classification": str(raw.get("classification") or "").lower(),
        "name": "" if name.lower() == "unknown" else name,
        "last_seen": _intel.day(raw.get("last_seen")),
        "link": str(raw.get("link") or ""),
        "message": str(raw.get("message") or ""),
    }


def _judge(data: dict[str, Any]) -> tuple[str, str, bool]:
    """(verdict, summary, known good). The first rule that fits wins: a malicious classification
    beats a known service, a known service beats having been seen scanning."""
    name, classification = data["name"], data["classification"]
    seen = f", last seen {data['last_seen']}" if data["last_seen"] else ""
    if classification == "malicious":
        who = f", {name}" if name else ""
        return "malicious", f"seen scanning the internet, classified malicious{who}{seen}", False
    if data["riot"]:
        return "clean", f"a known service: {name}" if name else "a known business service", True
    if classification == "benign":
        return "clean", f"a known benign scanner: {name}" if name else "a known benign scanner", False
    if classification == "suspicious" or data["noise"]:
        said = "classified suspicious" if classification == "suspicious" else "not classified"
        return "suspicious", f"seen scanning the internet, {said}{seen}", False
    return "unknown", "not seen scanning the internet, not a known service", False


def _rows(data: dict[str, Any]) -> list[tuple[str, Any]]:
    return list(data.items())
