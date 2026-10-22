"""curl: HTTP requests with curl's command line, on Python's `requests`.

Parsed the way curl parses (combined short flags `-sSL`, attached values `-XPOST`, repeated
options, several URLs, any order), sent with one `requests.Session`, and answered with curl's
output and exit statuses, so a curl command copied from a browser or a README runs as is.
`command curl` runs the OS program instead.

What curl does that this doesn't yet is listed in TODO (and `man curl`).
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from shellcraft.builtins import builtin
from shellcraft.context import CommandError, WithStatus
from shellcraft.modkit import check_cancel, error_stream

if TYPE_CHECKING:
    from shellcraft.context import ShellContext

CURL_VERSION = "8.11.1"
USER_AGENT = f"curl/{CURL_VERSION}"
RETRY_STATUS = {408, 429, 500, 502, 503, 504}  # curl's "transient" HTTP errors
CHUNK = 64 * 1024
TODO = ("SOCKS proxies", "Copy as PowerShell pastes", "HTTP/2 and HTTP/3", "URL globbing ([1-10], {a,b})",
        "-K config files", "--resolve / --connect-to", "-4 / -6", "--limit-rate", "--parallel",
        "--trace / --trace-ascii", "NTLM / Negotiate auth", "FTP, SCP, SMTP and other protocols",
        "the progress meter", "--libcurl", "-u USER without a password (asking for it)")

_BINARY_TYPES = {"image", "audio", "video", "font"}
_BINARY_SUBTYPES = {"octet-stream", "pdf", "zip", "gzip", "x-gzip", "x-tar", "x-7z-compressed",
                    "x-rar-compressed", "vnd.rar", "x-bzip2", "x-xz", "zstd", "wasm", "x-msdownload",
                    "x-executable", "x-sharedlib", "java-archive", "vnd.ms-excel", "msword"}
_SIZE = re.compile(r"^(\d+(?:\.\d+)?)\s*([kmg]?)b?$", re.I)


class CurlExit(Exception):
    """A curl error: its exit status and message ("curl: (7) Failed to connect …")."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ── the command line ─────────────────────────────────────────────────────────

# option name → (destination, kind); kind: flag | value | append
LONG: dict[str, tuple[str, str]] = {
    "url": ("urls", "append"), "request": ("request", "value"), "get": ("get", "flag"), "head": ("head", "flag"),
    "url-query": ("url_query", "append"),
    "header": ("headers", "append"), "user-agent": ("user_agent", "value"), "referer": ("referer", "value"),
    "compressed": ("compressed", "flag"),
    "data": ("data", "append"), "data-ascii": ("data", "append"), "data-raw": ("data_raw", "append"),
    "data-binary": ("data_binary", "append"), "data-urlencode": ("data_urlencode", "append"),
    "json": ("json", "append"), "form": ("form", "append"), "form-string": ("form_string", "append"),
    "upload-file": ("upload_file", "value"),
    "user": ("user", "value"), "basic": ("basic", "flag"), "digest": ("digest", "flag"),
    "oauth2-bearer": ("bearer", "value"), "cookie": ("cookie", "append"), "cookie-jar": ("cookie_jar", "value"),
    "location": ("location", "flag"), "max-redirs": ("max_redirs", "value"),
    "insecure": ("insecure", "flag"), "cacert": ("cacert", "value"), "capath": ("cacert", "value"),
    "cert": ("cert", "value"), "key": ("key", "value"),
    "proxy": ("proxy", "value"), "proxy-user": ("proxy_user", "value"), "noproxy": ("noproxy", "value"),
    "max-time": ("max_time", "value"), "connect-timeout": ("connect_timeout", "value"),
    "retry": ("retry", "value"), "retry-delay": ("retry_delay", "value"), "retry-max-time": ("retry_max_time", "value"),
    "retry-all-errors": ("retry_all", "flag"), "retry-connrefused": ("retry_connrefused", "flag"),
    "output": ("outputs", "append"), "remote-name": ("remote_name", "append_flag"),
    "remote-name-all": ("remote_name_all", "flag"), "output-dir": ("output_dir", "value"),
    "create-dirs": ("create_dirs", "flag"), "include": ("include", "flag"), "dump-header": ("dump_header", "value"),
    "silent": ("silent", "flag"), "show-error": ("show_error", "flag"), "verbose": ("verbose", "flag"),
    "write-out": ("write_out", "value"), "fail": ("fail", "flag"), "fail-with-body": ("fail_with_body", "flag"),
    "range": ("range", "value"), "max-filesize": ("max_filesize", "value"), "stderr": ("stderr", "value"),
    "help": ("help", "flag"), "version": ("version", "flag"),
    "jq": ("jq", "value"),  # ShellCraft: pick a JSON field
    # accepted for compatibility, no effect here
    "http1.1": ("_ignored", "flag"), "http1.0": ("_ignored", "flag"), "http2": ("_ignored", "flag"),
    "globoff": ("_ignored", "flag"), "path-as-is": ("_ignored", "flag"), "no-buffer": ("_ignored", "flag"),
    "progress-bar": ("_ignored", "flag"), "no-progress-meter": ("_ignored", "flag"), "disable": ("_ignored", "flag"),
    "raw": ("_ignored", "flag"), "tr-encoding": ("_ignored", "flag"), "no-keepalive": ("_ignored", "flag"),
    "location-trusted": ("location", "flag"), "ssl-no-revoke": ("_ignored", "flag"),
}
SHORT: dict[str, str] = {
    "X": "request", "G": "get", "I": "head", "H": "header", "A": "user-agent", "e": "referer",
    "d": "data", "F": "form", "T": "upload-file", "u": "user", "b": "cookie", "c": "cookie-jar",
    "L": "location", "k": "insecure", "E": "cert", "x": "proxy", "U": "proxy-user", "m": "max-time",
    "o": "output", "O": "remote-name", "i": "include", "D": "dump-header", "s": "silent", "S": "show-error",
    "v": "verbose", "w": "write-out", "f": "fail", "r": "range", "h": "help", "V": "version",
    "g": "globoff", "N": "no-buffer", "#": "progress-bar", "q": "disable", "0": "http1.0",
}
UNSUPPORTED = {"socks4": "SOCKS proxies", "socks4a": "SOCKS proxies", "socks5": "SOCKS proxies",
               "socks5-hostname": "SOCKS proxies", "http3": "HTTP/3", "http2-prior-knowledge": "HTTP/2",
               "config": "-K config files", "resolve": "--resolve", "connect-to": "--connect-to",
               "limit-rate": "--limit-rate", "parallel": "--parallel", "trace": "--trace",
               "trace-ascii": "--trace-ascii", "ntlm": "NTLM auth", "negotiate": "Negotiate auth",
               "libcurl": "--libcurl", "ipv4": "-4 / -6", "ipv6": "-4 / -6"}
