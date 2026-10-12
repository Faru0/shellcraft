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
    ui: UI | None = None  # None when headless (MCP)
    config: dict[str, Any] = field(default_factory=dict)
    runner: Runner = _direct
    interactive: bool = False
    allow_redirect: bool = True
    allow_system: bool = False  # OS executable fallback; see the `system_commands` setting
    allow_stateful: bool = True  # cd / theme / exit / reload / clear / settings
    allow_writes: bool = True  # filesystem-changing builtins: tee / mkdir / cp / mv / rm / touch
    allow_sensitive: bool = True  # builtins that may reveal secrets: env
    allow_aliases: bool = True  # expand the user's aliases (off for MCP clients)
    # When set, a module runs only if this says so (MCP: modules with `expose = false` stay local).
    module_filter: Callable[[str], bool] | None = None
    prev_dir: str | None = None
    history: Any = None  # the REPL's prompt_toolkit History, when there is one (for `history`)
    mcp_http: Any = None  # the MCP HTTP server started with `mcp start` (core.mcp_child.McpHttpChild)
    last_status: int = 0  # exit status of the last command: `$?`, and the prompt's [✗ N]
    # Shell variables (`x=1`, loop variables after a loop) and the ones exported with `export`.
    variables: dict[str, str] = field(default_factory=dict)
    exported: dict[str, str] = field(default_factory=dict)
    # Where a script (`for`, `if`, `a ; b`) sends each statement's output and errors as it runs.
    # The REPL sets these to print as it goes; when they are None (MCP, tests) the output is
    # collected and returned as one text.
    emit: Callable[[Any], None] | None = None
    emit_error: Callable[[Exception, str], None] | None = None


@dataclass
class Styled:
    """Output with two faces: `renderable` for the screen, `text` for pipes, files and MCP."""

    renderable: Any
    text: str


@dataclass
class Paged:
    """Output that chooses how the screen shows it, whatever its height: `page=True` always opens
    the pager (man pages), `page=False` always prints (help). Pipes, files and MCP see `value`."""

    value: Any
    page: bool


@dataclass
class WithStatus:
    """A command's output together with a nonzero exit status that is not an error, such as
    `grep` finding nothing (status 1). Only the pipeline sees it; it never reaches the screen."""

    value: Any
    status: int


class ShellExit(Exception):
    def __init__(self, code: int = 0):
        super().__init__(code)
        self.code = code


class CommandError(Exception):
    """A command failed with a message meant for the user. `status` becomes `$?` (default 1)."""

    def __init__(self, message: str = "", status: int = 1):
        super().__init__(message)
        self.status = status


def to_text(value: Any, width: int | None = None) -> str:
    """Flatten a str or Rich renderable into plain text (for pipes, files, MCP). `width` is the
    console width to wrap at; the current terminal's when there is one, else 100."""
    while isinstance(value, (Paged, WithStatus, Styled)):  # wrappers nest in any order
        value = value.text if isinstance(value, Styled) else value.value
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if width is None:
        width = _ui_width() or 100
    buf = io.StringIO()
    # Uses a preset theme so sc.* style names resolve even without a UI.
    console = Console(file=buf, width=width, color_system=None, force_terminal=False, highlight=False,
                      theme=PRESETS["nord"].rich_theme())
    console.print(value)
    return buf.getvalue()


_WIDTH_SOURCE: Any = None  # the UI whose console width to_text() follows (set by the shell)


def set_width_source(ui: Any) -> None:
    global _WIDTH_SOURCE
    _WIDTH_SOURCE = ui


def _ui_width() -> int | None:
    ui = _WIDTH_SOURCE
    if ui is None:
        return None
    try:
        return int(ui.console.width)
    except Exception:  # noqa: BLE001 — no console to ask
        return None
