"""Startup banner: gradient ASCII art inside a Rich panel."""

from __future__ import annotations

import platform
import sys
from typing import Iterable

from rich.align import Align
from rich.color import Color
from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from shellcraft import __version__
from shellcraft.loader import ModuleRegistry
from shellcraft.themes import UI


def core_warnings() -> list[str]:
    """What the shell itself is missing, shown in the banner like a module's missing import:
    optional packages whose absence takes a feature away (pywinpty on Windows: no subshells on
    a pseudo-terminal, so no footer; pyte: no screen redraw when switching subshells; mcp: no
    MCP server; keyring: keys in plain text)."""
    import importlib.util

    def missing(name: str) -> bool:
        try:
            return importlib.util.find_spec(name) is None
        except (ImportError, ValueError):
            return True

    python = sys.executable
    found: list[str] = []
    if sys.platform == "win32" and missing("winpty"):
        found.append("pywinpty is not installed: `sub PROGRAM` runs in the foreground, with no footer and no job "
                     f"list — {python} -m pip install pywinpty")
    if missing("pyte"):
        found.append("pyte is not installed: switching between subshells replays their last output instead of "
                     f"redrawing their screen — {python} -m pip install pyte")
    if missing("mcp"):
        found.append(f"the 'mcp' package is not installed: `mcp start` and --mcp won't work — "
                     f"{python} -m pip install mcp")
    if missing("keyring"):
        found.append("keyring is not installed: API keys are kept in plain text in config.json — "
                     f"{python} -m pip install keyring")
    return found


# "ANSI Shadow" block letters, assembled per glyph so columns always line up.
_GLYPHS = {
    "S": ["███████╗", "██╔════╝", "███████╗", "╚════██║", "███████║", "╚══════╝"],
    "H": ["██╗  ██╗", "██║  ██║", "███████║", "██╔══██║", "██║  ██║", "╚═╝  ╚═╝"],
    "E": ["███████╗", "██╔════╝", "█████╗  ", "██╔══╝  ", "███████╗", "╚══════╝"],
    "L": ["██╗     ", "██║     ", "██║     ", "██║     ", "███████╗", "╚══════╝"],
    "C": [" ██████╗", "██╔════╝", "██║     ", "██║     ", "╚██████╗", " ╚═════╝"],
    "R": ["██████╗ ", "██╔══██╗", "██████╔╝", "██╔══██╗", "██║  ██║", "╚═╝  ╚═╝"],
    "A": [" █████╗ ", "██╔══██╗", "███████║", "██╔══██║", "██║  ██║", "╚═╝  ╚═╝"],
    "F": ["███████╗", "██╔════╝", "█████╗  ", "██╔══╝  ", "██║     ", "╚═╝     "],
    "T": ["████████╗", "╚══██╔══╝", "   ██║   ", "   ██║   ", "   ██║   ", "   ╚═╝   "],
}


def _art(word: str) -> list[str]:
    return ["".join(_GLYPHS[ch][row] for ch in word) for row in range(6)]


def _gradient(lines: list[str], start: str, end: str) -> Text:
    a = Color.parse(start).get_truecolor()
    b = Color.parse(end).get_truecolor()
    width = max(len(ln) for ln in lines)
    text = Text()
    for ln in lines:
        for i, ch in enumerate(ln):
            t = i / max(1, width - 1)
            rgb = tuple(int(a[k] + (b[k] - a[k]) * t) for k in range(3))
            text.append(ch, style=f"bold #{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}")
        text.append("\n")
    text.rstrip()
    return text


def render_banner(ui: UI, registry: ModuleRegistry, warnings: Iterable[str] = ()) -> Panel:
    """The welcome panel. The module warnings (a missing import, a bad name), then the core's
    (core_warnings: an optional package missing), then `warnings` (theme problems, a bad config
    section) are shown in it, so nothing printed before the screen was cleared is lost."""
    theme = ui.theme
    art_lines = _art("SHELLCRAFT")
    if ui.console.width >= len(art_lines[0]) + 6:
        title = _gradient(art_lines, theme.gradient_from, theme.gradient_to)
    else:
        title = _gradient(["⚡ S H E L L C R A F T ⚡"], theme.gradient_from, theme.gradient_to)

    tagline = Text("a modular, pipe-friendly shell  ·  input → process → pipe/redirect", style="sc.muted")
    stats = Text.assemble(
        ("v", "sc.muted"), (__version__, "sc.accent"), ("   theme ", "sc.muted"), (theme.label, "sc.accent"),
        ("   modules ", "sc.muted"), (str(len(registry)), "sc.success"), (" loaded", "sc.muted"),
        ("   python ", "sc.muted"), (platform.python_version(), "sc.accent"),
        ("   ", ""), (sys.platform, "sc.muted"),
    )
    hint = Text.assemble(
        ("type ", "sc.muted"), ("help", "sc.accent"), (" · ", "sc.muted"), ("man <tool>", "sc.accent"),
        (" · ", "sc.muted"), ("theme <name>", "sc.accent"), (" · ", "sc.muted"), ("Tab", "sc.accent"),
        (" completes, ", "sc.muted"), ("→", "sc.accent"), (" accepts ghost text", "sc.muted"),
    )
    parts = [Align.center(title), Text(""), Align.center(tagline), Align.center(stats), Align.center(hint)]
    for warning in [*registry.warnings, *core_warnings(), *warnings]:
        parts.append(Align.center(Text(f"⚠ {warning}", style="sc.warning")))
    return Panel(Group(*parts), border_style="sc.border", padding=(1, 2),
                 subtitle=Text(" welcome, operator ", style="sc.prompt"), subtitle_align="right")
