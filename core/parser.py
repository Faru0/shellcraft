"""Command-line tokenizer and pipeline parser.

Grammar:  segment ( '|' segment )* [ ('>' | '>>') target ]
Quotes: '...' is literal, "..." allows \\" for a quote. Backslashes are otherwise
literal so Windows paths like C:\\data\\x.csv survive untouched.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

OPERATORS = ("|", ">>", ">")


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


def tokenize(line: str) -> list[Token]:
    tokens: list[Token] = []
    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        if ch.isspace():
            i += 1
            continue
        op = next((o for o in OPERATORS if line.startswith(o, i)), None)
        if op:
            tokens.append(Token(op, i, op=True))
            i += len(op)
            continue

        start = i
        buf: list[str] = []
        quoted = False
        while i < n and not line[i].isspace() and not any(line.startswith(o, i) for o in OPERATORS):
            ch = line[i]
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
                    buf.append(line[end])
                    end += 1
                i = end + 1
            else:
                buf.append(ch)
                i += 1
        word = "".join(buf)
        if not quoted and (word == "~" or word.startswith("~/") or word.startswith("~\\")):
            word = os.path.expanduser(word)
        tokens.append(Token(word, start))
    return tokens


def parse(line: str) -> Pipeline | None:
    """Parse a command line. Returns None for blank lines."""
    tokens = tokenize(line)
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
