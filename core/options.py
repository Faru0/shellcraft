"""Option (switch) metadata for Tab completion, read from a command's .skill and .md files.

No per-module completer code is needed: a module's `[[args]]` table (.skill) and its
`## Options` table (.md) already describe every switch. Builtins get the same treatment
from the Markdown in their `doc`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from core.loader import SkillInfo

_CELL_SPLIT = re.compile(r"(?<!\\)\|")
_CODE_SPAN = re.compile(r"`([^`]+)`")
_NAME_SPLIT = re.compile(r"[\s/,]+")
_PATH_WORDS = ("FILE", "PATH", "DIR")


@dataclass(frozen=True)
class OptionSpec:
    flags: tuple[str, ...]  # e.g. ("-f", "--field"); short forms first
    metavar: str | None = None  # e.g. "FIELD": the option takes a value
    help: str = ""  # one line for the completion menu
    values: tuple[str, ...] = ()  # allowed values, completed after the flag

    @property
    def short(self) -> str | None:
        return next((f for f in self.flags if not f.startswith("--")), None)

    @property
    def long(self) -> str | None:
        return next((f for f in self.flags if f.startswith("--")), None)

    @property
    def takes_path(self) -> bool:
        return bool(self.metavar) and any(w in self.metavar.upper() for w in _PATH_WORDS)

    @property
    def label(self) -> str:
        return ", ".join(self.flags) + (f" {self.metavar}" if self.metavar else "")


def _is_flag(token: str) -> bool:
    return len(token) > 1 and token.startswith("-") and not token[1].isspace()


def _ordered(flags: Iterable[str]) -> tuple[str, ...]:
    unique = list(dict.fromkeys(flags))
    return tuple(sorted(unique, key=lambda f: f.startswith("--")))


def _plain(text: str) -> str:
    text = text.replace("\\|", "|")
    text = re.sub(r"[`*]", "", text)
    return " ".join(text.split())


def _parse_spec(tokens: list[str]) -> tuple[tuple[str, ...], str | None]:
    flags = [t for t in tokens if _is_flag(t)]
    rest = [t for t in tokens if not _is_flag(t)]
    return _ordered(flags), (rest[0] if rest else None)


def _values_from_metavar(metavar: str | None) -> tuple[str, ...]:
    """`f|d|l` style metavars list their allowed values."""
    if metavar and "|" in metavar:
        return tuple(v for v in metavar.replace("\\|", "|").split("|") if v)
    return ()


def from_skill(skill: SkillInfo | None) -> list[OptionSpec]:
    """Options from `[[args]]` entries such as name = "-f / --field FIELD"."""
    if skill is None:
        return []
    options = []
    for arg in skill.args:
        flags, metavar = _parse_spec([t for t in _NAME_SPLIT.split(arg.get("name", "")) if t])
        if not flags:
            continue  # a positional like "FILE..." or "IP..."
        values = tuple(str(v) for v in arg.get("values") or ()) or _values_from_metavar(metavar)
        options.append(OptionSpec(flags, metavar, _plain(arg.get("description", "")), values))
    return options


def from_markdown(md: str | None) -> list[OptionSpec]:
    """Options from Markdown table rows whose first cell holds flags: | `-f`, `--field F` | meaning |.

    Every table in the page is scanned (not just an `## Options` section), because man pages
    also document switches under other headings.
    """
    if not md:
        return []
    options = []
    for line in md.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in _CELL_SPLIT.split(line.strip("|"))]
        if len(cells) < 2 or set(cells[0]) <= set("-: "):
            continue  # separator row
        tokens: list[str] = []
        for span in _CODE_SPAN.findall(cells[0]):
            tokens += span.replace("\\|", "|").split()
        flags, metavar = _parse_spec(tokens)
        if flags:
            options.append(OptionSpec(flags, metavar, _plain(cells[1]), _values_from_metavar(metavar)))
    return options


def merge(primary: list[OptionSpec], secondary: list[OptionSpec]) -> list[OptionSpec]:
    """Combine two sources: flags/metavar/values from `primary`, the shorter help from `secondary`."""
    merged: list[OptionSpec] = []
    used: set[int] = set()
    for p in primary:
        match = next((i for i, s in enumerate(secondary) if i not in used and set(p.flags) & set(s.flags)), None)
        if match is None:
            merged.append(p)
            continue
        used.add(match)
        s = secondary[match]
        merged.append(OptionSpec(_ordered(p.flags + s.flags), p.metavar or s.metavar,
                                 s.help or p.help, p.values or s.values))
    merged += [s for i, s in enumerate(secondary) if i not in used]
    return merged


@lru_cache(maxsize=None)
def builtin_options(name: str) -> tuple[OptionSpec, ...]:
    from core.builtins import BUILTINS

    builtin = BUILTINS.get(name)
    return tuple(from_markdown(builtin.doc)) if builtin else ()
