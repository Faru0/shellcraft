"""jq: look inside JSON — pretty-print it, list its keys, pick values out with a Python-style path.

Not the real jq language: the filter is the subscript you would write in Python after the
loaded value (`['name']`, `[5]['name']`, `['items'][0]['id']`), plus `.name` / `.items[0]`
shortcuts and `[*]` for every element of a list.
"""

from __future__ import annotations

import ast
import json
import re
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

def load(text: str, source: str = "stdin") -> Any:
    """JSON, else JSON Lines (one value per line, as a list), else a Python literal (single quotes,
    True / None), which is what `print(dict)` writes."""
    text = text.strip()
    if not text:
        raise ModuleError(f"jq: {source}: no input (pipe JSON in, or give a FILE)")
    try:
        return json.loads(text)
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


# ── the filter ───────────────────────────────────────────────────────────────

_STEP = re.compile(r"\s*(?:\[(?P<bracket>[^\]]*)\]|\.?(?P<name>[A-Za-z_][A-Za-z0-9_-]*)|\.(?P<num>-?\d+)|(?P<dot>\.))")


def parse_path(text: str) -> list[Any]:
    """`['a'][0]["b c"]`, `[a][0]`, `.a.b[0]`, `a.b`, `[*]`, `[1:3]` → a list of steps: str keys,
    int indexes, slices, or WILDCARD. Quotes are optional inside brackets (the shell removes
    them): a bare word is a key, a number an index."""
    steps: list[Any] = []
    i, text = 0, text.strip()
    if text in ("", "."):
        return steps
    while i < len(text):
        m = _STEP.match(text, i)
        if not m or m.end() == i:
            raise ModuleError(f"jq: bad filter at '{text[i:]}' (examples: ['name'], [0]['name'], .items[0].id)")
        i = m.end()
        if m.group("dot"):
            continue
        if m.group("num") is not None:
            steps.append(int(m.group("num")))
            continue
        if m.group("name") is not None:
            steps.append(m.group("name"))
            continue
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


def apply(values: list[Any], steps: list[Any]) -> tuple[list[Any], list[str]]:
    """Follow the path from each value. Returns (results, problems): a missing key or index under
    a wildcard is skipped and reported, so `-i *` keeps going past an element without it."""
    problems: list[str] = []
    for step in steps:
        out: list[Any] = []
        for value in values:
            out.extend(_step(value, step, problems))
        values = out
    return values, problems


def _step(value: Any, step: Any, problems: list[str]) -> list[Any]:
    if step is WILDCARD:
        if isinstance(value, list):
            return list(value)
        if isinstance(value, dict):
            return list(value.values())
        problems.append(f"[*] on a {_kind(value)}")
        return []
    if isinstance(step, slice):
        if isinstance(value, list):
            return [value[step]]
        problems.append(f"a slice on a {_kind(value)}")
        return []
    if isinstance(value, dict):
        key = step if isinstance(step, str) else str(step)
        if key in value:
            return [value[key]]
        if isinstance(step, int) and step in value:
            return [value[step]]
        keys = ", ".join(map(str, list(value)[:12])) + (" …" if len(value) > 12 else "")
        problems.append(f"no key '{key}' (keys: {keys or 'none'})")
        return []
    if isinstance(value, list):
        index = step if isinstance(step, int) else int(step) if str(step).lstrip("-").isdigit() else None
        if index is None:
            problems.append(f"'{step}' on a list of {len(value)} (use a number, or -i '*' to look inside each)")
            return []
        if -len(value) <= index < len(value):
            return [value[index]]
        problems.append(f"index {index} out of range (a list of {len(value)})")
        return []
    problems.append(f"'{step}' on a {_kind(value)} ({_short(value)})")
    return []


def _kind(value: Any) -> str:
    return {dict: "object", list: "list", str: "string", bool: "boolean", int: "number", float: "number"}.get(
        type(value), "null" if value is None else type(value).__name__)


def _short(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= 40 else text[:39] + "…"


# ── output ───────────────────────────────────────────────────────────────────

def _as_text(value: Any, compact: bool) -> str:
    """A string or number as itself (handy in pipes), objects and lists as JSON."""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=None if compact else 2,
                          separators=(",", ":") if compact else None)
    return json.dumps(value)


