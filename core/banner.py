"""Startup banner: gradient ASCII art inside a Rich panel."""

from __future__ import annotations

import platform
import sys

from rich.align import Align
from rich.color import Color
from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from core import __version__
from core.loader import ModuleRegistry
from core.themes import UI

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


def render_banner(ui: UI, registry: ModuleRegistry) -> Panel:
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
    for warning in registry.warnings:
        parts.append(Align.center(Text(f"⚠ {warning}", style="sc.warning")))
    return Panel(Group(*parts), border_style="sc.border", padding=(1, 2),
                 subtitle=Text(" welcome, operator ", style="sc.prompt"), subtitle_align="right")
