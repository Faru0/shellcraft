"""Scripts: `for` loops, `if` conditions, `;`, `&&` and `||`, in two syntaxes.

Bash syntax (the default), as in bash:

    for NAME in WORD...; do LIST; done
    if LIST; then LIST; [elif LIST; then LIST;] [else LIST;] fi
    a && b || c          ! cmd          a ; b

  Inside these blocks expansion follows bash: an unquoted `$x` or `$(command)` is split into
  words at whitespace (quote it, "$x", to keep it one word), `$(…)` runs a command, and in the
  `for` list `{1..5}` / `{a,b}` brace expansion and `*.log` globs apply. Conditions are commands
  (`[ … ]`, `[[ … ]]`, `test`, `grep -q`, …): true when their status is 0.

Python syntax (`-py`), parsed by ShellCraft itself:

    for -py NAME in ITEM... { LIST }        ITEM: word, N..M[..STEP], glob, (command)
    if -py (python-expression) | COMMAND { LIST } [elif … { … }] [else { … }]

  `$x` is always one argument whatever it holds, and a `( … )` condition is a small, safe
  subset of Python, checked node by node and evaluated by walking the tree (never `eval`).
  Inside a `-py` block, nested `for` / `if` use the Python syntax too.

    select -py NAME in ITEM... { LIST }
    case -py WORD { PATTERN [| PATTERN] { LIST } … }       `_` matches anything
    def NAME(PARAM, PARAM="default") { LIST }               parameters are `local` variables

  The Python style (a `:` and an indented body) is turned into these brace forms before parsing,
  by shellcraft.indent; the parser never sees a colon block.

`{` opens a `-py` block when it starts a word; `}` closes one when it is a word of its own.
Outside any block a plain command line is parsed exactly as before (no splitting, no `$(…)`),
so `echo {a} }` still prints its arguments.
"""

from __future__ import annotations

import ast
import glob
import operator
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Union

from shellcraft import getopts, traps
from shellcraft import parser as shparser
from shellcraft.context import ShellContext, to_text
from shellcraft.parser import ParseError, find_closing_paren

KEYWORDS = ("for", "if", "elif", "else", "break", "continue", "do", "done", "then", "fi", "while", "until",
            "case", "esac", "select", "def")
SH_RESERVED = frozenset({"do", "done", "then", "fi", "elif", "else", "esac"})
PY_FLAG = "-py"
MAX_RANGE = 100_000  # items one range / brace expansion may produce
MAX_SEQUENCE = 100_000  # longest string / list a condition may build with `*`
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_FUNCTION_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
_FUNCTION_DEF = re.compile(r"([A-Za-z_][A-Za-z0-9_-]*)[ \t]*\([ \t]*\)")  # name()
_EMPTY_PARENS = re.compile(r"[ \t]*\([ \t]*\)")
MAX_FUNCTION_DEPTH = 100  # nested / recursive function calls
_RANGE = re.compile(r"^(-?\d+)\.\.(-?\d+)(?:\.\.(\d+))?$")
_BRACE_SEQ = re.compile(r"^(-?\d+)\.\.(-?\d+)(?:\.\.(-?\d+))?$|^([A-Za-z])\.\.([A-Za-z])(?:\.\.(-?\d+))?$")


class IncompleteError(ParseError):
    """The text ends inside a block, a `( … )` or before a `{` / `do` / `then`: the shell asks
    for another line. `join` goes between this text and the next line; `mode` is "py" or "sh"."""

    def __init__(self, message: str, pos: int, join: str, mode: str = "sh"):
        super().__init__(message, pos)
        self.join = join
        self.mode = mode


# ── syntax tree ──────────────────────────────────────────────────────────────

@dataclass
class Simple:
    text: str  # a pipeline, parsed again with variables when it runs
    pos: int
    mode: str = "top"  # "top" (as typed at the prompt), "sh" (bash expansion) or "py"
    negate: bool = False  # `! cmd`


@dataclass
class Chain:
    """`a && b || c`: each part runs depending on the status so far, left to right."""

    parts: list[Simple | Jump]  # a Jump only after the first: `[ -f x ] || return 1`
    ops: list[str]
    pos: int


@dataclass
class Item:
    kind: str  # "word" or "cmd" (-py), "sh" (a bash word, expanded the bash way)
    text: str
    pos: int


@dataclass
class For:
    var: str
    items: list[Item]
    body: list[Stmt]
    pos: int
    mode: str = "sh"


@dataclass
class Cond:
    kind: str  # "expr" (Python), "cmd" (a -py pipeline's status) or "list" (bash: a statement list)
    text: str
    pos: int
    negate: bool = False
    tree: ast.Expression | None = None
    stmts: list[Stmt] = field(default_factory=list)


@dataclass
class If:
    branches: list[tuple[Cond, list[Stmt]]]
    orelse: list[Stmt] | None
    pos: int
    mode: str = "sh"


@dataclass
class Jump:
    kind: str  # "break", "continue" or "return"
    pos: int
    value: str = ""  # `return N`: the status, as typed (expanded when it runs)


@dataclass
class Select:
    """`select NAME in WORD...; do LIST; done`: a numbered menu, read until `break`."""

    var: str
    items: list[Item]
    body: list[Stmt]
    pos: int


@dataclass
class Function:
    """`name() { LIST; }` or `function name { LIST; }`: defines a shell function when it runs."""

    name: str
    body: list[Stmt]
    text: str  # the definition as typed (for `declare -f` and `type`)
    pos: int
    params: list[tuple[str, str | None]] | None = None  # `def f(a, b="x")`: (name, default)


@dataclass
class While:
    """`while LIST; do LIST; done` (or `until`, which loops while the condition fails)."""

    cond: Cond
    body: list[Stmt]
    pos: int
    until: bool = False
    mode: str = "sh"


@dataclass
class Case:
    """`case WORD in PATTERN) LIST ;; … esac`: the first clause whose pattern matches runs."""

    word: str
    clauses: list[tuple[list[str], list[Stmt]]]
    pos: int


@dataclass
class Piped:
    """A loop, if or case whose whole output goes on through a pipeline: `done | sort > out`.
    `tail` is the text after the block, starting with `|` or a redirection."""

    stmt: Stmt
    tail: str
    pos: int
    mode: str = "sh"


Stmt = Union[Simple, Chain, For, If, Jump, While, Case, Piped, Function, Select]


@dataclass
class Program:
    stmts: list[Stmt] = field(default_factory=list)

    @property
    def is_simple(self) -> bool:
        """One plain pipeline: run exactly as a command line always was."""
        return (len(self.stmts) == 1 and isinstance(self.stmts[0], Simple)
                and self.stmts[0].mode == "top" and not self.stmts[0].negate)


# ── parser ───────────────────────────────────────────────────────────────────

def parse_script(text: str) -> Program | None:
    """Parse a command line or script; None when it holds no statement. Raises ParseError
    (IncompleteError when more input could complete it). Colon blocks (`for -py x in a b:` and an
    indented body) are turned into the brace form first (shellcraft.indent)."""
    from shellcraft import indent

    if indent.has_colon_block(text):
        text = indent.translate(text)
    stmts, _ = _Parser(text).parse_list(None)
    program = Program(stmts)
    return program if program.stmts else None


def _incomplete_error(text: str) -> IncompleteError | None:
    try:
        parse_script(text)
    except IncompleteError as exc:
        return exc
    except ParseError:
        return None
    return None


def _ends_parameter(text: str, i: int) -> bool:
    """Whether the `}` at `i` closes a `${…}` expansion, not a block."""
    opened = text.rfind("${", 0, i)
    return opened >= 0 and "}" not in text[opened:i]


def incomplete(text: str) -> str | None:
    """The joiner for a continuation line when `text` is an unfinished script, else None.
    Comments don't count: `for x in a b; do  # loop` is unfinished, `echo { # note` isn't."""
    from shellcraft.parser import strip_comments

    exc = _incomplete_error(strip_comments(text))
    return None if exc is None else exc.join


def join_continuation(text: str, more: str) -> str:
    """`text` (unfinished) and the next line as one line, with `;` only where one is needed:
    none after `do`, `then`, `else`, `{`, `;`, `&&`, `||`, `|` or before a -py `}` / `else`.
    Comments are dropped first: joined into one line, a comment would swallow what follows it."""
    from shellcraft.parser import strip_comments

    text, more = strip_comments(text), strip_comments(more)
    exc = _incomplete_error(text)
    join = " ; " if exc is None else exc.join
    head, more = text.rstrip(), more.strip()
    if not more:
        return head
    last = head.split()[-1] if head.split() else ""
    first = more.split()[0]
    if join.strip() and (head.endswith((";", "{", "&&", "||", "|")) or last in ("do", "then", "else")
                         or (exc is not None and exc.mode == "py" and first in ("}", "else", "elif"))):
        join = " "
    return head + join + more


