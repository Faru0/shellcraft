"""Scripts: `for` loops, `if` conditions and statements separated by `;` or new lines.

    script  := stmt ( (';' | NEWLINE) stmt )*
    stmt    := for | if | 'break' | 'continue' | pipeline
    for     := 'for' NAME 'in' item* block
    item    := WORD | N..M[..STEP] | GLOB | '(' pipeline ')' | '$(' pipeline ')'
    if      := 'if' cond block ( 'elif' cond block )* ( 'else' block )?
    cond    := ['!'] ( '(' python-expression ')' | pipeline )
    block   := '{' script '}'

ShellCraft parses all of this itself, so a loop body needs no escaping: `$name` expands to the
loop variable as one argument whatever it contains, and quotes mean what they mean on a plain
command line. A `( … )` condition is a small, safe subset of Python, checked node by node and
evaluated by walking the tree (never `eval`), so it can't import, call arbitrary functions or
touch attributes other than a list of string methods. Any other condition is a pipeline, true
when its exit status is 0.

`{` opens a block when it starts a word; `}` closes one when it is a word of its own (`echo }`
inside a block needs quotes: `echo '}'`). Outside a block a plain command line is parsed exactly
as before, so `echo {a}` and `echo }` still print their arguments.
"""

from __future__ import annotations

import ast
import glob
import operator
import os
import re
from dataclasses import dataclass, field
from typing import Any, Union

from core import parser as shparser
from core.context import ShellContext, to_text
from core.parser import ParseError

KEYWORDS = ("for", "if", "elif", "else", "break", "continue")
MAX_RANGE = 100_000  # items a single N..M range may produce
MAX_SEQUENCE = 100_000  # longest string / list a condition may build with `*`
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_RANGE = re.compile(r"^(-?\d+)\.\.(-?\d+)(?:\.\.(\d+))?$")


class IncompleteError(ParseError):
    """The text ends inside a block, a `( … )` or before a `{`: the shell asks for another line.
    `join` is what goes between this text and the next line (" ; " inside a block, else " ")."""

    def __init__(self, message: str, pos: int, join: str):
        super().__init__(message, pos)
        self.join = join


# ── syntax tree ──────────────────────────────────────────────────────────────

@dataclass
class Simple:
    text: str  # a pipeline, parsed again with variables when it runs
    pos: int


@dataclass
class Item:
    kind: str  # "word" or "cmd"
    text: str
    pos: int


@dataclass
class For:
    var: str
    items: list[Item]
    body: list[Stmt]
    pos: int


@dataclass
class Cond:
    kind: str  # "expr" (Python) or "cmd" (a pipeline's exit status)
    text: str
    pos: int
    negate: bool = False
    tree: ast.Expression | None = None


@dataclass
class If:
    branches: list[tuple[Cond, list[Stmt]]]
    orelse: list[Stmt] | None
    pos: int


@dataclass
class Jump:
    kind: str  # "break" or "continue"
    pos: int


Stmt = Union[Simple, For, If, Jump]


@dataclass
class Program:
    stmts: list[Stmt] = field(default_factory=list)

    @property
    def is_simple(self) -> bool:
        """One plain pipeline: run exactly as a command line always was."""
        return len(self.stmts) == 1 and isinstance(self.stmts[0], Simple)


# ── parser ───────────────────────────────────────────────────────────────────

def parse_script(text: str) -> Program | None:
    """Parse a command line or script; None when it holds no statement. Raises ParseError
    (IncompleteError when more input could complete it)."""
    program = Program(_Parser(text).parse_list(closing=False))
    return program if program.stmts else None


def incomplete(text: str) -> str | None:
    """The joiner for a continuation line when `text` is an unfinished script, else None."""
    try:
        parse_script(text)
    except IncompleteError as exc:
        return exc.join
    except ParseError:
        return None
    return None


