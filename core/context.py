"""Shared shell state and control-flow exceptions."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from rich.console import Console

from core.themes import PRESETS

if TYPE_CHECKING:
    from core.loader import ModuleRegistry
    from core.themes import UI

# (label, thunk) -> thunk's result. The interactive shell swaps in a spinner-aware runner.
Runner = Callable[[str, Callable[[], Any]], Any]


def _direct(label: str, fn: Callable[[], Any]) -> Any:
    return fn()


@dataclass
class ShellContext:
    registry: ModuleRegistry
    ui: UI | None = None  # None when headless (-c without a tty, MCP)
    config: dict[str, Any] = field(default_factory=dict)
    runner: Runner = _direct
    interactive: bool = False
    allow_redirect: bool = True
    allow_system: bool = True
    allow_stateful: bool = True  # cd / theme / exit / reload / clear
    prev_dir: str | None = None


class ShellExit(Exception):
    def __init__(self, code: int = 0):
        super().__init__(code)
        self.code = code


class CommandError(Exception):
    """A command failed with a message meant for the user."""


def to_text(value: Any, width: int = 100) -> str:
    """Flatten a str or Rich renderable into plain text (for pipes, files, MCP)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    buf = io.StringIO()
    # Uses a preset theme so sc.* style names resolve even without a UI.
    console = Console(file=buf, width=width, color_system=None, force_terminal=False, highlight=False,
                      theme=PRESETS["nord"].rich_theme())
    console.print(value)
    return buf.getvalue()
