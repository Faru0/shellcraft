"""Interactive REPL built on prompt_toolkit."""

from __future__ import annotations

import contextlib
import getpass
import os
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.application.current import create_app_session, get_app_session
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.data_structures import Size
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.history import FileHistory, InMemoryHistory
from prompt_toolkit.output.color_depth import ColorDepth
from prompt_toolkit.output.vt100 import Vt100_Output
from prompt_toolkit.styles import DynamicStyle
from rich.console import Console, ConsoleDimensions
from rich.text import Text

from core import settings
from core.banner import render_banner
from core.completer import ShellCompleter
from core.config import history_path
from core.context import ShellContext, ShellExit
from core.output import make_spinner_runner, show, show_error
from core.parser import ParseError
from core.pipeline import PipelineError, run_line
from core.watch import ModuleWatcher


def _pretty_cwd(cwd: str | None = None, home: str | None = None) -> str:
    cwd = os.getcwd() if cwd is None else cwd
    home = str(Path.home()) if home is None else home
    # normcase: Windows paths are case-insensitive (C:\Users\Me vs c:\users\me).
    folded, folded_home = os.path.normcase(cwd), os.path.normcase(home)
    if folded == folded_home:
        return "~"
    if folded.startswith(folded_home.rstrip(os.sep) + os.sep):
        return "~" + cwd[len(home.rstrip(os.sep)):]
    return cwd


def _user_host() -> str:
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001 — no login name (containers, odd Windows setups)
        user = "user"
    return f"{user}@{socket.gethostname().split('.')[0]}"


class _RedactingHistory:
    """Mixin: store `settings NAME ••••` instead of an API key typed on the command line.

    A line typed with a leading space is not saved at all (bash's `ignorespace`).
    """

    def append_string(self, string: str) -> None:
        if string[:1].isspace():
            return
        super().append_string(settings.redact_line(string))


class RedactingFileHistory(_RedactingHistory, FileHistory):
    pass


class RedactingInMemoryHistory(_RedactingHistory, InMemoryHistory):
    pass


# ── Windows console ──────────────────────────────────────────────────────────
#
# On Windows the interactive shell talks to the console devices directly: keys come from CONIN$
# through prompt_toolkit's Win32Input, and output goes to CONOUT$ as VT sequences through a
# Vt100_Output. sys.stdin / sys.stdout and the process's std handles are never used or changed, so
# a redirected stdin or stdout (or NUL, for which isatty() is True) can't reach the prompt.

_ENABLE_PROCESSED_OUTPUT, _ENABLE_WRAP_AT_EOL_OUTPUT = 0x0001, 0x0002
_ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
# Delayed wrap, as on a VT terminal: writing the last column leaves the cursor there until the next
# character, so a full-width line followed by \r\n is one row, not two. Every writer here sends
# \r\n (prompt_toolkit's renderer, and Python's text streams on Windows translate \n).
_DISABLE_NEWLINE_AUTO_RETURN = 0x0008
_ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0200
_VT_OUTPUT_MODE = _ENABLE_PROCESSED_OUTPUT | _ENABLE_WRAP_AT_EOL_OUTPUT | _ENABLE_VIRTUAL_TERMINAL_PROCESSING


def _window_size(info: Any, full_width: bool = True) -> Size:
    """The visible console window from GetConsoleScreenBufferInfo: srWindow, not dwSize, whose height
    is the whole scrollback (9001 rows in conhost) and whose width can exceed the window. Without
    delayed wrap, writing the last column moves the cursor at once, so that column is left out."""
    window = info.srWindow
    columns = min(info.dwSize.X, window.Right - window.Left + 1)
    return Size(rows=max(1, window.Bottom - window.Top + 1),
                columns=max(1, columns if full_width else columns - 1))


class TerminalConsole(Console):
    """A Rich console that measures the terminal the way prompt_toolkit does, by asking the current
    app session's output, so the prompt and Rich always agree on the size and follow resizes.

    Rich alone tries stdin, stdout, then stderr (only stdout and stderr on Windows) and lets COLUMNS /
    LINES override them, which are a snapshot from when the shell started if something exported them.
    On Windows the session's output reads the size from CONOUT$ itself. Elsewhere it is stdout's
    terminal (TIOCGWINSZ).
    """

    def __init__(self, *args: Any, width: int | None = None, height: int | None = None, **kwargs: Any):
        super().__init__(*args, width=width, height=height, **kwargs)
        # Rich's __init__ turns COLUMNS / LINES into a fixed size; only an explicit one stays fixed.
        self._width, self._height = width, height

    @property
    def size(self) -> ConsoleDimensions:
        if self._width is not None and self._height is not None:
            return super().size
        try:
            size = get_app_session().output.get_size()
        except Exception:  # noqa: BLE001 — no usable output: Rich's own lookup
            return super().size
        return ConsoleDimensions(self._width or size.columns, self._height or size.rows)

    @size.setter
    def size(self, new_size: tuple[int, int]) -> None:
        Console.size.fset(self, new_size)  # type: ignore[attr-defined]