class _Parser:
    def __init__(self, text: str, mode: str = "top"):
        self.s = text
        self.n = len(text)
        self.i = 0
        self.depth = 0  # open -py blocks
        self.loops = 0  # enclosing for loops (for break / continue)
        self.functions = 0  # enclosing function bodies (for return)
        self.mode = mode  # "top", "sh" or "py": the syntax of the block being parsed

    # helpers
    def _error(self, message: str, pos: int | None = None) -> ParseError:
        return ParseError(message, self.i if pos is None else pos)

    def _incomplete(self, message: str, join: str) -> IncompleteError:
        return IncompleteError(message, self.n, join, "py" if self.mode == "py" else "sh")

    def _at_end(self) -> bool:
        return self.i >= self.n

    def _skip_blanks(self) -> None:
        while self.i < self.n and self.s[self.i] in " \t\r":
            self.i += 1

    def _skip_space(self, separators: bool = False) -> None:
        stop = " \t\r\n;" if separators else " \t\r\n"
        while self.i < self.n and self.s[self.i] in stop:
            self.i += 1

    def _word_start(self, i: int) -> bool:
        return i == 0 or self.s[i - 1] in " \t\r\n;{"

    def _is_open(self, i: int) -> bool:
        """A `{` that opens a block: it starts a word and is followed by a blank, `}` or the end."""
        return (i < self.n and self.s[i] == "{" and self._word_start(i)
                and (i + 1 >= self.n or self.s[i + 1] in " \t\r\n}"))

    def _is_close(self, i: int) -> bool:
        """A `}` that closes a block: a word of its own (or followed by `;` / another `}`)."""
        return (i < self.n and self.s[i] == "}" and self._word_start(i)
                and (i + 1 >= self.n or self.s[i + 1] in " \t\r\n;}"))

    def _bare_word(self, i: int) -> str:
        j = i
        while j < self.n and self.s[j] not in " \t\r\n;":
            j += 1
        return self.s[i:j]

    def _in_mode(self, mode: str, fn: Callable[[], Any]) -> Any:
        saved, self.mode = self.mode, mode
        try:
            return fn()
        finally:
            self.mode = saved

    # statement lists
    def parse_list(self, end: str | frozenset[str] | None) -> tuple[list[Stmt], str | None]:
        """Statements up to `end`: None (the end of the text), "}" (a -py block) or a set of bash
        keywords (`done`, `then`, `fi`…). Returns them and the word that ended the list."""
        stmts: list[Stmt] = []
        while True:
            self._skip_space()
            if isinstance(end, frozenset) and ";;" in end and self.s.startswith(";;", self.i):
                self.i += 2
                return stmts, ";;"
            if self.i < self.n and self.s[self.i] == ";":
                self.i += 1
                continue
            if self._at_end():
                if end is None:
                    return stmts, None
                if end == "}":
                    last = len(self.s.rstrip()) - 1
                    if self.s[last] == "}" and not self._is_close(last) and not _ends_parameter(self.s, last):
                        # `{break}`: meant to close (a line ending in `${1:-x}` is only unfinished)
                        raise self._error("missing '}' — a closing brace must be a word of its own, "
                                          "with a space before it: { echo $x }", last)
                    raise self._incomplete("missing '}'", " ; ")
                wanted = "fi" if "fi" in end else sorted(end)[0]
                raise self._incomplete(f"missing '{wanted}'", " ; ")
            if end == "}" and self._is_close(self.i):
                self.i += 1
                return stmts, "}"
            word = self._bare_word(self.i)
            if isinstance(end, frozenset) and word in end:
                self.i += len(word)
                return stmts, word
            stmts.append(self._statement())
            self._skip_blanks()
            if self._at_end() or self.s[self.i] in ";\n" or (end == "}" and self._is_close(self.i)):
                continue
            word = self._bare_word(self.i)
            raise self._error(f"expected ';' or a new line before '{word}'")

    def _statement(self) -> Stmt:
        start = self.i
        word = self._bare_word(start)
        if word in ("for", "if", "while", "until", "case", "select"):
            stmt: Stmt
            if word == "for":
                stmt = self._for()
            elif word == "select":
                stmt = self._select()
            elif word == "if":
                stmt = self._if()
            elif word == "case":
                stmt = self._case()
            else:
                stmt = self._while(until=word == "until")
            return self._maybe_piped(stmt)
        if word in ("break", "continue", "return"):
            return self._jump(word)
        if word == "def":
            return self._def()
        if word == "function" or (self.mode != "py" and _FUNCTION_DEF.match(self.s, start)):
            return self._function(keyword=word == "function")
        if word in ("elif", "else") and self.mode == "py":
            raise self._error(f"'{word}' without a matching if (put it on the same line as the "
                              "if's closing '}')", start)
        if word in SH_RESERVED:
            owner = "for" if word in ("do", "done") else "if"
            raise self._error(f"'{word}' without a matching {owner}", start)
        return self._chain()

    def _jump(self, word: str) -> Jump:
        """`break`, `continue` or `return [N]`, alone or after `&&` / `||` (`[ -f x ] || return 1`)."""
        start = self.i
        if word in ("break", "continue"):
            if not self.loops:
                raise self._error(f"'{word}' outside a for loop", start)
            self.i += len(word)
            return Jump(word, start)
        if not self.functions:
            raise self._error("'return' outside a function", start)
        self.i += len(word)
        self._skip_blanks()
        value = ""
        if (not self._at_end() and self.s[self.i] not in ";\n" and not self._is_close(self.i)
                and not self.s.startswith(("&&", "||"), self.i)):
            value = self._word()
        return Jump("return", start, value)

    def _maybe_piped(self, stmt: Stmt) -> Stmt:
        """After `done`, `fi`, `esac` or `}`: a `| …` or `> file` tail pipes the block's output."""
        self._skip_blanks()
        i = self.i
        starts_tail = (i < self.n and self.s[i] in "|><" and not self.s.startswith("||", i)) or \
            self.s.startswith("2>", i) or self.s.startswith("&>", i)
        if not starts_tail:
            return stmt
        start = self.i
        tail = self._tail_text()
        try:
            shparser.parse(tail, substitute=lambda _: "", allow_leading_pipe=True)
        except ParseError as exc:
            raise ParseError(exc.message, None if exc.pos is None else start + exc.pos) from None
        return Piped(stmt, tail, start, self.mode)

    def _tail_text(self) -> str:
        """The text up to the end of the statement (`;`, a new line, `&&`, `||`, a closing `}`)."""
        start, quote = self.i, None
        while self.i < self.n:
            ch = self.s[self.i]
            if quote:
                if quote == '"' and self.s.startswith('\\"', self.i):
                    self.i += 2
                    continue
                if ch == quote:
                    quote = None
                self.i += 1
                continue
            if ch in ("'", '"'):
                quote = ch
            elif ch in ";\n" or self.s.startswith("&&", self.i) or self.s.startswith("||", self.i):
                break
            elif self.depth and self._is_close(self.i):
                break
            self.i += 1
        return self.s[start:self.i].rstrip()

    # select
    def _select(self) -> Select:
        """`select NAME in WORD...; do LIST; done`: the list is read like a bash `for` list.
        `select -py NAME in ITEM... { LIST }`: like a `for -py` list, with a -py block."""
        pos = self.i
        self.i += len("select")
        py = self._py_flag()
        self._skip_space()
        match = _NAME.match(self.s, self.i)
        if not match:
            if self._at_end():
                raise self._incomplete("select: missing a variable name", " ")
            raise self._error("select: expected a variable name (letters, digits and _)")
        var = match.group()
        self.i = match.end()
        self._skip_space()
        if self._at_end():
            raise self._incomplete("select: missing 'in'", " ")
        if self._bare_word(self.i) != "in":
            raise self._error(f"select: give the choices: select {var} in WORD...; do …; done")
        self.i += 2
        loop = self._for_py(var, pos, "select") if py else self._for_sh(var, pos)
        return Select(var, loop.items, loop.body, pos)

    # functions
    def _function(self, keyword: bool) -> Function:
        """`name() { LIST; }`, `function name { LIST; }` or `function name() { LIST; }`. The body
        is bash syntax; a function body may define functions and use `return` and `local`."""
        pos = self.i
        if keyword:
            self.i += len("function")
            self._skip_blanks()
            match = _FUNCTION_NAME.match(self.s, self.i)
            if not match:
                if self._at_end():
                    raise self._incomplete("function: missing a name", " ")
                raise self._error("function: expected a name (letters, digits, _ and -)")
            name = match.group()
            self.i = match.end()
            parens = _EMPTY_PARENS.match(self.s, self.i)
            if parens:
                self.i = parens.end()
        else:
            match = _FUNCTION_DEF.match(self.s, self.i)
            assert match is not None
            name = match.group(1)
            self.i = match.end()
        if name in KEYWORDS or name in ("function", "return", "select", "in"):
            raise self._error(f"'{name}' is a shell keyword and can't be a function name", pos)
        self._skip_space()
        if self._at_end():
            raise self._incomplete(f"{name}: missing '{{'", " ")
        if self.s[self.i] != "{" or (self.i + 1 < self.n and self.s[self.i + 1] not in " \t\r\n}"):
            raise self._error(f"{name}: a function body goes in braces: {name}() {{ echo hi; }}")
        self.i += 1
        saved_loops, self.loops = self.loops, 0  # break / continue can't leave the function
        self.depth += 1
        self.functions += 1
        try:
            body, _ = self._in_mode("sh", lambda: self.parse_list("}"))
        finally:
            self.depth -= 1
            self.functions -= 1
            self.loops = saved_loops
        return Function(name, body, self.s[pos:self.i], pos)

    def _def(self) -> Function:
        """`def NAME(PARAMS) { LIST }`: a function in the Python style. Each parameter is a
        `local` variable set from `$1`, `$2`… or its default; the body is a -py block."""
        pos = self.i
        self.i += len("def")
        self._skip_blanks()
        match = _FUNCTION_NAME.match(self.s, self.i)
        if not match:
            if self._at_end():
                raise self._incomplete("def: missing a name", " ")
            raise self._error("def: expected a name: def greet(name): …")
        name = match.group()
        self.i = match.end()
        if name in KEYWORDS or name in ("function", "return", "select", "in", "def"):
            raise self._error(f"'{name}' is a shell keyword and can't be a function name", pos)
        self._skip_blanks()
        if self._at_end():
            raise self._incomplete(f"def {name}: missing '('", " ")
        if self.s[self.i] != "(":
            raise self._error(f"def {name}: expected '(' and the parameters: def {name}(a, b=\"x\"): …")
        text, start = self._paren()
        params = _parse_params(name, text, start)
        self._skip_space()
        if self._at_end():
            raise self._incomplete(f"def {name}: missing '{{'", " ")
        if not self._is_open(self.i):
            raise self._error(f"def {name}: expected ':' (or '{{') after the parameters")
        saved_loops, self.loops = self.loops, 0
        self.functions += 1
        try:
            body = self._in_mode("py", lambda: self._block(f"def {name}"))
        finally:
            self.functions -= 1
            self.loops = saved_loops
        return Function(name, body, self.s[pos:self.i], pos, params)

    # while / until
    def _while(self, until: bool) -> While:
        pos = self.i
        self.i += 5
        word = "until" if until else "while"
        if self._py_flag():
            cond = self._in_mode("py", lambda: self._cond(word))
            self.loops += 1
            try:
                body = self._in_mode("py", lambda: self._block(word))
            finally:
                self.loops -= 1
            return While(cond, body, pos, until, "py")
        self._skip_space()
        start = self.i
        if self._at_end():
            raise self._incomplete(f"{word}: missing a condition", " ")
        try:
            stmts, _ = self._in_mode("sh", lambda: self.parse_list(frozenset({"do"})))
        except IncompleteError:
            rest = self.s[start:].rstrip()
            if rest.endswith("{") or " { " in rest:
                raise self._error(f"{word}: bash syntax is `{word} …; do …; done`; for braces use "
                                  f"`{word} -py … {{ … }}`", start) from None
            raise
        if not stmts:
            raise self._error(f"{word}: missing a condition before 'do'", start)
        cond = Cond("list", self.s[start:self.i], start, stmts=stmts)
        self.loops += 1
        try:
            body, _ = self._in_mode("sh", lambda: self.parse_list(frozenset({"done"})))
        finally:
            self.loops -= 1
        return While(cond, body, pos, until, "sh")

    # case
    def _case(self) -> Case:
        pos = self.i
        self.i += 4
        self._skip_blanks()
        flagged = self._bare_word(self.i) == PY_FLAG
        if flagged:
            self.i += len(PY_FLAG)
        self._skip_space()
        if self._at_end():
            raise self._incomplete("case: missing a word", " ")
        word = self._word()
        if not word:
            raise self._error("case: expected a word after 'case'")
        self._skip_space()
        if self._at_end():
            raise self._incomplete("case: missing '{'" if flagged else "case: missing 'in'", " ")
        if (flagged or self.mode == "py") and self._is_open(self.i):
            return self._case_py(word, pos)
        if flagged:
            raise self._error(f"case -py: expected '{{' after '{word}': case -py $x {{ \"a\" {{ … }} _ {{ … }} }}")
        if self._bare_word(self.i) != "in":
            raise self._error(f"case: expected 'in' after '{word}'")
        self.i += 2
        clauses: list[tuple[list[str], list[Stmt]]] = []
        while True:
            self._skip_space(separators=True)
            if self._at_end():
                raise self._incomplete("case: missing 'esac'", " ; ")
            if self._bare_word(self.i) == "esac":
                self.i += 4
                return Case(word, clauses, pos)
            start = self.i
            if self.s[self.i] == "(":
                self.i += 1
            close = self._pattern_end()
            if close is None:
                raise self._incomplete("case: missing ')' after the pattern", " ")
            patterns = [p.strip() for p in self.s[self.i:close].split("|")]
            if not all(patterns):
                raise self._error("case: empty pattern", start)
            self.i = close + 1
            body, _ = self._in_mode("sh", lambda: self.parse_list(frozenset({";;", "esac"})))
            clauses.append((patterns, body))
            if self.s[self.i - 4:self.i] == "esac":
                return Case(word, clauses, pos)

    def _case_py(self, word: str, pos: int) -> Case:
        """`case -py WORD { PATTERN [| PATTERN] { LIST } … }`: `_` matches anything, a quoted
        pattern may still hold `*`, `?` and `[…]`; the first matching choice runs."""
        self.i += 1
        self.depth += 1
        clauses: list[tuple[list[str], list[Stmt]]] = []
        try:
            while True:
                self._skip_space(separators=True)
                if self._at_end():
                    raise self._incomplete("case: missing '}'", " ; ")
                if self._is_close(self.i):
                    self.i += 1
                    return Case(word, clauses, pos)
                start, quote, j = self.i, None, self.i
                while j < self.n:
                    ch = self.s[j]
                    if quote:
                        if ch == quote:
                            quote = None
                    elif ch in ("'", '"'):
                        quote = ch
                    elif self._is_open(j) or ch in ";\n":
                        break
                    j += 1
                if j >= self.n:
                    raise self._incomplete("case: missing '{' after the pattern", " ")
                if not self._is_open(j):
                    raise self._error("case -py: expected '{' after the pattern: \"a\" | \"b\" { … }", start)
                patterns = [_py_pattern(p.strip()) for p in _split_bars(self.s[start:j])]
                if not all(patterns):
                    raise self._error("case: empty pattern", start)
                self.i = j
                body = self._in_mode("py", lambda: self._block("case"))
                clauses.append((patterns, body))
        finally:
            self.depth -= 1

    def _pattern_end(self) -> int | None:
        """The index of the `)` that ends a case pattern (quotes respected)."""
        quote, j = None, self.i
        while j < self.n:
            ch = self.s[j]
            if quote:
                if ch == quote:
                    quote = None
            elif ch in ("'", '"'):
                quote = ch
            elif ch == ")":
                return j
            elif ch in ";\n":
                return None
            j += 1
        return None

    def _chain(self) -> Stmt:
        start = self.i
        parts: list[Simple | Jump] = [self._pipeline()]
        ops: list[str] = []
        while True:
            self._skip_blanks()
            op = self.s[self.i:self.i + 2]
            if op not in ("&&", "||"):
                break
            self.i += 2
            self._skip_space()
            if self._at_end():
                raise self._incomplete(f"missing a command after '{op}'", " ")
            ops.append(op)
            word = self._bare_word(self.i)
            parts.append(self._jump(word) if word in ("break", "continue", "return") else self._pipeline())
        return parts[0] if not ops else Chain(parts, ops, start)

    def _pipeline(self) -> Simple:
        negate = False
        if self.s.startswith("!", self.i) and self.i + 1 < self.n and self.s[self.i + 1] in " \t":
            negate = True
            self.i += 1
            self._skip_blanks()
        simple = self._simple(stop_at_open=False)
        simple.negate = negate
        return simple

    def _simple(self, stop_at_open: bool) -> Simple:
        start, quote = self.i, None
        brackets = 0  # open `[[ … ]]`: their `&&` / `||` belong to the test, not to the shell
        while self.i < self.n:
            ch = self.s[self.i]
            if not quote and self._word_start(self.i):
                end = self.i + 2
                if self.s.startswith("[[", self.i) and (end >= self.n or self.s[end] in " \t"):
                    brackets += 1
                elif brackets and self.s.startswith("]]", self.i) and (end >= self.n or self.s[end] in " \t;&|"):
                    brackets -= 1
            if quote:
                if quote == '"' and ch == "\\" and self.s.startswith('\\"', self.i):
                    self.i += 2
                    continue
                if quote == '"' and self.mode == "sh" and self.s.startswith("$(", self.i):
                    self._skip_substitution()
                    continue
                if ch == quote:
                    quote = None
                self.i += 1
                continue
            if ch in ("'", '"'):
                quote = ch
            elif self.mode == "sh" and self.s.startswith("$(", self.i):
                self._skip_substitution()
                continue
            elif ch in ";\n" or (not brackets and (self.s.startswith("&&", self.i) or self.s.startswith("||", self.i))):
                break
            elif self.depth and self._is_close(self.i):
                break
            elif stop_at_open and self._is_open(self.i):
                break
            self.i += 1
        text = self.s[start:self.i].rstrip()
        if not text:
            raise self._error("expected a command", start)
        _check_pipeline(text, start, self.mode)
        return Simple(text, start, self.mode)

    def _skip_substitution(self) -> None:
        """At `$(` in bash mode: check the command inside and move past its `)`."""
        close = find_closing_paren(self.s, self.i + 2)
        if close is None:
            raise self._incomplete("missing ')'", " ")
        inner_start = self.i + 2
        try:
            _Parser(self.s[inner_start:close], "sh").parse_list(None)
        except ParseError as exc:
            raise ParseError(exc.message, None if exc.pos is None else inner_start + exc.pos) from None
        self.i = close + 1

    def _paren(self) -> tuple[str, int]:
        """At `(` or `$(`: the text inside the matching `)` and where it starts."""
        self.i += 2 if self.s[self.i] == "$" else 1
        start = self.i
        close = find_closing_paren(self.s, start)
        if close is None:
            raise self._incomplete("missing ')'", " ")
        self.i = close + 1
        return self.s[start:close], start

    def _word(self) -> str:
        """One word as typed (quotes and `$( … )` kept whole), up to a blank or `;`."""
        start, quote = self.i, None
        while self.i < self.n:
            ch = self.s[self.i]
            if quote:
                if quote == '"' and self.s.startswith('\\"', self.i):
                    self.i += 2
                    continue
                if ch == quote:
                    quote = None
            elif ch in ("'", '"'):
                quote = ch
            elif self.s.startswith("$(", self.i):
                close = find_closing_paren(self.s, self.i + 2)
                if close is None:
                    raise self._incomplete("missing ')'", " ")
                self.i = close + 1
                continue
            elif ch in " \t\r\n;":
                break
            self.i += 1
        return self.s[start:self.i]

    def _py_flag(self) -> bool:
        """Consume `-py` after for / if; True for the Python syntax. Inside a -py block it is
        inherited, unless the header is bash's (a `do` or `then` before any `{`)."""
        self._skip_blanks()
        if self._bare_word(self.i) == PY_FLAG:
            self.i += len(PY_FLAG)
            return True
        return self.mode == "py" and not self._bash_header()

    def _bash_header(self) -> bool:
        """Whether the header from here on is bash's: `do` or `then` as a word before a block `{`."""
        j, quote = self.i, None
        while j < self.n:
            ch = self.s[j]
            if quote:
                if ch == quote:
                    quote = None
            elif ch in ("'", '"'):
                quote = ch
            elif self._is_open(j):
                return False
            elif self._word_start(j) and self._bare_word(j) in ("do", "then"):
                return True
            j += 1
        return False

    # for
    def _for(self) -> For:
        pos = self.i
        self.i += 3
        py = self._py_flag()
        self._skip_space()
        match = _NAME.match(self.s, self.i)
        if not match:
            if self._at_end():
                raise self._incomplete("for: missing a variable name", " ")
            raise self._error("for: expected a variable name (letters, digits and _)")
        var = match.group()
        self.i = match.end()
        self._skip_blanks()
        if not py and (self._at_end() or self.s[self.i] in ";\n" or self._bare_word(self.i) == "do"):
            raise self._error(f"for: give the list to loop over: for {var} in WORD...; do …; done "
                              "(looping over the script's arguments isn't supported)")
        self._skip_space()
        if self._at_end():
            raise self._incomplete("for: missing 'in'", " ")
        if self._bare_word(self.i) != "in":
            raise self._error(f"for: expected 'in' after '{var}'")
        self.i += 2
        return self._for_py(var, pos) if py else self._for_sh(var, pos)

    def _for_sh(self, var: str, pos: int) -> For:
        items: list[Item] = []
        while True:
            self._skip_blanks()
            if self._at_end():
                raise self._incomplete("for: missing '; do'", " ; ")
            if self.s[self.i] in ";\n":
                self.i += 1
                break
            start = self.i
            word = self._word()
            if word == "{":
                raise self._error("for: bash syntax is `for x in …; do …; done`; for braces use "
                                  "`for -py x in … { … }`", start)
            if word == "do":
                raise self._error("for: put ';' or a new line before 'do'", start)
            try:
                tokens = shparser.tokenize(word, substitute=lambda _: "")
            except ParseError as exc:
                raise ParseError(f"for: {exc.message}", start + (exc.pos or 0)) from None
            if any(t.op for t in tokens):
                raise self._error("for: an item can't contain '|' or '>' — loop over a command's "
                                  "output with $(…): for x in $(cat file); do …; done", start)
            _check_substitutions(word, start)
            items.append(Item("sh", word, start))
        self._skip_space(separators=True)
        if self._at_end():
            raise self._incomplete("for: missing 'do'", " ")
        if self._bare_word(self.i) != "do":
            word = self._bare_word(self.i)
            raise self._error(f"for: expected 'do', got '{word}'")
        self.i += 2
        self.loops += 1
        try:
            body, _ = self._in_mode("sh", lambda: self.parse_list(frozenset({"done"})))
        finally:
            self.loops -= 1
        return For(var, items, body, pos, "sh")

    def _for_py(self, var: str, pos: int, what: str = "for") -> For:
        items: list[Item] = []
        while True:
            self._skip_space()
            if self._at_end():
                raise self._incomplete(f"{what}: missing '{{'", " ")
            ch = self.s[self.i]
            if ch == "{" and self._word_start(self.i):
                break
            if ch == ";":
                raise self._error("for -py: expected '{' after the items (no ';' before it)")
            if ch == "(" or self.s.startswith("$(", self.i):
                text, start = self._paren()
                _check_pipeline(text, start, "py")
                items.append(Item("cmd", text, start))
                continue
            start = self.i
            word = self._word()
            try:
                tokens = shparser.tokenize(word)
            except ParseError as exc:
                raise ParseError(f"for: {exc.message}", start + (exc.pos or 0)) from None
            if any(t.op for t in tokens):
                raise self._error("for: an item can't contain '|' or '>' — to loop over a command's "
                                  "output, put it in parentheses: for -py x in (cat file) { … }", start)
            items.append(Item("word", word, start))
        self.loops += 1
        try:
            body = self._in_mode("py", lambda: self._block(what))
        finally:
            self.loops -= 1
        return For(var, items, body, pos, "py")

    def _block(self, what: str) -> list[Stmt]:
        self._skip_space()
        if self._at_end():
            raise self._incomplete(f"{what}: missing '{{'", " ")
        if self.s[self.i] != "{":
            raise self._error(f"{what}: expected '{{'")
        self.i += 1
        self.depth += 1
        try:
            return self.parse_list("}")[0]
        finally:
            self.depth -= 1

    # if
    def _if(self) -> If:
        pos = self.i
        self.i += 2
        return self._in_mode("py", lambda: self._if_py(pos)) if self._py_flag() else self._if_sh(pos)

    def _if_sh(self, pos: int) -> If:
        def condition(what: str) -> Cond:
            self._skip_space()
            start = self.i
            if self.s.startswith("(", self.i):
                raise self._error(f"{what}: a ( … ) condition is Python: use `if -py ( … ) {{ … }}`, "
                                  "or a bash test: if [ … ]; then … fi")
            try:
                stmts, _ = self._in_mode("sh", lambda: self.parse_list(frozenset({"then"})))
            except IncompleteError:
                rest = self.s[start:].rstrip()
                if rest.endswith("{") or " { " in rest:
                    raise self._error(f"{what}: bash syntax is `if …; then …; fi`; for braces use "
                                      "`if -py … { … }`", start) from None
                raise
            if not stmts:
                raise self._error(f"{what}: missing a condition before 'then'", start)
            return Cond("list", self.s[start:self.i], start, stmts=stmts)

        def body() -> tuple[list[Stmt], str | None]:
            return self._in_mode("sh", lambda: self.parse_list(frozenset({"elif", "else", "fi"})))

        branches = []
        cond = condition("if")
        stmts, end = body()
        branches.append((cond, stmts))
        while end == "elif":
            cond = condition("elif")
            stmts, end = body()
            branches.append((cond, stmts))
        orelse = None
        if end == "else":
            orelse, _ = self._in_mode("sh", lambda: self.parse_list(frozenset({"fi"})))
        return If(branches, orelse, pos, "sh")

    def _if_py(self, pos: int) -> If:
        branches = [(self._cond("if"), self._block("if"))]
        orelse = None
        while True:
            save = self.i
            self._skip_space(separators=True)
            word = self._bare_word(self.i) if not self._at_end() else ""
            if word == "elif":
                self.i += 4
                branches.append((self._cond("elif"), self._block("elif")))
            elif word == "else":
                self.i += 4
                orelse = self._block("else")
                break
            else:
                self.i = save
                break
        return If(branches, orelse, pos, "py")

    def _cond(self, what: str) -> Cond:
        self._skip_space()
        if self._at_end():
            raise self._incomplete(f"{what}: missing a condition", " ")
        negate = False
        if self.s[self.i] == "!" and self.i + 1 < self.n and self.s[self.i + 1] in " \t":
            negate = True
            self.i += 1
            self._skip_space()
        if self.i < self.n and self.s[self.i] == "(":
            text, start = self._paren()
            return Cond("expr", text, start, negate, compile_condition(text, start))
        if self._is_open(self.i):
            raise self._error(f"{what}: missing a condition")
        simple = self._simple(stop_at_open=True)
        return Cond("cmd", simple.text, simple.pos, negate)


