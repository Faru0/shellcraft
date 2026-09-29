"""Command aliases: `alias ls='ls -a -l'`, saved under "aliases" in ~/.shellcraft/config.json.

An alias replaces the first word of a pipeline segment; the rest of the segment's words are
appended, so with the alias above `ls -r src` runs `ls -a -l -r src`. Expansion repeats while the
new first word is another alias it hasn't used yet, so `ls` may alias to `ls …` without looping.
A leading backslash (`\\ls`) runs the command itself, skipping its alias. Aliases apply to the
interactive shell and `-c`, never to MCP clients (ShellContext.allow_aliases).
"""

from __future__ import annotations

import re
from typing import Any

from core.parser import Command, ParseError, Pipeline, parse

NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.+-]*$")
BYPASS = "\\"


class AliasError(ValueError):
    pass


def get_all(config: dict[str, Any]) -> dict[str, str]:
    aliases = config.get("aliases")
    if not isinstance(aliases, dict):
        return {}
    return {k: v for k, v in aliases.items() if isinstance(k, str) and isinstance(v, str) and NAME.match(k)}


def validate(name: str, value: str) -> Command:
    """Check an alias definition; returns the single command it expands to."""
    if not NAME.match(name):
        raise AliasError(f"invalid alias name '{name}' (letters, digits, _ . + - only)")
    try:
        pipeline = parse(value)
    except ParseError as exc:
        raise AliasError(f"alias {name}: {exc.message}") from None
    if pipeline is None:
        raise AliasError(f"alias {name}: the expansion is empty")
    if len(pipeline.segments) > 1 or pipeline.redirect is not None:
        raise AliasError(f"alias {name}: an alias must be a single command (no '|', '>' or '>>')")
    return pipeline.segments[0]


def set_alias(config: dict[str, Any], name: str, value: str) -> None:
    validate(name, value)
    aliases = config.get("aliases")
    if not isinstance(aliases, dict):
        aliases = config["aliases"] = {}
    aliases[name] = value


def remove(config: dict[str, Any], name: str) -> bool:
    aliases = config.get("aliases")
    return isinstance(aliases, dict) and aliases.pop(name, None) is not None


def expand_command(cmd: Command, aliases: dict[str, str]) -> Command:
    if cmd.name.startswith(BYPASS) and len(cmd.name) > 1:
        return Command(cmd.name[1:], cmd.args)  # \ls: the real ls
    used: set[str] = set()
    while cmd.name in aliases and cmd.name not in used:
        used.add(cmd.name)
        try:
            target = validate(cmd.name, aliases[cmd.name])
        except AliasError:
            break  # a hand-edited, broken entry in config.json: run the command as typed
        cmd = Command(target.name, target.args + cmd.args)
    return cmd


def expand(pipeline: Pipeline, aliases: dict[str, str]) -> Pipeline:
    if not aliases and not any(c.name.startswith(BYPASS) for c in pipeline.segments):
        return pipeline
    return Pipeline([expand_command(c, aliases) for c in pipeline.segments], pipeline.redirect)
