"""The loading line: what the screen shows while the shell starts, until the first prompt.

Under the early banner (or alone, in a `sub shellcraft` subshell, which has no banner), a line
animates on a background thread while prompt_toolkit and the shell load, and is erased the moment
the shell takes over the terminal. Two styles, `settings loader bar|spinner` (top-level `loader`
in config.json):

- `bar` (the default): a gradient progress bar, the percentage and the step it is on. The
  percentage is measured against how long the same steps took at the last start (timing.txt).
- `spinner`: a braille spinner and "loading ShellCraft…".

Plain ANSI sequences and no Rich or prompt_toolkit, since it runs while they are still loading.
Nothing is drawn for the first DELAY seconds, so a fast start never flashes it.
"""

from __future__ import annotations

import math
import sys
import threading
import time
from typing import Any

from shellcraft import termsize, timing

CONFIG_KEY = "loader"
DEFAULT = "bar"
STYLES = {"bar": "a progress bar with the step it's on (the default)",
          "spinner": "a spinner and \"loading ShellCraft…\""}
DELAY = 0.12  # seconds before it appears
FRAME = 1 / 12  # seconds between frames
BAR_WIDTH = 30
BRAILLE = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
INDENT = "  "
FIRST_STEP = "loading prompt_toolkit"
# timing.mark label -> what happens next (the step the bar names)
NEXT_STEP = {"prompt_toolkit imported": "loading the shell's commands",
             "prompt_toolkit + shell imported": "starting the shell"}
# The span the loader covers, in the timing report: from the first of these rows to the last.
SPAN = (("banner", "config and theme"), "prompt_toolkit + shell imported")
DEFAULT_EXPECTED = 2.0  # seconds, when there is no earlier start to go by

_active: Loader | None = None


def style(config: Any) -> str:
    value = config.get(CONFIG_KEY) if hasattr(config, "get") else None
    return value if value in STYLES else DEFAULT


def start(kind: str, theme: Any) -> None:
    """Start the loading line on stdout (a terminal only); stop() erases it."""
    global _active
    if _active is not None or not sys.stdout.isatty():
        return
    _active = Loader(kind if kind in STYLES else DEFAULT, theme, expected_seconds())
    timing.on_mark(_active.step)
    _active.start()


def stop() -> None:
    """Erase the loading line (if it was drawn) and end its thread. Safe to call any number of times."""
    global _active
    loader, _active = _active, None
    if loader is not None:
        timing.on_mark(None)
        loader.finish()


def expected_seconds(report: str | None = None) -> float:
    """How long the loader's span took at the last start, from the saved timing report."""
    text = timing.last() if report is None else report
    totals: dict[str, float] = {}
    for line in text.splitlines():
        parts = line.rsplit(None, 4)  # label… own "ms" total "ms"
        if len(parts) == 5 and parts[2] == "ms" and parts[4] == "ms":
            try:
                totals[parts[0].strip()] = float(parts[3])
            except ValueError:
                continue
    begin = next((totals[k] for k in SPAN[0] if k in totals), None)
    end = totals.get(SPAN[1])
    if begin is None or end is None or end <= begin:
        return DEFAULT_EXPECTED
    return max(0.3, (end - begin) / 1000)


def _rgb(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    try:
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except (ValueError, IndexError):
        return 200, 200, 200


def _mix(a: str, b: str, t: float) -> tuple[int, int, int]:
    x, y = _rgb(a), _rgb(b)
    return tuple(round(p + (q - p) * t) for p, q in zip(x, y))  # type: ignore[return-value]


def _paint(rgb: tuple[int, int, int], text: str, bold: bool = False) -> str:
    return f"\x1b[{'1;' if bold else ''}38;2;{rgb[0]};{rgb[1]};{rgb[2]}m{text}\x1b[0m"


class Loader(threading.Thread):
    def __init__(self, kind: str, theme: Any, expected: float, out: Any = None):
        super().__init__(name="shellcraft-loader", daemon=True)
        self.kind = kind
        self.theme = theme
        self.expected = expected
        self.out = out or sys.stdout
        self.label = FIRST_STEP
        self.started = time.monotonic()
        self.done = threading.Event()
        self.lock = threading.Lock()
        self.drawn = False
        self.width = max(20, (termsize.columns() or 80) - 1)
        self._restore_vt = termsize.enable_vt()

    def step(self, mark: str) -> None:
        self.label = NEXT_STEP.get(mark, self.label)

    def run(self) -> None:
        if self.done.wait(DELAY):
            return
        frame = 0
        while not self.done.is_set():
            with self.lock:
                if self.done.is_set():
                    break
                self._write(("\x1b[?25l" if not self.drawn else "") + "\r" + self.line(frame) + "\x1b[K")
                self.drawn = True
            frame += 1
            self.done.wait(FRAME)

    def finish(self) -> None:
        self.done.set()
        with self.lock:
            if self.drawn:
                self._write("\r\x1b[2K\x1b[?25h")  # erased; the cursor stays at the start of that line
                self.drawn = False
        self.join(timeout=1)
        self._restore_vt()

    def _write(self, text: str) -> None:
        try:
            self.out.write(text)
            self.out.flush()
        except (OSError, ValueError):
            self.done.set()

    def line(self, frame: int, elapsed: float | None = None) -> str:
        """One frame of the line, already colored (also used by the tests)."""
        t = self.theme
        if elapsed is None:
            elapsed = time.monotonic() - self.started
        if self.kind == "spinner":
            parts = [(BRAILLE[frame % len(BRAILLE)], _rgb(t.accent), True),
                     (" loading ShellCraft…", _rgb(t.muted), False)]
        else:
            ratio = elapsed / self.expected
            done = 0.97 * min(1.0, ratio) + (0.02 * (1 - math.exp(-(ratio - 1))) if ratio > 1 else 0.0)
            filled = int(done * BAR_WIDTH)
            empty = _mix(t.muted, "#000000", 0.55)
            parts = []
            for k in range(BAR_WIDTH):
                color = _mix(t.gradient_from, t.gradient_to, k / (BAR_WIDTH - 1))
                parts.append(("━" if k < filled else "╸" if k == filled else "━", color if k <= filled else empty,
                              False))
            parts += [(f"  {int(done * 100):3d}%", _rgb(t.accent), True), (f"  {self.label}…", _rgb(t.muted), False)]
        out, used = [INDENT], len(INDENT)
        for text, color, bold in parts:  # cut at the terminal width: a wrapped line can't be erased with \r
            text = text[: max(0, self.width - used)]
            if text:
                out.append(_paint(color, text, bold))
                used += len(text)
        return "".join(out)
