"""Check a script without running it: `source -n FILE` and `func check NAME`.

The file is read the way `source` reads it (startup.logical_lines, Python-style colon blocks
included) and each command is parsed (script.parse_script), so every line that won't parse is an
error. Then the statement tree is walked and the first word of every simple command is looked up
the way pipeline._call would look it up: a word nothing answers to is a warning. Nothing runs: no
command, no `$( … )`, no assignment, no function definition is stored.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any

from shellcraft import script
from shellcraft.parser import ParseError, find_closing_paren

MAX_INDENT_ERRORS = 50  # a file indented wrongly all the way down: stop reporting after this many
_MISSING = re.compile(r"missing '([a-z]+|\})'")
_ASSIGN_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\+?=")
_ALIAS = re.compile(r"alias\s+([A-Za-z0-9_.:+-]+)=")


@dataclass
class Problem:
    line: int
    message: str
    error: bool  # an error (it won't parse) or a warning (an unknown command)


@dataclass
class Report:
    name: str  # the file name shown before each line number
    lines: int = 0  # the file's lines
    problems: list[Problem] = field(default_factory=list)

    @property
    def errors(self) -> int:
        return sum(p.error for p in self.problems)

    @property
    def warnings(self) -> int:
        return len(self.problems) - self.errors

    @property
    def status(self) -> int:
        """0 clean, 1 warnings only, 2 any error."""
        return 2 if self.errors else 1 if self.problems else 0

    def text(self) -> str:
        out = [f"{self.name}:{p.line}: {'' if p.error else 'warning: '}{p.message}"
               for p in sorted(self.problems, key=lambda p: p.line)]
        if not self.problems:
            out.append(f"ok: {self.lines} line{'s' * (self.lines != 1)}, no problems")
        else:
            parts = [f"{n} {word}{'s' * (n != 1)}" for n, word in ((self.errors, "error"), (self.warnings, "warning"))
                     if n]
            total = len(self.problems)
            out.append(f"{total} problem{'s' * (total != 1)} ({', '.join(parts)})")
        return "\n".join(out) + "\n"


def check_text(text: str, name: str, ctx: Any, steps: bool = False) -> Report:
    """Check a file's text. With `steps` (a func), a line may start with `@` (show its output)."""
    report = Report(name, len(text.splitlines()))
    original = text.splitlines()
    lines = list(original)
    for _ in range(MAX_INDENT_ERRORS):
        commands = _commands(lines, report)
        if commands is None:
            return report
        # A line that won't parse inside a block is reported, blanked and the file read again,
        # so the rest of the block stays in it (not read as top-level lines, with warnings).
        found: list[Problem] = []
        parsed: list[tuple[_Span, script.Program]] = []
        bad = _parse_all(commands, original, steps, found, parsed, stop=True)
        if bad is None or not 0 < bad.line <= len(lines) or not lines[bad.line - 1].strip():
            if bad is not None:
                found, parsed = [], []
                _parse_all(commands, original, steps, found, parsed, stop=False)
            report.problems += found
            break
        report.problems.append(bad)
        lines[bad.line - 1] = ""
    else:
        return report

    walker = _Walker(ctx, report)
    for _, program in parsed:  # every function the file defines, for calls inside function bodies
        walker.collect(program.stmts)
    for span, program in parsed:
        walker.walk(program.stmts, span, top=True)
    return report


def _commands(lines: list[str], report: Report) -> list[tuple[int, str]] | None:
    """The file's commands, as logical_lines reads them. A wrongly indented line is reported and
    blanked (in `lines`), and the rest read; None after too many."""
    from shellcraft.indent import IndentError
    from shellcraft.startup import logical_lines

    for _ in range(MAX_INDENT_ERRORS):
        try:
            return logical_lines("\n".join(lines))
        except IndentError as exc:  # report it, blank that line and read the rest
            report.problems.append(Problem(exc.line, _indent_message(exc), True))
            if not 0 < exc.line <= len(lines) or not lines[exc.line - 1].strip():
                return logical_lines("\n".join(lines), colon_blocks=False)
            lines[exc.line - 1] = ""
    return None


def _parse_all(commands: list[tuple[int, str]], original: list[str], steps: bool, found: list[Problem],
               parsed: list[tuple[_Span, script.Program]], stop: bool) -> Problem | None:
    """Parse every command into `parsed`, problems into `found`. With `stop`, stops at a parse
    error on a line after the first of its command (inside a block) and returns it."""
    from shellcraft import ask

    starts = [number for number, _ in commands]
    for k, (number, line) in enumerate(commands):
        end = len(original)
        if k + 1 < len(starts):  # a block's closing `}` is numbered with its last body line
            end = starts[k + 1] - (commands[k + 1][1] != "}")
        span = _Span(original, number, end)
        if steps and line.startswith("@"):
            line = line[1:].lstrip()
        try:
            asking = ask.parse_ask_line(line)
            if asking is not None:  # `name = input("…")`
                if isinstance(asking[1], ask.FPrompt):  # input(f"…"): its fields are Python
                    script.compile_condition(asking[1], 0, "input", "prompt")
                continue
        except ask.AskError as exc:
            found.append(Problem(number, str(exc), True))
            continue
        except ParseError as exc:
            found.append(Problem(number, exc.message, True))
            continue
        before = len(found)
        program = _parse(line, span, found)
        if program is not None:
            parsed.append((span, program))
        elif stop and len(found) > before and found[-1].line > number and not found[-1].message.endswith("never ends"):
            return found.pop()
    return None


