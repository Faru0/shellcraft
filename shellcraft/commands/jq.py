"""jq: look inside JSON — pretty-print it, list its keys, pick values out with a Python-style path.

Not the real jq language: the filter is the subscript you would write in Python after the
loaded value (`['name']`, `[5]['name']`, `['items'][0]['id']`), plus `.name` / `.items[0]`
shortcuts and `[*]` for every element of a list. `..key` finds a key at any depth, and the
walkers below are iterative, so any depth of nesting is fine.
"""

from __future__ import annotations

import ast
import difflib
import json
import os
import re
from collections import deque
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from rich.json import JSON as RichJSON
from rich.text import Text

from shellcraft.builtins import builtin
from shellcraft.commands._io import read_file
from shellcraft.context import Styled, WithStatus
from shellcraft.modkit import ArgParser, ModuleError

if TYPE_CHECKING:
    from shellcraft.context import ShellContext

WILDCARD = object()  # `[*]` / `-i *`: every element (or every value of an object)


# ── loading ──────────────────────────────────────────────────────────────────

def load(text: str, source: str = "stdin", xml: bool = False) -> Any:
    """JSON, else JSON Lines (one value per line, as a list), else a Python literal (single quotes,
    True / None), which is what `print(dict)` writes; XML with `xml` (-x), or by itself when the
    text starts with a tag."""
    text = text.strip()
    if not text:
        raise ModuleError(f"jq: {source}: no input (pipe JSON in, or give a FILE)")
    if xml or text.startswith(("<?xml", "<!DOCTYPE")) or (text.startswith("<") and text.endswith(">")):
        return xml_to_data(text, source)
    try:
        return json.loads(text)
    except RecursionError:
        raise ModuleError(f"jq: {source}: nested too deeply for the JSON parser") from None
    except ValueError as first:
        lines = [line for line in text.splitlines() if line.strip()]
        if len(lines) > 1:
            try:
                return [json.loads(line) for line in lines]
            except ValueError:
                pass
        try:
            value = ast.literal_eval(text)
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            raise ModuleError(f"jq: {source}: not valid JSON ({first.msg} at line {first.lineno}, "
                              f"column {first.colno})") from None
        if isinstance(value, (dict, list, tuple, str, int, float, bool)) or value is None:
            return list(value) if isinstance(value, tuple) else value
        raise ModuleError(f"jq: {source}: not valid JSON") from None


def xml_to_data(text: str, source: str = "stdin") -> Any:
    """XML as JSON-style data, the way xmltodict does it: `{root: {...}}`; an element with
    children or attributes is an object (attributes as `@name`, its own text as `#text`), an
    element with text alone is that text, an empty one is null, and children that share a tag
    become a list."""
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ModuleError(f"jq: {source}: not valid XML ({exc})") from None
    return {_tag(root): _element(root)}


def _tag(element: Any) -> str:
    tag = element.tag
    return tag.rsplit("}", 1)[1] if isinstance(tag, str) and tag.startswith("{") else str(tag)


def _element(element: Any) -> Any:
    out: dict[str, Any] = {f"@{k}": v for k, v in element.attrib.items()}
    for child in element:
        key, value = _tag(child), _element(child)
        if key in out:
            if not isinstance(out[key], list):
                out[key] = [out[key]]
            out[key].append(value)
        else:
            out[key] = value
    text = (element.text or "").strip()
    if not out:
        return text or None
    if text:
        out["#text"] = text
    return out


# ── the filter ───────────────────────────────────────────────────────────────

Pair = tuple[str, Any]  # (path that reaches a value, the value): `.items[0].tags[1]`, "" for the root
CONTAINERS = (dict, list)
_NAME = r"[A-Za-z_][A-Za-z0-9_-]*"
_IDENT = re.compile(_NAME)
_STEP = re.compile(rf"\s*(?:(?P<rec>\.\.)(?P<recname>{_NAME})?|\[(?P<bracket>[^\]]*)\]|\.?(?P<name>{_NAME})"
                   rf"|\.(?P<num>-?\d+)|(?P<dot>\.))")


class Recurse:
    """`..` (this value and everything below it) or `..key` (the value of `key` at any depth)."""

    def __init__(self, key: str | None = None):
        self.key = key

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Recurse) and other.key == self.key

    __hash__ = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        return f"Recurse({self.key!r})"


class Elided(str):
    """A summary that stands for a container below the `--depth` limit: `{…3 keys}`."""


def seg_key(key: Any) -> str:
    """One path step for a key: `.name`, or `["two words"]` when it is not a plain name."""
    text = str(key)
    return f".{text}" if _IDENT.fullmatch(text) else f"[{json.dumps(text, ensure_ascii=False)}]"


def elide(value: Any) -> Elided:
    n = len(value)
    return Elided(f"{{…{n} key{'s' if n != 1 else ''}}}" if isinstance(value, dict)
                  else f"[…{n} item{'s' if n != 1 else ''}]")


def parse_path(text: str) -> list[Any]:
    """`['a'][0]["b c"]`, `[a][0]`, `.a.b[0]`, `a.b`, `[*]`, `[1:3]`, `..name` (that key at any
    depth), `..` (everything below) → a list of steps: str keys, int indexes, slices, WILDCARD or
    Recurse. Quotes are optional inside brackets (the shell removes them): a bare word is a key,
    a number an index."""
    steps: list[Any] = []
    i, text = 0, text.strip()
    if text in ("", "."):
        return steps
    while i < len(text):
        m = _STEP.match(text, i)
        if not m or m.end() == i:
            raise ModuleError(f"jq: bad filter at '{text[i:]}' (examples: ['name'], [0]['name'], .items[0].id, "
                              "[*].tags[*], ..name)")
        i = m.end()
        if m.group("rec"):
            steps.append(Recurse(m.group("recname")))
        elif m.group("dot"):
            continue
        elif m.group("num") is not None:
            steps.append(int(m.group("num")))
        elif m.group("name") is not None:
            steps.append(m.group("name"))
        else:
            steps.append(_bracket(m.group("bracket").strip()))
    return steps


