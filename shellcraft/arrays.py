"""arrays — the bash side of arrays and dicts: `a=(x y)`, `a+=(z)`, `a[1]=v`, `m[key]=v`, `unset 'a[1]'`.

The values themselves are in shellcraft.values (the Python side, `hosts = ["a", "b"]`, builds the
same ones in shellcraft.script). Here a line is recognised as one of the four assignments, its
words are expanded like the words of a command, and the new value is stored with `set_variable`.

What bash allows and ShellCraft does not, each with a one-line error: arrays with gaps
(`a[5]=x` past the end), and a word as an index of an array that is not a dict (`a[web]=1` sets
element 0 in bash, a mistake nobody means).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Mapping

from shellcraft import parser as shparser
from shellcraft.parser import ParseError, closing_bracket, find_closing_paren, tokenize
from shellcraft.values import ShArray, ShMap, ValueProblem, elements

if TYPE_CHECKING:
    from shellcraft.context import ShellContext

_NAME = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*)")
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_KEYED = re.compile(r"\[([^\]]*)\]=(.*)", re.S)
REFERENCE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\[(.*)\]", re.S)  # `a[1]`, as `unset` and `read` take it


@dataclass
class Target:
    """The left side of an array assignment and the text after its `=`."""

    name: str
    sub: str | None  # the text between `[` and `]`; None for `NAME=( … )`
    append: bool  # `+=`
    raw: str


def target(line: str) -> Target | None:
    """`NAME=( … )`, `NAME+=( … )`, `NAME[SUB]=VALUE` or `NAME[SUB]+=VALUE` at the start of `line`: its parts."""
    match = _NAME.match(line)
    if not match:
        return None
    i, sub = match.end(), None
    if line.startswith("[", i):
        close = closing_bracket(line, i)
        if close is None:
            return None
        sub, i = line[i + 1:close], close + 1
    append = line.startswith("+=", i)
    if append:
        i += 1
    if not line.startswith("=", i):
        return None
    raw = line[i + 1:]
    if sub is None and not raw.startswith("("):
        return None
    return Target(match.group(1), sub, append, raw)


def literal_end(text: str, i: int) -> int | None:
    """With text[i] the `(` of `NAME=( … )`: the index of its `)`; None when it is still open."""
    return find_closing_paren(text, i + 1)


def words_of(raw: str, name: str, variables: Mapping[str, str] | None,
             substitute: Callable[[str], str] | None) -> list[str]:
    """The words inside `( … )`, expanded like the words of a command: quotes, `$x` (split at
    white space when not quoted), `$(command)`, `"${b[@]}"`, `{1..3}` and `*.log`."""
    close = literal_end(raw, 0)
    if close is None:
        raise ParseError(f"{name}=(: missing ')'")
    if raw[close + 1:].strip():
        raise ParseError(f"{name}=( … ): unexpected '{raw[close + 1:].strip()}' after the ')' "
                         "(an array is set on a line of its own)")
    tokens = tokenize(raw[1:close], variables, split=True, substitute=substitute, glob=True)
    for token in tokens:
        if token.op:
            raise ParseError(f"{name}=( … ): '{token.value}' is not allowed in an array (quote it to keep it as text)")
    return [token.value for token in tokens]


def compound(name: str, current: str | None, words: list[str], append: bool = False,
             as_map: bool | None = None) -> str:
    """The value `NAME=(WORDS)` or `NAME+=(WORDS)` gives. A dict (`as_map`, or a name that holds one
    already) takes `[key]=value` words; an array takes plain words, or `[0]=x [1]=y` in order, the
    form `declare -p` prints."""
    keyed = [match for word in words if (match := _KEYED.fullmatch(word))]
    if as_map is None:
        as_map = isinstance(current, ShMap)
    if as_map:
        if len(keyed) != len(words):
            raise ValueProblem(f"{name}: a dict takes [key]=value words: {name}=([web]=80 [db]=5432)")
        before = current.items if append and isinstance(current, ShMap) else ()
        return ShMap([*before, *((match.group(1), match.group(2)) for match in keyed)])
    before_items = [] if not append or current is None else elements(current)
    if keyed and len(keyed) == len(words):
        plain: list[str] = []
        for match in keyed:
            key, expected = match.group(1), len(before_items) + len(plain)
            if not re.fullmatch(r"\d+", key):
                raise ValueProblem(f"{name}: not a dict: declare -A {name} first")
            if int(key) != expected:
                raise ValueProblem(f"{name}[{key}]: arrays have no gaps (next index is {expected})")
            plain.append(match.group(2))
        words = plain
    return ShArray([*before_items, *words])


def with_element(name: str, current: str | None, sub: str, value: str, append: bool,
                 variables: Mapping[str, str], substitute: Callable[[str], str] | None) -> str:
    """The value `NAME[SUB]=VALUE` gives: a key of a dict, else an index of an array (plain text
    becomes an array of one element first, as in bash)."""
    if isinstance(current, ShMap):
        key = shparser._expand_word(sub, variables, substitute, False, False, 0)
        return current.with_item(key, (current.get(key) or "") + value if append else value)
    if _WORD.fullmatch(sub.strip()) and sub.strip() not in variables:
        raise ValueProblem(f"{name}: not a dict: declare -A {name} first")
    index = shparser.subscript_number(name, sub, variables)
    array = current if isinstance(current, ShArray) else ShArray([] if current is None else [str(current)])
    return array.with_item(index, (array.get(index) or "") + value if append else value, name)


def without_element(name: str, current: str, sub: str, variables: Mapping[str, str]) -> str:
    """The value `unset 'NAME[SUB]'` leaves: a dict without that key, an array without its last element."""
    if isinstance(current, ShMap):
        return current.without(shparser._expand_word(sub, variables, None, False, False, 0))
    array = current if isinstance(current, ShArray) else ShArray([str(current)])
    return array.without(shparser.subscript_number(name, sub, variables), name)


def assign(line: str, ctx: ShellContext, variables: Mapping[str, str],
           substitute: Callable[[str], str] | None, target_store: dict[str, str] | None = None,
           as_map: bool | None = None) -> bool:
    """Run `line` when it is an array assignment and return True; False when it is something else.
    Raises ParseError for a literal that is not closed, PipelineError for a value that can't be built."""
    from shellcraft.pipeline import PipelineError, set_variable

    found = target(line)
    if found is None:
        return False
    name = found.name
    store = ctx.variables if target_store is None else target_store
    current = store.get(name)
    try:
        if found.sub is None:
            value = compound(name, current, words_of(found.raw, name, variables, substitute), found.append, as_map)
        else:
            try:
                dry = tokenize(found.raw, None, substitute=lambda _: "x")
            except ParseError:
                return False
            if len(dry) > 1 or any(token.op for token in dry):
                return False  # `a[0]=x y`: a command line, which the pipeline parser reports
            tokens = tokenize(found.raw, variables, substitute=substitute)
            text = " ".join(token.value for token in tokens)
            value = with_element(name, current, found.sub, text, found.append, variables, substitute)
    except ValueProblem as exc:
        ctx.last_status = 1
        message = str(exc)
        label = message.partition(": ")[0]  # `a[9]: arrays have no gaps`: the error is about a[9]
        if not label.startswith(name):
            label, message = name, f"{name}: {message}"
        raise PipelineError(1, 1, label, message) from None
    set_variable(ctx, name, value, target_store)
    return True


