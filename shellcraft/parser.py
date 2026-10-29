"""Command-line tokenizer and pipeline parser.

Grammar:  segment ( '|' segment )* with redirections anywhere after a segment's command word:
          '>' FILE, '>>' FILE (the pipeline's output), '<' FILE (the first command's input),
          '2>' FILE, '2>>' FILE (a failing command's error message), '2>&1' (the error message
          becomes the command's output), '&>' FILE (output and errors), '>&2' / '1>&2' (the
          command's output goes to the error stream: `echo "bad input" >&2`).
A lone `&` (background jobs) and other `>&` forms are rejected with a ParseError instead of being passed on
as arguments: `rm a & ls` must never mean `rm a '&' ls`. `;`, `&&` and `||` are split by
shellcraft/script.py before a pipeline gets here, and are rejected here too.
Quotes: '...' is literal, "..." allows \\" for a quote. Backslashes are otherwise
literal so Windows paths like C:\\data\\x.csv survive untouched.

Variables: given a `variables` mapping, `$?`, `$name` and `${name}` expand outside single quotes
(`$?` is the last exit status, `$name` a loop or shell variable). An expansion never splits into
several words unless `split` is set (bash-style blocks), so a value with spaces stays one
argument. A name that isn't set is empty, as in bash (`set -u` makes it an error).
`${…}` also takes bash's parameter expansions: `${v:-default}`, `${#v}`, `${v%.txt}`, `${v/a/b}`,
`${v:1:3}`, `${v^^}` and the rest (see _parameter).
`$(( … ))` is arithmetic (shellcraft/arith.py), `$( … )` runs a command when `substitute` is
given, and with `glob` set an unquoted word with `*`, `?` or `[` becomes the matching file names
(the word itself when nothing matches).
"""

from __future__ import annotations

import fnmatch
import glob as globmod
import os
import re
from dataclasses import dataclass
from typing import Callable, Mapping

from shellcraft import arith

# Longest first. The `2>` forms and a lone `&` only count at the start of a word, as in POSIX
# shells, so `a2>b` is `a2 > b` and a URL's `?a=1&b=2` stays one word.
OPERATORS = ("2>&1", "1>&2", ">&2", "2>>", "2>", "&>", "|", ">>", ">", "<")
UNSUPPORTED = (">&", "&&", "||", ";", "&")
_WORD_START_ONLY = ("2>&1", "1>&2", "2>>", "2>", "&")
TO_STDERR = (">&2", "1>&2")
_VAR_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Commands whose arguments are never globbed: `jq -i *` and `jq -f [name]` mean what they say.
NO_GLOB = frozenset({"jq"})


def _segment_command(tokens: list[Token]) -> str | None:
    """The command word of the segment being tokenized (the first word after the last operator)."""
    for k in range(len(tokens) - 1, -1, -1):
        if tokens[k].op:
            return tokens[k + 1].value if k + 1 < len(tokens) else None
    return tokens[0].value if tokens else None


class ParseError(Exception):
    def __init__(self, message: str, pos: int | None = None):
        super().__init__(message)
        self.message = message
        self.pos = pos


class UnboundVariable(ParseError):
    """`$name` for a variable that isn't set, under `set -u` (the shell reports it as an error)."""

    def __init__(self, name: str, pos: int | None = None):
        super().__init__(f"{name}: unbound variable", pos)
        self.name = name


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
    bypass: bool = False  # typed as `\name`: no alias and no shell function, the command itself
    to_stderr: bool = False  # `>&2`: what the command prints goes to the error stream


@dataclass
class Redirect:
    path: str
    append: bool


@dataclass
class Pipeline:
    segments: list[Command]
    redirect: Redirect | None = None
    stdin_path: str | None = None  # `< file`


def _expand_simple(word: str, variables: Mapping[str, str]) -> str:
    """A prompt's text (`input("Hello $USER")`): its `$x` / `${x}` expand, the rest is literal."""
    out, i = [], 0
    while i < len(word):
        if word[i] == "$" and (var := expand_variable(word, i, variables)) is not None:
            out.append(var[0])
            i = var[1]
        else:
            out.append(word[i])
            i += 1
    return "".join(out)


