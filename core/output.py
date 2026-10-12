"""Live feedback: delayed spinners, styled errors, and an auto-pager viewport."""

from __future__ import annotations

import re
import threading
import time
from typing import Any, Callable

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import ANSI, StyleAndTextTuples, to_formatted_text
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.mouse_events import MouseEvent, MouseEventType
from prompt_toolkit.utils import get_cwidth
from rich.color import Color
from rich.panel import Panel
from rich.text import Text

from core import settings
from core.context import Paged, ShellContext, Styled
from core.parser import ParseError
from core.pipeline import PipelineError
from core.themes import UI

SPINNER_DELAY = 0.15  # seconds before a spinner appears; fast commands never flash one
SPINNER_ELAPSED_AFTER = 3.0  # seconds after which the spinner shows how long it has been
SPINNER_HINT_AFTER = 10.0  # ... and that Ctrl-C cancels
PAGER_DIRECT_LINES = 5_000  # plain text this tall goes straight to the pager, skipping Rich
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def make_spinner_runner(ctx: ShellContext) -> Callable[[str, Callable[[], Any]], Any]:
    """Run each segment in a worker thread and show a Rich spinner if it takes a while."""
    ui = ctx.ui

    def runner(label: str, fn: Callable[[], Any]) -> Any:
        done = threading.Event()
        box: dict[str, Any] = {}

        def target() -> None:
            try:
                box["value"] = fn()
            except BaseException as exc:  # noqa: BLE001 — re-raised on the main thread
                box["error"] = exc
            finally:
                done.set()

        started = time.monotonic()
        threading.Thread(target=target, daemon=True, name=f"shellcraft:{label}").start()
        if not done.wait(SPINNER_DELAY) and settings.get(ctx.config, "spinner"):
            with ui.console.status(Text(label, style="sc.accent"), spinner="dots12",
                                   spinner_style="sc.accent") as status:
                shown = label
                while not done.wait(0.05):
                    text = spinner_label(label, time.monotonic() - started)
                    if text != shown:
                        shown = text
                        status.update(Text(text, style="sc.accent"))
        done.wait()
        if "error" in box:
            raise box["error"]
        return box.get("value")

    return runner


def spinner_label(label: str, elapsed: float) -> str:
    """The spinner's text: the label, then `(12 s)` after a few seconds, then how to cancel."""
    if elapsed < SPINNER_ELAPSED_AFTER:
        return label
    text = f"{label} ({int(elapsed)} s)"
    if elapsed >= SPINNER_HINT_AFTER:
        text += "  · Ctrl-C cancels"
    return text


def show(ui: UI, value: Any, pager: bool = True, title: str | None = None) -> None:
    """Print a command's final output, paging it if it is taller than the terminal, or as a
    Paged value asks (the `pager` setting off still means never). `title` (the command line)
    is shown in the pager's status bar."""
    forced: bool | None = None
    while isinstance(value, (Paged, Styled)):  # either may wrap the other (help: Styled(Paged))
        if isinstance(value, Paged):
            value, forced = value.value, value.page
        else:
            if not value.text:
                return
            value = value.renderable
    if value is None or value == "":
        return
    console = ui.console
    if isinstance(value, str) and value.count("\n") > PAGER_DIRECT_LINES and console.is_terminal \
            and pager and forced is not False:
        page(ui, value.rstrip("\n"), title)  # too big to run through Rich first
        return
    renderable = Text.from_ansi(value.rstrip("\n")) if isinstance(value, str) else value
    if not console.is_terminal or not pager or forced is False:
        console.print(renderable)
        return
    with console.capture() as capture:
        console.print(renderable)
    rendered = capture.get()
    if forced or rendered.count("\n") > console.height - 2:
        page(ui, rendered, title)
    else:
        console.file.write(rendered)  # already rendered once: no second Rich pass
        console.file.flush()