@dataclass
class WindowsConsole:
    """What open_windows_console() opened: prompt_toolkit's input and output, the CONOUT$ text
    stream for Rich, and the raw handles and modes for the diagnostics report."""

    input: Any                  # Win32Input on CONIN$
    output: Vt100_Output        # VT sequences to CONOUT$
    stream: Any                 # CONOUT$ as a text stream (WriteConsoleW: any Unicode, any code page)
    conin: Any                  # HANDLE
    conout: Any                 # HANDLE
    output_mode_before: int
    full_width: bool            # delayed wrap is on, so the last column is usable

    def buffer_info(self) -> Any:
        return _buffer_info(self.conout)

    def size(self) -> Size:
        return _console_size(self.conout, self.full_width)


def _kernel32() -> Any:
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]  # Windows only
    k32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.GetConsoleScreenBufferInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    return k32


def _buffer_info(conout: Any) -> Any:
    import ctypes

    from prompt_toolkit.win32_types import CONSOLE_SCREEN_BUFFER_INFO

    info = CONSOLE_SCREEN_BUFFER_INFO()
    return info if _kernel32().GetConsoleScreenBufferInfo(conout, ctypes.byref(info)) else None


def _console_size(conout: Any, full_width: bool) -> Size:
    info = _buffer_info(conout)
    return Size(rows=24, columns=80) if info is None else _window_size(info, full_width)


def _console_input(conin_fd: int) -> Any:
    """prompt_toolkit's Win32Input, bound to our CONIN$ handle.

    Win32Input finds the console with GetStdHandle(STD_INPUT_HANDLE) in three places: the key
    reader, raw / cooked mode and the VT-input check. Each one is pointed at CONIN$ here instead, so
    the handle that reads the keys is the handle that is put into raw mode.
    """
    import ctypes
    import msvcrt
    from ctypes import wintypes

    from prompt_toolkit.input import win32

    conin = wintypes.HANDLE(msvcrt.get_osfhandle(conin_fd))  # type: ignore[attr-defined]  # Windows only

    def vt_input_supported() -> bool:
        k32 = _kernel32()
        mode = wintypes.DWORD()
        if not k32.GetConsoleMode(conin, ctypes.byref(mode)):
            return False
        try:
            return bool(k32.SetConsoleMode(conin, _ENABLE_VIRTUAL_TERMINAL_INPUT))
        finally:
            k32.SetConsoleMode(conin, mode.value)

    class ConsoleInput(win32.Win32Input):
        def __init__(self) -> None:
            win32._Win32InputBase.__init__(self)  # not Win32Input's: it looks at the std handle
            self._use_virtual_terminal_input = vt_input_supported()
            reader = (win32.Vt100ConsoleInputReader() if self._use_virtual_terminal_input
                      else win32.ConsoleInputReader())
            reader.close()  # it opens its own CONIN$ when sys.stdin isn't a tty
            reader._fdcon = None
            reader.handle = conin
            self.console_input_reader = reader

        def _on_conin(self, mode: Any) -> Any:
            mode.handle = conin
            return mode

        def raw_mode(self) -> Any:
            return self._on_conin(win32.raw_mode(
                use_win10_virtual_terminal_input=self._use_virtual_terminal_input))

        def cooked_mode(self) -> Any:
            return self._on_conin(win32.cooked_mode())

        def fileno(self) -> int:
            return conin_fd

        def close(self) -> None:
            pass  # the shell closes CONIN$

    return ConsoleInput(), conin