def quoted_arguments(line: str, i: int, variables: Mapping[str, str] | None) -> tuple[list[str], int] | None:
    """At `$@` / `${@}` inside double quotes, in a shell function: (its arguments, index after),
    so that "$@" becomes one word per argument as in bash. None elsewhere."""
    if line.startswith("$@", i):
        end = i + 2
    elif line.startswith("${@}", i):
        end = i + 4
    else:
        return None
    arguments = getattr(variables, "arguments", None)
    args = arguments() if callable(arguments) else None
    return None if args is None else (list(args), end)


class ParameterError(UnboundVariable):
    """`${name:?message}` for a name that is unset or empty: an error that stops the script, as
    `set -u` does."""

    def __init__(self, name: str, message: str, pos: int | None = None):
        super().__init__(name, pos)
        self.message = f"{name}: {message}"
        self.args = (self.message,)


def expand_variable(line: str, i: int, variables: Mapping[str, str] | None,
                    substitute: Callable[[str], str] | None = None, quoted: bool = False) -> tuple[str, int] | None:
    """At a `$` (line[i]): (value, index after the reference) for a variable reference, else None.
    `${…}` takes bash's parameter expansions (see _parameter); `quoted` is set inside "…", where
    single quotes in a default word are plain text, as in bash."""
    if not variables or i + 1 >= len(line):
        return None
    nxt = line[i + 1]
    if nxt == "?":
        name, end = "?", i + 2
    elif nxt.isdigit() or nxt in "#@*":  # a function's arguments: $1 … $9, $#, $@, $*
        name, end = nxt, i + 2
    elif nxt == "{":
        close = _closing_brace(line, i + 2)
        if close is None:
            return None
        return _parameter(line[i + 2:close], variables, substitute, quoted, i), close + 1
    else:
        match = _VAR_NAME.match(line, i + 1)
        if not match:
            return None
        name, end = match.group(), match.end()
    if name not in variables:
        if getattr(variables, "nounset", False) and not (name.isdigit() or name in "#@*?"):
            raise UnboundVariable(name, i)  # `set -u`
        return "", end  # as in bash: a name that isn't set is empty
    return variables[name], end


def _closing_brace(line: str, i: int) -> int | None:
    """With line[i] just after `${`: the index of its `}`. Quotes, `$( … )` and inner braces
    (`${a:-${b}}`) are skipped over."""
    level, n = 0, len(line)
    while i < n:
        ch = line[i]
        if ch == "\\":
            i += 2
            continue
        if ch in ("'", '"'):
            end = line.find(ch, i + 1)
            if ch == '"':
                while end != -1 and line[end - 1] == "\\":
                    end = line.find(ch, end + 1)
            if end == -1:
                return None
            i = end + 1
            continue
        if line.startswith("$(", i):
            close = find_closing_paren(line, i + 2)
            if close is None:
                return None
            i = close + 1
            continue
        if ch == "{":
            level += 1
        elif ch == "}":
            if level == 0:
                return i
            level -= 1
        i += 1
    return None


