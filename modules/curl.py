"""curl — HTTP requests the way curl makes them, on Python requests (man curl)."""

from __future__ import annotations

from typing import Any

from _curl import main

SUMMARY = "Make HTTP requests like curl: methods, headers, bodies, proxies, cookies (Python requests)"
SPINNER_TEXT = "requesting…"


def run(args: list[str], stdin: str) -> Any:
    return main(args, stdin, prog="curl")
