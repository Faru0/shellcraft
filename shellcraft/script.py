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
    NAME = EXPR      NAME += EXPR (-= *= /= //= %=)          in a -py block or a def: a Python value
    print(EXPR, …, sep=" ", end="\n")                       in a -py block or a def: output, like echo
    try [-py] { LIST } [except [as NAME] { LIST }] [finally { LIST }]
                     the first failing command (or Python error) in the body runs `except`

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
import string
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Mapping, TypeGuard, Union

from shellcraft import arith, arrays, getopts, traps
from shellcraft import parser as shparser
from shellcraft.braces import MAX_RANGE, brace_expand  # noqa: F401  (re-exported)
from shellcraft.context import ShellContext, ShellExit, to_text
from shellcraft.inputs import Input
from shellcraft.parser import ParseError, _closing_double_paren, find_closing_paren
from shellcraft.values import MAX_SEQUENCE, MAX_TEXT, PyValue, ShArray, ShMap, ValueProblem  # noqa: F401

KEYWORDS = ("for", "if", "elif", "else", "break", "continue", "do", "done", "then", "fi", "while", "until",
            "case", "esac", "select", "def", "try", "except", "finally")
SH_RESERVED = frozenset({"do", "done", "then", "fi", "elif", "else", "esac"})
PY_FLAG = "-py"
_MATCH_WORD = re.compile(r"""(?:"[^"]*"|'[^']*'|[^\s{;]+)[ \t]+\{(?:\s|$)""")
MAX_FORMAT_NUMBER = 100_000  # largest width / precision in a format spec (`{x:>10}`, `{x:.2f}`)
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_FUNCTION_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
_FUNCTION_DEF = re.compile(r"([A-Za-z_][A-Za-z0-9_-]*)[ \t]*\([ \t]*\)")  # name()
_EMPTY_PARENS = re.compile(r"[ \t]*\([ \t]*\)")
# `count = 0`, `count += 1` in py mode (`x=5`, with no spaces, stays the bash assignment)
_PY_ASSIGN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)([ \t]*)(//=|\+=|-=|\*=|/=|%=|=)(?!=)([ \t]*)")
_PRINT_CALL = re.compile(r"print[ \t]*\(")  # `print(…)`
_CALL = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)[ \t]*\(")  # `add(1, 2)`: a function call on a line of its own
_MUTATOR_CALL = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)[ \t]*\(")  # `hosts.append(`
# a `for -py` list that is one call: `sorted(hosts)`, `d.items()`, `"a b".split()`
_ITEM_CALL = re.compile(r"(?:([A-Za-z_][A-Za-z0-9_]*)|(?:[A-Za-z_][A-Za-z0-9_]*|\"[^\"]*\"|'[^']*')"
                        r"\.[A-Za-z_][A-Za-z0-9_]*)\(")
_ITEM_ASSIGN = re.compile(r"[ \t]+(//=|\+=|-=|\*=|/=|%=|=)(?!=)[ \t]*")  # the ` = ` of `hosts[0] = "x"`
MAX_FUNCTION_DEPTH = 100  # nested / recursive function calls
_RANGE = re.compile(r"^(-?\d+)\.\.(-?\d+)(?:\.\.(\d+))?$")


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
class Arith:
    """`(( EXPR ))`: arithmetic as a command. Assignments and `++` work; the status is 0 when
    the value is not zero, and nothing is printed."""

    text: str  # the expression, as typed
    pos: int
    negate: bool = False


@dataclass
class Group:
    """`{ LIST; }` (same variables) or `( LIST )` (a subshell: its variables, directory, options and
    traps are gone when it ends). A `| cmd`, `> file` or `2>&1` after it wraps it in a Piped."""

    body: list[Stmt]
    subshell: bool
    pos: int
    text: str


@dataclass
class CFor:
    """`for (( init; cond; step )); do LIST; done`: the C-style loop. An empty `cond` is true."""

    init: str
    cond: str
    step: str
    body: list[Stmt]
    pos: int


@dataclass
class Chain:
    """`a && b || c`: each part runs depending on the status so far, left to right."""

    # a Jump only after the first part: `[ -f x ] || return 1`
    parts: list[Simple | Jump | PipedInto | Arith | Group | Piped]
    ops: list[str]
    pos: int


@dataclass
class Item:
    # "word" or "cmd" (-py), "sh" (a bash word, expanded the bash way), "py" (a Python value to
    # loop over: `sorted(hosts)`), "name" (`hosts`, `hosts[1:]`: looped over when the variable
    # holds a list or dict, else the word it is)
    kind: str
    text: str
    pos: int
    tree: ast.Expression | None = None  # "py" and "name": the expression


@dataclass
class For:
    var: str
    items: list[Item]
    body: list[Stmt]
    pos: int
    mode: str = "sh"
    more: tuple[str, ...] = ()  # `for k, v in d.items()`: the names after the first


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
    tree: ast.Expression | None = None  # `return EXPR` in a `def`: a Python value


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


@dataclass
class PipedInto:
    """`cmd | while read x; do …; done` (also `until`, `for`, `if`, `case`): the command's output
    is the input of the block, which reads it line by line (`read`). As in bash the block is a
    subshell: variables it sets do not last after it."""

    source: Simple  # the command (or pipeline) on the left
    stmt: Stmt  # the compound statement on the right
    pos: int
    mode: str = "sh"


@dataclass
class Assign:
    """`NAME = EXPR` or `NAME += EXPR` (`-=`, `*=`, `/=`, `//=`, `%=`) in a -py block or a `def`:
    the value is a Python expression, kept as text in the shell variable."""

    name: str
    op: str
    text: str  # the expression, as typed
    pos: int
    tree: ast.Expression
    command: Simple | None = None  # outside -py: the line as a command, run instead when NAME names one
    touched: tuple[str, ...] = ()  # `last = hosts.pop()`: the variables the value changes


@dataclass
class Mutate:
    """`NAME.append(…)`, `NAME.pop()`, `NAME.update(…)` and the other methods that change a list or
    a dict, on a line of their own: the changed value is stored back in NAME."""

    name: str
    text: str  # the call, as typed
    pos: int
    tree: ast.Expression
    touched: tuple[str, ...]


@dataclass
class SetItem:
    """`NAME[INDEX] = EXPR` (or `+=` and the other `op=` forms), with a space before the `=`:
    one element of a list, one key of a dict. (`NAME[0]=x` with no space is the bash form.)"""

    name: str
    op: str
    text: str  # the whole statement, as typed
    pos: int
    key: ast.Expression  # the index or key (a slice for `a[1:3] = …`)
    tree: ast.Expression
    touched: tuple[str, ...] = ()


@dataclass
class Del:
    """`del NAME[INDEX]`: remove one element of a list (the rest moves up) or one key of a dict.
    `del NAME` (in a -py block or a `def`) unsets the variable."""

    name: str
    text: str
    pos: int
    key: ast.Expression | None
    command: Simple | None = None  # outside -py: the line as a command, run instead when `del` names one


@dataclass
class Call:
    """`name(args)` on a line of its own: the shell function (a `def` or bash) is called with the
    values; what it printed or returned is shown."""

    name: str
    text: str  # the call, as typed
    pos: int
    tree: ast.Expression


@dataclass
class Try:
    """`try { LIST } except [as NAME] { LIST } finally { LIST }`: the first command in the body
    that fails (or a Python error) stops it and runs `except`, with NAME and `$?` set to the
    failing status; `finally` always runs. At least one of `except` / `finally` is there."""

    body: list[Stmt]
    handler: list[Stmt] | None
    name: str  # `except as NAME`, or ""
    final: list[Stmt] | None
    pos: int


@dataclass
class Print:
    """`print(EXPR, …, sep=…, end=…)` in a -py block or a `def`: the values as text, like echo."""

    text: str  # the call, as typed
    pos: int
    tree: ast.Expression
    touched: tuple[str, ...] = ()  # `print(queue.pop())`


Stmt = Union[Simple, Arith, CFor, Group, Chain, For, If, Jump, While, Case, Piped, PipedInto, Function, Select, Assign,
             Call, Try, Print, Mutate, SetItem, Del]


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

    text = shparser.rewrite_backticks(text)
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


BARE_CALL = re.compile(r"[ \t]*([A-Za-z_][A-Za-z0-9_-]*)[ \t]*\([ \t]*\)[ \t]*")


def bare_call(text: str, known: Mapping[str, Any] | set[str] | frozenset[str]) -> str | None:
    """The function name when `text` is just `name()` and `name` is a function that exists: a call,
    not the start of a definition (`name()` then `{` on the next line)."""
    match = BARE_CALL.fullmatch(text)
    return match.group(1) if match and match.group(1) in known else None


def incomplete(text: str, known: Mapping[str, Any] | set[str] | frozenset[str] = frozenset()) -> str | None:
    """The joiner for a continuation line when `text` is an unfinished script, else None.
    Comments don't count: `for x in a b; do  # loop` is unfinished, `echo { # note` isn't.
    `known` names the functions that exist: `hi()` alone is then a call, not an unfinished definition."""
    from shellcraft.parser import strip_comments

    if known and bare_call(strip_comments(text), known):
        return None
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


PIPE_BLOCKS = ("while", "until", "for", "if", "case", "{")  # what `cmd | …` can feed (not `select`: a command name)


class _Parser:
    def __init__(self, text: str, mode: str = "top"):
        self.s = text
        self.n = len(text)
        self.i = 0
        self.depth = 0  # open -py blocks
        self.loops = 0  # enclosing for loops (for break / continue)
        self.functions = 0  # enclosing function bodies (for return)
        self.in_def = False  # the innermost function is a `def` (`return EXPR` is Python)
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
        if word == "match" and self._match_follows(start + 5):
            return self._maybe_piped(self._case(len(word)))
        if word == "try" and self._try_follows(start + 3):
            return self._try()
        if word.rstrip(":") in ("except", "finally"):
            raise self._error(f"'{word.rstrip(':')}' without a matching try", start)
        if word == "function" or (self.mode != "py" and _FUNCTION_DEF.match(self.s, start)):
            return self._function(keyword=word == "function")
        if word in ("elif", "else") and self.mode == "py":
            raise self._error(f"'{word}' without a matching if (put it on the same line as the "
                              "if's closing '}')", start)
        if word in SH_RESERVED:
            owner = "for" if word in ("do", "done") else "if"
            raise self._error(f"'{word}' without a matching {owner}", start)
        if _PRINT_CALL.match(self.s, start):
            return self._print()
        if arrays.is_open(self.s, start):
            return self._chain()  # `a=(x y)`, `a+=(z)`: a bash array, set like `x=1`
        if word == "del" and (deleted := self._del()) is not None:
            return deleted
        if (changed := self._set_item() or self._mutate()) is not None:
            return changed
        if match := _PY_ASSIGN.match(self.s, start):
            # Python's `x = 5`: in a -py block always; elsewhere unless NAME is a builtin (`echo = hi`)
            # or the `=` has a space after it only (`IFS= read x`: a variable for one command)
            if self.mode == "py":
                if match.group(3) != "=" or match.group(2) or match.group(4):
                    return self._assign(match)
            elif match.group(3) != "=" or match.group(2):
                from shellcraft.builtins import BUILTINS

                if match.group(1) not in BUILTINS:
                    return self._assign(match)
        if (call := _CALL.match(self.s, start)) and call.group(1) not in KEYWORDS and (
                self.mode == "py" or self.s[call.end():call.end() + 1] not in (")", " ", "\t")):
            found = self._call(call)
            if found is not None:
                return found
        return self._chain()

    def _call(self, match: re.Match[str]) -> Call | None:
        """`name(args)` alone on its line (a function call); None when something else follows the `)`."""
        start = self.i
        close = find_closing_paren(self.s, match.end())
        if close is None:
            return None
        end = close + 1
        while end < self.n and self.s[end] in " \t":
            end += 1
        if end < self.n and self.s[end] not in ";\n" and not (self.depth and self._is_close(end)):
            return None
        text = self.s[start:close + 1]
        tree = compile_condition(text, start, match.group(1), "call")
        if not (isinstance(tree.body, ast.Call) and isinstance(tree.body.func, ast.Name)):
            return None
        self.i = close + 1
        return Call(match.group(1), text, start, tree)

    def _statement_ends(self, i: int) -> bool:
        """Whether nothing but the end of the statement follows at `i` (blanks aside)."""
        while i < self.n and self.s[i] in " \t":
            i += 1
        return i >= self.n or self.s[i] in ";\n" or bool(self.depth and self._is_close(i))

    def _mutate(self) -> Mutate | None:
        """`hosts.append("c")` alone on its line; None for anything else."""
        start = self.i
        match = _MUTATOR_CALL.match(self.s, start)
        if not match or match.group(2) not in MUTATORS:
            return None
        close = find_closing_paren(self.s, match.end())
        if close is None:
            raise self._incomplete(f"{match.group(1)}.{match.group(2)}(: missing ')'", " ")
        if not self._statement_ends(close + 1):
            return None
        text = self.s[start:close + 1]
        tree = compile_condition(text, start, f"{match.group(1)}.{match.group(2)}", "call", mutators=True)
        self.i = close + 1
        return Mutate(match.group(1), text, start, tree, touched(tree))

    def _set_item(self) -> SetItem | None:
        """`hosts[0] = "x"`, `port["web"] += 1`: a space before the `=` makes it Python's."""
        start = self.i
        name = _NAME.match(self.s, start)
        if not name or not self.s.startswith("[", name.end()):
            return None
        close = shparser.closing_bracket(self.s, name.end())
        if close is None or "\n" in self.s[start:close]:
            return None
        op = _ITEM_ASSIGN.match(self.s, close + 1)
        if not op:
            return None
        target = self.s[start:close + 1]
        self.i = op.end()
        expr = self._tail_text(python=True)
        if not expr:
            raise self._error(f"{target} {op.group(1)}: missing a value after '{op.group(1)}'", start)
        if "$(" in expr:
            raise self._error(f"{target} {op.group(1)}: $( … ) is bash: write {target}=$(command), "
                              "with no spaces", start)
        subscript = compile_condition(target, start, target, "index").body
        assert isinstance(subscript, ast.Subscript)
        tree = compile_condition(expr, start, f"{target} {op.group(1)}", "value", mutators=True)
        return SetItem(name.group(), op.group(1), self.s[start:self.i], start, ast.Expression(subscript.slice),
                       tree, touched(tree))

    def _del(self) -> Del | None:
        """`del hosts[0]`, `del port["web"]`, and in a -py block `del NAME`. None when the line is
        something else (a command called del)."""
        start = at = self.i
        py = self.mode == "py"
        at += len("del")
        if self.s[at:at + 1] not in (" ", "\t"):
            return None
        name = _NAME.match(self.s, at + len(self.s[at:]) - len(self.s[at:].lstrip(" \t")))
        if not name:
            return None
        end, key = name.end(), None
        if self.s.startswith("[", end):
            close = shparser.closing_bracket(self.s, end)
            if close is None:
                return None
            subscript = compile_condition(self.s[name.start():close + 1], start, "del", "index").body
            assert isinstance(subscript, ast.Subscript)
            end, key = close + 1, ast.Expression(subscript.slice)
        elif not py:
            return None  # `del notes` at the prompt stays a command: only -py code unsets this way
        if not self._statement_ends(end):
            if py:
                raise self._error("del: one at a time: del NAME[INDEX] or del NAME", start)
            return None
        self.i = end
        text = self.s[start:end]
        return Del(name.group(), text, start, key, None if py else Simple(text, start, self.mode))

    def _assign(self, match: re.Match[str]) -> Stmt:
        """`NAME = EXPR` / `NAME += EXPR` in py mode. `NAME = input("…")` with a string prompt stays
        a command (run_input: `$who` in the prompt is filled in); any other input() is Python."""
        start = self.i
        name, op = match.group(1), match.group(3)
        self.i = match.end()
        expr = self._tail_text(python=True)
        if _ask_line(self.s[start:self.i]):
            self.i = start
            return self._chain()
        if not expr:
            raise self._error(f"{name} {op}: missing a value after '{op}'", start)
        if "$(" in expr:
            raise self._error(f"{name} {op}: $( … ) is bash: write {name}=$(command), with no spaces", start)
        command = None if self.mode == "py" else Simple(self.s[start:self.i], start, self.mode)
        tree = compile_condition(expr, start, f"{name} {op}", "value", mutators=True)
        return Assign(name, op, expr, start, tree, command, touched(tree))

    def _print(self) -> Print:
        """`print(EXPR, …, sep=…, end=…)` in py mode: checked now, so `source -n` reports a bad one."""
        start = self.i
        text = self._tail_text(python=True)
        close = find_closing_paren(self.s, self.s.index("(", start) + 1)
        if close is not None and close < self.i and self.s[close + 1:self.i].strip():
            raise self._error("print: nothing may follow print(…) on its line (to pipe the output, "
                              "pipe the whole block, or use echo)", start)
        tree = compile_condition(text, start, "print", "arguments", mutators=True)
        call = tree.body
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "print"):
            raise self._error(f"print: write print(…) on a line of its own: {text}", start)
        for keyword in call.keywords:
            if keyword.arg not in ("sep", "end"):
                raise self._error(f"print: unknown keyword '{keyword.arg}' (print takes sep= and end=)", start)
        return Print(text, start, tree, touched(tree))

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
            if self.mode == "py" and self.in_def:  # `return a + b`, `return True`
                value = self._tail_text(python=True)
                if "$(" in value:
                    raise self._error("return: $( … ) is bash: put the command's output in a "
                                      "variable first (x=$(command))", start)
                return Jump("return", start, value, compile_condition(value, start, "return", "value"))
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

    def _tail_text(self, python: bool = False) -> str:
        """The text up to the end of the statement (`;`, a new line, `&&`, `||`, a closing `}`).
        `python`: an expression, which goes on while a `(`, `[` or `{` is open, over lines too
        (`hosts = [` … `]`, `d = {"a": 1}` in a block)."""
        start, quote, opened = self.i, None, 0
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
            elif python and ch in "([{":
                opened += 1
            elif opened:
                opened -= ch in ")]}"
            elif ch in ";\n" or self.s.startswith("&&", self.i) or self.s.startswith("||", self.i):
                break
            elif self.depth and self._is_close(self.i):
                break
            self.i += 1
        if opened and not quote:
            raise self._incomplete(f"missing a closing bracket in: {self.s[start:].strip()[:40]}", " ")
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
        saved_def, self.in_def = self.in_def, False
        self.depth += 1
        self.functions += 1
        try:
            body, _ = self._in_mode("sh", lambda: self.parse_list("}"))
        finally:
            self.depth -= 1
            self.functions -= 1
            self.loops = saved_loops
            self.in_def = saved_def
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
        saved_def, self.in_def = self.in_def, True
        self.functions += 1
        try:
            body = self._in_mode("py", lambda: self._block(f"def {name}"))
        finally:
            self.functions -= 1
            self.loops = saved_loops
            self.in_def = saved_def
        return Function(name, body, self.s[pos:self.i], pos, params)

    # try
    def _try_follows(self, i: int) -> bool:
        """Whether `try` at the start of a statement opens a try block: `-py`, a `{` or the end
        comes next (anything else runs a command named try)."""
        while i < self.n and self.s[i] in " \t\r\n":
            i += 1
        return i >= self.n or self._bare_word(i) == PY_FLAG or self._is_open(i)

    def _match_follows(self, i: int) -> bool:
        """Whether `match` at the start of a statement is `case -py` spelled the Python way:
        `match -py WORD {`, or `match WORD {` inside a -py block (else it runs a command)."""
        while i < self.n and self.s[i] in " \t":
            i += 1
        if self._bare_word(i) == PY_FLAG:
            return True
        return self.mode == "py" and _MATCH_WORD.match(self.s, i) is not None

    def _try(self) -> Try:
        """`try [-py] { LIST } [except [as NAME] { LIST }] [finally { LIST }]`. The blocks are
        Python style with `-py` or inside a -py block, else they keep the syntax around them
        (bash lines in a bash function)."""
        pos = self.i
        self.i += 3
        self._skip_blanks()
        py = self._bare_word(self.i) == PY_FLAG
        if py:
            self.i += len(PY_FLAG)
        mode = "py" if py or self.mode == "py" else "sh"

        def block(what: str) -> list[Stmt]:
            stmts: list[Stmt] = self._in_mode(mode, lambda: self._block(what))
            return stmts

        body = block("try")
        handler: list[Stmt] | None = None
        final: list[Stmt] | None = None
        name = ""
        while True:
            save = self.i
            self._skip_space(separators=True)
            word = self._bare_word(self.i) if not self._at_end() else ""
            if word == "except":
                if final is not None:
                    raise self._error("'except' goes before 'finally'")
                if handler is not None:
                    raise self._error("a try has only one 'except'")
                self.i += len(word)
                self._skip_blanks()
                if self._bare_word(self.i) == "as":
                    self.i += 2
                    self._skip_blanks()
                    match = _NAME.match(self.s, self.i)
                    if not match:
                        if self._at_end():
                            raise self._incomplete("except as: missing a name", " ")
                        raise self._error("except as: expected a name: except as status { … }")
                    name = match.group()
                    self.i = match.end()
                elif not self._at_end() and not self._is_open(self.i):
                    raise self._error("except: there are no error types here; use `except { … }`, "
                                      "or `except as status { … }` for the failing status")
                handler = block("except")
            elif word == "finally":
                if final is not None:
                    raise self._error("a try has only one 'finally'")
                self.i += len(word)
                final = block("finally")
            else:
                if handler is None and final is None:
                    if self._at_end():
                        raise self._incomplete("try: missing 'except' or 'finally'", " ")
                    raise self._error("try: needs an 'except' or a 'finally' after its block: "
                                      "try { … } except { … }", pos)
                self.i = save
                break
        return Try(body, handler, name, final, pos)

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
    def _case(self, keyword: int = 4) -> Case:
        pos = self.i
        self.i += keyword
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
                text = re.sub(r"^case\s+(?=\S)", "", self.s[start:j])  # `case "a" { … }`, as match writes it
                patterns = [_py_pattern(p.strip()) for p in _split_bars(text)]
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
        parts: list[Simple | Jump | PipedInto | Arith | Group | Piped] = [self._pipeline()]
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

    def _pipeline(self) -> Simple | PipedInto | Arith | Group | Piped:
        negate = False
        if self.s.startswith("!", self.i) and self.i + 1 < self.n and self.s[self.i + 1] in " \t":
            negate = True
            self.i += 1
            self._skip_blanks()
        if self.mode != "py" and self.s.startswith("((", self.i):
            return self._arith(negate)
        if self.mode != "py" and (self._is_open(self.i) or (self.s.startswith("(", self.i))):
            if negate:
                raise self._error("! before a group is not supported: put the test inside it")
            return self._group()
        simple = self._simple(stop_at_open=False)
        simple.negate = negate
        if self.i < self.n and self.s[self.i] == "|" and self._pipes_into_block(self.i):
            self.i += 1
            self._skip_space()
            return PipedInto(simple, self._statement(), simple.pos, self.mode)
        return simple

    def _group(self) -> Group | Piped:
        """`{ LIST; }` or `( LIST )`, with any `| cmd` / `> file` after it."""
        start = self.i
        if self.s[start] == "(":
            close = find_closing_paren(self.s, start + 1)
            if close is None:
                raise self._incomplete("missing ')'", " ; ")
            inner = _Parser(self.s[start + 1:close], "sh")
            inner.functions, inner.in_def = self.functions, self.in_def
            try:
                body, _ = inner.parse_list(None)
            except ParseError as exc:
                raise ParseError(exc.message, None if exc.pos is None else start + 1 + exc.pos) from None
            if not body:
                raise self._error("( ): a subshell needs a command inside", start)
            self.i = close + 1
            group = Group(body, True, start, self.s[start:self.i])
        else:
            self.i += 1
            self.depth += 1
            try:
                body, _ = self._in_mode("sh", lambda: self.parse_list("}"))
            finally:
                self.depth -= 1
            if not body:
                raise self._error("{ }: a group needs a command inside: { echo hi; }", start)
            group = Group(body, False, start, self.s[start:self.i])
        stmt = self._maybe_piped(group)
        assert isinstance(stmt, (Group, Piped))
        return stmt

    def _arith(self, negate: bool) -> Arith:
        """`(( EXPR ))`: the text up to the matching `))`; nothing but a separator may follow."""
        start = self.i
        close = _closing_double_paren(self.s, start + 2)
        if close is None:
            if "))" in self.s[start:]:
                raise self._error("(( )): unbalanced parentheses in the expression", start)
            raise self._incomplete("missing '))' for '(('", " ")
        text = self.s[start + 2:close].strip()
        if not text:
            raise self._error("(( )): empty expression", start)
        self.i = close + 2
        self._skip_blanks()
        if not (self._at_end() or self.s[self.i] in ";\n|&" or self._is_close(self.i)):
            raise self._error(f"expected ';' or a new line after '(( {text} ))'")
        return Arith(text, start, negate)

    def _pipes_into_block(self, i: int) -> bool:
        """Whether the `|` at `i` is followed by a loop, `if` or `case`: `cmd | while read x; …`."""
        if self.mode not in ("sh", "top") or self.s.startswith("||", i) or self.s.startswith("|&", i):
            return False
        j = i + 1
        while j < self.n and self.s[j] in " \t\r\n":
            j += 1
        return self._bare_word(j) in PIPE_BLOCKS or (j < self.n and self.s[j] == "(" and not self.s.startswith("((", j))

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
                if quote == '"' and self.mode in ("sh", "top") and self.s.startswith("$(", self.i):
                    self._skip_substitution()
                    continue
                if ch == quote:
                    quote = None
                self.i += 1
                continue
            if ch in ("'", '"'):
                quote = ch
            elif self.mode in ("sh", "top") and self.s.startswith("$(", self.i):
                self._skip_substitution()
                continue
            elif self._word_start(self.i) and (opened := arrays.is_open(self.s, self.i)):
                close = find_closing_paren(self.s, opened.end())  # `a=( … )`: its words may take several lines
                if close is None:
                    raise self._incomplete("missing ')'", " ")
                self.i = close + 1
                continue
            elif ch in ";\n" or (not brackets and (self.s.startswith("&&", self.i) or self.s.startswith("||", self.i))):
                break
            elif ch == "|" and not brackets and self._pipes_into_block(self.i):
                break  # `cmd | while read x; do …; done`: the block is parsed after the command
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
        """At `$(` in bash mode: check the command inside and move past its `)`. `$(( … ))` is
        arithmetic, not a command: its `<<` is a shift, not a here-document."""
        if self.s.startswith("$((", self.i):
            arith_end = _closing_double_paren(self.s, self.i + 3)
            if arith_end is not None:
                self.i = arith_end + 2
                return
        close = find_closing_paren(self.s, self.i + 2)
        if close is None:
            raise self._incomplete("missing ')'", " ; ")
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
    def _for(self) -> For | CFor:
        pos = self.i
        self.i += 3
        py = self._py_flag()
        self._skip_space()
        if not py and self.s.startswith("((", self.i):
            return self._for_c(pos)
        match = _NAME.match(self.s, self.i)
        if not match:
            if self._at_end():
                raise self._incomplete("for: missing a variable name", " ")
            raise self._error("for: expected a variable name (letters, digits and _)")
        var = match.group()
        self.i = match.end()
        self._skip_blanks()
        more: list[str] = []
        while py and self.s.startswith(",", self.i):  # for k, v in d.items()
            self.i += 1
            self._skip_blanks()
            match = _NAME.match(self.s, self.i)
            if not match:
                raise self._error(f"for: expected a variable name after '{var},'")
            more.append(match.group())
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
        if not py:
            return self._for_sh(var, pos)
        loop = self._for_py(var, pos)
        loop.more = tuple(more)
        return loop

    def _for_c(self, pos: int) -> CFor:
        """`for (( init; cond; step )); do LIST; done`."""
        close = _closing_double_paren(self.s, self.i + 2)
        if close is None:
            raise self._incomplete("for: missing '))'", " ")
        parts = arith.split_semicolons(self.s[self.i + 2:close])
        if len(parts) != 3:
            raise self._error("for: write the loop as for ((init; condition; step)); do …; done")
        self.i = close + 2
        self._skip_space(separators=True)
        if self._at_end():
            raise self._incomplete("for: missing 'do'", " ")
        if self._bare_word(self.i) != "do":
            raise self._error(f"for: expected 'do', got '{self._bare_word(self.i)}'")
        self.i += 2
        self.loops += 1
        try:
            body, _ = self._in_mode("sh", lambda: self.parse_list(frozenset({"done"})))
        finally:
            self.loops -= 1
        return CFor(parts[0].strip(), parts[1].strip(), parts[2].strip(), body, pos)

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

    def _py_item(self) -> Item | None:
        """The list of a `for -py` when it is one Python value: `hosts`, `hosts[1:]`, `d.items()`,
        `line.split(",")`, `sorted(hosts)`, `range(3)`, `["a", "b"]`. None for a list of words."""
        self._skip_blanks()
        start, quote, opened = self.i, None, 0
        end = start
        while end < self.n:
            ch = self.s[end]
            if quote:
                quote = None if ch == quote else quote
            elif ch in ("'", '"'):
                quote = ch
            elif ch in "([":
                opened += 1
            elif ch in ")]":
                opened -= 1
            elif not opened and (ch in ";\n" or self._is_open(end)):
                break
            end += 1
        text = self.s[start:end].strip()
        if not self._is_open(end) or not text:
            return None
        if _NAME.fullmatch(text):
            kind = "name"
        elif (name := _NAME.match(text)) and text[name.end():name.end() + 1] == "[":
            if shparser.closing_bracket(text, name.end()) != len(text) - 1:
                return None
            kind = "name"  # `log[12]` is a file pattern when `log` holds no list
        elif (call := _ITEM_CALL.match(text)):
            if (call.group(1) is not None and call.group(1) not in FUNCTIONS) \
                    or find_closing_paren(text, call.end()) != len(text) - 1:
                return None
            kind = "py"
        elif text.startswith("[") and text.endswith("]"):
            kind = "list"
        else:
            return None
        try:
            tree = compile_condition(text, start, "for", "list")
        except ParseError:
            if kind == "py":
                raise
            return None  # `log[!a]`, `[abc]*`: a file pattern
        if kind == "list":
            elements = tree.body.elts if isinstance(tree.body, ast.List) else None
            if elements is None or (len(elements) == 1 and isinstance(elements[0], ast.Name)):
                return None  # `[ab]`: a file pattern too
            kind = "py"
        self.i = end
        return Item(kind, text, start, tree)

    def _for_py(self, var: str, pos: int, what: str = "for") -> For:
        items: list[Item] = []
        if (value := self._py_item()) is not None:
            items.append(value)
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
            if self.s.startswith("(", self.i) and not self.s.startswith("((", self.i):
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


