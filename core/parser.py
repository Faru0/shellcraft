"""Command-line tokenizer and pipeline parser.

Grammar:  segment ( '|' segment )* [ ('>' | '>>') target ]
Other shell operators (`;`, `&&`, `||`, `<`, `2>`, `2>&1`, `&>`, `>&`) are rejected with a
ParseError instead of being passed on as arguments: `rm a ; ls` must never mean `rm a ';' ls`.
Quotes: '...' is literal, "..." allows \\" for a quote. Backslashes are otherwise
literal so Windows paths like C:\\data\\x.csv survive untouched.

Variables: given a `variables` mapping, `$?`, `$name` and `${name}` expand outside single quotes
(`$?` is the last exit status, `$name` a `for` loop variable). An expansion never splits into
several words, so a value with spaces stays one argument. A name that isn't defined stays as
typed (`$HOME` is not the environment variable).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Callable, Mapping

OPERATORS = ("|", ">>", ">")
# Longest first. The `2>` forms only count at the start of a word, as in POSIX shells.
UNSUPPORTED = ("2>&1", "2>>", "2>", "&>", ">&", "&&", "||", ";", "<")
_VAR_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class ParseError(Exception):
    def __init__(self, message: str, pos: int | None = None):
        super().__init__(message)
        self.message = message
        self.pos = pos


@dataclass(frozen=True)
class Token:
    value: str
    pos: int
    op: bool = False  # True for |, >, >>


@dataclass
class Command:
    name: str
    args: list[str]


@dataclass
class Redirect:
    path: str
    append: bool


@dataclass
class Pipeline:
    segments: list[Command]
    redirect: Redirect | None = None


def expand_variable(line: str, i: int, variables: Mapping[str, str] | None) -> tuple[str, int] | None:
    """At a `$` (line[i]): (value, index after the reference) for a defined variable, else None."""
    if not variables or i + 1 >= len(line):
        return None
    nxt = line[i + 1]
    if nxt == "?":
        name, end = "?", i + 2
    elif nxt == "{":
        close = line.find("}", i + 2)
        if close == -1:
            return None
        name, end = line[i + 2:close], close + 1
    else:
        match = _VAR_NAME.match(line, i + 1)
        if not match:
            return None
        name, end = match.group(), match.end()
    if name not in variables:
        return None
    return variables[name], end


def find_closing_paren(line: str, i: int) -> int | None:
    """With line[i] just after a `(`: the index of the matching `)`, skipping quoted text."""
    level, quote, n = 1, None, len(line)
    while i < n:
        ch = line[i]
        if quote:
            if ch == "\\" and quote == '"' and i + 1 < n:
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "(":
            level += 1
        elif ch == ")":
            level -= 1
            if level == 0:
                return i
        i += 1
    return None


def tokenize(line: str, variables: Mapping[str, str] | None = None, split: bool = False,
             substitute: Callable[[str], str] | None = None) -> list[Token]:
    """Split a line into words and operators.

    `split` and `substitute` give bash's behavior inside bash-style `for` / `if` blocks: an
    unquoted expansion is split into words at whitespace (quote it, "$x", to keep it one word),
    and `$(command)` is replaced by the command's output (`substitute(command)`), minus trailing
    new lines."""
    tokens: list[Token] = []
    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        if ch.isspace():
            i += 1
            continue
        bad = next((o for o in UNSUPPORTED if line.startswith(o, i)), None)
        if bad:
            raise ParseError(f"'{bad}' is not supported yet (quote it to pass it as text)", i)
        op = next((o for o in OPERATORS if line.startswith(o, i)), None)
        if op:
            tokens.append(Token(op, i, op=True))
            i += len(op)
            continue

        start = i
        buf: list[str] = []
        quoted = expanded = False

        def expansion(at: int) -> tuple[str, int] | None:
            if substitute is not None and line.startswith("$(", at):
                close = find_closing_paren(line, at + 2)
                if close is None:
                    raise ParseError("missing ')' for '$('", at)
                return substitute(line[at + 2:close]).rstrip("\n"), close + 1
            return expand_variable(line, at, variables)

        while i < n and not line[i].isspace() and not _breaks_word(line, i):
            ch = line[i]
            if ch == "$" and (var := expansion(i)) is not None:
                value, i = var
                expanded = True
                if not split or not any(c.isspace() for c in value):
                    buf.append(value)
                    continue
                pieces = value.split()
                if value[:1].isspace() and (buf or quoted):
                    tokens.append(Token("".join(buf), start))
                    buf, quoted = [], False
                for k, piece in enumerate(pieces):
                    if k:
                        tokens.append(Token("".join(buf), start))
                        buf, quoted = [], False
                    buf.append(piece)
                if value[-1:].isspace() and pieces:
                    tokens.append(Token("".join(buf), start))
                    buf, quoted, expanded = [], False, True
                continue
            if ch in ("'", '"'):
                quoted = True
                end = i + 1
                while True:
                    if end >= n:
                        raise ParseError(f"unterminated {ch} quote", i)
                    if ch == '"' and line[end] == "\\" and end + 1 < n and line[end + 1] == '"':
                        buf.append('"')
                        end += 2
                        continue
                    if line[end] == ch:
                        break
                    if ch == '"' and line[end] == "$" and (var := expansion(end)) is not None:
                        buf.append(var[0])
                        end = var[1]
                        continue
                    buf.append(line[end])
                    end += 1
                i = end + 1
            else:
                buf.append(ch)
                i += 1
        word = "".join(buf)
        if expanded and not quoted and not word:
            continue  # an unquoted expansion that came to nothing is no argument at all
        if not quoted and (word == "~" or word.startswith("~/") or word.startswith("~\\")):
            word = os.path.expanduser(word)
        tokens.append(Token(word, start))
    return tokens


def _breaks_word(line: str, i: int) -> bool:
    """True where an operator ends an unquoted word (`2>` only starts one, so `a2>b` is `a2` `>` `b`)."""
    return any(line.startswith(o, i) for o in OPERATORS + UNSUPPORTED if not o.startswith("2"))


def parse(line: str, variables: Mapping[str, str] | None = None, split: bool = False,
          substitute: Callable[[str], str] | None = None) -> Pipeline | None:
    """Parse a command line. Returns None for blank lines."""
    tokens = tokenize(line, variables, split, substitute)
    if not tokens:
        return None

    segments: list[Command] = []
    current: list[Token] = []
    redirect: Redirect | None = None
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if redirect is not None:
            raise ParseError("redirection must be the last part of a pipeline", tok.pos)
        if tok.op and tok.value == "|":
            if not current:
                raise ParseError("empty pipeline segment before '|'", tok.pos)
            segments.append(_command(current))
            current = []
        elif tok.op:  # > or >>
            if not current:
                raise ParseError(f"missing command before '{tok.value}'", tok.pos)
            if i + 1 >= len(tokens) or tokens[i + 1].op:
                raise ParseError(f"missing file name after '{tok.value}'", tok.pos)
            redirect = Redirect(tokens[i + 1].value, append=tok.value == ">>")
            i += 1
        else:
            current.append(tok)
        i += 1

    if not current:
        raise ParseError("pipeline ends with '|'", tokens[-1].pos)
    segments.append(_command(current))
    return Pipeline(segments, redirect)


def _command(tokens: list[Token]) -> Command:
    return Command(tokens[0].value, [t.value for t in tokens[1:]])
