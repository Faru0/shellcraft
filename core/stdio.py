"""stdin / stdout / stderr helpers that behave the same on Windows and Linux.

- is_console(): whether a stream is really the terminal. On Windows isatty() is True for any
  character device (NUL included), so the console is detected with GetConsoleMode instead.
- utf8_stdio(): output that goes to a pipe or file is UTF-8. Python on Windows otherwise uses the
  ANSI code page (cp1252) there, which can't encode the box drawing in `tree`, tables and error
  panels, and the write fails with UnicodeEncodeError. It also matches the UTF-8 that `>` writes.

The interactive shell's Windows console (CONIN$ / CONOUT$) is opened by core.shell.open_windows_console.
"""

from __future__ import annotations

import codecs
import os
import sys
from typing import Any


def is_console(stream: Any) -> bool:
    """True when `stream` is the terminal itself (not a pipe, a file or NUL)."""
    try:
        fd = stream.fileno()
    except (AttributeError, OSError, ValueError):  # None under pythonw.exe, or a StringIO
        return False
    if sys.platform != "win32":
        return os.isatty(fd)
    import ctypes
    import msvcrt
    from ctypes import wintypes

    try:
        handle = msvcrt.get_osfhandle(fd)  # type: ignore[attr-defined]  # Windows only
    except OSError:
        return False
    kernel32 = ctypes.WinDLL("kernel32")  # type: ignore[attr-defined]
    kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    mode = wintypes.DWORD()
    return bool(kernel32.GetConsoleMode(wintypes.HANDLE(handle), ctypes.byref(mode)))


def utf8_stdio() -> None:
    """Make stdout and stderr write UTF-8 unless they already do (or PYTHONIOENCODING chooses)."""
    if os.environ.get("PYTHONIOENCODING"):
        return
    for stream in (sys.stdout, sys.stderr):
        encoding = getattr(stream, "encoding", None)
        if not encoding or not hasattr(stream, "reconfigure"):
            continue
        try:
            if codecs.lookup(encoding).name == "utf-8":
                continue
        except LookupError:
            pass
        stream.reconfigure(encoding="utf-8", errors="replace")
