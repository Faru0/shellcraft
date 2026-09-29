"""Information commands: date, which, env."""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from core import aliases
from core.builtins import BUILTINS, builtin
from core.commands._io import lines_out
from core.modkit import ArgParser, ModuleError

if TYPE_CHECKING:
    from core.context import ShellContext


@builtin("date", "Print the current date and time", "date [-u] [--iso] [+FORMAT]", category="info", doc="""\
# date

Print the current date and time. Use `+FORMAT` for a custom format (Python `strftime` codes,
the same as Unix `date`).

| Option | Meaning |
| --- | --- |
| `-u` | Use UTC instead of local time. |
| `--iso` | ISO 8601 format, e.g. `2026-09-27T14:03:11+02:00`. |
| `+FORMAT` | Custom format, e.g. `+%Y-%m-%d` or `"+%H:%M:%S"`. |

Common codes: `%Y` year · `%m` month · `%d` day · `%H` hour · `%M` minute · `%S` second ·
`%A` weekday · `%B` month name · `%Z` timezone · `%s` unix timestamp.

## Examples

```
date
date +%Y-%m-%d
date -u --iso
date +%F > stamp.txt
```
""")
def date(ctx: ShellContext, args: list[str], stdin: str) -> str:
    fmt = None
    rest = []
    for a in args:
        if a.startswith("+"):
            fmt = a[1:]
        else:
            rest.append(a)
    parser = ArgParser("date")
    parser.add_argument("-u", "--utc", action="store_true")
    parser.add_argument("--iso", action="store_true")
    opts = parser.parse_args(rest)

    now = datetime.now(timezone.utc) if opts.utc else datetime.now().astimezone()
    if opts.iso:
        if fmt is not None:
            raise ModuleError("date: use either --iso or +FORMAT, not both")
        return now.isoformat(timespec="seconds") + "\n"
    if fmt is None:
        fmt = "%a %b %d %H:%M:%S %Z %Y"
    # %s (epoch seconds) isn't portable across platforms' strftime, so handle it here.
    fmt = fmt.replace("%s", str(int(now.timestamp())))
    try:
        return now.strftime(fmt) + "\n"
    except ValueError as exc:
        raise ModuleError(f"date: invalid format {fmt!r}: {exc}") from None


@builtin("which", "Show what a command name runs", "which [-a] NAME...", category="info", doc="""\
# which

Show what each NAME runs when you type it: an alias, a ShellCraft builtin, a module (with its
file), or an OS program on your PATH. They are checked in that order, and the first match wins.

| Option | Meaning |
| --- | --- |
| `-a` | Show every match, including commands hidden behind an alias, builtin or module. |

An OS program is marked *disabled* while the `system_commands` setting is off.

## Examples

```
which ls
which -a ls grep
which fetch git
```
""")
def which(ctx: ShellContext, args: list[str], stdin: str) -> str:
    parser = ArgParser("which")
    parser.add_argument("-a", "--all", action="store_true")
    parser.add_argument("names", nargs="+")
    opts = parser.parse_args(args)

    lines: list[str] = []
    missing: list[str] = []
    for name in opts.names:
        hits: list[str] = []
        if ctx.allow_aliases and (value := aliases.get_all(ctx.config).get(name)) is not None:
            hits.append(f"{name}: aliased to '{value}'")
        if name in BUILTINS:
            hits.append(f"{name}: ShellCraft builtin ({BUILTINS[name].category})")
        if (spec := ctx.registry.get(name)) is not None:
            hits.append(f"{name}: ShellCraft module ({spec.path})")
        if (path := shutil.which(name)) is not None:
            notes = [n for n, on in (("shadowed", bool(hits)), ("disabled: OS commands are off",
                                                                 not ctx.allow_system)) if on]
            hits.append(f"{name}: {path}" + (f" ({', '.join(notes)})" if notes else ""))
        if not hits:
            missing.append(name)
            lines.append(f"{name}: not found")
        else:
            lines += hits if opts.all else hits[:1]
    if len(missing) == len(opts.names):
        raise ModuleError(f"which: not found: {', '.join(missing)}")
    return lines_out(lines)


@builtin("env", "Print environment variables", "env [NAME...]", category="info", sensitive=True, doc="""\
# env

Print environment variables as `NAME=value`, sorted by name. With NAMEs, print only those
variables. Unset names are skipped.

Environment variables often hold secrets such as API keys, so `env` is **not** available to AI
clients through the MCP server.

## Examples

```
env
env PATH HOME
env | grep -i proxy
```
""")
def env(ctx: ShellContext, args: list[str], stdin: str) -> str:
    if args:
        return lines_out(f"{name}={os.environ[name]}" for name in args if name in os.environ)
    return lines_out(f"{k}={v}" for k, v in sorted(os.environ.items(), key=lambda kv: kv[0].lower()))
