"""Tables and lists for module authors: hand over a structure, return what comes back.

    from shellcraft import table, listing

    def run(args, stdin):
        return table([{"host": "web01", "status": "ok"}], title="Hosts")

The result has two faces (context.Styled): the coloured one for the screen, and plain text for a
pipe, a file, `$(...)` and an AI client. The shell picks the face; the module only returns it.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

from rich import box
from rich.cells import cell_len
from rich.console import Group
from rich.errors import StyleSyntaxError
from rich.padding import Padding
from rich.style import Style
from rich.table import Table
from rich.text import Text

from shellcraft.context import Styled
from shellcraft.modkit import ModuleError

GOOD, WARN, BAD, WORST, QUIET = "sc.success", "sc.warning", "sc.danger", "sc.error", "sc.muted"
EMPTY = "-"  # what stands in for None, "" and a key a row does not have

# The automatic colours: a cell that is exactly one of these words (in any case).
WORDS = {
    **dict.fromkeys(("ok", "up", "yes", "true", "pass", "passed", "open"), GOOD),
    **dict.fromkeys(("warn", "warning", "medium", "degraded"), WARN),
    **dict.fromkeys(("fail", "failed", "down", "error", "no", "false", "high"), BAD),
    **dict.fromkeys(("crit", "critical"), WORST),  # bold, so that it stands out from `high`
    **dict.fromkeys(("info", "low"), QUIET),
}
# The colour names an author may use; they follow the theme. Anything else is a Rich style.
COLOURS = {"green": GOOD, "yellow": WARN, "red": BAD, "grey": QUIET, "gray": QUIET, "accent": "sc.accent",
           "plain": ""}
TAG_FIELDS = ("severity", "status", "state", "level", "result", "verdict")
PLAIN_FORMS = ("tsv", "columns", "json")
# The table looks. `boxed` is the one the builtins have (fleet, env, settings).
LOOKS: dict[str, dict[str, Any]] = {
    "boxed": {"border_style": "sc.border"},
    "lines": {"box": box.SIMPLE_HEAD, "border_style": "sc.border", "show_edge": False, "pad_edge": False,
              "title_justify": "left"},
    "bare": {"box": None, "pad_edge": False, "padding": (0, 3, 0, 0), "title_justify": "left"},
}
_MISSING: Any = object()


class Pretty(Styled):
    """What `table` and `listing` return. Two of them, or one and some text, join with `+`."""

    def __add__(self, other: Any) -> Pretty:
        return _join(self, other)

    def __radd__(self, other: Any) -> Pretty:
        return _join(other, self)

    def __str__(self) -> str:
        return self.text


def _join(left: Any, right: Any) -> Pretty:
    """One block under the other, on screen and in the text (a line break is added where the
    first block's text has none)."""
    shown, text = [], ""
    for part in (left, right):
        if isinstance(part, Styled):
            piece = part.text
            if piece:
                shown.append(part.renderable)
        elif isinstance(part, str):
            piece = part
            if piece:
                shown.append(Text(piece.removesuffix("\n")))
        else:
            return NotImplemented
        text += ("\n" if text and piece and not text.endswith("\n") else "") + piece
    return Pretty(Group(*shown), text)


# ── the data ─────────────────────────────────────────────────────────────────

def _load(what: str, data: Any) -> Any:
    """The structure to show: a dict or a list. JSON text is read first."""
    if isinstance(data, (str, bytes)):
        if not data.strip():
            return []
        try:
            data = json.loads(data)
        except ValueError as exc:
            raise ModuleError(f"{what}: the text is not JSON ({exc})") from None
    if isinstance(data, Mapping):
        return {str(k): v for k, v in data.items()}
    if isinstance(data, Iterable) and not isinstance(data, (str, bytes)):
        return [{str(k): v for k, v in i.items()} if isinstance(i, Mapping) else i for i in data]
    raise ModuleError(f"{what}: give it a list, a dict or JSON text, not {type(data).__name__}")


def _show(value: Any) -> str:
    """One value as it is shown: yes / no, `-` for nothing, `a, b, c`, a nested dict as JSON."""
    if value is None or value is _MISSING or (isinstance(value, str) and not value):
        return EMPTY
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, Mapping):
        return json.dumps(value, ensure_ascii=False, default=str)
    if isinstance(value, (set, frozenset)):
        value = sorted(value, key=str)
    if isinstance(value, (list, tuple)):
        return ", ".join(_show(v) for v in value) or EMPTY
    return str(value)


def _flat(text: str) -> str:
    """A value on one line with no tab in it, so that it stays one field of a plain line."""
    return " ".join(text.replace("\t", " ").splitlines())


def _picked(what: str, name: str, chosen: Any) -> tuple[list[str], list[str]] | None:
    """`columns=` / `fields=`: the keys to show, in order, and the name each is shown under."""
    if chosen is None:
        return None
    if isinstance(chosen, Mapping):
        return [str(k) for k in chosen], [str(v) for v in chosen.values()]
    if isinstance(chosen, (list, tuple)):
        return [str(k) for k in chosen], [str(k) for k in chosen]
    raise ModuleError(f"{what}: {name} is a list of names, or a dict to rename them, not {type(chosen).__name__}")


