"""Shared input helpers for the ported commands."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from rich.style import Style
from rich.text import Text

from core import settings
from core.modkit import ModuleError

if TYPE_CHECKING:
    from core.context import ShellContext

_SHORT_COUNT = re.compile(r"^-(\d+)$")


def links_on(ctx: ShellContext) -> bool:
    """Whether file names on screen should be terminal hyperlinks (the `links` setting; only in
    the interactive shell, never in pipes or over MCP, which see plain text anyway)."""
    return ctx.interactive and ctx.ui is not None and settings.get(ctx.config, "links")


def linked(text: Text, path: Path) -> Text:
    """`text` as a hyperlink to `path` (OSC 8 with a file:// URI): Ctrl-click opens it in the
    terminals that support links (Windows Terminal, GNOME Terminal, kitty, WezTerm, iTerm2…);
    the others show the plain name. The pager strips the links, since it draws its own screen."""
    try:
        uri = path.resolve().as_uri()
    except (OSError, ValueError):
        return text
    text.stylize(Style(link=uri))
    return text


def user_path(name: str) -> Path:
    """A path argument as a Path, with `~` / `~user` expanded. Unlike Path.expanduser(), an unknown
    user (`~nosuchuser`) or a missing home directory leaves the name as typed instead of raising."""
    try:
        return Path(name).expanduser()
    except (RuntimeError, KeyError):
        return Path(name)


def read_file(prog: str, name: str) -> str:
    path = user_path(name)
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise ModuleError(f"{prog}: {name}: no such file") from None
    except IsADirectoryError:
        raise ModuleError(f"{prog}: {name}: is a directory") from None
    except PermissionError:
        raise ModuleError(f"{prog}: {name}: permission denied") from None
    except OSError as exc:
        raise ModuleError(f"{prog}: {name}: {exc.strerror or exc}") from None


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