_PARAM_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+|[@*#?]")
_CASE_OPS = ("^^", ",,", "^", ",")


def _parameter(body: str, variables: Mapping[str, str], substitute: Callable[[str], str] | None,
               quoted: bool, pos: int) -> str:
    """The value of `${body}`, as bash expands it: `${v}`, `${#v}` (length), `${v:-word}`
    `${v-word}` (default), `${v:=word}` `${v=word}` (default, also assigned), `${v:+word}`
    `${v+word}` (alternative), `${v:?msg}` `${v?msg}` (error when unset), `${v#pat}` `${v##pat}`
    `${v%pat}` `${v%%pat}` (trim the shortest / longest match), `${v/pat/rep}` `${v//pat/rep}`
    `${v/#pat/rep}` `${v/%pat/rep}` (replace), `${v:off}` `${v:off:len}` (a slice) and
    `${v^^}` `${v,,}` `${v^}` `${v,}` (case). Anything else is a "bad substitution" error."""
    def bad() -> ParseError:
        return ParseError(f"${{{body}}}: bad substitution", pos)

    def word(text: str, pattern: bool = False) -> str:
        return _expand_word(text, variables, substitute, quoted, pattern, pos)

    if body == "#":
        return str(variables.get("#", "0"))
    if body.startswith("#") and len(body) > 1:  # ${#name}: the length of the value
        name = body[1:]
        if name in ("@", "*"):
            return str(variables.get("#", "0"))
        if not _PARAM_NAME.fullmatch(name):
            raise bad()
        return str(len(_value(name, variables, pos)))
    match = _PARAM_NAME.match(body)
    if not match:
        raise bad()
    name, rest = match.group(), body[match.end():]
    if not rest:
        return _value(name, variables, pos)
    is_set = _is_set(name, variables)
    for op in (":-", ":=", ":+", ":?", "-", "=", "+", "?"):
        if rest.startswith(op):
            text = rest[len(op):]
            present = is_set and not (op.startswith(":") and str(variables[name]) == "")
            kind = op[-1]
            if kind == "+":
                return word(text) if present else ""
            if present:
                return str(variables[name])
            if kind == "-":
                return word(text)
            if kind == "=":
                if not _VAR_NAME.fullmatch(name):
                    raise ParseError(f"${{{body}}}: ${name}: cannot assign in this way", pos)
                value = word(text)
                _assign(variables, name, value)
                return value
            raise ParameterError(name, word(text) if text else "parameter null or not set", pos)
    value = _value(name, variables, pos)
    if rest.startswith(":"):  # ${v:offset} / ${v:offset:length}
        spec = rest[1:]
        offset_text, colon, length_text = spec.partition(":")
        if not offset_text.strip() or (colon and not length_text.strip()):
            raise bad()
        try:
            offset = arith.evaluate(word(offset_text), variables)
            length = arith.evaluate(word(length_text), variables) if colon else None
        except arith.ArithError as exc:
            raise ParseError(f"${{{body}}}: {exc}", pos) from None
        if offset < 0:  # from the end: ${v: -3}
            offset += len(value)
        if not 0 <= offset <= len(value):
            return ""
        if length is None:
            return value[offset:]
        if length < 0:
            end = len(value) + length
            if end < offset:
                raise ParseError(f"${{{body}}}: {length}: substring expression < 0", pos)
            return value[offset:end]
        return value[offset:offset + length]
    for op in ("##", "#", "%%", "%"):
        if rest.startswith(op):
            return _trim(value, _pattern(word(rest[len(op):], pattern=True)), op)
    if rest.startswith("/"):
        spec = rest[1:]
        how = ""
        if spec[:1] in ("/", "#", "%"):
            how, spec = spec[0], spec[1:]
        slash = _unquoted_slash(spec)
        pat, rep = (spec, "") if slash is None else (spec[:slash], spec[slash + 1:])
        return _replace(value, word(pat, pattern=True), word(rep), how)
    for op in _CASE_OPS:
        if rest.startswith(op):
            only = rest[len(op):]
            matches = _pattern(word(only, pattern=True)) if only else None
            change = str.upper if op[0] == "^" else str.lower
            out = []
            for k, ch in enumerate(value):
                if (len(op) == 2 or k == 0) and (matches is None or matches.fullmatch(ch)):
                    ch = change(ch)
                out.append(ch)
            return "".join(out)
    raise bad()


def _value(name: str, variables: Mapping[str, str], pos: int) -> str:
    """A name's value as plain text; under `set -u` an unset name is an error (as `$name`)."""
    if name not in variables:
        if getattr(variables, "nounset", False) and not (name.isdigit() or name in "#@*?"):
            raise UnboundVariable(name, pos)
        return ""
    return str(variables[name])  # plain text, even for a number set from Python (script.PyValue)


def _is_set(name: str, variables: Mapping[str, str]) -> bool:
    """Whether a name is set: in a shell function `$3` is set only with three arguments or more."""
    if name.isdigit() and name != "0":
        arguments = getattr(variables, "arguments", None)
        args = arguments() if callable(arguments) else None
        if args is not None:
            return int(name) <= len(args)
    return name in variables


def _assign(variables: Mapping[str, str], name: str, value: str) -> None:
    """`${name:=word}`: set the variable where `name=word` would (Variables.assign)."""
    assign = getattr(variables, "assign", None)
    if callable(assign):
        assign(name, value)
    elif isinstance(variables, dict):
        variables[name] = value


def _expand_word(text: str, variables: Mapping[str, str], substitute: Callable[[str], str] | None,
                 in_quotes: bool, pattern: bool, pos: int) -> str:
    """The word of `${v:-word}` (or a pattern or replacement): `$x`, `${x}`, `$( … )` and
    `$(( … ))` expand, quotes are removed, and a leading `~` is the home directory. In a
    `pattern`, quoted text (and a backslashed character) matches as itself; the `${…}` being
    inside "…" (`in_quotes`) doesn't quote it, as in bash: "${f%.*}" still trims."""
    out: list[str] = []

    def add(chunk: str, literal: bool) -> None:
        out.append(globmod.escape(chunk) if pattern and literal else chunk)

    def dollar(at: int, dquoted: bool) -> tuple[str, int] | None:
        if text.startswith("$((", at):
            close = _closing_double_paren(text, at + 3)
            if close is None:
                raise ParseError("missing '))' for '$(('", pos)
            try:
                return str(arith.evaluate(_arith_text(text[at + 3:close], variables, substitute), variables)), close + 2
            except arith.ArithError as exc:
                raise ParseError(f"$(( )): {exc}", pos) from None
        if text.startswith("$(", at):
            close = find_closing_paren(text, at + 2)
            if close is None:
                raise ParseError("missing ')' for '$('", pos)
            if substitute is None:
                return text[at:close + 1], close + 1
            return substitute(text[at + 2:close]).rstrip("\n"), close + 1
        return expand_variable(text, at, variables, substitute, dquoted)

    i, n = 0, len(text)
    if not in_quotes and (text == "~" or text.startswith("~/")):
        add(os.path.expanduser("~"), True)
        i = 1
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n and (not in_quotes or text[i + 1] in '"\\$`}/'):
            add(text[i + 1], True)
            i += 2
        elif ch == "'" and not in_quotes:
            end = text.find("'", i + 1)
            end = n if end == -1 else end
            add(text[i + 1:end], True)
            i = end + 1
        elif ch == '"':
            i += 1
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n and text[i + 1] in '"\\$':
                    add(text[i + 1], True)
                    i += 2
                elif text[i] == "$" and (var := dollar(i, True)) is not None:
                    add(var[0], True)
                    i = var[1]
                else:
                    add(text[i], True)
                    i += 1
            i += 1
        elif ch == "$" and (var := dollar(i, in_quotes)) is not None:
            add(var[0], False)
            i = var[1]
        else:
            add(ch, False)
            i += 1
    return "".join(out)


def _arith_text(text: str, variables: Mapping[str, str] | None,
                substitute: Callable[[str], str] | None) -> str:
    """The inside of `$(( … ))` with its `${#v}`, `${v:-0}` and other `${…}` forms expanded
    first, as bash does; a plain `$x` / `${x}` is left for shellcraft.arith to read."""
    if variables is None or "${" not in text:
        return text
    out, i = [], 0
    while (at := text.find("${", i)) != -1:
        close = _closing_brace(text, at + 2)
        if close is None:
            break
        out.append(text[i:at])
        body = text[at + 2:close]
        if _PARAM_NAME.fullmatch(body):
            out.append(text[at:close + 1])
        else:
            out.append(_parameter(body, variables, substitute, False, at))
        i = close + 1
    out.append(text[i:])
    return "".join(out)


def _unquoted_slash(text: str) -> int | None:
    """The index of the `/` that ends the pattern of `${v/pattern/replacement}`."""
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch in ("'", '"'):
            end = text.find(ch, i + 1)
            i = n if end == -1 else end + 1
            continue
        if text.startswith("${", i):
            close = _closing_brace(text, i + 2)
            i = n if close is None else close + 1
            continue
        if ch == "/":
            return i
        i += 1
    return None


def _pattern(glob_pattern: str) -> re.Pattern[str]:
    return re.compile(fnmatch.translate(glob_pattern), re.S)


def _trim(value: str, pattern: re.Pattern[str], op: str) -> str:
    """`${v#p}` `${v##p}`: the value without the shortest / longest prefix matching p;
    `${v%p}` `${v%%p}`: without the shortest / longest such suffix."""
    n = len(value)
    if op[0] == "#":
        cuts = range(n, -1, -1) if op == "##" else range(n + 1)
        return next((value[k:] for k in cuts if pattern.fullmatch(value[:k])), value)
    cuts = range(n + 1) if op == "%%" else range(n, -1, -1)
    return next((value[:k] for k in cuts if pattern.fullmatch(value[k:])), value)


def _replace(value: str, glob_pattern: str, replacement: str, how: str) -> str:
    """`${v/p/r}`: the first longest match of p replaced by r; `//` every match, `/#` a match
    at the start, `/%` a match at the end. An empty pattern changes nothing."""
    if not glob_pattern:
        return value
    pattern = _pattern(glob_pattern)
    n = len(value)
    if how == "#":
        k = next((k for k in range(n, -1, -1) if pattern.fullmatch(value[:k])), None)
        return value if k is None else replacement + value[k:]
    if how == "%":
        k = next((k for k in range(n + 1) if pattern.fullmatch(value[k:])), None)
        return value if k is None else value[:k] + replacement
    out: list[str] = []
    i = 0
    while i < n:
        end = next((j for j in range(n, i, -1) if pattern.fullmatch(value[i:j])), None)
        if end is None:
            out.append(value[i])
            i += 1
            continue
        out.append(replacement)
        i = end
        if how != "/":
            out.append(value[i:])
            return "".join(out)
    return "".join(out)


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


_ANSI_C = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "f": "\f", "v": "\v", "e": "\x1b",
           "E": "\x1b", "\\": "\\", "'": "'", '"': '"', "?": "?"}