def _keys(items: list[dict[str, Any]]) -> list[str]:
    """Every key seen, in the order met."""
    seen: dict[str, None] = {}
    for item in items:
        for key in item:
            seen.setdefault(key, None)
    return list(seen)


def _lined_up(rows: list[list[str]]) -> str:
    """Rows as columns lined up with spaces."""
    if not rows:
        return ""
    widths = [max(cell_len(row[i]) for row in rows) for i in range(len(rows[0]))]
    return "".join("  ".join(cell + " " * (width - cell_len(cell)) for cell, width in zip(row, widths)).rstrip()
                   + "\n" for row in rows)


def _plain(form: str, rows: list[list[str]], data: Any) -> str:
    """The face for a pipe: `rows` as text, or `data` itself as JSON."""
    if form == "json":
        return json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n"
    rows = [[_flat(cell) for cell in row] for row in rows]
    if form == "columns":
        return _lined_up(rows)
    return "".join("\t".join(row) + "\n" for row in rows)


def _check_plain(what: str, form: Any) -> None:
    if form not in PLAIN_FORMS:
        raise ModuleError(f"{what}: plain is 'tsv', 'columns' or 'json', not {form!r}")


# ── the colours ──────────────────────────────────────────────────────────────

def _style(what: str, name: Any) -> str | Style:
    """A colour an author named: one of ours (it follows the theme) or a Rich style."""
    if isinstance(name, Style):
        return name
    if isinstance(name, str):
        key = name.strip().lower()
        if key in COLOURS:
            return COLOURS[key]
        if key.startswith("sc."):
            return key
        try:
            Style.parse(name)
        except StyleSyntaxError:
            pass
        else:
            return name
    raise ModuleError(f"{what}: {name!r} is not a colour (use green, yellow, red, grey, accent, or a Rich style "
                      "such as 'bold cyan')")


class _Colours:
    """Which style a cell gets: the author's rule for its column first, then the automatic words."""

    def __init__(self, what: str, colors: Any):
        self.what = what
        self.auto = colors is not False
        self.rules: dict[str, Any] = {}
        if colors is None or isinstance(colors, bool):
            return
        if not isinstance(colors, Mapping):
            raise ModuleError(f"{what}: colors is a dict, one entry for a column (or False for no colours), "
                              f"not {type(colors).__name__}")
        for column, rule in colors.items():
            if isinstance(rule, Mapping):  # value -> colour, matched on the value as it is shown
                rule = {_show(k).lower(): _style(what, v) for k, v in rule.items()}
            elif rule is not None and rule is not False and not callable(rule):
                raise ModuleError(f"{what}: colors[{column!r}] is a dict of value and colour, a function, or "
                                  f"False, not {type(rule).__name__}")
            self.rules[str(column)] = rule

    def of(self, column: str, value: Any, text: str) -> str | Style:
        if text == EMPTY:
            return QUIET
        rule = self.rules.get(column, _MISSING)
        if rule is None or rule is False:
            return ""
        key = text.strip().lower()
        if isinstance(rule, dict):
            if key in rule:
                found: str | Style = rule[key]
                return found
        elif callable(rule):
            try:
                name = rule(value)
            except (TypeError, ValueError):  # a value the function was not written for
                name = None
            if name is not None:
                return _style(self.what, name)
        return WORDS.get(key, "") if self.auto else ""


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# ── table ────────────────────────────────────────────────────────────────────

