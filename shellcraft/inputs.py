"""The input a command reads line by line: `read`, and the loops around it.

An `Input` is a piece of text with a place in it. `done < file`, `<<< "text"`, a here-document and
`cmd | while read …` all give the loop's commands one, so each `read` takes the next line, as it
does from a real stream. It is a `str` (the whole text, for a command like `cat` that reads it all),
so everything that takes a command's input takes it.
"""

from __future__ import annotations

from typing import Any

BLANKS = " \t\n"  # what `$IFS` holds until it is set


class Input(str):
    """Text read from the front: `.pos` moves forward as lines are taken."""

    pos: int

    def __new__(cls, text: str = "") -> Input:
        obj = super().__new__(cls, text)
        obj.pos = 0
        return obj

    def at_end(self) -> bool:
        return self.pos >= len(self)


def attached(stdin: Any) -> Input | None:
    """The input a command was given: an Input as it is (so its reads carry on), plain text as a
    new Input, and None when nothing was attached (the terminal is then the input)."""
    if isinstance(stdin, Input):
        return stdin
    if isinstance(stdin, str) and stdin:
        return Input(stdin)
    return None


Chars = list[tuple[str, bool]]  # a line's characters, each with "was it backslash-escaped"


def read_record(source: Input, delimiter: str = "\n", limit: int | None = None,
                raw: bool = False) -> tuple[Chars, bool]:
    """Take one record from `source`: up to `delimiter` (not kept), or `limit` characters.
    Without `raw` a backslash makes the next character plain (so it is never split on) and
    backslash + new line continues the record on the next line, as bash's `read` does.
    Returns (the characters, whether the record was ended by the delimiter or the limit; False at
    the end of the text)."""
    out: Chars = []
    n = len(source)
    while source.pos < n:
        if limit is not None and len(out) >= limit:
            return out, True
        ch = source[source.pos]
        source.pos += 1
        if ch == delimiter:
            return out, True
        if ch == "\\" and not raw and source.pos < n:
            nxt = source[source.pos]
            source.pos += 1
            if nxt != "\n":
                out.append((nxt, True))
            continue
        out.append((ch, False))
    if limit is not None and len(out) >= limit:
        return out, True
    return out, False


def text_chars(text: str, raw: bool) -> Chars:
    """A typed line as the characters `read` splits (backslashes handled unless `raw`)."""
    return read_record(Input(text), "\0", None, raw)[0]


def split_fields(chars: Chars, ifs: str, count: int) -> list[str]:
    """The `count` values of `read a b c`: the line split on `ifs`, the last value holding all
    that is left. White space in `ifs` (space, tab, new line) is dropped at the ends and runs of it
    count as one separator; any other `ifs` character separates exactly one field."""
    if count == 1 and not ifs:
        return ["".join(c for c, _ in chars)]
    white = {c for c in ifs if c in BLANKS}
    marks = set(ifs) - white
    n = len(chars)

    def is_white(k: int) -> bool:
        return chars[k][0] in white and not chars[k][1]

    def is_mark(k: int) -> bool:
        return chars[k][0] in marks and not chars[k][1]

    pos = 0
    while pos < n and is_white(pos):
        pos += 1
    fields: list[str] = []
    while len(fields) < count - 1 and pos < n:
        start = pos
        while pos < n and not is_white(pos) and not is_mark(pos):
            pos += 1
        fields.append("".join(c for c, _ in chars[start:pos]))
        while pos < n and is_white(pos):
            pos += 1
        if pos < n and is_mark(pos):
            pos += 1
            while pos < n and is_white(pos):
                pos += 1
    end = n
    while end > pos and is_white(end - 1):
        end -= 1
    if end > pos and is_mark(end - 1) and not any(is_mark(k) for k in range(pos, end - 1)):
        end -= 1  # one delimiter ending the last value only closes it: `IFS=: read a b <<< "x:y:"`
        while end > pos and is_white(end - 1):
            end -= 1
    fields += [""] * (count - 1 - len(fields))
    fields.append("".join(c for c, _ in chars[pos:end]))
    return fields
