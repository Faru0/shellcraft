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
from typing import TYPE_CHECKING, Any

from prompt_toolkit.utils import get_cwidth
from rich.text import Text

if TYPE_CHECKING:
    from core.jobs import JobTable
    from core.themes import UI

PREFIX = "Ctrl-]"
MAX_ROWS = 10  # the footer never takes more rows than this (the stack is cut with "… and N more")
# A scroll-region command from an attached program (`ESC[r`, `ESC[a;br`): rewritten to stay
# above the footer, else a full-screen reset would let its output scroll over the footer row.
MARGINS_RE = re.compile(rb"\x1b\[(\d*)(?:;(\d*))?r")


class Footer:
    def __init__(self, ui: UI, jobs: JobTable, depth: int = 0, alerts: Any = None):
        self.ui = ui
        self.jobs = jobs
        self.alerts = alerts  # core.alerts.AlertBus, shown above the stack
        self.dirty = False  # set from other threads (an alert, a job change): the attach loop redraws
        self.depth = depth  # inside `sub shellcraft`: shown on tab 0
        self.current = 0  # 0 = this shell, else the attached job's number
        self.hover: int | None = None  # the tab under the mouse (0 or a job number)
        self.enabled = False
        self.row_tabs: list[int | None] = []  # the tab of each footer row, after the last draw
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
        return max(1, self.size()[0] - self.height())

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
        clear = "".join(f"\x1b[{rows - i};1H\x1b[2K" for i in range(self.height()))
        self._write(f"\x1b7\x1b[r{clear}\x1b8")

    def draw(self) -> None:
        """Set the scroll region for the current size and paint the footer on the last row."""
        if self.enabled:
            self._write(self.sequence())

    def sequence(self) -> str:
        """What draw() writes: the scroll region above the footer, then the footer's rows."""
        rows, cols = self.size()
        lines = self.render(cols)
        self._rows = rows
        top = rows - len(lines) + 1
        # Region first (it also moves the cursor home, hence the save / restore around it), then
        # the rows: they are outside the region, so absolute moves reach them.
        body = "".join(f"\x1b[{top + i};1H\x1b[2K{line}" for i, line in enumerate(lines))
        return f"\x1b7\x1b[1;{top - 1}r\x1b8\x1b7{body}\x1b8"

    def render_bytes(self) -> bytes:
        """The draw sequence as bytes, appended to an attached program's output."""
        return self.sequence().encode("utf-8") if self.enabled else b""

    def _write(self, text: str) -> None:
        try:
            self.ui.console.file.write(text)
            self.ui.console.file.flush()
        except (OSError, ValueError):
            pass

    def height(self) -> int:
        """Rows the footer takes: the alerts, the header, `main`, and one per subshell (capped)."""
        alerts = len(self.alerts.current()) if self.alerts is not None else 0
        return alerts + 2 + min(len(self.jobs), MAX_ROWS - 2)

    def render(self, cols: int) -> list[str]:
        """The footer's lines (ANSI, exactly `cols` cells each): the same stack as the panel under
        the prompt. Records which tab each row is."""
        lines = panel_lines(list(self.jobs), self.current, self.hover, cols, self.depth, attached=True,
                            alerts=self.alerts.current() if self.alerts is not None else [])
        self.row_tabs = [tab for tab, _style, _text, _hovered in lines]
        console = self.ui.console
        out = []
        for _tab, style, text, hovered in lines:
            rich = Text(text, style=f"sc.footer.{style}", no_wrap=True, overflow="crop")
            if hovered:
                rich.stylize("sc.footer.hover", 0, MARK)  # the filled circle, in the accent color
            with console.capture() as capture:
                console.print(rich, end="", width=cols, no_wrap=True, overflow="crop", crop=True, highlight=False,
                              markup=False, soft_wrap=True)
            out.append(capture.get())
        return out

    # ── the mouse ────────────────────────────────────────────────────────────

    def hit(self, x: int, y: int) -> int | None:
        """The tab on terminal row y (1-based), or None when the row is not a shell's line."""
        if not self.enabled or not self._rows:
            return None
        first = self._rows - len(self.row_tabs) + 1
        index = y - first
        if 0 <= index < len(self.row_tabs):
            return self.row_tabs[index]
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


# ── the stack, shared by the panel under the prompt and the footer in a subshell ─

MARK = 3  # the width of the " ○ " / " ● " marker that starts a shell's line


def alert_lines(alerts: list, width: int) -> list[tuple[int | None, str, str, bool]]:
    """One line per alert, most severe first: ` ▲ disk almost full            warning · 42s`."""
    from core.alerts import ICONS

    lines: list[tuple[int | None, str, str, bool]] = []
    for alert in alerts:
        left = f" {ICONS[alert.level]} {alert.message}"
        remaining = alert.remaining
        right = f"{alert.level}" + (f" · {int(remaining) + 1}s " if remaining is not None else " · sticky ")
        room = max(10, width - get_cwidth(right) - 1)
        if get_cwidth(left) > room:
            left = left[: room - 1] + "…"
        pad = max(1, width - get_cwidth(left) - get_cwidth(right))
        lines.append((None, f"alert.{alert.level}", left + " " * pad + right, False))
    return lines


def panel_lines(jobs: list, current: int, hover: int | None, width: int, depth: int = 0,
                attached: bool = False, cursor: int | None = None,
                alerts: list | None = None) -> list[tuple[int | None, str, str, bool]]:
    """The lines of the shell stack: (tab, style, text, hovered) each, where tab is 0 for this
    shell, a job number, or None for the header; style is one of header, current, main, running,
    done, failed; and hovered says the line is under the mouse or chosen with ↓ / ↑, which fills
    its circle (` ● `) without changing the line's colors. `current` is the attached job (0 at
    the prompt): its circle is filled too.

        ⤷ 2 subshells (1 running) · click or Ctrl-] N switches · ↓ to manage
        ● main
        ○ 1  bash --norc                                          2m 18s · running
    """
    from core.shell import format_duration

    width = max(40, width)
    lines: list[tuple[int | None, str, str, bool]] = alert_lines(alerts or [], width)
    if not jobs:
        return lines  # alerts alone, when there are no subshells to list
    running = sum(1 for j in jobs if j.running)
    head = f" ⤷ {len(jobs)} subshell{'s' if len(jobs) != 1 else ''}"
    head += f" ({running} running)" if running != len(jobs) else ""
    head += f" · click or {PREFIX} N switches · " + (f"{PREFIX} alone returns" if attached else "↓ to manage")
    lines.append((None, "header", head, False))
    main_hover = hover == 0
    main = f" {'●' if current == 0 or main_hover else '○'} main" + (f" ⤷{depth}" if depth else "")
    lines.append((0, "current" if current == 0 else "main", main, main_hover))
    shown = jobs[: MAX_ROWS - 2]
    for index, job in enumerate(shown):
        chosen = hover == job.number or cursor == index
        mark = "●" if current == job.number or chosen else "○"
        left = f" {mark} {job.number}  {job.short(width // 2)}"
        state = "running" if job.running else "done" if job.status == 0 else "failed"
        right = f"{format_duration(job.elapsed)} · {job.label} "
        pad = max(1, width - get_cwidth(left) - get_cwidth(right))
        style = "current" if current == job.number else state
        lines.append((job.number, style, left + " " * pad + right, chosen))
    if len(jobs) > len(shown):
        lines.append((None, "header", f"   … and {len(jobs) - len(shown)} more (jobs)", False))
    return lines


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

