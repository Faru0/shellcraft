"""The interactive shell's terminal: prompt_toolkit's input / output and the Rich console.

On Windows the shell talks to the console devices directly: keys come from CONIN$ through
prompt_toolkit's Win32Input, and output goes to CONOUT$ as VT sequences. sys.stdin / sys.stdout and
the process's std handles are never used or changed, so a redirected stdin or stdout (or NUL, for
which isatty() is True) can't reach the prompt.

The output side copies prompt_toolkit's own Windows10_Output, which is what works in Windows Terminal:
each flush writes with the screen buffer in exactly PROCESSED_OUTPUT | VIRTUAL_TERMINAL_PROCESSING
(no wrap at EOL, no delayed wrap), and the size leaves out the last column ("windows will wrap
otherwise"). Rich measures itself from the same output, so a full-width Rich line never reaches the
last column either. Earlier versions kept CONOUT$ in delayed-wrap mode (DISABLE_NEWLINE_AUTO_RETURN)
with the full width, and in Windows Terminal (ConPTY) the cursor and prompt_toolkit's picture of the
screen drifted apart: a garbled prompt, Backspace and Tab completion that seemed to do nothing.

SHELLCRAFT_CONSOLE chooses the setup, to compare them on a machine where something is wrong:
`direct` (the default), `legacy-keys` (direct, but the classic key reader without VT input) or
`native` (prompt_toolkit's own console handling on the std handles).
"""

from __future__ import annotations

import contextlib
import functools
import os
import sys
from dataclasses import dataclass
from typing import Any

from prompt_toolkit.application.current import get_app_session
from prompt_toolkit.data_structures import Size
from prompt_toolkit.output.color_depth import ColorDepth
from prompt_toolkit.output.vt100 import Vt100_Output
from rich.console import Console, ConsoleDimensions

# Console mode flags (SetConsoleMode).
ENABLE_PROCESSED_OUTPUT = 0x0001
ENABLE_WRAP_AT_EOL_OUTPUT = 0x0002
ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
DISABLE_NEWLINE_AUTO_RETURN = 0x0008
ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0200

# Between prompts (Rich output, spinners, error panels): VT sequences on, long lines wrap.
RESTING_OUTPUT_MODE = ENABLE_PROCESSED_OUTPUT | ENABLE_WRAP_AT_EOL_OUTPUT | ENABLE_VIRTUAL_TERMINAL_PROCESSING
# While prompt_toolkit writes: exactly what Windows10_Output.flush() sets.
FLUSH_OUTPUT_MODE = ENABLE_PROCESSED_OUTPUT | ENABLE_VIRTUAL_TERMINAL_PROCESSING

CONSOLE_MODES = ("direct", "legacy-keys", "native")


def console_mode() -> str:
    """The SHELLCRAFT_CONSOLE setting: direct (default), legacy-keys or native."""
    value = os.environ.get("SHELLCRAFT_CONSOLE", "").strip().lower()
    return value if value in CONSOLE_MODES else "direct"


# ── Rich ─────────────────────────────────────────────────────────────────────