class _Span:
    """The file lines one command (a whole `for … done` is one) came from, to point at the line
    a word is on: logical_lines has joined them, so a position in the command is not a line."""

    def __init__(self, lines: list[str], start: int, end: int):
        self.lines, self.start, self.end = lines, start, max(start, end)
        self.cursor = start  # warnings come in the order of the text: look from the last one on

    def find(self, word: str) -> int:
        from shellcraft.parser import strip_comments

        if not word:
            return self.start
        pattern = re.compile(rf"(?<![\w-]){re.escape(word)}(?![\w-])")
        order = [*range(self.cursor, self.end + 1), *range(self.start, self.cursor)]
        for number in order:
            if 0 < number <= len(self.lines) and pattern.search(strip_comments(self.lines[number - 1])):
                self.cursor = number
                return number
        return self.start


def _indent_message(exc: Exception) -> str:
    return re.sub(r"^line \d+: ", "", str(exc))


def _parse(line: str, span: _Span, found: list[Problem]) -> script.Program | None:
    """The command's statement tree; a parse error is reported. A block still open at the end of
    the file is reported, then closed so the commands inside it are checked too."""
    try:
        return script.parse_script(line)
    except script.IncompleteError as exc:
        found.append(Problem(span.start, f"{exc.message}: the block that starts here never ends", True))
        text = line
        for _ in range(20):
            missing = _MISSING.search(exc.message)
            if missing is None:
                return None
            text = text.rstrip() + exc.join + missing.group(1)
            try:
                return script.parse_script(text)
            except script.IncompleteError as again:
                exc = again
            except ParseError:
                return None
        return None
    except ParseError as exc:
        at = line[exc.pos:].split(None, 1) if exc.pos is not None and 0 <= exc.pos < len(line) else []
        found.append(Problem(span.find(at[0]) if at else span.start, exc.message, True))
        return None


