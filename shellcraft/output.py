"""Live feedback: delayed spinners, styled errors, and an auto-pager viewport."""

from __future__ import annotations

import asyncio
import os
import re
import sys
import threading
import time
from typing import Any, Callable

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import ANSI, StyleAndTextTuples, to_formatted_text
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout, VSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType
from prompt_toolkit.utils import get_cwidth
from rich.color import Color
from rich.panel import Panel
from rich.text import Text

from shellcraft import settings
from shellcraft.context import Paged, ShellContext, SoftWrapped, Styled
from shellcraft.parser import ParseError
from shellcraft.pipeline import PipelineError
from shellcraft.themes import UI

SPINNER_DELAY = 0.15  # seconds before a spinner appears; fast commands never flash one
SPINNER_ELAPSED_AFTER = 3.0  # seconds after which the spinner shows how long it has been
SPINNER_HINT_AFTER = 10.0  # ... and that Ctrl-C cancels
PAGER_DIRECT_LINES = 5_000  # plain text this tall goes straight to the pager, skipping Rich
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# OSC sequences (hyperlinks from the `links` setting): the pager draws its own screen, so they
# are removed before it parses the colors; prompt_toolkit's ANSI parser would show them as text.
_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


CLEAR_SCREEN = "\x1b[H\x1b[2J\x1b[3J"  # home, clear the screen, clear the scrollback


def clear_screen(ui: UI) -> None:
    """Clear the terminal as `clear` does in bash and `cls` on Windows: the screen and the
    scrollback (ESC[3J). Written straight to the terminal: Rich's own clear sends only ESC[2J,
    which Windows Terminal turns into scrolling the old text away rather than removing it."""
    from shellcraft.scrollback import scrollback_of, terminal_file

    try:
        file = terminal_file(ui.console.file)
        file.write(CLEAR_SCREEN)
        file.flush()
    except (OSError, ValueError):
        pass
    scrollback = scrollback_of(ui.console.file)
    if scrollback is not None:
        scrollback.clear()  # like the terminal's own history


def strip_links(text: str) -> str:
    return _OSC_RE.sub("", text)


def make_spinner_runner(ctx: ShellContext) -> Callable[[str, Callable[[], Any]], Any]:
    """Run each segment in a worker thread and show a Rich spinner if it takes a while."""
    ui = ctx.ui

    def runner(label: str, fn: Callable[[], Any]) -> Any:
        from shellcraft.modkit import run_cancellable

        done = threading.Event()
        cancel = threading.Event()  # set on Ctrl-C: modules that check modkit.cancelled() stop
        box: dict[str, Any] = {}

        def target() -> None:
            try:
                box["value"] = run_cancellable(fn, cancel)
            except BaseException as exc:  # noqa: BLE001 — re-raised on the main thread
                box["error"] = exc
            finally:
                done.set()

        started = time.monotonic()
        threading.Thread(target=target, daemon=True, name=f"shellcraft:{label}").start()
        try:
            if not done.wait(SPINNER_DELAY) and settings.get(ctx.config, "spinner"):
                with ui.console.status(Text(label, style="sc.accent"), spinner="dots12",
                                       spinner_style="sc.accent") as status:
                    shown = label
                    while not done.wait(0.05):
                        text = spinner_label(label, time.monotonic() - started)
                        if text != shown:
                            shown = text
                            status.update(Text(text, style="sc.accent"))
            while not done.wait(0.1):  # in slices: an untimed wait only sees Ctrl-C once fn returns
                pass
        except KeyboardInterrupt:
            cancel.set()  # the thread can't be killed; a module that checks modkit.cancelled() stops
            raise
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
    """Print a command's final output; a Paged value with page=True (a man page, `cmd | less`)
    opens the viewer instead, unless the `pager` setting is off. Tall output is never paged by
    itself: `less` is for that. `title` (the command line) is shown in the viewer's status bar."""
    forced: bool | None = None
    soft = False
    while isinstance(value, (Paged, Styled, SoftWrapped)):  # they may wrap each other (help: Styled(Paged))
        if isinstance(value, SoftWrapped):
            value, soft = value.value, True
        elif isinstance(value, Paged):
            value, forced = value.value, value.page
        else:
            if not value.text:
                return
            value = value.renderable
    if value is None or value == "":
        return
    console = ui.console
    if isinstance(value, str) and value.count("\n") > PAGER_DIRECT_LINES and console.is_terminal \
            and pager and forced:
        page(ui, value.rstrip("\n"), title)  # too big to run through Rich first
        return
    renderable = Text.from_ansi(value.rstrip("\n")) if isinstance(value, str) else value
    if not console.is_terminal or not pager or not forced:
        console.print(renderable, soft_wrap=soft)  # soft: a long line the terminal folds, copied whole
        return
    with console.capture() as capture:
        console.print(renderable)
    rendered = capture.get()
    if forced:
        if isinstance(renderable, Text):
            # Plain or colored text: give the pager the lines unwrapped, so ← / → scroll through
            # long lines and `w` wraps them (Rich would have wrapped them at the window width).
            with console.capture() as capture:
                console.print(renderable, soft_wrap=True)
            rendered = capture.get()
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
    ("mouse", "wheel scrolls; drag the bar on the right; click a line to mark it"),
    ("", "(n / N search on from the mark); click a key in the status bar"),
    ("?", "this help"), ("q  Esc", "close"),
]
# The status bar's hints; the keys with an action are clickable.
PAGER_HINTS = [("↑↓", "scroll", None), ("/", "search", "search"), ("n/N", "next", "next"),
               ("w", "wrap", "wrap"), ("?", "keys", "help"), ("q", "quit", "quit")]


