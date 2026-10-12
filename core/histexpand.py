"""History expansion for the interactive shell, as in bash.

    !!        the whole previous command line
    !$        the previous command line's last word, exactly as it was typed (quotes included)
    !*        the previous command line's words after the first
    !N        command number N of `history` (!-N: N commands back)
    !prefix   the most recent command starting with `prefix`

Expansion happens on the line as typed, before it is parsed. It is skipped inside single quotes
(like bash, it still happens inside double quotes). `!!` and `!$` work anywhere; `!N`, `!*` and
`!prefix` only where a word starts, so `if (a != b)`, `echo hi!` and `[[ ! -f x ]]` are left
alone. The shell prints the expanded line before running it and saves that line in the history,
not the `!!`. Only the interactive shell expands history: MCP clients have none.
"""

from __future__ import annotations

import re

from core.parser import ParseError

DESIGNATORS = ("!!", "!$")
_WORD_BREAKS = " \t\r\n|;<>&"
_NUMBER = re.compile(r"!(-?\d+)")
_PREFIX = re.compile(r"!([A-Za-z_][A-Za-z0-9_.-]*)")


def expand(line: str, previous: str | None, history: list[str] | None = None) -> tuple[str, bool]:
    """(expanded line, whether anything was expanded). `history` is every saved command, oldest
    first (for `!N` and `!prefix`). Raises ParseError when an event is not found, as bash does."""
    if "!" not in line:
        return line, False
    out: list[str] = []
    i, quote, changed = 0, None, False
    while i < len(line):
        ch = line[i]
        if quote == "'":
            if ch == "'":
                quote = None
        elif ch == "'" and quote is None:
            quote = "'"
        elif ch == '"':
            quote = None if quote == '"' else '"'
        elif ch == "!" and line[i:i + 2] in DESIGNATORS:
            token = line[i:i + 2]
            if not previous:
                raise ParseError(f"{token}: event not found (no previous command)", i)
            if token == "!!":
                out.append(previous)
            else:
                word = last_word(previous)
                if word is None:
                    raise ParseError("!$: event not found (the previous command has no words)", i)
                out.append(word)
            i += 2
            changed = True
            continue
        elif ch == "!" and (i == 0 or line[i - 1] in _WORD_BREAKS):
            if line.startswith("!*", i):
                if not previous:
                    raise ParseError("!*: event not found (no previous command)", i)
                out.append(" ".join(_raw_words(previous)[1:]))
                i += 2
                changed = True
                continue
            match = _NUMBER.match(line, i) or _PREFIX.match(line, i)
            if match:
                out.append(_event(match.group(1), history or [], i))
                i = match.end()
                changed = True
                continue
        out.append(ch)
        i += 1
    return "".join(out), changed


def _event(designator: str, history: list[str], pos: int) -> str:
    """The history line `!N`, `!-N` or `!prefix` refers to."""
    if designator.lstrip("-").isdigit():
        number = int(designator)
        index = len(history) + number if number < 0 else number - 1
        if number == 0 or not 0 <= index < len(history):
            raise ParseError(f"!{designator}: event not found", pos)
        return history[index]
    for line in reversed(history):
        if line.startswith(designator):
            return line
    raise ParseError(f"!{designator}: event not found", pos)


def last_word(line: str) -> str | None:
    """The last word of a command line, as typed: `ls 'my dir'` → `'my dir'`, `cat a > b` → `b`."""
    words = _raw_words(line)
    return words[-1] if words else None


def _raw_words(line: str) -> list[str]:
    words: list[str] = []
    buf: list[str] = []
    quote = None
    for ch in line:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif ch in _WORD_BREAKS:
            if buf:
                words.append("".join(buf))
                buf = []
        else:
            buf.append(ch)
    if buf:
        words.append("".join(buf))
    return words
