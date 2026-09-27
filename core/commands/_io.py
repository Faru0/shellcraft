"""Shared input helpers for the ported commands."""

from __future__ import annotations

import re
from pathlib import Path

from core.modkit import ModuleError

_SHORT_COUNT = re.compile(r"^-(\d+)$")


def read_file(prog: str, name: str) -> str:
    path = Path(name).expanduser()
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise ModuleError(f"{prog}: {name}: no such file") from None
    except IsADirectoryError:
        raise ModuleError(f"{prog}: {name}: is a directory") from None
    except PermissionError:
        raise ModuleError(f"{prog}: {name}: permission denied") from None


def sources(prog: str, files: list[str], stdin: str) -> list[tuple[str, str]]:
    """[(name, text)] for each FILE argument ('-' = stdin), or stdin alone when none are given."""
    if not files:
        return [("-", stdin)]
    return [("-", stdin) if f == "-" else (f, read_file(prog, f)) for f in files]


def joined(prog: str, files: list[str], stdin: str) -> str:
    return "".join(_ensure_newline(text) for _, text in sources(prog, files, stdin))


def expand_count(args: list[str]) -> list[str]:
    """Rewrite the classic `-5` shorthand into `-n 5` (head/tail)."""
    out: list[str] = []
    for a in args:
        m = _SHORT_COUNT.match(a)
        out += ["-n", m.group(1)] if m else [a]
    return out


def lines_out(lines: list[str]) -> str:
    return "".join(f"{ln}\n" for ln in lines)


def _ensure_newline(text: str) -> str:
    return text if not text or text.endswith("\n") else text + "\n"