SHORT_UNSUPPORTED = {"K": "config", "Z": "parallel", "4": "ipv4", "6": "ipv6"}
VALUE_KINDS = ("value", "append")


@dataclass
class Options:
    urls: list[str] = field(default_factory=list)
    request: str | None = None
    get: bool = False
    head: bool = False
    url_query: list[str] = field(default_factory=list)
    headers: list[str] = field(default_factory=list)
    user_agent: str | None = None
    referer: str | None = None
    compressed: bool = False
    data: list[str] = field(default_factory=list)
    data_raw: list[str] = field(default_factory=list)
    data_binary: list[str] = field(default_factory=list)
    data_urlencode: list[str] = field(default_factory=list)
    json: list[str] = field(default_factory=list)
    form: list[str] = field(default_factory=list)
    form_string: list[str] = field(default_factory=list)
    upload_file: str | None = None
    user: str | None = None
    basic: bool = False
    digest: bool = False
    bearer: str | None = None
    cookie: list[str] = field(default_factory=list)
    cookie_jar: str | None = None
    location: bool = False
    max_redirs: str | None = None
    insecure: bool = False
    cacert: str | None = None
    cert: str | None = None
    key: str | None = None
    proxy: str | None = None
    proxy_user: str | None = None
    noproxy: str | None = None
    max_time: str | None = None
    connect_timeout: str | None = None
    retry: str | None = None
    retry_delay: str | None = None
    retry_max_time: str | None = None
    retry_all: bool = False
    retry_connrefused: bool = False
    outputs: list[str] = field(default_factory=list)
    remote_name: int = 0
    remote_name_all: bool = False
    output_dir: str | None = None
    create_dirs: bool = False
    include: bool = False
    dump_header: str | None = None
    silent: bool = False
    show_error: bool = False
    verbose: bool = False
    write_out: str | None = None
    fail: bool = False
    fail_with_body: bool = False
    range: str | None = None
    max_filesize: str | None = None
    stderr: str | None = None
    help: bool = False
    version: bool = False
    jq: str | None = None
    _ignored: bool = False
    order: list[str] = field(default_factory=list)  # body options in the order given (-d, --json…)


def parse(argv: list[str]) -> Options:
    """curl's option syntax. Raises CurlExit(2) for an unknown or incomplete option."""
    opts = Options()
    words = list(argv)
    i = 0

    def take(name: str, value: str | None) -> None:
        dest, kind = LONG[name]
        if kind == "flag":
            setattr(opts, dest, True)
        elif kind == "append_flag":
            setattr(opts, dest, getattr(opts, dest) + 1)
        elif kind == "value":
            setattr(opts, dest, value)
        else:
            getattr(opts, dest).append(value)
            if dest in ("data", "data_raw", "data_binary", "data_urlencode", "json"):
                opts.order.append(dest)

    while i < len(words):
        word = words[i]
        i += 1
        if word == "--":
            opts.urls.extend(words[i:])
            break
        if word.startswith("--") and len(word) > 2:
            name, eq, attached = word[2:].partition("=")
            if name in UNSUPPORTED:
                raise CurlExit(2, f"curl: option --{name}: not supported yet ({UNSUPPORTED[name]}; see man curl)")
            if name not in LONG:
                raise CurlExit(2, f"curl: option --{name}: is unknown\n"
                                  f"curl: try 'curl --help' for more information")
            if LONG[name][1] in VALUE_KINDS:
                if eq:
                    value = attached
                elif i < len(words):
                    value, i = words[i], i + 1
                else:
                    raise CurlExit(2, f"curl: option --{name}: requires parameter")
                take(name, value)
            else:
                take(name, None)
            continue
        if word.startswith("-") and len(word) > 1:
            j = 1
            while j < len(word):
                letter = word[j]
                if letter in SHORT_UNSUPPORTED:
                    raise CurlExit(2, f"curl: option -{letter}: not supported yet "
                                      f"({UNSUPPORTED[SHORT_UNSUPPORTED[letter]]}; see man curl)")
                if letter not in SHORT:
                    raise CurlExit(2, f"curl: option -{word[j:]}: is unknown\n"
                                      f"curl: try 'curl --help' for more information")
                name = SHORT[letter]
                if LONG[name][1] in VALUE_KINDS:
                    rest = word[j + 1:]
                    if rest:
                        value = rest
                    elif i < len(words):
                        value, i = words[i], i + 1
                    else:
                        raise CurlExit(2, f"curl: option -{letter}: requires parameter")
                    take(name, value)
                    break
                take(name, None)
                j += 1
            continue
        opts.urls.append(word)
    return opts


# ── running ──────────────────────────────────────────────────────────────────

