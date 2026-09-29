"""Named parameters declared in a module's .skill `[[params]]` table.

Each entry becomes one property of the module's MCP input schema, and a call's named values are
turned back into the CLI-style `args` list the module's run(args, stdin) already understands:

    [[params]]
    name = "output"            # the JSON property name (snake_case)
    flag = "--output"          # the CLI switch; omit it for a positional argument
    short = "-o"               # optional short form (docs and Tab completion only)
    metavar = "FORMAT"         # optional, for docs and Tab completion
    type = "string"            # string (default) | integer | number | boolean | array
    values = ["table", "json"] # optional: allowed values (for an array: allowed items)
    required = false
    description = "..."

A boolean is passed as its bare `flag` when true. An array repeats its `flag` once per item, or,
as a positional, adds each item. Switches come first, then positionals in declaration order.
"""

from __future__ import annotations

import re
from typing import Any

TYPES = ("string", "integer", "number", "boolean", "array")
KEYS = {"name", "flag", "short", "metavar", "type", "values", "required", "description"}
STDIN = "stdin"
_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_FLAG = re.compile(r"^--?[A-Za-z0-9][\w-]*$")


class ParamError(ValueError):
    """A call's named values don't match the declared params."""


def problems(params: list[dict[str, Any]]) -> list[str]:
    """Everything wrong with a [[params]] table, as messages (empty when it's valid)."""
    errors: list[str] = []
    seen: set[str] = set()
    positional_array = None
    for i, p in enumerate(params, start=1):
        label = f"entry #{i}"
        if not isinstance(p, dict):
            errors.append(f"{label} is not a table")
            continue
        name = p.get("name")
        if not isinstance(name, str) or not _NAME.match(name) or name == STDIN:
            errors.append(f"{label}: `name` must be snake_case and not '{STDIN}'")
        elif name in seen:
            errors.append(f"{label}: '{name}' is declared twice")
        else:
            seen.add(name)
            label = f"'{name}'"
        if stray := sorted(set(p) - KEYS):
            errors.append(f"{label}: unknown key(s) {', '.join(stray)}")
        kind = p.get("type", "string")
        if kind not in TYPES:
            errors.append(f"{label}: `type` must be one of {', '.join(TYPES)}")
        for key in ("flag", "short"):
            if key in p and (not isinstance(p[key], str) or not _FLAG.match(p[key])):
                errors.append(f"{label}: `{key}` must look like --name or -n")
        if kind == "boolean" and "flag" not in p:
            errors.append(f"{label}: a boolean needs a `flag`")
        if "short" in p and "flag" not in p:
            errors.append(f"{label}: `short` needs a `flag`")
        values = p.get("values")
        if values is not None and (not isinstance(values, list) or not all(isinstance(v, str) for v in values)):
            errors.append(f"{label}: `values` must be a list of strings")
        if not isinstance(p.get("description", ""), str) or not p.get("description"):
            errors.append(f"{label}: needs a `description`")
        if "flag" not in p:
            if positional_array is not None:
                errors.append(f"{label}: no positional may follow the positional array '{positional_array}'")
            if kind == "array":
                positional_array = p.get("name")
    return errors


def input_schema(params: list[dict[str, Any]]) -> dict[str, Any]:
    """The MCP JSON Schema for a module's named params plus the `stdin` text."""
    properties: dict[str, Any] = {}
    for p in params:
        kind = p.get("type", "string")
        prop: dict[str, Any] = {"description": _describe(p)}
        if kind == "array":
            item: dict[str, Any] = {"type": "string"}
            if p.get("values"):
                item["enum"] = list(p["values"])
            prop.update(type="array", items=item)
        else:
            prop["type"] = kind
            if p.get("values"):
                prop["enum"] = list(p["values"])
        properties[p["name"]] = prop
    properties[STDIN] = {
        "type": "string",
        "description": "Text piped into the tool, as if from `previous | tool`. Omit or leave empty for none.",
    }
    schema: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
    required = [p["name"] for p in params if p.get("required")]
    if required:
        schema["required"] = required
    return schema


def _describe(p: dict[str, Any]) -> str:
    text = str(p.get("description", "")).strip()
    if flag := p.get("flag"):
        text += f" (CLI: {flag})"
    return text


def to_argv(params: list[dict[str, Any]], values: dict[str, Any]) -> list[str]:
    """Turn a call's named values into CLI args; raises ParamError on unknown, missing or mistyped values."""
    declared = {p["name"]: p for p in params}
    if unknown := sorted(set(values) - set(declared)):
        raise ParamError(f"unknown parameter(s): {', '.join(unknown)} (expected: {', '.join(declared) or 'none'})")
    if missing := [p["name"] for p in params if p.get("required") and values.get(p["name"]) in (None, "", [])]:
        raise ParamError(f"missing required parameter(s): {', '.join(missing)}")

    switches: list[str] = []
    positionals: list[str] = []
    for p in params:
        value = values.get(p["name"])
        if value is None:
            continue
        words = _words(p, value)
        flag = p.get("flag")
        if p.get("type") == "boolean":
            if value:
                switches.append(flag)
        elif flag:
            for word in words:
                switches += [flag, word]
        else:
            positionals += words
    return switches + positionals


def _words(p: dict[str, Any], value: Any) -> list[str]:
    name, kind = p["name"], p.get("type", "string")
    checks = {
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "array": lambda v: isinstance(v, list) and all(isinstance(i, (str, int, float)) for i in v),
    }
    if not checks[kind](value):
        raise ParamError(f"parameter '{name}' must be {'an' if kind[0] in 'aeiou' else 'a'} {kind}")
    items = [str(v) for v in value] if kind == "array" else [_scalar(value)]
    allowed = p.get("values")
    if allowed and (bad := [i for i in items if i not in allowed]):
        raise ParamError(f"parameter '{name}': {', '.join(map(repr, bad))} not in {', '.join(allowed)}")
    return items


def _scalar(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def as_args(params: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """[[args]]-style entries (for Tab completion and man-style docs) derived from [[params]]."""
    entries = []
    for p in params:
        if flag := p.get("flag"):
            name = " / ".join(f for f in (p.get("short"), flag) if f)
            if p.get("type") != "boolean":
                name += " " + p.get("metavar", p["name"].upper())
        else:
            name = p.get("metavar", p["name"].upper()) + ("..." if p.get("type") == "array" else "")
        entries.append({"name": name, "description": str(p.get("description", "")),
                        "values": [str(v) for v in p.get("values") or []]})
    return entries