def table(data: Any, columns: Any = None, title: str | None = None, colors: Any = None, style: str = "boxed",
          plain: str = "tsv") -> Pretty:
    """`data` as a table. `data` is a list of dicts (one row each; the columns are every key seen,
    in order), a list of lists with `columns=[...]`, one dict (key and value rows), or JSON text.

    `columns` picks the keys and their order; a dict also renames them (`{"host": "Host"}`).
    `colors` is `{"column": {"value": "green"}}`, or a function of the value for a column, or
    False for no colours. `style` is "boxed", "lines" or "bare". `plain` is what a pipe gets:
    "tsv" (a header line, then tabs between the fields), "columns" or "json".
    """
    what = "table"
    _check_plain(what, plain)
    if style not in LOOKS:
        raise ModuleError(f"{what}: style is 'boxed', 'lines' or 'bare', not {style!r}")
    items = _load(what, data)
    paint = _Colours(what, colors)
    picked = _picked(what, "columns", columns)
    record = isinstance(items, dict)
    out: Any = items  # what plain="json" gives
    rows: list[list[Any]]
    if record:
        keys = heads = ["key", "value"]
        if picked:
            if len(picked[1]) != 2:
                raise ModuleError(f"{what}: for one dict, columns is the two header names")
            heads = picked[1]
        rows = [[key, value] for key, value in items.items()]
    elif items and all(isinstance(i, dict) for i in items):
        keys, heads = picked or (_keys(items), _keys(items))
        rows = [[item.get(key, _MISSING) for key in keys] for item in items]
        if picked:
            out = [{key: item[key] for key in keys if key in item} for item in items]
    elif items and all(isinstance(i, (list, tuple)) for i in items):
        if not picked:
            raise ModuleError(f"{what}: a list of lists needs columns=[...] for the header")
        keys, heads = picked
        for number, item in enumerate(items, 1):
            if len(item) > len(keys):
                raise ModuleError(f"{what}: row {number} has {len(item)} values for {len(keys)} columns")
        rows = [[*item, *[_MISSING] * (len(keys) - len(item))] for item in items]
        out = [dict(zip(keys, item)) for item in items]
    elif items:
        keys, heads = ["value"], picked[1] if picked and len(picked[1]) == 1 else ["value"]
        rows = [[item] for item in items]
    elif picked:
        (keys, heads), rows = picked, []
    else:
        return Pretty(Text(""), "")
    grid = Table(title=title, title_style="sc.prompt", header_style="sc.accent", **LOOKS[style])
    for index, head in enumerate(heads):
        values = [row[index] for row in rows if row[index] is not None and row[index] is not _MISSING]
        numbers = not record and bool(values) and all(_number(v) for v in values)
        grid.add_column(head, overflow="fold", justify="right" if numbers else "left")
    texts = []
    for row in rows:
        cells = [_show(value) for value in row]
        texts.append(cells)
        if record:  # one dict: the value is coloured by the rule for its own key
            grid.add_row(cells[0], Text(cells[1], style=paint.of(cells[0], row[1], cells[1])))
        else:
            grid.add_row(*(Text(cell, style=paint.of(key, value, cell)) for key, value, cell in zip(keys, row, cells)))
    return Pretty(grid, _plain(plain, [list(heads), *texts], out))


# ── listing ──────────────────────────────────────────────────────────────────

def listing(data: Any, tag: Any = None, title: str | None = None, fields: Any = None, colors: Any = None,
            plain: str = "tsv") -> Pretty:
    """`data` as a list, by what it is: one dict gives its fields (`name  value` lines), a list of
    dicts gives one line per item with a coloured tag (`● critical  db01  disk full`), a list of
    plain values gives bullets. JSON text is read first.

    `tag` names the field that is the tag; left out, it is the first of severity, status, state,
    level, result and verdict that the items have (`tag=False`: no tag). `fields` picks the fields
    and their order. `colors` and `plain` are as for `table`; the plain form has no header line.
    """
    what = "listing"
    _check_plain(what, plain)
    items = _load(what, data)
    paint = _Colours(what, colors)
    picked = _picked(what, "fields", fields)
    out: Any = items
    rows: list[list[str]] = []
    grid = Table.grid(padding=(0, 2))
    if isinstance(items, dict):
        keys, names = picked or (list(items), list(items))
        if picked:
            out = {key: items[key] for key in keys if key in items}
        grid.add_column(style="sc.accent", no_wrap=True)
        grid.add_column(overflow="fold")
        for key, name in zip(keys, names):
            value = items.get(key, _MISSING)
            rows.append([name, _show(value)])
            grid.add_row(name, Text(rows[-1][1], style=paint.of(key, value, rows[-1][1])))
    elif items and all(isinstance(i, dict) for i in items):
        keys = _keys(items)
        if tag is None:
            tag = next((key for word in TAG_FIELDS for key in keys if key.lower() == word), None)
        tag = None if tag is None or tag is False else str(tag)
        rest = [key for key in (picked[0] if picked else keys) if key != tag]
        if picked:
            out = [{key: item[key] for key in ([tag] if tag else []) + rest if key in item} for item in items]
        grid.add_column(no_wrap=True)
        for _ in rest:
            grid.add_column(overflow="fold")
        for item in items:
            cells = [_show(item.get(key, _MISSING)) for key in rest]
            # The first field is the item's name: it has the path colour unless a rule colours it.
            shown = [Text(cell, style=paint.of(key, item.get(key, _MISSING), cell) or ("" if i else "sc.path"))
                     for i, (key, cell) in enumerate(zip(rest, cells))]
            if tag:
                word = _show(item.get(tag, _MISSING))
                colour = paint.of(tag, item.get(tag, _MISSING), word) or "sc.accent"
                grid.add_row(Text.assemble(("● ", colour), (word, colour)), *shown)
                rows.append([word, *cells])
            else:
                grid.add_row(Text("•", style="sc.accent"), *shown)
                rows.append(cells)
    elif items:
        grid = Table.grid(padding=(0, 1))
        grid.add_column(no_wrap=True)
        grid.add_column(overflow="fold")
        for item in items:
            rows.append([_show(item)])
            grid.add_row(Text("•", style="sc.accent"), Text(rows[-1][0], style=paint.of("", item, rows[-1][0])))
    else:
        return Pretty(Text(""), "")
    body = Padding(grid, (0, 0, 0, 2), expand=False)
    return Pretty(Group(Text(title, style="sc.prompt"), body) if title else body, _plain(plain, rows, out))
