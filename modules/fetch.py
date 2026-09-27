"""fetch — pull text from a URL or file into the pipeline (or pass stdin through)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

from core.modkit import ArgParser, ModuleError

SUMMARY = "Fetch text from a URL or file (or pass stdin through)"
SPINNER_TEXT = "fetching data…"


def run(args: list[str], stdin: str) -> str:
    parser = ArgParser("fetch")
    parser.add_argument("source", nargs="?", default="-")
    parser.add_argument("--head", type=int, metavar="N")
    parser.add_argument("--json", dest="json_path", metavar="PATH")
    parser.add_argument("--timeout", type=float, default=15.0)
    opts = parser.parse_args(args)

    if opts.source == "-":
        text = stdin
    elif opts.source.startswith(("http://", "https://")):
        text = _fetch_url(opts.source, opts.timeout)
    else:
        text = _read_file(opts.source)

    if opts.json_path is not None:
        text = _extract_json(text, opts.json_path)
    if opts.head is not None:
        if opts.head < 0:
            raise ModuleError("fetch: --head must be >= 0")
        text = "".join(text.splitlines(keepends=True)[: opts.head])
    return text


def _fetch_url(url: str, timeout: float) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "ShellCraft-fetch/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        raise ModuleError(f"fetch: HTTP {exc.code} {exc.reason} for {url}") from None
    except urllib.error.URLError as exc:
        raise ModuleError(f"fetch: cannot reach {url}: {exc.reason}") from None
    except TimeoutError:
        raise ModuleError(f"fetch: timed out after {timeout:g}s: {url}") from None


def _read_file(name: str) -> str:
    path = Path(name).expanduser()
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise ModuleError(f"fetch: no such file: {name}") from None
    except IsADirectoryError:
        raise ModuleError(f"fetch: is a directory: {name}") from None
    except PermissionError:
        raise ModuleError(f"fetch: permission denied: {name}") from None


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
