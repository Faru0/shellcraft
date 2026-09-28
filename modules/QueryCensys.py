"""QueryCensys — hosts, certificates and searches from the Censys Platform API (censys-platform SDK)."""

from __future__ import annotations

import json
import os
from typing import Any

from core.modkit import ArgParser, EnvSetting, ModuleError

SUMMARY = "Look up a host or certificate, or search, in the Censys Platform (needs an API token)"
SPINNER_TEXT = "asking Censys…"

TOKEN_VAR = "CENSYS_API_TOKEN"
ORG_VAR = "CENSYS_ORG_ID"
ENV_SETTINGS = [
    EnvSetting(TOKEN_VAR, "Personal Access Token",
               "Censys Platform → your user icon → API Access → Create New Token."),
    EnvSetting(ORG_VAR, "Organization ID",
               "Optional; paid plans only (needed for search). Shown under Current Organization "
               "on the Personal Access Tokens page."),
]

COMMANDS = ("host", "search", "cert")
OUTPUTS = ("table", "json")
INSTALL_HINT = 'QueryCensys: needs the Censys SDK: pip install censys-platform  (or pip install -e ".[censys]")'


def run(args: list[str], stdin: str) -> str:
    parser = ArgParser("QueryCensys")
    parser.add_argument("command", nargs="?", choices=COMMANDS)
    parser.add_argument("targets", nargs="*", metavar="TARGET")
    parser.add_argument("-o", "--output", default="table", choices=OUTPUTS)
    parser.add_argument("-n", "--limit", type=int, default=25, metavar="N")
    parser.add_argument("--page-token", metavar="TOKEN")
    parser.add_argument("--timeout", type=float, default=30.0)
    opts = parser.parse_args(args)

    if opts.command is None:
        raise ModuleError("QueryCensys: give a command: host IP | search QUERY | cert SHA256  (see `man QueryCensys`)")
    targets = opts.targets or [ln.strip() for ln in stdin.splitlines()
                               if ln.strip() and not ln.lstrip().startswith("#")]
    if not targets:
        what = {"host": "an IP address", "search": "a query", "cert": "a SHA-256 fingerprint"}[opts.command]
        raise ModuleError(f"QueryCensys: {opts.command} needs {what} (as an argument or on stdin)")
    if opts.command == "search":
        if not 1 <= opts.limit <= 100:
            raise ModuleError("QueryCensys: --limit must be between 1 and 100")
        targets = [" ".join(opts.targets) if opts.targets else " ".join(targets)]
    if opts.command == "cert":
        targets = [_fingerprint(t) for t in targets]

    token = os.environ.get(TOKEN_VAR, "").strip()
    if not token:
        raise ModuleError(f"QueryCensys: no Censys API token. Create a Personal Access Token in the Censys "
                          f"Platform (user icon → API Access), then run: settings {TOKEN_VAR}")
    org = os.environ.get(ORG_VAR, "").strip() or None
    sdk = _client(token, org, opts.timeout)

    if opts.command == "host":
        records = [_call(lambda ip=ip: sdk.global_data.get_host(host_id=ip), "host", ip, org) for ip in targets]
        return _dump(records) if opts.output == "json" else "\n".join(_host_table(r) for r in records)
    if opts.command == "cert":
        records = [_call(lambda fp=fp: sdk.global_data.get_certificate(certificate_id=fp), "cert", fp, org)
                   for fp in targets]
        return _dump(records) if opts.output == "json" else "\n".join(_cert_table(r) for r in records)

    body: dict[str, Any] = {"query": targets[0], "page_size": opts.limit}
    if opts.page_token:
        body["page_token"] = opts.page_token
    result = _call(lambda: sdk.global_data.search(search_query_input_body=body), "search", targets[0], org)
    return _dump(result) if opts.output == "json" else _search_table(result)


# ── talking to Censys ────────────────────────────────────────────────────────

def _client(token: str, org: str | None, timeout: float) -> Any:
    """The SDK client (a seam: tests replace this function with a fake)."""
    try:
        from censys_platform import SDK
    except ImportError:
        raise ModuleError(INSTALL_HINT) from None
    return SDK(personal_access_token=token, organization_id=org, timeout_ms=int(timeout * 1000))


def _call(fn, command: str, target: str, org: str | None) -> dict:
    """Run one SDK call; return the payload as plain JSON data, or raise a clear ModuleError."""
    try:
        response = fn()
    except Exception as exc:  # noqa: BLE001 — SDK and httpx errors, translated below
        raise ModuleError(_explain(exc, command, target, org)) from None
    envelope = getattr(response, "result", None)
    payload = getattr(envelope, "result", None)
    if payload is None:
        raise ModuleError(f"QueryCensys: Censys returned no data for {target}")
    return payload.model_dump(mode="json", by_alias=True, exclude_none=True)


def _explain(exc: Exception, command: str, target: str, org: str | None) -> str:
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        name = type(exc).__name__
        if "Timeout" in name:
            return f"QueryCensys: Censys did not answer in time for {target} (raise --timeout)"
        if name in ("ConnectError", "NetworkError", "ProxyError") or isinstance(exc, OSError):
            return f"QueryCensys: cannot reach api.platform.censys.io: {exc}"
        return f"QueryCensys: {name}: {exc}"
    detail = _detail(exc)
    if status == 401:
        return f"QueryCensys: Censys rejected the API token (401). Create a new one, then run: settings {TOKEN_VAR}"
    if status == 403:
        return ("QueryCensys: Censys denied access (403): your plan doesn't include this, or your user lacks the "
                "API Access role" + (f" ({detail})" if detail else ""))
    if status == 404:
        what = {"host": "host", "cert": "certificate", "search": "resource"}[command]
        return f"QueryCensys: Censys has no {what} {target} (404)"
    if status == 422:
        if command == "search" and org is None:
            return ("QueryCensys: search needs a paid Censys plan and its organization ID; "
                    f"run: settings {ORG_VAR}" + (f" ({detail})" if detail else ""))
        return f"QueryCensys: Censys rejected the request (422): {detail or 'invalid value'}"
    if status == 429:
        return "QueryCensys: too many concurrent requests for your Censys plan (429); wait and retry"
    if status == 503:
        return "QueryCensys: Censys rate limit reached (503); slow down and retry"
    return f"QueryCensys: Censys error {status}" + (f": {detail}" if detail else "")


