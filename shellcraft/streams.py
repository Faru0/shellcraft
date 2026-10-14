"""Streaming between pipeline segments: every segment runs at the same time, lines flow as made.

How a pipeline `a | b | c` runs (shellcraft.pipeline.run_pipeline):

- Each segment but the last runs in its own thread and writes into a bounded `Channel`; the
  next segment reads from it. The last segment runs in a thread too, writing into a final
  channel the calling thread reads (with the spinner while nothing has arrived, and live
  printing once output keeps coming; see `Gather`).
- A builtin registered with `streams=True` gets its stdin as a `Lines` object: iterate it for
  lines as they arrive, or `.read()` the rest as one text. Every other builtin and module gets a
  plain `str`, joined when its upstream has finished, exactly as before.
- A segment may return an iterator or generator of lines (or `Styled` items): they flow on as
  they are produced. A generator's `return N` is its exit status.
- When the last segment finishes early (`head -3`), the pipeline's `cancel` event is set: every
  producer still writing gets `Cancelled` on its next write (its generator is closed), so
  `yes | head -3` returns at once. Long-running producers (`tail -f`) also call `check()`.
- A failing segment's error travels down the channels as `UpstreamFailed` and is raised by the
  pipeline as that segment's own `PipelineError`, as before.

`Cancelled` and `UpstreamFailed` derive from BaseException, so a module's `except Exception` does
not swallow them.
"""

from __future__ import annotations

import queue
import threading
import weakref
from collections.abc import Iterator
from typing import Any

CHANNEL_SIZE = 256  # chunks buffered between two segments before the writer waits
POLL = 0.05  # seconds between checks of the cancel event while waiting

_local = threading.local()


class Cancelled(BaseException):
    """The pipeline was stopped (its consumer finished early, or Ctrl-C): stop producing."""


class UpstreamFailed(BaseException):
    """An earlier segment failed; carries its PipelineError down the pipeline."""

    def __init__(self, error: Exception):
        super().__init__(str(error))
        self.error = error


class StopFlag(threading.Event):
    """A pipeline's stop flag that is also stopped by its parent's: a background job's flag is the
    parent of every pipeline the job runs, so `jobs -k` stops all of them, while each pipeline's own
    end (`head` has its lines) stops only that pipeline."""

    def __init__(self, parent: threading.Event | None = None):
        super().__init__()
        self._children: weakref.WeakSet[StopFlag] = weakref.WeakSet()
        if isinstance(parent, StopFlag):
            parent._children.add(self)
        if parent is not None and parent.is_set():
            super().set()

    def set(self) -> None:
        super().set()
        for child in list(self._children):
            child.set()


def current_cancel() -> threading.Event | None:
    return getattr(_local, "cancel", None)


def set_cancel(event: threading.Event | None) -> None:
    """This thread's pipeline stop flag. It is also modkit's cancel event for the thread, so a
    module calling modkit.cancelled() / check_cancel() / sleep() stops when the pipeline does
    (`head` has its lines, Ctrl-C), not only on the spinner's Ctrl-C."""
    _local.cancel = event
    from shellcraft import modkit

    modkit._cancel_event.set(event)


def check() -> None:
    """For long-running producers (`tail -f`, `yes`): raise Cancelled once the pipeline stops."""
    event = current_cancel()
    if event is not None and event.is_set():
        raise Cancelled


def wait(seconds: float) -> None:
    """Sleep, but wake up (with Cancelled) as soon as the pipeline is stopped."""
    event = current_cancel()
    if event is None:
        import time

        time.sleep(seconds)
        return
    if event.wait(seconds):
        raise Cancelled


