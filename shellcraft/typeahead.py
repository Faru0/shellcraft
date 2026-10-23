"""What was typed while a command ran, kept for the next prompt without the junk.

Between two prompts the terminal keeps delivering: keys typed ahead (kept, as in bash), but also
answers to the last prompt's cursor-position query and mouse reports if one was in flight when
reporting was switched off. prompt_toolkit reads all of it at the next prompt, and a report
that lost its escape prefix on the way shows up as text: `<64;10;5M`, `12;34R`. So before a
prompt starts, what is waiting is read here, parsed the way the prompt would, and only real
keys (text, Enter, pastes) are handed to the prompt as typeahead."""

from __future__ import annotations

import os
import select
import sys
from typing import Any

from prompt_toolkit.input.typeahead import store_typeahead
from prompt_toolkit.input.vt100_parser import Vt100Parser
from prompt_toolkit.key_binding import KeyPress
from prompt_toolkit.keys import Keys

JUNK = {Keys.CPRResponse, Keys.Vt100MouseEvent, Keys.WindowsMouseEvent, Keys.Ignore}


def pending_bytes(fd: int) -> bytes:
    """Whatever the terminal has delivered and nobody read yet (POSIX; nothing on Windows, where
    the console delivers key records, not bytes)."""
    if sys.platform == "win32":
        return b""
    chunks = []
    try:
        while select.select([fd], [], [], 0)[0]:
            data = os.read(fd, 4096)
            if not data:
                break
            chunks.append(data)
    except (OSError, ValueError):
        pass
    return b"".join(chunks)


def keep(data: str) -> tuple[list[KeyPress], int]:
    """The keys in `data` worth passing on, and how many were dropped (cursor replies, mouse)."""
    kept, dropped = sort(data)
    return kept, len(dropped)


def sort(data: str) -> tuple[list[KeyPress], list[KeyPress]]:
    """The keys in `data`: those worth passing on, and the junk (cursor replies, mouse reports)."""
    keys: list[KeyPress] = []
    parser = Vt100Parser(keys.append)
    parser.feed_and_flush(data)
    return [k for k in keys if k.key not in JUNK], [k for k in keys if k.key in JUNK]


def collect(input_obj: Any, fd: int | None = None) -> tuple[int, int, str]:
    """Move the pending input to the prompt's typeahead, junk removed: (keys kept, junk keys
    dropped, a sample of the junk for `diag`). Read in raw mode: in cooked mode a partial line
    is not readable yet, and would reach the prompt untouched the moment it switches to raw."""
    nothing = (0, 0, "")
    if fd is None:
        try:
            fd = sys.stdin.fileno()
        except (AttributeError, ValueError, OSError):
            return nothing
    try:
        with input_obj.raw_mode():
            data = pending_bytes(fd)
    except Exception:  # noqa: BLE001 — not a terminal (tests, pipes): nothing to collect
        return nothing
    if not data:
        return nothing
    kept, junk = sort(data.decode("utf-8", errors="replace"))
    if kept:
        store_typeahead(input_obj, kept)
    sample = " ".join(repr(k.data) for k in junk[:4]) + (" …" if len(junk) > 4 else "")
    return len(kept), len(junk), sample