def _bracket(inner: str) -> Any:
    if inner in ("*", ""):
        return WILDCARD
    if re.fullmatch(r"-?\d*:-?\d*", inner):
        start, _, stop = inner.partition(":")
        return slice(int(start) if start else None, int(stop) if stop else None)
    if re.fullmatch(r"-?\d+", inner):
        return int(inner)
    try:
        value = ast.literal_eval(inner)
    except (ValueError, SyntaxError):
        return inner  # a bare word: the shell took its quotes away
    if isinstance(value, (str, int)):
        return value
    raise ModuleError(f"jq: [{inner}]: use a key or a number inside brackets")


def walk(pairs: list[Pair], steps: list[Any], problems: list[str]) -> list[Pair]:
    """Follow the steps from each (path, value). A missing key or index under a wildcard is
    skipped and reported in `problems`, so `-i *` keeps going past an element without it."""
    for step in steps:
        out: list[Pair] = []
        for path, value in pairs:
            out.extend(_step(path, value, step, problems))
        pairs = out
    return pairs


def apply(values: list[Any], steps: list[Any]) -> tuple[list[Any], list[str]]:
    """walk() without the paths: (results, problems)."""
    problems: list[str] = []
    pairs = walk([("", v) for v in values], steps, problems)
    return [v for _, v in pairs], problems


def _at(text: str, path: str) -> str:
    return f"{text} (at {path})" if path else text


def _step(path: str, value: Any, step: Any, problems: list[str]) -> list[Pair]:
    if isinstance(step, Recurse):
        return _descend(path, value, step.key, problems)
    if step is WILDCARD:
        if isinstance(value, list):
            return [(f"{path}[{i}]", v) for i, v in enumerate(value)]
        if isinstance(value, dict):
            return [(path + seg_key(k), v) for k, v in value.items()]
        problems.append(_at(f"[*] on a {_kind(value)} ({_short(value)}): nothing to go through here", path))
        return []
    if isinstance(step, slice):
        if isinstance(value, list):
            a = "" if step.start is None else step.start
            b = "" if step.stop is None else step.stop
            return [(f"{path}[{a}:{b}]", value[step])]
        problems.append(_at(f"a slice ([a:b]) needs a list, but this is {_kind(value)}", path))
        return []
    if isinstance(value, dict):
        key = step if isinstance(step, str) else str(step)
        if key in value:
            return [(path + seg_key(key), value[key])]
        if isinstance(step, int) and step in value:
            return [(f"{path}[{step}]", value[step])]
        problems.append(_missing_key(path, value, step))
        return []
    if isinstance(value, list):
        index = step if isinstance(step, int) else int(step) if str(step).lstrip("-").isdigit() else None
        if index is None:
            problems.append(_key_on_list(path, value, str(step)))
            return []
        if -len(value) <= index < len(value):
            return [(f"{path}[{index}]", value[index])]
        n = len(value)
        problems.append(_at(f"index {index} out of range (a list of {n}): use 0..{n - 1} or -{n}..-1", path))
        return []
    problems.append(_at(f"'{step}' on a {_kind(value)} ({_short(value)}): nothing below this level", path))
    return []


def _descend(path: str, value: Any, key: str | None, problems: list[str]) -> list[Pair]:
    """`..` / `..key`: walk everything below (and including) `value`, in document order, without
    recursion: `..key` gives the value of that key wherever it is, `..` every value."""
    out: list[Pair] = []
    stack: list[tuple[str, Any, bool]] = [(path, value, key is None)]
    while stack:
        p, node, emit = stack.pop()
        if emit:
            out.append((p, node))
        if isinstance(node, dict):
            children = [(p + seg_key(k), v, key is None or str(k) == key) for k, v in node.items()]
        elif isinstance(node, list):
            children = [(f"{p}[{i}]", v, key is None) for i, v in enumerate(node)]
        else:
            continue
        stack.extend(reversed(children))
    if not out and key is not None:
        close = difflib.get_close_matches(key, _all_keys(value), n=1, cutoff=0.7)
        problems.append(_at(f"no key '{key}' at any depth" + (f"; did you mean '{close[0]}'?" if close else ""), path))
    return out


def _all_keys(value: Any, budget: int = 50_000) -> list[str]:
    """Distinct keys anywhere in the data (breadth first, bounded), for "did you mean"."""
    seen: dict[str, None] = {}
    queue: deque[Any] = deque([value])
    while queue and budget > 0:
        node = queue.popleft()
        budget -= 1
        if isinstance(node, dict):
            for k, v in node.items():
                seen.setdefault(str(k), None)
                if isinstance(v, CONTAINERS):
                    queue.append(v)
        elif isinstance(node, list):
            queue.extend(e for e in node if isinstance(e, CONTAINERS))
    return list(seen)


def locate(value: Any, key: str, limit: int = 3, budget: int = 50_000) -> list[str]:
    """Where `key` is below `value`: the shallowest paths first, at most `limit`."""
    found: list[str] = []
    queue: deque[tuple[str, Any]] = deque([("", value)])
    while queue and len(found) < limit and budget > 0:
        path, node = queue.popleft()
        budget -= 1
        if isinstance(node, dict):
            for k, v in node.items():
                if str(k) == key and len(found) < limit:
                    found.append(path + seg_key(k))
                if isinstance(v, CONTAINERS):
                    queue.append((path + seg_key(k), v))
        elif isinstance(node, list):
            queue.extend((f"{path}[{i}]", e) for i, e in enumerate(node) if isinstance(e, CONTAINERS))
    return found


def _missing_key(path: str, value: dict, step: Any) -> str:
    keys = ", ".join(map(str, list(value)[:12])) + (" …" if len(value) > 12 else "")
    if not isinstance(step, str):
        first = next(iter(value), None)
        text = f"[{step}] on an object with keys {keys or 'none'}: an object is read by key, not by position"
        if first is not None:
            text += f"; try {path}{seg_key(first)}"
        return _at(text, path)
    text = f"no key '{step}' (keys: {keys or 'none'})"
    close = difflib.get_close_matches(step, [str(k) for k in value], n=1, cutoff=0.7)
    if close:
        text += f"; did you mean '{close[0]}'?"
    found = locate(value, step)
    if found:
        text += f"; '{step}' is deeper, first at {path}{found[0]}: try -R {step} to find it at any depth"
    return _at(text, path)


