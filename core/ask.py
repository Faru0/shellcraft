"""Asking the user for a value: `name = input("What is your name? ")` and `read -p "…" name`.

The answer becomes a shell variable (`$name`) for the rest of the session, so an rc file, a
script or the prompt can ask once and use it later. Only the interactive shell can ask: over MCP
(and anywhere without a terminal) the line fails with a clear message.

`input(...)` is recognised on a line of its own, before the shell's parser sees it (the
parentheses are Python, not shell): `NAME = input()`, `NAME = input("prompt")`, with any Python
string literal as the prompt (single or double quotes, escapes). Inside `for` / `if` blocks, use
`read -p "prompt" NAME`, which is a normal command.
"""

from __future__ import annotations

import ast
import re
from typing import Any

INPUT_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*input\s*\((.*)\)\s*$", re.S)


class AskError(Exception):
    """The value could not be asked for (no terminal, cancelled, a bad prompt)."""

    def __init__(self, message: str, status: int = 1):
        super().__init__(message)
        self.status = status


def parse_input_line(line: str) -> tuple[str, str] | None:
    """(NAME, prompt) for `NAME = input("prompt")`, None for any other line. Raises AskError when
    it is an input() line whose prompt is not a plain string."""
    m = INPUT_LINE.match(line)
    if not m:
        return None
    name, raw = m.group(1), m.group(2).strip()
    if not raw:
        return name, ""
    try:
        prompt = ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        raise AskError(f"input: the prompt must be a string, like input(\"Your name? \"), not {raw}") from None
    if not isinstance(prompt, str):
        raise AskError(f"input: the prompt must be a string, not {type(prompt).__name__}")
    return name, prompt


def ask(ctx: Any, prompt: str, hidden: bool = False) -> str:
    """Show `prompt` and read one line (without echo when `hidden`). Raises AskError without a
    terminal, or on Ctrl-C (status 130) / Ctrl-D (status 1)."""
    if not ctx.interactive or ctx.ui is None:
        raise AskError("input: there is no terminal to ask on (the interactive shell only)")
    from prompt_toolkit import prompt as pt_prompt

    try:
        return pt_prompt(prompt, is_password=hidden)
    except KeyboardInterrupt:
        raise AskError("input: cancelled", 130) from None
    except EOFError:
        raise AskError("input: no answer (end of input)") from None
