"""Offline drafts of a module's .md (man page) and .skill (MCP description), built from the analysis.

They are correct about what the code shows (options, defaults, choices, error messages, API
keys) and say TODO where a human — or an AI, see ai.py — has to add judgement (when not to use
it, real example output). Both pass the structure checks in check.py.
"""

from __future__ import annotations

import json
import re

from tools.ingest.analyze import Analysis, Option

_SNAKE = re.compile(r"[^a-z0-9_]+")


def summary_of(analysis: Analysis, name: str) -> str:
    text = analysis.docstring or analysis.description
    first = re.split(r"(?<=[.!?])\s|\n\s*\n", text.strip(), maxsplit=1)[0] if text else ""
    first = " ".join(first.split()).rstrip(".")
    if first.lower().startswith(f"{name.lower()} "):  # "mytool — does X" / "mytool: does X"
        first = first[len(name):].lstrip(" —-:")
    return (first[:1].upper() + first[1:])[:190] if first else f"Run the {name} script"


def metavar(opt: Option) -> str:
    return opt.metavar or opt.dest.upper()


def synopsis(analysis: Analysis, name: str) -> str:
    parts = [name]
    for opt in analysis.options:
        if opt.positional:
            continue
        flag = opt.short or opt.long
        word = flag if opt.kind == "boolean" else f"{flag} {metavar(opt)}"
        parts.append(word if opt.required else f"[{word}]")
    for opt in analysis.options:
        if opt.positional:
            word = metavar(opt) + ("..." if opt.kind == "array" else "")
            parts.append(word if opt.required else f"[{word}]")
    if not analysis.options and not analysis.uses_stdin:
        parts.append("[ARGS...]")
    return " ".join(parts)


def _meaning(opt: Option) -> str:
    text = " ".join(opt.help.split()) or "TODO: describe this option"
    text = text[:1].upper() + text[1:]
    if not text.endswith("."):
        text += "."
    if opt.choices:
        text += f" One of {', '.join(opt.choices)}."
    if opt.default not in (None, False, "", []) and "default" not in text.lower():
        text += f" Default {opt.default}."
    return text


def _option_cell(opt: Option) -> str:
    if opt.positional:
        return f"`{metavar(opt)}`" + ("..." if opt.kind == "array" else "")
    suffix = "" if opt.kind == "boolean" else f" {metavar(opt)}"
    return ", ".join(f"`{flag}{suffix}`" for flag in sorted(opt.flags, key=lambda f: f.startswith("--")))


def markdown(analysis: Analysis, name: str) -> str:
    summary = summary_of(analysis, name)
    out = [f"# {name}", "", f"{summary}.", "", "## Synopsis", "", "```", synopsis(analysis, name), "```", "",
           "## Description", ""]
    about = analysis.docstring or analysis.description
    rest = about.split("\n\n", 1)[1].strip() if "\n\n" in about else ""
    if rest:
        out += [rest, ""]
    if analysis.uses_stdin:
        out += [f"`{name}` reads its input from stdin, so it works at the end of a pipeline.", ""]
    out += ["The output is the text the script prints.", ""]
    if analysis.uses_network:
        out += ["It connects to the network.", ""]
    if analysis.writes_files:
        out += ["It can write files.", ""]
    if analysis.env_vars:
        out += ["### Setup", ""]
        out += [f"- `{var}`: set it once with `settings {var}`." for var in analysis.env_vars]
        out.append("")
    if analysis.options:
        out += ["## Options", "", "| Option | Meaning |", "| --- | --- |"]
        out += [f"| {_option_cell(o)} | {_meaning(o).replace('|', chr(92) + '|')} |" for o in analysis.options]
        out.append("")
    out += ["## Examples", "", "```", _example_line(analysis, name)]
    out.append(f"{_example_line(analysis, name)} | head -5" if not analysis.uses_stdin
               else f"cat notes.txt | {name}")
    out += [f"{_example_line(analysis, name)} > {name}.txt", "```", ""]
    if analysis.errors:
        out += ["## Errors", ""] + [f"- `{e}`" for e in analysis.errors] + [""]
    return "\n".join(out)