def page(ui: UI, rendered: str, title: str | None = None, at_end: int | None = None,
         close_past_end: bool = False) -> dict[str, Any]:
    """Interactive scrollable viewport for ANSI text (cross-platform, no external pager).
    `at_end`: open N lines above the end instead of at the top (the scroll view); with
    `close_past_end`, scrolling down past the end closes it. The scroll view (`at_end`) is
    bottom-aligned like a terminal: output shorter than the screen sits at its bottom."""
    lines = strip_links(rendered).rstrip("\n").split("\n")
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
        if close_past_end and delta > 0 and state["top"] >= max_top() and not state["searching"]:
            app.exit()  # the scroll view: down past the end goes back to the prompt
            return
        state["top"] = min(max_top(), max(0, state["top"] + delta))
        state["message"] = ""  # a mark or a "not found" note lasts until the next scroll

    def find(forward: bool = True) -> None:
        if not state["pattern"]:
            return
        hit = _next_match(plain, state["pattern"], state["top"], state["match"], body_height(), forward)
        if hit is None:
            state["message"] = f"pattern not found: {state['pattern']}"
            return
        state["match"], state["top"], state["message"] = hit, min(hit, max_top()), ""

    def top_pad() -> int:
        """Blank rows above the text in the scroll view, so short output sits at the bottom of the
        screen, as the terminal showed it."""
        if at_end is None or state["help"] or state["wrap"]:
            return 0
        return max(0, body_height() - len(lines))

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
        fragments: StyleAndTextTuples = [("", "\n" * top_pad())] if top_pad() else []
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
        out: StyleAndTextTuples = []
        if title:
            out.append(("class:pager.title", f" {_short(title, size()[1] // 3)} "))
        out.append(("class:pager.status.key", f" lines {top + 1}-{end}/{len(lines)} ({pct}%) "))
        if state["message"]:
            out.append(("class:pager.status", f" {state['message']} "))
            return out
        out.append(("class:pager.status", " "))
        for i, (key, what, action) in enumerate(PAGER_HINTS):
            if i:
                out.append(("class:pager.status", "  "))
            if action is None:
                out.append(("class:pager.status", f"{key} {what}"))
            else:
                out.append(("class:pager.status.key", key, actions[action]))
                out.append(("class:pager.status", f" {what}", actions[action]))
        out.append(("class:pager.status", " "))
        return out

    def line_at(y: int) -> int:
        """The line shown on screen row y of the body."""
        y -= top_pad()
        used, i = 0, state["top"]
        while i < len(lines) - 1 and used + rows_of(i) <= y:
            used += rows_of(i)
            i += 1
        return i

    def begin_search() -> None:
        state["searching"] = True
        state["help"] = False
        search.reset()
        app.layout.focus(search_window)

    def on_click(name: str) -> Callable[[MouseEvent], Any]:
        """A status-bar hint's handler: acts on the mouse-up, ignores the rest."""
        def handler(event: MouseEvent) -> Any:
            if event.event_type != MouseEventType.MOUSE_UP:
                return NotImplemented
            if name == "quit":
                app.exit()
            elif name == "help":
                state["help"] = not state["help"]
            elif name == "wrap":
                state["wrap"] = not state["wrap"]
                state["left"] = 0
                state["top"] = min(state["top"], max_top())
            elif name == "next":
                find(True)
            elif name == "search":
                begin_search()
            return None
        return handler

    actions = {name: on_click(name) for name in ("quit", "help", "wrap", "next", "search")}

    def get_scrollbar() -> StyleAndTextTuples:
        height = body_height()
        thumb = max(1, round(height * height / max(height, len(lines))))
        start = 0 if max_top() == 0 else round((height - thumb) * state["top"] / max_top())
        out: StyleAndTextTuples = []
        for row in range(height):
            on_thumb = start <= row < start + thumb
            style = "class:pager.scrollbar.thumb" if on_thumb else "class:pager.scrollbar"
            out.append((style, "┃" if on_thumb else "│"))
            if row < height - 1:
                out.append(("", "\n"))
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

    from prompt_toolkit.key_binding.bindings.mouse import load_mouse_bindings
    from prompt_toolkit.keys import Keys

    default_mouse = load_mouse_bindings()

    @kb.add(Keys.Vt100MouseEvent)
    @kb.add(Keys.WindowsMouseEvent)
    def _mouse(event):
        """The wheel scrolls wherever the mouse is: the Windows console reports wheel events at
        screen coordinates, which the layout's own handlers don't match (so the wheel did
        nothing on Windows 11, in both key readers). Other mouse events go to the layout."""
        from shellcraft.footer import parse_mouse, wheel_up

        parsed = parse_mouse(event.data)
        if parsed is not None and parsed[0] == "scroll" and not state["help"]:
            scroll(-3 if wheel_up(event.data) else 3)
            return None
        for binding in default_mouse.get_bindings_for_keys((event.key_sequence[0].key,)):
            return binding.handler(event)
        return NotImplemented

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
        begin_search()

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
            kind = mouse_event.event_type
            if kind == MouseEventType.SCROLL_DOWN:
                scroll(3)
                return None
            if kind == MouseEventType.SCROLL_UP:
                scroll(-3)
                return None
            if kind == MouseEventType.MOUSE_UP:
                if state["help"]:
                    state["help"] = False
                    return None
                if state["searching"]:
                    return NotImplemented
                state["match"] = line_at(mouse_event.position.y)  # n / N search on from here
                state["message"] = f"line {state['match'] + 1} marked: n / N search from it"
                return None
            return NotImplemented

    class _Bar(FormattedTextControl):
        """The scrollbar: click or drag to the row's share of the file; the wheel scrolls."""

        def mouse_handler(self, mouse_event: MouseEvent) -> Any:
            kind = mouse_event.event_type
            if kind == MouseEventType.SCROLL_DOWN:
                scroll(3)
            elif kind == MouseEventType.SCROLL_UP:
                scroll(-3)
            elif kind in (MouseEventType.MOUSE_DOWN, MouseEventType.MOUSE_UP) or (
                    kind == MouseEventType.MOUSE_MOVE and mouse_event.button != MouseButton.NONE):
                rows = max(1, body_height() - 1)
                state["top"] = min(max_top(), round(max_top() * min(rows, mouse_event.position.y) / rows))
            else:
                return NotImplemented
            return None

    body_window = Window(_Body(get_body, focusable=True), wrap_lines=Condition(lambda: state["wrap"]))
    scrollbar = ConditionalContainer(Window(_Bar(get_scrollbar), width=1, dont_extend_width=True),
                                     filter=Condition(lambda: max_top() > 0 and not state["help"]))
    search_window = Window(BufferControl(search), height=1, style="class:pager.search",
                           get_line_prefix=lambda *_: [("class:pager.search", "/")])
    layout = Layout(HSplit([
        VSplit([body_window, scrollbar]),
        ConditionalContainer(Window(FormattedTextControl(get_status), height=1, style="class:pager.status"),
                             filter=not_searching),
        ConditionalContainer(search_window, filter=searching),
    ]), focused_element=body_window)
    # prompt_toolkit redraws on SIGWINCH by itself (Application._on_resize), so a resize is
    # reflected at once; body_height() is read on every render.
    app: Application = Application(layout=layout, key_bindings=kb, full_screen=True, style=ui.pt_style,
                                   mouse_support=ui.mouse)  # the `mouse` setting; off keeps selection
    # Opened from a click while the prompt's event loop runs (run_in_terminal): the viewer then
    # needs its own loop, in a thread; asyncio refuses a nested loop.
    def start_at_end() -> None:
        if at_end is not None:
            state["top"] = max(0, max_top() - at_end)

    app.run(in_thread=_loop_running(), pre_run=start_at_end)
    return state  # for tests: where the viewer was when it closed


