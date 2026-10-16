"""The scroll view: the session's recent output, kept so the wheel can scroll back through it.

While the shell has the mouse (the footer lists a subshell or an alert, so its lines and marks
can be clicked), the terminal cannot scroll its own history with the wheel. The wheel then opens
this view instead: the last lines the shell printed, with the commands that produced them, in
the scrollable viewer, starting at the bottom. Wheel down past the end, or q, closes it.

Everything the console prints passes through `CaptureFile`, which writes it to the terminal as
before and keeps a copy in memory (colors kept, cursor movements dropped). Nothing is written to
disk, and `clear` empties it.
"""

from __future__ import annotations

import re
import threading
from collections import deque
from typing import Any

MAX_LINES = 5000
# CSI sequences that are not colors (cursor moves, erases, modes), OSC, and other escapes.
_NOT_SGR = re.compile(r"\x1b\[[0-?]*[ -/]*[@-ln-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")


class Scrollback:
    """The last MAX_LINES lines of output, plus the line being written."""

    def __init__(self, max_lines: int = MAX_LINES):
        self.lines: deque[str] = deque(maxlen=max_lines)
        self.partial = ""
        self._lock = threading.Lock()

    def feed(self, text: str) -> None:
        text = _NOT_SGR.sub("", text)
        if not text:
            return
        with self._lock:
            pieces = (self.partial + text).split("\n")
            self.partial = pieces.pop()
            for line in pieces:
                self.lines.append(_after_returns(line))
            self.partial = _after_returns(self.partial, keep_last=True)

    def command(self, line: str) -> None:
        """A command typed at the prompt: the prompt itself is not printed through the console."""
        with self._lock:
            if self.partial:
                self.lines.append(self.partial)
                self.partial = ""
            self.lines.append(f"\x1b[1m❯\x1b[0m {line}")

    def clear(self) -> None:
        with self._lock:
            self.lines.clear()
            self.partial = ""

    def text(self) -> str:
        with self._lock:
            lines = list(self.lines) + ([self.partial] if self.partial else [])
        return "\n".join(lines)


def _after_returns(line: str, keep_last: bool = False) -> str:
    """A spinner or a progress line rewrites itself with \\r: only what was written last counts."""
    if "\r" not in line:
        return line
    parts = line.split("\r")
    if keep_last:
        return parts[-1]
    return next((p for p in reversed(parts) if p), "")


class CaptureFile:
    """The console's file: everything goes to the terminal (`raw`) and into the scrollback."""

    def __init__(self, raw: Any, scrollback: Scrollback):
        self.raw = raw
        self.scrollback = scrollback

    def write(self, text: str) -> int:
        n = self.raw.write(text)
        self.scrollback.feed(text)
        return n if isinstance(n, int) else len(text)

    def flush(self) -> None:
        self.raw.flush()

    def __getattr__(self, name: str) -> Any:  # fileno, isatty, encoding… from the terminal
        return getattr(self.raw, name)


def terminal_file(file: Any) -> Any:
    """The terminal under any CaptureFile / record Tee wrapping: for writes that are drawing,
    not output (the footer), which must not land in the scrollback or a transcript."""
    while hasattr(file, "raw") and file.raw is not file:
        file = file.raw
    return file


def scrollback_of(file: Any) -> Scrollback | None:
    """The Scrollback behind a console file, through a record Tee if there is one."""
    while file is not None:
        if isinstance(file, CaptureFile):
            return file.scrollback
        file = getattr(file, "raw", None)
    return None
