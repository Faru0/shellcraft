"""The wizard's screen: one console for everything it prints and asks.

Standalone (`python -m tools.ingest`, `shellcraft ingest`) it is a plain Rich console on stdout.
Inside ShellCraft, the `ingest` builtin hands over the shell's own console (`using()`), so the
wizard writes the way every other command does: on Windows to the console window directly, sized
like the prompt and inside the rows the footer leaves free, scrolling at the bottom instead of
writing over the last line.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.syntax import Syntax
from rich.text import Text

console: Console = Console(highlight=False)


@contextlib.contextmanager
def using(other: Console | None) -> Iterator[None]:
    """Print and ask through `other` for a while (None: keep the current console)."""
    global console
    if other is None:
        yield
        return
    previous, console = console, other
    try:
        yield
    finally:
        console = previous


def say(text: str = "", style: str = "") -> None:
    console.print(Text(text, style=style) if style else text, soft_wrap=True)


def step(n: int, title: str) -> None:
    console.print()
    console.rule(Text(f"{n}. {title}", style="bold cyan"), align="left")


def show_diff(diff: str) -> None:
    if diff.strip():
        console.print(Syntax(diff, "diff", theme="ansi_dark", word_wrap=True))
    else:
        say("(no changes)", "dim")


def ask(question: str, **kwargs: Any) -> str:
    """A question with a typed answer (Rich's Prompt), on the wizard's console."""
    return Prompt.ask(question, console=console, **kwargs)


def confirm(question: str, default: bool = False) -> bool:
    """A yes/no question (Rich's Confirm), on the wizard's console."""
    return Confirm.ask(question, console=console, default=default)