def declared_as(name: str, current: str | None, as_map: bool) -> str:
    """What `declare -a NAME` (or `-A`) leaves in NAME: an empty array or dict when it is not set,
    the one that is there already, or plain text as its element 0 (key `0`), as in bash."""
    if as_map:
        if isinstance(current, ShArray):
            raise ValueProblem(f"{name}: it is an array: a dict needs a new name, or unset {name} first")
        return current if isinstance(current, ShMap) else ShMap([] if current is None else [("0", str(current))])
    if isinstance(current, ShMap):
        raise ValueProblem(f"{name}: it is a dict: an array needs a new name, or unset {name} first")
    return current if isinstance(current, ShArray) else ShArray([] if current is None else [str(current)])


_DECLARE = re.compile(r"\s*(declare|readonly|local|export)(?=\s)")
_COMPOUND = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\+?=\(")


def is_open(text: str, i: int) -> re.Match[str] | None:
    """`NAME=(` or `NAME+=(` at text[i]: the match (it ends just after the `(`)."""
    return _COMPOUND.match(text, i)


def declaration(line: str) -> tuple[str, list[str], bool | None] | None:
    """`declare -a NAME=( … )`, `readonly NAME=( … )` or `local NAME=( … )` taken apart: (the line
    with just the names, the `NAME=( … )` assignments, whether `-A` asks for a dict). None for a
    line that sets no array this way."""
    match = _DECLARE.match(line)
    if not match:
        return None
    head: list[str] = [line[:match.end()]]
    found: list[str] = []
    i, quote, n = match.end(), None, len(line)
    while i < n:
        ch = line[i]
        if quote:
            quote = None if ch == quote else quote
        elif ch in ("'", '"'):
            quote = ch
        elif line[i - 1].isspace() and (opened := is_open(line, i)):
            close = find_closing_paren(line, opened.end())
            if close is None:
                raise ParseError(f"{line[i:opened.end() - 2]}=(: missing ')'")
            found.append(line[i:close + 1])
            head.append(line[i:opened.end() - 1].rstrip("+="))
            i = close + 1
            continue
        head.append(ch)
        i += 1
    if not found:
        return None
    flags = "".join(re.findall(r"(?<!\S)-([A-Za-z]+)", "".join(head)))
    return "".join(head), found, True if "A" in flags else False if "a" in flags else None
