"""What may come next on a command line, read from the command's usage line.

`cut (-f LIST [-d DELIM] [-s] | -c LIST) [FILE...]` says that after `cut` comes `-f` or `-c`,
not a file; `queryCensys host IP... | cert SHA256... | search QUERY` that the first word is
one of three actions. Tab completion asks `expect(usage, words)` and offers those instead of
file names. The grammar is the usual one: `[optional]`, `(group)`, `a | b`, `WORD...` (one or
more), `-f VALUE` (a switch with its value), lowercase words to type as is, UPPERCASE words for
a value, `a|b|c` (no spaces) for a value out of a list. A usage this does not understand, or a
line it does not match, gives None, and completion falls back to file names as before.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cache

_PATH_WORDS = ("FILE", "PATH", "DIR", "SRC", "DEST", "SCRIPT")
_COMMAND_WORDS = ("COMMAND", "PROGRAM")
_LIMIT = 20000  # matching steps per call: a guard against a pathological usage line


@dataclass(frozen=True)
class Tok:
    kind: str  # "literal", "flag", "meta" or "values"
    text: str
    values: tuple[str, ...] = ()
    arg: Tok | None = None  # a flag's value (`-l LEVEL`)

    def matches(self, word: str) -> bool:
        if self.kind == "literal":
            return word == self.text
        if self.kind == "flag":
            return word == self.text
        if self.kind == "values":
            return not _is_flag(word)  # lenient: the list may not be the whole story
        return not _is_flag(word) or word == "-"  # a value

    @property
    def path(self) -> bool:
        return self.kind == "meta" and any(w in self.text.upper() for w in _PATH_WORDS)

    @property
    def command(self) -> bool:
        return self.kind == "meta" and self.text.rstrip(".").upper() in _COMMAND_WORDS


@dataclass(frozen=True)
class Node:
    kind: str  # "seq", "alt", "opt", "rep"
    items: tuple = ()


@dataclass
class Expect:
    """What may come next: literal words, switches (required by the usage, or optional ones that
    may come anywhere) and values (their metavars), and whether the line may end here."""
    literals: dict[str, str] = field(default_factory=dict)  # word → what it is
    flags: set[str] = field(default_factory=set)  # switches the usage needs at this point
    free_flags: set[str] = field(default_factory=set)  # optional switches, allowed anywhere
    metas: list[Tok] = field(default_factory=list)
    can_end: bool = False

    @property
    def wants_path(self) -> bool:
        return any(t.path for t in self.metas)

    @property
    def wants_command(self) -> bool:
        return any(t.command for t in self.metas)


def _is_flag(word: str) -> bool:
    return len(word) > 1 and word.startswith("-") and not word[1].isdigit()


_TOKEN = re.compile(r"\.\.\.|[\[\]()|]|[^\s\[\]()|]+(?:\|[^\s\[\]()|]+)*")


def _tokens(text: str) -> list[str]:
    """Split a usage line: brackets, parentheses and a spaced `|` are syntax; `a|b|c` is one word."""
    out: list[str] = []
    for m in _TOKEN.finditer(text):
        t = m.group()
        if t.endswith("...") and t != "...":
            out += [t[:-3], "..."]
        else:
            out.append(t)
    return out


class _Parser:
    def __init__(self, tokens: list[str], name: str):
        self.t, self.i, self.name = tokens, 0, name

    def peek(self) -> str | None:
        return self.t[self.i] if self.i < len(self.t) else None

    def take(self) -> str:
        self.i += 1
        return self.t[self.i - 1]

    def alt(self) -> Node:
        options = [self.seq()]
        while self.peek() == "|":
            self.take()
            options.append(self.seq())
        return options[0] if len(options) == 1 else Node("alt", tuple(options))

    def seq(self) -> Node:
        items: list = []
        while self.peek() not in (None, "|", "]", ")"):
            t = self.take()
            if t in ("[", "("):
                inner = self.alt()
                if self.take() != ("]" if t == "[" else ")"):
                    raise ValueError("unbalanced")
                if t == "(" and isinstance(inner, Tok) and "," in inner.text and items \
                        and isinstance(items[-1], Tok):  # `LEVEL (info,warn,crit)`: its values
                    last = items.pop()
                    items.append(_with_values(last, tuple(inner.text.split(","))))
                    continue
                item = Node("opt", (inner,)) if t == "[" else inner
            elif t == "...":
                if not items:
                    raise ValueError("... with nothing before it")
                items.append(Node("rep", (items.pop(),)))
                continue
            else:
                item = _word(t)
            items.append(item)
        items = _bind_flag_args(items)
        if len(items) == 1:
            return items[0]
        return Node("seq", tuple(items))


def _with_values(tok: Tok, values: tuple[str, ...]) -> Tok:
    if tok.kind == "flag" and tok.arg is not None:
        return Tok("flag", tok.text, arg=Tok("values", tok.arg.text, values))
    return Tok("values", tok.text, values)


def _word(t: str) -> Tok:
    if t.startswith("-") and len(t) > 1 and t[1] != "-" or t.startswith("--") and len(t) > 2:
        return Tok("flag", t.split("=")[0])
    if "|" in t:
        return Tok("values", t, tuple(v for v in t.split("|") if v))
    stem = t.split(".")[0]  # SCRIPT.py is a value too
    if any(c.isupper() for c in stem) and not any(c.islower() for c in stem) or t.startswith("+"):
        return Tok("meta", t)
    return Tok("literal", t)


def _bind_flag_args(items: list) -> list:
    """`-l LEVEL` → one switch that takes a value. A value that repeats (`-a PATH...`) is not one."""
    out: list = []
    for item in items:
        prev = out[-1] if out else None
        if isinstance(prev, Tok) and prev.kind == "flag" and prev.arg is None \
                and isinstance(item, Tok) and item.kind in ("meta", "values"):
            out[-1] = Tok("flag", prev.text, arg=item)
            continue
        out.append(item)
    return out


def _free(node) -> bool:
    """An optional group of nothing but switches (`[-n]`, `[-m N]`, `[-u | -U N]`): such switches
    may come anywhere on the line, as they do in practice."""
    if isinstance(node, Tok):
        return node.kind == "flag"
    return all(_free(i) for i in node.items)


def _flags(node, out: dict[str, Tok]) -> None:
    if isinstance(node, Tok):
        if node.kind == "flag":
            out.setdefault(node.text, node)
        return
    for item in node.items:
        _flags(item, out)


@dataclass(frozen=True)
class Grammar:
    root: Node | Tok
    free: dict  # free switch → its Tok (with .arg when it takes a value)
    flags: dict  # every switch in the usage → its Tok


def _strip_free(node, free: dict[str, Tok]):
    """The grammar without its free option groups (collected into `free`)."""
    if isinstance(node, Tok):
        return node
    if node.kind == "opt" and _free(node.items[0]):
        _flags(node.items[0], free)
        return None
    items = tuple(x for x in (_strip_free(i, free) for i in node.items) if x is not None)
    if node.kind in ("opt", "rep") and not items:
        return None
    if node.kind == "seq" and not items:
        return Node("seq", ())
    if node.kind == "alt" and len(items) < len(node.items):
        items = items + (Node("seq", ()),)  # an alternative that was only switches: now nothing
    return Node(node.kind, items)


@cache
def grammar(name: str, usage: str) -> Grammar | None:
    """The parsed usage of `name`, or None when it is not one this reads (shell syntax with
    `{ … }` or `; do`, unbalanced brackets)."""
    if not usage or any(s in usage for s in ("…", "{", ";", "()")):
        return None
    try:
        tokens = _tokens(usage)
        parser = _Parser(tokens, name)
        root = parser.alt()
        if parser.peek() is not None:
            return None
    except (ValueError, IndexError):
        return None
    # each alternative may repeat the command's name (`history [N] | history -c`): drop it
    branches = root.items if isinstance(root, Node) and root.kind == "alt" else (root,)
    trimmed = []
    for branch in branches:
        if isinstance(branch, Tok) and branch.kind == "literal" and branch.text == name:
            branch = Node("seq", ())
        elif isinstance(branch, Node) and branch.kind == "seq" and branch.items \
                and isinstance(branch.items[0], Tok) and branch.items[0].text == name:
            branch = Node("seq", branch.items[1:])
        trimmed.append(branch)
    root = trimmed[0] if len(trimmed) == 1 else Node("alt", tuple(trimmed))
    flags: dict[str, Tok] = {}
    _flags(root, flags)
    free: dict[str, Tok] = {}
    root = _strip_free(root, free) or Node("seq", ())
    return Grammar(root, free, flags)


def expect(name: str, usage: str, words: list[str], takes_value: dict[str, bool] | None = None) -> Expect | None:
    """What may follow `words` (typed after the command name, the word being typed left out),
    or None when the usage can't be read or the words don't fit it. `takes_value` tells, for
    switches the usage doesn't spell out, whether they take a value (from the command's docs)."""
    g = grammar(name, usage)
    if g is None:
        return None
    takes_value = takes_value or {}
    result = Expect()
    matched = False
    steps = 0

    def skip(i: int) -> list[int]:
        """The positions reachable from `i` by reading free switches (with their values)."""
        out = [i]
        j = i
        while j < len(words) and _is_flag(words[j]) and words[j] not in g.flags or \
                j < len(words) and words[j] in g.free:
            w = words[j].split("=")[0]
            tok = g.free.get(w) or g.flags.get(w)
            needs = tok.arg is not None if tok is not None else takes_value.get(w, False)
            j += 2 if needs and "=" not in words[j] else 1
            if j <= len(words):
                out.append(j)
        return out

    def walk(node, i: int, cont) -> None:
        nonlocal steps
        steps += 1
        if steps > _LIMIT:
            raise RecursionError
        if isinstance(node, Tok):
            for j in skip(i):
                if j == len(words):
                    _note(node)
                elif node.matches(words[j]):
                    if node.kind == "flag" and node.arg is not None:
                        if j + 1 == len(words):
                            continue  # its value is the word being typed: the completer handles that
                        cont(j + 2)
                    else:
                        cont(j + 1)
            return
        if node.kind == "seq":
            def chain(k: int, i: int) -> None:
                if k == len(node.items):
                    cont(i)
                else:
                    walk(node.items[k], i, lambda j: chain(k + 1, j))
            chain(0, i)
        elif node.kind == "alt":
            for item in node.items:
                walk(item, i, cont)
        elif node.kind == "opt":
            walk(node.items[0], i, cont)
            cont(i)
        elif node.kind == "rep":
            def again(j: int) -> None:
                cont(j)
                if j > i:
                    walk(node, j, cont)
            walk(node.items[0], i, again)

    def _note(tok: Tok) -> None:
        nonlocal matched
        matched = True
        if tok.kind == "literal":
            result.literals.setdefault(tok.text, "")
        elif tok.kind == "flag":
            result.flags.add(tok.text)
        elif tok.kind == "values":
            for v in tok.values:
                result.literals.setdefault(v, tok.text.lower() if "|" not in tok.text else "")
        elif all(t.text != tok.text for t in result.metas):
            result.metas.append(tok)

    def done(i: int) -> None:
        nonlocal matched
        if len(words) in skip(i):
            matched = True
            result.can_end = True

    try:
        walk(g.root, 0, done)
    except RecursionError:
        return None
    if not matched:
        return None
    used = {w.split("=")[0] for w in words}
    result.free_flags = {f for f in g.free if f not in used}
    return result