def _key_on_list(path: str, value: list, key: str) -> str:
    """A key asked of a list: say what the list holds, and the form that works."""
    n = len(value)
    brief = _level(value, None)[0]
    text = f"'{key}' on a {brief[0].lower() + brief[1:]}"
    sample = [e for e in value[:200] if isinstance(e, dict)]
    if sample and any(key in e for e in sample):
        text += f": '{key}' is inside each element; try {path}[*]{seg_key(key)}"
        if not path:
            text += f" or -i '*' -f {key}"
    elif sample:
        found = locate(value, key)
        text += f": the elements have no '{key}'"
        text += (f" at this level; first deeper at {path}{found[0]}: try -R {key}" if found
                 else f"; use a number (0..{n - 1}) or [*] to look inside each")
    else:
        text += f": use a number (0..{n - 1}) or [*] to look inside each"
    return _at(text, path)


def _kind(value: Any) -> str:
    return {dict: "object", list: "list", str: "string", bool: "boolean", int: "number", float: "number"}.get(
        type(value), "null" if value is None else type(value).__name__)


def _dumps(value: Any, **kwargs: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, **kwargs)
    except RecursionError:
        raise ModuleError("jq: nested too deeply to print as it is: use -d N to stop at a depth, -F to "
                          "flatten, or -R KEY to find a key") from None


def _short(value: Any) -> str:
    text = _dumps(value)
    return text if len(text) <= 40 else text[:39] + "…"


# ── output ───────────────────────────────────────────────────────────────────

def _as_text(value: Any, compact: bool) -> str:
    """A string or number as itself (handy in pipes), objects and lists as JSON."""
    if isinstance(value, str):
        return value
    if isinstance(value, CONTAINERS):
        return _dumps(value, indent=None if compact else 2, separators=(",", ":") if compact else None)
    return _dumps(value)


# ── the shape of the data ────────────────────────────────────────────────────

def shape(value: Any) -> str:
    """What the data is, in a line: `object with 3 keys: name, items, ok`, `list of 5 objects
    (keys: name, age)`, `list of 12 numbers`, `string`…"""
    if isinstance(value, dict):
        keys = ", ".join(map(str, list(value)[:8])) + (" …" if len(value) > 8 else "")
        return f"object with {len(value)} key{'s' if len(value) != 1 else ''}" + (f": {keys}" if value else "")
    if isinstance(value, list):
        if not value:
            return "empty list"
        kinds = {_kind(v) for v in value}
        n = len(value)
        if kinds == {"object"}:
            keys: list[str] = []
            for item in value:
                keys += [str(k) for k in item if str(k) not in keys]
            shown = ", ".join(keys[:8]) + (" …" if len(keys) > 8 else "")
            return f"list of {n} object{'s' if n != 1 else ''} (keys: {shown})"
        if kinds == {"list"}:
            return f"list of {n} list{'s' if n != 1 else ''}"
        if len(kinds) == 1:
            kind = kinds.pop()
            return f"list of {n} {kind}{'s' if n != 1 else ''}"
        return f"list of {n} mixed values ({', '.join(sorted(kinds))})"
    return _kind(value)


NESTED = 4  # container levels from which the data counts as deeply nested (dict -> list -> dict -> list)
OUTLINE_LEVELS = 8  # levels the outline follows before it says `…`


def _depths(root: Any) -> dict[int, int]:
    """For every container below `root` (by id): how many container levels it holds, itself
    included (a dict of scalars is 1, a list of those 2…). Iterative: any depth is fine."""
    depth: dict[int, int] = {}
    stack: list[tuple[Any, bool]] = [(root, False)]
    while stack:
        node, done = stack.pop()
        if not isinstance(node, CONTAINERS):
            continue
        kids = node.values() if isinstance(node, dict) else node
        if done:
            depth[id(node)] = 1 + max((depth.get(id(k), 0) for k in kids if isinstance(k, CONTAINERS)), default=0)
        elif id(node) not in depth:
            stack.append((node, True))
            stack.extend((k, False) for k in kids if isinstance(k, CONTAINERS) and id(k) not in depth)
    return depth


def nesting(value: Any) -> int:
    """How many container levels the data has: 0 for a scalar, 3 for dict -> list -> dict."""
    return _depths(value).get(id(value), 0) if isinstance(value, CONTAINERS) else 0


def _deepest(pairs: Any, depths: dict[int, int] | None) -> tuple[str, Any] | None:
    if depths is None:
        return None
    best: tuple[str, Any] | None = None
    best_depth = 0
    for label, node in pairs:
        d = depths.get(id(node), 0)
        if d > best_depth:
            best, best_depth = (label, node), d
    return best


def _level(node: Any, depths: dict[int, int] | None) -> tuple[str, tuple[str, Any] | None, str]:
    """One level of the outline: (what it is, the deepest container below as (label, node) or None,
    the compact chain word). `depths` None describes the level only."""
    if isinstance(node, dict):
        keys = [str(k) for k in node]
        extra = f",+{len(keys) - 6}" if len(keys) > 6 else ""
        children = ((str(k), v) for k, v in node.items() if isinstance(v, CONTAINERS))
        return "Object{" + ",".join(keys[:6]) + extra + "}", _deepest(children, depths), "dict"
    if isinstance(node, list):
        n = len(node)
        if n == 0:
            return "Empty list", None, "list[0]"
        kinds = {_kind(v) for v in node}
        s = "s" if n != 1 else ""
        if kinds == {"object"}:
            keys: dict[str, None] = {}
            for item in node[:2000]:
                for k in item:
                    keys.setdefault(str(k), None)
            names = list(keys)
            extra = f",+{len(names) - 6}" if len(names) > 6 else ""
            children = ((str(k), v) for item in node for k, v in item.items() if isinstance(v, CONTAINERS))
            return (f"List of {n} object{s}" + "{" + ",".join(names[:6]) + extra + "}",
                    _deepest(children, depths), f"list[{n}] -> dict")
        if kinds == {"list"}:
            return f"List of {n} list{s}", _deepest((("[*]", e) for e in node), depths), f"list[{n}]"
        if len(kinds) == 1:
            return f"List of {n} {kinds.pop()}{s}", None, f"list[{n}]"
        return (f"List of {n} mixed ({', '.join(sorted(kinds))})",
                _deepest((("[*]", e) for e in node if isinstance(e, CONTAINERS)), depths), f"list[{n}]")
    return _kind(node), None, _kind(node)


