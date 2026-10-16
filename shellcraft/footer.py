"""The footer: one line at the bottom of the terminal with a tab per shell, always visible.

    ⤷ 0:⚡shellcraft │ 1:bash ● │ 2:seq 3 ✓ │ 3:shellcraft ⤷1 ●     Ctrl-] N switches

The last terminal row is reserved with a scroll region (DECSTBM, rows 1..N-1), so the prompt,
command output and attached programs scroll above it and never push it away. The footer is drawn
with the cursor saved and restored (DECSC / DECRC), so it can be refreshed at any moment: after
every prompt render, after output from an attached program, after a resize.

Tab 0 is this shell; the others are the subshells of `shellcraft.jobs`. The current tab (the attached
program, or 0 at the prompt) is highlighted, and so is the tab under the mouse. `hit()` maps a
click to a tab, for the prompt's mouse binding and for the attach loop, which both switch on it.
"""

from __future__ import annotations

import os
import re
import shutil
from typing import TYPE_CHECKING, Any, Callable, NamedTuple

from prompt_toolkit.utils import get_cwidth
from rich.text import Text

if TYPE_CHECKING:
    from shellcraft.jobs import JobTable
    from shellcraft.themes import UI

PREFIX = "Ctrl-]"
MIN_STACK = 3  # subshell rows the stack may always take; beyond that, up to half the screen
# A scroll-region command from an attached program (`ESC[r`, `ESC[a;br`): rewritten to stay
# above the footer, else a full-screen reset would let its output scroll over the footer row.
MARGINS_RE = re.compile(rb"\x1b\[(\d*)(?:;(\d*))?r")


