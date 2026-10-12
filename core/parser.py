"""Command-line tokenizer and pipeline parser.

Grammar:  segment ( '|' segment )* with redirections anywhere after a segment's command word:
          '>' FILE, '>>' FILE (the pipeline's output), '<' FILE (the first command's input),
          '2>' FILE, '2>>' FILE (a failing command's error message), '2>&1' (the error message
          becomes the command's output), '&>' FILE (output and errors).
A lone `&` (background jobs) and `>&` are rejected with a ParseError instead of being passed on
as arguments: `rm a & ls` must never mean `rm a '&' ls`. `;`, `&&` and `||` are split by
core/script.py before a pipeline gets here, and are rejected here too.
Quotes: '...' is literal, "..." allows \\" for a quote. Backslashes are otherwise
literal so Windows paths like C:\\data\\x.csv survive untouched.

Variables: given a `variables` mapping, `$?`, `$name` and `${name}` expand outside single quotes
(`$?` is the last exit status, `$name` a loop or shell variable). An expansion never splits into
several words unless `split` is set (bash-style blocks), so a value with spaces stays one
argument. A name that isn't defined stays as typed. `$(( … ))` is arithmetic (core/arith.py),
`$( … )` runs a command when `substitute` is given, and with `glob` set an unquoted word with
`*`, `?` or `[` becomes the matching file names (the word itself when nothing matches).
"""

from __future__ import annotations

import glob as globmod
import os
import re
from dataclasses import dataclass, field
from typing import Callable, Mapping

from core import arith

# Longest first. The `2>` forms and a lone `&` only count at the start of a word, as in POSIX
# shells, so `a2>b` is `a2 > b` and a URL's `?a=1&b=2` stays one word.
OPERATORS = ("2>&1", "2>>", "2>", "&>", "|", ">>", ">", "<")
UNSUPPORTED = (">&", "&&", "||", ";", "&")
_WORD_START_ONLY = ("2>&1", "2>>", "2>", "&")
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
    op: bool = False  # True for an operator (OPERATORS)


@dataclass
class Command:
    name: str
    args: list[str]
    stderr: Redirect | None = None  # `2> file`: where a failure's message goes
    merge_stderr: bool = False  # `2>&1`: a failure's message becomes the command's output


@dataclass
class Redirect:
    path: str
    append: bool


@dataclass
class Pipeline:
    segments: list[Command]
    redirect: Redirect | None = None
    stdin_path: str | None = None  # `< file`
    extra: list[Token] = field(default_factory=list)  # unused; kept for compatibility


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
             substitute: Callable[[str], str] | None = None, glob: bool = False) -> list[Token]:
    """Split a line into words and operators.

    `split` and `substitute` give bash's behavior inside bash-style `for` / `if` blocks: an
    unquoted expansion is split into words at whitespace (quote it, "$x", to keep it one word),
    and `$(command)` is replaced by the command's output (`substitute(command)`), minus trailing
    new lines. `glob` expands unquoted `*.py`-style words into the matching file names."""
    tokens: list[Token] = []
    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        if ch.isspace():
            i += 1
            continue
        # The longest operator wins: `&>` is a redirection, `&&` and a lone `&` are not supported.
        bad = next((o for o in UNSUPPORTED if line.startswith(o, i)), None)
        op = next((o for o in OPERATORS if line.startswith(o, i)), None)
        if bad and (op is None or len(bad) >= len(op)):
            what = "background jobs are not supported; " if bad == "&" else ""
            raise ParseError(f"'{bad}' is not supported yet ({what}quote it to pass it as text)", i)
        if op:
            tokens.append(Token(op, i, op=True))
            i += len(op)
            continue

        start = i
        buf: list[str] = []
        quoted = expanded = False
        globbable: list[bool] = []  # per character: may it be a glob character (unquoted)?

        def expansion(at: int) -> tuple[str, int] | None:
            if line.startswith("$((", at):
                close = _closing_double_paren(line, at + 3)
                if close is None:
                    raise ParseError("missing '))' for '$(('", at)
                try:
                    return str(arith.evaluate(line[at + 3:close], variables)), close + 2
                except arith.ArithError as exc:
                    raise ParseError(f"$(( )): {exc}", at) from None
            if substitute is not None and line.startswith("$(", at):
                close = find_closing_paren(line, at + 2)
                if close is None:
                    raise ParseError("missing ')' for '$('", at)
                return substitute(line[at + 2:close]).rstrip("\n"), close + 1
            return expand_variable(line, at, variables)

        def add(text: str, may_glob: bool) -> None:
            buf.append(text)
            globbable.extend([may_glob] * len(text))

        def flush() -> None:
            nonlocal buf, quoted, globbable
            _emit(tokens, "".join(buf), start, quoted, globbable, glob)
            buf, quoted, globbable = [], False, []

        while i < n and not line[i].isspace() and not _breaks_word(line, i):
            ch = line[i]
            if ch == "$" and (var := expansion(i)) is not None:
                value, i = var
                expanded = True
                if not split or not any(c.isspace() for c in value):
                    add(value, False)
                    continue
                pieces = value.split()
                if value[:1].isspace() and (buf or quoted):
                    flush()
                for k, piece in enumerate(pieces):
                    if k:
                        flush()
                    add(piece, False)
                if value[-1:].isspace() and pieces:
                    flush()
                    expanded = True
                continue
            if ch in ("'", '"'):
                quoted = True
                end = i + 1
                while True:
                    if end >= n:
                        raise ParseError(f"unterminated {ch} quote", i)
                    if ch == '"' and line[end] == "\\" and end + 1 < n and line[end + 1] == '"':
                        add('"', False)
                        end += 2
                        continue
                    if line[end] == ch:
                        break
                    if ch == '"' and line[end] == "$" and (var := expansion(end)) is not None:
                        add(var[0], False)
                        end = var[1]
                        continue
                    add(line[end], False)
                    end += 1
                i = end + 1
            else:
                add(ch, True)
                i += 1
        word = "".join(buf)
        if expanded and not quoted and not word:
            continue  # an unquoted expansion that came to nothing is no argument at all
        if not quoted and word.startswith("~"):
            expanded_word = expand_tilde(word)
            if expanded_word != word:
                globbable = [False] * (len(expanded_word) - len(word) + 1) + globbable[1:]
                word = expanded_word
        _emit(tokens, word, start, quoted, globbable, glob)
    return tokens