class Channel:
    """A bounded queue between two segments. Items: ("data", obj), ("end", status) or
    ("error", PipelineError). Every blocking call gives up with Cancelled once `cancel` is set."""

    def __init__(self, cancel: threading.Event, size: int = CHANNEL_SIZE):
        self.cancel = cancel
        self._q: queue.Queue[tuple[str, Any]] = queue.Queue(size)

    def _put(self, item: tuple[str, Any], may_drop: bool = False) -> None:
        while True:
            if self.cancel.is_set():
                if may_drop:
                    return
                raise Cancelled
            try:
                self._q.put(item, timeout=POLL)
                return
            except queue.Full:
                continue

    def put(self, obj: Any) -> None:
        self._put(("data", obj))

    def end(self, status: int = 0) -> None:
        self._put(("end", status), may_drop=True)

    def fail(self, error: Exception) -> None:
        self._put(("error", error), may_drop=True)

    def get(self, timeout: float | None = None) -> tuple[str, Any] | None:
        """The next item; None when `timeout` passes first. Raises Cancelled when stopped."""
        waited = 0.0
        while True:
            if self.cancel.is_set():
                raise Cancelled
            try:
                return self._q.get(timeout=POLL)
            except queue.Empty:
                waited += POLL
                if timeout is not None and waited >= timeout:
                    return None


class Lines:
    """A streaming builtin's stdin: iterate it for lines (each ending in a newline, except perhaps
    the last) as they arrive; `.read()` returns everything that is left as one text."""

    def __init__(self, source: Any):
        self._source = source  # a str, or a Channel from the previous segment
        self._pending = ""
        self._done = not isinstance(source, Channel)

    @staticmethod
    def of(stdin: Any) -> Lines:
        return stdin if isinstance(stdin, Lines) else Lines("" if stdin is None else stdin)

    def _chunks(self) -> Iterator[str]:
        if not isinstance(self._source, Channel):
            text, self._source = self._source, ""
            if text:
                yield text
            return
        if self._done:
            return
        while True:
            item = self._source.get()
            assert item is not None
            kind, value = item
            if kind == "data":
                if value:
                    yield value
            elif kind == "end":
                self._done = True
                return
            else:
                self._done = True
                raise UpstreamFailed(value)

    def __iter__(self) -> Iterator[str]:
        for chunk in self._chunks():
            text = self._pending + chunk
            lines = text.split("\n")
            self._pending = lines.pop()
            for line in lines:
                yield line + "\n"
        if self._pending:
            rest, self._pending = self._pending, ""
            yield rest

    def read(self) -> str:
        return "".join(self)


def text(stdin: Any) -> str:
    """Any stdin (a str or Lines) as one text."""
    return stdin.read() if isinstance(stdin, Lines) else ("" if stdin is None else stdin)


def is_stream(value: Any) -> bool:
    """A result that flows item by item: an iterator or generator (not a str or a renderable)."""
    return isinstance(value, Iterator) and not isinstance(value, (str, bytes))


def item_text(item: Any) -> str:
    """One streamed item as text, ending in a newline."""
    from shellcraft.context import to_text

    value = to_text(item)
    return value if value.endswith("\n") or not value else value + "\n"


def drain(result: Any) -> Any:
    """A streamed result collected into one value (a WithStatus when the generator returned a
    nonzero status), for callers that need it whole (`xargs`, `time`, main-thread builtins)."""
    from shellcraft.context import WithStatus

    items: list[Any] = []
    status = 0
    while True:
        try:
            items.append(next(result))
        except StopIteration as stop:
            if isinstance(stop.value, int):
                status = stop.value
            break
    value = combine([i for i in items if i is not None and i != ""])
    return WithStatus(value, status) if status else value


def combine(items: list[Any]) -> Any:
    """Streamed items as one value: a plain str, or a Styled when any item is styled (so a
    pipeline that finished quickly looks exactly as before: colors, the pager)."""
    from rich.text import Text

    from shellcraft.context import Styled

    if not items:
        return ""
    if all(isinstance(i, str) for i in items):
        return "".join(i if i.endswith("\n") else i + "\n" for i in items)
    styled = Text()
    plain: list[str] = []
    for item in items:
        line = item_text(item)
        plain.append(line)
        if isinstance(item, Styled) and isinstance(item.renderable, Text):
            styled.append_text(item.renderable)
            styled.append("\n")
        elif isinstance(item, Text):
            styled.append_text(item)
            styled.append("\n")
        else:
            styled.append(line)
    styled.rstrip()
    return Styled(styled, "".join(plain))
