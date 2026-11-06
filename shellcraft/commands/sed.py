"""sed: a stream editor. The core of GNU sed, portable: s d p a i c y q Q = and { }, line / $ / regex /
range addresses, -n -e -f -E -r -i -s. Patterns are POSIX basic (or extended with -E) regular
expressions, translated onto Python's `re`. No hold space or branching (h g x n N b t ...)."""

from __future__ import annotations

import os
import re
import tempfile
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generator, Iterable, Iterator

from shellcraft import streams
from shellcraft.builtins import builtin
from shellcraft.commands._io import user_path
from shellcraft.modkit import ModuleError

if TYPE_CHECKING:
    from shellcraft.context import ShellContext


class _SedError(Exception):
    """A problem in a pattern; the script parser adds the position."""


# ── regular expressions: POSIX (basic or extended) to Python ─────────────────

_CLASSES = {
    "alpha": "a-zA-Z", "digit": "0-9", "alnum": "a-zA-Z0-9", "upper": "A-Z", "lower": "a-z",
    "space": r" \t\n\r\f\v", "blank": r" \t", "punct": r"!-/:-@\[-`{-~", "print": r" -~", "graph": r"!-~",
    "cntrl": r"\x00-\x1f\x7f", "xdigit": "0-9A-Fa-f",
}


def _bracket(p: str, i: int) -> tuple[str, int]:
    """The bracket expression starting at p[i] == '[': (Python text, index after it)."""
    j = i + 1
    out = ["["]
    if j < len(p) and p[j] == "^":
        out.append("^")
        j += 1
    first = True
    while j < len(p):
        c = p[j]
        if c == "]" and not first:
            out.append("]")
            return "".join(out), j + 1
        first = False
        if c == "[" and j + 1 < len(p) and p[j + 1] in ":.=":
            kind = p[j + 1]
            end = p.find(kind + "]", j + 2)
            if end != -1:
                name = p[j + 2:end]
                if kind == ":":
                    if name not in _CLASSES:
                        raise _SedError("invalid character class")
                    out.append(_CLASSES[name])
                else:
                    out.append(re.escape(name))
                j = end + 2
                continue
        if c == "\\":
            nxt = p[j + 1] if j + 1 < len(p) else ""
            if nxt in ("n", "t"):
                out.append("\\" + nxt)
                j += 2
            else:
                out.append("\\\\")
                j += 1
            continue
        out.append("\\" + c if c in "[]&|~^" else c)
        j += 1
    raise _SedError("unterminated `[' in a regular expression")


def _translate(p: str, ere: bool) -> str:
    """A POSIX regular expression as Python `re` syntax."""
    out: list[str] = []
    i, n = 0, len(p)
    start = True  # at the start of an expression: in a basic one `*` is literal here
    while i < n:
        c = p[i]
        if c == "[":
            text, i = _bracket(p, i)
            out.append(text)
            start = False
            continue
        if c == "\\":
            if i + 1 >= n:
                raise _SedError("trailing backslash (\\)")
            d = p[i + 1]
            i += 2
            if not ere and d in "(){}|+?":
                out.append(d)
                start = d in "(|"
                continue
            start = False
            if d in "123456789":
                out.append("\\" + d)
            elif d == "n":
                out.append("\\n")
            elif d == "t":
                out.append("\\t")
            elif d in "wWsSbB":
                out.append("\\" + d)
            elif d == "<":
                out.append(r"\b(?=\w)")
            elif d == ">":
                out.append(r"\b(?<=\w)")
            elif d == "`":
                out.append(r"\A")
            elif d == "'":
                out.append(r"\Z")
            else:
                out.append(re.escape(d))
            continue
        i += 1
        if c == "*":
            out.append("\\*" if start and not ere else "*")
            start = False
        elif c == "^":
            if ere or start:
                out.append("^")  # an anchor (in a basic expression `*` after it is still literal)
            else:
                out.append("\\^")
                start = False
        elif c == "$":
            at_end = i == n or p.startswith(("\\)", "\\|"), i)
            out.append("\\Z" if ere or at_end else "\\$")
            start = False
        elif c in "(){}|+?":
            if ere:
                out.append(c)
                start = c in "(|"
            else:
                out.append("\\" + c)
                start = False
        elif c == ".":
            out.append(".")
            start = False
        else:
            out.append(re.escape(c))
            start = False
    return "".join(out)