def open_windows_console(cleanup: contextlib.ExitStack) -> WindowsConsole | None:
    """Open CONIN$ / CONOUT$ with VT output on, for the interactive shell on Windows; None elsewhere,
    or without a usable console (none at all, or one that can't process VT sequences: before
    Windows 10). Everything opened or changed is undone by `cleanup`, in reverse order."""
    if sys.platform != "win32":
        return None
    import ctypes
    import msvcrt
    from ctypes import wintypes

    k32 = _kernel32()
    try:
        with contextlib.ExitStack() as opened:
            conin_fd = os.open("CONIN$", os.O_RDWR | os.O_BINARY)  # type: ignore[attr-defined]
            opened.callback(os.close, conin_fd)
            # By name, Python opens CONOUT$ as a console stream (WriteConsoleW), so Unicode is safe
            # whatever the console code page is.
            stream = open("CONOUT$", "w", encoding="utf-8", errors="replace")  # noqa: SIM115
            opened.callback(stream.close)
            conout = wintypes.HANDLE(msvcrt.get_osfhandle(stream.fileno()))  # type: ignore[attr-defined]

            mode = wintypes.DWORD()
            if not k32.GetConsoleMode(conout, ctypes.byref(mode)):
                raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
            before = mode.value
            # Prefer delayed wrap, so the whole width is usable; consoles that refuse it get the
            # last column left out of the size instead.
            full_width = bool(k32.SetConsoleMode(conout, before | _VT_OUTPUT_MODE | _DISABLE_NEWLINE_AUTO_RETURN))
            if not full_width and not k32.SetConsoleMode(conout, before | _VT_OUTPUT_MODE):
                raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
            opened.callback(k32.SetConsoleMode, conout, before)

            console_input, conin = _console_input(conin_fd)
            # No CPR: Win32Input may not be reading VT input, so a reply could arrive as keys.
            output = Vt100_Output(stream, lambda: _console_size(conout, full_width), enable_cpr=False,
                                  default_color_depth=ColorDepth.from_env() or ColorDepth.TRUE_COLOR)
            cleanup.push(opened.pop_all())
            return WindowsConsole(input=console_input, output=output, stream=stream, conin=conin,
                                  conout=conout, output_mode_before=before, full_width=full_width)
    except OSError:
        return None


def _truecolor_output() -> Vt100_Output | None:
    """A truecolor prompt_toolkit output on stdout when COLORTERM says the terminal has 24-bit color.

    prompt_toolkit only looks at TERM and stays at 256 colors otherwise, while Rich reads COLORTERM,
    so the prompt and the pager showed the theme's colors rounded off next to exact Rich output.
    None leaves prompt_toolkit's default: no such terminal, stdout isn't one, or NO_COLOR /
    PROMPT_TOOLKIT_COLOR_DEPTH choose the depth.
    """
    if os.environ.get("COLORTERM", "").lower() not in ("truecolor", "24bit"):
        return None
    if ColorDepth.from_env() is not None or not sys.stdout.isatty():
        return None
    return Vt100_Output.from_pty(sys.stdout, term=os.environ.get("TERM"),
                                 default_color_depth=ColorDepth.TRUE_COLOR)


