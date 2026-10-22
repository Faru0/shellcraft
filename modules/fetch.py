"""fetch — curl under another name, plus a local file or stdin as the source (man fetch)."""

from __future__ import annotations

from typing import Any

from _curl import main

SUMMARY = "curl's engine under another name; also reads a local file or stdin (--head N, --jq PATH)"
SPINNER_TEXT = "fetching data…"


def run(args: list[str], stdin: str) -> Any:
    return main(args, stdin, prog="fetch")
