"""ip2geo — geolocate IP addresses or domain names via ip-api.com (JSON endpoint, no key)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from core.modkit import ArgParser, ModuleError

SUMMARY = "Geolocate IPs or domains: country, city, ISP, ASN, proxy/hosting flags (ip-api.com)"
SPINNER_TEXT = "asking ip-api.com…"

API = "http://ip-api.com/json/"  # the free endpoint is HTTP only
FIELDS = [
    "query", "continent", "continentCode", "country", "countryCode", "region", "regionName", "city",
    "district", "zip", "lat", "lon", "timezone", "offset", "currency", "isp", "org", "as", "asname",
    "reverse", "mobile", "proxy", "hosting",
]
DEFAULT_FIELDS = [f for f in FIELDS if f != "reverse"]  # reverse DNS slows every answer down
LANGS = ("en", "de", "es", "pt-BR", "fr", "ja", "zh-CN", "ru")
OUTPUTS = ("table", "json", "tsv")

# Seam for tests: stubbed so waiting out the rate-limit window doesn't slow them down.
sleep = time.sleep


def run(args: list[str], stdin: str) -> str:
    parser = ArgParser("ip2geo")
    parser.add_argument("queries", nargs="*", metavar="QUERY")
    parser.add_argument("-f", "--fields", action="append", metavar="FIELDS")
    parser.add_argument("-o", "--output", default="table", choices=OUTPUTS)
    parser.add_argument("--lang", default="en", choices=LANGS)
    parser.add_argument("--timeout", type=float, default=10.0)
    opts = parser.parse_args(args)

    fields = _fields(opts.fields)
    queries = opts.queries or [ln.split()[0] for ln in stdin.splitlines()
                               if ln.strip() and not ln.lstrip().startswith("#")]
    for q in queries:
        if "/" in q or "?" in q or "#" in q:
            raise ModuleError(f"ip2geo: '{q}' is not an IP address or domain name")

    records = []
    for i, query in enumerate(queries or [""]):  # "" = the IP this request comes from
        record, remaining, reset = _get(query, fields, opts.lang, opts.timeout)
        records.append(record)
        if remaining == 0 and i < len(queries) - 1:
            sleep(reset + 1)  # ip-api.com: at X-Rl 0, send nothing until X-Ttl has passed

    if len(records) == 1 and records[0].get("status") == "fail":
        r = records[0]
        raise ModuleError(f"ip2geo: {r.get('query') or 'lookup'}: {r.get('message', 'lookup failed')}")
    if opts.output == "json":
        return json.dumps(records[0] if len(records) == 1 else records, indent=2, ensure_ascii=False) + "\n"
    if opts.output == "tsv":
        head = "\t".join(fields) + "\n"
        return head + "".join(_tsv_row(r, fields) for r in records)
    return "\n".join(_table(r, fields) for r in records)


def _fields(chosen: list[str] | None) -> list[str]:
    if not chosen:
        return list(DEFAULT_FIELDS)
    lookup = {f.lower(): f for f in FIELDS} | {"all": "all"}
    fields = ["query"]
    for item in chosen:
        for name in filter(None, (p.strip() for p in item.split(","))):
            field = lookup.get(name.lower().replace("-", "").replace("_", ""))
            if field is None:
                raise ModuleError(f"ip2geo: unknown field '{name}' (choose from: {', '.join(FIELDS)}, all)")
            for f in (FIELDS if field == "all" else [field]):
                if f not in fields:
                    fields.append(f)
    return fields


def _get(query: str, fields: list[str], lang: str, timeout: float) -> tuple[dict, int | None, int]:
    params = {"fields": ",".join(["status", "message"] + fields)}
    if lang != "en":
        params["lang"] = lang
    url = API + urllib.parse.quote(query, safe=":") + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "ShellCraft-ip2geo/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
            remaining, reset = _int(response.headers.get("X-Rl")), _int(response.headers.get("X-Ttl")) or 60
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            wait = _int(exc.headers.get("X-Ttl")) if exc.headers else None
            raise ModuleError("ip2geo: ip-api.com rate limit reached (45 requests per minute); try again in "
                              f"{wait if wait is not None else 60}s. Repeated overuse gets your IP banned "
                              "for an hour") from None
        raise ModuleError(f"ip2geo: ip-api.com HTTP {exc.code} {exc.reason}") from None
    except urllib.error.URLError as exc:
        raise ModuleError(f"ip2geo: cannot reach ip-api.com: {exc.reason}") from None
    except TimeoutError:
        raise ModuleError(f"ip2geo: ip-api.com timed out after {timeout:g}s") from None
    except json.JSONDecodeError:
        raise ModuleError("ip2geo: ip-api.com returned an unexpected (non-JSON) response") from None
    if not isinstance(data, dict):
        raise ModuleError("ip2geo: ip-api.com returned an unexpected response")
    data.setdefault("query", query)
    return data, remaining, reset


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _tsv_row(record: dict, fields: list[str]) -> str:
    if record.get("status") == "fail":  # keep the column count: the error goes in the second column
        cells = [str(record.get("query", "")), f"fail: {record.get('message', 'lookup failed')}"]
        cells += [""] * (len(fields) - 2)
        return "\t".join(cells[:len(fields)]) + "\n"
    return "\t".join(_cell(record, f) for f in fields) + "\n"


def _cell(record: dict, field: str) -> str:
    value = record.get(field, "")
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _table(record: dict, fields: list[str]) -> str:
    if record.get("status") == "fail":
        return f"query  {record.get('query', '')}\nerror  {record.get('message', 'lookup failed')}\n"
    rows = [(f, _cell(record, f)) for f in fields if f in record and _cell(record, f) != ""]
    width = max((len(f) for f, _ in rows), default=0)
    return "".join(f"{f.ljust(width)}  {v}\n" for f, v in rows)