def _ask_line(text: str) -> bool:
    """Whether `text` is a whole `NAME = input("…")` line (ask.parse_ask_line), not an expression
    that only starts and ends like one (`x = input("a") + str(n)`, `x = input(question)`)."""
    from shellcraft import ask

    try:
        return ask.parse_ask_line(text) is not None
    except ask.AskError:
        return False


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
    if arrays.target(text) is not None:
        return  # `a=(x y)`, `a[1]=v`: the same for an array
    if (declared := arrays.declaration(text)) is not None:
        text = declared[0]  # `declare -a a=(x y)`: the command is `declare -a a`
    try:
        shparser.parse(text, substitute=(lambda _: "") if mode in ("sh", "top") else None)
    except ParseError as exc:
        raise ParseError(exc.message, None if exc.pos is None else offset + exc.pos) from None


# ── bash word expansion (for lists) ──────────────────────────────────────────

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

    def expansion(at: int, quoted: bool = False) -> tuple[str, int] | None:
        if word.startswith("$(", at):
            close = find_closing_paren(word, at + 2)
            if close is not None:
                return substitute(word[at + 2:close]).rstrip("\n"), close + 1
            return None
        return shparser.expand_variable(word, at, variables, substitute, quoted)

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
                elif word[i] == "$" and (at := shparser.list_expansion(word, i, variables, quoted=True)) is not None:
                    args, i = at  # "$@", "${a[@]}": one field per item
                    if not args and not fields[-1][0]:
                        fields[-1] = (fields[-1][0], False)  # no arguments: no field, unless other text joins it
                    for k, arg in enumerate(args):
                        if k:
                            new_field()
                            fields[-1] = (fields[-1][0], True)
                        add(arg, True)
                elif word[i] == "$" and (var := expansion(i, quoted=True)) is not None:
                    add(var[0], True)
                    i = var[1]
                else:
                    add(word[i], True)
                    i += 1
            i += 1
        elif ch == "$" and (at := shparser.list_expansion(word, i, variables)) is not None:
            for k, element in enumerate(at[0]):  # ${a[@]}: every element, each split like an unquoted $x
                if k and fields[-1][0]:
                    new_field()
                add_split(element)
            i = at[1]
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

