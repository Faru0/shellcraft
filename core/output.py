"""Live feedback: delayed spinners, styled errors, and an auto-pager viewport."""

from __future__ import annotations

import re
import threading
from typing import Any, Callable

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.filters import Condition
from rich.panel import Panel
from rich.text import Text

from core.parser import ParseError
from core.pipeline import PipelineError
from core.themes import UI

SPINNER_DELAY = 0.15  # seconds before a spinner appears; fast commands never flash one
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def make_spinner_runner(ui: UI) -> Callable[[str, Callable[[], Any]], Any]:
    """Run each segment in a worker thread and show a Rich spinner if it takes a while."""

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

        threading.Thread(target=target, daemon=True, name=f"shellcraft:{label}").start()
        if not done.wait(SPINNER_DELAY):
            with ui.console.status(Text(label, style="sc.accent"), spinner="dots12", spinner_style="sc.accent"):
                while not done.wait(0.05):
                    pass
        if "error" in box:
            raise box["error"]
        return box.get("value")

    return runner


def show(ui: UI, value: Any) -> None:
    """Print a command's final output, paging it if it is taller than the terminal."""
    if value is None or value == "":
        return
    renderable = Text.from_ansi(value.rstrip("\n")) if isinstance(value, str) else value
    console = ui.console
    if not console.is_terminal:
        console.print(renderable)
        return
    with console.capture() as capture:
        console.print(renderable)
    rendered = capture.get()
    if rendered.count("\n") > console.height - 2:
        page(ui, rendered)
    else:
        console.print(renderable)


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


def page(ui: UI, rendered: str) -> None:
    """Interactive scrollable viewport for ANSI text (cross-platform, no external pager)."""
    lines = rendered.rstrip("\n").split("\n")
    plain = [_ANSI_RE.sub("", ln) for ln in lines]
    state = {"top": 0, "searching": False, "pattern": "", "message": ""}
    search = Buffer(multiline=False)

    def body_height() -> int:
        return max(1, app.output.get_size().rows - 1)

    def max_top() -> int:
        return max(0, len(lines) - body_height())

    def scroll(delta: int) -> None:
        state["top"] = min(max_top(), max(0, state["top"] + delta))

    def find(forward: bool = True) -> None:
        pat = state["pattern"].lower()
        if not pat:
            return
        order = range(state["top"] + 1, len(lines)) if forward else range(state["top"] - 1, -1, -1)
        for i in order:
            if pat in plain[i].lower():
                state["top"] = min(i, max_top()) if forward else i
                state["message"] = ""
                return
        state["message"] = f"pattern not found: {state['pattern']}"

    def get_body():
        top = state["top"]
        return ANSI("\n".join(lines[top: top + body_height()]))

    def get_status():
        top, h = state["top"], body_height()
        end = min(len(lines), top + h)
        pct = 100 if len(lines) <= h else int(end * 100 / len(lines))
        msg = state["message"] or "↑↓ scroll  PgUp/PgDn page  g/G top/end  / search  n/N next/prev  q quit"
        return [("class:pager.status.key", f" lines {top + 1}-{end}/{len(lines)} ({pct}%) "),
                ("class:pager.status", f" {msg} ")]

    kb = KeyBindings()
    not_searching = Condition(lambda: not state["searching"])
    searching = Condition(lambda: state["searching"])

    @kb.add("q", filter=not_searching)
    @kb.add("escape", filter=not_searching)
    @kb.add("c-c")
    def _quit(event):
        event.app.exit()

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
        scroll(body_height() - 1)

    @kb.add("pageup", filter=not_searching)
    @kb.add("b", filter=not_searching)
    def _pgup(event):
        scroll(-(body_height() - 1))

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
        event.app.layout.focus(body_window)
        find(True)

    @kb.add("escape", filter=searching)
    def _cancel_search(event):
        state["searching"] = False
        event.app.layout.focus(body_window)

    body_window = Window(FormattedTextControl(get_body, focusable=True), wrap_lines=False)
    search_window = Window(BufferControl(search), height=1, style="class:pager.search",
                           get_line_prefix=lambda *_: [("class:pager.search", "/")])
    layout = Layout(HSplit([
        body_window,
        ConditionalContainer(Window(FormattedTextControl(get_status), height=1, style="class:pager.status"),
                             filter=not_searching),
        ConditionalContainer(search_window, filter=searching),
    ]), focused_element=body_window)
    app: Application = Application(layout=layout, key_bindings=kb, full_screen=True, style=ui.pt_style)
    app.run()
