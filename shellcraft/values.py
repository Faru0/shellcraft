"""values — what a variable can hold besides plain text: a typed number, an array, a dict.

Every value is a `str`, so the many places that only know text keep working:

- `PyValue`: a number or bool set from Python. Its text is `5` or `True`, `.value` the number.
- `ShArray`: a bash array and a Python list, one and the same. Its text is its first element,
  which is what bash gives for `$a`. `.items` holds the elements.
- `ShMap`: a `declare -A` array and a Python dict. Its text is the value at key `0`. `.items`
  holds the `(key, value)` pairs in the order they were added.

An array or dict is never changed in place: every change builds a new value and stores it, so the
copies made for `$( … )`, `( … )`, `local` and a function call stay apart without more work.

This module imports nothing from shellcraft, so the parser can use it.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

MAX_SEQUENCE = 100_000  # longest string / list a condition may build with `*`; most elements an array holds
MAX_TEXT = 10_000_000  # longest text or list a Python expression may build with `+` (`s += s` in a loop)

_PLAIN_WORD = re.compile(r"[A-Za-z0-9_./:=@+,%-]+")


class ValueProblem(Exception):
    """An array or dict that can't be built: too large, nested, a key that is not text."""


class PyValue(str):
    """A number or bool set from Python (`n = 5`, `n = int(input())`): bash reads it as text
    (`$n` is `5`, `True`), Python code reads `.value`, the number itself (python_names)."""

    value: Any

    def __new__(cls, value: Any) -> PyValue:
        self = super().__new__(cls, str(value))
        self.value = value
        return self


def _element(value: Any, kind: str) -> str:
    """One element as it is stored: text, or a PyValue for a number."""
    if isinstance(value, (ShArray, ShMap, list, tuple, dict, set, frozenset)):
        raise ValueProblem(f"a {kind} can hold only text and numbers, not another list or dict")
    if isinstance(value, str):
        return value
    if isinstance(value, (bool, int, float)):
        return PyValue(value)
    raise ValueProblem(f"a {kind} can hold only text and numbers, not {value!r}")


def _check_size(count: int, size: int, kind: str) -> None:
    if count > MAX_SEQUENCE:
        raise ValueProblem(f"a {kind} can hold {MAX_SEQUENCE} elements at most")
    if size > MAX_TEXT:
        raise ValueProblem(f"the {kind} would be too large ({MAX_TEXT} characters at most)")


class ShArray(str):
    """An indexed array (`a=(x y)`, `hosts = ["a", "b"]`). Elements are numbered from 0 with no gaps."""

    items: tuple[str, ...]

    def __new__(cls, items: Iterable[Any] = ()) -> ShArray:
        kept = tuple(_element(item, "list") for item in items)
        _check_size(len(kept), sum(len(item) for item in kept), "list")
        self = super().__new__(cls, kept[0] if kept else "")
        self.items = kept
        return self

    def to_python(self) -> list[Any]:
        """A fresh Python list: numbers as numbers."""
        return [item.value if isinstance(item, PyValue) else item for item in self.items]

    def position(self, index: int) -> int | None:
        """Where element `index` is (a negative index counts from the end); None when there is none."""
        if index < 0:
            index += len(self.items)
        return index if 0 <= index < len(self.items) else None

    def get(self, index: int) -> str | None:
        at = self.position(index)
        return None if at is None else self.items[at]

    def with_item(self, index: int, value: Any, name: str = "array") -> ShArray:
        """This array with element `index` set; `index` may be one past the end (it appends)."""
        count = len(self.items)
        at = index + count if index < 0 else index
        if at < 0:
            raise ValueProblem(f"{name}[{index}]: there is no such element")
        if at > count:
            raise ValueProblem(f"{name}[{index}]: arrays have no gaps (next index is {count})")
        return ShArray((*self.items[:at], value, *self.items[at + 1:]))

    def without(self, index: int, name: str = "array") -> ShArray:
        """This array without its last element (`unset 'a[-1]'`); any other index would leave a gap."""
        at = self.position(index)
        if at is None:
            return self
        if at != len(self.items) - 1:
            raise ValueProblem(f"{name}[{index}]: would leave a gap; use del {name}[{index}]")
        return ShArray(self.items[:-1])


