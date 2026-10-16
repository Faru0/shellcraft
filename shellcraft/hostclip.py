"""Copy text to the host's clipboard (the system clipboard, not ShellCraft's `clip` register).

Two routes, no external program:

* OSC 52, an escape sequence the terminal itself turns into a clipboard write. Windows Terminal,
  kitty, WezTerm, foot, iTerm2, Alacritty and tmux honor it, and it works over ssh because it
  travels to the terminal you are sitting at. A terminal that ignores it simply shows nothing.
* On Windows, also the Win32 clipboard, set in-process with ctypes (the same calls pyperclip
  makes), for the terminals that do not honor OSC 52.
"""

from __future__ import annotations

import base64
import sys
import time
from collections.abc import Callable

MAX_BYTES = 60_000  # most terminals refuse an OSC 52 payload past ~100 KB of base64


def osc52(text: str) -> str:
    """The escape sequence that asks the terminal to put `text` on the clipboard."""
    data = text.encode("utf-8", errors="replace")[:MAX_BYTES]
    return "\x1b]52;c;" + base64.b64encode(data).decode("ascii") + "\x07"


def copy(text: str, write: Callable[[str], object] | None = None) -> bool:
    """Put `text` on the host clipboard. `write` sends a string to the terminal (OSC 52); on
    Windows the Win32 clipboard is set as well. True when some route accepted it."""
    if not text:
        return False
    done = False
    if write is not None:
        try:
            write(osc52(text))
            done = True
        except (OSError, ValueError):
            pass
    if sys.platform == "win32":
        done = _windows_clipboard(text) or done
    return done


def _windows_clipboard(text: str) -> bool:
    """CF_UNICODETEXT through user32 / kernel32, with every signature spelled out (the handles are
    64 bits wide). The clipboard needs a window to own it: a throwaway STATIC one, as pyperclip
    does, because OpenClipboard(NULL) followed by EmptyClipboard makes SetClipboardData fail."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        user32.CreateWindowExA.argtypes = [wintypes.DWORD, wintypes.LPCSTR, wintypes.LPCSTR, wintypes.DWORD,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                           wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
        user32.CreateWindowExA.restype = wintypes.HWND
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.DestroyWindow.restype = wintypes.BOOL
        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.CloseClipboard.argtypes = []
        user32.CloseClipboard.restype = wintypes.BOOL
        user32.EmptyClipboard.argtypes = []
        user32.EmptyClipboard.restype = wintypes.BOOL
        user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        user32.SetClipboardData.restype = wintypes.HANDLE
        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = wintypes.LPVOID
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.restype = wintypes.BOOL
        kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalFree.restype = wintypes.HGLOBAL
    except (ImportError, AttributeError, OSError):
        return False
    cf_unicodetext, gmem_moveable = 13, 0x0002
    data = (text.replace("\r\n", "\n").replace("\n", "\r\n") + "\0").encode("utf-16-le")
    owner = user32.CreateWindowExA(0, b"STATIC", None, 0, 0, 0, 0, 0, None, None, None, None)
    if not owner:
        return False
    try:
        for _ in range(10):  # another program may hold the clipboard for a moment
            if user32.OpenClipboard(owner):
                break
            time.sleep(0.02)
        else:
            return False
        try:
            user32.EmptyClipboard()
            handle = kernel32.GlobalAlloc(gmem_moveable, len(data))
            if not handle:
                return False
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                kernel32.GlobalFree(handle)
                return False
            ctypes.memmove(pointer, data, len(data))
            kernel32.GlobalUnlock(handle)
            if not user32.SetClipboardData(cf_unicodetext, handle):
                kernel32.GlobalFree(handle)
                return False
            return True  # the clipboard owns the memory now
        finally:
            user32.CloseClipboard()
    finally:
        user32.DestroyWindow(owner)