class _SyncedConsole(Console):
    """A Rich console that measures the terminal the way prompt_toolkit does, by asking the current
    app session's output, so the prompt and Rich always agree on the size and follow resizes.

    Rich alone tries stdin, stdout, then stderr (only stdout and stderr on Windows) and lets COLUMNS /
    LINES override them, which are a snapshot from when the shell started if something exported them.
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


def build_console(file: Any = None, **kwargs: Any) -> Console:
    """The shell's Rich console: always a terminal, VT sequences (never legacy Win32 console calls),
    truecolor, sized like the prompt. `file` is the direct CONOUT$ stream on Windows, else stdout."""
    options: dict[str, Any] = dict(highlight=False, force_terminal=True, legacy_windows=False,
                                   color_system="truecolor")
    options.update(kwargs)
    return _SyncedConsole(file=file, **options)


# ── Windows console ──────────────────────────────────────────────────────────

@functools.cache
def _kernel32() -> Any:
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]  # Windows only
    k32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.GetConsoleScreenBufferInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    return k32


def get_mode(handle: Any) -> int | None:
    import ctypes
    from ctypes import wintypes

    mode = wintypes.DWORD()
    return mode.value if _kernel32().GetConsoleMode(handle, ctypes.byref(mode)) else None


def set_mode(handle: Any, mode: int) -> bool:
    return bool(_kernel32().SetConsoleMode(handle, mode))


def buffer_info(conout: Any) -> Any:
    """GetConsoleScreenBufferInfo, or None when it fails."""
    import ctypes

    from prompt_toolkit.win32_types import CONSOLE_SCREEN_BUFFER_INFO

    info = CONSOLE_SCREEN_BUFFER_INFO()
    return info if _kernel32().GetConsoleScreenBufferInfo(conout, ctypes.byref(info)) else None


def window_size(info: Any) -> Size:
    """The size prompt_toolkit's Win32Output reports: the visible window (srWindow, not dwSize, whose
    height is the whole scrollback), without its last column, capped below the buffer width."""
    window = info.srWindow
    columns = min(info.dwSize.X - 1, window.Right - window.Left)
    return Size(rows=max(1, window.Bottom - window.Top + 1), columns=max(1, columns))


class DirectOutput(Vt100_Output):
    """prompt_toolkit's Windows10_Output, bound to our CONOUT$ handle instead of GetStdHandle.

    Each flush writes with the screen buffer in exactly FLUSH_OUTPUT_MODE and restores the mode after.
    The size and the rows below the cursor come from the screen buffer, as Win32Output does. CPR is
    off: the answer could arrive as keys, and the rows below the cursor make it unnecessary."""

    def __init__(self, stream: Any, conout: Any):
        super().__init__(stream, self._size, enable_cpr=False,
                         default_color_depth=ColorDepth.from_env() or ColorDepth.TRUE_COLOR)
        self.conout = conout

    def _size(self) -> Size:
        info = buffer_info(self.conout)
        return Size(rows=24, columns=79) if info is None else window_size(info)

    def flush(self) -> None:
        if not self._buffer:
            return
        before = get_mode(self.conout)
        set_mode(self.conout, FLUSH_OUTPUT_MODE)
        try:
            super().flush()
        finally:
            if before is not None:
                set_mode(self.conout, before)

    def get_rows_below_cursor_position(self) -> int:
        info = buffer_info(self.conout)
        if info is None:
            raise NotImplementedError  # the renderer then works without it
        return info.srWindow.Bottom - info.dwCursorPosition.Y + 1


@dataclass
class DirectConsole:
    """What create_prompt_toolkit_console() opened: prompt_toolkit's input and output, the CONOUT$
    text stream for Rich, and the raw handles and modes for the diagnostics report."""

    input: Any                  # Win32Input on CONIN$
    output: Any                 # DirectOutput on CONOUT$
    stream: Any                 # CONOUT$ as a text stream (WriteConsoleW: any Unicode, any code page)
    conin: Any                  # HANDLE
    conout: Any                 # HANDLE
    output_mode_before: int | None
    input_mode_before: int | None
    reader: str                 # the key reader prompt_toolkit uses
    vt_input: bool              # keys arrive as VT sequences (ENABLE_VIRTUAL_TERMINAL_INPUT)
    mode: str = "direct"        # SHELLCRAFT_CONSOLE

    def buffer_info(self) -> Any:
        return buffer_info(self.conout)

    def size(self) -> Size:
        return self.output.get_size()


# What _console_input() uses from prompt_toolkit.input.win32 beyond its public API. Checked up
# front, so a prompt_toolkit that changed them is a clear error, not a console in the wrong mode.
WIN32_INTERNALS = ("_Win32InputBase", "Win32Input", "ConsoleInputReader", "Vt100ConsoleInputReader",
                   "raw_mode", "cooked_mode")


def check_win32_internals(win32: Any) -> None:
    import prompt_toolkit

    missing = [name for name in WIN32_INTERNALS if not hasattr(win32, name)]
    if missing:
        raise OSError(f"prompt_toolkit {prompt_toolkit.__version__} has no {', '.join(missing)} "
                      "in prompt_toolkit.input.win32 (shellcraft needs prompt_toolkit 3.0.x)")


def check_handle(obj: Any) -> None:
    """The reader and the console-mode objects keep the console in .handle; we replace it."""
    if not hasattr(obj, "handle"):
        import prompt_toolkit

        raise OSError(f"prompt_toolkit {prompt_toolkit.__version__}: {type(obj).__name__} has no .handle "
                      "(shellcraft needs prompt_toolkit 3.0.x)")


def _vt_input_supported(conin: Any) -> bool:
    """Whether CONIN$ accepts ENABLE_VIRTUAL_TERMINAL_INPUT (prompt_toolkit's own test, on CONIN$)."""
    before = get_mode(conin)
    if before is None:
        return False
    try:
        return set_mode(conin, ENABLE_VIRTUAL_TERMINAL_INPUT)
    finally:
        set_mode(conin, before)


def _console_input(conin_fd: int, vt_keys: bool) -> tuple[Any, Any]:
    """prompt_toolkit's Win32Input, bound to our CONIN$ handle; returns (input, handle).

    Win32Input finds the console with GetStdHandle(STD_INPUT_HANDLE) in three places: the key
    reader, raw / cooked mode and the VT-input check. Each one is pointed at CONIN$ here instead, so
    the handle that reads the keys is the handle that is put into raw mode. `vt_keys` False forces
    the classic reader (key events, no VT input), as before prompt_toolkit 3.0.49.
    """
    import msvcrt
    from ctypes import wintypes

    from prompt_toolkit.input import win32

    check_win32_internals(win32)
    conin = wintypes.HANDLE(msvcrt.get_osfhandle(conin_fd))  # type: ignore[attr-defined]  # Windows only
    use_vt = vt_keys and _vt_input_supported(conin)

    class ConsoleInput(win32.Win32Input):
        def __init__(self) -> None:
            win32._Win32InputBase.__init__(self)  # not Win32Input's: it looks at the std handle
            self._use_virtual_terminal_input = use_vt
            reader = win32.Vt100ConsoleInputReader() if use_vt else win32.ConsoleInputReader()
            reader.close()  # it opens its own CONIN$ when sys.stdin isn't a tty
            check_handle(reader)
            reader._fdcon = None
            reader.handle = conin
            self.console_input_reader = reader

        def _on_conin(self, mode: Any) -> Any:
            check_handle(mode)
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
            pass  # create_prompt_toolkit_console's cleanup closes CONIN$

    return ConsoleInput(), conin


def create_prompt_toolkit_console(cleanup: contextlib.ExitStack) -> DirectConsole | None:
    """Open CONIN$ / CONOUT$ for the interactive shell on Windows; None elsewhere.

    Turns on VT output, binds a Win32Input to CONIN$ and a DirectOutput to CONOUT$, and returns them
    with the CONOUT$ text stream for Rich. Raises OSError, with the reason, when there is no usable
    console (none at all, or one that can't process VT sequences: before Windows 10), when
    prompt_toolkit's Win32 input changed, or when SHELLCRAFT_CONSOLE=native asks for prompt_toolkit's
    own console handling. Both console modes are restored, and both devices closed, by `cleanup`.
    """
    if sys.platform != "win32":
        return None
    mode = console_mode()
    if mode == "native":
        raise OSError("SHELLCRAFT_CONSOLE=native")
    import ctypes
    import msvcrt
    from ctypes import wintypes

    with contextlib.ExitStack() as opened:
        conin_fd = os.open("CONIN$", os.O_RDWR | os.O_BINARY)  # type: ignore[attr-defined]
        opened.callback(os.close, conin_fd)
        # By name, Python opens CONOUT$ as a console stream (WriteConsoleW), so Unicode is safe
        # whatever the console code page is. It opens it for reading too, which the mode calls need.
        stream = open("CONOUT$", "w", encoding="utf-8", errors="replace")  # noqa: SIM115
        opened.callback(stream.close)
        conout = wintypes.HANDLE(msvcrt.get_osfhandle(stream.fileno()))  # type: ignore[attr-defined]

        output_before = get_mode(conout)
        if output_before is None:
            raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
        # Delayed wrap off: it is what made the cursor drift in Windows Terminal.
        resting = (output_before | RESTING_OUTPUT_MODE) & ~DISABLE_NEWLINE_AUTO_RETURN
        if not set_mode(conout, resting):
            raise ctypes.WinError(ctypes.get_last_error())  # type: ignore[attr-defined]
        opened.callback(set_mode, conout, output_before)

        console_input, conin = _console_input(conin_fd, vt_keys=mode != "legacy-keys")
        # raw_mode restores CONIN$ after each prompt; this covers a crash in the middle of one.
        input_before = get_mode(conin)
        if input_before is not None:
            opened.callback(set_mode, conin, input_before)

        output = DirectOutput(stream, conout)
        cleanup.push(opened.pop_all())
        reader = type(console_input.console_input_reader).__name__
        return DirectConsole(input=console_input, output=output, stream=stream, conin=conin, conout=conout,
                             output_mode_before=output_before, input_mode_before=input_before,
                             reader=reader, vt_input=console_input._use_virtual_terminal_input, mode=mode)


# ── Linux / macOS ────────────────────────────────────────────────────────────

def truecolor_output() -> Vt100_Output | None:
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
