"""queryCert — certificates logged for a domain in Certificate Transparency, via crt.sh."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from core.modkit import ArgParser, ModuleError

SUMMARY = "List a domain's TLS certificates and subdomains from Certificate Transparency logs (crt.sh)"
SPINNER_TEXT = "searching crt.sh (can take a while)…"

API = "https://crt.sh/"
OUTPUTS = ("table", "names", "json")
OVERLOADED = "crt.sh is overloaded or down (it often is); wait a minute and retry"
HOSTNAME = re.compile(r"^(\*\.)?[a-z0-9_-]+(\.[a-z0-9_-]+)+$")  # names output skips e-mails, free text
POLITE_DELAY = 1.0  # between domains: crt.sh is a free, shared service

# Seam for tests: stubbed so the delay between domains doesn't slow them down.
sleep = time.sleep


def run(args: list[str], stdin: str) -> str:
    parser = ArgParser("queryCert")
    parser.add_argument("domains", nargs="*", metavar="DOMAIN")
    parser.add_argument("-s", "--subdomains", action="store_true")
    parser.add_argument("--expired", action="store_true")
    parser.add_argument("-o", "--output", default="table", choices=OUTPUTS)
    parser.add_argument("--timeout", type=float, default=90.0)
    opts = parser.parse_args(args)

    domains = [_domain(d) for d in (opts.domains or _stdin_domains(stdin))]
    if not domains:
        raise ModuleError("queryCert: give a domain, e.g. `queryCert example.com` (or pipe domains in, one per line)")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    certs: list[dict] = []
    seen: set[int] = set()
    for i, domain in enumerate(dict.fromkeys(domains)):
        if i:
            sleep(POLITE_DELAY)
        query = f"%.{domain}" if opts.subdomains else domain
        for cert in _get(query, opts.expired, opts.timeout):
            if cert.get("id") in seen or (not opts.expired and _expired(cert, now)):
                continue
            seen.add(cert.get("id"))
            certs.append(cert)
    certs.sort(key=lambda c: (str(c.get("not_before", "")), c.get("id") or 0), reverse=True)

    if opts.output == "json":
        return json.dumps(certs, indent=2, ensure_ascii=False) + "\n"
    if opts.output == "names":
        names = {n.strip().lower() for c in certs for n in _names(c)}
        names = {n for n in names if HOSTNAME.match(n)}
        return "".join(f"{n}\n" for n in sorted(names, key=_name_order))
    return _table(certs, now)


def _stdin_domains(stdin: str) -> list[str]:
    return [ln.split()[0] for ln in stdin.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


def _domain(text: str) -> str:
    domain = text.strip().lower()
    if "://" in domain:
        domain = urllib.parse.urlsplit(domain).hostname or ""
    for prefix in ("%.", "*."):
        domain = domain.removeprefix(prefix)
    domain = domain.strip(".")
    if not domain or "." not in domain or any(c in domain for c in "/ @:%*"):
        raise ModuleError(f"queryCert: '{text}' is not a domain name")
    return domain


def _get(query: str, expired: bool, timeout: float) -> list[dict]:
    params = {"q": query, "output": "json"}
    if not expired:
        params["exclude"] = "expired"
    url = API + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"Accept": "application/json",
                                                   "User-Agent": "ShellCraft-queryCert/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code in (429, 500, 502, 503, 504):
            raise ModuleError(f"queryCert: {OVERLOADED} (HTTP {exc.code})") from None
        raise ModuleError(f"queryCert: crt.sh HTTP {exc.code} {exc.reason} for {query}") from None
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise ModuleError(f"queryCert: crt.sh gave no answer within {timeout:g}s; {OVERLOADED}, "
                              "or raise --timeout") from None
        raise ModuleError(f"queryCert: cannot reach crt.sh: {exc.reason}") from None
    except TimeoutError:
        raise ModuleError(f"queryCert: crt.sh gave no answer within {timeout:g}s; {OVERLOADED}, "
                          "or raise --timeout") from None
    if not body.strip():
        return []
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        # An overloaded crt.sh answers 200 with an HTML error page instead of JSON.
        raise ModuleError(f"queryCert: {OVERLOADED} (it returned an error page instead of JSON)") from None
    if not isinstance(data, list):
        raise ModuleError("queryCert: crt.sh returned an unexpected response")
    return [c for c in data if isinstance(c, dict)]


def _names(cert: dict) -> list[str]:
    names = str(cert.get("name_value") or "").split("\n")
    return names + [str(cert.get("common_name") or "")]


def _name_order(name: str) -> tuple:
    # Group by registered domain, parents before children: example.com, *.example.com, a.example.com
    return tuple(reversed(name.removeprefix("*.").split("."))), name


def _expired(cert: dict, now: datetime) -> bool:
    try:
        return datetime.fromisoformat(str(cert.get("not_after"))) < now
    except ValueError:
        return False


def _issuer(issuer_name: str) -> str:
    parts = dict(p.strip().split("=", 1) for p in issuer_name.split(",") if "=" in p)
    return parts.get("O") or parts.get("CN") or issuer_name


def _table(certs: list[dict], now: datetime) -> str:
    if not certs:
        return "(no certificates found)\n"
    header = ("CRT.SH ID", "NOT BEFORE", "NOT AFTER", "ISSUER", "NAMES")
    rows = []
    for c in certs:
        names = sorted({n.strip().lower() for n in str(c.get("name_value") or "").split("\n") if n.strip()},
                       key=_name_order)
        not_after = str(c.get("not_after", ""))[:10] + (" (expired)" if _expired(c, now) else "")
        rows.append((str(c.get("id", "")), str(c.get("not_before", ""))[:10], not_after,
                     _issuer(str(c.get("issuer_name", ""))), ", ".join(names)))
    widths = [max(len(r[i]) for r in rows + [header]) for i in range(len(header) - 1)]
    return "".join("  ".join(v.ljust(w) for v, w in zip(row, widths)) + "  " + row[-1] + "\n"
                   for row in [header] + rows)
