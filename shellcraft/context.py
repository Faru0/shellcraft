"""Shared shell state and control-flow exceptions."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from rich.console import Console

from shellcraft.themes import PRESETS

if TYPE_CHECKING:
    from shellcraft.loader import ModuleRegistry
    from shellcraft.themes import UI

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
    mcp_http: Any = None  # the MCP HTTP server started with `mcp start` (shellcraft.mcp_child.McpHttpChild)
    jobs: Any = None  # the subshells started with `sub` (shellcraft.jobs.JobTable); the REPL sets it
    footer: Any = None  # the tab bar on the last row (shellcraft.footer.Footer); the REPL sets it
    alerts: Any = None  # the status alerts and their listener (shellcraft.alerts.AlertBus); the REPL sets it
    lite: bool = False  # `shellcraft --lite` (what `sub shellcraft` runs): builtins, modules and settings only
    recording: Any = None  # the session transcript being written (shellcraft.recording.Recorder), or None
    prefill: str = ""  # text the next prompt starts with (`clip -i`)
    console_report: Any = None  # () -> the console / footer diagnostics text; the REPL sets it (`diag`)
    startup_results: dict[str, Any] = field(default_factory=dict)  # shellcraft.startup.Result per script
    supervisor: Any = None  # the background startup scripts (shellcraft.startup.Supervisor); the REPL sets it
    stop: Any = None  # a background job's stop flag (streams.StopFlag): every pipeline it runs stops with it
    last_status: int = 0  # exit status of the last command: `$?`, and the prompt's [✗ N]
    # Shell variables (`x=1`, loop variables after a loop) and the ones exported with `export`.
    variables: dict[str, str] = field(default_factory=dict)
    exported: dict[str, str] = field(default_factory=dict)
    # Shell functions (`name() { …; }`, core.script.Function) and the arguments of the calls in
    # progress (`$1`, `$@`, `$#` read the innermost). Never used over MCP.
    functions: dict[str, Any] = field(default_factory=dict)
    positional: list[list[str]] = field(default_factory=list)
    # Where a script (`for`, `if`, `a ; b`) sends each statement's output and errors as it runs.
    # The REPL sets these to print as it goes; when they are None (MCP, tests) the output is
    # collected and returned as one text.
    emit: Callable[[Any], None] | None = None
    # Where a pipeline's output goes as it is produced, once it keeps coming (core.streams): the
    # REPL prints each item, a background job appends it. None: the output is collected.
    live: Callable[[Any], None] | None = None
    emit_error: Callable[[Exception, str], None] | None = None
    # The error stream (`echo oops >&2`): the innermost entry of `stderr_sinks` (a function call or
    # a block with `2> file` / `2>&1` pushes one), else `emit_stderr` (the REPL prints it in the
    # error style), else the process's stderr (pipeline.write_stderr).
    emit_stderr: Callable[[str], None] | None = None
    stderr_sinks: list[Callable[[str], None]] = field(default_factory=list)


@dataclass
class Styled:
    """Output with two faces: `renderable` for the screen, `text` for pipes, files and MCP."""

    renderable: Any
    text: str


@dataclass
class SoftWrapped:
    """Output whose long lines must not be broken by Rich (a command line to copy, such as
    `claude mcp add …`): printed with soft wrapping, so the terminal folds a long line on screen
    but a copy of it is still one line. Pipes, files and MCP get it unbroken too."""

    value: Any


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
    soft = False
    while isinstance(value, (Paged, WithStatus, Styled, SoftWrapped)):  # wrappers nest in any order
        soft = soft or isinstance(value, SoftWrapped)
        value = value.text if isinstance(value, Styled) else value.value
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    from collections.abc import Iterator

    if isinstance(value, Iterator) and not isinstance(value, bytes):  # a streamed result: drain it
        parts = [to_text(item, width) for item in value]
        return "".join(p if p.endswith("\n") or not p else p + "\n" for p in parts)
    if width is None:
        width = _ui_width() or 100
    buf = io.StringIO()
    # Uses a preset theme so sc.* style names resolve even without a UI.
    console = Console(file=buf, width=width, color_system=None, force_terminal=False, highlight=False,
                      theme=PRESETS["nord"].rich_theme())
    console.print(value, soft_wrap=soft)
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