class Footer:
    def __init__(self, ui: UI, jobs: JobTable, depth: int = 0, alerts: Any = None):
        self.ui = ui
        self.jobs = jobs
        self.alerts = alerts  # shellcraft.alerts.AlertBus, shown above the stack
        self.dirty = False  # set from other threads (an alert, a job change): the attach loop redraws
        self.depth = depth  # inside `sub shellcraft`: shown on tab 0
        self.current = 0  # 0 = this shell, else the attached job's number
        self.hover: Any = None  # what is under the mouse: a tab (0 or a job number) or an Alert
        self.enabled = False
        self.rows: list[Row] = []  # the footer's rows, after the last draw
        self._pressed = "none"  # the button of the last press: Windows reports every release as NONE
        self.collapsed = False  # the subshell rows folded into `main  N subshells ▸` (Ctrl-] c, a click)
        self.cursor: int | None = None  # the subshell row chosen with ↓ / ↑ at the prompt
        self.at_prompt = False  # drawn at the prompt too (the `footer` setting), not only in subshells
        self.on_change: Callable[[], None] | None = None  # the shell: redraw the prompt after a fold
        self._painted = 0  # rows the last draw painted: a shorter footer clears the ones it gave up
        self.show_hint: Callable[[], bool] = lambda: False  # the panel_hint setting (set by the shell)
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
        clear = "".join(f"\x1b[{rows - i};1H\x1b[2K" for i in range(max(self._painted, self.height())))
        self._painted = 0
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
        # A footer that got shorter (a fold, a subshell gone, an alert expired) clears the rows it
        # no longer paints: they are back in the scroll region, and would keep its colors otherwise.
        freed = "".join(f"\x1b[{top - 1 - i};1H\x1b[2K" for i in range(max(0, self._painted - len(lines))))
        self._painted = len(lines)
        # Region first (it also moves the cursor home, hence the save / restore around it), then
        # the rows: they are outside the region, so absolute moves reach them.
        body = "".join(f"\x1b[{top + i};1H\x1b[2K{line}" for i, line in enumerate(lines))
        return f"\x1b7\x1b[1;{top - 1}r\x1b8\x1b7{freed}{body}\x1b8"

    def render_bytes(self) -> bytes:
        """The draw sequence as bytes, appended to an attached program's output."""
        return self.sequence().encode("utf-8") if self.enabled else b""

    def _write(self, text: str) -> None:
        file = self.ui.console.file
        file = getattr(file, "raw", file)  # while `record` runs: the terminal, not the transcript
        try:
            file.write(text)
            file.flush()
        except (OSError, ValueError):
            pass

    def max_rows(self) -> int:
        """Subshell rows the stack may take: up to half the screen, three at least."""
        return max(MIN_STACK, self.size()[0] // 2)

    def height(self) -> int:
        """Rows the footer takes: the alert rows shown, the header (with panel_hint), `main`, and
        one per subshell (up to max_rows, then one row saying how many more), none when
        collapsed."""
        jobs = list(self.jobs)
        rows = len(alert_rows(self._alerts(), jobs, self._expanded())) + 1
        if jobs and self.show_hint():
            rows += 1
        if jobs and not self.collapsed:
            shown = min(len(jobs), self.max_rows())
            rows += shown + (1 if shown < len(jobs) else 0)
        return rows

    def toggle_collapsed(self) -> bool:
        """Fold the subshell rows into the main row, or unfold them; the new state. The attach
        loop resizes the program to the rows that freed up (dirty), the prompt redraws."""
        self.collapsed = not self.collapsed
        self.dirty = True
        self.draw()
        if self.on_change is not None:
            self.on_change()
        return self.collapsed

    def _alerts(self) -> list:
        return self.alerts.current() if self.alerts is not None else []

    def _expanded(self) -> bool:
        return bool(self.alerts is not None and self.alerts.expanded)

    def render(self, cols: int) -> list[str]:
        """The footer's lines (ANSI, exactly `cols` cells each): the same stack as the panel under
        the prompt. Records the rows, for the mouse."""
        self.rows = panel_lines(list(self.jobs), self.current, self.hover, cols, self.depth,
                                attached=self.current != 0, cursor=self.cursor,
                                alerts=self._alerts(), hint=self.show_hint(), expanded=self._expanded(),
                                copied=self.alerts.copied if self.alerts is not None else None,
                                collapsed=self.collapsed, max_rows=self.max_rows(), stack=True)
        console = self.ui.console
        out = []
        for row in self.rows:
            rich = Text(row.text, style=f"sc.footer.{row.style}", no_wrap=True, overflow="crop")
            if row.hovered and row.tab is not None:
                rich.stylize("sc.footer.hover", 0, MARK)  # the filled circle, in the accent color
            for start, end, style, _role in row.spans:
                if style:
                    rich.stylize(f"sc.footer.{style}", start, end)
            with console.capture() as capture:
                console.print(rich, end="", width=cols, no_wrap=True, overflow="crop", crop=True, highlight=False,
                              markup=False, soft_wrap=True)
            out.append(capture.get())
        return out

    # ── the mouse ────────────────────────────────────────────────────────────

    @property
    def row_tabs(self) -> list[int | None]:
        return [row.tab for row in self.rows]

    def row_at(self, y: int) -> Row | None:
        """The footer row on terminal row y (1-based), or None when y is above the footer."""
        if not self.enabled or not self._rows:
            return None
        index = y - (self._rows - len(self.rows) + 1)
        return self.rows[index] if 0 <= index < len(self.rows) else None

    def hit(self, x: int, y: int) -> int | None:
        """The tab on terminal row y (1-based), or None when the row is not a shell's line."""
        row = self.row_at(y)
        return row.tab if row is not None else None

    def set_hover(self, key: Any) -> bool:
        """Returns whether it changed (then the footer needs a redraw)."""
        if key is self.hover or (key == self.hover and type(key) is type(self.hover)):
            return False
        self.hover = key
        if self.alerts is not None and self.alerts.copied is not key:
            self.alerts.copied = None  # the "copied" hint goes when the mouse moves to another row
        return True

    def mouse(self, kind: str, x: int, y: int, button: str = "left") -> tuple[str, Any] | None:
        """A mouse event (from parse_mouse) over the footer. None when it is not on the footer;
        ("switch", tab) for a click on a shell's line; ("handled", None) for the rest: hover, a
        click on the badge (fold / unfold the alerts), a click on an alert's icon (dismiss it) or a
        right-click on an alert (copy its content into the clip register)."""
        if kind == "down":
            self._pressed = button
        elif kind == "up" and button == "none":
            button = self._pressed  # Windows: RIGHT;MOUSE_DOWN, then NONE;MOUSE_UP
        row = self.row_at(y)
        if row is None:
            return None
        key = row.alert if row.alert is not None else row.tab
        if kind == "move":
            if self.set_hover(key):
                self.draw()
            return "handled", None
        if kind != "up":
            return "handled", None
        col = x - 1
        if row.alert is not None and button == "right":
            if self.alerts is not None:
                self.alerts.copy(row.alert)
                self.draw()
            return "handled", None
        if row.alert is not None and row.close >= 0 and \
                get_cwidth(row.text[: row.close]) <= col < get_cwidth(row.text[: row.close + 1]):
            if self.alerts is not None:
                self.set_hover(None)
                self.alerts.remove(row.alert)
                self.draw()
            return "handled", None
        if button != "left":
            return "handled", None
        for start, end, _style, role in row.spans:
            if role == "fold" and get_cwidth(row.text[:start]) <= col < get_cwidth(row.text[:end]):
                self.toggle_collapsed()
                return "handled", None
        if row.badge >= 0 and col >= get_cwidth(row.text[: row.badge]):
            if self.alerts is not None:
                self.alerts.toggle()
                self.draw()
            return "handled", None
        if row.tab is not None:
            self.set_hover(None)
            return "switch", row.tab  # 0: back to the prompt (the attach loop); nothing at the prompt
        return "handled", None

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
MARK_DOT = "●"  # the alert counts in the badge and an inline alert: a filled bubble in the level's color


class Row(NamedTuple):
    """One line of the stack. `tab` is 0 for this shell, a job number, or None (an alert, the
    header); `alert` the Alert an alert row shows; `spans` (start, end, style, role) ranges
    styled over the row (offsets in characters): the badge's counts, an inline alert, the icon;
    `badge` where the alert badge starts on the main row and `close` where the icon (the close
    button) is on an
    alert row (-1: none). `hovered` says the row is under the mouse or chosen with ↓ / ↑."""

    tab: int | None
    style: str
    text: str
    hovered: bool
    alert: Any = None
    spans: tuple[tuple[int, int, str | None, str | None], ...] = ()
    badge: int = -1
    close: int = -1


def alert_rows(alerts: list, jobs: list, expanded: bool) -> list:
    """The alerts that get a row of their own: all of them when unfolded (or when there is no
    stack to fold them into), else only the critical ones; the rest live in the badge."""
    if expanded or not jobs:
        return list(alerts)
    return [a for a in alerts if a.level == "critical"]


def alert_lines(alerts: list, width: int, hover: Any = None, copied: Any = None) -> list[Row]:
    """One line per alert, most severe first: ` ▲ disk almost full            warning · 42s`.
    The icon at the start is the close button; the row says `copied` after a right-click on it."""
    from shellcraft.alerts import ICONS

    lines: list[Row] = []
    for alert in alerts:
        hovered = hover is alert
        left = f" {ICONS[alert.level]} {alert.message}" + (f" · from {alert.source}" if alert.source else "")
        remaining = alert.remaining
        if hovered and copied is alert:
            right = "copied · Alt-v pastes "
        else:
            right = f"{alert.level}" + (f" · {int(remaining) + 1}s " if remaining is not None else " ")
        room = max(10, width - get_cwidth(right) - 1)
        if get_cwidth(left) > room:
            left = left[: room - 1] + "…"
        pad = max(1, width - get_cwidth(left) - get_cwidth(right))
        text = left + " " * pad + right
        close = 1  # the icon: the close button
        spans = ((close, close + 1, None, "close"),)
        lines.append(Row(None, f"alert.{alert.level}", text, hovered, alert=alert, spans=spans, close=close))
    return lines


def alert_badge(alerts: list, foldable: bool) -> tuple[str, list[tuple[int, int, str]]]:
    """`● 1  ● 2  ● 1 ▸`: how many alerts of each level, in their colors, and ▸ / ▾ when a click
    folds or unfolds rows. Returns the text and (start, end, style) for each count."""
    from shellcraft.alerts import LEVELS

    text, spans = "", []
    for level in reversed(LEVELS):
        count = sum(1 for a in alerts if a.level == level)
        if count:
            if text:
                text += "  "
            spans.append((len(text), len(text) + len(f"{MARK_DOT} {count}"), f"alert.{level}.mark"))
            text += f"{MARK_DOT} {count}"
    return text, spans


def panel_lines(jobs: list, current: int, hover: Any = None, width: int = 80, depth: int = 0,
                attached: bool = False, cursor: int | None = None,
                alerts: list | None = None, hint: bool = False, expanded: bool = False,
                copied: Any = None, collapsed: bool = False, max_rows: int = 8,
                stack: bool = False) -> list[Row]:
    """The rows of the shell stack (see Row): the alert rows (critical ones always; the others
    when `expanded`, or when there is no stack), the header with the keys (only with `hint`,
    the `panel_hint` setting), `main` with the alert badge on its right, and one row per
    subshell with that subshell's own alert inline after its command. `hover` is the tab or the
    Alert under the mouse; `current` the attached job (0 at the prompt). A hovered or chosen
    shell row gets a filled circle (` ● `) without changing its colors. With `stack` the main
    row is always there (the footer); without it, alerts alone show when there is no subshell
    (the panel under the prompt with the footer off). `collapsed` folds the subshell rows into
    the main row (`main  3 subshells ▸`); `max_rows` caps them, with a row saying how many more.

        ✖ Payment API is down · from mcp · claude                              critical
        ● main ▾                                                      ● 1  ● 1  ● 1 ▸
        ○ 1  bash --norc  ▲ Disk /home at 91%
        ○ 2  find / -name "*.log"                                                  done
    """
    width = max(40, width)
    alerts = list(alerts or [])
    lines: list[Row] = alert_lines(alert_rows(alerts, jobs, expanded), width, hover, copied)
    if not jobs and not stack:
        return lines  # alerts alone, when there are no subshells to list
    running = sum(1 for j in jobs if j.running)
    head = f" {len(jobs)} subshell{'s' if len(jobs) != 1 else ''}"
    head += f" ({running} running)" if running != len(jobs) else ""
    head += f" · click or {PREFIX} N switches · " + (f"{PREFIX} alone returns · {PREFIX} x ends it" if attached
                                                       else "↓ to manage · x stops")
    if hint and jobs:
        lines.append(Row(None, "header", head, False))
    main_hover = hover == 0
    main = f" {'●' if current == 0 or main_hover else '○'} main" + (f" [{depth}]" if depth else "")
    badge_at, spans = -1, ()
    if jobs:  # the fold control: a click (or Ctrl-] c) hides the subshell rows, or brings them back
        fold = f"  {len(jobs)} subshell{'s' if len(jobs) != 1 else ''} ▸" if collapsed else " ▾"
        spans = ((len(main), len(main) + len(fold), None, "fold"),)
        main += fold
    if alerts:
        foldable = any(a.level != "critical" for a in alerts)
        badge, marks = alert_badge(alerts, foldable)
        badge += (" ▾" if expanded else " ▸") if foldable else ""
        pad = max(2, width - get_cwidth(main) - get_cwidth(badge) - 1)
        badge_at = len(main) + pad
        spans += tuple((badge_at + s, badge_at + e, style, "badge") for s, e, style in marks)
        spans += ((badge_at + len(badge) - 2, badge_at + len(badge) + 1, None, "badge"),) if foldable else ()
        main += " " * pad + badge + " "
    lines.append(Row(0, "current" if current == 0 else "main", main, main_hover, spans=spans, badge=badge_at))
    if collapsed:
        return lines
    shown = jobs[:max_rows]
    numbers = {job.number for job in jobs}
    for index, job in enumerate(shown):
        chosen = hover == job.number or cursor == index
        mark = "●" if current == job.number or chosen else "○"
        left = f" {mark} {job.number}  {job.short(width // 3)}"
        state = "running" if job.running else "done" if job.status == 0 else "failed"
        right = "" if job.running else f"{job.label} "
        spans = ()
        own = [a for a in alerts if a.job == job.number and a.job in numbers]
        if own:
            first = own[0]  # the most severe (alerts are sorted)
            inline = f"  {MARK_DOT} {first.message}" + (f" +{len(own) - 1}" if len(own) > 1 else "")
            room = width - get_cwidth(left) - get_cwidth(right) - 1
            if get_cwidth(inline) > room:
                inline = inline[: max(4, room - 1)] + "…"
            spans = ((len(left), len(left) + len(inline), f"alert.{first.level}.mark", None),)
            left += inline
        pad = max(1, width - get_cwidth(left) - get_cwidth(right))
        style = "current" if current == job.number else state
        lines.append(Row(job.number, style, left + " " * pad + right, chosen, spans=spans))
    if len(jobs) > len(shown):
        lines.append(Row(None, "header", f"   … and {len(jobs) - len(shown)} more (jobs)", False))
    return lines


# ── SGR / Windows mouse data, as prompt_toolkit's key press carries it ────────

SGR_RE = re.compile(r"\x1b\[<(\d+);(\d+);(\d+)([Mm])")


BUTTONS = {0: "left", 1: "middle", 2: "right", 3: "none"}


def parse_mouse(data: str) -> tuple[str, int, int, str] | None:
    """(kind, x, y, button) with 1-based terminal coordinates from a vt100 SGR mouse sequence or
    prompt_toolkit's Windows mouse data (`button;type;x;y`, 0-based). kind is "up", "down",
    "move", "scroll" or "other"; button "left", "middle", "right" or "none". None when it is not
    a mouse sequence this understands."""
    m = SGR_RE.match(data)
    if m:
        code, x, y, final = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)
        button = BUTTONS[code & 3]
        if code & 64:
            return "scroll", x, y, button
        if code & 32:
            return "move", x, y, button
        return ("up" if final == "m" else "down"), x, y, button
    parts = data.split(";")
    if len(parts) == 4 and parts[2].isdigit() and parts[3].isdigit():
        kinds = {"MOUSE_UP": "up", "MOUSE_DOWN": "down", "MOUSE_MOVE": "move",
                 "SCROLL_UP": "scroll", "SCROLL_DOWN": "scroll"}
        return kinds.get(parts[1], "other"), int(parts[2]) + 1, int(parts[3]) + 1, parts[0].lower()
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

