"""History expansion for the interactive shell, as in bash: `!!` and `!$`.

    !!   the whole previous command line
    !$   the previous command line's last word, exactly as it was typed (quotes included)

Expansion happens on the line as typed, before it is parsed. It is skipped inside single quotes
(like bash, it still happens inside double quotes), and a `!` followed by anything else is an
ordinary character, so `if (a != b)` and `echo hi!` are untouched. The shell prints the expanded
line before running it and saves that line in the history, not the `!!`. Only the interactive
shell expands history: MCP clients have none.
"""

from __future__ import annotations

from core.parser import ParseError

DESIGNATORS = ("!!", "!$")
_WORD_BREAKS = " \t\r\n|;<>&"


def expand(line: str, previous: str | None) -> tuple[str, bool]:
    """(expanded line, whether anything was expanded). Raises ParseError when the line uses
    `!!` / `!$` and there is no previous command, as bash says "event not found"."""
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
        out.append(ch)
        i += 1
    return "".join(out), changed


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