def main(argv: list[str], stdin: str, may_write: bool = True) -> Any:
    """Run a curl command line; the output text, or WithStatus(text, curl's status). `may_write`
    is False for an AI client's command, which writes no files."""
    try:
        opts = parse(argv)
        if opts.help:
            return HELP
        if opts.version:
            import requests

            return f"curl {CURL_VERSION} (ShellCraft, Python requests {requests.__version__})\n"
        return _run(opts, stdin, may_write)
    except CurlExit as exc:
        if exc.code == 2:  # usage errors are always shown
            raise CommandError(exc.message, 2) from None
        opts_silent = "opts" in locals() and opts.silent and not opts.show_error
        if opts_silent:
            return WithStatus("", exc.code)
        raise CommandError(exc.message, exc.code) from None


def _run(opts: Options, stdin: str, may_write: bool) -> Any:
    import requests
    import urllib3

    sources = list(opts.urls)
    if not sources:
        raise CurlExit(2, "curl: no URL specified\ncurl: try 'curl --help' for more information")
    writes = [name for name, on in (("-o", any(o not in ("-", *NULL_DEVICES) for o in opts.outputs)),
                                    ("-O", opts.remote_name or opts.remote_name_all),
                                    ("-D", opts.dump_header not in (None, "-", *NULL_DEVICES)), ("-c", opts.cookie_jar))
              if on]
    if writes and not may_write:
        raise CurlExit(2, f"curl: {', '.join(writes)} would write files, which commands run for an AI client "
                          "can't do; leave them out to get the response as text")
    session = requests.Session()
    session.headers.clear()
    session.headers["User-Agent"] = USER_AGENT
    session.headers["Accept"] = "*/*"
    if opts.compressed:
        session.headers["Accept-Encoding"] = _encodings()
    else:  # curl sends no Accept-Encoding (Python's HTTP layer would add "identity")
        session.headers["Accept-Encoding"] = urllib3.util.SKIP_HEADER
    if opts.insecure:
        session.verify = False
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    elif opts.cacert:
        session.verify = opts.cacert
    if opts.cert:
        cert, _, _password = opts.cert.partition(":") if not os.path.exists(opts.cert) else (opts.cert, "", "")
        session.cert = (cert, opts.key) if opts.key else cert
    if opts.location:
        session.max_redirects = _int(opts.max_redirs, "--max-redirs", 50)
    jar = _load_cookies(opts, session)
    limit = _size(opts.max_filesize) if opts.max_filesize is not None else 0
    outputs = list(opts.outputs)
    out: list[str] = []
    status = 0
    for index, source in enumerate(sources):
        check_cancel()
        target = outputs[index] if index < len(outputs) else None
        if target is None and (opts.remote_name_all or index < opts.remote_name):
            target = _remote_name(source)
        piece, code = _one(session, opts, source, stdin, target, limit)
        out.append(piece)
        status = status or code
    if opts.cookie_jar and jar is not None:
        _save_cookies(session, opts.cookie_jar)
    text = "".join(out)
    return WithStatus(text, status) if status else text


@dataclass
class Exchange:
    """What -i, -v, -D and -w need from one transfer."""
    responses: list[Any]
    body: bytes
    seconds: float
    method: str
    url: str


def _one(session: Any, opts: Options, url: str, stdin: str, target: str | None, limit: int) -> tuple[str, int]:
    url = _with_scheme(url)
    if url.startswith("file://"):
        return _finish_text(_read_file(urllib.parse.unquote(urllib.parse.urlparse(url).path), limit),
                            opts), 0
    method, body, headers, files = _request_parts(opts, stdin)
    if opts.get:
        query = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else (body or "")
        if query:
            url += ("&" if urllib.parse.urlparse(url).query else "?") + query
        body = None
    for item in opts.url_query:
        url += ("&" if urllib.parse.urlparse(url).query else "?") + _url_query(item)
    auth = _auth(opts)
    proxies = _proxies(opts, url)
    connect = _float(opts.connect_timeout, "--connect-timeout", None)
    total = _float(opts.max_time, "--max-time", None)
    timeout = (connect or total or 300.0, total or None)
    retries = _int(opts.retry, "--retry", 0)
    delay = _float(opts.retry_delay, "--retry-delay", None)
    retry_until = time.monotonic() + _float(opts.retry_max_time, "--retry-max-time", 0) if opts.retry_max_time else None
    for attempt in range(retries + 1):
        started = time.monotonic()
        try:
            exchange = _send(session, method, url, headers, body, files, auth, proxies, timeout, total, opts,
                             limit)
        except CurlExit as exc:
            if attempt < retries and _transient(exc.code, opts) and _may_retry(retry_until):
                _wait(attempt, delay, None, opts)
                continue
            raise
        response = exchange.responses[-1]
        if response.status_code in RETRY_STATUS or (opts.retry_all and response.status_code >= 400):
            if attempt < retries and _may_retry(retry_until):
                _wait(attempt, delay, response.headers.get("Retry-After"), opts)
                continue
        exchange.seconds = time.monotonic() - started
        return _deliver(exchange, opts, target)
    raise AssertionError("unreachable")