def spine(value: Any, cap: int = OUTLINE_LEVELS, depths: dict[int, int] | None = None) -> tuple[str, str, int, str]:
    """Follow the deepest branch of the data: (outline, chain, levels, a key from the last level).
    outline: `object{name,items} -> items: list of 12 objects{id,tags} -> tags: list of 3 strings`;
    chain: `dict -> list[12] -> dict -> list[3]`."""
    depths = _depths(value) if depths is None else depths
    levels = depths.get(id(value), 0)
    parts: list[str] = []
    chain: list[str] = []
    node, label = value, None
    last_key = ""
    for _ in range(cap):
        text, nxt, word = _level(node, depths)
        text = text[0].lower() + text[1:]
        parts.append(f"{label}: {text}" if label else text)
        chain.append(word)
        last_key = _first_key(node) or (label or last_key)
        if nxt is None:
            break
        label, node = nxt
    else:
        parts.append("…")
        chain.append("…")
    return " -> ".join(parts), " -> ".join(chain), levels, last_key


def _first_key(node: Any) -> str:
    """A plain key of this level (for a hint such as `jq -R id`), else ""."""
    for item in (node if isinstance(node, list) else [node])[:50]:
        if isinstance(item, dict):
            for k in item:
                if _IDENT.fullmatch(str(k)):
                    return str(k)
    return ""


def describe(value: Any) -> list[str]:
    """The lines `-s` prints: the one-line shape, or for deeply nested data the outline of the
    deepest branch and the chain of kinds with how many levels there are."""
    if nesting(value) < NESTED:
        return [shape(value)]
    outline, chain, levels, _ = spine(value)
    return [outline, f"{chain}   ({levels} levels)"]


def hints(value: Any) -> str:
    """How to dig in, for the header on screen: the commands that fit the shape."""
    base = ""
    if isinstance(value, dict):
        first = next(iter(value), None)
        base = f"jq -k · jq -f {first}" if first is not None and str(first).isidentifier() else "jq -k"
    elif isinstance(value, list) and value:
        if all(isinstance(v, dict) for v in value):
            key = next((str(k) for k in value[0] if str(k).isidentifier()), None)
            base = "jq [0] · jq -t" + (f" · jq -i '*' -f {key}" if key else "") + " · jq -n"
        else:
            base = f"jq [0] · jq [{len(value) - 1}] · jq -n"
    if isinstance(value, CONTAINERS):
        depths = _depths(value)
        levels = depths.get(id(value), 0)
        if levels >= NESTED:
            key = spine(value, depths=depths)[3]
            more = ["jq -F"] + ([f"jq -R {key}"] if key else []) + [f"jq -k -d {min(levels, 4)}"]
            base = " · ".join([base, *more] if base else more)
    return base


def _looks_like_path(word: str) -> bool:
    return word in (".", "..") or (word.startswith(("[", ".")) and not os.path.exists(word))


def _keys(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(k) for k in value]
    if isinstance(value, list):
        return [str(i) for i in range(len(value))]
    return []


# ── -w / --where, -W / --where-any ───────────────────────────────────────────

_WHERE = re.compile(r"^\s*(?P<path>.+?)\s*(?P<op>==|!=|>=|<=|=|>|<|~)\s*(?P<value>.*?)\s*$", re.S)
_WHERE_VALUE = re.compile(r"^\s*(?P<op>==|!=|>=|<=|=|>|<|~)\s*(?P<value>.*?)\s*$", re.S)


def parse_where(text: str) -> tuple[list[Any], str | None, Any]:
    """`age>30`, `name=ada`, `name~AD` (contains, any case), `tags` (the key is there) →
    (path, operator, value). The value is JSON when it parses (30, true, null), else text. With
    no path (`~timeout`, `=error`) it is a condition on the value itself, for -W."""
    m = _WHERE_VALUE.match(text) or _WHERE.match(text)
    if not m:
        return parse_path(text), None, None
    raw = m.group("value")
    try:
        value = json.loads(raw)
    except ValueError:
        value = raw[1:-1] if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"" else raw
    path = m.groupdict().get("path")
    return (parse_path(path) if path else []), m.group("op"), value


def matches(item: Any, condition: tuple[list[Any], str | None, Any]) -> bool:
    path, op, want = condition
    found, _ = apply([item], path)
    if not found:
        return False
    have = found[0]
    if op is None:
        return True
    if op in ("=", "=="):
        return have == want or _as_text(have, True) == _as_text(want, True)
    if op == "!=":
        return not (have == want or _as_text(have, True) == _as_text(want, True))
    if op == "~":
        return _as_text(want, True).lower() in _as_text(have, True).lower()
    if _is_number(want):  # a number is compared with numbers: text that reads as one, else no match
        a = have if _is_number(have) else _numeric(have)
        if a is None:
            return False
        b = want
    else:
        a, b = _as_text(have, True), _as_text(want, True)
    return {">": a > b, "<": a < b, ">=": a >= b, "<=": a <= b}[op]


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _numeric(value: Any) -> float | None:
    """The number a string reads as ("12", "3.5"), else None."""
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def where_any(pairs: list[Pair], conditions: list[tuple[list[Any], str | None, Any]]) -> list[Pair]:
    """-W: every value at any depth that meets all the conditions, in document order. A condition
    with a path (`status=error`, `tags`) is about objects that hold it; one without (`~timeout`,
    `>100`) about the scalar values themselves."""
    def applies(node: Any, cond: tuple[list[Any], str | None, Any]) -> bool:
        container = isinstance(node, CONTAINERS)
        return container == bool(cond[0]) and matches(node, cond)

    out: list[Pair] = []
    for path, root in pairs:
        stack: list[Pair] = [(path, root)]
        while stack:
            p, node = stack.pop()
            if all(applies(node, c) for c in conditions):
                out.append((p, node))
            if isinstance(node, dict):
                stack.extend(reversed([(p + seg_key(k), v) for k, v in node.items()]))
            elif isinstance(node, list):
                stack.extend(reversed([(f"{p}[{i}]", v) for i, v in enumerate(node)]))
    return out


