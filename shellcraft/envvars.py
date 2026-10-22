"""The environment variables set in this ShellCraft session: `env set`, `export`, the rc file.

They go straight into `os.environ`, so every module (in-process), OS program, `sub` program,
lite subshell and `mcp start` server started afterwards sees them. They last for the session;
`~/.shellcraft/rc` (lines like `env set NAME=VALUE`) is how one is set at every start.

ShellCraft's value wins over the host's: `env set PATH=…` replaces it, and `env unset PATH`
brings the host's value back (the bash `unset PATH` removes it for the session instead).

A secret (`env set -s NAME`) is masked wherever it is shown and never reaches the history file;
with no value it is asked for with hidden typing (in an rc file: at every start).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class EnvVar:
    name: str
    secret: bool = False
    origin: str = "typed"  # "typed", "export", or the file it was set in ("rc", "rc.work")
    had_host: bool = False  # the host environment had this name when ShellCraft first set it
    host: str | None = None  # and its value then (`env unset` puts it back)


def origin() -> str:
    """Where a line runs from: the rc file (or a sourced file) being read, else typed."""
    from shellcraft import startup

    return startup.sourcing[-1] if startup.sourcing else "typed"


def valid(name: str) -> bool:
    return bool(NAME.match(name))


def set_var(ctx: Any, name: str, value: str, secret: bool = False, how: str | None = None) -> EnvVar:
    """Set NAME for this session: os.environ, and the shell variable when there is one."""
    entry = ctx.env_vars.get(name)
    if entry is None:
        had = name in os.environ
        entry = EnvVar(name, had_host=had, host=os.environ.get(name))
        ctx.env_vars[name] = entry
    entry.secret = secret or (entry.secret and how == "export")  # `export NAME` keeps a secret masked
    entry.origin = how or origin()
    os.environ[name] = value
    if name in ctx.variables or name in ctx.exported:
        ctx.variables[name] = value  # `$NAME` must never be stale
    if name in ctx.exported:
        ctx.exported[name] = value
    return entry


def unset_var(ctx: Any, name: str) -> str | None:
    """Drop ShellCraft's value. Returns "restored" when a host value came back, "removed" when
    the variable is gone, None when ShellCraft had not set it."""
    entry = ctx.env_vars.pop(name, None)
    exported = ctx.exported.pop(name, None)
    ctx.variables.pop(name, None)
    if entry is None and exported is None:
        return None
    if entry is not None and entry.had_host and entry.host is not None:
        os.environ[name] = entry.host
        return "restored"
    os.environ.pop(name, None)
    return "removed"


def forget(ctx: Any, name: str) -> None:
    """The bash `unset NAME`: gone for the session, and no longer ShellCraft's."""
    ctx.env_vars.pop(name, None)


def overrides(entry: EnvVar) -> bool:
    return entry.had_host and os.environ.get(entry.name) != entry.host


def session(ctx: Any) -> list[EnvVar]:
    """The variables set in this session, by name (still in the environment)."""
    return sorted((e for e in ctx.env_vars.values() if e.name in os.environ), key=lambda e: e.name.lower())


def shown(entry: EnvVar) -> str:
    """The value as `env` and `settings` show it: whole, or masked for a secret."""
    from shellcraft.settings import mask

    value = os.environ.get(entry.name, "")
    return mask(value) if entry.secret else value