def ansi_c_string(line: str, at: int) -> tuple[str, int]:
    """`$'…'` starting at `at`: its text with bash's escapes decoded (\\n \\t \\xHH \\uHHHH \\NNN …),
    and the index after the closing quote."""
    out: list[str] = []
    i, n = at + 2, len(line)
    while i < n:
        ch = line[i]
        if ch == "'":
            return "".join(out), i + 1
        if ch == "\\" and i + 1 < n:
            nxt = line[i + 1]
            if nxt in _ANSI_C:
                out.append(_ANSI_C[nxt])
                i += 2
                continue
            for prefix, digits, base in (("x", 2, 16), ("u", 4, 16), ("U", 8, 16)):
                if nxt == prefix:
                    j = i + 2
                    while j < n and j < i + 2 + digits and line[j] in "0123456789abcdefABCDEF":
                        j += 1
                    if j > i + 2:
                        out.append(chr(int(line[i + 2:j], base)))
                        i = j
                        break
            else:
                if nxt in "01234567":
                    j = i + 1
                    while j < n and j < i + 4 and line[j] in "01234567":
                        j += 1
                    out.append(chr(int(line[i + 1:j], 8)))
                    i = j
                    continue
                out.append("\\" + nxt)
                i += 2
                continue
            continue
        out.append(ch)
        i += 1
    raise ParseError("unterminated $' quote", at)