# ── -p / --paths, -F / --flatten, -d / --depth ───────────────────────────────

def limit_depth(value: Any, depth: int) -> Any:
    """A copy of the data with everything deeper than `depth` container levels replaced by a
    summary (`{…3 keys}`, `[…12 items]`). Iterative."""
    if not isinstance(value, CONTAINERS):
        return value
    if depth < 1:
        return elide(value)
    root: Any = {} if isinstance(value, dict) else []
    stack: list[tuple[Any, Any, int]] = [(value, root, 1)]
    while stack:
        src, dst, level = stack.pop()
        for k, v in (src.items() if isinstance(src, dict) else enumerate(src)):
            if isinstance(v, CONTAINERS):
                if level >= depth:
                    new: Any = elide(v)
                else:
                    new = {} if isinstance(v, dict) else []
                    stack.append((v, new, level + 1))
            else:
                new = v
            if isinstance(dst, dict):
                dst[k] = new
            else:
                dst.append(new)
    return root


def leaves(value: Any, prefix: str = "", depth: int | None = None) -> Iterator[tuple[str, Any]]:
    """(path, leaf) for every scalar and empty container, in document order. With `depth`, a
    container deeper than that is a leaf too (an Elided summary). Iterative."""
    stack: list[tuple[str, Any, int]] = [(prefix, value, 1)]
    while stack:
        path, node, level = stack.pop()
        if isinstance(node, CONTAINERS) and node:
            if depth is not None and level > depth:
                yield path, elide(node)
                continue
            if isinstance(node, dict):
                children = [(path + seg_key(k), v, level + 1) for k, v in node.items()]
            else:
                children = [(f"{path}[{i}]", v, level + 1) for i, v in enumerate(node)]
            stack.extend(reversed(children))
        else:
            yield path, node


def _leaf_json(leaf: Any) -> str:
    return str(leaf) if isinstance(leaf, Elided) else _dumps(leaf)


def leaf_pairs(value: Any, prefix: str = "", depth: int | None = None) -> list[tuple[str, str]]:
    """(path, JSON of the value) for -p: each path works back in as a `-f` filter."""
    return [(path or ".", _leaf_json(leaf)) for path, leaf in leaves(value, prefix, depth)]


def leaf_paths(value: Any, prefix: str = "", depth: int | None = None) -> list[str]:
    """Every value with the path that reaches it, written so it can go back into -f."""
    return [f"{path} = {text}" for path, text in leaf_pairs(value, prefix, depth)]


def _flat_text(leaf: Any) -> str:
    """A leaf for -F: a string as it is, a number or boolean as JSON, an empty container as {} / []."""
    return str(leaf) if isinstance(leaf, str) else _dumps(leaf)


def flat_items(value: Any, prefix: str = "", depth: int | None = None) -> list[tuple[str, str]]:
    """-F: `a.b[0].c` and the value, for every leaf (no leading dot; `value` for a bare scalar)."""
    return [(path[1:] if path.startswith(".") else (path or "value"), _flat_text(leaf))
            for path, leaf in leaves(value, prefix, depth)]


def key_paths(value: Any, depth: int, prefix: str = "") -> list[str]:
    """-k -d N: the key outline, `items[*].tags[*].label`: every key path down to N key levels,
    a list shown once as `[*]` however long it is. Iterative."""
    seen: dict[str, None] = {}
    stack: list[tuple[str, Any, int]] = [(prefix, value, 1)]
    while stack:
        path, node, level = stack.pop()
        if isinstance(node, list):
            stack.extend((path + "[*]", e, level) for e in reversed(node[:5000]) if isinstance(e, CONTAINERS))
            continue
        if not isinstance(node, dict) or level > depth:
            continue
        items = [(path + seg_key(k), v) for k, v in node.items()]
        for p, _ in items:
            seen.setdefault(p[1:] if p.startswith(".") and not prefix else p, None)
        stack.extend((p, v, level + 1) for p, v in reversed(items) if isinstance(v, CONTAINERS))
    return list(seen)


# ── -t / --table ─────────────────────────────────────────────────────────────

def table_rows(results: list[Any], spread: bool) -> tuple[list[str], list[list[str]]]:
    """Columns and rows: a list of objects is one row each (the columns are every key seen, in
    order), a single object is key/value pairs, anything else one value per row."""
    items = results if spread or len(results) != 1 else results[0]
    if isinstance(items, dict):
        return ["key", "value"], [[str(k), _cell(v)] for k, v in items.items()]
    if not isinstance(items, list):
        items = [items]
    if items and all(isinstance(i, dict) for i in items):
        columns: list[str] = []
        for item in items:
            columns += [str(k) for k in item if str(k) not in columns]
        return columns, [[_cell(item[c]) if c in item else "" for c in columns]
                         for item in ({str(k): v for k, v in i.items()} for i in items)]
    return ["value"], [[_cell(i)] for i in items]


MAX_COLUMNS = 40


def flat_table(results: list[Any], spread: bool, depth: int | None) -> tuple[list[str], list[list[str]]]:
    """-F -t: like table_rows, but every nested key becomes a column (`team.lead.name`), so a list
    of deep records is one flat row each. A single object is path / value rows."""
    items = results if spread or len(results) != 1 else results[0]
    if isinstance(items, dict):
        return ["key", "value"], [[k, _cell(v)] for k, v in flat_items(items, "", depth)]
    if not isinstance(items, list):
        items = [items]
    flats = [dict(flat_items(i, "", depth)) if isinstance(i, CONTAINERS) else {"value": _flat_text(i)}
             for i in items]
    columns: dict[str, None] = {}
    for flat in flats:
        for k in flat:
            columns.setdefault(k, None)
    names = list(columns)
    more = len(names) - MAX_COLUMNS
    if more > 0:
        names = names[:MAX_COLUMNS]
    rows = [[_cell(flat.get(c, "")) for c in names] for flat in flats]
    if more > 0:
        names.append(f"… +{more} more")
        rows = [row + [""] for row in rows]
    return names, rows


