"""myip — show your public IP, or geolocate any IP, via ipconfig.io."""

from __future__ import annotations

import json
import re

import requests

from core.modkit import ArgParser, ModuleError

SUMMARY = "Show your public IP or look up IP geolocation/ASN (ipconfig.io)"
SPINNER_TEXT = "asking ipconfig.io…"

API = "https://ipconfig.io"
HEADERS = {"Accept": "application/json", "User-Agent": "ShellCraft-myip/0.1"}

# Display order for --info; optional fields only appear when the API knows them.
INFO_FIELDS = [
    "ip", "hostname", "country", "country_iso", "country_eu", "region_name", "region_code",
    "city", "zip_code", "latitude", "longitude", "metro_code", "time_zone", "asn", "asn_org",
    "ip_decimal",
]
FIELDS = INFO_FIELDS + ["coordinates"]


def run(args: list[str], stdin: str) -> str:
    parser = ArgParser("myip")
    parser.add_argument("ips", nargs="*")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("-i", "--info", action="store_true")
    mode.add_argument("-j", "--json", action="store_true")
    mode.add_argument("-f", "--field")
    mode.add_argument("-p", "--port", type=int)
    parser.add_argument("--timeout", type=float, default=10.0)
    opts = parser.parse_args(args)

    if opts.port is not None:
        if opts.ips:
            raise ModuleError("myip: --port only checks your own IP; drop the IP argument")
        if not 1 <= opts.port <= 65535:
            raise ModuleError("myip: --port must be between 1 and 65535")
        result = _get(f"/port/{opts.port}", None, opts.timeout)
        state = "reachable" if result.get("reachable") else "NOT reachable"
        return f"{result.get('ip')}:{result.get('port')} {state} from the internet\n"

    field = None
    if opts.field:
        field = opts.field.lower().replace("-", "_")
        if field not in FIELDS:
            raise ModuleError(f"myip: unknown field '{opts.field}' (choose from: {', '.join(FIELDS)})")

    targets = opts.ips or [ln.strip() for ln in stdin.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    if not targets:
        record = _get("/json", None, opts.timeout)
        if opts.json:
            return _dump(_clean(record))
        if opts.info:
            return _info(record)
        return f"{_value(record, field or 'ip')}\n"

    records = [_get("/json", ip, opts.timeout) for ip in targets]
    if opts.json:
        cleaned = [_clean(r) for r in records]
        return _dump(cleaned[0] if len(cleaned) == 1 else cleaned)
    if field:
        if len(records) == 1:
            return f"{_value(records[0], field)}\n"
        return "".join(f"{r.get('ip')}\t{_value(r, field)}\n" for r in records)
    return "\n".join(_info(r) for r in records)  # an IP was given: details are the useful answer


def _get(path: str, ip: str | None, timeout: float) -> dict:
    try:
        response = requests.get(API + path, params={"ip": ip} if ip else None,
                                headers=HEADERS, timeout=timeout)
    except requests.Timeout:  # before ConnectionError: ConnectTimeout is both
        raise ModuleError(f"myip: ipconfig.io timed out after {timeout:g}s") from None
    except requests.ConnectionError as exc:
        raise ModuleError(f"myip: cannot reach ipconfig.io: {_reason(exc)}") from None
    except requests.RequestException as exc:
        raise ModuleError(f"myip: request to ipconfig.io failed: {exc}") from None

    if not response.ok:
        # The API reports bad input as JSON: {"status": 400, "error": "could not parse IP: x"}
        try:
            detail = response.json().get("error")
        except (ValueError, AttributeError):
            detail = None
        raise ModuleError(f"myip: {detail or f'ipconfig.io returned {response.status_code} {response.reason}'}")

    try:
        record = response.json()
    except ValueError:
        record = None
    if not isinstance(record, dict):
        raise ModuleError("myip: ipconfig.io returned an unexpected (non-JSON) response")
    return record


def _reason(exc: requests.ConnectionError) -> str:
    # requests wraps urllib3's MaxRetryError; its .reason is the underlying socket/DNS/TLS error.
    # urllib3 prefixes some of these with the connection's repr: "<...HTTPSConnection object at 0x…>: ".
    cause = exc.args[0] if exc.args else exc
    return re.sub(r"^<[^>]*>: ", "", str(getattr(cause, "reason", None) or cause))


def _clean(record: dict) -> dict:
    # user_agent describes this client, not the looked-up IP.
    return {k: v for k, v in record.items() if k != "user_agent"}


def _value(record: dict, field: str) -> str:
    if field == "coordinates":
        if "latitude" in record and "longitude" in record:
            return f"{record['latitude']},{record['longitude']}"
        return ""
    value = record.get(field, "")
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _info(record: dict) -> str:
    rows = [(k, _value(record, k)) for k in INFO_FIELDS if k in record]
    width = max(len(k) for k, _ in rows)
    return "".join(f"{k.ljust(width)}  {v}\n" for k, v in rows)


def _dump(value) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"
