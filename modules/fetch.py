"""fetch — pull text from a URL or file into the pipeline (or pass stdin through)."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from core.modkit import ArgParser, ModuleError

SUMMARY = "Fetch text from a URL or file (or pass stdin through)"
SPINNER_TEXT = "fetching data…"

DEFAULT_MAX_SIZE = 10 * 1024 * 1024
RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}
MAX_BACKOFF = 30.0
_SIZE = re.compile(r"^(\d+(?:\.\d+)?)\s*([kmg]?)b?$", re.I)
_BINARY_TYPES = {"image", "audio", "video", "font"}
_BINARY_SUBTYPES = {"octet-stream", "pdf", "zip", "gzip", "x-gzip", "x-tar", "x-7z-compressed",
                    "x-rar-compressed", "vnd.rar", "x-bzip2", "x-xz", "zstd", "wasm", "x-msdownload",
                    "x-executable", "x-sharedlib", "java-archive", "vnd.ms-excel", "msword"}


def run(args: list[str], stdin: str) -> str:
    parser = ArgParser("fetch")
    parser.add_argument("source", nargs="?", default="-")
    parser.add_argument("--head", type=int, metavar="N")
    parser.add_argument("--json", dest="json_path", metavar="PATH")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("-H", "--header", action="append", default=[], metavar="'NAME: VALUE'")
    parser.add_argument("-X", "--method", metavar="METHOD")
    parser.add_argument("-d", "--data", metavar="DATA")
    parser.add_argument("--retry", type=int, default=0, metavar="N")
    parser.add_argument("--max-size", default=str(DEFAULT_MAX_SIZE), metavar="SIZE")
    opts = parser.parse_args(args)

    max_size = _parse_size(opts.max_size)
    if opts.retry < 0:
        raise ModuleError("fetch: --retry must be >= 0")
    is_url = opts.source.startswith(("http://", "https://"))
    if not is_url and (opts.header or opts.method or opts.data is not None):
        raise ModuleError("fetch: --header, --method and --data only apply to http(s) URLs")

    if opts.source == "-":
        text = stdin
    elif is_url:
        body = _request_body(opts.data, stdin)
        headers = _parse_headers(opts.header)
        if body is not None and not any(k.lower() == "content-type" for k in headers):
            headers["Content-Type"] = _guess_content_type(body)
        method = (opts.method or ("POST" if body is not None else "GET")).upper()
        text = _fetch_url(opts.source, method, headers, body, opts.timeout, opts.retry, max_size)
    else:
        text = _read_file(opts.source, max_size)

    if opts.json_path is not None:
        text = _extract_json(text, opts.json_path)
    if opts.head is not None:
        if opts.head < 0:
            raise ModuleError("fetch: --head must be >= 0")
        text = "".join(text.splitlines(keepends=True)[: opts.head])
    return text


def _parse_size(value: str) -> int:
    """'10M', '512k', '2048' → bytes; 0 means no limit."""
    match = _SIZE.match(value.strip())
    if not match:
        raise ModuleError(f"fetch: invalid --max-size '{value}' (examples: 500K, 10M, 0 for no limit)")
    number, unit = float(match.group(1)), match.group(2).lower()
    return int(number * {"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}[unit])


def _format_size(size: int) -> str:
    for unit, factor in (("G", 1024 ** 3), ("M", 1024 ** 2), ("K", 1024)):
        if size >= factor:
            return f"{size / factor:g}{unit}"
    return f"{size} bytes"


def _parse_headers(raw: list[str]) -> dict[str, str]:
    headers = {"User-Agent": "ShellCraft-fetch/0.2"}
    for item in raw:
        name, sep, value = item.partition(":")
        if not sep or not name.strip():
            raise ModuleError(f"fetch: invalid header '{item}' (expected 'Name: value')")
        headers[name.strip()] = value.strip()
    return headers


def _request_body(data: str | None, stdin: str) -> bytes | None:
    """--data TEXT, @FILE to read a file, or @- to send stdin."""
    if data is None:
        return None
    if data == "@-":
        return stdin.encode("utf-8")
    if data.startswith("@"):
        path = Path(data[1:]).expanduser()
        try:
            return path.read_bytes()
        except OSError as exc:
            raise ModuleError(f"fetch: cannot read --data file {data[1:]}: {exc.strerror or exc}") from None
    return data.encode("utf-8")


def _guess_content_type(body: bytes) -> str:
    try:
        json.loads(body)
    except ValueError:
        return "application/x-www-form-urlencoded"
    return "application/json"


def _fetch_url(url: str, method: str, headers: dict[str, str], body: bytes | None, timeout: float,
               retries: int, max_size: int) -> str:
    for attempt in range(retries + 1):
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                _reject_binary_type(response.headers.get("Content-Type", ""), url)
                raw = response.read(max_size + 1) if max_size else response.read()
                if max_size and len(raw) > max_size:
                    raise ModuleError(f"fetch: response from {url} is larger than {_format_size(max_size)} "
                                      f"(raise it with --max-size, 0 for no limit)")
                _reject_binary_bytes(raw, url)
                charset = response.headers.get_content_charset() or "utf-8"
                return raw.decode(charset, errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code in RETRY_STATUS and attempt < retries:
                time.sleep(_backoff(attempt, exc.headers.get("Retry-After") if exc.headers else None))
                continue
            raise ModuleError(f"fetch: HTTP {exc.code} {exc.reason} for {url}") from None
        except urllib.error.URLError as exc:
            if attempt < retries:
                time.sleep(_backoff(attempt))
                continue
            raise ModuleError(f"fetch: cannot reach {url}: {exc.reason}") from None
        except TimeoutError:
            if attempt < retries:
                time.sleep(_backoff(attempt))
                continue
            raise ModuleError(f"fetch: timed out after {timeout:g}s: {url}") from None
    raise AssertionError("unreachable")


def _backoff(attempt: int, retry_after: str | None = None) -> float:
    """1s, 2s, 4s… capped; a numeric Retry-After header wins (also capped)."""
    if retry_after and retry_after.strip().isdigit():
        return min(float(retry_after), MAX_BACKOFF)
    return min(2.0 ** attempt, MAX_BACKOFF)


def _reject_binary_type(content_type: str, source: str) -> None:
    mime = content_type.split(";")[0].strip().lower()
    main, _, sub = mime.partition("/")
    if main in _BINARY_TYPES or (main == "application" and sub in _BINARY_SUBTYPES):
        raise ModuleError(f"fetch: {source} is binary ({mime}), not text")


def _reject_binary_bytes(raw: bytes, source: str) -> None:
    if b"\x00" in raw[:8192]:
        raise ModuleError(f"fetch: {source} looks like binary data, not text")


def _read_file(name: str, max_size: int) -> str:
    path = Path(name).expanduser()
    try:
        if max_size and path.is_file() and path.stat().st_size > max_size:
            raise ModuleError(f"fetch: {name} is larger than {_format_size(max_size)} "
                              f"(raise it with --max-size, 0 for no limit)")
        raw = path.read_bytes()
    except FileNotFoundError:
        raise ModuleError(f"fetch: no such file: {name}") from None
    except IsADirectoryError:
        raise ModuleError(f"fetch: is a directory: {name}") from None
    except PermissionError:
        raise ModuleError(f"fetch: permission denied: {name}") from None
    _reject_binary_bytes(raw, name)
    return raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")  # as read_text()


def _extract_json(text: str, dotted: str) -> str:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ModuleError(f"fetch: input is not valid JSON ({exc.msg} at line {exc.lineno})") from None
    for key in filter(None, dotted.split(".")):
        if isinstance(value, list):
            try:
                value = value[int(key)]
            except (ValueError, IndexError):
                raise ModuleError(f"fetch: no list index '{key}' in --json path {dotted}") from None
        elif isinstance(value, dict) and key in value:
            value = value[key]
        else:
            raise ModuleError(f"fetch: key '{key}' not found in --json path {dotted}")
    if isinstance(value, str):
        return value + "\n"
    if isinstance(value, list) and all(not isinstance(v, (dict, list)) for v in value):
        return "".join(f"{v}\n" for v in value)  # one scalar per line: friendly to `filter`
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"