def _cell(value: Any) -> str:
    text = _as_text(value, compact=True)
    return text if len(text) <= 60 else text[:59] + "…"


def _table_out(ctx: ShellContext, columns: list[str], rows: list[list[str]]) -> Any:
    plain = "".join("\t".join(row) + "\n" for row in [columns, *rows])
    if ctx.ui is None or not ctx.interactive:
        return plain
    from rich.table import Table

    table = Table(header_style="sc.accent", border_style="sc.muted")
    for column in columns:
        table.add_column(column, overflow="fold")
    for row in rows:
        table.add_row(*row)
    return Styled(table, plain)


def _lines_out(ctx: ShellContext, pairs: list[tuple[str, str]]) -> Any:
    """`path = value` lines: plain in a pipe, the path in color on screen."""
    plain = "".join(f"{p} = {t}\n" for p, t in pairs)
    if ctx.ui is None or not ctx.interactive:
        return plain
    text = Text()
    for p, t in pairs:
        text.append(p, style="sc.accent")
        text.append(" = ", style="sc.muted")
        text.append(t + "\n")
    return Styled(text, plain)


@builtin("jq", "Pretty-print JSON, list its keys, or pick values out of it",
         "jq [FILE] [-r FILE] [-x] [-i N|*] [-w COND]... [-W COND]... [-R KEY] [-f FILTER] "
         "[-k|-p|-t|-n|-s|-F] [-d N] [-P] [-c]", category="text", doc="""\
# jq

Look inside JSON: pretty-print it, list its keys, or pick values out with a path written the way
you would in Python after `json.loads`. It reads a FILE, or what is piped in. JSON Lines (one
object per line), Python-style dictionaries (single quotes, `True`, `None`) and **XML** (`-x`, or
by itself when the input starts with a tag) work too: XML becomes JSON-style data first.

This is ShellCraft's own jq, not the jq language: the filter is a Python subscript. It copes with
any depth of nesting (object in list in object in list…): see **Deep nesting** below.

## Synopsis

```
jq [FILE]                       pretty-print, with a line saying what the data is and how to dig in
jq [FILE] -s                    just that: the shape, and for deep data an outline of its nesting
jq [FILE] [3]                   element 3 of a list (a path on its own works like -f)
jq [FILE] -f FILTER             pick a value: -f ['name'], -f [5]['name'], -f .items[0].id
jq [FILE] -f '[*].items[*].id'  through any number of levels at once; -f ..id finds id at any depth
jq [FILE] -R KEY                every KEY at any depth, wherever it is
jq [FILE] -i N -f FILTER        the same, inside element N of a list
jq [FILE] -i '*' -f FILTER      the same, for every element of a list
jq [FILE] -k                    the keys (with -i: of each element; with -d N: a key outline)
jq [FILE] -w 'age>30' -t        the elements of a list that match, as a table
jq [FILE] -W 'status=error'     every object at any depth that matches
jq [FILE] -p                    every value with its path
jq [FILE] -F                    the same, flat: a.b[0].c = value
```

## Options

| Option | Meaning |
| --- | --- |
| `-f FILTER`, `--filter FILTER` | The path to pick: `['name']`, `[0]['name']`, `['items'][2]['id']`, `.items[2].id`, `[*]['name']` (every element), `[1:3]` (a slice), `[*].items[*].tags[*]` (many levels), `..name` (that key at any depth), `..` (every value). Quotes inside the brackets are optional. |
| `-i N`, `--index N` | Start inside element N of a list (`-i 0`, `-i -1` for the last). `-i '*'` (or `-i all`) means every element, and `-i 2:5` a range. |
| `-R KEY`, `--find KEY` | Every `KEY` at any depth, like `-f ..KEY`. Combine it with a path to search below it: `-f .departments -R skills`. |
| `-k`, `--keys` | The keys of the result, one per line (the indexes of a list). With `-i '*'`: each element's keys, under its index. With `-d N`: the key outline down to N levels, `items[*].tags[*].label`. |
| `-r FILE`, `--read FILE` | Read FILE (the same as giving FILE on its own). |
| `-x`, `--xml` | The input is XML: convert it to JSON-style data first, then do everything else. `{root: {…}}`; attributes as `@name`, an element's own text as `#text` when it also has children or attributes, text alone as the value, an empty element as null, children sharing a tag as a list. Values stay strings. |
| `-s`, `--shape` | Say what the data is: `object with 3 keys: name, items, ok`, `list of 5 objects (keys: name, age)`, `list of 12 numbers`, `string`. For data nested four levels or more: the outline of its deepest branch and the chain of kinds. After a filter, it describes what the filter found. |
| `-c`, `--compact` | Objects and lists on one line each, without indentation. |
| `-n`, `--count` | How many results the filter gives (the length of a single list or object). |
| `-w COND`, `--where COND` | Keep only the list elements that match COND (give it more than once: all must match). COND is `PATH OP VALUE` with OP one of `=` `!=` `>` `<` `>=` `<=` `~` (contains, any case), or just `PATH` (the key is there). Quote it when it has `>` or `<`: `-w 'age>30'`. On a list it implies `-i '*'`. |
| `-W COND`, `--where-any COND` | Like `-w`, but at any depth: every object that holds a matching key (`-W 'status=error'`, `-W level>=4`), or with no path every scalar value that matches (`-W '~timeout'`, `-W '>100'`). More than one: all must match. Combine with `-f` to pick from what it finds. |
| `-t`, `--table` | A list of objects as a table, one row each (a column for every key); a single object as key/value rows. Tab-separated with a header line in a pipe. |
| `-p`, `--paths` | Every value with the path that reaches it, one per line (`.items[0].id = 7`): a map of the document, and each path works as a `-f` filter. |
| `-F`, `--flatten` | Flat lines for every value: `departments[0].teams[1].lead.name = ada`, strings as they are, no leading dot, easy to `grep`. With `-t`: one flat row per record, a column for every nested key (`team.lead.name`). |
| `-d N`, `--depth N` | Stop at N levels: the pretty print shows `{…3 keys}` / `[…12 items]` below, `-p` and `-F` list those instead of their contents, and `-k` becomes the key outline. |
| `-P`, `--with-paths` | Print `path = value` for each result, so you see where every match lives. With `-p` and `-F`: paths from the root, not from the result. |

## Deep nesting

Data is often dict -> list -> dict -> list, any number of times. Spelling that out is the slow way;
let `jq` find its way:

```
jq -s                           # what is in here? an outline of the deepest branch:
                                #   object{company} -> company: object{name,departments} -> departments:
                                #   list of 2 objects{name,teams} -> teams: list of 3 objects{name,members} -> …
                                #   dict -> dict -> list[2] -> dict -> list[3] -> dict -> list[2] -> dict   (9 levels)
jq -k -d 4                      # every key path, 4 levels deep: company.departments[*].teams[*].name …
jq -R skills                    # every "skills" wherever it is, however deep
jq -R level -P                  # ... with where each one lives: .company.departments[0]…skills[0].level = 5
jq -f '..members[*].name'       # recursive descent, then through the lists
jq -f '.company.departments[*].teams[*].members[*].name'   # or spell every level, wildcards doing the lists
jq -W 'level>=4' -f name        # the skills (any object with level >= 4) that qualify, by name
jq -W '~sre' -P                 # every value containing "sre", with its path
jq -F | grep skills             # flatten, then search
jq -F -t -f .company.departments   # one flat row per department, a column per nested key
jq -d 2                         # the top two levels, deeper ones summarized
```

When a filter does not fit the shape, the error says what is there and what to type:

```
'name' on a list of 2 objects{name,teams} (at .company.departments): 'name' is inside each element; try .company.departments[*].name
no key 'skill' (keys: name, skills) (at .team.members[0]): did you mean 'skills'?
no key 'level' (keys: name, teams) (at .company.departments[0]): 'level' is deeper, first at .company.departments[0].teams[0]…: try -R level to find it at any depth
```

Everything walks the data without recursion, so a document nested thousands of levels deep or with
hundreds of thousands of values is fine (only Python's own JSON parser has a limit, far beyond any
real document, and says so). With a very deep document, print with `-d N`, `-F` or `-R KEY`.

## Output

`jq` looks at what it got first. On screen, a bare `jq` starts with a muted line that says the
shape and the commands that fit it: `# list of 3 objects (keys: name, age) · jq [0] · jq -t ·
jq -i '*' -f name · jq -n`, or `# object with 2 keys: name, items · jq -k · jq -f name`. For deeply
nested data it shows the outline, then a second line with the switches that fit (`jq -F`, `jq -R
KEY`, `jq -k -d 3`). In a pipe there is no such line, only the JSON. A path given on its own
(`jq [3]`, `jq .items[0]`, `jq ..id`) works like `-f`, so a list is indexed the way Python does it.

A string or number is printed as it is, so it pipes cleanly (`jq -f ['name'] | grep -i ada`);
objects and lists are printed as JSON, colored on screen. With `-i '*'`, `[*]` or `..` there is one
result per line. A missing key or index is an error (status 1) that lists the keys there are; under
`-i '*'` an element without the key is skipped, and the status is 1 only if none had it.

## Examples

```
cat users.json | jq                       # pretty-print, after a line saying what it is
cat users.json | jq -s                    # just that: list of 3 objects (keys: name, age)
cat users.json | jq [3]                   # element 3 of a list, like Python's users[3]
cat users.json | jq -k                    # the top-level keys
jq users.json -f ['name']                 # the "name" of an object
jq -r users.json --filter ['name']        # the same, with -r
cat list.json | jq -f [5]['name']         # "name" of the 6th element of a list
cat list.json | jq -i 5 -f ['name']       # the same
cat list.json | jq -i * -f ['name']       # "name" of every element
cat list.json | jq -i * -k                # the keys of every element
cat list.json | jq -n                     # how many elements
cat list.json | jq -w 'age>30' -f name    # names of those over 30
cat list.json | jq -w name~ad -w active=true -t   # matching elements as a table
cat list.json | jq -t                     # the whole list as a table
cat config.json | jq -p | grep -i port    # find where a value lives
cat org.json | jq -R email -P             # every email in a deep document, and where it is
cat org.json | jq -W 'status=error' -c    # every object, at any depth, with status "error"
cat org.json | jq -F | grep -i manager    # flat lines to grep
cat feed.xml | jq -x                      # XML as JSON
cat feed.xml | jq -x -f .rss.channel.item[*].title   # one title per line
curl -s https://x/api.xml | jq -x -k      # the root's keys
fetch https://api.example.com/users | jq -i * -f .address.city | sort | uniq -c
```

The shell would normally expand `*` into file names and `[x]` into a pattern; on a `jq` line it
doesn't, so the examples work as typed.

## See also

`curl URL --jq PATH` (one field of JSON from a URL), `grep`, `sort`.
""")
def jq(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    parser = ArgParser("jq")
    parser.add_argument("words", nargs="*", metavar="FILE|FILTER")
    parser.add_argument("-r", "--read", metavar="FILE")
    parser.add_argument("-s", "--shape", action="store_true")
    parser.add_argument("-x", "--xml", action="store_true")
    parser.add_argument("-f", "--filter", default="")
    parser.add_argument("-i", "--index")
    parser.add_argument("-k", "--keys", action="store_true")
    parser.add_argument("-c", "--compact", action="store_true")
    parser.add_argument("-n", "--count", action="store_true")
    parser.add_argument("-w", "--where", action="append", default=[], metavar="COND")
    parser.add_argument("-W", "--where-any", action="append", default=[], metavar="COND")
    parser.add_argument("-R", "--find", metavar="KEY")
    parser.add_argument("-t", "--table", action="store_true")
    parser.add_argument("-p", "--paths", action="store_true")
    parser.add_argument("-F", "--flatten", action="store_true")
    parser.add_argument("-d", "--depth", type=int, metavar="N")
    parser.add_argument("-P", "--with-paths", action="store_true")
    opts = parser.parse_args(args)
    if opts.depth is not None and opts.depth < 1:
        raise ModuleError("jq: -d / --depth needs a number from 1")
    # The positionals: a FILE, a path (`jq [3]`, `jq .items[0].id`, `jq ..id`, like -f), or both.
    files = [w for w in opts.words if not _looks_like_path(w)]
    paths = [w for w in opts.words if _looks_like_path(w)]
    if len(files) > 1:
        raise ModuleError(f"jq: one FILE at a time ({', '.join(files)})")
    if paths and opts.filter:
        raise ModuleError("jq: give the path once: as -f, or on its own")
    if len(paths) > 1:
        raise ModuleError(f"jq: one path at a time ({' '.join(paths)}): join them, as in {''.join(paths)}")
    if paths:
        opts.filter = paths[0]
    if files and opts.read:
        raise ModuleError("jq: give FILE or -r FILE, not both")
    source = opts.read or (files[0] if files else None)
    data = load(read_file("jq", source) if source else stdin, source or "stdin", xml=opts.xml)

    steps: list[Any] = []
    if opts.index is not None:
        index = opts.index.strip()
        steps.append(WILDCARD if index in ("*", "all") else _bracket(index))
    path = parse_path(opts.filter)
    if opts.find:
        path.append(Recurse(opts.find))
    if opts.where and not steps and isinstance(data, list):
        steps.append(WILDCARD)  # -w on a list looks at each element
    spread = any(s is WILDCARD or isinstance(s, Recurse) for s in steps + path)
    problems: list[str] = []
    pairs = walk([("", data)], steps, problems)
    if opts.where:
        conditions = [parse_where(c) for c in opts.where]
        pairs = [(p, v) for p, v in pairs if all(matches(v, c) for c in conditions)]
        if not pairs:
            return WithStatus("", 1)  # nothing matched, like grep
    if opts.where_any:
        pairs = where_any(pairs, [parse_where(c) for c in opts.where_any])
        spread = True
        if not pairs:
            return WithStatus("", 1)
    pairs = walk(pairs, path, problems)
    results = [v for _, v in pairs]
    if not results and problems:
        raise ModuleError(f"jq: {problems[0]}")
    filtered = bool(steps or path or opts.where or opts.where_any)
    depth = opts.depth

    if opts.shape:
        if not results:
            return WithStatus("", 1)
        if len(results) == 1 or not filtered:
            return "".join(line + "\n" for line in describe(results[0]))
        return "".join(f"{p or '.'}: {describe(v)[0]}\n" for p, v in pairs)
    if opts.paths:
        lines: list[tuple[str, str]] = []
        for p, v in pairs:
            lines += leaf_pairs(v, p if opts.with_paths else "", depth)
        return _lines_out(ctx, lines)
    if opts.flatten:
        if opts.table:
            return _table_out(ctx, *flat_table(results, spread, depth))
        lines = []
        for p, v in pairs:
            lines += flat_items(v, p if (spread or opts.with_paths) else "", depth)
        return _lines_out(ctx, lines)
    if opts.table:
        return _table_out(ctx, *table_rows(results, spread))
    if opts.count:
        if len(results) == 1 and not spread and isinstance(results[0], CONTAINERS):
            return f"{len(results[0])}\n"
        return f"{len(results)}\n"
    if opts.keys:
        if depth is not None:
            outline = [k for p, v in pairs for k in key_paths(v, depth, p if spread else "")]
            if not outline:
                raise ModuleError("jq: no keys to outline here (a scalar, or a list of scalars)")
            return "".join(f"{k}\n" for k in outline)
        if spread:
            rows = [f"[{n}] " + (", ".join(_keys(value)) or f"({_kind(value)})") for n, value in enumerate(results)]
            text = "\n".join(rows) + "\n"
        else:
            keys = _keys(results[0]) if results else []
            if results and not keys and not isinstance(results[0], CONTAINERS):
                raise ModuleError(f"jq: a {_kind(results[0])} has no keys ({_short(results[0])})")
            text = "".join(f"{k}\n" for k in keys)
        return WithStatus(text, 1) if problems and spread and not results else text

    shown = [limit_depth(v, depth) for v in results] if depth else results
    if opts.with_paths:
        return _lines_out(ctx, [(p or ".", _as_text(v, True)) for (p, _), v in zip(pairs, shown, strict=True)])
    plain = "".join(_as_text(v, opts.compact) + "\n" for v in shown)
    if ctx.ui is None or not ctx.interactive:
        return plain
    # On screen: JSON colored by Rich, plain text for pipes and files (Styled). A bare `jq`
    # (no path) says first what the data is and how to dig in; a pipe never sees that line.
    from rich.console import Group

    parts: list[Any] = []
    if not filtered and isinstance(data, CONTAINERS):
        lines_ = describe(data)
        if len(lines_) == 1:
            header = f"# {lines_[0]}" + (f" · {hints(data)}" if hints(data) else "")
            parts.append(Text(header, style="sc.muted"))
        else:
            outline_line = lines_[0] if len(lines_[0]) <= 200 else lines_[0][:199] + "…"
            parts.append(Text(f"# {outline_line}", style="sc.muted"))
            parts.append(Text(f"# {hints(data)}", style="sc.muted"))
    for value in shown:
        if isinstance(value, CONTAINERS) and not opts.compact:
            try:
                parts.append(RichJSON(_dumps(value), indent=2))
            except RecursionError:  # the JSON viewer's own parser: print it plain
                parts.append(Text(_as_text(value, False)))
        else:
            parts.append(Text(_as_text(value, opts.compact)))
    return Styled(Group(*parts), plain)