class ConditionError(Exception):
    def __init__(self, message: str = "", status: int = 1):
        super().__init__(message)
        self.status = status  # 130 for an `input()` cancelled with Ctrl-C


def _list(*values: Any) -> list[Any]:
    """`list()`, `list(hosts)`, `list("abc")`: a new list, never one longer than MAX_SEQUENCE."""
    if len(values) > 1:
        raise ConditionError("list() takes one value: list(hosts)")
    if values and isinstance(values[0], str) and len(values[0]) > MAX_SEQUENCE:
        raise ConditionError(f"list() would build more than {MAX_SEQUENCE} items")
    return list(*values)


def _range(*values: Any) -> list[int]:
    """`range(3)`, `range(1, 10, 2)` as a list. Text that is a whole number counts (`n=3` in bash)."""
    numbers = []
    for value in values:
        if isinstance(value, str) and not re.fullmatch(r"\s*-?\d+\s*", value):
            raise ConditionError(f"range: '{value}' is not a whole number")
        numbers.append(int(value) if isinstance(value, str) else value)
    found = range(*numbers)
    if len(found) > MAX_RANGE:
        raise ConditionError(f"range: more than {MAX_RANGE} items")
    return list(found)


# Every function gives back a plain value (a list, never an iterator), so nothing but text, numbers,
# lists, tuples, sets and dicts is ever inside an expression.
FUNCTIONS: dict[str, Any] = {
    "len": len, "int": int, "float": float, "str": str, "bool": bool, "abs": abs, "min": min, "max": max,
    "match": lambda pattern, text: re.search(pattern, text) is not None,
    "exists": os.path.exists, "isfile": os.path.isfile, "isdir": os.path.isdir,
    "sorted": sorted, "reversed": lambda items: list(reversed(items)), "list": _list,
    "dict": lambda *pairs: dict(*pairs), "enumerate": lambda items, start=0: list(enumerate(items, start)),
    "zip": lambda *items: list(zip(*items)), "range": _range, "sum": sum, "any": any, "all": all,
    "map": None,  # _eval's own (_map): its first argument is the name of a function
}
_BUILT_INS = ", ".join(sorted([*FUNCTIONS, "input"]))  # input() is _eval's own (it asks)
STR_METHODS = frozenset({
    "startswith", "endswith", "lower", "upper", "casefold", "title", "strip", "lstrip", "rstrip",
    "split", "rsplit", "splitlines", "removeprefix", "removesuffix", "replace", "count", "find", "rfind",
    "isdigit", "isnumeric", "isdecimal", "isalpha", "isalnum", "isspace", "islower", "isupper",
    "format",  # our own formatter (_safe_format), never str.format
    "join",  # our own too (_join): numbers are joined as their text, and the result is capped
})
LIST_METHODS = frozenset({"index", "count", "copy"})
LIST_MUTATORS = frozenset({"append", "extend", "insert", "pop", "remove", "clear", "sort", "reverse"})
DICT_METHODS = frozenset({"keys", "values", "items", "get", "copy"})
DICT_MUTATORS = frozenset({"pop", "update", "setdefault", "clear"})
MUTATORS = LIST_MUTATORS | DICT_MUTATORS  # they change the variable: only on a line of their own, or in `x = …`
# The methods of each type, looked up by the exact type of the value: nothing else is ever called.
_METHODS: dict[type, frozenset[str]] = {
    str: STR_METHODS, list: LIST_METHODS | LIST_MUTATORS, tuple: frozenset({"index", "count"}),
    dict: DICT_METHODS | DICT_MUTATORS,
}
_KINDS = {str: "text", list: "a list", tuple: "a tuple", dict: "a dict"}
_ALL_METHODS = frozenset().union(*_METHODS.values())
_BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
           ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod}
