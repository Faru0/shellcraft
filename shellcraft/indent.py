"""Python-style blocks: a header ending in `:` and an indented body, turned into the brace form.

    for -py line in (cat hosts.txt):            for -py line in (cat hosts.txt) {
        if line.startswith("#"):          →         if -py (line.startswith("#")) {
            continue                                    continue
        echo $line                                  }
                                                    echo $line
                                                }

Headers: `for` / `if` / `elif` / `else` / `while` / `until` / `select` / `case` with `-py` (inside
a colon block the flag is optional), `def NAME(PARAMS):`, `try:` / `except [as NAME]:` / `finally:`
and `match WORD:` (`try` and `match` need no flag: they aren't bash words), a `case` block's
`PATTERN:` arms and a `match` block's `case PATTERN:` arms (`match` is `case -py` spelled the
Python way).
Text after the colon is a one-line body (`for -py n in 1..3: echo $n`). A condition that reads as
Python is wrapped in `( … )`; anything else is a command (`if -py grep -q x f:`). `pass` is a
command that does nothing.

Only colon blocks change. Every other line (bash blocks, brace blocks, `name() { … }`) passes
through as it is, so the styles mix: a colon block in a bash function, a bash `if …; then` in a
colon block. The parser in shellcraft.script never sees a colon.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from shellcraft.parser import ParseError, strip_comments

_KEYWORDS = ("for", "if", "while", "until", "select", "case")
_DEF = re.compile(r"def\s+[A-Za-z_][A-Za-z0-9_-]*\s*\(.*\)$")
_BASH_WORD = re.compile(r"(?:^|[\s;])(?:then|do)(?:[\s;]|$)")
_FLAG = re.compile(r"^-py(?:\s+|$)")
_MERGES = {"if": ("elif", "else"), "try": ("except", "finally")}  # parts that join a block above


class IndentError(ParseError):
    """A colon block that is indented wrongly; `line` is its 1-based line number."""

    def __init__(self, message: str, line: int):
        super().__init__(f"line {line}: {message}", None)
        self.line = line


@dataclass
class _Level:
    kind: str  # "if" (if / elif / else), "try" (try / except / finally), "case", "match", "arm" or "block"
    header: str  # the header line's indentation
    body: str | None = None  # the body's indentation, once its first line is seen
    inline: bool = False  # a one-line body after the colon: the next line closes it
    line: int = 0  # the header's line number
    parts: tuple[str, ...] = ()  # a try: the `except` / `finally` seen so far


def split_header(text: str) -> tuple[str, str] | None:
    """(head, rest) at the header colon: the first `:` outside quotes and brackets that ends the
    text or has a blank after it. None when there is none."""
    quote, depth, i, n = None, 0, 0, len(text)
    while i < n:
        ch = text[i]
        if quote:
            if ch == "\\" and quote == '"':
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif ch == ":" and not depth and (i + 1 == n or text[i + 1] in " \t"):
            return text[:i].rstrip(), text[i + 1:].strip()
        i += 1
    return None


_TEST_WORDS = ("[", "[[", "test")


def _is_python(cond: str) -> bool:
    """Whether a condition reads as a Python expression ShellCraft accepts. A bash test
    (`[ -f x ]`, `[[ … ]]`, `test …`, with or without `!`) is a command, though `[ -f x ]` would
    also read as a Python list."""
    from shellcraft.script import compile_condition

    words = cond.split(None, 2)
    if words and (words[0] in _TEST_WORDS or (words[0] == "!" and len(words) > 1 and words[1] in _TEST_WORDS)):
        return False
    try:
        compile_condition(cond)
    except ParseError:
        return False
    return True


def _keyword_header(stripped: str, in_block: bool) -> tuple[str, str, str] | None:
    """(keyword, the text after it and `-py`, the one-line body) for a colon header."""
    parts = split_header(stripped)
    if parts is None:
        return None
    head, rest = parts
    word = head.split(None, 1)[0] if head else ""
    if word == "def":
        return ("def", head, rest) if _DEF.fullmatch(head) else None
    if word in ("elif", "else", "except", "finally") and in_block:
        return word, head[len(word):].strip(), rest
    if word == "try":
        after = head[len(word):].strip()
        if after not in ("", "-py"):
            return None  # a command named try
        return word, "", rest
    if word == "match":
        after = head[len(word):].strip()
        if after.startswith(("(", "=")):
            return None  # match(…), the condition function, or a command
        flagged = _FLAG.match(after)
        return word, after[flagged.end():].strip() if flagged else after, rest
    if word not in _KEYWORDS or _BASH_WORD.search(head):
        return None  # `if [ x ]; then echo a: b; fi` is bash
    after = head[len(word):].strip()
    flagged = _FLAG.match(after)
    if not flagged and not in_block:
        return None
    if flagged:
        after = after[flagged.end():].strip()
    if word == "case" and re.search(r"\sin$", " " + after):
        return None  # a bash `case $x in`
    return word, after, rest


def _condition(word: str, cond: str, number: int) -> str:
    if not cond:
        raise IndentError(f"{word}: missing a condition before ':'", number)
    return f"({cond})" if _is_python(cond) else cond


def has_colon_block(text: str) -> bool:
    """Whether any line of `text` starts a colon block (a cheap check before to_braces)."""
    if ":" not in text:
        return False
    return any(_keyword_header(strip_comments(line).strip(), False) is not None for line in text.splitlines())


def opens_block(line: str) -> bool:
    """Whether a line typed at the prompt is a colon header with its body still to come."""
    stripped = strip_comments(line).strip()
    found = _keyword_header(stripped, False)
    return found is not None and not found[2]


def to_braces(text: str) -> list[tuple[int, str]]:
    """(line number, line) for every line of `text`, with colon blocks in the brace form. Raises
    IndentError for a body that isn't indented, a dedent to no known level, or mixed tabs."""
    out: list[tuple[int, str]] = []
    stack: list[_Level] = []
    last = 0

    def close(number: int) -> None:
        top = stack.pop()
        if top.kind == "try" and not top.parts:
            raise IndentError("try: needs an 'except:' or a 'finally:'", top.line)
        out.append((number, "}"))

    for number, raw in enumerate(text.splitlines(), 1):
        stripped = strip_comments(raw).strip()
        if not stripped:
            out.append((number, raw))
            continue
        indent = raw[:len(raw) - len(raw.lstrip())]
        if stack and stack[-1].inline:
            top = stack.pop()
            stack.append(replace(top, body=top.header + "\x00", inline=False))  # closed by any line
        if stack and stack[-1].body is None:
            top = stack[-1]
            if len(indent) <= len(top.header) or not indent.startswith(top.header):
                if not indent.startswith(top.header) and not top.header.startswith(indent):
                    raise IndentError("inconsistent use of tabs and spaces in indentation", number)
                raise IndentError("expected an indented block", number)
            top.body = indent
        merged = ""
        try_parts: tuple[str, ...] = ()  # the try parts before this line's except / finally
        popped: str | None = None  # the header indentation of the last block this line closed
        while stack and (body := stack[-1].body) is not None and not indent.startswith(body):
            top = stack[-1]
            if not body.startswith(indent) and "\x00" not in body:
                raise IndentError("inconsistent use of tabs and spaces in indentation", number)
            word = stripped.split(None, 1)[0].rstrip(":")
            if indent == top.header and word in _MERGES.get(top.kind, ()):
                stack.pop()
                merged, try_parts = "} ", top.parts
                break
            popped = top.header
            close(last)
        if popped is not None and not merged:
            known = stack[-1].body if stack else popped
            if indent != known and not (not stack and len(indent) < len(popped)):
                raise IndentError("unindent does not match any outer indentation level", number)
        in_block = bool(stack) or bool(merged)
        last = number

        if stack and stack[-1].kind in ("case", "match"):
            head, rest = _arm(stack[-1].kind, stripped, number)
            rest = "true" if rest == "pass" else rest
            out.append((number, f"{indent}{head} {{" + (f" {rest}" if rest else "")))
            stack.append(_Level("arm", indent, inline=bool(rest)))
            continue

        found = _keyword_header(stripped, in_block)
        if found is None:
            if merged:
                raise IndentError(f"'{stripped.split(None, 1)[0]}' needs a ':' at the end", number)
            first = stripped.split(None, 1)[0].rstrip(":")
            if first in ("elif", "else") and stripped.endswith(":"):
                raise IndentError(f"'{first}' without a matching if", number)
            if first in ("except", "finally") and split_header(stripped) is not None:
                raise IndentError(f"'{first}' without a matching try", number)
            out.append((number, f"{indent}true" if stripped == "pass" else raw))
            continue
        word, after, rest = found
        if word in ("elif", "else") and not merged:
            raise IndentError(f"'{word}' without a matching if", number)
        if word in ("except", "finally") and not merged:
            raise IndentError(f"'{word}' without a matching try", number)
        if word == "def":
            line = f"{after} {{"
            kind = "block"
        elif word in ("if", "elif", "while", "until"):
            prefix = "} elif" if word == "elif" else f"{word} -py"
            line = f"{prefix} {_condition(word, after, number)} {{"
            kind = "if" if word in ("if", "elif") else "block"
        elif word == "else":
            if after:
                raise IndentError("else: nothing goes between 'else' and ':'", number)
            line, kind = "} else {", "if"
        elif word == "try":
            line, kind = "try -py {", "try"
        elif word in ("except", "finally"):
            line, kind = _try_part(word, after, try_parts, number), "try"
            try_parts += (word,)
        elif word in ("case", "match"):
            if not after:
                example = "case -py $choice:" if word == "case" else "match $choice:"
                raise IndentError(f"{word}: missing a word before ':' ({example})", number)
            if word == "match" and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", after):
                after = f"${after}"  # `match choice:` reads the variable, as Python would
            line, kind = f"case -py {after} {{", word
        else:  # for, select
            line, kind = f"{word} -py {after} {{", "block"
        if merged and word not in ("elif", "else", "except", "finally"):
            raise IndentError(f"expected 'elif' or 'else', got '{word}'", number)
        if word in ("elif", "else"):
            line = line[2:] if line.startswith("} ") else line
            line = "} " + line
        if kind in ("case", "match") and rest:
            raise IndentError(f"{word}: the choices go on the lines below, indented", number)
        if rest:
            line += f" {'true' if rest == 'pass' else rest}"
        out.append((number, indent + line))
        stack.append(_Level(kind, indent, inline=bool(rest), line=number, parts=try_parts))
    while stack:
        close(last)
    return out