def _example_line(analysis: Analysis, name: str) -> str:
    words = [name]
    for i, opt in enumerate(o for o in analysis.options if o.positional):
        if opt.required or i == 0:
            words.append(opt.choices[0] if opt.choices else metavar(opt).lower())
    return " ".join(words)


def param_name(opt: Option, taken: set[str]) -> str:
    base = _SNAKE.sub("_", opt.dest.lower()).strip("_") or "value"
    if not base[0].isalpha():
        base = "opt_" + base
    if base == "stdin":
        base = "stdin_text"
    name, n = base, 2
    while name in taken:
        name, n = f"{base}_{n}", n + 1
    taken.add(name)
    return name


def params(analysis: Analysis) -> list[dict]:
    taken: set[str] = set()
    out = []
    positional_array = False
    for opt in [o for o in analysis.options if not o.positional] + [o for o in analysis.options if o.positional]:
        p: dict = {"name": param_name(opt, taken)}
        if not opt.positional:
            p["flag"] = opt.long or opt.short
            if opt.long and opt.short:
                p["short"] = opt.short
        elif positional_array:
            continue  # nothing may follow a positional array
        if opt.kind != "boolean":
            p["metavar"] = metavar(opt)
        if opt.kind != "string":
            p["type"] = opt.kind
        if opt.choices:
            p["values"] = opt.choices
        if opt.required:
            p["required"] = True
        p["description"] = _meaning(opt)
        positional_array = positional_array or (opt.positional and opt.kind == "array")
        out.append(p)
    return out


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)


def _toml_literal(text: str) -> str:
    """'…' (a TOML literal string, no escapes) when possible: easier to read for JSON-ish examples."""
    return f"'{text}'" if "'" not in text and "\n" not in text else _toml_value(text)


def _toml_block(text: str) -> str:
    if "'''" not in text:
        return "'''\n" + text.strip() + "'''"
    return json.dumps(text.strip(), ensure_ascii=False)


def skill(analysis: Analysis, name: str) -> str:
    summary = summary_of(analysis, name)
    lowered = summary[:1].lower() + summary[1:]
    where = "Pass the input text in `stdin`." if analysis.uses_stdin else \
        "Give its inputs as the parameters below."
    when = f"Use to {lowered}.\n{where}"
    example_params = {}
    for p in params(analysis):
        if p.get("required"):
            example_params[p["name"]] = [p.get("metavar", "x").lower()] if p.get("type") == "array" else \
                (p["values"][0] if p.get("values") else p.get("metavar", "x").lower())
    if analysis.uses_stdin:
        example_params["stdin"] = "some text"
    example = json.dumps(example_params) + f"  -> the text {name} prints"
    notes = ["Output is plain text: exactly what the script prints."]
    if analysis.uses_network:
        notes.append("Needs network access.")
    if analysis.writes_files:
        notes.append("Can write files on the machine running ShellCraft.")
    for var in analysis.env_vars:
        notes.append(f"Needs {var}, which the *user* sets once with `settings {var}`. If it is missing, "
                     "relay the error; don't retry.")
    notes.append("A bad option or a failure comes back as a tool error with a one-line message.")

    out = [
        f"# {name}.skill: the MCP tool description AI clients read. Drafted from the code by",
        "# `python -m tools.ingest`; review the lines marked TODO, then run:",
        f"#   python -m tools.ingest check modules/{name}.py",
        "",
        f"summary = {_toml_value(summary + '.')}",
        "",
        "# TODO: also say when NOT to use it, and which tool to use instead.",
        f"when_to_use = {_toml_block(when)}",
        "",
        f"usage = {_toml_value(synopsis(analysis, name))}",
        "",
        "# TODO: replace with a real call and its real output.",
        f"examples = [{_toml_literal(example)}]",
        "",
        f"notes = {_toml_block(chr(10).join(notes))}",
    ]
    for p in params(analysis):
        out += ["", "[[params]]"] + [f"{k} = {_toml_value(v)}" for k, v in p.items()]
    out += [
        "",
        "# Tests run by `python -m tools.ingest check`, never shown to AI clients. For example:",
        "# [[tests]]",
        "# args = [\"--some-option\", \"value\"]",
        "# stdin = \"input text\"",
        "# contains = \"expected output\"     # or: expect = \"exact output\", or: error = \"message\"",
        "",
    ]
    return "\n".join(out)