def _loop_running() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


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


def describe_keys(presses: list[Any]) -> str:
    """One key press, as `diag keys` shows it: `escape v` is Alt-v, `escape c-v` Ctrl+Alt+v."""
    names = []
    for press in presses:
        key = press.key if isinstance(press.key, str) else str(press.key)
        key = key.removeprefix("Keys.").lower() if key.startswith("Keys.") else key
        names.append(key if key.strip() else repr(key))
    return " ".join(names)


def windows_flags(state: int) -> str:
    """The modifier bits of a Windows key event, named."""
    from shellcraft.console import LEFT_ALT_PRESSED, LEFT_CTRL_PRESSED, RIGHT_ALT_PRESSED, SHIFT_PRESSED

    names = [name for bit, name in ((LEFT_ALT_PRESSED, "left-alt"), (RIGHT_ALT_PRESSED, "right-alt"),
                                    (LEFT_CTRL_PRESSED, "left-ctrl"), (0x0004, "right-ctrl"),
                                    (SHIFT_PRESSED, "shift"), (0x0020, "numlock"), (0x0080, "capslock"),
                                    (0x0100, "enhanced")) if state & bit]
    return "+".join(names) or "none"


def key_test(ui: UI) -> list[str]:
    """`diag keys`: show what each key press reaches the shell as, until Enter. Alt-v must show
    as `escape v` (or `escape c-v`) for Alt-v to paste. On Windows, the raw modifier flags of the
    console's key events are shown too (left-alt, right-alt, left-ctrl…). Returns the lines."""
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.layout import Layout, Window
    from prompt_toolkit.layout.controls import FormattedTextControl

    from shellcraft import console as console_mod

    seen: list[str] = []
    kb = KeyBindings()

    def record(presses: list[Any]) -> None:
        line = describe_keys(presses)
        if console_mod.LAST_KEYS:
            state, vk, char = console_mod.LAST_KEYS[-1]
            line += f"    (windows: vk=0x{vk:02X} char={char!r} {windows_flags(state)})"
        seen.append(line)
        del seen[:-14]

    @kb.add("escape", Keys.Any)
    def _alt(event: Any) -> None:
        record(list(event.key_sequence))

    @kb.add(Keys.Any)
    def _any(event: Any) -> None:
        record(list(event.key_sequence))

    @kb.add("enter")
    def _done(event: Any) -> None:
        event.app.exit()

    def text() -> Any:
        head = [("class:pager.status", " key test · press keys (Alt-v, right Alt-v, AltGr-v) · Enter ends "),
                ("", "\n\n")]
        body = [("", "  " + line + "\n") for line in seen] or [("", "  (nothing yet)\n")]
        hint = [("", "\n"), ("class:pager.help", "  Alt-v pastes when it shows as: escape v   (or escape c-v)\n")]
        return head + body + hint

    app: Application = Application(layout=Layout(Window(FormattedTextControl(text))), key_bindings=kb,
                                   full_screen=False, style=ui.pt_style)
    app.run(in_thread=_loop_running())
    return seen