def show_error(ui: UI, exc: Exception, line: str) -> None:
    console = ui.console
    if isinstance(exc, ParseError):
        body = Text(exc.message, style="sc.error")
        if exc.pos is not None:
            body.append("\n\n  " + line + "\n  " + " " * exc.pos + "^", style="sc.muted")
        title = "✗ parse error"
    elif isinstance(exc, PipelineError):
        where = f"segment {exc.index}/{exc.total}" if exc.total > 1 else "command"
        body = Text.assemble((exc.name, "sc.accent"), (f"  ({where})\n", "sc.muted"), (exc.message, ""))
        title = "✗ pipeline failed" if exc.total > 1 else "✗ command failed"
    else:
        body = Text(f"{type(exc).__name__}: {exc}")
        title = "✗ error"
    console.print(Panel(body, title=Text(title, style="sc.error"), title_align="left",
                        border_style="sc.error", expand=False, padding=(0, 1)))


PAGER_KEYS = [
    ("↑ ↓  j k", "scroll one line"), ("PgUp PgDn  b f  space", "scroll one screen"),
    ("d u", "scroll half a screen"), ("g G  Home End", "top / end"),
    ("← →", "scroll sideways (when lines are not wrapped)"), ("w", "wrap long lines on / off"),
    ("/", "search (case-insensitive)"), ("n N", "next / previous match"),
    ("mouse wheel", "scroll"), ("?", "this help"), ("q  Esc", "close"),
]


