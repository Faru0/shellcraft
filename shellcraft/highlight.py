"""Syntax highlighting of the command line as it is typed (a prompt_toolkit Lexer).

The first word of every segment is colored by whether it is a known command (a builtin, a
module, an alias or a shell keyword); operators, quoted strings, switches and `$variables`
get their own styles. The styles are named `cmd.known`, `cmd.unknown`, `operator`, `string`,
`switch`, `variable` and `comment`, and every theme defines them (shellcraft/themes.py).
"""

from __future__ import annotations

import re
from typing import Callable

from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.lexers import Lexer

KEYWORDS = frozenset({"for", "if", "elif", "else", "then", "do", "done", "fi", "while", "until", "case",
                      "esac", "in", "break", "continue", "!", "-py", "{", "}", "export", "unset",
                      "function", "return", "select"})
_OPERATORS = ("&&", "||", "2>&1", "1>&2", ">&2", "2>>", "2>", "&>", ">>", ">", "|", ";", "<", "&")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_VARIABLE = re.compile(r"\$(\?|\{[^}]*\}|[A-Za-z_][A-Za-z0-9_]*|\(\(|\()")


class ShellLexer(Lexer):
    """`known(name)` says whether a command name exists; it is asked once per word."""

    def __init__(self, known: Callable[[str], bool]):
        self.known = known

    def lex_document(self, document: Document) -> Callable[[int], StyleAndTextTuples]:
        lines = document.lines
        cache: dict[int, StyleAndTextTuples] = {}

        def get_line(lineno: int) -> StyleAndTextTuples:
            if lineno not in cache:
                cache[lineno] = highlight(lines[lineno], self.known) if lineno < len(lines) else []
            return cache[lineno]

        return get_line


def highlight(line: str, known: Callable[[str], bool]) -> StyleAndTextTuples:
    """Style fragments for one line."""
    out: StyleAndTextTuples = []
    i, n = 0, len(line)
    expect_command = True  # the next word names a command
    while i < n:
        ch = line[i]
        if ch.isspace():
            out.append(("", ch))
            i += 1
            continue
        if ch == "#" and (i == 0 or line[i - 1].isspace()):
            out.append(("class:comment", line[i:]))
            break
        op = next((o for o in _OPERATORS if line.startswith(o, i)), None)
        if op and (op not in ("&", "2>", "2>>", "2>&1") or i == 0 or line[i - 1].isspace()):
            out.append(("class:operator", op))
            i += len(op)
            expect_command = True
            continue
        start = i
        quoted = False
        while i < n and not line[i].isspace() and not _at_operator(line, i):
            if line[i] in ("'", '"'):
                quote, j = line[i], i + 1
                while j < n and line[j] != quote:
                    j += 2 if quote == '"' and line[j] == "\\" and j + 1 < n else 1
                out.append(("class:string", line[i:min(j + 1, n)]))
                i = j + 1
                quoted = True
                continue
            j = i
            while j < n and not line[j].isspace() and line[j] not in ("'", '"') and not _at_operator(line, j):
                j += 1
            out.extend(_plain_word(line[i:j], expect_command and not quoted and i == start, known))
            i = j
        word = line[start:i]
        if expect_command and not quoted and word and _ASSIGNMENT.match(word):
            expect_command = True  # `x=1 cmd`: the command still comes
        elif expect_command and word not in KEYWORDS and word:
            expect_command = False
    return out


def _at_operator(line: str, i: int) -> bool:
    op = next((o for o in _OPERATORS if line.startswith(o, i)), None)
    if op is None:
        return False
    if op in ("&", "2>", "2>>", "2>&1"):
        return i == 0 or line[i - 1].isspace()
    return True


def _plain_word(word: str, is_command: bool, known: Callable[[str], bool]) -> StyleAndTextTuples:
    if not word:
        return []
    if is_command:
        if word in KEYWORDS:
            return [("class:keyword", word)]
        if _ASSIGNMENT.match(word):
            return _with_variables(word, "class:variable")
        name = word[1:] if word.startswith("\\") else word
        return [("class:cmd.known" if known(name) else "class:cmd.unknown", word)]
    if word in KEYWORDS:
        return [("class:keyword", word)]
    if word.startswith("-") and len(word) > 1:
        return [("class:switch", word)]
    return _with_variables(word, "")


def _with_variables(word: str, style: str) -> StyleAndTextTuples:
    out: StyleAndTextTuples = []
    last = 0
    for match in _VARIABLE.finditer(word):
        if match.start() > last:
            out.append((style, word[last:match.start()]))
        out.append(("class:variable", match.group()))
        last = match.end()
    if last < len(word):
        out.append((style, word[last:]))
    return out