def _send(session: Any, method: str, url: str, headers: dict[str, Any], body: Any, files: Any, auth: Any,
          proxies: Any, timeout: Any, total: float | None, opts: Options, limit: int) -> Exchange:
    import requests

    started = time.monotonic()
    request = requests.Request(method, url, headers=headers, data=body, files=files, auth=auth)
    prepared = session.prepare_request(request)
    if opts.verbose:
        _verbose_request(prepared, proxies)
    try:
        env = session.merge_environment_settings(prepared.url, proxies or {}, True, session.verify, session.cert)
        response = session.send(prepared, allow_redirects=opts.location, timeout=timeout, **env)
        chunks: list[bytes] = []
        size = 0
        length = response.headers.get("Content-Length")
        if limit and length and length.isdigit() and int(length) > limit:
            raise CurlExit(63, "curl: (63) Maximum file size exceeded")
        if method != "HEAD":
            for chunk in response.iter_content(CHUNK):
                check_cancel()
                size += len(chunk)
                if limit and size > limit:
                    raise CurlExit(63, "curl: (63) Maximum file size exceeded")
                if total is not None and time.monotonic() - started > total:
                    raise CurlExit(28, f"curl: (28) Operation timed out after {int(total * 1000)} milliseconds "
                                       f"with {size} bytes received")
                chunks.append(chunk)
    except requests.exceptions.TooManyRedirects:
        raise CurlExit(47, f"curl: (47) Maximum ({session.max_redirects}) redirects followed") from None
    except requests.exceptions.SSLError as exc:
        raise CurlExit(60, f"curl: (60) SSL certificate problem: {_reason(exc)}\nMore details here: "
                           f"use --insecure (-k) to skip the check, or --cacert FILE") from None
    except requests.exceptions.ProxyError as exc:
        raise CurlExit(7, f"curl: (7) Failed to connect to the proxy: {_reason(exc)}") from None
    except (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout):
        spent = int((time.monotonic() - started) * 1000)
        raise CurlExit(28, f"curl: (28) Operation timed out after {spent} milliseconds") from None
    except requests.exceptions.ConnectionError as exc:
        host = urllib.parse.urlparse(url).hostname or url
        text = str(exc)
        if any(s in text for s in ("Name or service not known", "getaddrinfo failed", "Failed to resolve",
                                   "nodename nor servname", "No address associated", "Temporary failure in name")):
            raise CurlExit(6, f"curl: (6) Could not resolve host: {host}") from None
        if "Read timed out" in text or "timed out" in text:
            raise CurlExit(28, "curl: (28) Operation timed out") from None
        port = urllib.parse.urlparse(url).port or (443 if url.startswith("https") else 80)
        raise CurlExit(7, f"curl: (7) Failed to connect to {host} port {port}: {_reason(exc)}") from None
    except (requests.exceptions.ChunkedEncodingError, requests.exceptions.ContentDecodingError) as exc:
        raise CurlExit(56, f"curl: (56) Failure when receiving data from the peer: {_reason(exc)}") from None
    except (requests.exceptions.InvalidURL, requests.exceptions.MissingSchema,
            requests.exceptions.InvalidSchema) as exc:
        raise CurlExit(3, f"curl: (3) URL rejected: {_reason(exc)}") from None
    return Exchange([*response.history, response], b"".join(chunks), time.monotonic() - started, method,
                    response.url)


def _deliver(ex: Exchange, opts: Options, target: str | None) -> tuple[str, int]:
    response = ex.responses[-1]
    if opts.verbose:
        for resp in ex.responses:
            error_stream("".join(f"< {line}\n" for line in _header_lines(resp)) + "<\n")
    head_text = "".join("".join(f"{line}\r\n" for line in _header_lines(r)) + "\r\n"
                        for r in (ex.responses if opts.location else ex.responses[-1:]))
    if opts.dump_header and opts.dump_header != "-":
        _write(opts.dump_header, head_text.encode("utf-8"), opts)
    failed = response.status_code >= 400 and (opts.fail or opts.fail_with_body)
    text = ""
    if opts.include or opts.head or opts.dump_header == "-":
        text += head_text.replace("\r\n", "\n")
    if failed and not opts.fail_with_body:
        body_text = ""
    elif target is not None and target != "-":
        _write(target, ex.body, opts)
        body_text = ""
    elif ex.method == "HEAD" or not ex.body:
        body_text = ""
    else:
        if target != "-" and _binary(response, ex.body):
            raise CurlExit(23, "curl: Binary output can mess up your terminal. Use \"--output -\" to tell "
                               "curl to output it anyway, or consider \"--output <FILE>\" to save to a file.")
        body_text = _decode(response, ex.body)
        body_text = _finish_text(body_text, opts)
    text += body_text
    if opts.write_out:
        text += _write_out(opts.write_out, ex, opts)
    if failed:
        message = f"curl: (22) The requested URL returned error: {response.status_code}"
        if opts.silent and not opts.show_error:
            return text, 22
        if text:
            error_stream(message + "\n")
            return text, 22
        raise CurlExit(22, message)
    return text, 0


# ── request pieces ───────────────────────────────────────────────────────────

def _request_parts(opts: Options, stdin: str) -> tuple[str, Any, dict[str, Any], Any]:
    headers: dict[str, Any] = {}
    files: Any = None
    body: Any = None
    content_type: str | None = None
    pieces: list[bytes] = []
    counters = {k: 0 for k in ("data", "data_raw", "data_binary", "data_urlencode", "json")}
    for kind in opts.order:
        value = getattr(opts, kind)[counters[kind]]
        counters[kind] += 1
        if kind == "data":
            pieces.append(_read_at(value, stdin, strip_newlines=True))
        elif kind == "data_binary":
            pieces.append(_read_at(value, stdin, strip_newlines=False))
        elif kind == "data_raw":
            pieces.append(value.encode("utf-8"))
        elif kind == "data_urlencode":
            pieces.append(_urlencode_part(value, stdin))
        elif kind == "json":
            pieces.append(_read_at(value, stdin, strip_newlines=False))
            content_type = "application/json"
            headers.setdefault("Accept", "application/json")
    if pieces:
        joiner = b"" if opts.json and len(opts.json) == len(pieces) else b"&"
        body = joiner.join(pieces)
        if content_type is None:
            content_type = "application/x-www-form-urlencoded"
    if opts.form or opts.form_string:
        files = _form_parts(opts)
        content_type = None
    method = "GET"
    if opts.upload_file:
        body = _upload_body(opts.upload_file, stdin)
        method = "PUT"
    elif (body is not None and not opts.get) or files is not None:
        method = "POST"  # with -G the data goes in the URL and it stays a GET
    if opts.head:
        method = "HEAD"
    if opts.request:
        method = opts.request.upper()
    if content_type and not (opts.get and files is None):
        headers["Content-Type"] = content_type
    if opts.user_agent is not None:
        headers["User-Agent"] = opts.user_agent or None
    if opts.referer:
        headers["Referer"] = opts.referer.split(";auto")[0]
    if opts.range:
        headers["Range"] = f"bytes={opts.range}"
    if opts.bearer:
        headers["Authorization"] = f"Bearer {opts.bearer}"
    for raw in opts.headers:
        for name, value in _header_items(raw, stdin):
            for existing in [k for k in headers if k.lower() == name.lower()]:
                del headers[existing]
            headers[name] = value
    return method, body, headers, files