class ShMap(str):
    """A dict (`declare -A m; m[web]=80`, `d = {"web": 80}`): text keys, in the order they were added."""

    items: tuple[tuple[str, str], ...]

    def __new__(cls, pairs: Mapping[Any, Any] | Iterable[tuple[Any, Any]] = ()) -> ShMap:
        merged: dict[str, str] = {}
        for key, value in (pairs.items() if isinstance(pairs, Mapping) else pairs):
            if isinstance(key, PyValue) or not isinstance(key, str):
                shown = key.value if isinstance(key, PyValue) else key
                raise ValueProblem(f'dict keys must be text ("{shown}", not {shown!r})')
            merged[str(key)] = _element(value, "dict")
        _check_size(len(merged), sum(len(k) + len(v) for k, v in merged.items()), "dict")
        self = super().__new__(cls, merged.get("0", ""))
        self.items = tuple(merged.items())
        return self

    def to_python(self) -> dict[str, Any]:
        """A fresh Python dict: numbers as numbers."""
        return {key: value.value if isinstance(value, PyValue) else value for key, value in self.items}

    def get(self, key: str) -> str | None:
        for name, value in self.items:
            if name == key:
                return value
        return None

    def with_item(self, key: str, value: Any) -> ShMap:
        return ShMap([*self.items, (key, value)])

    def without(self, key: str) -> ShMap:
        return ShMap([pair for pair in self.items if pair[0] != key])


Container = (ShArray, ShMap)


def is_container(value: Any) -> bool:
    return isinstance(value, Container)


def elements(value: str) -> list[str]:
    """What `${name[@]}` gives: the elements of an array, the values of a dict, and for plain
    text that one value (bash reads a plain variable as an array of one)."""
    if isinstance(value, ShArray):
        return list(value.items)
    if isinstance(value, ShMap):
        return [item for _, item in value.items]
    return [value]


def keys(value: str) -> list[str]:
    """What `${!name[@]}` gives: the indexes of an array, the keys of a dict."""
    if isinstance(value, ShArray):
        return [str(n) for n in range(len(value.items))]
    if isinstance(value, ShMap):
        return [key for key, _ in value.items]
    return ["0"]


def to_python(value: Any) -> Any:
    """A stored value as Python code sees it: a list, a dict, a number, or the text."""
    if isinstance(value, Container):
        return value.to_python()
    return value.value if isinstance(value, PyValue) else value


def quote_word(text: str) -> str:
    """One element as you would type it in `a=( … )`."""
    if _PLAIN_WORD.fullmatch(text):
        return text
    return "'" + text.replace("'", "'\\''") + "'"


def literal(value: str) -> str:
    """An array or dict as the right side of an assignment: `(x 'y z')`, `([web]=80 [db]=5432)`."""
    if isinstance(value, ShArray):
        return "(" + " ".join(quote_word(item) for item in value.items) + ")"
    if isinstance(value, ShMap):
        return "(" + " ".join(f"[{quote_word(key)}]={quote_word(item)}" for key, item in value.items) + ")"
    return quote_word(value)


def declared(value: str) -> str:
    """The same for `declare -p`, in bash's own form: `([0]="x" [1]="y z")`."""
    def quoted(text: str) -> str:
        return '"' + re.sub(r'(["\\$`])', r"\\\1", text) + '"'

    if isinstance(value, ShArray):
        return "(" + " ".join(f"[{n}]={quoted(item)}" for n, item in enumerate(value.items)) + ")"
    if isinstance(value, ShMap):
        return "(" + " ".join(f"[{quote_word(key)}]={quoted(item)}" for key, item in value.items) + ")"
    return quoted(value)
