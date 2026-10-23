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
from dataclasses import replace
from pathlib import Path
from typing import Any

from shellcraft.parser import Command, ParseError, Pipeline, parse, strip_comments

NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.+-]*$")
BYPASS = "\\"


class AliasError(ValueError):
    pass


PROFILE_HEADER = """\
# This profile's aliases, added on top of ~/.shellcraft/aliases (an alias here wins).
# `alias NAME='COMMAND'` in the shell writes here while the profile is active; `alias -g` writes the shared file.
"""
FILE_HEADER = """# ShellCraft aliases, like ~/.bash_aliases: one alias per line, read when the file changes.
#   alias ll='ls -l'
#   alias errors='grep -i error'
# `alias NAME='COMMAND'` and `unalias NAME` in the shell edit this file too; comments stay.
"""

_cache: dict[str, dict[str, Any]] = {}  # per file: {"key": (path, mtime, size), "aliases", "errors"}


def path() -> Path:
    """The base aliases file, shared by every profile."""
    from shellcraft.config import config_dir

    return config_dir() / "aliases"


def profile_path(config: dict[str, Any]) -> Path | None:
    """The active profile's aliases file (shellcraft.profiles), or None in the default setup."""
    name = getattr(config, "profile", None)
    if not name:
        return None
    from shellcraft import profiles

    return profiles.text_file(name, "aliases")


def target(config: dict[str, Any], global_: bool = False) -> Path:
    """Where `alias` writes: the active profile's file, or the base one (`alias -g`, the default setup)."""
    own = None if global_ else profile_path(config)
    return own if own is not None else path()


def parse_file(text: str) -> tuple[dict[str, str], list[str]]:
    """The aliases in an aliases file, and one message per line that could not be used.
    Accepts `alias a='x' b='y'` as bash does, and `NAME=VALUE` without the `alias` word."""
    found: dict[str, str] = {}
    errors: list[str] = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = strip_comments(raw).strip()  # `# …` lines and `alias x='y'  # note`, as in the rc file
        if not line:
            continue
        if line.startswith("alias ") or line.startswith("alias\t"):
            line = line[5:].strip()
        try:
            words = shlex.split(line)
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


def _file_aliases(file: Path | None = None) -> tuple[dict[str, str], list[str]]:
    """A file's aliases (the base file by default), re-read only when it changed (mtime and size)."""
    file = file or path()
    try:
        st = file.stat()
    except OSError:
        return {}, []
    key = (str(file), st.st_mtime_ns, st.st_size)
    entry = _cache.get(str(file))
    if entry is None or entry["key"] != key:
        try:
            text = file.read_text(encoding="utf-8")
        except OSError:
            return {}, []
        found, errors = parse_file(text)
        entry = _cache[str(file)] = {"key": key, "aliases": found, "errors": errors}
    return dict(entry["aliases"]), list(entry["errors"])


def file_errors(config: dict[str, Any] | None = None) -> list[str]:
    """The lines of the aliases files that could not be used (for `alias` to report)."""
    errors = _file_aliases()[1]
    own = profile_path(config) if config is not None else None
    return errors + ([f"(profile) {e}" for e in _file_aliases(own)[1]] if own is not None else [])


def _config_aliases(config: dict[str, Any]) -> dict[str, str]:
    aliases = config.get("aliases")
    if not isinstance(aliases, dict):
        return {}
    return {k: v for k, v in aliases.items() if isinstance(k, str) and isinstance(v, str) and NAME.match(k)}


def get_all(config: dict[str, Any]) -> dict[str, str]:
    """Every alias: the aliases file, any still in config.json (from before the file), and the
    active profile's aliases on top."""
    own = profile_path(config)
    return {**_file_aliases()[0], **_config_aliases(config), **(_file_aliases(own)[0] if own is not None else {})}


def defined_in(config: dict[str, Any], name: str) -> str | None:
    """"profile" or "base" (or None): where an alias comes from."""
    own = profile_path(config)
    if own is not None and name in _file_aliases(own)[0]:
        return "profile"
    return "base" if name in _file_aliases()[0] or name in _config_aliases(config) else None


def quote(value: str) -> str:
    """Quote an alias value so the line can be pasted back into a shell or the aliases file."""
    if "'" not in value:
        return f"'{value}'"
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _defines(line: str) -> set[str]:
    """The alias names a line of the file defines."""
    return set(parse_file(line)[0])


def _rewrite(edit: Any, file: Path | None = None) -> None:
    """Apply `edit(lines) -> lines` to an aliases file (the base one by default), atomically."""
    file = file or path()
    try:
        lines = file.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        header = FILE_HEADER if file == path() else PROFILE_HEADER
        lines = header.rstrip("\n").splitlines()
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
    _cache.pop(str(file), None)


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


def set_alias(config: dict[str, Any], name: str, value: str, global_: bool = False) -> None:
    """Define or replace an alias in the aliases file (the line is replaced where it was): the
    active profile's file, or the base one with `global_` (and in the default setup)."""
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

    _rewrite(edit, target(config, global_))
    if target(config, global_) == path():
        _config_section(config).pop(name, None)


def remove(config: dict[str, Any], name: str, global_: bool = False) -> bool:
    """Remove an alias from the file it is written to (the active profile's, or the base one with
    `global_`; config.json's old section too); returns whether it existed there."""
    file = target(config, global_)
    found = name in _file_aliases(file)[0]
    if found:
        _rewrite(lambda lines: [line for line in lines if name not in _defines(line)], file)
    if file != path():
        return found
    return (_config_section(config).pop(name, None) is not None) or found


def remove_all(config: dict[str, Any], global_: bool = False) -> int:
    """Remove every alias of the target file; its comments stay. Returns how many there were."""
    file = target(config, global_)
    count = len(_file_aliases(file)[0])
    if count:
        _rewrite(lambda lines: [line for line in lines if not _defines(line)], file)
    if file == path():
        count += len(_config_aliases(config))
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
        return replace(cmd, name=cmd.name[1:], bypass=True)  # \ls: the real ls
    used: set[str] = set()
    while cmd.name in aliases and cmd.name not in used:
        used.add(cmd.name)
        try:
            target = validate(cmd.name, aliases[cmd.name])
        except AliasError:
            break  # a hand-edited, broken entry in config.json: run the command as typed
        cmd = replace(cmd, name=target.name, args=target.args + cmd.args)
    return cmd


def expand(pipeline: Pipeline, aliases: dict[str, str]) -> Pipeline:
    if not aliases and not any(c.name.startswith(BYPASS) for c in pipeline.segments):
        return pipeline
    return Pipeline([expand_command(c, aliases) for c in pipeline.segments], pipeline.redirect,
                    pipeline.stdin_path)
