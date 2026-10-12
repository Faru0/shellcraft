"""`$(( … ))` arithmetic: integer expressions evaluated without `eval`.

Operators: `+ - * / % **`, `//`, comparisons (`<`, `<=`, `==`, `!=`, `>`, `>=` give 1 or 0),
`&& || !` (as `and or not`), bit operations `& | ^ ~ << >>`, parentheses, and the ternary
`a ? b : c`. Names are variables (a missing or non-numeric one counts as 0, as in bash).
`/` is integer division, as in bash.
"""

from __future__ import annotations

import ast
import operator
import re
from typing import Any, Callable, Mapping

MAX_BITS = 4096  # a result (or an intermediate) bigger than this is refused: `2 ** 99999999` must not hang

_BIN: dict[type, Callable[[int, int], int]] = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: lambda a, b: int(a / b) if a * b >= 0 else -(-a // b), ast.FloorDiv: operator.floordiv,
    ast.Mod: lambda a, b: a - b * int(a / b), ast.Pow: operator.pow,
    ast.BitAnd: operator.and_, ast.BitOr: operator.or_, ast.BitXor: operator.xor,
    ast.LShift: operator.lshift, ast.RShift: operator.rshift,
}
_CMP: dict[type, Callable[[int, int], bool]] = {
    ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
    ast.Eq: operator.eq, ast.NotEq: operator.ne,
}
_TERNARY = re.compile(r"^(.*?)\?(.*):(.*)$", re.S)


class ArithError(ValueError):
    pass


def evaluate(text: str, variables: Mapping[str, str] | None = None) -> int:
    """The value of an arithmetic expression; raises ArithError with a message for the user."""
    source = _to_python(text.strip())
    if not source:
        raise ArithError("empty expression in $(( ))")
    try:
        tree = ast.parse(source.strip(), mode="eval")
    except SyntaxError:
        raise ArithError(f"invalid arithmetic expression: {text.strip()}") from None
    return _eval(tree.body, variables or {})


def _to_python(text: str) -> str:
    """Bash spellings to Python ones: `&&` → and, `||` → or, `!x` → not x, `a ? b : c` → (b if a else c)."""
    match = _TERNARY.match(text)
    if match and "?" in text:
        cond, yes, no = (match.group(i).strip() for i in (1, 2, 3))
        return f"(({_to_python(yes)}) if ({_to_python(cond)}) else ({_to_python(no)}))"
    text = text.replace("&&", " and ").replace("||", " or ")
    text = re.sub(r"!(?!=)", " not ", text)
    return text


def _eval(node: ast.AST, variables: Mapping[str, str]) -> int:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool):
            return int(node.value)
        if isinstance(node.value, int):
            return node.value
        raise ArithError(f"only integers are allowed in $(( )), not {node.value!r}")
    if isinstance(node, ast.Name):
        raw = variables.get(node.id, "0") if hasattr(variables, "get") else "0"
        try:
            return int(str(raw).strip() or "0", 0)
        except ValueError:
            return 0
    if isinstance(node, ast.UnaryOp):
        value = _eval(node.operand, variables)
        if isinstance(node.op, ast.USub):
            return -value
        if isinstance(node.op, ast.UAdd):
            return value
        if isinstance(node.op, ast.Not):
            return int(not value)
        if isinstance(node.op, ast.Invert):
            return ~value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        left, right = _eval(node.left, variables), _eval(node.right, variables)
        if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and right == 0:
            raise ArithError("division by zero")
        if isinstance(node.op, ast.Pow):
            if right < 0:
                raise ArithError("negative exponent")
            if left.bit_length() * right > MAX_BITS:
                raise ArithError("the result would be too large")
        if isinstance(node.op, ast.LShift) and right > MAX_BITS:
            raise ArithError("the result would be too large")
        result = _BIN[type(node.op)](left, right)
        if result.bit_length() > MAX_BITS:
            raise ArithError("the result would be too large")
        return result
    if isinstance(node, ast.Compare):
        left = _eval(node.left, variables)
        for op, comparator in zip(node.ops, node.comparators):
            if type(op) not in _CMP:
                raise ArithError("only < <= == != > >= comparisons are allowed")
            right = _eval(comparator, variables)
            if not _CMP[type(op)](left, right):
                return 0
            left = right
        return 1
    if isinstance(node, ast.BoolOp):
        values = [_eval(v, variables) for v in node.values]
        return int(all(values) if isinstance(node.op, ast.And) else any(values))
    if isinstance(node, ast.IfExp):
        return _eval(node.body, variables) if _eval(node.test, variables) else _eval(node.orelse, variables)
    raise ArithError(f"'{type(node).__name__}' is not allowed in $(( ))")


def is_assignment(text: str) -> re.Match[str] | None:
    """`NAME=VALUE` on its own (VALUE may be quoted or empty): the match, or None."""
    return _ASSIGN.match(text)


_ASSIGN = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.S)


def assignment_value(raw: str, expand: Callable[[str], list[str]]) -> str:
    """The value of an assignment's right-hand side, expanded as one word (`expand` tokenizes)."""
    raw = raw.strip()
    if not raw:
        return ""
    words = expand(raw)
    return " ".join(words)


def quote(value: Any) -> str:
    text = str(value)
    if text and re.fullmatch(r"[A-Za-z0-9_./:=@+,%-]+", text):
        return text
    return "'" + text.replace("'", "'\\''") + "'"