def page(ui: UI, rendered: str, title: str | None = None) -> dict[str, Any]:
    """Interactive scrollable viewport for ANSI text (cross-platform, no external pager)."""
    lines = rendered.rstrip("\n").split("\n")
    plain = [_ANSI_RE.sub("", ln) for ln in lines]
    widths = [get_cwidth(ln) for ln in plain]
    # `match` is the line of the last search hit, kept apart from `top` (which is clamped to the
    # last screen) so that `n` keeps advancing through matches on the final page.
    state = {"top": 0, "left": 0, "match": -1, "searching": False, "pattern": "", "message": "",
             "wrap": False, "help": False}
    search = Buffer(multiline=False)
    hl_on, hl_off = _theme_highlight(ui)

    def size() -> tuple[int, int]:
        s = app.output.get_size()
        return max(1, s.rows - 1), max(1, s.columns)

    def body_height() -> int:
        return size()[0]

    def rows_of(i: int) -> int:
        """Screen rows line i takes: 1, or more when wrapping."""
        if not state["wrap"]:
            return 1
        columns = size()[1]
        return max(1, -(-widths[i] // columns))

    def max_top() -> int:
        height = body_height()
        if not state["wrap"]:
            return max(0, len(lines) - height)
        used, i = 0, len(lines)
        while i > 0 and used + rows_of(i - 1) <= height:
            i -= 1
            used += rows_of(i)
        return i

    def max_left() -> int:
        return max(0, max(widths, default=0) - size()[1])

    def scroll(delta: int) -> None:
        state["top"] = min(max_top(), max(0, state["top"] + delta))

    def find(forward: bool = True) -> None:
        if not state["pattern"]:
            return
        hit = _next_match(plain, state["pattern"], state["top"], state["match"], body_height(), forward)
        if hit is None:
            state["message"] = f"pattern not found: {state['pattern']}"
            return
        state["match"], state["top"], state["message"] = hit, min(hit, max_top()), ""

    def get_body() -> StyleAndTextTuples:
        if state["help"]:
            out: StyleAndTextTuples = [("class:pager.help", " keys ".center(size()[1], "─")), ("", "\n")]
            for keys, what in PAGER_KEYS:
                out += [("class:pager.help.key", f"  {keys:<22}"), ("class:pager.help", f" {what}"), ("", "\n")]
            return out
        top, height = state["top"], body_height()
        if state["wrap"]:
            shown, used, i = [], 0, top
            while i < len(lines) and used < height:
                shown.append(lines[i])
                used += rows_of(i)
                i += 1
        else:
            shown = lines[top: top + height]
        if state["pattern"]:
            shown = [_highlight(ln, state["pattern"], hl_on, hl_off) for ln in shown]
        fragments: StyleAndTextTuples = []
        for k, line in enumerate(shown):
            parts = to_formatted_text(ANSI(line))
            if state["left"] and not state["wrap"]:
                parts = _cut_left(parts, state["left"])
            fragments += parts
            if k < len(shown) - 1:
                fragments.append(("", "\n"))
        return fragments

    def get_status() -> StyleAndTextTuples:
        top, h = state["top"], body_height()
        end = min(len(lines), top + h) if not state["wrap"] else min(len(lines), top + max(1, len(_visible())))
        pct = 100 if top >= max_top() else int(end * 100 / len(lines)) if lines else 100
        msg = state["message"] or "↑↓ scroll  / search  n/N next  w wrap  ? keys  q quit"
        out: StyleAndTextTuples = []
        if title:
            out.append(("class:pager.title", f" {_short(title, size()[1] // 3)} "))
        out += [("class:pager.status.key", f" lines {top + 1}-{end}/{len(lines)} ({pct}%) "),
                ("class:pager.status", f" {msg} ")]
        return out

    def _visible() -> list[int]:
        rows, used, i = [], 0, state["top"]
        while i < len(lines) and used < body_height():
            rows.append(i)
            used += rows_of(i)
            i += 1
        return rows

    kb = KeyBindings()
    not_searching = Condition(lambda: not state["searching"])
    searching = Condition(lambda: state["searching"])

    @kb.add("q", filter=not_searching)
    @kb.add("escape", filter=not_searching)
    def _quit(event):
        if state["help"]:
            state["help"] = False  # the first q closes the key help
            return
        event.app.exit()

    @kb.add("c-c")
    def _interrupt(event):
        event.app.exit()

    @kb.add("?", filter=not_searching)
    def _help(event):
        state["help"] = not state["help"]

    @kb.add("down", filter=not_searching)
    @kb.add("j", filter=not_searching)
    @kb.add("enter", filter=not_searching)
    def _down(event):
        scroll(1)

    @kb.add("up", filter=not_searching)
    @kb.add("k", filter=not_searching)
    def _up(event):
        scroll(-1)

    @kb.add("pagedown", filter=not_searching)
    @kb.add("space", filter=not_searching)
    @kb.add("f", filter=not_searching)
    def _pgdn(event):
        scroll(len(_visible()) - 1 or 1)

    @kb.add("pageup", filter=not_searching)
    @kb.add("b", filter=not_searching)
    def _pgup(event):
        scroll(-(body_height() - 1))

    @kb.add("d", filter=not_searching)
    def _half_down(event):
        scroll(max(1, body_height() // 2))

    @kb.add("u", filter=not_searching)
    def _half_up(event):
        scroll(-max(1, body_height() // 2))

    @kb.add("right", filter=not_searching)
    def _right(event):
        state["left"] = min(max_left(), state["left"] + 8)

    @kb.add("left", filter=not_searching)
    def _left(event):
        state["left"] = max(0, state["left"] - 8)

    @kb.add("w", filter=not_searching)
    def _wrap(event):
        state["wrap"] = not state["wrap"]
        state["left"] = 0
        state["top"] = min(state["top"], max_top())

    @kb.add("g", filter=not_searching)
    @kb.add("home", filter=not_searching)
    def _home(event):
        state["top"] = 0

    @kb.add("G", filter=not_searching)
    @kb.add("end", filter=not_searching)
    def _end(event):
        state["top"] = max_top()

    @kb.add("/", filter=not_searching)
    def _start_search(event):
        state["searching"] = True
        state["help"] = False
        search.reset()
        event.app.layout.focus(search_window)

    @kb.add("n", filter=not_searching)
    def _next(event):
        find(True)

    @kb.add("N", filter=not_searching)
    def _prev(event):
        find(False)

    @kb.add("enter", filter=searching)
    def _submit_search(event):
        state["searching"] = False
        state["pattern"] = search.text
        state["match"] = -1
        event.app.layout.focus(body_window)
        find(True)

    @kb.add("escape", filter=searching)
    def _cancel_search(event):
        state["searching"] = False
        event.app.layout.focus(body_window)

    class _Body(FormattedTextControl):
        def mouse_handler(self, mouse_event: MouseEvent) -> Any:
            if mouse_event.event_type == MouseEventType.SCROLL_DOWN:
                scroll(3)
                return None
            if mouse_event.event_type == MouseEventType.SCROLL_UP:
                scroll(-3)
                return None
            return NotImplemented

    body_window = Window(_Body(get_body, focusable=True), wrap_lines=Condition(lambda: state["wrap"]))
    search_window = Window(BufferControl(search), height=1, style="class:pager.search",
                           get_line_prefix=lambda *_: [("class:pager.search", "/")])
    layout = Layout(HSplit([
        body_window,
        ConditionalContainer(Window(FormattedTextControl(get_status), height=1, style="class:pager.status"),
                             filter=not_searching),
        ConditionalContainer(search_window, filter=searching),
    ]), focused_element=body_window)
    # prompt_toolkit redraws on SIGWINCH by itself (Application._on_resize), so a resize is
    # reflected at once; body_height() is read on every render.
    app: Application = Application(layout=layout, key_bindings=kb, full_screen=True, style=ui.pt_style,
                                   mouse_support=ui.mouse)  # the `mouse` setting; off keeps selection
    app.run()
    return state  # for tests: where the viewer was when it closed


def _short(text: str, width: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= max(8, width) else text[:max(8, width) - 1] + "…"


def _cut_left(fragments: StyleAndTextTuples, columns: int) -> StyleAndTextTuples:
    """The fragments with their first `columns` screen columns removed (horizontal scrolling)."""
    out: StyleAndTextTuples = []
    skip = columns
    for item in fragments:
        style, text = item[0], item[1]
        if skip <= 0:
            out.append((style, text))
            continue
        kept: list[str] = []
        for ch in text:
            if skip > 0:
                skip -= get_cwidth(ch)
            else:
                kept.append(ch)
        if kept:
            out.append((style, "".join(kept)))
    return out


def _theme_highlight(ui: UI) -> tuple[str, str]:
    """ANSI on/off codes for search matches in the theme's colors (accent background)."""
    try:
        r, g, b = Color.parse(ui.theme.accent).get_truecolor()
        fr, fg, fb = Color.parse(ui.theme._on_accent()).get_truecolor()
    except Exception:  # noqa: BLE001 — any odd color: plain reverse video still works
        return _HL_ON, _HL_OFF
    return f"\x1b[1;38;2;{fr};{fg};{fb};48;2;{r};{g};{b}m", "\x1b[0m"


def _next_match(plain: list[str], pattern: str, top: int, match: int, height: int,
                forward: bool) -> int | None:
    """Line of the next search hit: after the last hit while it's on screen, else from the screen top."""
    if top <= match < top + height:
        start = match + 1 if forward else match - 1
    else:
        start = top if forward else top - 1
    return _find_line(plain, pattern, start, forward)


def _find_line(plain: list[str], pattern: str, start: int, forward: bool) -> int | None:
    """Index of the next line (from `start`, inclusive) containing `pattern`, ignoring case."""
    pat = pattern.lower()
    order = range(max(start, 0), len(plain)) if forward else range(min(start, len(plain) - 1), -1, -1)
    return next((i for i in order if pat in plain[i].lower()), None)


_HL_ON, _HL_OFF = "\x1b[7m", "\x1b[27m"  # reverse video on/off; leaves the line's colors alone
_RESET_RE = re.compile(r"\x1b\[0?m")


def _highlight(line: str, pattern: str, on: str = _HL_ON, off: str = _HL_OFF) -> str:
    """Mark every case-insensitive occurrence of `pattern` in an ANSI-colored line with `on` … `off`.

    When `off` is a full reset (`\\x1b[0m`), the line's own SGR codes seen since its last reset
    are re-applied after it, so the text after a match keeps its color.
    """
    plain = _ANSI_RE.sub("", line).lower()
    pat = pattern.lower()
    starts, ends = set(), set()
    pos = plain.find(pat)
    while pat and pos != -1:
        starts.add(pos)
        ends.add(pos + len(pat))
        pos = plain.find(pat, pos + len(pat))
    if not starts:
        return line
    out: list[str] = []
    active: list[str] = []  # the line's own codes since its last reset, to restore after `off`
    p = i = 0
    inside = False
    full_reset = _RESET_RE.fullmatch(off) is not None
    while i < len(line):
        code = _ANSI_RE.match(line, i)
        if code:  # keep the line's own codes; a reset inside a match must not end the highlight
            out.append(code.group() + (on if inside else ""))
            if _RESET_RE.fullmatch(code.group()):
                active.clear()
            elif code.group().endswith("m"):
                active.append(code.group())
            i = code.end()
            continue
        if p in ends:
            out.append(off + ("".join(active) if full_reset else ""))
            inside = False
        if p in starts:
            out.append(on)
            inside = True
        out.append(line[i])
        p += 1
        i += 1
    if inside:
        out.append(off + ("".join(active) if full_reset else ""))
    return "".join(out)
