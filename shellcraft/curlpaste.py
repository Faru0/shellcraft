"""Pasted curl commands from Windows: Chrome's / Edge's "Copy as cURL (cmd)".

That form wraps every argument in `^"…^"`, escapes cmd's special characters with `^` (`^&`, `^%`,
`^^`…), writes a quote inside an argument as `\\^"`, ends continued lines with `^`, and a newline
inside an argument as `^` + two newlines. `from_cmd()` turns such a `curl` (or `fetch`) line into
the same command in ShellCraft's own quoting (`$'…'` strings), so it runs unchanged.

The bash form ("Copy as cURL (bash)") needs no conversion: the parser understands its `$'…'`
strings and `\\` line continuations itself.
"""

from __future__ import annotations

import re

_LEADING = re.compile(r"^\s*(curl|fetch)(\.exe)?\s", re.I)


def looks_like_cmd(line: str) -> bool:
    """A curl / fetch line in cmd's quoting (Chrome's "Copy as cURL (cmd)")."""
    return bool(_LEADING.match(line)) and '^"' in line


def from_cmd(line: str) -> str:
    """The same command in ShellCraft quoting; any other line comes back unchanged."""
    if not looks_like_cmd(line):
        return line
    argv = _windows_argv(_uncaret(line))
    if not argv:
        return line
    name = argv[0].lower().removesuffix(".exe")
    return " ".join([name, *(_quote(arg) for arg in argv[1:])])


def _uncaret(text: str) -> str:
    """cmd's first pass: `^X` is X, `^` + newline continues the line (inside a quoted argument,
    `^` + newline + newline is one real newline, as Chrome writes it)."""
    out: list[str] = []
    i, n = 0, len(text)
    in_quotes = False
    while i < n:
        ch = text[i]
        if ch == "^" and i + 1 < n:
            nxt = text[i + 1]
            if nxt in "\r\n":
                j = i + 1
                if text.startswith("\r\n", j):
                    j += 2
                else:
                    j += 1
                if in_quotes and j < n and text[j] in "\r\n":  # ^ + 2 newlines: a newline in the value
                    j += 2 if text.startswith("\r\n", j) else 1
                    out.append("\n")
                i = j
                continue
            if nxt == '"':
                in_quotes = not in_quotes if (not out or out[-1] != "\\") else in_quotes
            out.append(nxt)
            i += 2
            continue
        if ch == '"':
            in_quotes = not in_quotes
        out.append(ch)
        i += 1
    return "".join(out)


def _windows_argv(text: str) -> list[str]:
    """Split a command line the way Windows programs do (CommandLineToArgvW): whitespace separates,
    `"` groups, `\\"` is a quote, 2n backslashes before a quote are n backslashes."""
    args: list[str] = []
    current: list[str] = []
    have = in_quotes = False
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":
            j = i
            while j < n and text[j] == "\\":
                j += 1
            count = j - i
            if j < n and text[j] == '"':
                current.append("\\" * (count // 2))
                if count % 2:
                    current.append('"')
                    i = j + 1
                else:
                    in_quotes = not in_quotes
                    i = j + 1
                have = True
                continue
            current.append("\\" * count)
            have = True
            i = j
            continue
        if ch == '"':
            in_quotes = not in_quotes
            have = True
            i += 1
            continue
        if ch.isspace() and not in_quotes:
            if have:
                args.append("".join(current))
                current, have = [], False
            i += 1
            continue
        current.append(ch)
        have = True
        i += 1
    if have:
        args.append("".join(current))
    return args


def _quote(arg: str) -> str:
    """One argument for ShellCraft's parser: plain when it is safe, else a `$'…'` string."""
    if arg and re.fullmatch(r"[A-Za-z0-9_@%+=:,./-]+", arg):
        return arg
    out = []
    for ch in arg:
        if ch == "\\":
            out.append("\\\\")
        elif ch == "'":
            out.append("\\'")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 32 or ord(ch) == 127:
            out.append(f"\\x{ord(ch):02x}")
        else:
            out.append(ch)
    return "$'" + "".join(out) + "'"