class _Walker:
    """Walks statement trees and warns about each command word nothing answers to."""

    def __init__(self, ctx: Any, report: Report):
        self.ctx = ctx
        self.report = report
        self.defined: set[str] = set()  # the functions the file defines, anywhere in it
        self.seen: set[str] = set()  # functions and aliases defined so far, walking top-level lines in order
        self.aliases: set[str] = set()

    def collect(self, stmts: list[Any]) -> None:
        for stmt in stmts:
            for node in _nodes(stmt):
                if isinstance(node, script.Function):
                    self.defined.add(node.name)

    def walk(self, stmts: list[Any], span: _Span, top: bool) -> None:
        for stmt in stmts:
            self._stmt(stmt, span, top)

    def _stmt(self, stmt: Any, span: _Span, top: bool) -> None:
        if isinstance(stmt, script.Simple):
            if not self._asks(stmt.text, span):
                self._simple(stmt.text, span, top)
        elif isinstance(stmt, script.Chain):
            self.walk(stmt.parts, span, top)
        elif isinstance(stmt, (script.For, script.Select)):
            self._items(stmt.items, span, top)
            self.walk(stmt.body, span, top)
        elif isinstance(stmt, script.If):
            for cond, body in stmt.branches:
                self._cond(cond, span, top)
                self.walk(body, span, top)
            self.walk(stmt.orelse or [], span, top)
        elif isinstance(stmt, script.While):
            self._cond(stmt.cond, span, top)
            self.walk(stmt.body, span, top)
        elif isinstance(stmt, script.Case):
            self._substitutions(stmt.word, span, top)
            for _, body in stmt.clauses:
                self.walk(body, span, top)
        elif isinstance(stmt, script.Piped):
            self._stmt(stmt.stmt, span, top)
            self._simple(stmt.tail, span, top, leading_pipe=True)
        elif isinstance(stmt, script.Try):
            for block in (stmt.body, stmt.handler, stmt.final):
                self.walk(block or [], span, top)
        elif isinstance(stmt, script.Function):
            self.seen.add(stmt.name)  # defined from here on; its body runs later, when it's called
            self.walk(stmt.body, span, top=False)

    def _asks(self, text: str, span: _Span) -> bool:
        """`name = input(…)` inside a block: not a command. A bad prompt is an error."""
        from shellcraft import ask

        try:
            asking = ask.parse_ask_line(text)
            if asking is not None and isinstance(asking[1], ask.FPrompt):
                script.compile_condition(asking[1], 0, "input", "prompt")
        except ask.AskError as exc:
            self.report.problems.append(Problem(span.find("input"), str(exc), True))
            return True
        except ParseError as exc:
            self.report.problems.append(Problem(span.find("input"), exc.message, True))
            return True
        return asking is not None

    def _cond(self, cond: script.Cond, span: _Span, top: bool) -> None:
        if cond.kind == "cmd":
            self._simple(cond.text, span, top)
        elif cond.kind == "list":
            self.walk(cond.stmts, span, top)

    def _items(self, items: list[script.Item], span: _Span, top: bool) -> None:
        for item in items:
            if item.kind == "cmd":  # -py `(command)`
                self._simple(item.text, span, top)
            else:
                self._substitutions(item.text, span, top)

    def _simple(self, text: str, span: _Span, top: bool, leading_pipe: bool = False) -> None:
        from shellcraft.parser import parse

        self._substitutions(text, span, top)
        try:
            pipeline = parse(text, allow_leading_pipe=leading_pipe)  # no variables: `$x` stays as typed
        except ParseError:
            return
        if pipeline is None:
            return
        for cmd in pipeline.segments:
            word = cmd.name
            if word == "alias":
                for arg in cmd.args:
                    if m := _ALIAS.match(f"alias {arg}"):
                        self.aliases.add(m.group(1))
            problem = self._unknown(word, top)
            if problem:
                self.report.problems.append(Problem(span.find(word), problem, False))

    def _substitutions(self, text: str, span: _Span, top: bool) -> None:
        """Check the commands inside each `$( … )` of a word or a command line."""
        i, quote = 0, False
        while i < len(text):
            ch = text[i]
            if ch == "\\" and not quote:
                i += 2
                continue
            if ch == "'":
                quote = not quote
            elif not quote and text.startswith("$(", i) and not text.startswith("$((", i):
                close = find_closing_paren(text, i + 2)
                if close is None:
                    return
                try:
                    program = script.parse_script(text[i + 2:close])
                except ParseError:
                    program = None
                if program is not None:
                    self.walk(program.stmts, span, top)
                i = close
            i += 1

    def _unknown(self, word: str, top: bool) -> str | None:
        """Why `word` won't run as a command, or None when something answers to it."""
        from shellcraft import aliases, funcs
        from shellcraft.builtins import BUILTINS
        from shellcraft.parser import Command
        from shellcraft.pipeline import SPECIAL_BUILTINS, _resolve_system

        ctx = self.ctx
        if not word or word == "}" or word.startswith("$") or _ASSIGN_WORD.match(word):  # `}`: after a bad header
            return None
        bypass = word.startswith("\\") and len(word) > 1
        name = word[1:] if bypass else word
        if not bypass and name not in SPECIAL_BUILTINS:
            if name in self.seen or name in ctx.functions:
                return None
            if name in self.defined:
                if not top:
                    return None
                return f"{name} is called before the file defines it"
            if ctx.allow_aliases and (name in self.aliases or name in aliases.get_all(ctx.config)):
                return None
        if name in BUILTINS or ctx.registry.get(name) is not None:
            return None
        if not bypass and funcs.as_command(ctx, name) is not None:
            return None
        if _resolve_system(Command(name, [])) is not None:
            if ctx.allow_system:
                return None
            return f"{name} is an OS program, and OS commands are off (settings system_commands on)"
        if ctx.registry.is_disabled(name):
            return f"module '{name}' is disabled (modules enable {name})"
        known = [*BUILTINS, *ctx.registry.names(), *self.defined, *ctx.functions]
        hint = difflib.get_close_matches(name, known, n=1)
        return f"unknown command '{name}'" + (f" (did you mean '{hint[0]}'?)" if hint else "")


def _nodes(stmt: Any) -> list[Any]:
    """`stmt` and every statement nested in it."""
    out = [stmt]
    children: list[Any] = []
    if isinstance(stmt, script.Chain):
        children = list(stmt.parts)
    elif isinstance(stmt, (script.For, script.Select, script.Function)):
        children = list(stmt.body)
    elif isinstance(stmt, script.If):
        for cond, body in stmt.branches:
            children += [*cond.stmts, *body]
        children += stmt.orelse or []
    elif isinstance(stmt, script.While):
        children = [*stmt.cond.stmts, *stmt.body]
    elif isinstance(stmt, script.Case):
        for _, body in stmt.clauses:
            children += body
    elif isinstance(stmt, script.Piped):
        children = [stmt.stmt]
    elif isinstance(stmt, script.Try):
        children = [*stmt.body, *(stmt.handler or []), *(stmt.final or [])]
    for child in children:
        out += _nodes(child)
    return out
