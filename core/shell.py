"""Interactive REPL built on prompt_toolkit."""

from __future__ import annotations

import contextlib
import getpass
import os
import socket
import sys
from pathlib import Path
from typing import Any, Callable

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

_STD_INPUT_HANDLE, _STD_OUTPUT_HANDLE = -10, -11
# Output mode flags: process \n etc., wrap at the last column, and interpret VT escape sequences.
_VT_OUTPUT_MODE = 0x0001 | 0x0002 | 0x0004
# DISABLE_NEWLINE_AUTO_RETURN: delayed wrap, as on a VT terminal. Writing the last column leaves the
# cursor there until the next character, so a full-width line followed by \r\n is one row, not two.
# A bare \n then no longer returns the carriage, but every writer here sends \r\n: prompt_toolkit's
# renderer does, and Python's text streams on Windows translate \n.
_DELAYED_WRAP = 0x0008


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
    On Windows the session's output is WindowsConsole, which reads CONOUT$ directly, whatever the
    std handles or file descriptors point at. Elsewhere it is stdout's terminal (TIOCGWINSZ).
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


class _ConsoleOutput(Vt100_Output):
    """VT output to CONOUT$ that can also report the rows below the cursor, like Win32Output does,
    so completion menus get the room they need."""

    def __init__(self, console: WindowsConsole):
        super().__init__(console.stream, console.size, enable_cpr=False,  # no CPR: the input is Win32
                         default_color_depth=ColorDepth.from_env() or ColorDepth.TRUE_COLOR)
        self._console = console

    def get_rows_below_cursor_position(self) -> int:
        info = self._console.buffer_info()
        if info is None:
            raise NotImplementedError  # the renderer then falls back to the window height
        return info.srWindow.Bottom - info.dwCursorPosition.Y + 1