class _Parser:
    def __init__(self, text: str):
        self.s = text
        self.n = len(text)
        self.i = 0
        self.depth = 0  # open blocks
        self.loops = 0  # enclosing for loops (for break / continue)

    # helpers
    def _error(self, message: str, pos: int | None = None) -> ParseError:
        return ParseError(message, self.i if pos is None else pos)

    def _incomplete(self, message: str, join: str) -> IncompleteError:
        return IncompleteError(message, self.n, join)

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

    # statements
    def parse_list(self, closing: bool) -> list[Stmt]:
        stmts: list[Stmt] = []
        while True:
            self._skip_space(separators=True)
            if self._at_end():
                if not closing:
                    return stmts
                last = len(self.s.rstrip()) - 1
                if self.s[last] == "}" and not self._is_close(last):  # `{break}`: meant to close
                    raise self._error("missing '}' — a closing brace must be a word of its own, "
                                      "with a space before it: { echo $x }", last)
                raise self._incomplete("missing '}'", " ; ")
            if closing and self._is_close(self.i):
                self.i += 1
                return stmts
            stmts.append(self._statement())
            self._skip_blanks()
            if self._at_end() or self.s[self.i] in ";\n" or (closing and self._is_close(self.i)):
                continue
            word = self._bare_word(self.i)
            raise self._error(f"expected ';' or a new line before '{word}' "
                              "(a loop's or an if's output can't be piped or redirected)")

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
        if word in ("elif", "else"):
            raise self._error(f"'{word}' without a matching if (put it on the same line as the "
                              "if's closing '}')", start)
        return self._simple(stop_at_open=False)

    def _simple(self, stop_at_open: bool) -> Simple:
        start, quote = self.i, None
        while self.i < self.n:
            ch = self.s[self.i]
            if quote:
                if quote == '"' and ch == "\\" and self.s.startswith('\\"', self.i):
                    self.i += 2
                    continue
                if ch == quote:
                    quote = None
                self.i += 1
                continue
            if ch in ("'", '"'):
                quote = ch
            elif ch in ";\n":
                break
            elif self.depth and self._is_close(self.i):
                break
            elif stop_at_open and self._is_open(self.i):
                break
            self.i += 1
        text = self.s[start:self.i].rstrip()
        if not text:
            raise self._error("expected a command", start)
        _check_pipeline(text, start)
        return Simple(text, start)

    def _paren(self) -> tuple[str, int]:
        """At `(` or `$(`: the text inside the matching `)` and where it starts."""
        self.i += 2 if self.s[self.i] == "$" else 1
        start, level, quote = self.i, 1, None
        while self.i < self.n:
            ch = self.s[self.i]
            if quote:
                if ch == "\\":
                    self.i += 2
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
                    self.i += 1
                    return self.s[start:self.i - 1], start
            self.i += 1
        raise self._incomplete("missing ')'", " ")

    def _block(self, what: str) -> list[Stmt]:
        self._skip_space()
        if self._at_end():
            raise self._incomplete(f"{what}: missing '{{'", " ")
        if self.s[self.i] != "{":
            raise self._error(f"{what}: expected '{{'")
        self.i += 1
        self.depth += 1
        try:
            return self.parse_list(closing=True)
        finally:
            self.depth -= 1

    def _for(self) -> For:
        pos = self.i
        self.i += 3
        self._skip_space()
        match = _NAME.match(self.s, self.i)
        if not match:
            if self._at_end():
                raise self._incomplete("for: missing a variable name", " ")
            raise self._error("for: expected a variable name (letters, digits and _)")
        var = match.group()
        self.i = match.end()
        self._skip_space()
        if self._at_end():
            raise self._incomplete("for: missing 'in'", " ")
        if self._bare_word(self.i) != "in":
            raise self._error(f"for: expected 'in' after '{var}'")
        self.i += 2
        items: list[Item] = []
        while True:
            self._skip_space()
            if self._at_end():
                raise self._incomplete("for: missing '{'", " ")
            ch = self.s[self.i]
            if ch == "{" and self._word_start(self.i):
                break
            if ch == ";":
                raise self._error("for: expected '{' after the items (no ';' before it)")
            if ch == "(" or self.s.startswith("$(", self.i):
                text, start = self._paren()
                _check_pipeline(text, start)
                items.append(Item("cmd", text, start))
                continue
            start = self.i
            word = self._item_word()
            try:
                tokens = shparser.tokenize(word)
            except ParseError as exc:
                raise ParseError(f"for: {exc.message}", start + (exc.pos or 0)) from None
            if any(t.op for t in tokens):
                raise self._error("for: an item can't contain '|' or '>' — to loop over a command's "
                                  "output, put it in parentheses: for x in (cat file) { … }", start)
            items.append(Item("word", word, start))
        self.loops += 1
        try:
            body = self._block("for")
        finally:
            self.loops -= 1
        return For(var, items, body, pos)

    def _item_word(self) -> str:
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
            elif ch in " \t\r\n;":
                break
            self.i += 1
        return self.s[start:self.i]

    def _if(self) -> If:
        pos = self.i
        self.i += 2
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
        return If(branches, orelse, pos)

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


def _check_pipeline(text: str, offset: int) -> None:
    """Syntax-check a pipeline before anything runs, with the error position in the whole line."""
    try:
        shparser.parse(text)
    except ParseError as exc:
        raise ParseError(exc.message, None if exc.pos is None else offset + exc.pos) from None


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
    def __init__(self, ctx: ShellContext):
        self.ctx = ctx
        self.vars: dict[str, str] = {}
        self.collected: list[str] = []

    def variables(self) -> dict[str, str]:
        return {**self.vars, "?": str(self.ctx.last_status)}

    def show(self, value: Any) -> None:
        if self.ctx.emit is not None:
            self.ctx.emit(value)
            return
        text = to_text(value)
        if text:
            self.collected.append(text if text.endswith("\n") else text + "\n")

    def report(self, exc: Exception, text: str) -> None:
        from core.pipeline import PipelineError

        if self.ctx.emit_error is not None:
            self.ctx.emit_error(exc, text)
        elif isinstance(exc, PipelineError):
            self.collected.append(f"✗ {exc.name}: {exc.message}\n")
        elif isinstance(exc, ParseError):
            self.collected.append(f"✗ parse error: {exc.message}\n")
        else:
            self.collected.append(f"✗ {exc}\n")

    def run_list(self, stmts: list[Stmt]) -> None:
        for stmt in stmts:
            if isinstance(stmt, Simple):
                _, output = self.run_simple(stmt.text)
                if output is not None:
                    self.show(output)
            elif isinstance(stmt, For):
                self.run_for(stmt)
            elif isinstance(stmt, If):
                self.run_if(stmt)
            elif stmt.kind == "break":
                raise _Break
            else:
                raise _Continue

    def run_simple(self, text: str) -> tuple[int, Any]:
        """Run one pipeline and show its output; returns (status, output)."""
        from core.pipeline import PipelineError, _run_parsed

        try:
            result = _run_parsed(text, self.ctx, self.variables())
        except (ParseError, PipelineError) as exc:
            self.report(exc, text)
            return self.ctx.last_status, None
        output = None if result is None else result.output
        return self.ctx.last_status, output

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