_CMPOPS = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le,
           ast.Gt: operator.gt, ast.GtE: operator.ge, ast.In: lambda a, b: a in b,
           ast.NotIn: lambda a, b: a not in b, ast.Is: operator.is_, ast.IsNot: operator.is_not}
_PLAIN_NODES = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub, ast.UAdd,
                ast.BinOp, ast.Compare, ast.Name, ast.Load, ast.Tuple, ast.List, ast.Set, ast.Dict, ast.Subscript,
                ast.Slice, ast.IfExp, ast.JoinedStr, ast.FormattedValue, ast.keyword, *_BINOPS, *_CMPOPS)


_AUGMENTED = {"+=": ast.Add, "-=": ast.Sub, "*=": ast.Mult, "/=": ast.Div, "//=": ast.FloorDiv, "%=": ast.Mod}


def as_variable(value: Any) -> str:
    """What a variable set from Python stores: a PyValue for an int, float or bool (it keeps its
    type for later Python code), an array for a list or tuple, a dict for a dict, else the value
    as plain text. Raises ConditionError for a list that can't be stored (a list in a list)."""
    if isinstance(value, (ShArray, ShMap)):
        return value
    if isinstance(value, (bool, int, float)):
        return PyValue(value)
    try:
        if isinstance(value, (list, tuple)):
            return ShArray(value)
        if isinstance(value, dict):
            return ShMap(value)
    except ValueProblem as exc:
        raise ConditionError(str(exc)) from None
    return str(value)


def python_names(variables: Mapping[str, str], status: int) -> dict[str, Any]:
    """The names a Python expression sees: the variables (a PyValue as its number) and `status`.
    An array or dict stays as stored: _eval makes the list or dict of it when the name is read."""
    names: dict[str, Any] = {k: v.value if isinstance(v, PyValue) else v for k, v in variables.items()}
    names["status"] = status
    return names


# How a Python expression calls a shell function (`add(1, 2)`): set by evaluate() for the
# runner doing the evaluating, so _eval needs no shell context of its own.
_CALL_FUNCTION: ContextVar[Callable[[str, list[Any]], Any] | None] = ContextVar("_CALL_FUNCTION", default=None)
# How `input(prompt)` asks (`ask(prompt, kind)`, kind str / int / float): set the same way.
_ASK: ContextVar[Callable[[str, str], Any] | None] = ContextVar("_ASK", default=None)


def _number(text: str) -> int | float:
    """A variable's text as a number, for `count += 1`: int, else float."""
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    raise ConditionError(f"its value '{text}' is not a number")


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
            inner = text[i + 2:close]
            if inner.endswith(("[@]", "[*]")):  # `${a[@]}` is the list a, `${#a[@]}` its length
                inner = f"len({inner[1:-3]})" if inner.startswith("#") else inner[:-3]
            out.append(inner)
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


def compile_condition(text: str, pos: int = 0, what: str = "if", noun: str = "condition",
                      mutators: bool = False) -> ast.Expression:
    """Parse and vet a `( … )` condition (or an assignment's value, with `what` and `noun` for the
    messages); raises ParseError naming what isn't allowed. `mutators`: `hosts.pop()` and the
    other methods that change a variable may be used (the caller stores the result: see touched)."""
    source = _dollars_to_names(text).strip()
    if not source:
        raise ParseError(f"{what}: empty {noun} '()'", pos)
    try:
        tree = ast.parse(source.replace("\n", " "), mode="eval")
    except SyntaxError as exc:
        raise ParseError(f"{what}: invalid {noun} ({exc.msg}): {source}", pos) from None
    for node in ast.walk(tree):
        problem = _vet(node)
        if problem:
            raise ParseError(f"{what}: {problem} in {noun}: {source}", pos)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in MUTATORS:
            owner, method = node.func.value, node.func.attr
            if not isinstance(owner, ast.Name):
                raise ParseError(f"{what}: '.{method}()' changes a variable, so it goes after a name "
                                 f"(hosts.{method}(…)): {source}", pos)
            if not mutators:
                raise ParseError(f"{what}: '.{method}()' changes {owner.id}: use it on a line of its own "
                                 f"({owner.id}.{method}(…)), not in a {noun}", pos)
    return tree


def touched(tree: ast.AST) -> tuple[str, ...]:
    """The variables an expression changes: the names its `.append(…)`, `.pop()`, … are called on."""
    names = [node.func.value.id for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
             and node.func.attr in MUTATORS and isinstance(node.func.value, ast.Name)]
    return tuple(dict.fromkeys(names))


def _vet(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant):
        ok = node.value is None or isinstance(node.value, (str, int, float, bool))
        return None if ok else f"constant {node.value!r} not allowed"
    if isinstance(node, ast.Call):
        if any(isinstance(a, ast.Starred) for a in node.args) or any(k.arg is None for k in node.keywords):
            return "* and ** arguments are not allowed"
        called = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        if node.keywords and called in ("sorted", "sort"):
            if [k.arg for k in node.keywords] != ["reverse"]:
                return f"{called} takes only reverse=True (no key=)"
        elif node.keywords and not (isinstance(node.func, ast.Attribute) and called == "format"
                                    or isinstance(node.func, ast.Name) and called == "print"):
            return ("keyword arguments are not allowed (only in .format(name=…), print(sep=…, end=…) "
                    "and sorted(…, reverse=True))")
        if isinstance(node.func, ast.Name):  # a built-in one, or a shell function (looked up when it runs)
            return None if not node.func.id.startswith("_") else (
                f"function '{node.func.id}' is not available (use: {_BUILT_INS})")
        if isinstance(node.func, ast.Attribute):
            return None if node.func.attr in _ALL_METHODS else f"method '.{node.func.attr}()' is not available"
        return "only plain function and method calls are allowed"
    if isinstance(node, ast.Attribute):
        return None if node.attr in _ALL_METHODS else f"attribute '.{node.attr}' is not available"
    if isinstance(node, ast.Dict) and any(key is None for key in node.keys):
        return "** is not allowed in a dict"
    if isinstance(node, ast.Name) and node.id.startswith("_"):
        return f"name '{node.id}' is not allowed"
    if not isinstance(node, _PLAIN_NODES):
        return f"'{type(node).__name__}' is not allowed"
    return None


def evaluate(tree: ast.Expression, names: dict[str, Any],
             call: Callable[[str, list[Any]], Any] | None = None,
             ask: Callable[[str, str], Any] | None = None) -> Any:
    """The value of a vetted expression. `call(name, args)` runs a shell function the expression
    calls (`add(1, 2)`); without it, only the built-in functions can be called. `ask(prompt, kind)`
    is what `input(…)` does; without it, input() is an error."""
    token, asking = _CALL_FUNCTION.set(call), _ASK.set(ask)
    try:
        return _eval(tree.body, names)
    except ConditionError:
        raise
    except TypeError as exc:
        hint = " (values set from bash are text: use int(name) to compare numbers)" if "'str' and 'int'" in str(exc) \
            or "'int' and 'str'" in str(exc) else ""
        if "non-int of type 'str'" in str(exc) or 'str (not "int")' in str(exc):  # "2" * "2", "2" + 1
            hint = " (values set from bash are text: use int(name) for numbers, str(n) for text)"
        if "can only concatenate list" in str(exc):
            hint = ' (to add one element: hosts.append("x") or hosts += ["x"])'
        raise ConditionError(f"{exc}{hint}") from None
    except KeyError as exc:
        raise ConditionError(f"there is no key {exc} (.get({exc}) gives None for a missing key)") from None
    except (ValueError, ZeroDivisionError, IndexError, re.error, OverflowError) as exc:
        raise ConditionError(f"{type(exc).__name__}: {exc}") from None
    finally:
        _CALL_FUNCTION.reset(token)
        _ASK.reset(asking)