def _parse_params(name: str, text: str, pos: int) -> list[tuple[str, str | None]]:
    """`a, b="x", n=3` → [("a", None), ("b", "x"), ("n", "3")]: plain names, and defaults that
    are text, numbers, True, False or None."""
    try:
        tree = ast.parse(f"def f({text}): pass")
    except SyntaxError as exc:
        raise ParseError(f"def {name}: invalid parameters ({exc.msg}): ({text})", pos) from None
    fn = tree.body[0]
    assert isinstance(fn, ast.FunctionDef)
    args = fn.args
    if args.vararg or args.kwarg or args.kwonlyargs or args.posonlyargs:
        raise ParseError(f"def {name}: only plain parameters are allowed: def {name}(a, b=\"x\")"
                         " ($@ still has every argument)", pos)
    defaults: list[str | None] = [None] * (len(args.args) - len(args.defaults))
    for node in args.defaults:
        if not isinstance(node, ast.Constant) or not isinstance(node.value, (str, int, float, bool, type(None))):
            raise ParseError(f"def {name}: a default must be text or a number", pos)
        value = node.value
        defaults.append("" if value is None else str(value))
    return [(arg.arg, default) for arg, default in zip(args.args, defaults)]


def _py_pattern(text: str) -> str:
    """A `case -py` pattern as `run_case` reads it: `_` is anything, and a quoted pattern keeps
    its `*`, `?` and `[…]` (`"*.py"` matches every .py name)."""
    if text == "_":
        return "*"
    if len(text) >= 2 and text[0] in ("'", '"') and text[-1] == text[0] and text[0] not in text[1:-1]:
        return text[1:-1] if text[1:-1] else '""'
    return text


