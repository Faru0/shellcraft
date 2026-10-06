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
from dataclasses import dataclass, field
from typing import Any, Callable, Union

from core import parser as shparser
from core.context import ShellContext, to_text
from core.parser import ParseError, find_closing_paren

KEYWORDS = ("for", "if", "elif", "else", "break", "continue", "do", "done", "then", "fi")
SH_RESERVED = frozenset({"do", "done", "then", "fi", "elif", "else"})
PY_FLAG = "-py"
MAX_RANGE = 100_000  # items one range / brace expansion may produce
MAX_SEQUENCE = 100_000  # longest string / list a condition may build with `*`
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
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

    parts: list[Simple]
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
    kind: str  # "break" or "continue"
    pos: int


Stmt = Union[Simple, Chain, For, If, Jump]


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
    (IncompleteError when more input could complete it)."""
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


def incomplete(text: str) -> str | None:
    """The joiner for a continuation line when `text` is an unfinished script, else None."""
    exc = _incomplete_error(text)
    return None if exc is None else exc.join


def join_continuation(text: str, more: str) -> str:
    """`text` (unfinished) and the next line as one line, with `;` only where one is needed:
    none after `do`, `then`, `else`, `{`, `;`, `&&`, `||`, `|` or before a -py `}` / `else`."""
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
            self._skip_space(separators=True)
            if self._at_end():
                if end is None:
                    return stmts, None
                if end == "}":
                    last = len(self.s.rstrip()) - 1
                    if self.s[last] == "}" and not self._is_close(last):  # `{break}`: meant to close
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
            raise self._error(f"expected ';' or a new line before '{word}' "
                              "(a loop's or an if's output can't be piped or redirected yet)")

    def _statement(self) -> Stmt:
        start = self.i
        word = self._bare_word(start)
        if word == "for":
            return self._for()
        if word == "if":
            return self._if()
        if word in ("break", "continue"):
            if not self.loops:
                raise self._error(f"'{word}' outside a for loop", start)
            self.i += len(word)
            return Jump(word, start)
        if word in ("elif", "else") and self.mode == "py":
            raise self._error(f"'{word}' without a matching if (put it on the same line as the "
                              "if's closing '}')", start)
        if word in SH_RESERVED:
            owner = "for" if word in ("do", "done") else "if"
            raise self._error(f"'{word}' without a matching {owner}", start)
        return self._chain()

    def _chain(self) -> Stmt:
        start = self.i
        parts, ops = [self._pipeline()], []
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
            parts.append(self._pipeline())
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
        while self.i < self.n:
            ch = self.s[self.i]
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
            elif ch in ";\n" or self.s.startswith("&&", self.i) or self.s.startswith("||", self.i):
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
        """Consume `-py` after for / if; True for the Python syntax (also inherited in -py blocks)."""
        self._skip_blanks()
        if self._bare_word(self.i) == PY_FLAG:
            self.i += len(PY_FLAG)
            return True
        return self.mode == "py"

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

    def _for_py(self, var: str, pos: int) -> For:
        items: list[Item] = []
        while True:
            self._skip_space()
            if self._at_end():
                raise self._incomplete("for: missing '{'", " ")
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
            body = self._in_mode("py", lambda: self._block("for"))
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
    try:
        shparser.parse(text, substitute=(lambda _: "") if mode == "sh" else None)
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


class _Abort(Exception):
    """Stops the whole script (a bad condition would fail the same way on every iteration)."""

    def __init__(self, error: Exception, text: str):
        super().__init__(str(error))
        self.error = error
        self.text = text


def execute(program: Program, ctx: ShellContext) -> Any:
    """Run a parsed script. Each statement's output goes to ctx.emit as it is produced, or is
    collected into the result's text when there is no emit (MCP, tests). A failing statement is
    reported and the script goes on, as in a POSIX shell; `$?` holds its status."""
    from core.pipeline import PipelineError, PipelineResult

    runner = _Runner(ctx)
    try:
        runner.run_list(program.stmts)
    except _Abort as exc:
        ctx.last_status = exc.error.status if isinstance(exc.error, PipelineError) else 1
        runner.report(exc.error, exc.text)
    output = None if ctx.emit else "".join(runner.collected)
    return PipelineResult(output=output, status=ctx.last_status)


class _Runner:
    def __init__(self, ctx: ShellContext, parent: _Runner | None = None):
        self.ctx = ctx
        self.parent = parent  # set for `$( … )`: output is captured, errors go to the parent
        self.vars: dict[str, str] = dict(parent.vars) if parent else {}
        self.collected: list[str] = []

    def variables(self) -> dict[str, str]:
        return {**self.vars, "?": str(self.ctx.last_status)}

    def show(self, value: Any) -> None:
        if self.ctx.emit is not None and self.parent is None:
            self.ctx.emit(value)
            return
        text = to_text(value)
        if text:
            self.collected.append(text if text.endswith("\n") else text + "\n")

    def report(self, exc: Exception, text: str) -> None:
        from core.pipeline import PipelineError

        if self.parent is not None:
            self.parent.report(exc, text)
        elif self.ctx.emit_error is not None:
            self.ctx.emit_error(exc, text)
        elif isinstance(exc, PipelineError):
            self.collected.append(f"✗ {exc.name}: {exc.message}\n")
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
        return "".join(sub.collected)

    def run_list(self, stmts: list[Stmt]) -> None:
        for stmt in stmts:
            if isinstance(stmt, Simple):
                _, output = self.run_simple(stmt)
                if output is not None:
                    self.show(output)
            elif isinstance(stmt, Chain):
                self.run_chain(stmt)
            elif isinstance(stmt, For):
                self.run_for(stmt)
            elif isinstance(stmt, If):
                self.run_if(stmt)
            elif stmt.kind == "break":
                raise _Break
            else:
                raise _Continue

    def run_simple(self, stmt: Simple | str, show_errors: bool = True) -> tuple[int, Any]:
        """Run one pipeline; returns (status, output). Bash-mode statements split unquoted
        expansions into words and run `$( … )` (not inside `[[ … ]]`, as in bash)."""
        from core.pipeline import PipelineError, _run_parsed

        if isinstance(stmt, str):
            stmt = Simple(stmt, 0, "py")
        sh = stmt.mode == "sh"
        try:
            result = _run_parsed(stmt.text, self.ctx, self.variables(),
                                 split=sh and not stmt.text.lstrip().startswith("[["),
                                 substitute=self.substitute if sh else None)
        except (ParseError, PipelineError) as exc:
            if show_errors:
                self.report(exc, stmt.text)
            result = None
        output = None if result is None else result.output
        if stmt.negate:
            self.ctx.last_status = 0 if self.ctx.last_status else 1
        return self.ctx.last_status, output

    def run_chain(self, stmt: Chain) -> None:
        status, output = self.run_simple(stmt.parts[0])
        if output is not None:
            self.show(output)
        for op, part in zip(stmt.ops, stmt.parts[1:]):
            if (op == "&&") == (status == 0):
                status, output = self.run_simple(part)
                if output is not None:
                    self.show(output)

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
        from core.pipeline import PipelineError

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
        from core.pipeline import PipelineError

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