def _is_input(node: ast.AST | None) -> TypeGuard[ast.Call]:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "input"


def _input(node: ast.Call, names: dict[str, Any], kind: str) -> Any:
    """`input(prompt)` in an expression: the answer as text, or a number for `int(input(…))` /
    `float(input(…))`, which ask again until the answer is one."""
    if len(node.args) > 1:
        raise ConditionError("input() takes one prompt, like input(\"Your name? \")")
    prompt = _eval(node.args[0], names) if node.args else ""
    if not isinstance(prompt, str):
        raise ConditionError(f"input: the prompt must be text, not {type(prompt).__name__}")
    ask = _ASK.get()
    if ask is None:
        raise ConditionError("input() can't ask here")
    return ask(prompt, kind)


def _map(node: ast.Call, names: dict[str, Any]) -> list[Any]:
    """`map(int, sizes)`: a function, named by its bare name, over each element. Gives a list."""
    first = node.args[0] if node.args else None
    if len(node.args) != 2 or node.keywords or not isinstance(first, ast.Name) or first.id in ("map", "input"):
        raise ConditionError("map takes the name of a function and one list: map(int, sizes)")
    items = _list(_eval(node.args[1], names))
    if FUNCTIONS.get(first.id) is not None:
        return [FUNCTIONS[first.id](item) for item in items]
    call = _CALL_FUNCTION.get()
    if call is None:
        raise ConditionError(f"{first.id} is not a function (built in: {_BUILT_INS})")
    return [call(first.id, [item]) for item in items]


def _weight(value: Any, limit: int) -> int:
    """About how many characters `value` holds, counting into lists, tuples, sets and dicts, so
    `*` and `+` can't build gigabytes (`["a" * 99999] * 99999`). Stops counting past `limit`."""
    if isinstance(value, str):
        return len(value)
    if isinstance(value, dict):
        return _weight([*value, *value.values()], limit)
    if isinstance(value, (list, tuple, set)):
        total = len(value)
        for item in value:
            total += _weight(item, limit)
            if total > limit:
                break
        return total
    return 1


