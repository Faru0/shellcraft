"""Here-documents (`<<EOF`, `<<-EOF`, `<<'EOF'`) and `\\` line continuation, read before anything
else looks at a script.

`encode(text)` is the pre-pass every way of running text goes through (a file, a func, `source`,
a line typed at the prompt). A here-document's body lines are taken out of the text and carried in
the command line itself as a here-string whose word is a marker: `cat <<EOF` becomes
`cat <<<@@HDe...`. The body lines (and the delimiter line) are left as empty lines, so every
other line keeps its number and nothing later (comments, indentation, the statement parser) ever
sees the body. A line ending in an odd number of backslashes is joined with the next one the same
way, the next one left empty.

`decode(word)` reads a marker back (shellcraft.parser does, when it meets the `<<<`), and
`expand(body, ...)` is how an unquoted body expands `$x`, `${x}`, `$( … )` and `$(( … ))`.
"""

from __future__ import annotations

import base64
from typing import Callable, Mapping

from shellcraft.parser import ParseError, _closing_double_paren, comment_at

MARK = "@@HD"
BLANKS = " \t"


class Unterminated(ParseError):
    """A here-document whose delimiter line never came."""

    def __init__(self, delimiter: str, line: int):
        super().__init__(f"here-document delimited by end of input (wanted '{delimiter}')")
        self.delimiter = delimiter
        self.line = line


def marker(body: str, literal: bool) -> str:
    """The word that stands for a here-document body (`literal`: quoted delimiter, no expansion)."""
    data = base64.urlsafe_b64encode(body.encode("utf-8")).decode("ascii").rstrip("=")
    return f"{MARK}{'l' if literal else 'e'}{data}"


def decode(word: str) -> tuple[str, bool] | None:
    """(body, literal) for a marker word, None for any other word."""
    if not word.startswith(MARK) or len(word) < len(MARK) + 1 or word[len(MARK)] not in "le":
        return None
    data = word[len(MARK) + 1:]
    try:
        body = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8")
    except ValueError:
        return None
    return body, word[len(MARK)] == "l"


def expand(body: str, variables: Mapping[str, str] | None,
           substitute: Callable[[str], str] | None) -> str:
    """An unquoted body as bash expands it: `$x`, `${x}`, `$( … )` and `$(( … ))`; a backslash only
    escapes `$`, a backtick and itself, and joins a line with the next one at the end of a line.
    Quotes are plain text."""
    from shellcraft.parser import expansion_at

    out: list[str] = []
    i, n = 0, len(body)
    while i < n:
        ch = body[i]
        if ch == "\\" and i + 1 < n:
            nxt = body[i + 1]
            if nxt in "$`\\":
                out.append(nxt)
                i += 2
                continue
            if nxt == "\n":
                i += 2
                continue
        elif ch == "$":
            found = expansion_at(body, i, variables, substitute, True)
            if found is not None:
                out.append(found[0])
                i = found[1]
                continue
        out.append(ch)
        i += 1
    return "".join(out)


class _Op:
    """One `<<DELIM` found on a line: where it is, and what it asks for."""

    def __init__(self, start: int, end: int, delimiter: str, literal: bool, strip: bool):
        self.start, self.end = start, end
        self.delimiter, self.literal, self.strip = delimiter, literal, strip


def _scan(line: str, quote: str = "") -> tuple[list[_Op], bool, str]:
    """(the here-document operators on `line`, whether it ends in a continuing `\\`, the quote
    still open at its end). `quote` is the one already open when the line starts."""
    ops: list[_Op] = []
    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        if quote:
            if ch == "\\" and quote != "'":
                if i + 1 >= n:
                    return ops, True, quote  # `"a \` at the end: bash joins the next line here too
                i += 2
                continue
            if ch == quote[-1]:
                quote = ""
            i += 1
            continue
        if ch == "\\":
            if i + 1 >= n:
                return ops, True, quote
            i += 2
            continue
        if ch == "$" and line.startswith("$'", i):
            quote = "$'"
            i += 2
            continue
        if ch == "$" and line.startswith("$((", i):
            close = _closing_double_paren(line, i + 3)
            i = n if close is None else close + 2
            continue
        if ch == "(" and line.startswith("((", i):  # `(( a << 2 ))`
            close = _closing_double_paren(line, i + 2)
            if close is not None:
                i = close + 2
                continue
        if ch in "'\"":
            quote = ch
            i += 1
            continue
        if ch == "#" and comment_at(line, i):
            return ops, False, quote  # a comment never continues, and holds no here-document
        if line.startswith("<<", i) and not line.startswith("<<<", i) and (i == 0 or line[i - 1] != "<"):
            op = _operator(line, i)
            if op is not None:
                ops.append(op)
                i = op.end
                continue
            i += 2
            continue
        if line.startswith("<<<", i):
            i += 3
            continue
        i += 1
    return ops, False, quote


def _operator(line: str, i: int) -> _Op | None:
    """The `<<[-]WORD` at line[i], or None when no delimiter word follows."""
    n = len(line)
    j = i + 2
    strip = j < n and line[j] == "-"
    if strip:
        j += 1
    while j < n and line[j] in BLANKS:
        j += 1
    word: list[str] = []
    literal = False
    while j < n and line[j] not in " \t;|&<>()\n":
        c = line[j]
        if c in "'\"":
            end = line.find(c, j + 1)
            if end == -1:
                return None
            word.append(line[j + 1:end])
            literal = True
            j = end + 1
        elif c == "\\" and j + 1 < n:
            word.append(line[j + 1])
            literal = True
            j += 2
        else:
            word.append(c)
            j += 1
    delimiter = "".join(word)
    return _Op(i, j, delimiter, literal, strip) if delimiter else None


def encode(text: str, strict: bool = False, join: bool = True) -> str:
    """`text` with its here-documents moved into the command lines (see the module doc) and, with
    `join`, each `\\`-continued line joined with the next. The line count never changes.
    A delimiter that never comes ends the body at the end of the text, as bash does with a
    warning; with `strict` it raises Unterminated (the prompt then asks for more lines).
    `join` is off for one command line, where run_line joins its own `\\` lines."""
    if "<<" not in text and not (join and "\\\n" in text):
        return text
    lines = text.split("\n")
    out: list[str] = []
    quote = ""
    i = 0
    while i < len(lines):
        number = i + 1
        logical = lines[i]
        i += 1
        padding = 0
        ops, continued, end_quote = _scan(logical, "" if join else quote)
        while join and continued and i < len(lines):
            logical = logical[:-1] + lines[i]
            i += 1
            padding += 1
            ops, continued, end_quote = _scan(logical)
        quote = "" if join else end_quote  # a text over several lines may quote across them
        if not ops:
            out.append(logical)
            out.extend([""] * padding)
            continue
        markers: list[str] = []
        for op in ops:
            body: list[str] = []
            while True:
                if i >= len(lines):
                    if strict:
                        raise Unterminated(op.delimiter, number)
                    break
                raw = lines[i].rstrip("\r")
                i += 1
                padding += 1
                check = raw.lstrip("\t") if op.strip else raw
                if check == op.delimiter:
                    break
                body.append(check if op.strip else raw)
            markers.append(marker("".join(f"{b}\n" for b in body), op.literal))
        pieces: list[str] = []
        at = 0
        for op, mark in zip(ops, markers):
            pieces.append(logical[at:op.start])
            pieces.append(f"<<<{mark}")
            at = op.end
        pieces.append(logical[at:])
        out.append("".join(pieces))
        out.extend([""] * padding)
    return "\n".join(out)