# Mouse reporting for `diag keys --raw --mouse`: clicks and the wheel (1000), SGR encoding (1006).
MOUSE_REPORT_ON, MOUSE_REPORT_OFF = "\x1b[?1000h\x1b[?1006h", "\x1b[?1006l\x1b[?1000l"
SGR_MOUSE_RE = re.compile(r"\x1b\[<(\d+);(\d+);(\d+)([Mm])")


def raw_keys(write: Callable[[str], None], vt: bool = False, fd: int | None = None, mouse: bool = False,
             control: Callable[[str], None] | None = None) -> list[str]:
    """`diag keys --raw`: what the console itself delivers, below prompt_toolkit, until Enter.
    Windows: every key record from CONIN$, key down and key up, with its virtual key, scan code,
    character and modifier flags (`vt`: with the console's VT input on, as the direct key reader
    has it). Elsewhere: the raw bytes the terminal sends for each key press.
    `mouse`: only the mouse (clicks and the wheel), with mouse reporting turned on; `control`
    writes the escape sequences that turn it on and off (default: to `fd`). Returns the lines."""
    lines: list[str] = []

    def emit(line: str) -> None:
        lines.append(line)
        write(line)

    fd = sys.stdin.fileno() if fd is None else fd
    if control is None:
        def control(seq: str) -> None:
            os.write(fd, seq.encode())
    if sys.platform == "win32":
        _raw_keys_windows(emit, vt, mouse, control)
    elif mouse:
        _raw_mouse_posix(emit, fd, control)
    else:
        _raw_keys_posix(emit, fd)
    return lines