def _header_items(raw: str, stdin: str) -> list[tuple[str, Any]]:
    if raw.startswith("@"):
        text = Path(raw[1:]).expanduser().read_text(encoding="utf-8") if raw != "@-" else stdin
        out: list[tuple[str, Any]] = []
        for line in text.splitlines():
            if line.strip():
                out += _header_items(line.strip(), "")
        return out
    if ":" not in raw and raw.rstrip().endswith(";"):
        return [(raw.rstrip()[:-1].strip(), "")]  # "Name;": send it empty
    name, sep, value = raw.partition(":")
    if not sep or not name.strip():
        return []  # curl ignores a header it can't read
    value = value.strip()
    return [(name.strip(), value if value else None)]  # "Name:" removes the header


def _read_at(value: str, stdin: str, strip_newlines: bool) -> bytes:
    """`@FILE` / `@-` read a file / stdin (-d strips its newlines, as curl does); else the text."""
    if not value.startswith("@"):
        return value.encode("utf-8")
    if value == "@-":
        data = stdin.encode("utf-8")
    else:
        try:
            data = Path(value[1:]).expanduser().read_bytes()
        except OSError as exc:
            raise CurlExit(26, f"curl: (26) Failed to open/read local data from file/application: "
                               f"{value[1:]}: {exc.strerror or exc}") from None
    return data.replace(b"\r", b"").replace(b"\n", b"") if strip_newlines else data


def _urlencode_part(value: str, stdin: str) -> bytes:
    def quote(text: str) -> str:  # as curl: everything but letters, digits and -._~ (a space is %20)
        return urllib.parse.quote(text, safe="")

    if "=" in value:
        name, _, content = value.partition("=")
        encoded = quote(content)
        return (f"{name}={encoded}" if name else encoded).encode("ascii")
    if "@" in value:
        name, _, file = value.partition("@")
        content = _read_at("@" + file, stdin, strip_newlines=False).decode("utf-8", errors="replace")
        return (f"{name}={quote(content)}" if name else quote(content)).encode("ascii")
    return quote(value).encode("ascii")


def _url_query(item: str) -> str:
    if item.startswith("+"):
        return item[1:]
    return _urlencode_part(item, "").decode("ascii")


def _form_parts(opts: Options) -> list[tuple[str, Any]]:
    parts: list[tuple[str, Any]] = []
    for raw in opts.form:
        name, _, rest = raw.partition("=")
        fields = rest.split(";")
        value, params = fields[0], dict(p.split("=", 1) for p in fields[1:] if "=" in p)
        if value.startswith("@"):
            path = Path(value[1:]).expanduser()
            try:
                content = path.read_bytes()
            except OSError as exc:
                raise CurlExit(26, f"curl: (26) Failed to open/read local data from file/application: "
                                   f"{path}: {exc.strerror or exc}") from None
            parts.append((name, (params.get("filename", path.name), content, params.get("type"))))
        elif value.startswith("<"):
            parts.append((name, (None, Path(value[1:]).expanduser().read_bytes(), params.get("type"))))
        else:
            parts.append((name, (None, value.encode("utf-8"), params.get("type"))))
    for raw in opts.form_string:
        name, _, value = raw.partition("=")
        parts.append((name, (None, value.encode("utf-8"))))
    return parts


def _upload_body(name: str, stdin: str) -> bytes:
    if name in ("-", "."):
        return stdin.encode("utf-8")
    try:
        return Path(name).expanduser().read_bytes()
    except OSError as exc:
        raise CurlExit(26, f"curl: (26) Failed to open/read local data from file/application: {name}: "
                           f"{exc.strerror or exc}") from None


def _auth(opts: Options) -> Any:
    if not opts.user:
        return None
    user, sep, password = opts.user.partition(":")
    if not sep:
        raise CurlExit(2, "curl: -u USER without a password: use -u USER:PASSWORD (asking for it is on the "
                          "to-do list, man curl)")
    if opts.digest:
        from requests.auth import HTTPDigestAuth

        return HTTPDigestAuth(user, password)
    from requests.auth import HTTPBasicAuth

    return HTTPBasicAuth(user, password)


def _proxies(opts: Options, url: str) -> dict[str, str] | None:
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    noproxy = opts.noproxy if opts.noproxy is not None else (os.environ.get("NO_PROXY") or os.environ.get("no_proxy"))
    if noproxy is not None and _no_proxy_match(host, noproxy):
        return {"no_proxy": "*"}  # no proxy, not even one from the environment
    if not opts.proxy:
        return None  # requests uses HTTP_PROXY / HTTPS_PROXY from the environment, as curl does
    proxy = opts.proxy
    scheme = proxy.split("://", 1)[0].lower() if "://" in proxy else "http"
    if scheme.startswith("socks"):
        raise CurlExit(2, "curl: SOCKS proxies are not supported yet (man curl); use an http(s) proxy")
    if "://" not in proxy:
        proxy = "http://" + proxy
    if opts.proxy_user and "@" not in proxy.split("://", 1)[1]:
        user = urllib.parse.quote(opts.proxy_user, safe=":")
        proxy = proxy.replace("://", f"://{user}@", 1)
    return {"http": proxy, "https": proxy}


def _no_proxy_match(host: str, spec: str) -> bool:
    for item in (s.strip().lower().lstrip(".") for s in spec.split(",")):
        if item == "*" or (item and (host == item or host.endswith("." + item))):
            return True
    return False


