"""Console diagnostics: a plain-text report of what the shell detected about its terminal.

Printed at startup when the `diagnostics` setting is on (or with --diag), so it can be copied into a
bug report. It shows each size source next to the others (the OS per file descriptor, prompt_toolkit,
Rich, and on Windows the CONOUT$ screen buffer), the color depth each side picked and why, the
console modes and code pages, and the environment variables that change any of them.
"""

from __future__ import annotations

import locale
import os
import platform
import shutil
import sys
from typing import Any, Callable

from core import __version__

# Console mode flags, by name (SetConsoleMode).
_OUTPUT_FLAGS = {
    0x0001: "PROCESSED_OUTPUT", 0x0002: "WRAP_AT_EOL_OUTPUT", 0x0004: "VIRTUAL_TERMINAL_PROCESSING",
    0x0008: "DISABLE_NEWLINE_AUTO_RETURN", 0x0010: "LVB_GRID_WORLDWIDE",
}
_INPUT_FLAGS = {
    0x0001: "PROCESSED_INPUT", 0x0002: "LINE_INPUT", 0x0004: "ECHO_INPUT", 0x0008: "WINDOW_INPUT",
    0x0010: "MOUSE_INPUT", 0x0020: "INSERT_MODE", 0x0040: "QUICK_EDIT_MODE", 0x0080: "EXTENDED_FLAGS",
    0x0100: "AUTO_POSITION", 0x0200: "VIRTUAL_TERMINAL_INPUT",
}
_ENV_VARS = ("TERM", "COLORTERM", "TERM_PROGRAM", "TERM_PROGRAM_VERSION", "WT_SESSION", "WT_PROFILE_ID",
             "ConEmuANSI", "ANSICON", "COLUMNS", "LINES", "NO_COLOR", "FORCE_COLOR", "TTY_COMPATIBLE",
             "PROMPT_TOOLKIT_COLOR_DEPTH", "PYTHONIOENCODING", "PYTHONUTF8", "LANG", "LC_ALL", "TMUX",
             "SSH_TTY")


def _try(probe: Callable[[], Any]) -> str:
    try:
        return str(probe())
    except Exception as exc:  # noqa: BLE001 — a report line, never a crash
        return f"<{type(exc).__name__}: {exc}>"


def _flags(value: int | None, names: dict[int, str]) -> str:
    if value is None:
        return "<unknown>"
    known = [name for bit, name in names.items() if value & bit]
    rest = value & ~sum(names)
    return f"0x{value:04x} " + (" | ".join(known) or "-") + (f" | 0x{rest:x}" if rest else "")


def _stream(name: str, stream: Any) -> str:
    from core.stdio import is_console

    if stream is None:
        return f"{name}: None"
    return (f"{name}: fd={_try(stream.fileno)} isatty={_try(stream.isatty)} "
            f"is_console={_try(lambda: is_console(stream))} encoding={getattr(stream, 'encoding', None)} "
            f"errors={getattr(stream, 'errors', None)} type={type(stream).__name__}")


def _section(title: str, lines: list[str]) -> list[str]:
    return [f"[{title}]", *(f"  {line}" for line in lines)]