class Shell:
    """The interactive REPL. Use it as a context manager: on Windows it owns the console session
    (see _open_console), and leaving the `with` block restores the console whatever happened."""

    def __init__(self, ctx: ShellContext):
        assert ctx.ui is not None
        self.ctx = ctx
        self.ui = ctx.ui
        self.last_failed = False
        self.user_host = _user_host()
        self._cleanup = contextlib.ExitStack()
        self.windows_console: WindowsConsole | None = None
        try:
            self._open_console()
            self._setup()
        except BaseException:
            self.close()
            raise

    def _open_console(self) -> None:
        """Windows: run on CONIN$ / CONOUT$ directly (Win32Input + Vt100_Output). Elsewhere: stdout,
        in truecolor when the terminal says it supports it. The app session must be current before
        the PromptSession is built, because prompt_toolkit binds an Application's input and output
        when it is created; the pager and the hidden API-key prompt pick it up the same way."""
        console = open_windows_console(self._cleanup)
        if console is None:
            output = _truecolor_output()
            if output is not None:
                self._cleanup.enter_context(create_app_session(output=output))
            return
        self.windows_console = console
        self._cleanup.enter_context(create_app_session(input=console.input, output=console.output))
        # VT processing is on, so Rich writes plain escape sequences (no legacy Win32 console calls).
        # Its size comes from the session's output, the same as the prompt's, so it follows resizes.
        previous = self.ui.use_console(TerminalConsole(file=console.stream, highlight=False, force_terminal=True,
                                                       legacy_windows=False, color_system="truecolor"))
        self._cleanup.callback(self.ui.use_console, previous)

    def _setup(self) -> None:
        ctx = self.ctx
        self._cleanup.callback(self._stop_mcp_http)  # registered last, so it runs first on close
        ctx.runner = make_spinner_runner(ctx)
        ctx.interactive = True
        self.watcher = ModuleWatcher(ctx.registry.directory)

        try:
            path = history_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            history = RedactingFileHistory(str(path))
        except OSError:
            history = RedactingInMemoryHistory()

        ctx.history = history
        self.session: PromptSession = PromptSession(
            history=history,
            auto_suggest=AutoSuggestFromHistory(),
            completer=ShellCompleter(ctx),
            complete_while_typing=True,
            style=DynamicStyle(lambda: self.ui.pt_style),
            include_default_pygments_style=False,
        )

    def _stop_mcp_http(self) -> None:
        if self.ctx.mcp_http is not None:
            self.ctx.mcp_http.stop()
            self.ctx.mcp_http = None

    def close(self) -> None:
        """Stop a server started with `mcp start`, put back the Rich console, leave the app
        session, restore the console mode and close CONIN$ / CONOUT$ (in that order)."""
        self._cleanup.close()

    def __enter__(self) -> Shell:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _prompt(self) -> FormattedText:
        parts = [
            ("class:frame", "╭─"), ("class:name", " ⚡shellcraft "), ("class:sep", "─ "),
            ("class:user", self.user_host), ("class:sep", " ─ "), ("class:path", _pretty_cwd()),
        ]
        parts += self._mcp_marker()
        if self.last_failed:
            parts += [("class:sep", " "), ("class:failed", "[✗]")]
        parts += [("", "\n"), ("class:frame", "╰─"), ("class:arrow", "❯ ")]
        return FormattedText(parts)

    def _mcp_marker(self) -> list[tuple[str, str]]:
        """`● mcp :8765` while a server started with `mcp start` runs; `✗ mcp :8765` if it died
        on its own (until `mcp` or `mcp stop` notices). Nothing when there is none."""
        child = self.ctx.mcp_http
        if child is None:
            return []
        address = f":{child.port}" if child.host in ("127.0.0.1", "localhost") else child.url.split("/")[2]
        if child.running():
            return [("class:sep", " ─ "), ("class:mcp", f"● mcp {address}")]
        return [("class:sep", " ─ "), ("class:mcp.down", f"✗ mcp {address}")]

    def banner(self) -> None:
        self.ui.console.print(render_banner(self.ui, self.ctx.registry))

    def diagnostics(self) -> None:
        """Print the console diagnostics report (the `diagnostics` setting, or --diag)."""
        from core.diagnostics import console_report

        # Plain text, not wrapped, so it can be copied into a bug report as is.
        self.ui.console.print(console_report(self.ui, self.windows_console),
                              markup=False, highlight=False, soft_wrap=True)

    def loop(self) -> int:
        while True:
            try:
                line = self.session.prompt(self._prompt)
            except KeyboardInterrupt:
                continue
            except EOFError:
                return self._goodbye(0)

            self._hot_reload()
            try:
                result = run_line(line, self.ctx)
            except ShellExit as exc:
                return self._goodbye(exc.code)
            except KeyboardInterrupt:
                self.ui.console.print(Text("^C interrupted", style="sc.warning"))
                self.last_failed = True
                continue
            except (ParseError, PipelineError) as exc:
                show_error(self.ui, exc, line)
                self.last_failed = True
                continue

            self.last_failed = False
            if result is not None:
                try:
                    show(self.ui, result.output, pager=settings.get(self.ctx.config, "pager"))
                except KeyboardInterrupt:
                    pass

    def _hot_reload(self) -> None:
        """Reload modules whose files changed since the last command (the `hot_reload` setting)."""
        changed = self.watcher.changes()  # always consumed, so turning the setting on starts fresh
        if not changed or not settings.get(self.ctx.config, "hot_reload"):
            return
        self.ctx.registry.load()
        msg = Text.assemble(("↻ ", "sc.accent"), (f"modules reloaded ({', '.join(changed)})", "sc.muted"))
        for warning in self.ctx.registry.warnings:
            msg.append(f"\n⚠ {warning}", style="sc.warning")
        self.ui.console.print(msg)

    def _goodbye(self, code: int) -> int:
        self.ui.console.print(Text("⏻ session closed — stay curious.", style="sc.muted"))
        return code