def _load_cookies(opts: Options, session: Any) -> Any:
    for value in opts.cookie:
        if "=" in value:
            for pair in value.split(";"):
                name, sep, val = pair.strip().partition("=")
                if sep and name:
                    session.cookies.set(name, val)
        else:
            from http.cookiejar import MozillaCookieJar  # here: it imports urllib.request

            jar = MozillaCookieJar()
            try:
                jar.load(os.path.expanduser(value), ignore_discard=True, ignore_expires=True)
            except (OSError, ValueError):
                pass  # curl: a missing cookie file just means no cookies
            for cookie in jar:
                session.cookies.set_cookie(cookie)
    return session.cookies if opts.cookie_jar else None


def _save_cookies(session: Any, path: str) -> None:
    from http.cookiejar import MozillaCookieJar

    jar = MozillaCookieJar(os.path.expanduser(path))
    for cookie in session.cookies:
        jar.set_cookie(cookie)
    try:
        jar.save(ignore_discard=True, ignore_expires=True)
    except OSError as exc:
        error_stream(f"curl: could not write the cookie jar {path}: {exc.strerror or exc}\n")


# ── output ───────────────────────────────────────────────────────────────────

def _header_lines(response: Any) -> list[str]:
    version = {10: "HTTP/1.0", 11: "HTTP/1.1", 20: "HTTP/2"}.get(getattr(response.raw, "version", 11), "HTTP/1.1")
    lines = [f"{version} {response.status_code} {response.reason or ''}".rstrip()]
    raw = getattr(response.raw, "headers", None)
    items = raw.items() if raw is not None and hasattr(raw, "items") else response.headers.items()
    lines += [f"{k}: {v}" for k, v in items]
    return lines


def _verbose_request(prepared: Any, proxies: Any) -> None:
    parsed = urllib.parse.urlparse(prepared.url)
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query
    lines = [f"* Connecting to {parsed.hostname} port {parsed.port or (443 if parsed.scheme == 'https' else 80)}"
             + (f" through the proxy {proxies.get('http')}" if proxies and proxies.get("http") else ""),
             f"> {prepared.method} {target} HTTP/1.1", f"> Host: {parsed.netloc}"]
    lines += [f"> {k}: {v}" for k, v in prepared.headers.items() if k.lower() != "host"]
    text = "\n".join(lines) + "\n>\n"
    if prepared.body:
        size = len(prepared.body) if isinstance(prepared.body, (bytes, str)) else "?"
        text += f"* upload: {size} bytes\n"
    error_stream(text)


def _write_out(fmt: str, ex: Exchange, opts: Options) -> str:
    if fmt.startswith("@"):
        fmt = Path(fmt[1:]).expanduser().read_text(encoding="utf-8")
    response = ex.responses[-1]
    head_size = sum(len(line) + 2 for line in _header_lines(response)) + 2
    values = {
        "http_code": f"{response.status_code:03d}", "response_code": f"{response.status_code:03d}",
        "size_download": str(len(ex.body)), "size_header": str(head_size), "time_total": f"{ex.seconds:.6f}",
        "content_type": response.headers.get("Content-Type", ""), "url_effective": response.url,
        "url": ex.url, "redirect_url": response.headers.get("Location", "") if not opts.location else "",
        "num_redirects": str(len(ex.responses) - 1), "method": ex.method, "http_version": "1.1",
        "scheme": urllib.parse.urlparse(response.url).scheme.upper(), "exitcode": "0",
        "errormsg": "", "filename_effective": "",
    }

    def var(m: re.Match[str]) -> str:
        if m.group(1) == "header":
            return response.headers.get(m.group(2), "")
        return values.get(m.group(2), "")

    text = re.sub(r"%(header)?\{([^}]+)\}", var, fmt)
    return text.replace("\\n", "\n").replace("\\t", "\t").replace("\\r", "\r").replace("\\\\", "\\")


