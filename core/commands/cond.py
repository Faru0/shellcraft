"""Condition commands for bash-style scripts: test, [ ], [[ ]], true, false.

They print nothing; the answer is the exit status (0 true, 1 false, 2 a usage error), which
is what `if …; then`, `&&` and `||` look at.
"""

from __future__ import annotations

import fnmatch
import os
import re
from typing import Any

from core.builtins import builtin
from core.context import CommandError, WithStatus

_FILE_TESTS = {
    "-e": os.path.exists, "-f": os.path.isfile, "-d": os.path.isdir, "-L": os.path.islink,
    "-h": os.path.islink, "-s": lambda p: os.path.isfile(p) and os.path.getsize(p) > 0,
    "-r": lambda p: os.access(p, os.R_OK), "-w": lambda p: os.access(p, os.W_OK),
    "-x": lambda p: os.access(p, os.X_OK),
}
_STRING_TESTS = {"-z": lambda s: s == "", "-n": lambda s: s != ""}
_UNARY = set(_FILE_TESTS) | set(_STRING_TESTS)
_INT_OPS = {"-eq": int.__eq__, "-ne": int.__ne__, "-lt": int.__lt__, "-le": int.__le__,
            "-gt": int.__gt__, "-ge": int.__ge__}
_STR_OPS = {"=", "==", "!="}


class _Usage(Exception):
    pass


class _Evaluator:
    """POSIX `test` expressions: ! EXPR, EXPR -a EXPR, EXPR -o EXPR, ( EXPR ), unary and binary
    tests. `extended` is `[[ ]]`: `==` / `!=` match a glob pattern, `=~` a regex, `&&`/`||` are
    not available (the shell splits on them), `-a`/`-o` still work."""

    def __init__(self, args: list[str], extended: bool):
        self.args = args
        self.i = 0
        self.extended = extended

    def peek(self, k: int = 0) -> str | None:
        j = self.i + k
        return self.args[j] if j < len(self.args) else None

    def take(self) -> str:
        if self.i >= len(self.args):
            raise _Usage("argument expected")
        self.i += 1
        return self.args[self.i - 1]

    def run(self) -> bool:
        if not self.args:
            return False
        value = self.or_()
        if self.i != len(self.args):
            raise _Usage(f"unexpected argument '{self.args[self.i]}'")
        return value

    def or_(self) -> bool:
        value = self.and_()
        while self.peek() == "-o":
            self.take()
            right = self.and_()
            value = value or right
        return value

    def and_(self) -> bool:
        value = self.not_()
        while self.peek() == "-a" and self.peek(1) is not None:
            self.take()
            right = self.not_()
            value = value and right
        return value

    def not_(self) -> bool:
        if self.peek() == "!" and self.peek(1) is not None:
            self.take()
            return not self.not_()
        return self.primary()

    def primary(self) -> bool:
        tok = self.take()
        if tok == "(" and self.peek() is not None and self.peek(1) is not None:
            value = self.or_()
            if self.take() != ")":
                raise _Usage("')' expected")
            return value
        op = self.peek()
        if op in _STR_OPS or op in _INT_OPS or (self.extended and op == "=~"):
            self.take()
            return self.binary(tok, op, self.take())
        if tok in _UNARY and op is not None:
            arg = self.take()
            return _FILE_TESTS[tok](arg) if tok in _FILE_TESTS else _STRING_TESTS[tok](arg)
        return tok != ""

    def binary(self, left: str, op: str, right: str) -> bool:
        if op in _INT_OPS:
            try:
                return _INT_OPS[op](int(left.strip()), int(right.strip()))
            except ValueError:
                bad = left if not _is_int(left) else right
                raise _Usage(f"{bad}: integer expression expected") from None
        if op == "=~":
            try:
                return re.search(right, left) is not None
            except re.error as exc:
                raise _Usage(f"invalid regex '{right}': {exc}") from None
        if self.extended and op in ("==", "!="):
            match = fnmatch.fnmatchcase(left, right)
            return match if op == "==" else not match
        return (left == right) if op in ("=", "==") else (left != right)


def _is_int(text: str) -> bool:
    try:
        int(text.strip())
    except ValueError:
        return False
    return True


def _evaluate(prog: str, args: list[str], extended: bool = False) -> WithStatus:
    try:
        ok = _Evaluator(args, extended).run()
    except _Usage as exc:
        raise CommandError(f"{prog}: {exc}", status=2) from None
    return WithStatus("", 0 if ok else 1)


TEST_DOC = """# test, [ ], [[ ]]

Check a condition. Nothing is printed: the exit status is 0 when it holds and 1 when it
doesn't (2 for a mistake in the expression). Use them in bash-style `if` and with `&&` / `||`.

## Usage

```
test EXPRESSION
[ EXPRESSION ]
[[ EXPRESSION ]]
```

## Tests

| Test | True when |
| --- | --- |
| `-z S`, `-n S` | S is empty / not empty. `S` on its own is the same as `-n S`. |
| `A = B`, `A == B`, `A != B` | The strings are equal / differ. In `[[ ]]`, B is a glob pattern: `[[ $f == *.log ]]`. |
| `A =~ RE` | `[[ ]]` only: A matches the regular expression RE (Python syntax). |
| `A -eq B`, `-ne`, `-lt`, `-le`, `-gt`, `-ge` | Integer comparisons. |
| `-e F`, `-f F`, `-d F`, `-s F` | F exists / is a file / is a directory / is a non-empty file. |
| `-r F`, `-w F`, `-x F`, `-L F` | F is readable / writable / executable / a symbolic link. |
| `! E`, `E -a E`, `E -o E`, `( E )` | Not, and, or, grouping. |

`<` and `>` are redirections in ShellCraft, so compare numbers with `-lt` / `-gt`. Inside
`[[ ]]` use `-a` / `-o` (or `[[ a ]] && [[ b ]]`), not `&&` / `||`. Quote variables in `[ ]`,
as in bash: `[ "$x" = "a b" ]`; in `[[ ]]` they are never split.

## Examples

```
for f in *.log; do if [ -s "$f" ]; then echo "$f"; fi; done
[[ $host == *.gov ]] && queryDns $host
if [ "$n" -gt 3 -a -d out ]; then echo big; fi
```
"""


@builtin("test", "Check a condition (exit status 0 or 1)", "test EXPRESSION", category="shell", doc=TEST_DOC)
def _test(ctx: Any, args: list[str], stdin: str) -> WithStatus:
    return _evaluate("test", args)


@builtin("[", "Check a condition: [ EXPRESSION ]", "[ EXPRESSION ]", category="shell", doc=TEST_DOC)
def _bracket(ctx: Any, args: list[str], stdin: str) -> WithStatus:
    if not args or args[-1] != "]":
        raise CommandError("[: missing ']'", status=2)
    return _evaluate("[", args[:-1])


@builtin("[[", "Check a condition, with patterns: [[ EXPRESSION ]]", "[[ EXPRESSION ]]", category="shell",
         doc=TEST_DOC)
def _double_bracket(ctx: Any, args: list[str], stdin: str) -> WithStatus:
    if not args or args[-1] != "]]":
        raise CommandError("[[: missing ']]'", status=2)
    return _evaluate("[[", args[:-1], extended=True)


@builtin("true", "Do nothing, successfully (status 0)", "true", category="shell")
def _true(ctx: Any, args: list[str], stdin: str) -> str:
    return ""


@builtin("false", "Do nothing, unsuccessfully (status 1)", "false", category="shell")
def _false(ctx: Any, args: list[str], stdin: str) -> WithStatus:
    return WithStatus("", 1)
