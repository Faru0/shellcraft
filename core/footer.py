"""The footer: one line at the bottom of the terminal with a tab per shell, always visible.

    ⤷ 0:⚡shellcraft │ 1:bash ● │ 2:seq 3 ✓ │ 3:shellcraft ⤷1 ●     Ctrl-] N switches

The last terminal row is reserved with a scroll region (DECSTBM, rows 1..N-1), so the prompt,
command output and attached programs scroll above it and never push it away. The footer is drawn
with the cursor saved and restored (DECSC / DECRC), so it can be refreshed at any moment: after
every prompt render, after output from an attached program, after a resize.

Tab 0 is this shell; the others are the subshells of `core.jobs`. The current tab (the attached
program, or 0 at the prompt) is highlighted, and so is the tab under the mouse. `hit()` maps a
click to a tab, for the prompt's mouse binding and for the attach loop, which both switch on it.
"""

from __future__ import annotations

import os
import re
import shutil
from typing import TYPE_CHECKING

from prompt_toolkit.utils import get_cwidth
from rich.text import Text

if TYPE_CHECKING:
    from core.jobs import JobTable
    from core.themes import UI

PREFIX = "Ctrl-]"
# A scroll-region command from an attached program (`ESC[r`, `ESC[a;br`): rewritten to stay
# above the footer, else a full-screen reset would let its output scroll over the footer row.
MARGINS_RE = re.compile(rb"\x1b\[(\d*)(?:;(\d*))?r")


class Footer:
    def __init__(self, ui: UI, jobs: JobTable, depth: int = 0):
        self.ui = ui
        self.jobs = jobs
        self.depth = depth  # inside `sub shellcraft`: shown on tab 0
        self.current = 0  # 0 = this shell, else the attached job's number
        self.hover: int | None = None  # the tab under the mouse (0 or a job number)
        self.enabled = False
        self.spans: list[tuple[int, int, int]] = []  # (x_start, x_end, tab) of the last draw, 0-based columns
        self._rows = 0

    # ── geometry ─────────────────────────────────────────────────────────────

    def size(self) -> tuple[int, int]:
        try:
            s = os.get_terminal_size(self.ui.console.file.fileno())
            rows, cols = s.lines, s.columns
        except (OSError, ValueError, AttributeError):
            s = shutil.get_terminal_size()
            rows, cols = s.lines, s.columns
        return max(2, rows), max(10, cols)

    @property
    def region_rows(self) -> int:
        """Rows available above the footer: what attached programs are told the terminal has."""
        return self.size()[0] - 1

    def footer_row(self) -> int:
        """The footer's row, 1-based (the terminal's last)."""
        return self.size()[0]

    # ── drawing ──────────────────────────────────────────────────────────────

    def enable(self) -> None:
        self.enabled = True
        self.draw()

    def disable(self) -> None:
        """Back to a plain terminal: no scroll region, the footer row cleared."""
        if not self.enabled:
            return
        self.enabled = False
        rows = self.size()[0]
        self._write(f"\x1b7\x1b[r\x1b[{rows};1H\x1b[2K\x1b8")

    def draw(self) -> None:
        """Set the scroll region for the current size and paint the footer on the last row."""
        if self.enabled:
            self._write(self.sequence())

    def sequence(self) -> str:
        """What draw() writes: the scroll region for the current size, then the footer line."""
        rows, cols = self.size()
        line = self.render(cols)
        self._rows = rows
        # Region first (it also moves the cursor home, hence the save / restore around it), then
        # the footer: the row is outside the region, so an absolute move reaches it.
        return f"\x1b7\x1b[1;{rows - 1}r\x1b8\x1b7\x1b[{rows};1H\x1b[2K{line}\x1b8"

    def render_bytes(self) -> bytes:
        """The draw sequence as bytes, appended to an attached program's output."""
        return self.sequence().encode("utf-8") if self.enabled else b""

    def _write(self, text: str) -> None:
        try:
            self.ui.console.file.write(text)
            self.ui.console.file.flush()
        except (OSError, ValueError):
            pass

    def render(self, cols: int) -> str:
        """The footer as one ANSI line of exactly `cols` cells; records the tab spans."""
        text = Text(no_wrap=True, overflow="crop")
        self.spans = []
        x = 0

        def add(part: str, style: str, tab: int | None = None) -> None:
            nonlocal x
            text.append(part, style=style)
            width = get_cwidth(part)
            if tab is not None:
                self.spans.append((x, x + width, tab))
            x += width

        add(" ⤷ ", "sc.footer.key")
        main = f" 0:⚡shellcraft{' ⤷' + str(self.depth) if self.depth else ''} "
        add(main, self._tab_style(0, running=True), tab=0)
        for job in self.jobs:
            icon = "●" if job.running else "✓" if job.status == 0 else "✗"
            add("│", "sc.footer")
            failed = not job.running and job.status != 0
            style = self._tab_style(job.number, job.running, failed)
            add(f" {job.number}:{job.short()} {icon} ", style, tab=job.number)
        hint = f" {PREFIX} N switches "
        if x + get_cwidth(hint) + 1 <= cols:
            add(" " * (cols - x - get_cwidth(hint)), "sc.footer")
            add(hint, "sc.footer.hint")
        elif x < cols:
            add(" " * (cols - x), "sc.footer")
        console = self.ui.console
        with console.capture() as capture:
            console.print(text, end="", width=cols, no_wrap=True, overflow="crop", crop=True, highlight=False,
                          markup=False, soft_wrap=True)
        return capture.get()

    def _tab_style(self, tab: int, running: bool, failed: bool = False) -> str:
        if tab == self.hover:
            return "sc.footer.hover"
        if tab == self.current:
            return "sc.footer.current"
        if failed:
            return "sc.footer.failed"
        return "sc.footer.tab" if running else "sc.footer.done"

    # ── the mouse ────────────────────────────────────────────────────────────

    def hit(self, x: int, y: int) -> int | None:
        """The tab at terminal column x, row y (both 1-based), or None when not on a tab."""
        if y != self._rows or not self.enabled:
            return None
        for x0, x1, tab in self.spans:
            if x0 <= x - 1 < x1:
                return tab
        return None

    def set_hover(self, tab: int | None) -> bool:
        """Returns whether it changed (then the footer needs a redraw)."""
        if tab == self.hover:
            return False
        self.hover = tab
        return True

    @staticmethod
    def clamp_margins(data: bytes, rows: int) -> bytes:
        """Rewrite DECSTBM in a program's output so it never includes the footer row."""
        def fix(m: re.Match[bytes]) -> bytes:
            top = int(m.group(1) or 1)
            bottom = int(m.group(2) or rows) if m.group(2) is not None else rows
            if not m.group(1) and m.group(2) is None:
                top, bottom = 1, rows
            return f"\x1b[{max(1, top)};{min(bottom, rows)}r".encode()
        return MARGINS_RE.sub(fix, data)