def _keys(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(k) for k in value]
    if isinstance(value, list):
        return [str(i) for i in range(len(value))]
    return []


# ── -w / --where ─────────────────────────────────────────────────────────────

_WHERE = re.compile(r"^\s*(?P<path>.+?)\s*(?P<op>==|!=|>=|<=|=|>|<|~)\s*(?P<value>.*?)\s*$", re.S)


def parse_where(text: str) -> tuple[list[Any], str | None, Any]:
    """`age>30`, `name=ada`, `name~AD` (contains, any case), `tags` (the key is there) →
    (path, operator, value). The value is JSON when it parses (30, true, null), else text."""
    m = _WHERE.match(text)
    if not m:
        return parse_path(text), None, None
    raw = m.group("value")
    try:
        value = json.loads(raw)
    except ValueError:
        value = raw[1:-1] if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"" else raw
    return parse_path(m.group("path")), m.group("op"), value


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
    numbers = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (have, want))
    a, b = (have, want) if numbers else (_as_text(have, True), _as_text(want, True))
    return {">": a > b, "<": a < b, ">=": a >= b, "<=": a <= b}[op]


# ── -p / --paths ─────────────────────────────────────────────────────────────

def leaf_paths(value: Any, prefix: str = "") -> list[str]:
    """Every value with the path that reaches it, written so it can go back into -f."""
    if isinstance(value, dict) and value:
        out = []
        for key, sub in value.items():
            key = str(key)
            step = f".{key}" if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", key) else f"[{json.dumps(key)}]"
            out += leaf_paths(sub, prefix + step)
        return out
    if isinstance(value, list) and value:
        out = []
        for n, sub in enumerate(value):
            out += leaf_paths(sub, f"{prefix}[{n}]")
        return out
    return [f"{prefix or '.'} = {json.dumps(value, ensure_ascii=False)}"]


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


def _cell(value: Any) -> str:
    text = _as_text(value, compact=True)
    return text if len(text) <= 60 else text[:59] + "…"