def comment_at(text: str, i: int) -> bool:
    """Whether the `#` at text[i] (outside quotes) starts a comment that runs to the end of its line:
    it is the first non-space character of the line, or it has whitespace before it and its run of
    `#`s is followed by whitespace or the end of the line (` # note`, ` ## note`). A `#` touching
    other text is plain text: `#05d9e8`, `#TODO`, `$#`, `${#name}`, `page#top`."""
    if text[i] != "#":
        return False
    line_start = text.rfind("\n", 0, i) + 1
    if not text[line_start:i].strip():
        return True
    if not text[i - 1].isspace() or (i >= 2 and text[i - 2] == "\\"):  # `a\ #`: an escaped space is part of a word
        return False
    j = i
    while j < len(text) and text[j] == "#":
        j += 1
    return j == len(text) or text[j].isspace()


def strip_comments(text: str) -> str:
    """`text` without its comments (see comment_at), line by line; quoted text is never a comment.
    The whitespace before a comment goes too, the line breaks stay."""
    if "#" not in text:
        return text
    out: list[str] = []
    quote = ""  # the open quote: ', " or $'
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if quote:
            if ch == "\\" and quote != "'" and i + 1 < n:  # an escape inside "…" or $'…'
                out.append(text[i:i + 2])
                i += 2
                continue
            if ch == quote[-1]:
                quote = ""
            out.append(ch)
            i += 1
            continue
        if ch == "\\" and i + 1 < n:  # \# is a plain #
            out.append(text[i:i + 2])
            i += 2
            continue
        if text.startswith("$'", i):
            quote = "$'"
            out.append("$'")
            i += 2
            continue
        if ch in "'\"":
            quote = ch
        elif ch == "#" and comment_at(text, i):
            end = text.find("\n", i)
            while out and out[-1] in " \t":
                out.pop()
            i = n if end == -1 else end
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def join_continued_lines(line: str) -> str:
    """Backslash + newline (outside single quotes) joins two lines, as in bash: a curl command
    pasted over several lines with `\\` at the ends is one command."""
    if "\\\n" not in line and "\\\r\n" not in line:
        return line
    out: list[str] = []
    quote = ""
    i, n = 0, len(line)
    while i < n:
        ch = line[i]
        if quote:
            if ch == quote:
                quote = ""
            elif quote == '"' and ch == "\\" and i + 1 < n and line[i + 1] in '"\\':
                out.append(line[i:i + 2])
                i += 2
                continue
        elif ch in ("'", '"'):
            quote = ch
        if ch == "\\" and quote != "'":
            if line.startswith("\\\n", i):
                i += 2
                continue
            if line.startswith("\\\r\n", i):
                i += 3
                continue
        out.append(ch)
        i += 1
    return "".join(out)


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
        in_test = bool(tokens) and tokens[0].value == "[[" and not tokens[0].op
        if in_test and (cmp := next((c for c in ("<=", ">=", "<", ">") if line.startswith(c, i)), None)):
            tokens.append(Token(cmp, i))  # inside `[[ … ]]`: a comparison, not a redirection
            i += len(cmp)
            continue
        if bad in ("&&", "||") and in_test:
            tokens.append(Token(bad, i))  # inside `[[ … ]]`: the test's own and / or (shellcraft/commands/cond.py)
            i += 2
            continue
        if bad and (op is None or len(bad) >= len(op)):
            what = {"&": "only a trailing & (run the line in the background) is supported; ",
                    ">&": "only >&2, 1>&2 and 2>&1 are; "}.get(bad, "")
            raise ParseError(f"'{bad}' is not supported yet ({what}quote it to pass it as text)", i)
        if op:
            tokens.append(Token(op, i, op=True))
            i += len(op)
            continue

        start = i
        buf: list[str] = []
        quoted = expanded = no_arguments = False
        globbable: list[bool] = []  # per character: may it be a glob character (unquoted)?
        literal: list[bool] = []  # per character: was it quoted (a `[[ x == "*" ]]` pattern takes it as text)?

        def expansion(at: int, quoted: bool = False) -> tuple[str, int] | None:
            if line.startswith("$((", at):
                close = _closing_double_paren(line, at + 3)
                if close is None:
                    raise ParseError("missing '))' for '$(('", at)
                try:
                    expr = _arith_text(line[at + 3:close], variables, substitute)
                    return str(arith.evaluate(expr, variables)), close + 2
                except arith.ArithError as exc:
                    if variables is None:  # a first look at the line, without its variables: `$((a / b))`
                        return line[at:close + 2], close + 2  # would divide by 0 here; it runs with them later
                    raise ParseError(f"$(( )): {exc}", at) from None
            if substitute is not None and line.startswith("$(", at):
                close = find_closing_paren(line, at + 2)
                if close is None:
                    raise ParseError("missing ')' for '$('", at)
                return substitute(line[at + 2:close]).rstrip("\n"), close + 1
            return expand_variable(line, at, variables, substitute, quoted)

        def add(text: str, may_glob: bool, quoted_text: bool | None = None) -> None:
            buf.append(text)
            globbable.extend([may_glob] * len(text))
            literal.extend([not may_glob if quoted_text is None else quoted_text] * len(text))

        def flush() -> None:
            nonlocal buf, quoted, globbable, literal
            _emit(tokens, "".join(buf), start, globbable, glob and _segment_command(tokens) not in NO_GLOB)
            buf, quoted, globbable, literal = [], False, [], []

        while i < n and not line[i].isspace() and not _breaks_word(line, i):
            ch = line[i]
            if ch == "$" and line.startswith("$'", i):  # bash's ANSI-C string: $'a\nb', as curl pastes use
                text, i = ansi_c_string(line, i)
                quoted = True
                add(text, False)
                continue
            if ch == "$" and (var := expansion(i)) is not None:
                value, i = var
                expanded = True
                if not split or not any(c.isspace() for c in value):
                    add(value, False, quoted_text=False)  # unquoted: a pattern in `[[ x == $pat ]]`
                    continue
                pieces = value.split()
                if value[:1].isspace() and (buf or quoted):
                    flush()
                for k, piece in enumerate(pieces):
                    if k:
                        flush()
                    add(piece, False, quoted_text=False)
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
                    if ch == '"' and line[end] == "$" and (at := quoted_arguments(line, end, variables)) is not None:
                        args, end = at  # "$@": one word per argument; prefix / suffix join the first / last
                        no_arguments = no_arguments or not args
                        for k, arg in enumerate(args):
                            if k:
                                flush()
                                quoted = True
                            add(arg, False)
                        continue
                    if ch == '"' and line[end] == "$" and (var := expansion(end, quoted=True)) is not None:
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
        if no_arguments and not word:
            continue  # "$@" with no arguments is no word at all, as in bash
        if not quoted and word.startswith("~"):
            expanded_word = expand_tilde(word)
            if expanded_word != word:
                globbable = [False] * (len(expanded_word) - len(word) + 1) + globbable[1:]
                word = expanded_word
        if (len(tokens) > 2 and tokens[0].value == "[[" and not tokens[0].op and not tokens[-1].op
                and tokens[-1].value in ("==", "=", "!=") and len(literal) == len(word)):
            # the pattern of `[[ x == PATTERN ]]`: as in bash, its quoted parts match as plain text
            word = "".join(globmod.escape(c) if lit and c in "*?[" else c for c, lit in zip(word, literal))
        _emit(tokens, word, start, globbable, glob and _segment_command(tokens) not in NO_GLOB)
    return tokens