def describe_sgr_mouse(code: int, x: int, y: int, final: str) -> str:
    """One SGR mouse report in words: `wheel up at 12,5 +ctrl`, `left down at 3,1`."""
    mods = "".join(f" +{name}" for bit, name in ((4, "shift"), (8, "alt"), (16, "ctrl")) if code & bit)
    button = code & 3
    if code & 64:
        what = {0: "wheel up", 1: "wheel down", 2: "wheel left", 3: "wheel right"}[button]
    else:
        name = ("left", "middle", "right", "none")[button]
        what = f"{name} {'move' if code & 32 else 'up' if final == 'm' else 'down'}"
    return f"{what} at {x},{y}{mods}"


def _mouse_reports(buf: str, emit: Callable[[str], None]) -> tuple[str, bool]:
    """Print every complete SGR mouse report in `buf`; returns (the unfinished rest, Enter seen)."""
    done = False
    pos = 0
    for m in SGR_MOUSE_RE.finditer(buf):
        done = done or "\r" in buf[pos:m.start()] or "\n" in buf[pos:m.start()]
        emit(f"{describe_sgr_mouse(int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4))}"
             f"    (sgr {m.group(0)[1:]!r})")
        pos = m.end()
    rest = buf[pos:]
    if "\r" in rest or "\n" in rest:
        return "", True
    i = rest.rfind("\x1b")
    return (rest[i:] if i >= 0 and len(rest) - i < 32 else ""), done