def _compile(pattern: str, ere: bool, icase: bool) -> re.Pattern[str]:
    flags = re.S | (re.I if icase else 0)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return re.compile(_translate(pattern, ere), flags)
    except re.error as exc:
        raise _SedError(f"invalid regular expression: {exc}") from None


@dataclass
class _Rx:
    """A pattern; `compiled` is None for the empty pattern, which means the last one used."""
    compiled: re.Pattern[str] | None


# ── the script ───────────────────────────────────────────────────────────────

_UNSUPPORTED = set("hHgGxnNDPbtT:lrRwWezFv")


@dataclass(eq=False)
class _Cmd:
    name: str
    a1: tuple[Any, ...] | None = None
    a2: tuple[Any, ...] | None = None
    negate: bool = False
    rx: _Rx | None = None  # s
    repl: list[Any] = field(default_factory=list)  # s: text, group numbers, case operators
    glob: bool = False
    print_: bool = False
    nth: int = 1
    text: str = ""  # a i c
    code: int = 0  # q Q
    table: dict[int, int] = field(default_factory=dict)  # y
    end: int = 0  # {: index of its }
    active: bool = False  # inside a range
    stop_at: int = 0  # the line a +N range ends on


class _Parser:
    def __init__(self, src: str, ere: bool):
        self.src, self.ere, self.i = src, ere, 0
        self.cmds: list[_Cmd] = []

    def fail(self, message: str) -> ModuleError:
        return ModuleError(f"sed: -e expression #1, char {self.i}: {message}")

    def peek(self) -> str:
        return self.src[self.i] if self.i < len(self.src) else ""

    def skip(self, chars: str = " \t") -> None:
        while self.i < len(self.src) and self.src[self.i] in chars:
            self.i += 1

    def number(self) -> int:
        j = self.i
        while self.peek().isdigit():
            self.i += 1
        return int(self.src[j:self.i])

    def delimited(self, delim: str, regex: bool, what: str) -> str:
        """Text up to the next unescaped `delim`; `\\delim` becomes the delimiter, other escapes stay."""
        out: list[str] = []
        src = self.src
        while self.i < len(src):
            c = src[self.i]
            if c == delim:
                self.i += 1
                return "".join(out)
            if c == "\\" and self.i + 1 < len(src):
                d = src[self.i + 1]
                out.append(d if d == delim and delim not in "n" else c + d)
                self.i += 2
                continue
            if c == "[" and regex:
                try:
                    _, j = _bracket(src, self.i)
                except _SedError:
                    j = self.i + 1
                out.append(src[self.i:j])
                self.i = j
                continue
            out.append(c)
            self.i += 1
        raise self.fail(f"unterminated {what}")

    def regex(self, text: str, icase: bool) -> _Rx:
        if text == "":
            return _Rx(None)
        try:
            return _Rx(_compile(text, self.ere, icase))
        except _SedError as exc:
            raise self.fail(str(exc)) from None

    def address(self) -> tuple[Any, ...] | None:
        c = self.peek()
        if c.isdigit():
            first = self.number()
            if self.peek() == "~":
                self.i += 1
                return ("step", first, self.number() if self.peek().isdigit() else 0)
            return ("line", first)
        if c == "$":
            self.i += 1
            return ("last",)
        if c == "/" or c == "\\":
            self.i += 1
            delim = "/"
            if c == "\\":
                delim = self.peek()
                if not delim:
                    raise self.fail("unexpected end of script")
                self.i += 1
            text = self.delimited(delim, True, "address regex")
            icase = False
            while self.peek() in ("I", "M") and self.peek():
                icase = icase or self.peek() == "I"
                self.i += 1
            return ("re", self.regex(text, icase))
        return None

    def end_of_command(self) -> None:
        self.skip()
        c = self.peek()
        if c in (";", "\n"):
            self.i += 1
        elif c and c not in "}#":
            raise self.fail("extra characters after command")

    def text_argument(self) -> str:
        self.skip()
        if self.peek() == "\\":
            self.i += 1
            self.skip()
            if self.peek() == "\n":
                self.i += 1
        if self.i >= len(self.src):
            raise self.fail("expected \\ after `a', `c' or `i'")
        out: list[str] = []
        src = self.src
        while self.i < len(src):
            c = src[self.i]
            if c == "\\" and self.i + 1 < len(src):
                out.append(src[self.i + 1])
                self.i += 2
            elif c == "\n":
                self.i += 1
                break
            else:
                out.append(c)
                self.i += 1
        return "".join(out)

    def parse(self) -> list[_Cmd]:
        stack: list[int] = []
        src = self.src
        while True:
            self.skip(" \t\n;")
            if self.i >= len(src):
                break
            if src[self.i] == "#":
                while self.i < len(src) and src[self.i] != "\n":
                    self.i += 1
                continue
            a1 = self.address()
            a2 = None
            self.skip()
            if a1 is not None and self.peek() == ",":
                self.i += 1
                self.skip()
                if self.peek() in ("+", "~"):
                    kind = "plus" if self.peek() == "+" else "mult"
                    self.i += 1
                    if not self.peek().isdigit():
                        raise self.fail("expected a number after `,+' or `,~'")
                    a2 = (kind, self.number())
                else:
                    a2 = self.address()
                    if a2 is None:
                        raise self.fail("unexpected `,'")
            self.skip()
            negate = False
            while self.peek() == "!":
                negate = True
                self.i += 1
                self.skip()
            name = self.peek()
            if not name:
                raise self.fail("missing command")
            self.i += 1
            cmd = _Cmd(name, a1, a2, negate)
            if name == "{":
                stack.append(len(self.cmds))
            elif name == "}":
                if a1 is not None:
                    raise self.fail("} doesn't want any addresses")
                if not stack:
                    raise self.fail("unexpected `}'")
                self.cmds[stack.pop()].end = len(self.cmds)
                self.end_of_command()
            elif name in "dp=":
                self.end_of_command()
            elif name in "aic":
                cmd.text = self.text_argument()
            elif name in "qQ":
                self.skip()
                if self.peek().isdigit():
                    cmd.code = self.number()
                self.end_of_command()
            elif name == "y":
                self.parse_y(cmd)
            elif name == "s":
                self.parse_s(cmd)
            elif name in _UNSUPPORTED:
                raise self.fail(f"command `{name}' is not supported (ShellCraft sed has s d p a i c y q Q = and {{}})")
            else:
                raise self.fail(f"unknown command: `{name}'")
            self.cmds.append(cmd)
        if stack:
            raise self.fail("unmatched `{'")
        return self.cmds

    def parse_y(self, cmd: _Cmd) -> None:
        delim = self.peek()
        if not delim or delim in "\n\\":
            raise self.fail("unterminated `y' command")
        self.i += 1
        left = self.unescape_y(self.delimited(delim, False, "`y' command"))
        right = self.unescape_y(self.delimited(delim, False, "`y' command"))
        if len(left) != len(right):
            raise self.fail("strings for `y' command are different lengths")
        cmd.table = str.maketrans(left, right)
        self.end_of_command()

    def unescape_y(self, text: str) -> str:
        return re.sub(r"\\(.)", lambda m: "\n" if m.group(1) == "n" else m.group(1), text, flags=re.S)

    def parse_s(self, cmd: _Cmd) -> None:
        delim = self.peek()
        if not delim or delim in "\n\\":
            raise self.fail("unterminated `s' command")
        self.i += 1
        pattern = self.delimited(delim, True, "`s' command")
        replacement = self.delimited(delim, False, "`s' command")
        icase = False
        while True:
            c = self.peek()
            if c == "g":
                cmd.glob = True
            elif c == "p":
                cmd.print_ = True
            elif c in ("i", "I"):
                icase = True
            elif c in ("m", "M"):
                pass
            elif c.isdigit() and c != "0":
                cmd.nth = self.number()
                continue
            else:
                break
            self.i += 1
        c = self.peek()
        if c and c not in " \t;\n}#":
            raise self.fail("unknown option to `s'" if c not in "we" else f"the `{c}' flag of `s' is not supported")
        cmd.rx = self.regex(pattern, icase)
        cmd.repl = self.replacement(replacement)
        if cmd.rx.compiled is not None:
            top = max((t for t in cmd.repl if isinstance(t, int)), default=0)
            if top > cmd.rx.compiled.groups:
                raise self.fail(f"invalid reference \\{top} on `s' command's RHS")
        self.end_of_command()

    @staticmethod
    def replacement(text: str) -> list[Any]:
        """Literal text (str), group numbers (int; 0 is the whole match) and case operators (("case", c))."""
        tokens: list[Any] = []
        buf: list[str] = []

        def flush() -> None:
            if buf:
                tokens.append("".join(buf))
                buf.clear()

        i = 0
        while i < len(text):
            c = text[i]
            if c == "\\" and i + 1 < len(text):
                d = text[i + 1]
                i += 2
                if d.isdigit():
                    flush()
                    tokens.append(int(d))
                elif d in "ULEul":
                    flush()
                    tokens.append(("case", d))
                elif d == "n":
                    buf.append("\n")
                elif d == "t":
                    buf.append("\t")
                else:
                    buf.append(d)  # \& \\ \/ and a backslash-newline
            elif c == "&":
                flush()
                tokens.append(0)
                i += 1
            else:
                buf.append(c)
                i += 1
        flush()
        return tokens