def _detail(exc: Exception) -> str:
    body = getattr(exc, "body", "") or ""
    try:
        data = json.loads(body)
    except (TypeError, ValueError):
        return body.strip()[:200]
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err.get("reason") or "")
        return str(data.get("detail") or data.get("title") or err or "")
    return ""


def _fingerprint(text: str) -> str:
    fp = text.strip().lower().replace(":", "")
    if len(fp) != 64 or any(c not in "0123456789abcdef" for c in fp):
        raise ModuleError(f"QueryCensys: '{text}' is not a SHA-256 fingerprint (64 hex characters)")
    return fp


# ── formatting ───────────────────────────────────────────────────────────────

def _dump(value: Any) -> str:
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def _pairs(rows: list[tuple[str, Any]]) -> str:
    rows = [(k, str(v)) for k, v in rows if v not in (None, "", [], {})]
    width = max((len(k) for k, _ in rows), default=0)
    return "".join(f"{k.ljust(width)}  {v}\n" for k, v in rows)


def _columns(header: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    widths = [max(len(r[i]) for r in rows + [header]) for i in range(len(header) - 1)]
    return "".join(("  ".join(v.ljust(w) for v, w in zip(row, widths)) + "  " + row[-1]).rstrip() + "\n"
                   for row in [header] + rows)


def _asn(routing: dict) -> str:
    if not routing.get("asn"):
        return ""
    return f"AS{routing['asn']} {routing.get('name') or routing.get('description') or ''}".strip()


def _software(items: list[dict]) -> str:
    names = []
    for item in items or []:
        name = " ".join(str(item[k]) for k in ("vendor", "product", "version") if item.get(k))
        if name and name not in names:
            names.append(name)
    return ", ".join(names)


def _host_table(asset: dict) -> str:
    host = asset.get("resource") or {}
    routing, location = host.get("autonomous_system") or {}, host.get("location") or {}
    os_ = host.get("operating_system") or {}
    place = ", ".join(str(location[k]) for k in ("city", "province", "country") if location.get(k))
    info = _pairs([
        ("ip", host.get("ip")),
        ("asn", _asn(routing)),
        ("bgp_prefix", routing.get("bgp_prefix")),
        ("location", place),
        ("os", _software([os_]) if os_ else ""),
        ("dns_names", ", ".join((host.get("dns") or {}).get("names") or [])),
        ("labels", ", ".join(str(lb.get("value")) for lb in host.get("labels") or [] if lb.get("value"))),
        ("service_count", host.get("service_count")),
    ])
    services = host.get("services") or []
    if not services:
        return info
    rows = [(f"{s.get('port', '')}/{s.get('transport_protocol', '')}".lower(), str(s.get("protocol") or ""),
             _software(s.get("software") or [])) for s in sorted(services, key=lambda s: s.get("port") or 0)]
    return info + "\n" + _columns(("PORT", "PROTOCOL", "SOFTWARE"), rows)


def _cert_table(asset: dict) -> str:
    cert = asset.get("resource") or {}
    parsed = cert.get("parsed") or {}
    validity = parsed.get("validity_period") or {}
    return _pairs([
        ("sha256", cert.get("fingerprint_sha256")),
        ("subject", parsed.get("subject_dn")),
        ("issuer", parsed.get("issuer_dn")),
        ("not_before", validity.get("not_before")),
        ("not_after", validity.get("not_after")),
        ("names", ", ".join(cert.get("names") or [])),
        ("serial", parsed.get("serial_number_hex") or parsed.get("serial_number")),
        ("validation_level", cert.get("validation_level")),
        ("revoked", "yes" if cert.get("revoked") else ("no" if "revoked" in cert else "")),
    ])


def _search_table(result: dict) -> str:
    rows = []
    for hit in result.get("hits") or []:
        if "host_v1" in hit:
            h = (hit["host_v1"].get("resource") or {})
            where = h.get("location") or {}
            rows.append(("host", str(h.get("ip", "")),
                         " ".join(filter(None, [_asn(h.get("autonomous_system") or {}), where.get("country_code")]))))
        elif "webproperty_v1" in hit:
            w = (hit["webproperty_v1"].get("resource") or {})
            rows.append(("web", f"{w.get('hostname', '')}:{w.get('port', '')}", _software(w.get("software") or [])))
        elif "certificate_v1" in hit:
            c = (hit["certificate_v1"].get("resource") or {})
            rows.append(("cert", str(c.get("fingerprint_sha256", "")), ", ".join((c.get("names") or [])[:5])))
    total = result.get("total_hits")
    footer = f"# {len(rows)} of {int(total) if isinstance(total, (int, float)) else '?'} hit(s)"
    if result.get("next_page_token"):
        footer += f"; next page: --page-token {result['next_page_token']}"
    return (_columns(("TYPE", "ASSET", "DETAILS"), rows) if rows else "(no hits)\n") + footer + "\n"
