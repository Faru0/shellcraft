"""What the lookup modules share: reading an indicator, one HTTP helper with one error style, the 24 hour answer store.

Used by queryRdap, queryShodan, queryVirusTotal, queryAbuseIPDB, queryGreyNoise, ip2geo, ioc and
enrich. A lookup module is three things on top of this file:

    SOURCE = "rdap"                    # its short name: `enrich --only rdap`, the SOURCE: lines
    KINDS = ("ipv4", "ipv6", "domain") # the indicators it takes
    KEY_VAR = ""                       # its API key variable ("" when it needs none)

    def lookup(indicator, *, timeout=10.0, fresh=False) -> dict     # one record, see record()

    def run(args, stdin): parser(), each(), show()                  # the command

A record is {"source", "indicator", "kind", "verdict", "summary", "known_good", "cached", "data",
"raw"}: `verdict` is one of VERDICTS, `summary` is one line saying why, `data` is the short list
of fields the table shows, `raw` is the service's whole answer (`-o raw`).
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, NamedTuple

from shellcraft.modkit import ArgParser, Cancelled, ModuleError, check_cancel
from shellcraft.modkit import sleep as modkit_sleep

KINDS = ("ipv4", "ipv6", "domain", "url", "md5", "sha1", "sha256", "email")
GROUPS = {"ip": ("ipv4", "ipv6"), "hash": ("md5", "sha1", "sha256")}  # names `--type` also takes
VERDICTS = ("malicious", "suspicious", "clean", "unknown")  # worst first
OUTPUTS = ("table", "json", "raw")
STORE_SECONDS = 24 * 3600  # how long an answer is reused: the free quotas are small
MAX_WAIT = 65  # the longest rate limit a list of lookups waits out (VirusTotal: 4 lookups a minute)
MAX_WAITED = 300  # seconds a list waits in all; after that the rest get the rate limit as their error
AGENT = "ShellCraft-intel/0.1"

# Seam for tests: stubbed so waiting out a rate limit doesn't slow them down.
sleep = modkit_sleep  # wakes up (Cancelled) on Ctrl-C


class NotKnown(ModuleError):
    """The service answered 404: it has nothing on this indicator. An answer, not a failure."""


class NoKey(ModuleError):
    """The service needs an API key and none is set. `enrich` lists such a source as skipped."""


class RateLimited(ModuleError):
    """The service answered 429. `wait` is its Retry-After in seconds, when it gave one."""

    def __init__(self, message: str, wait: int | None = None):
        super().__init__(message)
        self.wait = wait


# ── indicators ───────────────────────────────────────────────────────────────

_REFANG = [
    (re.compile(r"(?i)\bh(?:xx|\*\*|tt)p(s?)(?=\[?:)"), r"http\1"),
    (re.compile(r"(?i)\bfxp(?=\[?:)"), "ftp"),
    (re.compile(r"\[://\]|\[:\]//|:\[//\]"), "://"),
    (re.compile(r"(?i)\[\.\]|\(\.\)|\{\.\}|\[dot\]|\(dot\)|\{dot\}"), "."),
    (re.compile(r"\[:\]"), ":"),
    (re.compile(r"(?i)\[@\]|\(@\)|\[at\]|\(at\)"), "@"),
]


def refang(text: str) -> str:
    """Text with defanged indicators made real again: hxxp://evil[.]com -> http://evil.com."""
    for pattern, repl in _REFANG:
        text = pattern.sub(repl, text)
    return text


def defang(value: str) -> str:
    """One indicator made safe to paste into a ticket or a chat: nothing in it is a link any more."""
    kind = kind_of(value)
    if kind == "url":
        scheme, _, rest = value.partition("://")
        host, slash, path = rest.partition("/")
        scheme = re.sub(r"(?i)^http", "hxxp", scheme)
        return f"{scheme}://{host.replace('.', '[.]')}{slash}{path}"
    if kind == "ipv6":
        return value.replace(":", "[:]")
    if kind == "email":
        return value.replace("@", "[@]").replace(".", "[.]")
    if kind in ("ipv4", "domain"):
        return value.replace(".", "[.]")
    return value


_DOMAIN = r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}"
_IS_DOMAIN = re.compile(rf"(?i){_DOMAIN}\.?")
_IS_EMAIL = re.compile(rf"(?i)[a-z0-9][a-z0-9._%+-]*@{_DOMAIN}")
_IS_URL = re.compile(r"(?i)[a-z][a-z0-9+.-]*://\S+")
_HASHES = {32: "md5", 40: "sha1", 64: "sha256"}