# ── running it ───────────────────────────────────────────────────────────────

def _expand(tokens: list[Any], match: re.Match[str]) -> str:
    out: list[str] = []
    mode = ""  # "U" or "L" until \E
    once = ""  # "u" or "l": the next character only

    def put(text: str) -> None:
        nonlocal once
        if not text:
            return
        if mode == "U":
            text = text.upper()
        elif mode == "L":
            text = text.lower()
        if once:
            text = (text[0].upper() if once == "u" else text[0].lower()) + text[1:]
            once = ""
        out.append(text)

    for token in tokens:
        if isinstance(token, str):
            put(token)
        elif isinstance(token, int):
            put(match.group(token) or "")
        elif token[1] in "UL":
            mode = token[1]
        elif token[1] == "E":
            mode = ""
        else:
            once = token[1]
    return "".join(out)


class _Engine:
    def __init__(self, cmds: list[_Cmd], quiet: bool, exact: bool = False):
        # exact: keep a missing final newline (for -i, which rewrites a file); output that flows
        # to the screen or a pipe is made of whole lines, so it always ends them.
        self.cmds, self.quiet, self.exact = cmds, quiet, exact
        self.needs_last = any(c.a1 and c.a1[0] == "last" or c.a2 and c.a2[0] == "last" for c in cmds)
        self.last: re.Pattern[str] | None = None
        self.stopped = False
        self.code = 0
        self.reset()

    def reset(self) -> None:
        """Line numbers and ranges start over (a new file under -s or -i)."""
        self.lineno = 0
        self.missing_newline = False
        for c in self.cmds:
            c.active = False

    def emit(self, text: str, newline: bool = True) -> str:
        """`text` as output; a line that had no newline gets one only when more output follows."""
        if not self.exact:
            return text + "\n"
        before = "\n" if self.missing_newline else ""
        self.missing_newline = not newline
        return before + text + ("\n" if newline else "")

    def search(self, rx: _Rx, text: str) -> re.Match[str] | None:
        pattern = rx.compiled or self.last
        if pattern is None:
            raise ModuleError("sed: no previous regular expression")
        self.last = pattern
        return pattern.search(text)

    def hit(self, a: tuple[Any, ...], text: str, last: bool) -> bool:
        kind = a[0]
        if kind == "line":
            return self.lineno == a[1]
        if kind == "last":
            return last
        if kind == "re":
            return self.search(a[1], text) is not None
        first, step = a[1], a[2]  # first~step
        return self.lineno == first if step == 0 else self.lineno >= first and (self.lineno - first) % step == 0

    def selected(self, c: _Cmd, text: str, last: bool) -> bool:
        if c.a1 is None:
            chosen = True
        elif c.a2 is None:
            chosen = self.hit(c.a1, text, last)
        elif c.active:
            chosen = True
            kind = c.a2[0]
            if kind == "line":
                c.active = self.lineno < c.a2[1]
            elif kind == "re":
                c.active = self.search(c.a2[1], text) is None
            elif kind == "last":
                c.active = not last
            elif kind == "plus":
                c.active = self.lineno < c.stop_at
            else:  # mult
                c.active = self.lineno % c.a2[1] != 0
        elif self.hit(c.a1, text, last):
            chosen = True
            kind = c.a2[0]
            if kind == "line":
                c.active = c.a2[1] > self.lineno
            elif kind == "re":
                c.active = True
            elif kind == "last":
                c.active = not last
            elif kind == "plus":
                c.stop_at = self.lineno + c.a2[1]
                c.active = c.a2[1] > 0
            else:  # mult
                c.active = c.a2[1] > 0 and self.lineno % c.a2[1] != 0
        else:
            chosen = False
        return chosen != c.negate

    def substitute(self, c: _Cmd, text: str) -> str | None:
        """`text` with the substitution done, or None when nothing matched."""
        assert c.rx is not None
        pattern = c.rx.compiled or self.last
        if pattern is None:
            raise ModuleError("sed: no previous regular expression")
        self.last = pattern
        pieces: list[str] = []
        done, count, previous_end = 0, 0, -1
        for m in pattern.finditer(text):
            if m.start() == m.end() == previous_end:
                continue  # an empty match right after the one before is not another match
            previous_end = m.end()
            count += 1
            if count < c.nth:
                continue
            pieces.append(text[done:m.start()])
            pieces.append(_expand(c.repl, m))
            done = m.end()
            if not c.glob:
                break
        if not pieces:
            return None
        pieces.append(text[done:])
        return "".join(pieces)

    def run(self, lines: Iterable[str]) -> Iterator[str]:
        """Each input line (with its newline, but for a last one without) through the script."""
        source = iter(lines)
        upcoming = next(source, None) if self.needs_last else None
        while not self.stopped:
            if self.needs_last:
                raw, upcoming = upcoming, next(source, None)
                if raw is None:
                    return
                last = upcoming is None
            else:
                raw = next(source, None)
                if raw is None:
                    return
                last = False
            self.lineno += 1
            had_newline = raw.endswith("\n")
            text = raw[:-1] if had_newline else raw
            yield from self.cycle(text, had_newline, last)

    def cycle(self, text: str, had_newline: bool, last: bool) -> Iterator[str]:
        later: list[str] = []  # `a` text, written when the cycle ends
        printing = not self.quiet
        cmds = self.cmds
        pc = 0
        while pc < len(cmds):
            c = cmds[pc]
            name = c.name
            if name == "}":
                pc += 1
                continue
            if not self.selected(c, text, last):
                pc = c.end + 1 if name == "{" else pc + 1
                continue
            pc += 1
            if name == "s":
                changed = self.substitute(c, text)
                if changed is not None:
                    text = changed
                    if c.print_:
                        yield self.emit(text, had_newline)
            elif name == "d":
                printing = False
                break
            elif name == "p":
                yield self.emit(text, had_newline)
            elif name == "a":
                later.append(c.text)
            elif name == "i":
                yield self.emit(c.text)
            elif name == "c":
                if c.a2 is None or not c.active or c.negate:
                    yield self.emit(c.text)
                printing = False
                break
            elif name == "y":
                text = text.translate(c.table)
            elif name == "=":
                yield self.emit(str(self.lineno))
            elif name in "qQ":
                self.stopped = True
                self.code = c.code
                printing = printing and name == "q"
                if name == "Q":
                    later.clear()
                break
        if printing:
            yield self.emit(text, had_newline)
        for extra in later:
            yield self.emit(extra)