def _arm(kind: str, stripped: str, number: int) -> tuple[str, str]:
    """(patterns, one-line body) of a choice in a `case` or `match` block. A match arm is
    `case PATTERN:`; a case arm may be written that way too."""
    parts = split_header(stripped)
    head = parts[0] if parts else ""
    labelled = re.match(r"case\s+", head)
    if kind == "match" and (parts is None or not labelled):
        raise IndentError("match: each choice is case PATTERN: (for anything else: case _:)", number)
    if parts is None or not head:
        raise IndentError("case: each choice is PATTERN: (for anything else: _:)", number)
    if labelled:
        head = head[labelled.end():]
    if "if" in re.sub(r"\"[^\"]*\"|'[^']*'", " x ", head).split()[1:]:
        raise IndentError(f"{kind}: no 'if' guards on a choice; test inside it instead", number)
    return head, parts[1]


def _try_part(word: str, after: str, parts: tuple[str, ...], number: int) -> str:
    """The brace form of an `except [as NAME]:` or `finally:` line, after the parts before it."""
    if word in parts:
        raise IndentError(f"a try has only one '{word}:'", number)
    if word == "except" and "finally" in parts:
        raise IndentError("'except:' goes before 'finally:'", number)
    if word == "finally":
        if after:
            raise IndentError("finally: nothing goes between 'finally' and ':'", number)
        return "} finally {"
    if not after:
        return "} except {"
    match = re.fullmatch(r"as\s+([A-Za-z_][A-Za-z0-9_]*)", after)
    if match is None:
        raise IndentError(f"except {after}: there are no error types here; use 'except:', or "
                          "'except as status:' for the failing status", number)
    return f"}} except as {match.group(1)} {{"


def translate(text: str) -> str:
    """`text` with its colon blocks in the brace form, comments dropped, lines kept apart."""
    return "\n".join(strip_comments(line) for _, line in to_braces(text))