def _eval(node: ast.AST, names: dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in names:
            value = names[node.id]
            if isinstance(value, (ShArray, ShMap)):  # made once: `hosts.append(x)` changes this one
                value = names[node.id] = value.to_python()
            return value
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
                if isinstance(seq, (str, list, tuple)) and isinstance(count, int) \
                        and _weight(seq, MAX_SEQUENCE) * count > MAX_SEQUENCE:
                    raise ConditionError(f"'*' would build more than {MAX_SEQUENCE} items")
        if isinstance(node.op, ast.Add) and isinstance(left, (str, list, tuple)) \
                and _weight(left, MAX_TEXT) + _weight(right, MAX_TEXT) > MAX_TEXT:
            raise ConditionError(f"'+' would build more than {MAX_TEXT} characters")
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
    if isinstance(node, ast.Dict):
        return {_eval(key, names): _eval(value, names)
                for key, value in zip(node.keys, node.values) if key is not None}
    if isinstance(node, ast.Subscript):
        return _eval(node.value, names)[_eval(node.slice, names)]
    if isinstance(node, ast.Slice):
        return slice(*(None if part is None else _eval(part, names) for part in (node.lower, node.upper, node.step)))
    if isinstance(node, ast.JoinedStr):  # f"…"
        return _join_capped(_eval(part, names) for part in node.values)
    if isinstance(node, ast.FormattedValue):  # {value!r:spec} in an f-string
        spec = "" if node.format_spec is None else _eval(node.format_spec, names)
        conversion = "" if node.conversion == -1 else chr(node.conversion)
        return _format_value(_eval(node.value, names), conversion, spec)
    if isinstance(node, ast.Call):
        if _is_input(node):
            return _input(node, names, "str")
        inner = node.args[0] if len(node.args) == 1 else None
        if isinstance(node.func, ast.Name) and node.func.id in ("int", "float") and _is_input(inner):
            return _input(inner, names, node.func.id)
        if isinstance(node.func, ast.Name) and node.func.id == "map":
            return _map(node, names)
        args = [_eval(a, names) for a in node.args]
        kwargs = {k.arg: _eval(k.value, names) for k in node.keywords if k.arg}
        if isinstance(node.func, ast.Attribute) and node.func.attr == "format":
            target = _eval(node.func.value, names)
            if not isinstance(target, str):
                raise ConditionError(f"'.format()' works on text, not on {type(target).__name__}")
            return _safe_format(target, args, kwargs)
        if isinstance(node.func, ast.Name) and node.func.id == "sorted":
            return sorted(*args, reverse=bool(kwargs.get("reverse")))
        if kwargs and not isinstance(node.func, ast.Attribute):  # vetted: only print(…) gets here
            raise ConditionError("print(…) is a statement of its own, not a value")
        if isinstance(node.func, ast.Name):
            if node.func.id in FUNCTIONS:
                return FUNCTIONS[node.func.id](*args)
            call = _CALL_FUNCTION.get()
            if call is None:
                raise ConditionError(f"{node.func.id} is not a function "
                                     f"(built in: {_BUILT_INS})")
            return call(node.func.id, args)
        return _method(_eval(node.func.value, names), node.func.attr, args, kwargs)
    raise ConditionError(f"'{type(node).__name__}' is not allowed")  # vetted already; defensive


def _held(names: dict[str, Any], name: str) -> Any:
    """The list or dict the variable `name` holds, for a statement that changes it."""
    if name not in names:
        raise ConditionError(f"not set, give it a value first ({name} = [] or {name} = {{}})")
    value = _eval(ast.Name(name), names)
    if not isinstance(value, (list, dict)):
        raise ConditionError("it holds text, not a list or a dict")
    return value


def _augmented(left: Any, op: str, right: Any) -> Any:
    """`left op= right`, with the checks and the error messages of an expression."""
    return evaluate(ast.Expression(ast.BinOp(ast.Constant(left), _AUGMENTED[op](), ast.Constant(right))), {})


def _method(target: Any, name: str, args: list[Any], kwargs: dict[str, Any]) -> Any:
    """`target.name(args)`: a method of text, a list, a tuple or a dict, and of nothing else."""
    kind = str if isinstance(target, str) else type(target)
    if name not in _METHODS.get(kind, ()):
        owners = [text for owner, text in _KINDS.items() if name in _METHODS[owner]]
        works_on = " or ".join(filter(None, [", ".join(owners[:-1]), owners[-1]]))
        raise ConditionError(f"'.{name}()' works on {works_on}, not on {type(target).__name__}")
    if name == "join":
        return _join(target, args)
    if name == "sort":
        return target.sort(*args, reverse=bool(kwargs.get("reverse")))
    if kind is dict and name in ("keys", "values", "items"):
        return list(getattr(target, name)(*args))  # a list, not a view
    if kind is str:
        return getattr(str, name)(target, *args)
    return getattr(target, name)(*args)


def _join(separator: str, args: list[Any]) -> str:
    """`", ".join(hosts)`: a number in the list is joined as its text."""
    if len(args) != 1 or not isinstance(args[0], (list, tuple, set)):
        raise ConditionError('join takes one list: ", ".join(hosts)')
    parts = [part if isinstance(part, str) else str(part) for part in args[0]]
    if sum(len(part) for part in parts) + len(separator) * len(parts) > MAX_TEXT:
        raise ConditionError(f"join: the text would be longer than {MAX_TEXT} characters")
    return separator.join(parts)


# Format specs: `[[fill]align][sign][z][#][0][width][grouping][.precision][type]`. A spec with a
# number type (or grouping) reads a variable's text as a number: shell variables are text.
_SPEC = re.compile(r"(?:.?[<>=^])?[-+ ]?z?#?0?\d*([,_]?)(?:\.\d+)?([bcdeEfFgGnoxX%]?)", re.S)


_CONVERSIONS: dict[str, Callable[[Any], str]] = {"s": str, "r": repr, "a": ascii}


def _format_value(value: Any, conversion: str, spec: str) -> str:
    """One `{value!conversion:spec}` field, for f-strings and .format(). Widths and precisions
    are capped, so `{x:>999999999}` can't build a huge string."""
    if conversion:
        convert = _CONVERSIONS.get(conversion)
        if convert is None:
            raise ConditionError(f"format: unknown conversion '!{conversion}' (use !s, !r or !a)")
        value = convert(value)
    for number in re.findall(r"\d+", spec):
        if int(number) > MAX_FORMAT_NUMBER:
            raise ConditionError(f"format: {number} in '{spec}' is too big (at most {MAX_FORMAT_NUMBER})")
    match = _SPEC.fullmatch(spec)
    if isinstance(value, str) and match and (match.group(1) or match.group(2)):
        try:
            value = _number(value.strip())
        except ConditionError:
            raise ConditionError(f"format: '{value}' is not a number (for ':{spec}')") from None
    try:
        text = format(value, spec)
    except (ValueError, TypeError) as exc:
        raise ConditionError(f"format ':{spec}': {exc}") from None
    if len(text) > MAX_SEQUENCE:
        raise ConditionError(f"format: the text would be longer than {MAX_SEQUENCE} characters")
    return text


def _join_capped(parts: Iterator[str] | list[str]) -> str:
    """The parts joined, stopping as soon as the text is longer than MAX_SEQUENCE."""
    out, size = [], 0
    for part in parts:
        size += len(part)
        if size > MAX_SEQUENCE:
            raise ConditionError(f"format: the text would be longer than {MAX_SEQUENCE} characters")
        out.append(part)
    return "".join(out)


def _safe_format(template: str, args: list[Any], kwargs: dict[str, Any]) -> str:
    """`"…".format(…)` without str.format: a field is `{}`, `{0}` or `{name}`, with an optional
    `!conversion` and `:spec`. `{0.attr}` and `{0[key]}` (the way out of a str.format sandbox)
    are refused."""
    auto: list[int] = [0]  # the next `{}` number; -1 once a field is numbered by hand

    def value(key: str) -> Any:
        if key == "":
            if auto[0] < 0:
                raise ConditionError("format: can't mix {} with numbered fields like {0}")
            index, auto[0] = auto[0], auto[0] + 1
        elif key.isdigit():
            if auto[0] > 0:
                raise ConditionError("format: can't mix {} with numbered fields like {0}")
            index, auto[0] = int(key), -1
        elif _NAME.fullmatch(key):
            if key not in kwargs:
                raise ConditionError(f"format: no value for {{{key}}} (pass it: .format({key}=…))")
            return kwargs[key]
        else:
            raise ConditionError(f"format: field '{{{key}}}' is not allowed (use {{}}, a number like "
                                 "{0} or a name like {name}; no .attribute or [key])")
        if index >= len(args):
            raise ConditionError(f"format: no value for field {index} ({len(args)} given)")
        return args[index]

    def fill(text: str, nested: bool) -> str:
        parts = []
        try:
            parsed = list(string.Formatter().parse(text))
        except ValueError as exc:
            raise ConditionError(f"format: {exc}") from None
        for literal, key, spec, conversion in parsed:
            parts.append(literal)
            if key is None:
                continue
            found = value(key)
            spec = spec or ""
            if "{" in spec:
                if nested:
                    raise ConditionError("format: fields are nested too deep")
                spec = fill(spec, nested=True)  # `{x:>{width}}`
            parts.append(_format_value(found, conversion or "", spec))
        return _join_capped(parts)

    return fill(template, nested=False)


def format_prompt(raw: str, names: dict[str, Any], call: Callable[[str, list[Any]], Any] | None = None,
                  ask: Callable[[str, str], Any] | None = None) -> str:
    """An `input(f"…")` prompt, filled in with `names`. Raises ConditionError."""
    try:
        tree = compile_condition(raw, 0, "input", "prompt")
    except ParseError as exc:
        raise ConditionError(exc.message.removeprefix("input: ")) from None
    return str(evaluate(tree, names, call, ask))


# ── execution ────────────────────────────────────────────────────────────────

class _Break(Exception):
    pass


class _Continue(Exception):
    pass


_NO_VALUE = object()  # a function that ended without `return EXPR`


class _Return(Exception):
    """`return [N]` in a function body: N, or None for the status of the last command. In a
    `def`, `return EXPR` carries EXPR's value instead (status None)."""

    def __init__(self, status: int | None, value: Any = _NO_VALUE):
        super().__init__(status)
        self.status = status
        self.value = value


def _returned(value: Any) -> tuple[str, int]:
    """What a `def`'s `return EXPR` means when it is called as a command: (text to print, status).
    0-255 is the status, True / False are 0 / 1, None is 0; anything else is printed."""
    if isinstance(value, bool):
        return "", 0 if value else 1
    if value is None:
        return "", 0
    if isinstance(value, int) and 0 <= value <= 255:
        return "", value
    return f"{value}\n", 0


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


class _TryFailed(Exception):
    """A command in a `try` body failed (`status`): the body stops there and `except` runs."""

    def __init__(self, status: int, text: str):
        super().__init__(status)
        self.status = status
        self.text = text


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
    made, through ctx.emit, and the function returns no output of its own. A `def`'s `return
    EXPR` is a status or printed (_returned)."""
    from shellcraft.context import WithStatus

    output, status, value = _run_function(fn, args, stdin, ctx, cmd, live)
    if value is not _NO_VALUE:
        text, status = _returned(value)
        output += text
        ctx.last_status = status
    return WithStatus(output, status) if status else output


def _run_function(fn: Function, args: list[str], stdin: str, ctx: ShellContext, cmd: Any = None,
                  live: bool = False) -> tuple[str, int, Any]:
    """Run a function's body (see call_function): (its output, its status, the value of its
    `return EXPR`, or _NO_VALUE)."""
    from shellcraft.context import CommandError
    from shellcraft.pipeline import stderr_file_sink

    if not ctx.allow_aliases:
        raise CommandError(f"{fn.name}: shell functions are not available here (MCP)")
    if len(ctx.positional) >= MAX_FUNCTION_DEPTH:
        raise CommandError(f"{fn.name}: functions nested more than {MAX_FUNCTION_DEPTH} deep "
                           "(a recursion that never stops?)")
    runner = _Runner(ctx)
    runner.collect_only = not live
    runner.frame = {}
    runner.stdin = stdin if isinstance(stdin, Input) or not stdin else Input(stdin)  # `read` takes it line by line
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
        value: Any = _NO_VALUE
        try:
            with traps.frame(ctx, runner):  # its own traps; EXIT runs here, before `local`s go
                try:
                    for k, (param, default) in enumerate(fn.params or []):
                        runner.frame.setdefault(param, ctx.variables.get(param))
                        ctx.variables[param] = args[k] if k < len(args) else (default or "")
                    runner.run_list(fn.body)
                except _Return as exc:
                    status, value = exc.status, exc.value
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
    return "".join(runner.collected), status, value


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
        self.stdin: str = parent.stdin if parent else ""
        # A function call's or block's `2> file` / `2>&1`: where its errors are reported.
        self.stderr_sink: Callable[[str], None] | None = None
        # `try` bodies being run by this runner: a failing command jumps to `except`. A function
        # call or `$( … )` starts at 0 (its status is what counts, once it ends).
        self.trying = 0

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
        elif isinstance(stmt, Arith):
            status = self.run_arith(stmt)
            if not stmt.negate:
                self.check_errexit(status, f"(( {stmt.text} ))")
        elif isinstance(stmt, CFor):
            self.run_cfor(stmt)
        elif isinstance(stmt, Group):
            self.run_group(stmt)
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
        elif isinstance(stmt, PipedInto):
            self.run_pipedinto(stmt)
        elif isinstance(stmt, Function):
            self.define(stmt)
        elif isinstance(stmt, Select):
            self.run_select(stmt)
        elif isinstance(stmt, Assign):
            if stmt.command is not None and self.is_command(stmt.name):
                self.run_stmt(stmt.command)  # `ll = 5` with ll an alias: the line is a command
            else:
                self.run_assign(stmt)
        elif isinstance(stmt, Call):
            self.run_call(stmt)
        elif isinstance(stmt, Try):
            self.run_try(stmt)
        elif isinstance(stmt, Print):
            self.run_print(stmt)
        elif isinstance(stmt, Mutate):
            self.run_mutate(stmt)
        elif isinstance(stmt, SetItem):
            self.run_set_item(stmt)
        elif isinstance(stmt, Del):
            if stmt.command is not None and self.is_command("del"):
                self.run_stmt(stmt.command)  # `del` is an alias or a function here
            else:
                self.run_del(stmt)
        elif stmt.kind == "break":
            raise _Break
        elif stmt.kind == "return":
            if stmt.tree is not None:
                raise _Return(None, self.return_value(stmt))
            raise _Return(self.return_status(stmt))
        else:
            raise _Continue

    def check_errexit(self, status: int, text: str) -> None:
        """A command that failed (and isn't a test, the left of `&&` / `||` or `!`) runs the ERR
        trap; with `set -e` it then stops the script, the function or the file it is in. In a
        `try` body it jumps to `except` instead (and ERR doesn't run)."""
        from shellcraft.pipeline import errexit

        if status and self.trying and not self.ctx.errexit_off:
            raise _TryFailed(status, text)
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

    def return_value(self, stmt: Jump) -> Any:
        """`return EXPR` in a `def`: EXPR's value. A bad expression is reported, and the
        function returns status 1."""
        from shellcraft.pipeline import PipelineError

        names = python_names(self.vars, self.ctx.last_status)
        try:
            return evaluate(stmt.tree, names, self.call_function, self.ask_input) if stmt.tree else None
        except ConditionError as exc:
            self.report(PipelineError(1, 1, "return", f"return {stmt.value}: {exc}", exc.status),
                        f"return {stmt.value}")
            if self.trying and not self.ctx.errexit_off:
                raise _TryFailed(exc.status, f"return {stmt.value}") from None
            raise _Return(exc.status) from None

    def call_function(self, name: str, args: list[Any]) -> Any:
        """`name(args)` in a Python expression: run the shell function (`def` or bash) with the
        arguments as variables (as_variable: `$1` is text, a `def`'s Python code gets a number as a
        number). The value is its `return EXPR`, else what it printed (without the last new line,
        as `$( )` gives it). Its status goes to `$?`."""
        from shellcraft.context import CommandError

        fn = self.ctx.functions.get(name)
        if fn is None:
            raise ConditionError(f"{name} is not a function (built in: {_BUILT_INS}; "
                                 "or define it first with def)")
        try:
            output, status, value = _run_function(fn, [as_variable(a) for a in args],
                                                  self.stdin, self.ctx)
        except CommandError as exc:
            raise ConditionError(str(exc)) from None
        self.ctx.last_status = status
        return output.rstrip("\n") if value is _NO_VALUE else value

    def ask_input(self, prompt: str, kind: str) -> Any:
        """`input(prompt)` in a Python expression (kind str), or `int(input(…))` / `float(…)`,
        which ask again until the answer is a number. Raises ConditionError (status 130 for Ctrl-C)."""
        from shellcraft import ask

        try:
            return ask.ask_as(self.ctx, prompt, kind, self.say)
        except ask.AskError as exc:
            raise ConditionError(str(exc), exc.status) from None

    def say(self, message: str) -> None:
        """A warning on the screen (`'x' is not a number; try again`), not part of any output."""
        if self.ctx.ui is not None:
            from rich.text import Text

            self.ctx.ui.console.print(Text(message, style="sc.warning"), markup=False, highlight=False)

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
        literals: list[str] = []
        kind: bool | None = None  # `local -A m`: a dict; `local -a a`: an array
        try:
            if (declared := arrays.declaration(stmt.text.strip())) is not None:  # local a=(x y)
                rest, literals = declared[0][len("local"):], declared[1]
            tokens = shparser.tokenize(rest, self.variables(), substitute=self.substitute)
        except shparser.UnboundVariable:
            raise  # `set -u`: run_list stops the script
        except ParseError as exc:
            self.report(exc, stmt.text)
            self.ctx.last_status = 2
            return 2, None
        while tokens and tokens[0].value in ("-a", "-A"):
            kind = tokens.pop(0).value == "-A"
        for token in tokens:
            name, sep, value = token.value.partition("=")
            if not _NAME.fullmatch(name):
                self.report(PipelineError(1, 1, "local", f"local: '{name}': not a valid identifier"), stmt.text)
                self.ctx.last_status = 1
                return 1, None
            if name in self.ctx.readonly:
                self.report(PipelineError(1, 1, "local", f"local: {name}: readonly variable"), stmt.text)
                self.ctx.last_status = 1
                return 1, None
            if name not in self.frame:
                self.frame[name] = self.ctx.variables.get(name)
            self.ctx.variables[name] = arrays.declared_as(name, value if sep else None, kind) \
                if kind is not None else value if sep else ""
        try:
            for literal in literals:
                arrays.assign(literal, self.ctx, self.variables(), self.substitute, None, kind)
        except (ParseError, PipelineError) as exc:
            if isinstance(exc, shparser.UnboundVariable):
                raise
            self.report(exc, stmt.text)
            self.ctx.last_status = 1
            return 1, None
        self.ctx.last_status = 0
        return 0, None

    def run_simple(self, stmt: Simple | str, show_errors: bool = True, live: bool | None = None) -> tuple[int, Any]:
        """Run one pipeline; returns (status, output). Bash-mode statements split unquoted
        expansions into words and run `$( … )` (not inside `[[ … ]]`, as in bash)."""
        from shellcraft.pipeline import PipelineError, UnboundError, _run_parsed, assign, assign_target

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
        token = assign_target.set(self.vars)  # `read` sets its variables where `x=1` would
        try:
            if assign(stmt.text, self.ctx, self.variables(), self.substitute if expands else None, target=self.vars):
                self.ctx.last_status = 0
                return 0, None
            result = _run_parsed(stmt.text, self.ctx, self.variables(),
                                 split=sh and not stmt.text.lstrip().startswith("[["),
                                 substitute=self.substitute if expands else None, glob=expands,
                                 stdin=self.stdin, live=self.shows_live if live is None else live)
        except (ParseError, PipelineError) as exc:
            if isinstance(exc, ParseError) and not isinstance(exc, shparser.UnboundVariable):
                self.ctx.last_status = 2  # a line that won't parse failed: `while <bad line>` must end
            if show_errors:
                self.report(exc, stmt.text)
            if isinstance(exc, UnboundError):  # `set -u`: the script stops here
                raise _Abort(exc, stmt.text, reported=show_errors, stops=True) from None
            result = None
        finally:
            assign_target.reset(token)
        output = None if result is None else result.output
        if stmt.negate:
            self.ctx.last_status = 0 if self.ctx.last_status else 1
        return self.ctx.last_status, output

    def run_input(self, stmt: Simple) -> int | None:
        """`name = input("…")` (or `int(input(…))`, `float(input(…))`) inside a block or a
        function, as on a line of its own; None when the line is something else."""
        from shellcraft import ask
        from shellcraft.pipeline import PipelineError

        try:
            asking = ask.parse_ask_line(stmt.text)
            if asking is None:
                return None
            name, prompt, kind = asking
            if isinstance(prompt, ask.FPrompt):  # input(f"Age of {who}? ")
                names = python_names(self.vars, self.ctx.last_status)
                prompt = format_prompt(prompt, names, self.call_function, self.ask_input)
            else:
                prompt = shparser._expand_simple(prompt, self.variables())  # input("Age of $who? ")
            self.vars[name] = as_variable(ask.ask_as(self.ctx, prompt, kind, self.say))
        except ConditionError as exc:
            self.report(PipelineError(1, 1, "input", f"input: {exc}"), stmt.text)
            self.ctx.last_status = 1
            return 1
        except ask.AskError as exc:
            self.report(PipelineError(1, 1, "input", str(exc), exc.status), stmt.text)
            self.ctx.last_status = exc.status
            return exc.status
        self.ctx.last_status = 0
        return 0

    def run_assign(self, stmt: Assign) -> None:
        """`NAME = EXPR`: an int, float or bool keeps its type, a list or dict is stored as an
        array or dict (as_variable), anything else is stored as text. `NAME += EXPR` and the other
        `op=` forms work on the current value: a number set from Python or a list as is; else text
        when EXPR is text (`s += "x"`), else the text read as a number (int, or float when it
        isn't a whole number)."""
        from shellcraft.pipeline import PipelineError, set_variable

        text = f"{stmt.name} {stmt.op} {stmt.text}"
        names = python_names(self.vars, self.ctx.last_status)
        try:
            value = evaluate(stmt.tree, names, self.call_function, self.ask_input)
            if stmt.op != "=":
                current = self.vars.get(stmt.name)
                if current is None:
                    raise ConditionError("not set, give it a value first")
                if isinstance(current, PyValue):
                    left = current.value
                elif isinstance(current, (ShArray, ShMap)):
                    left = _held(names, stmt.name)
                else:
                    left = current if isinstance(value, str) else _number(current)
                value = _augmented(left, stmt.op, value)
            changed = [*self.changed(names, stmt.touched, stmt.name), (stmt.name, as_variable(value))]
        except ConditionError as exc:
            self.report(PipelineError(1, 1, stmt.name, f"{stmt.name}: {exc}", exc.status), text)
            self.ctx.last_status = exc.status
            self.check_errexit(exc.status, text)
            return
        for name, new in changed:
            set_variable(self.ctx, name, new, self.vars)
        self.ctx.last_status = 0

    def changed(self, names: dict[str, Any], touched: tuple[str, ...], but: str = "") -> list[tuple[str, str]]:
        """What to store after an expression that called `.append(…)`, `.pop()`, …: (name, new
        value) for each list or dict it changed. Raises ConditionError for one that can't be stored."""
        found = []
        for name in touched:
            if name != but and isinstance(names.get(name), (list, dict)):
                try:
                    found.append((name, as_variable(names[name])))
                except ConditionError as exc:
                    raise ConditionError(f"{name}: {exc}") from None
        return found

    def python_fails(self, exc: ConditionError, name: str, text: str) -> None:
        """Report a Python statement's error the way an assignment's is: `✗ name: message`, `$?` set."""
        from shellcraft.pipeline import PipelineError

        message = str(exc).removeprefix(f"{name}: ")
        self.report(PipelineError(1, 1, name, f"{name}: {message}", exc.status), text)
        self.ctx.last_status = exc.status
        self.check_errexit(exc.status, text)

    def run_mutate(self, stmt: Mutate) -> None:
        """`hosts.append("c")`: run the method on the list or dict and store what it left."""
        from shellcraft.pipeline import set_variable

        names = python_names(self.vars, self.ctx.last_status)
        try:
            if stmt.name not in names:
                raise ConditionError(f"not set, give it a value first ({stmt.name} = [])")
            evaluate(stmt.tree, names, self.call_function, self.ask_input)
            changed = self.changed(names, stmt.touched)
        except ConditionError as exc:
            self.python_fails(exc, stmt.name, stmt.text)
            return
        for name, new in changed:
            set_variable(self.ctx, name, new, self.vars)
        self.ctx.last_status = 0

    def run_set_item(self, stmt: SetItem) -> None:
        """`hosts[0] = "x"`, `port["web"] += 1`: one element of a list, one key of a dict."""
        from shellcraft.pipeline import set_variable

        names = python_names(self.vars, self.ctx.last_status)
        try:
            value = evaluate(stmt.tree, names, self.call_function, self.ask_input)
            key = evaluate(stmt.key, names, self.call_function, self.ask_input)
            held = _held(names, stmt.name)
            try:
                if stmt.op != "=":
                    value = _augmented(held[key], stmt.op, value)
                held[key] = value
            except IndexError:
                raise ConditionError(f"there is no element {key} (it has {len(held)}; "
                                     f"{stmt.name}.append(…) adds one)") from None
            except KeyError:
                raise ConditionError(f"there is no key {key!r} for {stmt.op} to add to "
                                     f"(set it first: {stmt.name}[{key!r}] = …)") from None
            except (TypeError, ValueError) as exc:
                raise ConditionError(f"{type(exc).__name__}: {exc}") from None
            changed = self.changed(names, (*stmt.touched, stmt.name))
        except ConditionError as exc:
            self.python_fails(exc, stmt.name, stmt.text)
            return
        for name, new in changed:
            set_variable(self.ctx, name, new, self.vars)
        self.ctx.last_status = 0

    def run_del(self, stmt: Del) -> None:
        """`del hosts[0]`: the list without that element (the rest moves up), the dict without
        that key. `del NAME`: the variable is unset."""
        from shellcraft.pipeline import set_variable

        if stmt.key is None:
            status, output = self.run_simple(f"unset {stmt.name}")
            if output is not None:
                self.show(output)
            self.check_errexit(status, stmt.text)
            return
        names = python_names(self.vars, self.ctx.last_status)
        try:
            key = evaluate(stmt.key, names, self.call_function, self.ask_input)
            held = _held(names, stmt.name)
            try:
                del held[key]
            except IndexError:
                raise ConditionError(f"there is no element {key} (it has {len(held)})") from None
            except KeyError:
                raise ConditionError(f"there is no key {key!r}") from None
            except TypeError as exc:
                raise ConditionError(f"TypeError: {exc}") from None
            new = as_variable(held)
        except ConditionError as exc:
            self.python_fails(exc, stmt.name, stmt.text)
            return
        set_variable(self.ctx, stmt.name, new, self.vars)
        self.ctx.last_status = 0

    def is_command(self, name: str) -> bool:
        """Whether `name` is something a line can run: a module, alias, function, `def` or func
        (builtins are told apart when the line is parsed)."""
        from shellcraft import aliases, funcs

        ctx = self.ctx
        return (ctx.registry.get(name) is not None or name in ctx.functions
                or (ctx.allow_aliases and name in aliases.get_all(ctx.config))
                or (ctx.allow_aliases and funcs.as_command(ctx, name) is not None))

    def run_call(self, stmt: Call) -> None:
        """`add(1, 2)` on a line of its own: call the function and show what it printed or returned."""
        from shellcraft.pipeline import PipelineError

        names = python_names(self.vars, self.ctx.last_status)
        self.ctx.last_status = 0
        try:
            value = evaluate(stmt.tree, names, self.call_function, self.ask_input)
        except ConditionError as exc:
            self.report(PipelineError(1, 1, stmt.name, f"{stmt.name}: {exc}", exc.status), stmt.text)
            self.ctx.last_status = exc.status
            self.check_errexit(exc.status, stmt.text)
            return
        if value is not None and value != "":
            text = str(value)
            self.show(text if text.endswith("\n") else text + "\n")
        self.check_errexit(self.ctx.last_status, stmt.text)

    def run_print(self, stmt: Print) -> None:
        """`print(…)`: each value as text (`True` / `False` for a bool), joined with `sep` (a
        space) and ended with `end` (a new line). A bad value is reported like an assignment's."""
        from shellcraft.pipeline import PipelineError, set_variable, trace

        call = stmt.tree.body
        assert isinstance(call, ast.Call)
        names = python_names(self.vars, self.ctx.last_status)
        try:
            def value(node: ast.expr) -> Any:
                return evaluate(ast.Expression(node), names, self.call_function, self.ask_input)

            values = [str(value(arg)) for arg in call.args]
            options = {k.arg: value(k.value) for k in call.keywords}
            for name, option in options.items():
                if option is not None and not isinstance(option, str):
                    raise ConditionError(f"{name} must be text, not {type(option).__name__}")
            sep = " " if options.get("sep") is None else options["sep"]
            end = "\n" if options.get("end") is None else options["end"]
            text = _join_capped([sep.join(values), end])
            changed = self.changed(names, stmt.touched)
        except ConditionError as exc:
            self.report(PipelineError(1, 1, "print", f"print: {exc}", exc.status), stmt.text)
            self.ctx.last_status = exc.status
            self.check_errexit(exc.status, stmt.text)
            return
        for name, new in changed:  # print(queue.pop())
            set_variable(self.ctx, name, new, self.vars)
        trace(self.ctx, ["print", *values])
        if text:
            self.show(text)
        self.ctx.last_status = 0

    def run_try(self, stmt: Try) -> None:
        """Run the body; on its first failure run `except` (NAME and `$?` hold the failing
        status), which makes the try's status its own. `finally` runs whatever happens, also
        when break, continue, return, exit or Ctrl-C pass through, and leaves `$?` as it was.
        With no `except` the failure goes on after `finally`: ERR, `set -e` or an outer try."""
        from shellcraft.pipeline import set_variable

        failed: tuple[int, str] | None = None
        try:
            failed = self._try_body(stmt)
            if failed is not None and stmt.handler is not None:
                self.ctx.last_status = failed[0]
                if stmt.name:
                    set_variable(self.ctx, stmt.name, str(failed[0]), self.vars)
                failed = None
                self.run_list(stmt.handler)
        finally:
            if stmt.final is not None:
                status = self.ctx.last_status if failed is None else failed[0]
                self.run_list(stmt.final)
                self.ctx.last_status = status
        if failed is not None:
            self.ctx.last_status = failed[0]
            self.check_errexit(failed[0], failed[1])

    def _try_body(self, stmt: Try) -> tuple[int, str] | None:
        """Run a try's body: None when it ends well, else (the failing status, the command). A
        script-stopping error (a bad Python condition, `set -u`) counts when there is an
        `except`; without one it goes on stopping the script, after `finally`."""
        self.trying += 1
        if stmt.handler is not None:
            self.ctx.try_handlers += 1  # the except handles failures: no ERR trap for them
        try:
            self.run_list(stmt.body)
        except _TryFailed as exc:
            return exc.status, exc.text
        except _Abort as exc:
            if stmt.handler is None:
                raise
            if not exc.reported:
                self.report(exc.error, exc.text)
            return getattr(exc.error, "status", 1) or 1, exc.text
        finally:
            self.trying -= 1
            if stmt.handler is not None:
                self.ctx.try_handlers -= 1
        return None

    def run_chain(self, stmt: Chain) -> None:
        """`a && b || c`. Under `set -e` only the last part's failure stops the script, and only
        when it ran: `false && x` goes on, `x && false` stops."""
        first = stmt.parts[0]
        assert not isinstance(first, Jump)  # a Jump only ever follows && / ||
        last = len(stmt.parts) - 1
        status = self.run_part(first, last == 0)
        for k, (op, part) in enumerate(zip(stmt.ops, stmt.parts[1:]), 1):
            if (op == "&&") == (status == 0):
                if isinstance(part, Jump):
                    self.run_list([part])  # raises: break, continue or return
                    return
                status = self.run_part(part, k == last)

    def run_part(self, part: Simple | PipedInto | Arith | Group | Piped, last: bool) -> int:
        """One part of an `&&` / `||` chain; `set -e` applies to the last one only."""
        traps.new_command(self.ctx)
        if isinstance(part, (Group, Piped)):
            with _no_errexit(self.ctx) if not last else nullcontext():
                self.run_stmt(part)
            return self.ctx.last_status
        if isinstance(part, Arith):
            with _no_errexit(self.ctx) if not (last and not part.negate) else nullcontext():
                status = self.run_arith(part)
                if last and not part.negate:
                    self.check_errexit(status, f"(( {part.text} ))")
            return status
        if isinstance(part, PipedInto):
            with _no_errexit(self.ctx) if not last else nullcontext():
                self.run_pipedinto(part)
            return self.ctx.last_status
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

    def run_group(self, stmt: Group) -> None:
        """`{ LIST; }` runs here. `( LIST )` runs on a copy of the variables, with its own traps, and
        puts back the directory, the shell's variables, options and functions when it ends; `exit`
        inside it only ends it."""
        if not stmt.subshell:
            self.run_list(stmt.body)
            return
        child = _Runner(self.ctx, parent=self)
        child.stdin = self.stdin
        status: int | None = None
        with _subshell_state(self.ctx):
            try:
                with traps.frame(self.ctx, child):
                    child.run_list(stmt.body)
            except ShellExit as exc:
                status = exc.code
            except _Return as exc:
                status = exc.status
            except (_Break, _Continue):
                pass
            except _Abort as exc:
                if not exc.reported:
                    child.report(exc.error, exc.text)
                status = getattr(exc.error, "status", 1) or 1
        text = "".join(child.collected)
        if text:
            self.show(text)
        if status is not None:
            self.ctx.last_status = status

    def arith_value(self, text: str) -> int:
        """The value of an arithmetic expression (`(( ))`, `let`, a C-style `for`): `${…}` and `$( )` in it
        expand first, and assignments set shell variables (in this scope)."""
        variables = self.variables()
        text = shparser._arith_text(text, variables, self.substitute)
        while (at := text.find("$(")) != -1 and not text.startswith("$((", at):
            close = find_closing_paren(text, at + 2)
            if close is None:
                break
            text = text[:at] + self.substitute(text[at + 2:close]).strip() + text[close + 1:]
        return arith.execute(text, variables, variables.assign)

    def run_arith(self, stmt: Arith) -> int:
        """`(( EXPR ))`: status 0 when the value is not zero (1 when it is, or on an error)."""
        from shellcraft.pipeline import PipelineError

        try:
            status = 0 if self.arith_value(stmt.text) else 1
        except (arith.ArithError, PipelineError) as exc:
            message = exc.message if isinstance(exc, PipelineError) else str(exc)
            self.report(PipelineError(1, 1, "((", f"(( {stmt.text} )): {message}"), f"(( {stmt.text} ))")
            status = 1
        if stmt.negate:
            status = 0 if status else 1
        self.ctx.last_status = status
        return status

    def run_cfor(self, stmt: CFor) -> None:
        """`for ((init; cond; step))`: init once, then while cond is not zero (or empty): the body, step."""
        from shellcraft.pipeline import PipelineError

        def value(text: str) -> int:
            try:
                return self.arith_value(text)
            except (arith.ArithError, PipelineError) as exc:
                message = exc.message if isinstance(exc, PipelineError) else str(exc)
                raise _Abort(PipelineError(1, 1, "for", f"(( {text} )): {message}"), text) from None

        self.ctx.last_status = 0
        if stmt.init:
            value(stmt.init)
        while not stmt.cond or value(stmt.cond):
            try:
                self.run_list(stmt.body)
            except _Break:
                break
            except _Continue:
                pass
            if stmt.step:
                value(stmt.step)

    def run_for(self, stmt: For) -> None:
        from shellcraft.pipeline import PipelineError

        values = self.items(stmt)
        if not values:
            self.ctx.last_status = 0
        names = (stmt.var, *stmt.more)
        for value in values:
            if stmt.more:  # for k, v in d.items()
                parts = value.items if isinstance(value, ShArray) else ()
                if len(parts) != len(names):
                    raise _Abort(PipelineError(1, 1, "for", f"for: {len(names)} names ({', '.join(names)}) "
                                               f"but the item '{value}' is not {len(names)} values"), stmt.var)
                self.vars.update(zip(names, parts))
            else:
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
            if item.tree is not None and (item.kind == "py" or isinstance(
                    self.vars.get(item.text.partition("[")[0]), (ShArray, ShMap))):
                try:  # a Python value: each of its elements (a dict gives its keys)
                    found = evaluate(item.tree, python_names(self.vars, self.ctx.last_status),
                                     self.call_function, self.ask_input)
                    if not isinstance(found, (list, tuple, dict, set, str)):
                        raise ConditionError(f"{item.text} is {found!r}, which is not a list")
                    values += [as_variable(element) for element in found]
                except ConditionError as exc:
                    raise _Abort(PipelineError(1, 1, "for", f"for: {exc}", exc.status), item.text) from None
                continue
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
        """Run the block with its output collected, then feed that to the tail pipeline. A
        `< file`, `<<< text` or here-document on the tail is the block's input, not the tail's."""
        from shellcraft.pipeline import PipelineError, UnboundError, _run_parsed

        sh = stmt.mode == "sh"
        parsed, block_input = None, None
        if "<" in stmt.tail:
            try:
                parsed, block_input = self._block_input(stmt)
            except shparser.UnboundVariable:
                raise
            except (ParseError, PipelineError) as exc:
                self.ctx.last_status = 2 if isinstance(exc, ParseError) else getattr(exc, "status", 1)
                self.report(exc, stmt.tail)
                self.check_errexit(self.ctx.last_status, stmt.tail)
                return
        inner = _Runner(self.ctx, parent=self)
        inner.vars = self.vars  # the same scope: `for … done | sort` is not a subshell for variables
        inner.collect_only = True
        if block_input is not None:
            inner.stdin = block_input
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
        try:
            result = _run_parsed(stmt.tail, self.ctx, self.variables(), split=sh,
                                 substitute=self.substitute, glob=True, stdin=Input(text), allow_leading_pipe=True,
                                 live=self.shows_live, parsed=parsed)
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

    def _block_input(self, stmt: Piped) -> tuple[Any, Input | None]:
        """The tail parsed, and the block's input when it has one: `done < file`, `done <<< text`
        or a here-document. That input is taken off the tail, which would otherwise print it."""
        from shellcraft.pipeline import read_stdin_file

        pipeline = shparser.parse(stmt.tail, self.variables(), split=stmt.mode == "sh",
                                  substitute=self.substitute, glob=True, allow_leading_pipe=True)
        first = pipeline.segments[0] if pipeline and pipeline.segments else None
        if pipeline is None or first is None or first.name:
            return pipeline, None
        if pipeline.stdin_text is not None:
            text = pipeline.stdin_text
        elif pipeline.stdin_path is not None:
            text = read_stdin_file(pipeline)
        else:
            return pipeline, None
        pipeline.stdin_text = pipeline.stdin_path = None
        return pipeline, Input(text)

    def run_pipedinto(self, stmt: PipedInto) -> None:
        """`cmd | while read x; do …; done`: the command's output, collected, is what the block
        reads. The block runs on a copy of the variables, as a subshell would."""
        _, output = self.run_simple(stmt.source, live=False)
        head_status = self.ctx.last_status
        inner = _Runner(self.ctx, parent=self)
        inner.stdin = Input(to_text(output) if output is not None else "")
        try:
            inner.run_list([stmt.stmt])
        except (_Break, _Continue):
            pass
        except _Abort as exc:
            if not exc.reported:
                raise
        text = "".join(inner.collected)
        if text:
            self.show(text)
        if "pipefail" in self.ctx.options and head_status and not self.ctx.last_status:
            self.ctx.last_status = head_status

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
            names = python_names(self.vars, self.ctx.last_status)
            try:
                ok = bool(evaluate(cond.tree, names, self.call_function, self.ask_input))
            except ConditionError as exc:
                raise _Abort(PipelineError(1, 1, "if", f"({cond.text.strip()}): {exc}", exc.status),
                             cond.text) from None
            self.ctx.last_status = 0 if ok else 1
        return ok != cond.negate


@contextmanager
def _subshell_state(ctx: ShellContext) -> Iterator[None]:
    """What `( … )` leaves as it found it: the directory, the shell's variables, exports, options,
    functions and read-only / integer names, the environment, and a function's arguments."""
    cwd = os.getcwd()
    variables, exported = dict(ctx.variables), dict(ctx.exported)
    options, functions = set(ctx.options), dict(ctx.functions)
    readonly, integers = set(ctx.readonly), set(ctx.integers)
    environment = dict(os.environ)
    arguments = list(ctx.positional[-1]) if ctx.positional else None
    try:
        yield
    finally:
        ctx.variables.clear()  # always: an array that changed past its first element still reads as equal text
        ctx.variables.update(variables)
        for live, saved in ((ctx.exported, exported), (ctx.functions, functions)):
            if live != saved:
                live.clear()
                live.update(saved)
        for live_set, saved_set in ((ctx.options, options), (ctx.readonly, readonly), (ctx.integers, integers)):
            if live_set != saved_set:
                live_set.clear()
                live_set.update(saved_set)
        if arguments is not None and ctx.positional:
            ctx.positional[-1][:] = arguments
        if dict(os.environ) != environment:
            os.environ.clear()
            os.environ.update(environment)
        try:
            if os.getcwd() != cwd:
                os.chdir(cwd)
        except OSError:
            os.chdir(cwd)


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