@builtin("jq", "Pretty-print JSON, list its keys, or pick values out of it",
         "jq [FILE] [-r FILE] [-i N|*] [-w COND]... [-f FILTER] [-k|-p|-t|-n] [-c]", category="text", doc="""\
# jq

Look inside JSON: pretty-print it, list its keys, or pick values out with a path written the way
you would in Python after `json.loads`. It reads a FILE, or what is piped in. JSON Lines (one
object per line) and Python-style dictionaries (single quotes, `True`, `None`) work too.

This is ShellCraft's own jq, not the jq language: the filter is a Python subscript.

## Synopsis

```
jq [FILE]                       pretty-print
jq [FILE] -f FILTER             pick a value: -f ['name'], -f [5]['name'], -f .items[0].id
jq [FILE] -i N -f FILTER        the same, inside element N of a list
jq [FILE] -i '*' -f FILTER      the same, for every element of a list
jq [FILE] -k                    the keys (with -i: of each element)
jq [FILE] -w 'age>30' -t        the elements of a list that match, as a table
jq [FILE] -p                    every value with its path
```

## Options

| Option | Meaning |
| --- | --- |
| `-f FILTER`, `--filter FILTER` | The path to pick: `['name']`, `[0]['name']`, `['items'][2]['id']`, `.items[2].id`, `[*]['name']` (every element), `[1:3]` (a slice). Quotes inside the brackets are optional. |
| `-i N`, `--index N` | Start inside element N of a list (`-i 0`, `-i -1` for the last). `-i '*'` (or `-i all`) means every element, and `-i 2:5` a range. |
| `-k`, `--keys` | The keys of the result, one per line (the indexes of a list). With `-i '*'`: each element's keys, under its index. |
| `-r FILE`, `--read FILE` | Read FILE (the same as giving FILE on its own). |
| `-c`, `--compact` | Objects and lists on one line each, without indentation. |
| `-n`, `--count` | How many results the filter gives (the length of a single list or object). |
| `-w COND`, `--where COND` | Keep only the list elements that match COND (give it more than once: all must match). COND is `PATH OP VALUE` with OP one of `=` `!=` `>` `<` `>=` `<=` `~` (contains, any case), or just `PATH` (the key is there). Quote it when it has `>` or `<`: `-w 'age>30'`. On a list it implies `-i '*'`. |
| `-t`, `--table` | A list of objects as a table, one row each (a column for every key); a single object as key/value rows. Tab-separated with a header line in a pipe. |
| `-p`, `--paths` | Every value with the path that reaches it, one per line (`.items[0].id = 7`): a map of the document, and each path works as a `-f` filter. |

## Output

A string or number is printed as it is, so it pipes cleanly (`jq -f ['name'] | grep -i ada`);
objects and lists are printed as JSON, colored on screen. With `-i '*'` or `[*]` there is one result
per line. A missing key or index is an error (status 1) that lists the keys there are; under
`-i '*'` an element without the key is skipped, and the status is 1 only if none had it.

## Examples

```
cat users.json | jq                       # pretty-print
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
fetch https://api.example.com/users | jq -i * -f .address.city | sort | uniq -c
```

The shell would normally expand `*` into file names and `[x]` into a pattern; on a `jq` line it
doesn't, so the examples work as typed.

## See also

`fetch --json PATH` (JSON from a URL), `grep`, `sort`.
""")
def jq(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    parser = ArgParser("jq")
    parser.add_argument("file", nargs="?")
    parser.add_argument("-r", "--read", metavar="FILE")
    parser.add_argument("-f", "--filter", default="")
    parser.add_argument("-i", "--index")
    parser.add_argument("-k", "--keys", action="store_true")
    parser.add_argument("-c", "--compact", action="store_true")
    parser.add_argument("-n", "--count", action="store_true")
    parser.add_argument("-w", "--where", action="append", default=[], metavar="COND")
    parser.add_argument("-t", "--table", action="store_true")
    parser.add_argument("-p", "--paths", action="store_true")
    opts = parser.parse_args(args)
    if opts.file and opts.read:
        raise ModuleError("jq: give FILE or -r FILE, not both")
    source = opts.read or opts.file
    data = load(read_file("jq", source) if source else stdin, source or "stdin")

    steps: list[Any] = []
    if opts.index is not None:
        index = opts.index.strip()
        steps.append(WILDCARD if index in ("*", "all") else _bracket(index))
    path = parse_path(opts.filter)
    if opts.where and not steps and isinstance(data, list):
        steps.append(WILDCARD)  # -w on a list looks at each element
    spread = any(s is WILDCARD for s in steps + path)
    results, problems = apply([data], steps)
    if opts.where:
        conditions = [parse_where(c) for c in opts.where]
        results = [r for r in results if all(matches(r, c) for c in conditions)]
        if not results:
            return WithStatus("", 1)  # nothing matched, like grep
    results, more = apply(results, path)
    problems += more
    if not results and problems:
        raise ModuleError(f"jq: {problems[0]}")
    if opts.paths:
        return "".join(line + "\n" for value in results for line in leaf_paths(value))
    if opts.table:
        columns, rows = table_rows(results, spread)
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

    if opts.count:
        if len(results) == 1 and not spread and isinstance(results[0], (list, dict)):
            return f"{len(results[0])}\n"
        return f"{len(results)}\n"
    if opts.keys:
        if spread:
            lines = []
            for n, value in enumerate(results):
                lines.append(f"[{n}] " + (", ".join(_keys(value)) or f"({_kind(value)})"))
            text = "\n".join(lines) + "\n"
        else:
            keys = _keys(results[0]) if results else []
            if results and not keys and not isinstance(results[0], (dict, list)):
                raise ModuleError(f"jq: a {_kind(results[0])} has no keys ({_short(results[0])})")
            text = "".join(f"{k}\n" for k in keys)
        return WithStatus(text, 1) if problems and spread and not results else text

    plain = "".join(_as_text(v, opts.compact) + "\n" for v in results)
    if ctx.ui is None or not ctx.interactive:
        return plain
    # On screen: JSON colored by Rich, plain text for pipes and files (Styled).
    from rich.console import Group

    parts: list[Any] = []
    for value in results:
        if isinstance(value, (dict, list)) and not opts.compact:
            parts.append(RichJSON(json.dumps(value, ensure_ascii=False), indent=2))
        else:
            parts.append(Text(_as_text(value, opts.compact)))
    return Styled(Group(*parts), plain)
