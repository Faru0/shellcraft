"""queryRdap — who an IP address or a domain is registered to, from the registries (RDAP, no key)."""

from __future__ import annotations

from typing import Any

import _intel

from shellcraft.modkit import ModuleError

SUMMARY = "Who an IP or a domain is registered to: owner, registrar, dates, abuse contact (RDAP, no key)"
SPINNER_TEXT = "asking the registry…"

SOURCE = "rdap"
KINDS = ("ipv4", "ipv6", "domain")
KEY_VAR = ""  # no key
API = "https://rdap.org/"  # sends each question on to the registry that holds the answer
NEW_DAYS = 30  # a domain younger than this is worth a second look


def run(args: list[str], stdin: str) -> str:
    opts = _intel.parser("queryRdap", "TARGET").parse_args(args)
    wanted = _intel.targets("queryRdap", opts.targets, stdin, KINDS, "an IP address or a domain name")
    records = _intel.each("queryRdap", SOURCE, wanted,
                          lambda target: lookup(target, timeout=opts.timeout, fresh=opts.fresh))
    return _intel.show(records, opts.output, _rows)


def lookup(indicator: str, *, timeout: float = 10.0, fresh: bool = False) -> dict[str, Any]:
    """One record for an IP address or a domain (see _intel.record)."""
    indicator, kind = _intel.clean(indicator)
    if kind not in KINDS:
        raise ModuleError(f"queryRdap: '{indicator}' is not an IP address or a domain name")
    raw, cached = _intel.answer(SOURCE, indicator, lambda: _fetch(indicator, kind, timeout), fresh)
    if raw is None:
        return _intel.record(SOURCE, indicator, kind, "unknown", "the registries have no record of it", cached=cached)
    data = _domain(raw) if kind == "domain" else _network(raw)
    verdict, summary = _judge(data, kind)
    return _intel.record(SOURCE, indicator, kind, verdict, summary, cached=cached, data=data, raw=raw)


def _fetch(indicator: str, kind: str, timeout: float) -> Any:
    """The registry's answer. A host name (www.example.com) is not registered itself, so on "not
    known" the name is asked for again without its first part, down to two parts."""
    name = indicator
    while True:
        path = f"domain/{name}" if kind == "domain" else f"ip/{name}"
        try:
            return _intel.get_json(API + path, module="queryRdap", service="RDAP", what=indicator, timeout=timeout,
                                   headers={"Accept": "application/rdap+json, application/json"})
        except _intel.NotKnown:
            if kind != "domain" or name.count(".") < 2:
                raise
            name = name.split(".", 1)[1]


# ── reading an RDAP answer ───────────────────────────────────────────────────

def _entities(node: dict) -> list[dict]:
    """Every entity in the answer, the nested ones too (the abuse contact sits inside the registrar)."""
    found = []
    for entity in node.get("entities") or []:
        if isinstance(entity, dict):
            found.append(entity)
            found += _entities(entity)
    return found


def _card(entity: dict, field: str) -> str:
    """One field of an entity's vCard: fn (the name), org, email, tel."""
    card = entity.get("vcardArray")
    for item in (card[1] if isinstance(card, list) and len(card) > 1 else []):
        if isinstance(item, list) and len(item) >= 4 and item[0] == field and isinstance(item[3], str) and item[3]:
            return item[3].removeprefix("tel:").removeprefix("mailto:")
    return ""


def _role(raw: dict, role: str, field: str = "fn") -> str:
    for entity in _entities(raw):
        if role in (entity.get("roles") or []):
            value = _card(entity, field) or (_card(entity, "org") if field == "fn" else "")
            if value:
                return value
    return ""


def _event(raw: dict, action: str) -> str:
    for event in raw.get("events") or []:
        if isinstance(event, dict) and event.get("eventAction") == action:
            return _intel.day(event.get("eventDate"))
    return ""


def _domain(raw: dict) -> dict[str, Any]:
    registrar = next((e for e in _entities(raw) if "registrar" in (e.get("roles") or [])), {})
    ids = [p.get("identifier") for p in registrar.get("publicIds") or [] if isinstance(p, dict)]
    created = _event(raw, "registration")
    return {
        "domain": str(raw.get("ldhName") or "").lower(),
        "registrar": _card(registrar, "fn"),
        "registrar_id": ids[0] if ids else "",
        "registrant": _role(raw, "registrant"),
        "created": created,
        "age_days": _intel.days_since(created),
        "changed": _event(raw, "last changed"),
        "expires": _event(raw, "expiration"),
        "status": [s for s in raw.get("status") or [] if isinstance(s, str)],
        "nameservers": [str(n.get("ldhName") or "").lower() for n in raw.get("nameservers") or []
                        if isinstance(n, dict) and n.get("ldhName")],
        "dnssec": bool((raw.get("secureDNS") or {}).get("delegationSigned")),
        "abuse_email": _role(raw, "abuse", "email"),
        "abuse_phone": _role(raw, "abuse", "tel"),
    }


def _network(raw: dict) -> dict[str, Any]:
    blocks = [f"{c.get('v4prefix') or c.get('v6prefix')}/{c.get('length')}" for c in raw.get("cidr0_cidrs") or []
              if isinstance(c, dict) and (c.get("v4prefix") or c.get("v6prefix"))]
    span = f"{raw.get('startAddress')} - {raw.get('endAddress')}" if raw.get("startAddress") else ""
    return {
        "network": ", ".join(blocks) or span,
        "name": raw.get("name") or "",
        "handle": raw.get("handle") or "",
        "owner": _role(raw, "registrant") or _role(raw, "administrative"),
        "country": raw.get("country") or "",
        "type": raw.get("type") or "",
        "created": _event(raw, "registration"),
        "changed": _event(raw, "last changed"),
        "abuse_email": _role(raw, "abuse", "email"),
        "abuse_phone": _role(raw, "abuse", "tel"),
    }


def _judge(data: dict[str, Any], kind: str) -> tuple[str, str]:
    """RDAP says who and since when, not good or bad. The one thing it can flag: a brand new domain."""
    if kind == "domain":
        age = data.get("age_days")
        facts = [f"registered {data['created']}" if data.get("created") else "", data.get("registrar") or ""]
        if age is not None and age < NEW_DAYS:
            when = "today" if age == 0 else f"{age} day{'s' if age != 1 else ''} ago"
            return "suspicious", f"registered {when}" + (f" at {data['registrar']}" if data.get("registrar") else "")
        return "unknown", ", ".join(f for f in facts if f)
    owner = data.get("owner") or data.get("name") or ""
    return "unknown", ", ".join(f for f in (owner, data.get("network"), data.get("country")) if f)


def _rows(data: dict[str, Any]) -> list[tuple[str, Any]]:
    return [(field, value) for field, value in data.items() if field != "age_days"]