class WindowsConsole:
    """The interactive shell's terminal on Windows: CONIN$ for keys, CONOUT$ with VT sequences on.

    sys.stdin / sys.stdout may be redirected, and isatty() is True for any character device (NUL
    included), so prompt_toolkit can end up reading keys from CONIN$ while it puts the std input
    handle into raw mode, which leaves echo and line input on. Here both sides always use the
    console devices, and the process's std handles point at them too, because prompt_toolkit
    (raw mode, its VT-input check) looks the console up with GetStdHandle. The size is always read
    from CONOUT$ itself (see size() and TerminalConsole). Raises OSError when there is no console or it can't process VT sequences (before Windows 10).
    """

    def __init__(self) -> None:
        import ctypes
        import msvcrt
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetStdHandle.argtypes = [wintypes.DWORD]
        k32.GetStdHandle.restype = wintypes.HANDLE
        k32.SetStdHandle.argtypes = [wintypes.DWORD, wintypes.HANDLE]
        k32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k32.GetConsoleScreenBufferInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        self._k32, self._ctypes = k32, ctypes
        self._fds: list[int] = []
        self._undo: list[Callable[[], Any]] = []
        self.stream: Any = None

        def check(ok: Any) -> None:
            if not ok:
                raise ctypes.WinError(ctypes.get_last_error())

        def device(name: str) -> Any:
            fd = os.open(name, os.O_RDWR | os.O_BINARY)  # type: ignore[attr-defined]  # Windows only
            self._fds.append(fd)
            return wintypes.HANDLE(msvcrt.get_osfhandle(fd))  # type: ignore[attr-defined]

        try:
            conin, self._conout = device("CONIN$"), device("CONOUT$")
            mode = wintypes.DWORD()
            check(k32.GetConsoleMode(self._conout, ctypes.byref(mode)))
            # Prefer delayed wrap, so the whole window width is usable; without it (older consoles
            # refuse the flag) the size leaves out the last column, as Win32Output does.
            self.full_width = bool(k32.SetConsoleMode(self._conout, mode.value | _VT_OUTPUT_MODE | _DELAYED_WRAP))
            if not self.full_width:
                check(k32.SetConsoleMode(self._conout, mode.value | _VT_OUTPUT_MODE))
            self._undo.append(lambda: k32.SetConsoleMode(self._conout, mode.value))
            for which, handle in ((_STD_INPUT_HANDLE, conin), (_STD_OUTPUT_HANDLE, self._conout)):
                previous = k32.GetStdHandle(which)
                check(k32.SetStdHandle(which, handle))
                self._undo.append(lambda w=which, h=previous: k32.SetStdHandle(w, h))
            # By name, Python opens CONOUT$ as a console stream (WriteConsoleW), so Unicode is safe
            # whatever the console code page is.
            self.stream = open("CONOUT$", "w", encoding="utf-8", errors="replace")  # noqa: SIM115

            from prompt_toolkit.input.win32 import Win32Input

            self.input = Win32Input()  # created after SetStdHandle, so it reads CONIN$
            self.output = _ConsoleOutput(self)
        except BaseException:
            self.close()
            raise

    def buffer_info(self) -> Any:
        from prompt_toolkit.win32_types import CONSOLE_SCREEN_BUFFER_INFO

        info = CONSOLE_SCREEN_BUFFER_INFO()
        if not self._k32.GetConsoleScreenBufferInfo(self._conout, self._ctypes.byref(info)):
            return None
        return info

    def size(self) -> Size:
        info = self.buffer_info()
        return Size(rows=24, columns=80) if info is None else _window_size(info, self.full_width)

    def session(self) -> Any:
        """Context manager: prompt_toolkit apps inside it (prompt, pager, password prompt) use this console."""
        return create_app_session(input=self.input, output=self.output)

    def close(self) -> None:
        """Restore the std handles and the console mode, and close the console devices."""
        if getattr(self, "input", None) is not None:
            self.input.close()
            self.input = None
        if self.stream is not None:
            try:
                self.stream.close()
            except OSError:
                pass
            self.stream = None
        while self._undo:
            self._undo.pop()()
        while self._fds:
            try:
                os.close(self._fds.pop())
            except OSError:
                pass


def open_windows_console() -> WindowsConsole | None:
    """The console I/O for the interactive shell on Windows; None elsewhere, or without a usable console."""
    if sys.platform != "win32":
        return None
    try:
        return WindowsConsole()
    except OSError:
        return None


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
        try:
            self._open_console()
            self._setup()
        except BaseException:
            self.close()
            raise

    def _open_console(self) -> None:
        """Windows: run on CONIN$ / CONOUT$ (WindowsConsole). The app session must be current before
        the PromptSession is built, because prompt_toolkit binds an Application's input and output
        when it is created; the pager and the hidden API-key prompt pick it up the same way."""
        console = open_windows_console()
        if console is None:
            return
        self._cleanup.callback(console.close)
        self._cleanup.enter_context(console.session())
        console_file = console.stream
        # VT processing is on, so Rich writes plain escape sequences (no legacy Win32 console calls).
        # Its size comes from CONOUT$, the same as the prompt's, so it follows resizes.
        previous = self.ui.use_console(TerminalConsole(file=console_file, highlight=False, force_terminal=True,
                                                       legacy_windows=False, color_system="truecolor"))
        self._cleanup.callback(self.ui.use_console, previous)

    def _setup(self) -> None:
        ctx = self.ctx
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

    def close(self) -> None:
        """Put back the Rich console, leave the app session, restore the std handles and the
        console mode, and close the console devices (in that order)."""
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
        if self.last_failed:
            parts += [("class:sep", " "), ("class:failed", "[✗]")]
        parts += [("", "\n"), ("class:frame", "╰─"), ("class:arrow", "❯ ")]
        return FormattedText(parts)

    def banner(self) -> None:
        self.ui.console.print(render_banner(self.ui, self.ctx.registry))

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