# ── SGR / Windows mouse data, as prompt_toolkit's key press carries it ────────

SGR_RE = re.compile(r"\x1b\[<(\d+);(\d+);(\d+)([Mm])")


def parse_mouse(data: str) -> tuple[str, int, int] | None:
    """(kind, x, y) with 1-based terminal coordinates from a vt100 SGR mouse sequence or
    prompt_toolkit's Windows mouse data (`button;type;x;y`, 0-based). kind is "up", "down",
    "move", "scroll" or "other". None when it is not a mouse sequence this understands."""
    m = SGR_RE.match(data)
    if m:
        code, x, y, final = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)
        if code & 64:
            return "scroll", x, y
        if code & 32:
            return "move", x, y
        return ("up" if final == "m" else "down"), x, y
    parts = data.split(";")
    if len(parts) == 4 and parts[2].isdigit() and parts[3].isdigit():
        kinds = {"MOUSE_UP": "up", "MOUSE_DOWN": "down", "MOUSE_MOVE": "move",
                 "SCROLL_UP": "scroll", "SCROLL_DOWN": "scroll"}
        return kinds.get(parts[1], "other"), int(parts[2]) + 1, int(parts[3]) + 1
    return None


def incomplete_escape(data: bytes) -> bool:
    """Whether `data` ends inside an escape sequence (then a footer redraw must wait)."""
    i = data.rfind(b"\x1b", max(0, len(data) - 64))
    if i < 0:
        return False
    tail = data[i:]
    if len(tail) == 1:
        return True
    if tail[1:2] == b"[":  # CSI: ends at a byte 0x40–0x7e
        return not any(0x40 <= b <= 0x7E for b in tail[2:])
    if tail[1:2] == b"]":  # OSC: ends with BEL or ST
        return not (b"\x07" in tail or b"\x1b\\" in tail)
    return False