def kind_of(text: str) -> str | None:
    """What kind of indicator `text` is (one of KINDS), or None. Give it refanged text."""
    text = text.strip()
    try:
        return f"ipv{ipaddress.ip_address(text.strip('[]')).version}"
    except ValueError:
        pass
    if _IS_URL.fullmatch(text):
        return "url"
    if _IS_EMAIL.fullmatch(text):
        return "email"
    if len(text) in _HASHES and all(c in "0123456789abcdefABCDEF" for c in text):
        return _HASHES[len(text)]
    if _IS_DOMAIN.fullmatch(text):
        return "domain"
    return None


def clean(text: str) -> tuple[str, str | None]:
    """One indicator as typed (maybe defanged, maybe in mixed case) -> (the value to look up, its kind)."""
    value = refang(text.strip()).strip()
    kind = kind_of(value)
    if kind in ("ipv4", "ipv6"):
        value = str(ipaddress.ip_address(value.strip("[]")))
    elif kind in ("domain", "md5", "sha1", "sha256"):
        value = value.lower().rstrip(".")
    elif kind == "email":
        value = value.lower()
    return value, kind


def is_public(ip: str) -> bool:
    """True for an address on the internet: not private, loopback, link-local, reserved or multicast."""
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return address.is_global and not address.is_multicast


def host_of(value: str, kind: str | None = None) -> str:
    """The host a URL or an email address points at (lower case); any other value as it is."""
    kind = kind or kind_of(value)
    if kind == "url":
        try:
            return (urllib.parse.urlsplit(value).hostname or "").lower()
        except ValueError:
            return ""
    if kind == "email":
        return value.rpartition("@")[2].lower()
    return value


# Country endings, and the general ones that turn up in real traffic. A name must end in one of
# these to count as a domain in free text; `kind_of` (one value you typed) takes any ending.
TLDS = frozenset("""
ac ad ae af ag ai al am ao aq ar as at au aw ax az ba bb bd be bf bg bh bi bj bm bn bo br bs bt bw by bz ca cc cd cf
cg ch ci ck cl cm cn co cr cu cv cw cx cy cz de dj dk dm do dz ec ee eg er es et eu fi fj fk fm fo fr ga gd ge gf gg
gh gi gl gm gn gp gq gr gs gt gu gw gy hk hm hn hr ht hu id ie il im in io iq ir is it je jm jo jp ke kg kh ki km kn
kp kr kw ky kz la lb lc li lk lr ls lt lu lv ly ma mc md me mg mh mk ml mm mn mo mp mq mr ms mt mu mv mw mx my mz na
nc ne nf ng ni nl no np nr nu nz om pa pe pf pg ph pk pl pm pn pr ps pt pw py qa re ro rs ru rw sa sb sc sd se sg sh
si sk sl sm sn so sr ss st su sv sx sy sz tc td tf tg th tj tk tl tm tn to tr tt tv tw tz ua ug uk us uy uz va vc ve
vg vi vn vu wf ws ye yt za zm zw
com net org info biz edu gov mil int pro mobi asia tel xxx aero coop museum jobs travel cat arpa
xyz top site online club shop store app dev page blog cloud tech space live life world today news media email link
click icu vip work wiki website fun art bar best bid cam cfd cyou digital direct download finance fit global gdn
group host ink kim loan lol ltd men monster network ninja one party pics plus press quest red rest review rocks
run sbs science services skin solutions stream studio support systems team tips tokyo tools trade uno video win
zone zip mov foo academy agency capital center chat codes company computer consulting design domains education
energy engineering exchange expert express gallery games guru help hosting house institute international land
legal market marketing money photo photos pizza software technology google goog london berlin nyc paris africa
bond buzz city country date faith fyi gold lat love ooo racing rip sale security shopping surf vegas wang
""".split())  # noqa: SIM905 (400 names read better as text)
# Endings that are far more often a file than a site: `main.py`, `run.sh`, `notes.md`, `a.zip`.
# A two-part name with one of them is a domain only inside a URL or an email address.
FILE_ENDINGS = frozenset({"py", "sh", "rs", "md", "so", "ps", "cc", "pm", "ml", "mk", "st", "zip", "mov", "app"})