def _emit(tokens: list[Token], word: str, start: int, globbable: list[bool], glob: bool) -> None:
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
    merge_stderr = to_stderr = False
    leading = allow_leading_pipe
    i = 0

    def close_segment(pos: int) -> None:
        nonlocal current, stderr, merge_stderr, to_stderr
        if not current:
            raise ParseError("empty pipeline segment before '|'", pos)
        segments.append(Command(current[0].value, [t.value for t in current[1:]], stderr, merge_stderr,
                                to_stderr=to_stderr))
        current, stderr, merge_stderr, to_stderr = [], None, False, False

    while i < len(tokens):
        tok = tokens[i]
        if tok.op and tok.value == "|":
            if leading and not current and not segments:
                segments.append(Command("", [], stderr, merge_stderr, to_stderr=to_stderr))
                stderr, merge_stderr, to_stderr = None, False, False
            else:
                close_segment(tok.pos)
            leading = False
        elif tok.op and tok.value in ("2>&1", *TO_STDERR):
            if not current and not (leading and not segments):
                raise ParseError(f"missing command before '{tok.value}'", tok.pos)
            if tok.value == "2>&1":
                merge_stderr = True
            else:
                to_stderr = True
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
        if leading and not segments and (redirect is not None or stdin_path is not None or stderr is not None
                                         or merge_stderr or to_stderr):
            # `} > out`, `done 2> err.txt`, `done >&2`: only a redirection of the block
            segments.append(Command("", [], stderr, merge_stderr, to_stderr=to_stderr))
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


def split_background(line: str) -> str | None:
    """`cmd args &`: the command line without its trailing `&` (outside quotes, after a space),
    or None when the line does not end that way. `&&`, `&>` and `a=1&b=2` are not it."""
    quote = ""
    last = -1
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
        elif ch == "&":
            last = i
    if quote or last <= 0 or line[last + 1:].strip() or not line[last - 1].isspace():
        return None
    rest = line[:last].rstrip()
    return rest or None
