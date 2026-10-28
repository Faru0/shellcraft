"""`getopts`: read a function's options (`-v`, `-f FILE`, `-vf FILE`, `-fFILE`) one at a time, as in bash.

Each call takes the next option from the arguments (`$@`, or the words given after NAME) and
sets NAME to its letter, `OPTARG` to its value and `OPTIND` to the number of the next argument
to read. Inside a group like `-vf`, the position within the word is kept in ctx.getopts_at
(OPTIND, position) and starts over when OPTIND no longer matches, so `OPTIND=1` restarts.

A function call (shellcraft.script.call_function, `def` too) and a func (shellcraft.funcs.run)
each start with OPTIND=1 through fresh(), and the caller's OPTIND is put back when they end, so a
function that reads options can call another one that does.
"""

from __future__ import annotations

import contextlib
from typing import Any, Iterator


@contextlib.contextmanager
def fresh(ctx: Any) -> Iterator[None]:
    """OPTIND=1 for a function call or func; the caller's OPTIND (and place in a group) after."""
    saved = ctx.variables.get("OPTIND"), ctx.getopts_at
    ctx.variables["OPTIND"] = "1"
    ctx.getopts_at = None
    try:
        yield
    finally:
        if saved[0] is None:
            ctx.variables.pop("OPTIND", None)
        else:
            ctx.variables["OPTIND"] = saved[0]
        ctx.getopts_at = saved[1]


def next_option(ctx: Any, optstring: str, args: list[str]) -> tuple[bool, str, str | None, str | None]:
    """Read one option. Returns (found, NAME's value, OPTARG or None to unset it, error message or
    None) and moves OPTIND on. found is False at the end: no arguments left, a word that isn't
    an option, or `--` (which is skipped)."""
    silent = optstring.startswith(":")
    if silent:
        optstring = optstring[1:]
    try:
        index = max(int(ctx.variables.get("OPTIND", "1")), 1)
    except ValueError:
        index = 1
    at = ctx.getopts_at
    pos = at[1] if at is not None and at[0] == index else 1

    def done(value: str, optarg: str | None, error: str | None, found: bool = True) -> tuple:
        ctx.variables["OPTIND"] = str(index)
        ctx.getopts_at = (index, pos)
        return found, value, optarg, error

    if index > len(args):
        return done("?", None, None, found=False)
    word = args[index - 1]
    if pos == 1 or pos >= len(word):
        pos = 1
        if word == "--":
            index += 1
            return done("?", None, None, found=False)
        if len(word) < 2 or not word.startswith("-"):
            return done("?", None, None, found=False)
    letter = word[pos]
    pos += 1
    if pos >= len(word):
        index, pos = index + 1, 1
    spot = optstring.find(letter) if letter != ":" else -1
    if spot < 0:
        if silent:
            return done("?", letter, None)
        return done("?", None, f"getopts: illegal option -- {letter}")
    if optstring[spot + 1:spot + 2] != ":":
        return done(letter, None, None)
    if pos > 1:  # `-fFILE`: the rest of the word is the value
        value = word[pos:]
        index, pos = index + 1, 1
        return done(letter, value, None)
    if index <= len(args):  # `-f FILE`: the next argument
        value = args[index - 1]
        index += 1
        return done(letter, value, None)
    if silent:
        return done(":", letter, None)
    return done("?", None, f"getopts: option requires an argument -- {letter}")