_URL = re.compile(r"(?i)\b(?:https?|ftp)://[^\s<>\"'`\\^{}|]+")
_EMAIL = re.compile(rf"(?i)(?<![\w.+-])[a-z0-9][a-z0-9._%+-]*@{_DOMAIN}(?![\w-])")
_NAME = re.compile(rf"(?i)(?<![\w.-]){_DOMAIN}(?![\w-]|\.[a-z0-9])")
_IPV4 = re.compile(r"(?<![0-9A-Za-z.])(?:\d{1,3}\.){3}\d{1,3}(?![0-9A-Za-z]|\.\d)")
_IPV6 = re.compile(r"(?<![0-9A-Za-z:.])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![0-9A-Za-z:])")
_HEX = re.compile(r"(?<![0-9A-Za-z])(?:[0-9a-fA-F]{64}|[0-9a-fA-F]{40}|[0-9a-fA-F]{32})(?![0-9A-Za-z])")


class Found(NamedTuple):
    kind: str
    value: str
    count: int


def _url_end(url: str) -> str:
    """A URL without the punctuation of the sentence it stood in."""
    opens = {")": "(", "]": "[", "}": "{"}
    while url:
        last = url[-1]
        if last in ".,;:!?'\"" or (last in opens and url.count(last) > url.count(opens[last])):
            url = url[:-1]
        else:
            break
    return url


def find(text: str) -> list[Found]:
    """Every indicator in free text, in the order first seen, each once with how often it stood there.
    Defanged ones are read too. A URL also gives its host, an email address its domain."""
    text = refang(text)
    hits: list[tuple[int, int, str, str]] = []  # (position, order among kinds, kind, value)
    for match in _URL.finditer(text):
        url = _url_end(match.group())
        if host_of(url, "url"):
            hits.append((match.start(), 0, "url", url))
    for match in _EMAIL.finditer(text):
        hits.append((match.start(), 1, "email", match.group().lower()))
    for match in _IPV4.finditer(text):
        try:
            hits.append((match.start(), 2, "ipv4", str(ipaddress.IPv4Address(match.group()))))
        except ValueError:
            pass
    for match in _IPV6.finditer(text):
        word = match.group()
        if not any(c.isdigit() for c in word) and len(re.findall(r"[0-9A-Fa-f]+", word)) < 3:
            continue  # `a::b`, `dead::beef`: far more often code than an address
        if any(c.isalnum() for c in word):
            try:
                hits.append((match.start(), 2, "ipv6", str(ipaddress.IPv6Address(match.group()))))
            except ValueError:
                pass
    for match in _HEX.finditer(text):
        hits.append((match.start(), 3, _HASHES[len(match.group())], match.group().lower()))
    for match in _NAME.finditer(text):
        name = match.group()
        ending = name.rpartition(".")[2]
        if ending.lower() not in TLDS or not (ending.islower() or ending.isupper()):
            continue  # `done.It was` is a missing space, not Italy
        before = text[:match.start()]
        linked = before.endswith(("://", "@")) or re.search(r"://[^\s/@]*@$", before) is not None
        if ending.lower() in FILE_ENDINGS and name.count(".") == 1 and not linked:
            continue
        hits.append((match.start(), 4, "domain", name.lower()))
    counts: dict[tuple[str, str], int] = {}
    for _, _, kind, value in sorted(hits):
        counts[(kind, value)] = counts.get((kind, value), 0) + 1
    return [Found(kind, value, count) for (kind, value), count in counts.items()]


_PLURALS = {"ips": "ip", "hashes": "hash", "domains": "domain", "urls": "url", "emails": "email"}


def kinds_named(names: list[str] | None, module: str) -> tuple[str, ...]:
    """`--type ip,hash` and the like -> the kinds meant (all of them when nothing is named)."""
    if not names:
        return KINDS
    wanted: list[str] = []
    for item in names:
        for name in filter(None, (part.strip().lower() for part in item.split(","))):
            name = _PLURALS.get(name, name)
            if name not in KINDS and name not in GROUPS:
                raise ModuleError(f"{module}: unknown type '{name}' (choose from: ip, domain, url, hash, email, "
                                  "ipv4, ipv6, md5, sha1, sha256)")
            wanted += [k for k in GROUPS.get(name, (name,)) if k not in wanted]
    return tuple(wanted)


# ── HTTP ─────────────────────────────────────────────────────────────────────