def _split_bars(text: str) -> list[str]:
    """`"a" | b|c` → the parts between `|` outside quotes."""
    parts: list[str] = []
    quote, start = None, 0
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "|":
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return parts


def _check_substitutions(word: str, offset: int) -> None:
    """Syntax-check every `$( … )` in a bash word before anything runs."""
    i = word.find("$(")
    while i != -1:
        close = find_closing_paren(word, i + 2)
        if close is None:
            return
        try:
            _Parser(word[i + 2:close], "sh").parse_list(None)
        except ParseError as exc:
            raise ParseError(exc.message, None if exc.pos is None else offset + i + 2 + exc.pos) from None
        i = word.find("$(", close + 1)


def _check_pipeline(text: str, offset: int, mode: str = "top") -> None:
    """Syntax-check a pipeline before anything runs, with the error position in the whole line."""
    if mode in ("top", "sh") and shparser.arith.is_assignment(text) and "|" not in text and ">" not in text:
        return  # `x=1`: an assignment, not a command
    try:
        shparser.parse(text, substitute=(lambda _: "") if mode in ("sh", "top") else None)
    except ParseError as exc:
        raise ParseError(exc.message, None if exc.pos is None else offset + exc.pos) from None


# ── bash word expansion (for lists) ──────────────────────────────────────────

