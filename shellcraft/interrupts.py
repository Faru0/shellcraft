"""A log of the last Ctrl-Cs, for `diag`: where each one landed (a running command, its output
being printed, the prompt, the gap between commands), what was running and for how long, the
console modes at that moment on Windows, and what the terminal had delivered by the time the
next prompt started (typed text kept, reports dropped). The data to send with a bug report
about a Ctrl-C that left junk on the screen."""

from __future__ import annotations

import sys
import time
from collections import deque
from typing import Any

LIMIT = 20
ATTACH_WINDOW = 10.0  # seconds: typeahead found at a prompt belongs to an interrupt this recent

_events: deque[dict[str, Any]] = deque(maxlen=LIMIT)


def record(where: str, command: str = "", elapsed: float | None = None) -> dict[str, Any]:
    event: dict[str, Any] = {"at": time.time(), "mono": time.monotonic(), "where": where,
                             "command": command, "elapsed": elapsed, "modes": console_modes()}
    _events.append(event)
    return event


def note_typeahead(kept: int, dropped: int, sample: str) -> None:
    """What the next prompt found waiting: attached to a recent interrupt, or logged on its own
    when something was dropped (a report that arrived with no interrupt at all)."""
    last = _events[-1] if _events else None
    if last is not None and "typeahead" not in last and time.monotonic() - last["mono"] < ATTACH_WINDOW:
        last["typeahead"] = (kept, dropped, sample)
    elif dropped:
        record("a report at the prompt, no interrupt")["typeahead"] = (kept, dropped, sample)


def events() -> list[dict[str, Any]]:
    return list(_events)


def clear() -> None:
    _events.clear()


def console_modes() -> str | None:
    """Windows: the console's input and output modes right now, by flag name; None elsewhere."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        from shellcraft.diagnostics import _INPUT_FLAGS, _OUTPUT_FLAGS, _flags

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        k32.GetStdHandle.restype = wintypes.HANDLE

        def mode(which: int) -> int | None:
            value = wintypes.DWORD()
            return value.value if k32.GetConsoleMode(k32.GetStdHandle(which), ctypes.byref(value)) else None

        return f"in {_flags(mode(-10), _INPUT_FLAGS)} | out {_flags(mode(-11), _OUTPUT_FLAGS)}"
    except Exception:  # noqa: BLE001
        return None


def report_lines() -> list[str]:
    if not _events:
        return ["none this session (press Ctrl-C while something runs, then run diag again)"]
    lines = []
    for event in _events:
        when = time.strftime("%H:%M:%S", time.localtime(event["at"]))
        ran = f" after {event['elapsed']:.2f} s" if event.get("elapsed") is not None else ""
        what = f": {event['command']}" if event.get("command") else ""
        lines.append(f"{when}  {event['where']}{ran}{what}")
        if event.get("modes"):
            lines.append(f"          console modes then: {event['modes']}")
        if "typeahead" in event:
            kept, dropped, sample = event["typeahead"]
            found = f"{kept} key(s) kept, {dropped} report(s) dropped"
            lines.append(f"          pending at the next prompt: {found}" + (f": {sample}" if sample else ""))
    return lines
