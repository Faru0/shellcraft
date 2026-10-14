"""Show the current time as reported by an internet time source (UTC and the local time zone).

Asks timeapi.io first; if that fails, falls back to the Date header of an HTTPS server. Useful to
check whether the machine's clock is right: --offset shows how far it is off.
"""

from __future__ import annotations

import email.utils
import json
import urllib.error
import urllib.request
from datetime import UTC, datetime

from shellcraft.modkit import ArgParser, ModuleError, check_cancel

SUMMARY = "Show the current time from the internet (UTC and local), and how far the local clock is off"
SPINNER_TEXT = "asking the internet for the time…"
TIME_API = "https://timeapi.io/api/time/current/zone?timeZone=UTC"
FALLBACK = "https://www.google.com"
AGENT = {"User-Agent": "shellcraft-findtime/1.0"}


def internet_time(timeout: float) -> tuple[datetime, str]:
    """(UTC time, the source that answered). Raises ModuleError when no source answers."""
    try:
        req = urllib.request.Request(TIME_API, headers=AGENT)
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        return datetime.fromisoformat(data["dateTime"]).replace(tzinfo=UTC), "timeapi.io"
    except (OSError, ValueError, KeyError):
        pass  # the fallback below
    check_cancel()
    try:
        req = urllib.request.Request(FALLBACK, method="HEAD", headers=AGENT)
        with urllib.request.urlopen(req, timeout=timeout) as response:
            date = response.headers.get("Date")
    except urllib.error.URLError as exc:
        raise ModuleError(f"findtime: no time source could be reached ({exc.reason})") from None
    except OSError as exc:
        raise ModuleError(f"findtime: no time source could be reached ({exc})") from None
    if not date:
        raise ModuleError("findtime: the fallback server sent no Date header")
    return email.utils.parsedate_to_datetime(date).astimezone(UTC), "HTTP Date header"


def run(args: list[str], stdin: str) -> str:
    parser = ArgParser("findtime")
    parser.add_argument("-u", "--utc", action="store_true", help="print only the UTC time")
    parser.add_argument("-o", "--offset", action="store_true", help="also show how far the local clock is off")
    parser.add_argument("-t", "--timeout", type=float, default=5.0, help="seconds per request (default 5)")
    opts = parser.parse_args(args)
    if not 0 < opts.timeout <= 60:
        raise ModuleError("findtime: --timeout must be between 0 and 60 seconds")

    utc, source = internet_time(opts.timeout)
    if opts.utc:
        return utc.strftime("%Y-%m-%d %H:%M:%S UTC") + "\n"
    local = utc.astimezone()
    lines = [
        f"UTC:    {utc.strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"Local:  {local.strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"Source: {source}",
    ]
    if opts.offset:
        drift = (datetime.now(UTC) - utc).total_seconds()
        lines.append(f"Offset: the local clock is {abs(drift):.1f} s {'ahead' if drift >= 0 else 'behind'}")
    return "\n".join(lines) + "\n"
