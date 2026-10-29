"""Asking the user for a value: `name = input("What is your name? ")` and `read -p "…" name`.

The answer becomes a shell variable (`$name`) for the rest of the session, so an rc file, a
script or the prompt can ask once and use it later. Only the interactive shell can ask: over MCP
(and anywhere without a terminal) the line fails with a clear message.

`input(...)` is recognised on a line of its own, before the shell's parser sees it (the
parentheses are Python, not shell): `NAME = input()`, `NAME = input("prompt")`, with any Python
string literal as the prompt (single or double quotes, escapes), or an f-string
(`input(f"Age of {who}? ")`, filled in with the variables in scope). `NAME = int(input("prompt"))` and
`float(...)` ask until the answer is a number (Python would stop with a ValueError; a shell says
so and asks again), and the variable keeps the number for Python code (script.PyValue; `$n` is
its text). The same lines work inside blocks and functions (script._Runner.run_input), and
`input(…)` also works anywhere in a `-py` expression (script._input).
"""

from __future__ import annotations

import ast
import re
from typing import Any

INPUT_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*input\s*\((.*)\)\s*$", re.S)
# NAME = int(input(...)) / float(input(...))
NUMBER_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(int|float)\s*\(\s*input\s*\((.*)\)\s*\)\s*$", re.S)
CONVERT = {"str": str, "int": int, "float": float}


class FPrompt(str):
    """An f-string prompt, as typed (`f"Age of {who}? "`): the caller fills it in with
    script.format_prompt and the variables in scope."""


class AskError(Exception):
    """The value could not be asked for (no terminal, cancelled, a bad prompt)."""

    def __init__(self, message: str, status: int = 1):
        super().__init__(message)
        self.status = status


def parse_ask_line(line: str) -> tuple[str, str, str] | None:
    """(NAME, prompt, kind) for `NAME = input(…)`, `NAME = int(input(…))` or
    `NAME = float(input(…))`, where kind is str, int or float; None for any other line."""
    m = NUMBER_LINE.match(line)
    if m:
        name, kind, raw = m.group(1), m.group(2), m.group(3).strip()
    else:
        m = INPUT_LINE.match(line)
        if not m:
            return None
        name, kind, raw = m.group(1), "str", m.group(2).strip()
    return name, _prompt_literal(raw), kind


def _prompt_literal(raw: str) -> str:
    if not raw:
        return ""
    if re.match(r"[fF][rR]?['\"]|[rR][fF]['\"]", raw):
        try:
            tree = ast.parse(raw, mode="eval")
        except SyntaxError as exc:
            raise AskError(f"input: the prompt is not a valid f-string ({exc.msg}): {raw}") from None
        if not isinstance(tree.body, ast.JoinedStr):
            raise AskError(f"input: the prompt must be one string, not {raw}")
        return FPrompt(raw)
    try:
        prompt = ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        raise AskError(f"input: the prompt must be a string, like input(\"Your name? \"), not {raw}") from None
    if not isinstance(prompt, str):
        raise AskError(f"input: the prompt must be a string, not {type(prompt).__name__}")
    return prompt


def ask_as(ctx: Any, prompt: str, kind: str = "str", say: Any = None) -> str | int | float:
    """Ask until the answer converts with int / float (kind), and return the number (`7`, `2.5`;
    the text for kind str). `say(message)` reports a wrong answer before asking again."""
    convert = CONVERT[kind]
    while True:
        answer = ask(ctx, prompt)
        if kind == "str":
            return answer
        try:
            value = convert(answer.strip())
        except ValueError:
            message = f"'{answer.strip()}' is not {'a whole number' if kind == 'int' else 'a number'}; try again"
            if say is not None:
                say(message)
            continue
        return value


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