def brace_expand(word: str) -> list[str]:
    """Bash brace expansion of one word: `a{b,c}d` → abd acd, `{1..3}` → 1 2 3, `{01..03}`,
    `{a..e}`, `{0..10..5}`. Quoted braces and `${…}` are left alone; nesting works."""
    i, quote, n = 0, None, len(word)
    while i < n:
        ch = word[i]
        if quote:
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "{" and (i == 0 or word[i - 1] != "$"):
            close, parts = _brace_group(word, i)
            if close is not None:
                prefix, suffix = word[:i], word[close + 1:]
                inner = word[i + 1:close]
                if parts is None:
                    alternatives = _brace_sequence(inner)
                    if alternatives is None:
                        rest = brace_expand(word[i + 1:])
                        return [word[:i + 1] + r for r in rest]
                else:
                    alternatives = [a for p in parts for a in brace_expand(p)]
                out: list[str] = []
                for alt in alternatives:
                    out += brace_expand(prefix + alt + suffix)
                    if len(out) > MAX_RANGE:
                        raise ValueError(f"brace expansion makes more than {MAX_RANGE} words")
                return out
        i += 1
    return [word]


def _brace_group(word: str, i: int) -> tuple[int | None, list[str] | None]:
    """At `{`: (index of the matching `}`, the comma-separated parts or None when there's no comma)."""
    level, quote, parts, last = 0, None, [], i + 1
    for j in range(i, len(word)):
        ch = word[j]
        if quote:
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "{":
            level += 1
        elif ch == "}":
            level -= 1
            if level == 0:
                if not parts:
                    return j, None
                parts.append(word[last:j])
                return j, parts
        elif ch == "," and level == 1:
            parts.append(word[last:j])
            last = j + 1
    return None, None


def _brace_sequence(inner: str) -> list[str] | None:
    match = _BRACE_SEQ.match(inner)
    if not match:
        return None
    if match[1] is not None:
        a, b, step = int(match[1]), int(match[2]), abs(int(match[3] or 1)) or 1
        if abs(b - a) // step + 1 > MAX_RANGE:
            raise ValueError(f"{{{inner}}} makes more than {MAX_RANGE} words")
        width = max(len(match[1].lstrip("-")), len(match[2].lstrip("-")))
        padded = (match[1].lstrip("-").startswith("0") and len(match[1].lstrip("-")) > 1) or \
                 (match[2].lstrip("-").startswith("0") and len(match[2].lstrip("-")) > 1)
        nums = range(a, b + 1, step) if b >= a else range(a, b - 1, -step)
        return [f"{x:0{width + (1 if x < 0 else 0)}d}" if padded else str(x) for x in nums]
    a, b, step = ord(match[4]), ord(match[5]), abs(int(match[6] or 1)) or 1
    chars = range(a, b + 1, step) if b >= a else range(a, b - 1, -step)
    return [chr(c) for c in chars]


def expand_sh_word(raw: str, variables: dict[str, str], substitute: Callable[[str], str]) -> list[str]:
    """A `for` list word as bash expands it: braces, then `$x` / `$(…)` (split at whitespace
    when unquoted), quote removal, then globbing of unquoted `*?[` (the word itself when
    nothing matches)."""
    out: list[str] = []
    for word in brace_expand(raw):
        out += _expand_fields(word, variables, substitute)
    return out


def _expand_fields(word: str, variables: dict[str, str], substitute: Callable[[str], str]) -> list[str]:
    fields: list[tuple[list[tuple[str, bool]], bool]] = [([], False)]  # (chars + quoted flag, keep)

    def add(text: str, quoted: bool) -> None:
        chars, keep = fields[-1]
        chars.extend((c, quoted) for c in text)
        fields[-1] = (chars, keep or quoted)

    def new_field() -> None:
        fields.append(([], False))

    def add_split(value: str) -> None:
        pieces = value.split()
        if value[:1].isspace() and fields[-1][0]:
            new_field()
        for k, piece in enumerate(pieces):
            if k:
                new_field()
            add(piece, False)
        if value[-1:].isspace() and pieces:
            new_field()

    def expansion(at: int) -> tuple[str, int] | None:
        if word.startswith("$(", at):
            close = find_closing_paren(word, at + 2)
            if close is not None:
                return substitute(word[at + 2:close]).rstrip("\n"), close + 1
            return None
        return shparser.expand_variable(word, at, variables)

    i, n = 0, len(word)
    if word == "~" or word.startswith("~/"):
        add(os.path.expanduser("~"), False)
        i = 1
    while i < n:
        ch = word[i]
        if ch == "'":
            end = word.find("'", i + 1)
            end = n if end == -1 else end
            fields[-1] = (fields[-1][0], True)
            add(word[i + 1:end], True)
            i = end + 1
        elif ch == '"':
            fields[-1] = (fields[-1][0], True)
            i += 1
            while i < n and word[i] != '"':
                if word.startswith('\\"', i):
                    add('"', True)
                    i += 2
                elif word[i] == "$" and (at := shparser.quoted_arguments(word, i, variables)) is not None:
                    args, i = at  # "$@": one field per argument
                    if not args and not fields[-1][0]:
                        fields[-1] = (fields[-1][0], False)  # no arguments: no field, unless other text joins it
                    for k, arg in enumerate(args):
                        if k:
                            new_field()
                            fields[-1] = (fields[-1][0], True)
                        add(arg, True)
                elif word[i] == "$" and (var := expansion(i)) is not None:
                    add(var[0], True)
                    i = var[1]
                else:
                    add(word[i], True)
                    i += 1
            i += 1
        elif ch == "$" and (var := expansion(i)) is not None:
            add_split(var[0])
            i = var[1]
        else:
            add(ch, False)
            i += 1

    out: list[str] = []
    for chars, keep in fields:
        if not chars and not keep:
            continue
        text = "".join(c for c, _ in chars)
        if any(c in "*?[" and not q for c, q in chars):
            pattern = "".join(c if not q else glob.escape(c) for c, q in chars)
            matches = sorted(glob.glob(pattern))
            if matches:
                out += matches
                continue
        out.append(text)
    return out


# ── Python conditions ────────────────────────────────────────────────────────

FUNCTIONS: dict[str, Any] = {
    "len": len, "int": int, "float": float, "str": str, "bool": bool, "abs": abs, "min": min, "max": max,
    "match": lambda pattern, text: re.search(pattern, text) is not None,
    "exists": os.path.exists, "isfile": os.path.isfile, "isdir": os.path.isdir,
}
STR_METHODS = frozenset({
    "startswith", "endswith", "lower", "upper", "casefold", "title", "strip", "lstrip", "rstrip",
    "split", "rsplit", "splitlines", "removeprefix", "removesuffix", "replace", "count", "find", "rfind",
    "isdigit", "isnumeric", "isdecimal", "isalpha", "isalnum", "isspace", "islower", "isupper",
})
_BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
           ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod}
_CMPOPS = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le,
           ast.Gt: operator.gt, ast.GtE: operator.ge, ast.In: lambda a, b: a in b,
           ast.NotIn: lambda a, b: a not in b, ast.Is: operator.is_, ast.IsNot: operator.is_not}
_PLAIN_NODES = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub, ast.UAdd,
                ast.BinOp, ast.Compare, ast.Name, ast.Load, ast.Tuple, ast.List, ast.Set, ast.Subscript,
                ast.Slice, ast.IfExp, *_BINOPS, *_CMPOPS)


class ConditionError(Exception):
    pass