def _raw_keys_windows(emit: Callable[[str], None], vt: bool, mouse: bool = False,
                      control: Callable[[str], None] | None = None) -> None:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    from prompt_toolkit.win32_types import INPUT_RECORD

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    conin_fd = os.open("CONIN$", os.O_RDWR | os.O_BINARY)  # type: ignore[attr-defined]
    conin = wintypes.HANDLE(msvcrt.get_osfhandle(conin_fd))  # type: ignore[attr-defined]
    mode = wintypes.DWORD()
    k32.GetConsoleMode(conin, ctypes.byref(mode))
    before = mode.value
    raw = (before | 0x0008) & ~(0x0001 | 0x0002 | 0x0004)  # window input on; processed, line, echo off
    raw = (raw | 0x0200) if vt else (raw & ~0x0200)  # VT input as asked
    if mouse:  # mouse records on; quick edit off (it takes the mouse for selecting), extended flags on
        raw = (raw | 0x0010 | 0x0080) & ~0x0040
    k32.SetConsoleMode(conin, raw)
    emit(f"console input mode 0x{before:04X} -> 0x{raw:04X} (VT input {'on' if vt else 'off'}"
         f"{', mouse input on' if mouse else ''}); "
         f"WT_SESSION {'set' if os.environ.get('WT_SESSION') else 'not set'}")
    if mouse and vt and control:  # with VT input the mouse comes as SGR reports, once asked for
        control(MOUSE_REPORT_ON)
    records = (INPUT_RECORD * 64)()
    count = wintypes.DWORD()
    pending = ""  # VT input: the characters of a mouse report not finished yet
    try:
        while True:
            if not k32.ReadConsoleInputW(conin, records, 64, ctypes.byref(count)):
                emit(f"ReadConsoleInputW failed (error {ctypes.get_last_error()})")
                return
            for i in range(count.value):
                rec = records[i]
                if mouse and rec.EventType == 2:  # MOUSE_EVENT
                    m = rec.Event.MouseEvent
                    line = describe_windows_mouse(m.ButtonState, m.EventFlags, m.ControlKeyState,
                                                  m.MousePosition.X, m.MousePosition.Y)
                    if line:
                        emit(line)
                    continue
                if rec.EventType != 1:  # only key events
                    continue
                k = rec.Event.KeyEvent
                if mouse:  # only the mouse: VT mouse reports arrive as typed characters; Enter ends
                    if k.KeyDown and k.uChar.UnicodeChar not in ("", "\x00"):
                        pending, done = _mouse_reports(pending + k.uChar.UnicodeChar, emit)
                        if done:
                            return
                    elif k.KeyDown and k.VirtualKeyCode == 0x0D:
                        return
                    continue
                emit(f"{'down' if k.KeyDown else 'up  '}  vk=0x{k.VirtualKeyCode:02X}  scan=0x{k.VirtualScanCode:02X}  "
                     f"char={k.uChar.UnicodeChar!r}  {windows_flags(k.ControlKeyState)}  "
                     f"(state=0x{k.ControlKeyState:04X})")
                if k.KeyDown and k.VirtualKeyCode == 0x0D:
                    return
    finally:
        if mouse and vt and control:
            control(MOUSE_REPORT_OFF)
        k32.SetConsoleMode(conin, before)
        os.close(conin_fd)


def describe_windows_mouse(buttons: int, flags: int, state: int, x: int, y: int) -> str | None:
    """A Windows MOUSE_EVENT record in words, or None for a plain move (too many to show)."""
    if flags & 0x0001 and not buttons & 0xFFFF:  # MOUSE_MOVED with no button held
        return None
    delta = (buttons >> 16) & 0xFFFF
    delta = delta - 0x10000 if delta & 0x8000 else delta
    if flags & 0x0004:  # MOUSE_WHEELED
        what = f"wheel {'up' if delta > 0 else 'down'} (delta {delta})"
    elif flags & 0x0008:  # MOUSE_HWHEELED
        what = f"wheel {'right' if delta > 0 else 'left'} (delta {delta})"
    else:
        held = [name for bit, name in ((1, "left"), (2, "right"), (4, "middle")) if buttons & bit]
        kind = "double-click" if flags & 0x0002 else "drag" if flags & 0x0001 else "buttons"
        what = f"{kind} {'+'.join(held) or 'released'}"
    return (f"{what} at {x + 1},{y + 1}  {windows_flags(state)}  "
            f"(buttons=0x{buttons & 0xFFFFFFFF:08X} flags=0x{flags:X})")


def _raw_mouse_posix(emit: Callable[[str], None], fd: int, control: Callable[[str], None]) -> None:
    import termios
    import tty

    before = termios.tcgetattr(fd)
    tty.setraw(fd)
    control(MOUSE_REPORT_ON)
    pending = ""
    try:
        while True:
            data = os.read(fd, 256)
            if not data:
                return
            pending, done = _mouse_reports(pending + data.decode("utf-8", "replace"), emit)
            if done:
                return
    finally:
        control(MOUSE_REPORT_OFF)
        termios.tcsetattr(fd, termios.TCSADRAIN, before)


def _raw_keys_posix(emit: Callable[[str], None], fd: int) -> None:
    import termios
    import tty

    before = termios.tcgetattr(fd)
    tty.setraw(fd)
    try:
        while True:
            data = os.read(fd, 64)
            if not data:
                return
            emit(f"bytes {data!r}  ({' '.join(f'{b:02x}' for b in data)})")
            if data in (b"\r", b"\n"):
                return
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, before)
