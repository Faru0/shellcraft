"""Bash brace expansion of one word: `a{b,c}d` is `abd acd`, `{1..3}` is `1 2 3`, `{01..03}`,
`{a..e}`, `{0..10..5}`. Used for `for` lists and `case` words (shellcraft.script) and for the words
of every plain command line (shellcraft.parser.tokenize).

Quoted text, `${…}`, `$( … )`, a backslashed character and a lone `{}` (find's `-exec … {}`) are
never expanded; nesting works. A group with no comma and no range (`{a}`) is left as typed."""

from __future__ import annotations

import re

MAX_RANGE = 100_000  # items one range / brace expansion may produce
_BRACE_SEQ = re.compile(r"^(-?\d+)\.\.(-?\d+)(?:\.\.(-?\d+))?$|^([A-Za-z])\.\.([A-Za-z])(?:\.\.(-?\d+))?$")


def skip_dollar(word: str, i: int) -> int | None:
    """At a `$` that opens `$( … )`, `$(( … ))`, `${ … }` or `$'…'`: the index after it, else None."""
    from shellcraft.parser import _closing_brace, find_closing_paren

    if word.startswith("$(", i):
        close = find_closing_paren(word, i + 2)
        return len(word) if close is None else close + 1
    if word.startswith("${", i):
        close = _closing_brace(word, i + 2)
        return len(word) if close is None else close + 1
    if word.startswith("$'", i):
        j = i + 2
        while j < len(word) and word[j] != "'":
            j += 2 if word[j] == "\\" else 1
        return min(j + 1, len(word))
    return None


def brace_expand(word: str) -> list[str]:
    """Bash brace expansion of one word (see the module doc)."""
    i, quote, n = 0, None, len(word)
    while i < n:
        ch = word[i]
        if quote:
            if ch == "\\" and quote == '"':
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch == "\\":
            i += 2
            continue
        elif ch == "$" and (after := skip_dollar(word, i)) is not None:
            i = after
            continue
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "`":
            end = word.find("`", i + 1)
            i = n if end == -1 else end + 1
            continue
        elif ch == "{":
            close, parts = _brace_group(word, i)
            if close is not None:
                prefix, suffix = word[:i], word[close + 1:]
                inner = word[i + 1:close]
                if parts is None:
                    alternatives = _brace_sequence(inner)
                    if alternatives is None:
                        rest = brace_expand(word[i + 1:])
                        return [word[:i + 1] + r for r in rest]
                else:
                    alternatives = [a for p in parts for a in brace_expand(p)]
                out: list[str] = []
                for alt in alternatives:
                    out += brace_expand(prefix + alt + suffix)
                    if len(out) > MAX_RANGE:
                        raise ValueError(f"brace expansion makes more than {MAX_RANGE} words")
                return out
        i += 1
    return [word]


def _brace_group(word: str, i: int) -> tuple[int | None, list[str] | None]:
    """At `{`: (index of the matching `}`, the comma-separated parts or None when there's no comma)."""
    level, quote, parts, last = 0, None, [], i + 1
    j = i
    while j < len(word):
        ch = word[j]
        if quote:
            if ch == "\\" and quote == '"':
                j += 2
                continue
            if ch == quote:
                quote = None
        elif ch == "\\":
            j += 2
            continue
        elif ch == "$" and (after := skip_dollar(word, j)) is not None:
            j = after
            continue
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "{":
            level += 1
        elif ch == "}":
            level -= 1
            if level == 0:
                if not parts:
                    return j, None
                parts.append(word[last:j])
                return j, parts
        elif ch == "," and level == 1:
            parts.append(word[last:j])
            last = j + 1
        j += 1
    return None, None


def _brace_sequence(inner: str) -> list[str] | None:
    match = _BRACE_SEQ.match(inner)
    if not match:
        return None
    if match[1] is not None:
        a, b, step = int(match[1]), int(match[2]), abs(int(match[3] or 1)) or 1
        if abs(b - a) // step + 1 > MAX_RANGE:
            raise ValueError(f"{{{inner}}} makes more than {MAX_RANGE} words")
        width = max(len(match[1].lstrip("-")), len(match[2].lstrip("-")))
        padded = (match[1].lstrip("-").startswith("0") and len(match[1].lstrip("-")) > 1) or \
                 (match[2].lstrip("-").startswith("0") and len(match[2].lstrip("-")) > 1)
        nums = range(a, b + 1, step) if b >= a else range(a, b - 1, -step)
        return [f"{x:0{width + (1 if x < 0 else 0)}d}" if padded else str(x) for x in nums]
    a, b, step = ord(match[4]), ord(match[5]), abs(int(match[6] or 1)) or 1
    chars = range(a, b + 1, step) if b >= a else range(a, b - 1, -step)
    return [chr(c) for c in chars]
