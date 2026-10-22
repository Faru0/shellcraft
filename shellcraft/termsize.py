"""The terminal's width before prompt_toolkit is imported, measured the way the shell measures it.

The early banner prints before the shell's console exists. A plain Rich console guesses: it lets a
COLUMNS variable win and falls back to 80 columns when stdout can't be measured (on Windows, a
stdout that isn't the console window), and then the banner comes out narrow, with the small title.
The shell itself asks the console window (Windows: CONOUT$'s visible window, without its last
column, as prompt_toolkit's Win32Output does; elsewhere: the terminal on stdout, stderr or stdin),
so this does the same and the two banners match.
"""

from __future__ import annotations

import os
import sys


def columns() -> int | None:
    """The terminal's usable width in columns, or None when there is no terminal to ask."""
    if sys.platform == "win32":
        return _windows_columns()
    for fd in (1, 2, 0):
        try:
            width = os.get_terminal_size(fd).columns
        except (OSError, ValueError):
            continue
        if width > 0:
            return width
    return None


def _windows_columns() -> int | None:
    try:
        import ctypes
        from ctypes import wintypes

        class Coord(ctypes.Structure):
            _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]

        class Rect(ctypes.Structure):
            _fields_ = [("Left", wintypes.SHORT), ("Top", wintypes.SHORT), ("Right", wintypes.SHORT),
                        ("Bottom", wintypes.SHORT)]

        class BufferInfo(ctypes.Structure):
            _fields_ = [("dwSize", Coord), ("dwCursorPosition", Coord), ("wAttributes", wintypes.WORD),
                        ("srWindow", Rect), ("dwMaximumWindowSize", Coord)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]  # Windows only
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        k32.GetConsoleScreenBufferInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        generic_rw, share_rw, open_existing = 0xC0000000, 0x3, 3
        handle = k32.CreateFileW("CONOUT$", generic_rw, share_rw, None, open_existing, 0, None)
        if handle in (None, wintypes.HANDLE(-1).value):
            return None
        try:
            info = BufferInfo()
            if not k32.GetConsoleScreenBufferInfo(handle, ctypes.byref(info)):
                return None
        finally:
            k32.CloseHandle(handle)
        # As shellcraft.console.window_size: the visible window without its last column.
        width = min(info.dwSize.X - 1, info.srWindow.Right - info.srWindow.Left)
        return width if width > 0 else None
    except (OSError, AttributeError, ValueError):
        return None