def console_report(ui: Any, windows_console: Any = None) -> str:
    """The report as plain text; `windows_console` is the shell's WindowsConsole, if any."""
    from prompt_toolkit.application.current import get_app_session
    from prompt_toolkit.output.color_depth import ColorDepth
    from rich.console import Console

    out: list[str] = ["----- shellcraft console diagnostics (copy from here) -----"]

    out += _section("system", [
        f"shellcraft {__version__}",
        f"python {sys.version.split()[0]} ({platform.python_implementation()}) {sys.executable}",
        f"platform {platform.platform()} sys.platform={sys.platform}",
        *([f"windows version {_try(sys.getwindowsversion)}"] if sys.platform == "win32" else []),
        f"cwd {os.getcwd()}",
    ])

    out += _section("std streams", [
        _stream("stdin", sys.stdin), _stream("stdout", sys.stdout), _stream("stderr", sys.stderr),
        f"utf8_mode={sys.flags.utf8_mode} preferred_encoding={_try(locale.getpreferredencoding)} "
        f"filesystem_encoding={sys.getfilesystemencoding()}",
    ])

    out += _section("environment", [f"{name}={os.environ.get(name)!r}" for name in _ENV_VARS
                                    if name in os.environ] or ["(none of the terminal variables are set)"])

    # Each size source on its own line: they should all agree with the real window.
    sizes = [f"os.get_terminal_size({fd}) = {_try(lambda fd=fd: tuple(os.get_terminal_size(fd)))}"
             for fd in (0, 1, 2)]
    sizes.append(f"shutil.get_terminal_size() = {_try(lambda: tuple(shutil.get_terminal_size()))} "
                 "(honors COLUMNS/LINES)")
    session_output = _try(lambda: get_app_session().output)
    sizes.append(f"prompt_toolkit output = {session_output}")
    sizes.append(f"prompt_toolkit get_size() = {_try(lambda: get_app_session().output.get_size())} "
                 "(rows, columns: what the prompt uses)")
    console = ui.console
    sizes.append(f"rich ui.console.size = {_try(lambda: tuple(console.size))} "
                 f"(columns, rows: what output uses) via {type(console).__name__}")
    sizes.append(f"rich plain Console().size = {_try(lambda: tuple(Console().size))} "
                 "(Rich's own detection, for comparison)")
    out += _section("size", sizes)

    out += _section("color", [
        f"rich ui.console: color_system={_try(lambda: console.color_system)} "
        f"is_terminal={_try(lambda: console.is_terminal)} legacy_windows={_try(lambda: console.legacy_windows)} "
        f"encoding={_try(lambda: console.encoding)} file={_try(lambda: type(console.file).__name__)}",
        f"rich plain Console(): color_system={_try(lambda: Console().color_system)} "
        f"is_terminal={_try(lambda: Console().is_terminal)} "
        f"legacy_windows={_try(lambda: Console().legacy_windows)}",
        f"prompt_toolkit default color depth = "
        f"{_try(lambda: get_app_session().output.get_default_color_depth())} "
        f"(from env: {_try(ColorDepth.from_env)})",
    ])

    if windows_console is not None:
        out += _section("windows console (CONOUT$ / CONIN$)", _windows_lines(windows_console))
    elif sys.platform == "win32":
        out += _section("windows console", ["not open: CONIN$/CONOUT$ could not be used, "
                                            "so the shell runs on sys.stdin/sys.stdout"])

    out.append("----- end of shellcraft console diagnostics -----")
    return "\n".join(out)


def _windows_lines(console: Any) -> list[str]:
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]  # Windows only
    k32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k32.GetStdHandle.argtypes = [wintypes.DWORD]
    k32.GetStdHandle.restype = wintypes.HANDLE
    k32.GetFileType.argtypes = [wintypes.HANDLE]
    k32.GetConsoleWindow.restype = wintypes.HWND

    def mode(handle: Any) -> int | None:
        value = wintypes.DWORD()
        return value.value if k32.GetConsoleMode(handle, ctypes.byref(value)) else None

    lines: list[str] = []
    info = console.buffer_info()
    if info is None:
        lines.append(f"GetConsoleScreenBufferInfo failed: {ctypes.WinError(ctypes.get_last_error())}")
    else:
        w = info.srWindow
        lines += [
            f"buffer dwSize = {info.dwSize.X} x {info.dwSize.Y} (columns x rows, includes scrollback)",
            f"window srWindow = left {w.Left} top {w.Top} right {w.Right} bottom {w.Bottom} "
            f"-> {w.Right - w.Left + 1} x {w.Bottom - w.Top + 1} visible",
            f"cursor = column {info.dwCursorPosition.X} row {info.dwCursorPosition.Y}, "
            f"max window = {info.dwMaximumWindowSize.X} x {info.dwMaximumWindowSize.Y}",
        ]
    lines += [
        f"shell size = {_try(console.size)} (full_width={console.full_width}: "
        f"{'delayed wrap on, whole width used' if console.full_width else 'last column left out'})",
        f"CONOUT$ mode before = {_flags(console.output_mode_before, _OUTPUT_FLAGS)}",
        f"CONOUT$ mode now    = {_flags(mode(console.conout), _OUTPUT_FLAGS)}",
        f"CONIN$ mode now     = {_flags(mode(console.conin), _INPUT_FLAGS)}",
        f"code pages: input {k32.GetConsoleCP()} output {k32.GetConsoleOutputCP()} (65001 = UTF-8)",
    ]
    for name, which in (("STD_INPUT", -10), ("STD_OUTPUT", -11), ("STD_ERROR", -12)):
        handle = k32.GetStdHandle(which)
        # GetFileType: 1 disk file, 2 character device (console or NUL), 3 pipe.
        lines.append(f"{name} handle={handle} file_type={k32.GetFileType(handle)} "
                     f"console_mode={_flags(mode(handle), _OUTPUT_FLAGS if which != -10 else _INPUT_FLAGS)}")
    lines.append(f"CONIN$ handle={console.conin.value} CONOUT$ handle={console.conout.value}")
    lines.append(f"console window hwnd={k32.GetConsoleWindow()} "
                 f"(Windows Terminal: {'yes' if 'WT_SESSION' in os.environ else 'no'})")
    return lines