def _emit(tokens: list[Token], word: str, start: int, quoted: bool, globbable: list[bool], glob: bool) -> None:
    """Append a word, expanded into file names when it is an unquoted glob pattern."""
    if glob and any(c in "*?[" and g for c, g in zip(word, globbable)):
        pattern = "".join(c if g else globmod.escape(c) for c, g in zip(word, globbable))
        matches = sorted(globmod.glob(pattern))
        if matches:
            tokens.extend(Token(m, start) for m in matches)
            return
    tokens.append(Token(word, start))


def _closing_double_paren(line: str, i: int) -> int | None:
    """With line[i] just after `$((`: the index of the `))` that closes it."""
    level = 0
    while i < len(line):
        ch = line[i]
        if ch == "(":
            level += 1
        elif ch == ")":
            if level == 0:
                return i if line.startswith("))", i) else None
            level -= 1
        i += 1
    return None


_TILDE = re.compile(r"^~([A-Za-z0-9_.-]*)(?=$|[/\\])")


def expand_tilde(word: str) -> str:
    """`~`, `~/x` and `~user/x` as bash expands them; an unknown user leaves the word as typed."""
    if not _TILDE.match(word):
        return word
    try:
        return os.path.expanduser(word)
    except (RuntimeError, KeyError):  # no home directory can be determined
        return word


def _breaks_word(line: str, i: int) -> bool:
    """True where an operator ends an unquoted word (`2>` and `&` only start one, so `a2>b` is
    `a2` `>` `b` and `a=1&b=2` is one word)."""
    return any(line.startswith(o, i) for o in OPERATORS + UNSUPPORTED if o not in _WORD_START_ONLY)


def parse(line: str, variables: Mapping[str, str] | None = None, split: bool = False,
          substitute: Callable[[str], str] | None = None, glob: bool = False,
          allow_leading_pipe: bool = False) -> Pipeline | None:
    """Parse a command line. Returns None for blank lines. With `allow_leading_pipe`, the line
    may start with `|` or a redirection (the tail of `done | sort > out`): the first segment is
    then empty and the pipeline's input is whatever the caller feeds it."""
    tokens = tokenize(line, variables, split, substitute, glob)
    if not tokens:
        return None

    segments: list[Command] = []
    current: list[Token] = []
    redirect: Redirect | None = None
    stdin_path: str | None = None
    stderr: Redirect | None = None
    merge_stderr = False
    leading = allow_leading_pipe
    i = 0

    def close_segment(pos: int) -> None:
        nonlocal current, stderr, merge_stderr
        if not current:
            raise ParseError("empty pipeline segment before '|'", pos)
        segments.append(Command(current[0].value, [t.value for t in current[1:]], stderr, merge_stderr))
        current, stderr, merge_stderr = [], None, False

    while i < len(tokens):
        tok = tokens[i]
        if tok.op and tok.value == "|":
            if leading and not current and not segments:
                segments.append(Command("", [], stderr, merge_stderr))
                stderr, merge_stderr = None, False
            else:
                close_segment(tok.pos)
            leading = False
        elif tok.op and tok.value == "2>&1":
            if not current and not (leading and not segments):
                raise ParseError("missing command before '2>&1'", tok.pos)
            merge_stderr = True
        elif tok.op:  # > >> < 2> 2>> &>
            if not current and not (leading and not segments):
                raise ParseError(f"missing command before '{tok.value}'", tok.pos)
            if i + 1 >= len(tokens) or tokens[i + 1].op:
                raise ParseError(f"missing file name after '{tok.value}'", tok.pos)
            target = tokens[i + 1].value
            if tok.value == "<":
                if segments or stdin_path is not None:
                    raise ParseError("'<' only applies to the first command of a pipeline", tok.pos)
                stdin_path = target
            elif tok.value in ("2>", "2>>"):
                stderr = Redirect(target, append=tok.value == "2>>")
            elif tok.value == "&>":
                if redirect is not None:
                    raise ParseError("a pipeline can only redirect its output once", tok.pos)
                redirect, merge_stderr = Redirect(target, append=False), True
            else:
                if redirect is not None:
                    raise ParseError("a pipeline can only redirect its output once", tok.pos)
                redirect = Redirect(target, append=tok.value == ">>")
            i += 1
        else:
            if redirect is not None and not current and not leading:
                raise ParseError("redirection must be the last part of a pipeline", tok.pos)
            current.append(tok)
        i += 1

    if not current:
        if leading and not segments and (redirect is not None or stdin_path is not None):
            segments.append(Command("", [], stderr, merge_stderr))  # `} > out`: only a redirect
        elif tokens[-1].op and tokens[-1].value == "|":
            raise ParseError("pipeline ends with '|'", tokens[-1].pos)
        elif not segments:
            raise ParseError("expected a command", tokens[0].pos)
        elif redirect is None and stdin_path is None:
            raise ParseError("pipeline ends with '|'", tokens[-1].pos)
        else:
            raise ParseError("redirection must be the last part of a pipeline", tokens[-1].pos)
    else:
        close_segment(tokens[-1].pos)
    return Pipeline(segments, redirect, stdin_path)