def _decode(response: Any, body: bytes) -> str:
    charset = None
    content_type = response.headers.get("Content-Type", "")
    match = re.search(r"charset=([\w.-]+)", content_type, re.I)
    if match:
        charset = match.group(1)
    try:
        return body.decode(charset or "utf-8", errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def _binary(response: Any, body: bytes) -> bool:
    mime = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
    main, _, sub = mime.partition("/")
    return main in _BINARY_TYPES or (main == "application" and sub in _BINARY_SUBTYPES) or b"\x00" in body[:8192]


def _finish_text(text: str, opts: Options) -> str:
    if opts.jq is not None:
        text = _extract_json(text, opts.jq)
    return text


NULL_DEVICES = ("/dev/null", "NUL", "nul")  # `-o /dev/null` works on Windows too: nothing is written


def _write(name: str, data: bytes, opts: Options) -> None:
    if name in NULL_DEVICES:
        return
    path = Path(name).expanduser()
    if opts.output_dir and not path.is_absolute():
        path = Path(opts.output_dir).expanduser() / path
    try:
        if opts.create_dirs:
            path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
    except OSError as exc:
        raise CurlExit(23, f"curl: (23) Failure writing output to destination: {path}: "
                           f"{exc.strerror or exc}") from None


def _remote_name(url: str) -> str:
    name = urllib.parse.unquote(urllib.parse.urlparse(_with_scheme(url)).path.rsplit("/", 1)[-1])
    if not name:
        raise CurlExit(23, "curl: (23) Remote file name has no length")
    return name


def _read_file(name: str, limit: int) -> str:
    """A file:// URL's file, as text."""
    path = Path(name).expanduser()
    try:
        if limit and path.is_file() and path.stat().st_size > limit:
            raise CurlExit(63, "curl: (63) Maximum file size exceeded")
        raw = path.read_bytes()
    except OSError as exc:
        raise CurlExit(37, f"curl: (37) Couldn't open file {name}: {exc.strerror or exc}") from None
    if b"\x00" in raw[:8192]:
        raise CurlExit(23, "curl: Binary output can mess up your terminal. Use \"--output <FILE>\" to save it.")
    return raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


def _extract_json(text: str, path: str) -> str:
    """`--jq a.b.0` (or `.a.b[0]`): one field of a JSON document."""
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CurlExit(2, f"curl: --jq: the response is not valid JSON ({exc.msg} at line {exc.lineno})") from None
    dotted = re.sub(r"\[(\d+)\]", r".\1", path).strip(".")
    for key in filter(None, dotted.split(".")):
        if isinstance(value, list):
            try:
                value = value[int(key)]
            except (ValueError, IndexError):
                raise CurlExit(2, f"curl: --jq: no list index '{key}' in {path}") from None
        elif isinstance(value, dict) and key in value:
            value = value[key]
        else:
            raise CurlExit(2, f"curl: --jq: key '{key}' not found in {path}")
    if isinstance(value, str):
        return value + "\n"
    if isinstance(value, list) and all(not isinstance(v, (dict, list)) for v in value):
        return "".join(f"{v}\n" for v in value)
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


# ── small helpers ────────────────────────────────────────────────────────────

def _with_scheme(url: str) -> str:
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
        return url
    return ("ftp://" if url.startswith("ftp.") else "http://") + url  # curl's guess


def _encodings() -> str:
    names = ["gzip", "deflate"]
    try:
        import brotli  # type: ignore[import-not-found]  # noqa: F401

        names.append("br")
    except ImportError:
        pass
    return ", ".join(names)


def _transient(code: int, opts: Options) -> bool:
    """curl retries timeouts (and refused connections with --retry-connrefused)."""
    return opts.retry_all or code in (28, 56) or (code == 7 and opts.retry_connrefused)


def _may_retry(until: float | None) -> bool:
    return until is None or time.monotonic() < until


def _wait(attempt: int, delay: float | None, retry_after: str | None, opts: Options) -> None:
    seconds = delay if delay is not None else min(2.0 ** attempt, 600.0)
    if retry_after and retry_after.strip().isdigit():
        seconds = min(float(retry_after), 600.0)
    if not opts.silent:
        error_stream(f"Warning: Problem: a transient error. Will retry in {seconds:g} seconds.\n")
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        check_cancel()
        time.sleep(min(0.1, end - time.monotonic()) if end > time.monotonic() else 0)


def _int(value: str | None, name: str, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        raise CurlExit(2, f"curl: option {name}: expected a proper numerical parameter") from None


def _float(value: str | None, name: str, default: float | None) -> float | None:
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        raise CurlExit(2, f"curl: option {name}: expected a proper numerical parameter") from None


def _size(value: str) -> int:
    match = _SIZE.match(value.strip())
    if not match:
        raise CurlExit(2, f"curl: invalid size '{value}' (examples: 500K, 10M)")
    number, unit = float(match.group(1)), match.group(2).lower()
    return int(number * {"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}[unit])


def _reason(exc: BaseException) -> str:
    text = str(exc)
    match = re.search(r"\(Caused by [^(]*\((.*)\)\)\s*$", text) or re.search(r": (\[Errno[^\]]*\][^')]*)", text)
    return (match.group(1) if match else text)[:200]



HELP = """Usage: curl [options...] <url>
 -d, --data <data>          HTTP POST data (@FILE, @- for stdin; --data-raw, --data-binary, --data-urlencode)
     --json <data>          HTTP POST JSON (sets Content-Type and Accept)
 -F, --form <name=content>  multipart form (name=@file;type=…)
 -X, --request <method>     the request method (GET, POST, PUT, DELETE…)
 -G, --get                  put the -d data in the URL, as a GET
 -H, --header <header>      a header ("Name: value"; "Name:" removes one; "Name;" sends it empty)
 -A, --user-agent <name>    the User-Agent (default curl/""" + CURL_VERSION + """)
 -b, --cookie <data|file>   send cookies; -c, --cookie-jar <file> saves them
 -u, --user <user:password> basic auth (--digest for digest); --oauth2-bearer <token>
 -L, --location             follow redirects (--max-redirs <n>)
 -I, --head                 headers only (HEAD); -i, --include adds them to the output
 -o, --output <file>        write the body to a file (-O: the URL's file name; --output-dir, --create-dirs)
 -s, --silent               no error messages (-S shows them anyway); -v, --verbose: the exchange
 -w, --write-out <format>   %{http_code}, %{time_total}, %{size_download}, %header{name}…
 -f, --fail                 fail (exit 22) on HTTP errors; --fail-with-body keeps the body
 -x, --proxy <[proto://]host[:port]>  an http(s) proxy (-U user:password, --noproxy hosts)
 -k, --insecure             don't check the TLS certificate (--cacert, --cert, --key)
 -m, --max-time <seconds>   give up after this long (--connect-timeout for connecting)
     --retry <n>            retry transient errors (--retry-delay, --retry-max-time, --retry-all-errors)
 -r, --range <range>        part of the body (bytes)
     --compressed           ask for a compressed response
     --jq <path>            ShellCraft: print one field of a JSON response (a.b.0 or .a.b[0])
 man curl has everything, and what is not supported yet.
"""


@builtin("curl", "Make HTTP requests like curl: methods, headers, bodies, proxies, cookies",
         "curl [OPTIONS] URL...", category="info", doc="""\
# curl

HTTP requests the way **curl** makes them, built on Python's `requests`. The command line is
curl's: a curl command from a README, an API's docs or a browser's **Copy as cURL** runs here as
it is, and its output and exit statuses are curl's.

## Synopsis

```
curl [OPTIONS] URL...
```

## Description

`curl` sends one request per URL, one after another, and writes the response body to its output,
so it pipes like any command: `curl -s https://api.example.com/items | jq -s`. Short options
combine (`-sSL`, `-fsSL`), values attach or follow (`-XPOST`, `-X POST`), options go anywhere.

As curl does, it sends `User-Agent: curl/8.11.1` and `Accept: */*`, follows no redirects without
`-L`, prints the body of an HTTP error unless `-f` is given, and refuses to print a binary body
("Binary output can mess up your terminal"): save it with `-o FILE` or `-O`.

The OS program is still there: `command curl …` runs it (with OS commands on), and
`which -a curl` shows both. For an AI client, `curl` runs through the `shellcraft_pipeline` tool
and writes no files there (`-o`, `-O`, `-D FILE` and `-c` are refused).

## Options

| Area | Options |
| --- | --- |
| Method and URL | `URL`, `--url URL`, `-X`/`--request METHOD`, `-G`/`--get` (the `-d` data goes in the query), `-I`/`--head`, `--url-query DATA` |
| Headers | `-H`/`--header 'Name: value'` (repeat it; `'Name:'` removes a default header, `'Name;'` sends it empty, `@FILE` reads headers from a file), `-A`/`--user-agent`, `-e`/`--referer`, `--compressed` |
| Body | `-d`/`--data`, `--data-ascii`, `--data-raw`, `--data-binary`, `--data-urlencode`, `--json`, `-F`/`--form` (`name=@file;type=…`, `name=<file`), `--form-string`, `-T`/`--upload-file` (a PUT). `@FILE` reads a file, `@-` reads stdin. Several `-d` are joined with `&`. |
| Auth and cookies | `-u`/`--user user:password`, `--basic`, `--digest`, `--oauth2-bearer TOKEN`, `-b`/`--cookie` (`'a=1; b=2'` or a cookie file), `-c`/`--cookie-jar FILE` |
| Redirects | `-L`/`--location`, `--max-redirs N` |
| TLS | `-k`/`--insecure`, `--cacert FILE`, `--capath`, `-E`/`--cert FILE`, `--key FILE` |
| Proxy | `-x`/`--proxy [http://\\|https://]HOST[:PORT]`, `-U`/`--proxy-user`, `--noproxy HOSTS`. `HTTP_PROXY`, `HTTPS_PROXY` and `NO_PROXY` are honored, as curl does. |
| Time and retries | `-m`/`--max-time`, `--connect-timeout`, `--retry N`, `--retry-delay`, `--retry-max-time`, `--retry-all-errors`, `--retry-connrefused` |
| Output | `-o`/`--output FILE`, `-O`/`--remote-name`, `--remote-name-all`, `--output-dir`, `--create-dirs`, `-i`/`--include`, `-D`/`--dump-header FILE`, `-s`/`--silent`, `-S`/`--show-error`, `-v`/`--verbose`, `-w`/`--write-out`, `-f`/`--fail`, `--fail-with-body`, `-r`/`--range`, `--max-filesize` |
| ShellCraft | `--jq PATH`: print one field of a JSON response (`data.items.0.name` or `.data.items[0].name`) |
| Accepted, no effect | `--http1.1`, `--http2`, `-g`/`--globoff`, `--path-as-is`, `-N`/`--no-buffer`, `-#`, `--no-progress-meter`, `-q`, `--raw`, `--ssl-no-revoke` |

`-v` writes the request (`>` lines) and the response headers (`<` lines) to the error stream, so
`2>` and `2>&1` work with it.

### `-w` / `--write-out` variables

`%{http_code}` (also `%{response_code}`), `%{size_download}`, `%{size_header}`, `%{time_total}`,
`%{content_type}`, `%{url_effective}`, `%{redirect_url}`, `%{num_redirects}`, `%{method}`,
`%{scheme}`, `%header{Name}`, and `\\n`, `\\t`.

## Pasting a curl command

Right-click a request in the browser's developer tools (Network tab), **Copy as cURL**, and paste
it at the prompt:

- **Copy as cURL (bash)**: works as it is. Its `\\` line ends continue the command and its
  `$'…'` strings are understood.
- **Copy as cURL (cmd)**, Windows: works as it is. Its `^"…^"` quoting and `^` line ends are
  turned into ShellCraft's quoting before the line runs.

The same goes for rc files and funcs.

## Exit statuses

As curl's: `0` success, `2` a bad option, `3` a bad URL, `6` the host can't be resolved, `7`
can't connect, `22` an HTTP error with `-f`, `23` a write error or a binary body, `28` timed out,
`47` too many redirects, `56` a receive error, `60` the TLS certificate failed, `63` larger than
`--max-filesize`. `-s` hides the message; the status stays in `$?`.

## Not supported yet

SOCKS proxies · "Copy as PowerShell" pastes · HTTP/2 and HTTP/3 · URL globbing (`[1-10]`,
`{a,b}`) · `-K` config files · `--resolve` / `--connect-to` · `-4` / `-6` · `--limit-rate` ·
`--parallel` · `--trace` / `--trace-ascii` · NTLM and Negotiate auth · FTP, SCP, SMTP and the other
protocols · the progress meter · `--libcurl` · `-u USER` without a password (asking for it).

An option from this list stops with "not supported yet" and status 2.

## Examples

```
curl https://example.com
curl -sSL https://example.com/install.sh | head
curl -I https://example.com
curl -s https://api.github.com/repos/python/cpython --jq stargazers_count
curl -X POST https://api.example.com/items -H "Authorization: Bearer $TOKEN" --json '{"name": "x"}'
curl -F "file=@report.pdf" -F "note=weekly" https://upload.example.com/
curl -u admin:secret --digest https://intranet.example.com/status
curl -x http://127.0.0.1:8080 -k https://target.example.com/   # through Burp or ZAP
curl -s -o /dev/null -w '%{http_code} %{time_total}\\n' https://example.com
curl -O https://example.com/files/data.csv
```

## See also

`man command` · `man jq` · `docs/curl.md`
""")
def curl(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    return main(args, stdin, may_write=ctx.allow_writes)