# ── input and options ────────────────────────────────────────────────────────

def _split(text: str) -> list[str]:
    """Lines with their newlines (the last one has none when the text doesn't end with one)."""
    parts = text.split("\n")
    lines = [p + "\n" for p in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def _read(name: str, errors: str) -> str:
    path = user_path(name)
    try:
        with open(path, encoding="utf-8", errors=errors, newline="") as fh:
            return fh.read()
    except FileNotFoundError:
        raise ModuleError(f"sed: can't read {name}: No such file or directory") from None
    except IsADirectoryError:
        raise ModuleError(f"sed: couldn't edit {name}: not a regular file") from None
    except OSError as exc:
        raise ModuleError(f"sed: can't read {name}: {exc.strerror or exc}") from None


@dataclass
class _Options:
    scripts: list[str] = field(default_factory=list)
    positional: list[str] = field(default_factory=list)
    quiet: bool = False
    ere: bool = False
    separate: bool = False
    in_place: str | None = None  # the backup suffix ("" for none) when -i is given


_LONG = {"quiet": "n", "silent": "n", "regexp-extended": "E", "separate": "s", "posix": "", "debug": "",
         "sandbox": "", "follow-symlinks": "", "unbuffered": "", "binary": "", "line-length": "?"}


def _parse_options(args: list[str]) -> _Options:
    opts = _Options()
    i = 0

    def value(flag: str) -> str:
        nonlocal i
        if i >= len(args):
            raise ModuleError(f"sed: option requires an argument -- '{flag}'  (see `man sed`)")
        i += 1
        return args[i - 1]

    def script_file(name: str) -> str:
        text = _read("/dev/stdin" if name == "-" else name, "replace")
        return text[:-1] if text.endswith("\n") else text

    while i < len(args):
        arg = args[i]
        i += 1
        if arg == "--":
            opts.positional += args[i:]
            break
        if arg.startswith("--"):
            name, equals, given = arg[2:].partition("=")
            if name == "expression":
                opts.scripts.append(given if equals else value("e"))
            elif name == "file":
                opts.scripts.append(script_file(given if equals else value("f")))
            elif name == "in-place":
                opts.in_place = given
            elif name in _LONG:
                short = _LONG[name]
                opts.quiet = opts.quiet or short == "n"
                opts.ere = opts.ere or short == "E"
                opts.separate = opts.separate or short == "s"
            else:
                raise ModuleError(f"sed: unknown option -- '{arg}'  (see `man sed`)")
        elif arg.startswith("-") and len(arg) > 1:
            j = 1
            while j < len(arg):
                flag = arg[j]
                j += 1
                if flag == "n":
                    opts.quiet = True
                elif flag in "Er":
                    opts.ere = True
                elif flag == "s":
                    opts.separate = True
                elif flag == "u":
                    pass
                elif flag == "i":
                    opts.in_place = arg[j:]
                    j = len(arg)
                elif flag in "ef":
                    given = arg[j:] or value(flag)
                    opts.scripts.append(given if flag == "e" else script_file(given))
                    j = len(arg)
                else:
                    raise ModuleError(f"sed: unknown option -- '{flag}'  (see `man sed`)")
        else:
            opts.positional.append(arg)
    return opts


def writes_files(args: list[str]) -> bool:
    """Whether these arguments ask sed to change files (-i, --in-place): the AI co-pilot needs a
    grant for that, and the MCP sandbox refuses it."""
    for arg in args:
        if arg == "--":
            return False
        if arg.startswith("--"):
            if arg.partition("=")[0] == "--in-place":
                return True
        elif arg.startswith("-") and len(arg) > 1:
            # -e and -f take the rest of the word as their value, so stop looking there
            for flag in arg[1:]:
                if flag == "i":
                    return True
                if flag in "ef":
                    break
    return False


def _backup_name(path: Path, suffix: str) -> Path:
    if "*" in suffix:
        name = suffix.replace("*", path.name)
        return Path(name) if "/" in name or os.sep in name else path.with_name(name)
    return path.with_name(path.name + suffix)


def _edit_in_place(engine: _Engine, names: list[str], suffix: str) -> None:
    for name in names:
        path = user_path(name)
        if not path.is_file():
            raise ModuleError(f"sed: couldn't edit {name}: not a regular file")
        text = _read(name, "surrogateescape")
        engine.reset()
        edited = "".join(engine.run(_split(text)))
        target = path.resolve()
        handle, temporary = tempfile.mkstemp(dir=target.parent, prefix=".sed")
        try:
            with os.fdopen(handle, "w", encoding="utf-8", errors="surrogateescape", newline="") as out:
                out.write(edited)
            try:
                os.chmod(temporary, target.stat().st_mode & 0o7777)
            except OSError:
                pass
            if suffix:
                backup = _backup_name(target, suffix)
                os.replace(target, backup)
            os.replace(temporary, target)
        except OSError as exc:
            raise ModuleError(f"sed: couldn't edit {name}: {exc.strerror or exc}") from None
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        if engine.stopped:
            break


def _units(names: list[str], stdin: Any, separate: bool) -> list[Iterable[str]]:
    """The input: one unit per file under -s, otherwise everything as a single stream."""
    if not names:
        return [streams.Lines.of(stdin)]
    for name in names:
        if name != "-" and not user_path(name).exists():
            raise ModuleError(f"sed: can't read {name}: No such file or directory")

    def lines_of(name: str) -> Iterable[str]:
        return streams.Lines.of(stdin) if name == "-" else _split(_read(name, "replace"))

    if separate:
        return [lines_of(n) for n in names]

    def chained() -> Iterator[str]:
        for name in names:
            yield from lines_of(name)

    return [chained()]


@builtin("sed", "Edit a stream of text: substitute, delete, print, insert",
         "sed [-n] [-E] [-s] [-i[SUFFIX]] (-e SCRIPT | -f FILE | SCRIPT) [FILE...]",
         category="text", streams=True, writes_if=writes_files, doc="""\
# sed

A stream editor. It reads stdin or FILEs line by line, runs your script on each line, and prints
the result. The files are not changed unless you give `-i`.

## Options

| Option | Meaning |
| --- | --- |
| `-n`, `--quiet` | Print nothing unless the script says so (with `p`). |
| `-e SCRIPT` | Add a script. Repeat it to add several. |
| `-f FILE` | Read the script from a file. |
| `-E`, `-r` | Extended regular expressions: `( ) { } + ? \\|` work without backslashes. |
| `-i[SUFFIX]` | Change the files in place. With a SUFFIX (`-i.bak`) the original is kept as a backup. |
| `-s` | Number lines and match `$` in each file on its own. |

Without `-e` or `-f`, the first argument is the script.

## Commands

| Command | Meaning |
| --- | --- |
| `s/RE/TEXT/FLAGS` | Replace a match. Flags: `g` all matches, `I` ignore case, `p` print, a number N for the Nth match. |
| `d` | Delete the line. |
| `p` | Print the line (twice, unless you used `-n`). |
| `a TEXT`, `i TEXT`, `c TEXT` | Add text after the line, before it, or in place of it. |
| `y/abc/xyz/` | Change each character of the first set into the one in the same place in the second. |
| `q [CODE]`, `Q [CODE]` | Quit after the line (`q` prints it first); CODE is the exit status. |
| `=` | Print the line number. |
| `{ ... }` | Run several commands on the lines an address picks. |

In TEXT, `&` is the whole match and `\\1` to `\\9` are the groups. `\\U`, `\\L`, `\\u`, `\\l` change
case, and `\\E` ends it. The delimiter can be any character: `s|/usr|/opt|`.

## Addresses

A command runs on every line unless it has an address in front of it.

| Address | Lines |
| --- | --- |
| `5` | line 5 |
| `$` | the last line |
| `/RE/` | lines that match (add `I` to ignore case) |
| `3,5` | lines 3 to 5 |
| `/a/,/b/` | from a match of `a` to the next match of `b` |
| `3,+2` | line 3 and the 2 after it |
| `0~2` | every second line (`first~step`) |
| `ADDRESS!` | every line except those |

## Regular expressions

By default they are POSIX basic expressions: `\\(`, `\\)`, `\\{`, `\\}`, `\\+`, `\\?` and `\\|` are the operators,
and a bare `(` or `+` is just a character. With `-E` it is the other way round. Both understand
`[[:digit:]]`-style classes, `\\w`, `\\s`, `\\b`, `\\<` and `\\>`. An empty pattern (`s//x/`) reuses the
last one.

## Not supported

The hold space and branching commands (`h H g G x n N D P b t`), `w`, `r`, `e` and `l`. Using one
gives a clear error.

## Examples

```
sed 's/unix/linux/g' notes.txt
sed -n '10,15p' app.log
sed '/^#/d' config.conf
sed '/error/s/false/true/g' app.log
sed -i.bak 's/old/new/g' config.conf
ls | sed 's/^/> /'
sed -E 's/([a-z]+)@([a-z.]+)/\\2: \\1/' mail.txt
sed '1~2d' list.txt
sed '5q' big.log
```

Inside ShellCraft, quote a script that has spaces, `;`, `$` or `!` in it with single quotes.
""")
def sed(ctx: ShellContext, args: list[str], stdin: Any) -> Any:
    opts = _parse_options(args)
    positional = list(opts.positional)
    scripts = list(opts.scripts)
    if not scripts:
        if not positional:
            raise ModuleError("sed: no script given (usage: sed [OPTIONS] SCRIPT [FILE...])")
        scripts.append(positional.pop(0))
    source = "\n".join(scripts)
    quiet = opts.quiet or source.startswith("#n\n") or source == "#n"
    if opts.in_place is not None and not ctx.allow_writes:
        raise ModuleError("sed: -i is not available in this context")
    engine = _Engine(_Parser(source, opts.ere).parse(), quiet, exact=opts.in_place is not None)

    if opts.in_place is not None:
        if not positional:
            raise ModuleError("sed: no input files")
        _edit_in_place(engine, positional, opts.in_place)
        return "" if not engine.stopped or engine.code == 0 else streams_status(engine.code)

    units = _units(positional, stdin, opts.separate)
    return _stream(engine, units, opts.separate)


def streams_status(code: int) -> Any:
    from shellcraft.context import WithStatus

    return WithStatus("", code)


def _stream(engine: _Engine, units: list[Iterable[str]], separate: bool) -> Generator[str, None, int]:
    for unit in units:
        if separate:
            engine.reset()
        yield from engine.run(unit)
        if engine.stopped:
            break
    return engine.code
