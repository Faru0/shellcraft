"""Command aliases: `alias ls='ls -a -l'`, kept in ~/.shellcraft/aliases (like ~/.bash_aliases).

The file holds one `alias NAME='COMMAND ARGS'` per line, with comments (`#`) and blank lines; it
can be edited by hand and is re-read whenever it changes, so an edit applies to the next command.
`alias` and `unalias` rewrite only the lines of the names they touch. Aliases that older versions
saved under "aliases" in config.json are moved into the file once (`migrate`).

An alias replaces the first word of a pipeline segment; the rest of the segment's words are
appended, so with the alias above `ls -r src` runs `ls -a -l -r src`. Expansion repeats while the
new first word is another alias it hasn't used yet, so `ls` may alias to `ls …` without looping.
A leading backslash (`\\ls`) runs the command itself, skipping its alias. Aliases apply to the
interactive shell, never to MCP clients (ShellContext.allow_aliases).
"""

from __future__ import annotations

import os
import re
import shlex
import tempfile
from pathlib import Path
from typing import Any

from core.parser import Command, ParseError, Pipeline, parse

NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.+-]*$")
BYPASS = "\\"


class AliasError(ValueError):
    pass


FILE_HEADER = """# ShellCraft aliases, like ~/.bash_aliases: one alias per line, read when the file changes.
#   alias ll='ls -l'
#   alias errors='grep -i error'
# `alias NAME='COMMAND'` and `unalias NAME` in the shell edit this file too; comments stay.
"""

_cache: dict[str, Any] = {"key": None, "aliases": {}, "errors": []}


def path() -> Path:
    from core.config import config_dir

    return config_dir() / "aliases"


def parse_file(text: str) -> tuple[dict[str, str], list[str]]:
    """The aliases in an aliases file, and one message per line that could not be used.
    Accepts `alias a='x' b='y'` as bash does, and `NAME=VALUE` without the `alias` word."""
    found: dict[str, str] = {}
    errors: list[str] = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("alias ") or line.startswith("alias\t"):
            line = line[5:].strip()
        try:
            words = shlex.split(line, comments=True)
        except ValueError as exc:
            errors.append(f"line {number}: {exc}")
            continue
        for word in words:
            name, sep, value = word.partition("=")
            if not sep:
                errors.append(f"line {number}: '{word}' is not NAME='COMMAND'")
                continue
            try:
                validate(name, value)
            except AliasError as exc:
                errors.append(f"line {number}: {exc}")
                continue
            found[name] = value.strip()
    return found, errors


def _file_aliases() -> tuple[dict[str, str], list[str]]:
    """The file's aliases, re-read only when the file changed (mtime and size)."""
    file = path()
    try:
        st = file.stat()
    except OSError:
        return {}, []
    key = (str(file), st.st_mtime_ns, st.st_size)
    if _cache["key"] != key:
        try:
            text = file.read_text(encoding="utf-8")
        except OSError:
            return {}, []
        _cache["aliases"], _cache["errors"] = parse_file(text)
        _cache["key"] = key
    return dict(_cache["aliases"]), list(_cache["errors"])


def file_errors() -> list[str]:
    """The lines of the aliases file that could not be used (for `alias` to report)."""
    return _file_aliases()[1]


def _config_aliases(config: dict[str, Any]) -> dict[str, str]:
    aliases = config.get("aliases")
    if not isinstance(aliases, dict):
        return {}
    return {k: v for k, v in aliases.items() if isinstance(k, str) and isinstance(v, str) and NAME.match(k)}


def get_all(config: dict[str, Any]) -> dict[str, str]:
    """Every alias: the aliases file, plus any still in config.json (from before the file)."""
    return {**_file_aliases()[0], **_config_aliases(config)}


def quote(value: str) -> str:
    """Quote an alias value so the line can be pasted back into a shell or the aliases file."""
    if "'" not in value:
        return f"'{value}'"
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _defines(line: str) -> set[str]:
    """The alias names a line of the file defines."""
    return set(parse_file(line)[0])


def _rewrite(edit: Any) -> None:
    """Apply `edit(lines) -> lines` to the aliases file and write it back atomically."""
    file = path()
    try:
        lines = file.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = FILE_HEADER.rstrip("\n").splitlines()
    lines = edit(lines)
    file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=file.parent, prefix=".aliases-", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines).rstrip("\n") + "\n")
        os.replace(tmp, file)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _cache["key"] = None


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
    """Define or replace an alias in the aliases file (the line is replaced where it was)."""
    validate(name, value)
    entry = f"alias {name}={quote(value.strip())}"

    def edit(lines: list[str]) -> list[str]:
        out, placed = [], False
        for line in lines:
            if name in _defines(line):
                if not placed:
                    out.append(entry)
                    placed = True
                continue
            out.append(line)
        if not placed:
            out.append(entry)
        return out

    _rewrite(edit)
    _config_section(config).pop(name, None)


def remove(config: dict[str, Any], name: str) -> bool:
    """Remove an alias from the file (and from config.json); returns whether it existed."""
    found = name in _file_aliases()[0]
    if found:
        _rewrite(lambda lines: [line for line in lines if name not in _defines(line)])
    return (_config_section(config).pop(name, None) is not None) or found


def remove_all(config: dict[str, Any]) -> int:
    """Remove every alias; the file's comments stay. Returns how many there were."""
    count = len(get_all(config))
    if _file_aliases()[0]:
        _rewrite(lambda lines: [line for line in lines if not _defines(line)])
    config["aliases"] = {}
    return count


def migrate(config: dict[str, Any]) -> int:
    """Move the aliases saved in config.json by older versions into the aliases file, once.
    Returns how many moved (the caller saves the config)."""
    old = _config_aliases(config)
    if not old:
        return 0
    have = _file_aliases()[0]
    moving = {n: v for n, v in old.items() if n not in have}
    if moving:
        _rewrite(lambda lines: lines + [f"alias {n}={quote(v)}" for n, v in sorted(moving.items())])
    config["aliases"] = {}
    return len(moving)


def _config_section(config: dict[str, Any]) -> dict[str, Any]:
    section = config.get("aliases")
    return section if isinstance(section, dict) else {}


def expand_command(cmd: Command, aliases: dict[str, str]) -> Command:
    if cmd.name.startswith(BYPASS) and len(cmd.name) > 1:
        return Command(cmd.name[1:], cmd.args, cmd.stderr, cmd.merge_stderr)  # \ls: the real ls
    used: set[str] = set()
    while cmd.name in aliases and cmd.name not in used:
        used.add(cmd.name)
        try:
            target = validate(cmd.name, aliases[cmd.name])
        except AliasError:
            break  # a hand-edited, broken entry in config.json: run the command as typed
        cmd = Command(target.name, target.args + cmd.args, cmd.stderr, cmd.merge_stderr)
    return cmd


def expand(pipeline: Pipeline, aliases: dict[str, str]) -> Pipeline:
    if not aliases and not any(c.name.startswith(BYPASS) for c in pipeline.segments):
        return pipeline
    return Pipeline([expand_command(c, aliases) for c in pipeline.segments], pipeline.redirect,
                    pipeline.stdin_path)