def get_json(url: str, *, module: str, service: str, what: str = "it", headers: dict[str, str] | None = None,
             timeout: float = 10.0, key_var: str = "") -> Any:
    """GET `url` and return its JSON. Every failure is a ModuleError that starts with `module:` and
    names the `service`; the URL is never in it (it may hold a key). 404 raises NotKnown, 429 RateLimited."""
    request = urllib.request.Request(url, headers={"User-Agent": AGENT, "Accept": "application/json",
                                                   **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            if key_var:
                raise ModuleError(f"{module}: {service} rejected the API key ({exc.code}); "
                                  f"set a working one: settings {key_var}") from None
            raise ModuleError(f"{module}: {service} denied the request ({exc.code})") from None
        if exc.code == 404:
            raise NotKnown(f"{module}: {service} has nothing on {what}") from None
        if exc.code == 429:
            wait = _seconds(exc.headers.get("Retry-After") if exc.headers else None)
            when = f"in {wait}s" if wait is not None else "later"
            keyless = key_var and not os.environ.get(key_var, "").strip()
            hint = f" (a key raises the limit: settings {key_var})" if keyless else ""
            raise RateLimited(f"{module}: {service} rate limit reached; try again {when}{hint}", wait) from None
        raise ModuleError(f"{module}: {service} HTTP {exc.code} {exc.reason}") from None
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError):
            raise ModuleError(f"{module}: {service} timed out after {timeout:g}s") from None
        raise ModuleError(f"{module}: cannot reach {service}: {exc.reason}") from None
    except TimeoutError:
        raise ModuleError(f"{module}: {service} timed out after {timeout:g}s") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ModuleError(f"{module}: {service} returned an unexpected (non-JSON) response") from None


def _seconds(value: str | None) -> int | None:
    try:
        return max(0, int(value)) if value is not None else None
    except ValueError:
        return None


def need_key(module: str, var: str, service: str, where: str) -> str:
    """The API key in `var`, or a ModuleError that says where to get one and how to set it."""
    key = os.environ.get(var, "").strip()
    if not key:
        raise NoKey(f"{module}: no {service} API key. {where} Then run: settings {var}")
    return key


# ── the answer store ─────────────────────────────────────────────────────────

def store_dir() -> Path:
    from shellcraft.config import config_dir

    return config_dir() / "cache" / "intel"


def _store_file(service: str, indicator: str) -> Path:
    # as it is: clean() already put names and hashes in lower case, and in a URL the case counts
    return store_dir() / service / (hashlib.sha256(indicator.encode()).hexdigest()[:40] + ".json")


def answer(service: str, indicator: str, fetch: Callable[[], Any], fresh: bool = False) -> tuple[Any, bool]:
    """The service's answer for `indicator`: (data, True) from the store when it is younger than a
    day, else (fetch(), False), which is then stored. `fresh` asks again whatever is stored. A
    NotKnown from fetch() is an answer too: it is stored and returned as None."""
    path = _store_file(service, indicator)
    if not fresh:
        try:
            kept = json.loads(path.read_text(encoding="utf-8"))
            if time.time() - float(kept["at"]) < STORE_SECONDS:
                return kept["data"], True
        except (OSError, ValueError, KeyError, TypeError):
            pass
    try:
        data = fetch()
    except NotKnown:
        data = None
    _keep(path, {"at": time.time(), "indicator": indicator, "data": data})
    return data, False


def _keep(path: Path, entry: dict[str, Any]) -> None:
    """Best effort: a store that cannot be written never fails a lookup."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(entry, fh)
        os.replace(tmp, path)
        _sweep()
    except OSError:
        pass


def _sweep() -> None:
    """Once a day, remove the answers that are too old to be used."""
    root = store_dir()
    mark = root / ".swept"
    now = time.time()
    try:
        if now - mark.stat().st_mtime < STORE_SECONDS:
            return
    except OSError:
        pass
    mark.write_text("")
    for path in root.glob("*/*.json"):
        try:
            if now - path.stat().st_mtime > STORE_SECONDS:
                path.unlink()
        except OSError:
            pass


# ── the command around a lookup ──────────────────────────────────────────────

def record(source: str, indicator: str, kind: str, verdict: str = "unknown", summary: str = "", *,
           known_good: bool = False, cached: bool = False, data: dict[str, Any] | None = None,
           raw: Any = None) -> dict[str, Any]:
    return {"source": source, "indicator": indicator, "kind": kind, "verdict": verdict, "summary": summary,
            "known_good": known_good, "cached": cached, "data": data or {}, "raw": raw}


def parser(module: str, metavar: str, timeout: float = 10.0) -> ArgParser:
    """The options every lookup command has; the module adds its own."""
    p = ArgParser(module)
    p.add_argument("targets", nargs="*", metavar=metavar)
    p.add_argument("-o", "--output", default="table", choices=OUTPUTS)
    p.add_argument("--fresh", action="store_true")
    p.add_argument("--timeout", type=float, default=timeout)
    return p


def targets(module: str, args: list[str], stdin: str, kinds: tuple[str, ...], what: str) -> list[tuple[str, str]]:
    """The indicators to look up as (value, kind): the arguments, else the first word of each stdin
    line (blank lines and # comments skipped). Defanged ones are read. `what` names what the
    module takes, for the errors: "an IP address"."""
    given = args or [ln.split()[0] for ln in stdin.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    if not given:
        raise ModuleError(f"{module}: give {what} (as an argument, or one per line on stdin)")
    out = []
    for text in given:
        value, kind = clean(text)
        if kind not in kinds:
            raise ModuleError(f"{module}: '{text}' is not {what}")
        if (value, kind) not in out:
            out.append((value, kind))
    return out


class Patience:
    """What a list of lookups does about a rate limit. A short one (the service says when, and it
    is within MAX_WAIT) is waited out and the lookup asked again, for MAX_WAITED seconds in all.
    A long one (a daily limit), one with no time given, or one that is still there after the wait
    is not waited for: that lookup and the ones after it get the error. One per service."""

    def __init__(self, many: bool = True):
        self.many = many  # a single lookup never waits: its error says when to come back
        self.waited = 0.0
        self.given_up = False

    def ask(self, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        try:
            return fn()
        except RateLimited as exc:
            wait = exc.wait
            if (not self.many or self.given_up or wait is None or wait > MAX_WAIT
                    or self.waited + wait + 1 > MAX_WAITED):
                raise
            self.waited += wait + 1
            sleep(wait + 1)
        try:
            return fn()
        except RateLimited:
            self.given_up = True
            raise


def each(module: str, source: str, wanted: list[tuple[str, str]], fn: Callable[[str], dict[str, Any]]) -> list[dict]:
    """fn(indicator) for every target. One target: its error is the command's error. Several: a
    failed one becomes a record with an `error`, and a short rate limit is waited out (see Patience)."""
    records = []
    patience = Patience(len(wanted) > 1)
    for indicator, kind in wanted:
        check_cancel()
        try:
            records.append(patience.ask(lambda indicator=indicator: fn(indicator)))  # type: ignore[misc]
        except Cancelled:
            raise
        except ModuleError as exc:
            if len(wanted) == 1:
                raise
            failed = record(source, indicator, kind)
            failed["error"] = str(exc).removeprefix(f"{module}: ")
            records.append(failed)
    return records


def show(records: list[dict], output: str, rows: Callable[[dict[str, Any]], list[tuple[str, Any]]]) -> str:
    """The records as the user asked: `table` (aligned `field  value` lines, a blank line between
    records), `json` (the records without `raw`) or `raw` (what the service sent)."""
    if output == "raw":
        return dump([r.get("raw") for r in records])
    if output == "json":
        return dump([{k: v for k, v in r.items() if k != "raw"} for r in records])
    return "\n".join(_table(r, rows) for r in records)


def _table(rec: dict, rows: Callable[[dict[str, Any]], list[tuple[str, Any]]]) -> str:
    if rec.get("error"):
        return pairs([("indicator", rec["indicator"]), ("error", rec["error"])])
    verdict = rec["verdict"] + (f": {rec['summary']}" if rec.get("summary") else "")
    note = "a stored answer, less than a day old (--fresh asks again)" if rec.get("cached") else ""
    return pairs([("indicator", rec["indicator"]), ("verdict", verdict), *rows(rec["data"]), ("note", note)])


def dump(value: Any) -> str:
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def pairs(rows: list[tuple[str, Any]]) -> str:
    """`field  value` lines, aligned; empty values are left out, lists are joined with commas."""
    shown = [(k, text(v)) for k, v in rows if v not in (None, "", [], {})]
    width = max((len(k) for k, _ in shown), default=0)
    return "".join(f"{k.ljust(width)}  {v}\n" for k, v in shown)


def text(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    return str(value)


def day(value: Any) -> str:
    """A date from a service (ISO text or seconds since 1970) as YYYY-MM-DD; "" when it is neither."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return datetime.fromtimestamp(value, UTC).strftime("%Y-%m-%d")
    if isinstance(value, str) and re.match(r"\d{4}-\d{2}-\d{2}", value):
        return value[:10]
    return ""


def days_since(value: Any) -> int | None:
    """How many days ago a date from a service was; None when it is not a date."""
    stamp = day(value)
    if not stamp:
        return None
    then = datetime.strptime(stamp, "%Y-%m-%d").replace(tzinfo=UTC)
    return max(0, (datetime.now(UTC) - then).days)