def _dollars_to_names(text: str) -> str:
    """`$?` → status and `$name` / `${name}` → name outside string literals, so a condition can be
    written either way: (ip == "1.1.1.1") or ($ip == "1.1.1.1")."""
    out, i, quote = [], 0, None
    while i < len(text):
        ch = text[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < len(text):
                out.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            out.append(ch)
        elif ch == "$" and text.startswith("$?", i):
            out.append("status")
            i += 2
            continue
        elif ch == "$" and text.startswith("${", i) and (close := text.find("}", i)) != -1:
            out.append(text[i + 2:close])
            i = close + 1
            continue
        elif ch == "$" and (match := _NAME.match(text, i + 1)):
            out.append(match.group())
            i = match.end()
            continue
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def compile_condition(text: str, pos: int = 0) -> ast.Expression:
    """Parse and vet a `( … )` condition; raises ParseError naming what isn't allowed."""
    source = _dollars_to_names(text).strip()
    if not source:
        raise ParseError("if: empty condition '()'", pos)
    try:
        tree = ast.parse(source.replace("\n", " "), mode="eval")
    except SyntaxError as exc:
        raise ParseError(f"if: invalid condition ({exc.msg}): {source}", pos) from None
    for node in ast.walk(tree):
        problem = _vet(node)
        if problem:
            raise ParseError(f"if: {problem} in condition: {source}", pos)
    return tree


def _vet(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant):
        ok = node.value is None or isinstance(node.value, (str, int, float, bool))
        return None if ok else f"constant {node.value!r} not allowed"
    if isinstance(node, ast.Call):
        if node.keywords or any(isinstance(a, ast.Starred) for a in node.args):
            return "keyword or * arguments are not allowed"
        if isinstance(node.func, ast.Name):
            return None if node.func.id in FUNCTIONS else (
                f"function '{node.func.id}' is not available (use: {', '.join(sorted(FUNCTIONS))})")
        if isinstance(node.func, ast.Attribute):
            return None if node.func.attr in STR_METHODS else f"method '.{node.func.attr}()' is not available"
        return "only plain function and string-method calls are allowed"
    if isinstance(node, ast.Attribute):
        return None if node.attr in STR_METHODS else f"attribute '.{node.attr}' is not available"
    if isinstance(node, ast.Name) and node.id.startswith("_"):
        return f"name '{node.id}' is not allowed"
    if not isinstance(node, _PLAIN_NODES):
        return f"'{type(node).__name__}' is not allowed"
    return None


def evaluate(tree: ast.Expression, names: dict[str, Any]) -> Any:
    try:
        return _eval(tree.body, names)
    except ConditionError:
        raise
    except TypeError as exc:
        hint = " (loop variables are text: use int(name) to compare numbers)" if "'str' and 'int'" in str(exc) \
            or "'int' and 'str'" in str(exc) else ""
        raise ConditionError(f"{exc}{hint}") from None
    except (ValueError, ZeroDivisionError, IndexError, KeyError, re.error, OverflowError) as exc:
        raise ConditionError(f"{type(exc).__name__}: {exc}") from None


def _eval(node: ast.AST, names: dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in names:
            return names[node.id]
        known = ", ".join(sorted(names)) or "none"
        raise ConditionError(f"unknown name '{node.id}' (variables here: {known}; quote text: \"{node.id}\")")
    if isinstance(node, ast.BoolOp):
        value: Any = None
        for item in node.values:
            value = _eval(item, names)
            if isinstance(node.op, ast.And) and not value:
                return value
            if isinstance(node.op, ast.Or) and value:
                return value
        return value
    if isinstance(node, ast.UnaryOp):
        value = _eval(node.operand, names)
        if isinstance(node.op, ast.Not):
            return not value
        return -value if isinstance(node.op, ast.USub) else +value
    if isinstance(node, ast.BinOp):
        left, right = _eval(node.left, names), _eval(node.right, names)
        if isinstance(node.op, ast.Mult):
            for seq, count in ((left, right), (right, left)):
                if isinstance(seq, (str, list, tuple)) and isinstance(count, int) and len(seq) * count > MAX_SEQUENCE:
                    raise ConditionError(f"'*' would build more than {MAX_SEQUENCE} items")
        return _BINOPS[type(node.op)](left, right)
    if isinstance(node, ast.Compare):
        left = _eval(node.left, names)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval(comparator, names)
            if not _CMPOPS[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.IfExp):
        return _eval(node.body, names) if _eval(node.test, names) else _eval(node.orelse, names)
    if isinstance(node, (ast.Tuple, ast.List)):
        values = [_eval(e, names) for e in node.elts]
        return tuple(values) if isinstance(node, ast.Tuple) else values
    if isinstance(node, ast.Set):
        return {_eval(e, names) for e in node.elts}
    if isinstance(node, ast.Subscript):
        return _eval(node.value, names)[_eval(node.slice, names)]
    if isinstance(node, ast.Slice):
        return slice(*(None if part is None else _eval(part, names) for part in (node.lower, node.upper, node.step)))
    if isinstance(node, ast.Call):
        args = [_eval(a, names) for a in node.args]
        if isinstance(node.func, ast.Name):
            return FUNCTIONS[node.func.id](*args)
        target = _eval(node.func.value, names)
        if not isinstance(target, str):
            raise ConditionError(f"'.{node.func.attr}()' works on text, not on {type(target).__name__}")
        return getattr(target, node.func.attr)(*args)
    raise ConditionError(f"'{type(node).__name__}' is not allowed")  # vetted already; defensive


# ── execution ────────────────────────────────────────────────────────────────

class _Break(Exception):
    pass


class _Continue(Exception):
    pass


class _Return(Exception):
    """`return [N]` in a function body: N, or None for the status of the last command."""

    def __init__(self, status: int | None):
        super().__init__(status)
        self.status = status


class _Abort(Exception):
    """Stops the whole script (a bad condition would fail the same way on every iteration).
    `reported`: the error was already shown (a command that failed under `set -e`). `stops`: a
    sourced file stops too (`set -e`, `set -u`), not just the line."""

    def __init__(self, error: Exception, text: str, reported: bool = False, stops: bool = False):
        super().__init__(str(error))
        self.error = error
        self.text = text
        self.reported = reported
        self.stops = stops


def _errexit_stop(status: int, text: str) -> _Abort:
    """What `set -e` raises when a command fails: the command has already said why."""
    from shellcraft.pipeline import PipelineError

    return _Abort(PipelineError(1, 1, "set -e", f"stopped at: {text.strip()}", status), text,
                  reported=True, stops=True)


@contextmanager
def _no_errexit(ctx: ShellContext) -> Iterator[None]:
    """`set -e` doesn't apply in here: a test, the left of `&&` / `||`, a `!` command."""
    ctx.errexit_off += 1
    try:
        yield
    finally:
        ctx.errexit_off -= 1


def execute(program: Program, ctx: ShellContext) -> Any:
    """Run a parsed script. Each statement's output goes to ctx.emit as it is produced, or is
    collected into the result's text when there is no emit (MCP, tests). A failing statement is
    reported and the script goes on, as in a POSIX shell (with `set -e` it stops there, and the
    result says so: a sourced file stops too); `$?` holds its status."""
    from shellcraft.pipeline import PipelineError, PipelineResult

    runner = _Runner(ctx)
    stopped = False
    try:
        runner.run_list(program.stmts)
    except _Abort as exc:
        ctx.last_status = exc.error.status if isinstance(exc.error, PipelineError) else 1
        if not exc.reported:
            runner.report(exc.error, exc.text)
        stopped = exc.stops
    output = None if ctx.emit else "".join(runner.collected)
    return PipelineResult(output=output, status=ctx.last_status, stopped=stopped)


def run_trap(ctx: ShellContext, command: str, runner: _Runner | None = None) -> str:
    """Run a trap's command (shellcraft.traps) in the current context. Its output goes through
    `runner` when given (inside a function: into what the function prints); otherwise it is shown
    as it runs (ctx.emit) or returned. An error in it is reported, and nothing stops because of it."""
    try:
        program = parse_script(command)
    except ParseError as exc:
        (runner or _Runner(ctx)).report(exc, command)
        return ""
    if program is None:
        return ""
    sub = _Runner(ctx)
    if runner is not None:
        sub.frame, sub.stdin = runner.frame, runner.stdin
        sub.collect_only = not runner.shows_live
    try:
        sub.run_list(program.stmts)
    except (_Break, _Continue, _Return):
        pass
    except _Abort as exc:
        if not exc.reported:
            sub.report(exc.error, exc.text)
    text = "".join(sub.collected)
    if runner is not None:
        if text:
            runner.show(text)
        return ""
    return text


def call_function(fn: Function, args: list[str], stdin: str, ctx: ShellContext, cmd: Any = None,
                  live: bool = False) -> Any:
    """Run a shell function as a command: `$1`… are `args`, its commands read `stdin`, and what
    it prints is its output (so `fn | grep x` and `fn > file` work). Variables are the shell's
    (as in bash) except those declared `local`, which are put back when it returns. Its status
    is `return N`'s, else the last command's. With `cmd` (the call as parsed), its `2> file` /
    `2>&1` also take what the body sends to the error stream (`>&2`, failing commands). With
    `live` (a call on its own line, on the main thread) each command's output is shown as it is
    made, through ctx.emit, and the function returns no output of its own."""
    from shellcraft.context import CommandError, WithStatus
    from shellcraft.pipeline import stderr_file_sink

    if not ctx.allow_aliases:
        raise CommandError(f"{fn.name}: shell functions are not available here (MCP)")
    if len(ctx.positional) >= MAX_FUNCTION_DEPTH:
        raise CommandError(f"{fn.name}: functions nested more than {MAX_FUNCTION_DEPTH} deep "
                           "(a recursion that never stops?)")
    runner = _Runner(ctx)
    runner.collect_only = not live
    runner.frame = {}
    runner.stdin = stdin
    if cmd is not None and cmd.stderr is not None:
        runner.stderr_sink = stderr_file_sink(cmd.stderr)
    elif cmd is not None and cmd.merge_stderr:
        runner.stderr_sink = runner.collected.append  # `fn 2>&1 | grep`: errors join the output
    if runner.stderr_sink is not None:
        ctx.stderr_sinks.append(runner.stderr_sink)
    if fn.params is not None:
        missing = [p for k, (p, default) in enumerate(fn.params) if k >= len(args) and default is None]
        if missing:
            raise CommandError(f"{fn.name}: missing argument '{missing[0]}' "
                               f"(usage: {fn.name} {' '.join(p.upper() for p, _ in fn.params)})", 2)
    with getopts.fresh(ctx):  # OPTIND=1 for its options; the caller's back after the `local`s
        ctx.positional.append(list(args))
        status: int | None = None
        try:
            with traps.frame(ctx, runner):  # its own traps; EXIT runs here, before `local`s go
                try:
                    for k, (param, default) in enumerate(fn.params or []):
                        runner.frame.setdefault(param, ctx.variables.get(param))
                        ctx.variables[param] = args[k] if k < len(args) else (default or "")
                    runner.run_list(fn.body)
                except _Return as exc:
                    status = exc.status
                except _Abort as exc:
                    if not exc.reported:
                        runner.report(exc.error, exc.text)
                    status = getattr(exc.error, "status", 1) or 1
                except KeyboardInterrupt as exc:  # Ctrl-C: the INT trap, then the call stops as before
                    traps.interrupted(ctx, exc, runner)
                    raise
                finally:
                    ctx.last_status = ctx.last_status if status is None else status  # `$?` in the EXIT trap
        finally:
            ctx.positional.pop()
            if runner.stderr_sink is not None:
                ctx.stderr_sinks.pop()
            for name, old in runner.frame.items():
                if old is None:
                    ctx.variables.pop(name, None)
                else:
                    ctx.variables[name] = old
    if status is None:
        status = ctx.last_status
    ctx.last_status = status
    output = "".join(runner.collected)
    return WithStatus(output, status) if status else output


class _Runner:
    def __init__(self, ctx: ShellContext, parent: _Runner | None = None):
        self.ctx = ctx
        self.parent = parent  # set for `$( … )`: output is captured, errors go to the parent
        # The top-level runner writes variables straight to the shell (`x=1` and loop variables
        # outlive the line, as in bash); a `$( … )` runs with a copy, as a subshell would.
        self.vars: dict[str, str] = dict(parent.vars) if parent else ctx.variables
        self.collected: list[str] = []
        # In a function call: the values `local` replaced (None: the variable did not exist),
        # put back when the call ends; and the function's input, which its commands read.
        self.frame: dict[str, str | None] | None = parent.frame if parent else None
        self.stdin = parent.stdin if parent else ""
        # A function call's or block's `2> file` / `2>&1`: where its errors are reported.
        self.stderr_sink: Callable[[str], None] | None = None

    def variables(self) -> Any:
        from shellcraft.pipeline import Variables

        return Variables(self.ctx, self.vars)

    collect_only = False  # a piped block: output is collected even at the top level

    @property
    def shows_live(self) -> bool:
        """Whether a statement's output may stream to the screen as it is made (shellcraft.streams):
        only where show() prints, never inside `$( … )` or a piped block, which collect."""
        return self.ctx.emit is not None and self.parent is None and not self.collect_only

    def show(self, value: Any) -> None:
        if self.ctx.emit is not None and self.parent is None and not self.collect_only:
            self.ctx.emit(value)
            return
        text = to_text(value)
        if text:
            self.collected.append(text if text.endswith("\n") else text + "\n")

    def report(self, exc: Exception, text: str) -> None:
        from shellcraft.pipeline import PipelineError

        sink = self.stderr_sink
        if sink is None and self.parent is None and self.ctx.stderr_sinks:
            sink = self.ctx.stderr_sinks[-1]  # inside a call or block whose errors are redirected
        if sink is not None:  # `2> file` / `2>&1`
            if isinstance(exc, PipelineError):
                message = exc.message[len(exc.name) + 2:] if exc.message.startswith(f"{exc.name}: ") else exc.message
                sink(f"{exc.name}: {message}\n")
            else:
                sink(f"{getattr(exc, 'message', exc)}\n")
            return
        if self.parent is not None:
            self.parent.report(exc, text)
        elif self.ctx.emit_error is not None:
            self.ctx.emit_error(exc, text)
        elif isinstance(exc, PipelineError):
            message = exc.message
            if message.startswith(f"{exc.name}: "):
                message = message[len(exc.name) + 2:]  # ModuleError messages already name the command
            self.collected.append(f"✗ {exc.name}: {message}\n")
        elif isinstance(exc, ParseError):
            self.collected.append(f"✗ parse error: {exc.message}\n")
        else:
            self.collected.append(f"✗ {exc}\n")

    def substitute(self, command: str) -> str:
        """`$(command)`: run it (a whole bash list) and return its output as text."""
        sub = _Runner(self.ctx, parent=self)
        stmts, _ = _Parser(command, "sh").parse_list(None)
        try:
            sub.run_list(stmts)
        except (_Break, _Continue):
            pass
        except _Abort as exc:  # `set -e` stops the substitution's commands, not the line around it
            if not exc.reported:
                raise
        return "".join(sub.collected)

    def run_list(self, stmts: list[Stmt]) -> None:
        for stmt in stmts:
            try:
                self.run_stmt(stmt)
            except shparser.UnboundVariable as exc:  # `set -u`, in a `for` list or a `case` word
                from shellcraft.pipeline import UnboundError

                self.ctx.last_status = 1
                raise _Abort(UnboundError(1, 1, exc.name, str(exc)), getattr(stmt, "text", ""),
                             stops=True) from None

    def run_stmt(self, stmt: Stmt) -> None:
        traps.new_command(self.ctx)
        if isinstance(stmt, Simple):
            if stmt.negate:  # `! cmd`: never stopped by `set -e`
                with _no_errexit(self.ctx):
                    _, output = self.run_simple(stmt)
            else:
                status, output = self.run_simple(stmt)
            if output is not None:
                self.show(output)
            if not stmt.negate:
                self.check_errexit(status, stmt.text)
        elif isinstance(stmt, Chain):
            self.run_chain(stmt)
        elif isinstance(stmt, For):
            self.run_for(stmt)
        elif isinstance(stmt, If):
            self.run_if(stmt)
        elif isinstance(stmt, While):
            self.run_while(stmt)
        elif isinstance(stmt, Case):
            self.run_case(stmt)
        elif isinstance(stmt, Piped):
            self.run_piped(stmt)
        elif isinstance(stmt, Function):
            self.define(stmt)
        elif isinstance(stmt, Select):
            self.run_select(stmt)
        elif stmt.kind == "break":
            raise _Break
        elif stmt.kind == "return":
            raise _Return(self.return_status(stmt))
        else:
            raise _Continue

    def check_errexit(self, status: int, text: str) -> None:
        """A command that failed (and isn't a test, the left of `&&` / `||` or `!`) runs the ERR
        trap; with `set -e` it then stops the script, the function or the file it is in."""
        from shellcraft.pipeline import errexit

        traps.failed(self.ctx, status, self)
        if status and errexit(self.ctx):
            raise _errexit_stop(status, text)

    def define(self, stmt: Function) -> None:
        """Run a function definition: keep it for the session (never over MCP)."""
        from shellcraft.pipeline import PipelineError

        if not self.ctx.allow_stateful or not self.ctx.allow_aliases:
            self.report(PipelineError(1, 1, stmt.name, "shell functions can't be defined here (MCP)"), stmt.text)
            self.ctx.last_status = 1
            return
        self.ctx.functions[stmt.name] = stmt
        self.ctx.last_status = 0

    def return_status(self, stmt: Jump) -> int | None:
        if not stmt.value:
            return None
        words = [t.value for t in shparser.tokenize(stmt.value, self.variables())]
        try:
            return int(words[0]) & 0xFF if words else None
        except ValueError:
            return 2  # bash: "numeric argument required"

    def run_local(self, stmt: Simple) -> tuple[int, Any]:
        """`local NAME[=VALUE]...` in a function body: the variables get these values for the
        rest of the call and are put back (or removed) when it returns."""
        from shellcraft.pipeline import PipelineError

        rest = stmt.text.strip()[len("local"):]
        if self.frame is None:
            self.report(PipelineError(1, 1, "local", "local: can only be used in a function"), stmt.text)
            self.ctx.last_status = 1
            return 1, None
        try:
            tokens = shparser.tokenize(rest, self.variables(), substitute=self.substitute)
        except shparser.UnboundVariable:
            raise  # `set -u`: run_list stops the script
        except ParseError as exc:
            self.report(exc, stmt.text)
            self.ctx.last_status = 2
            return 2, None
        for token in tokens:
            name, sep, value = token.value.partition("=")
            if not _NAME.fullmatch(name):
                self.report(PipelineError(1, 1, "local", f"local: '{name}': not a valid identifier"), stmt.text)
                self.ctx.last_status = 1
                return 1, None
            if name not in self.frame:
                self.frame[name] = self.ctx.variables.get(name)
            self.ctx.variables[name] = value if sep else ""
        self.ctx.last_status = 0
        return 0, None

    def run_simple(self, stmt: Simple | str, show_errors: bool = True) -> tuple[int, Any]:
        """Run one pipeline; returns (status, output). Bash-mode statements split unquoted
        expansions into words and run `$( … )` (not inside `[[ … ]]`, as in bash)."""
        from shellcraft.pipeline import PipelineError, UnboundError, _run_parsed, assign

        if isinstance(stmt, str):
            stmt = Simple(stmt, 0, "py")
        sh = stmt.mode == "sh"
        expands = stmt.mode in ("sh", "top")
        if stmt.text.split(None, 1)[:1] == ["local"]:
            return self.run_local(stmt)
        if "input(" in stmt.text:
            asked = self.run_input(stmt)
            if asked is not None:
                return asked, None
        try:
            if assign(stmt.text, self.ctx, self.variables(), self.substitute if expands else None, target=self.vars):
                self.ctx.last_status = 0
                return 0, None
            result = _run_parsed(stmt.text, self.ctx, self.variables(),
                                 split=sh and not stmt.text.lstrip().startswith("[["),
                                 substitute=self.substitute if expands else None, glob=expands,
                                 stdin=self.stdin, live=self.shows_live)
        except (ParseError, PipelineError) as exc:
            if show_errors:
                self.report(exc, stmt.text)
            if isinstance(exc, UnboundError):  # `set -u`: the script stops here
                raise _Abort(exc, stmt.text, reported=show_errors, stops=True) from None
            result = None
        output = None if result is None else result.output
        if stmt.negate:
            self.ctx.last_status = 0 if self.ctx.last_status else 1
        return self.ctx.last_status, output

    def run_input(self, stmt: Simple) -> int | None:
        """`name = input("…")` (or `int(input(…))`, `float(input(…))`) inside a block or a
        function, as on a line of its own; None when the line is something else."""
        from shellcraft import ask
        from shellcraft.pipeline import PipelineError

        def say(message: str) -> None:
            if self.ctx.ui is not None:
                from rich.text import Text

                self.ctx.ui.console.print(Text(message, style="sc.warning"), markup=False, highlight=False)

        try:
            asking = ask.parse_ask_line(stmt.text)
            if asking is None:
                return None
            name, prompt, kind = asking
            prompt = shparser._expand_simple(prompt, self.variables())  # input("Age of $who? ")
            self.vars[name] = ask.ask_as(self.ctx, prompt, kind, say)
        except ask.AskError as exc:
            self.report(PipelineError(1, 1, "input", str(exc), exc.status), stmt.text)
            self.ctx.last_status = exc.status
            return exc.status
        self.ctx.last_status = 0
        return 0

    def run_chain(self, stmt: Chain) -> None:
        """`a && b || c`. Under `set -e` only the last part's failure stops the script, and only
        when it ran: `false && x` goes on, `x && false` stops."""
        first = stmt.parts[0]
        assert isinstance(first, Simple)  # a Jump only ever follows && / ||
        last = len(stmt.parts) - 1
        status = self.run_part(first, last == 0)
        for k, (op, part) in enumerate(zip(stmt.ops, stmt.parts[1:]), 1):
            if (op == "&&") == (status == 0):
                if isinstance(part, Jump):
                    self.run_list([part])  # raises: break, continue or return
                    return
                status = self.run_part(part, k == last)

    def run_part(self, part: Simple, last: bool) -> int:
        """One part of an `&&` / `||` chain; `set -e` applies to the last one only."""
        traps.new_command(self.ctx)
        if last and not part.negate:
            status, output = self.run_simple(part)
        else:
            with _no_errexit(self.ctx):
                status, output = self.run_simple(part)
        if output is not None:
            self.show(output)
        if last and not part.negate:
            self.check_errexit(status, part.text)
        return status

    def run_for(self, stmt: For) -> None:
        values = self.items(stmt)
        if not values:
            self.ctx.last_status = 0
        for value in values:
            self.vars[stmt.var] = value
            try:
                self.run_list(stmt.body)
            except _Break:
                break
            except _Continue:
                continue

    def items(self, stmt: For) -> list[str]:
        from shellcraft.pipeline import PipelineError

        values: list[str] = []
        for item in stmt.items:
            if item.kind == "sh":
                try:
                    values += expand_sh_word(item.text, self.variables(), self.substitute)
                except ValueError as exc:
                    raise _Abort(PipelineError(1, 1, "for", str(exc)), item.text) from None
                continue
            if item.kind == "cmd":
                status, output = self.run_simple(item.text)
                if status == 0 and output is not None:
                    values += [line.strip() for line in to_text(output).splitlines() if line.strip()]
                continue
            quoted = "'" in item.text or '"' in item.text
            for token in shparser.tokenize(item.text, self.variables()):
                value = token.value
                if not quoted and (match := _RANGE.match(value)):
                    start, stop, step = int(match[1]), int(match[2]), int(match[3] or 1)
                    if step == 0:
                        raise _Abort(PipelineError(1, 1, "for", f"range {value}: step must be > 0"), item.text)
                    if abs(stop - start) // step + 1 > MAX_RANGE:
                        raise _Abort(PipelineError(1, 1, "for", f"range {value}: more than {MAX_RANGE} items"),
                                     item.text)
                    direction = 1 if stop >= start else -1
                    values += [str(n) for n in range(start, stop + direction, step * direction)]
                elif not quoted and any(c in value for c in "*?[") and (found := sorted(glob.glob(value))):
                    values += found
                else:
                    values.append(value)
        return values

    def run_select(self, stmt: Select) -> None:
        """Show the numbered choices, ask (`$PS3`, default `#? `), set NAME to the chosen word
        (empty for an invalid answer) and `$REPLY` to what was typed, run the body; again until
        `break`, Ctrl-D or Ctrl-C. An empty answer shows the menu again. Interactive only."""
        from rich.text import Text

        from shellcraft import ask
        from shellcraft.pipeline import PipelineError

        values = self.items(For(stmt.var, stmt.items, stmt.body, stmt.pos))
        if not values:
            self.ctx.last_status = 0
            return
        if not self.ctx.interactive or self.ctx.ui is None:
            raise _Abort(PipelineError(1, 1, "select", "select needs the interactive shell (a terminal to ask on)"),
                         "select")
        prompt = self.vars.get("PS3") or self.ctx.variables.get("PS3") or "#? "
        width = len(str(len(values)))
        menu = Text("\n".join(f"{n:>{width}}) {value}" for n, value in enumerate(values, 1)))
        show_menu = True
        status = 0
        while True:
            if show_menu:
                self.ctx.ui.console.print(menu, markup=False, highlight=False)
                show_menu = False
            try:
                reply = ask.ask(self.ctx, prompt)
            except ask.AskError as exc:
                status = 130 if exc.status == 130 else status  # Ctrl-D ends it quietly, as in bash
                break
            self.vars["REPLY"] = reply
            answer = reply.strip()
            if not answer:
                show_menu = True
                continue
            number = int(answer) if answer.isdigit() else 0
            self.vars[stmt.var] = values[number - 1] if 1 <= number <= len(values) else ""
            try:
                self.run_list(stmt.body)
            except _Break:
                status = self.ctx.last_status
                break
            except _Continue:
                pass
            status = self.ctx.last_status
        self.ctx.last_status = status

    def run_while(self, stmt: While) -> None:
        status = 0  # as in bash: the status of the last body command, or 0 when none ran
        while self.test(stmt.cond) != stmt.until:
            try:
                self.run_list(stmt.body)
            except _Break:
                status = self.ctx.last_status
                break
            except _Continue:
                status = self.ctx.last_status
                continue
            status = self.ctx.last_status
        self.ctx.last_status = status

    def run_case(self, stmt: Case) -> None:
        import fnmatch

        word = " ".join(expand_sh_word(stmt.word, self.variables(), self.substitute))
        for patterns, body in stmt.clauses:
            for pattern in patterns:
                expanded = "".join(_unquote(p) for p in [pattern]) if "$" not in pattern else \
                    " ".join(expand_sh_word(pattern, self.variables(), self.substitute))
                if fnmatch.fnmatchcase(word, expanded):
                    self.run_list(body)
                    return
        self.ctx.last_status = 0

    def run_piped(self, stmt: Piped) -> None:
        """Run the block with its output collected, then feed that to the tail pipeline."""
        from shellcraft.pipeline import PipelineError, UnboundError, _run_parsed

        inner = _Runner(self.ctx, parent=self)
        inner.vars = self.vars  # the same scope: `for … done | sort` is not a subshell for variables
        inner.collect_only = True
        inner.stderr_sink = self._block_stderr(stmt, inner)
        if inner.stderr_sink is not None:
            self.ctx.stderr_sinks.append(inner.stderr_sink)
        try:
            inner.run_list([stmt.stmt])
        except (_Break, _Continue):
            pass
        except _Abort as exc:  # `set -e` stopped the block: its output so far still goes on
            if not exc.reported:
                raise
        finally:
            if inner.stderr_sink is not None:
                self.ctx.stderr_sinks.pop()
        block_status = self.ctx.last_status
        text = "".join(inner.collected)
        sh = stmt.mode == "sh"
        try:
            result = _run_parsed(stmt.tail, self.ctx, self.variables(), split=sh,
                                 substitute=self.substitute, glob=True, stdin=text, allow_leading_pipe=True,
                                 live=self.shows_live)
        except (ParseError, PipelineError) as exc:
            self.report(exc, stmt.tail)
            if isinstance(exc, UnboundError):
                raise _Abort(exc, stmt.tail, reported=True, stops=True) from None
            self.check_errexit(self.ctx.last_status, stmt.tail)
            return
        if result is not None and result.output is not None:
            self.show(result.output)
        if "pipefail" in self.ctx.options and block_status and not self.ctx.last_status:
            self.ctx.last_status = block_status
        self.check_errexit(self.ctx.last_status, stmt.tail)

    def _block_stderr(self, stmt: Piped, inner: _Runner) -> Callable[[str], None] | None:
        """`done 2> err.txt` / `done 2>&1 | …`: where the block's error stream goes (its `>&2`
        output and its failing commands), read from the tail's first, empty segment."""
        from shellcraft.pipeline import stderr_file_sink

        if "2>" not in stmt.tail:
            return None
        try:
            pipeline = shparser.parse(stmt.tail, self.variables(), substitute=lambda _: "", allow_leading_pipe=True)
        except ParseError:
            return None
        first = pipeline.segments[0] if pipeline and pipeline.segments else None
        if first is None or first.name:
            return None
        if first.stderr is not None:
            return stderr_file_sink(first.stderr)
        return inner.collected.append if first.merge_stderr else None

    def run_if(self, stmt: If) -> None:
        for cond, body in stmt.branches:
            if self.test(cond):
                self.run_list(body)
                return
        if stmt.orelse is not None:
            self.run_list(stmt.orelse)
        else:
            self.ctx.last_status = 0

    def test(self, cond: Cond) -> bool:
        with _no_errexit(self.ctx):  # a failing test is an answer, not an error (`set -e`)
            return self._test(cond)

    def _test(self, cond: Cond) -> bool:
        from shellcraft.pipeline import PipelineError

        if cond.kind == "list":
            self.run_list(cond.stmts)
            return self.ctx.last_status == 0
        if cond.kind == "cmd":
            status, output = self.run_simple(cond.text)
            if output is not None:
                self.show(output)
            ok = status == 0
        else:
            assert cond.tree is not None
            names: dict[str, Any] = {**self.vars, "status": self.ctx.last_status}
            try:
                ok = bool(evaluate(cond.tree, names))
            except ConditionError as exc:
                raise _Abort(PipelineError(1, 1, "if", f"({cond.text.strip()}): {exc}"), cond.text) from None
            self.ctx.last_status = 0 if ok else 1
        return ok != cond.negate


def _unquote(pattern: str) -> str:
    """A case pattern without its quotes; quoted glob characters, and one after a `\\` (`\\?)` for
    getopts' `?`), are escaped so they match literally."""
    out: list[str] = []
    quote = None
    escaped = False
    for ch in pattern:
        if escaped:
            out.append(f"[{ch}]" if ch in "*?[" else ch)
            escaped = False
        elif ch == "\\" and quote is None:
            escaped = True
        elif quote:
            if ch == quote:
                quote = None
            else:
                out.append(f"[{ch}]" if ch in "*?[" else ch)
        elif ch in ("'", '"'):
            quote = ch
        else:
            out.append(ch)
    return "".join(out)
