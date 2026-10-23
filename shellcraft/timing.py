"""Startup timing: where the time goes between starting the process and the first prompt.

`python main.py --timing` (or SHELLCRAFT_TIMING=1, which subshells inherit) prints the table just
before the first prompt; `diag` shows the last one (kept in ~/.shellcraft/timing.txt). The first
row is the time the process existed before ShellCraft's own code ran (the Python interpreter, its
site packages, and on Windows anything that scans the files first), read from the OS.

Marks are cheap (a perf_counter call), so they are always taken.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

_T0 = time.perf_counter()
_marks: list[tuple[str, float]] = []
ENV = "SHELLCRAFT_TIMING"
FILE = "timing.txt"


def process_age() -> float | None:
    """Seconds since this process was created, from the OS; None when it can't be read."""
    try:
        if sys.platform.startswith("linux"):
            with open("/proc/self/stat", encoding="ascii") as fh:
                fields = fh.read().rsplit(")", 1)[1].split()
            start_ticks = int(fields[19])  # field 22 of the line (starttime), after pid and (comm)
            with open("/proc/uptime", encoding="ascii") as fh:
                uptime = float(fh.read().split()[0])
            return max(0.0, uptime - start_ticks / os.sysconf("SC_CLK_TCK"))
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
            k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
            if not k32.GetProcessTimes(k32.GetCurrentProcess(), ctypes.byref(created), ctypes.byref(exited),
                                       ctypes.byref(kernel), ctypes.byref(user)):
                return None
            now = wintypes.FILETIME()
            k32.GetSystemTimePreciseAsFileTime(ctypes.byref(now))

            def ticks(ft: object) -> int:
                return (ft.dwHighDateTime << 32) | ft.dwLowDateTime  # type: ignore[attr-defined]

            return max(0.0, (ticks(now) - ticks(created)) / 1e7)
    except (OSError, ValueError, IndexError, AttributeError):
        return None
    return None


_BEFORE = process_age()  # how old the process was when ShellCraft's code started


_listener: Any = None  # shellcraft.loading's step(label), while the loading line runs


def on_mark(listener: Any) -> None:
    """Call `listener(label)` at each mark (None: stop)."""
    global _listener
    _listener = listener


def mark(label: str) -> None:
    _marks.append((label, time.perf_counter() - _T0))
    if _listener is not None:
        _listener(label)


def enabled() -> bool:
    return bool(os.environ.get(ENV))


def report() -> str:
    """The table: each phase's own time and the running total since the process was created."""
    before = _BEFORE or 0.0
    rows = [("Python start (before ShellCraft)", before, before)]
    last = 0.0
    for label, at in _marks:
        rows.append((label, at - last, before + at))
        last = at
    width = max(len(r[0]) for r in rows)
    lines = [f"{'startup phase':<{width}}  {'own':>7}  {'total':>7}"]
    lines += [f"{label:<{width}}  {own * 1000:5.0f} ms  {total * 1000:5.0f} ms" for label, own, total in rows]
    if _BEFORE is None:
        lines.append("(the time before ShellCraft's code could not be read on this system)")
    try:
        from shellcraft.diagnostics import host_summary

        lines.append(f"on: {host_summary()}")
    except Exception:  # noqa: BLE001 — the table is the point; the host line is a bonus
        pass
    return "\n".join(lines)


def save() -> None:
    """Keep the report for `diag`."""
    from shellcraft.config import config_dir

    try:
        path = config_dir() / FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report() + "\n", encoding="utf-8")
    except OSError:
        pass


def last() -> str:
    from shellcraft.config import config_dir

    try:
        return (config_dir() / FILE).read_text(encoding="utf-8").rstrip()
    except OSError:
        return ""
