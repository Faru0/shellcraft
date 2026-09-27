"""myip — show your public IP, or geolocate any IP, via ipconfig.io."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from core.modkit import ArgParser, ModuleError

SUMMARY = "Show your public IP or look up IP geolocation/ASN (ipconfig.io)"
SPINNER_TEXT = "asking ipconfig.io…"

API = "https://ipconfig.io"

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
    url = API + path + (f"?{urllib.parse.urlencode({'ip': ip})}" if ip else "")
    request = urllib.request.Request(url, headers={"Accept": "application/json",
                                                   "User-Agent": "ShellCraft-myip/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # The API reports bad input as JSON: {"status": 400, "error": "could not parse IP: x"}
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("error") or exc.reason
        except (ValueError, AttributeError):
            detail = exc.reason
        raise ModuleError(f"myip: {detail}") from None
    except urllib.error.URLError as exc:
        raise ModuleError(f"myip: cannot reach ipconfig.io: {exc.reason}") from None
    except TimeoutError:
        raise ModuleError(f"myip: ipconfig.io timed out after {timeout:g}s") from None
    except json.JSONDecodeError:
        raise ModuleError("myip: ipconfig.io returned an unexpected (non-JSON) response") from None


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
