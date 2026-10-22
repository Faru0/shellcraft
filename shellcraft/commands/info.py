"""Information commands: date, which, env."""

from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from shellcraft import aliases, envvars
from shellcraft.builtins import BUILTINS, builtin
from shellcraft.commands._io import lines_out
from shellcraft.context import CommandError, Styled
from shellcraft.modkit import ArgParser, ModuleError

if TYPE_CHECKING:
    from shellcraft.context import ShellContext


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

    now = datetime.now(UTC) if opts.utc else datetime.now().astimezone()
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
which curl git
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
        if ctx.allow_aliases and name in ctx.functions:
            hits.append(f"{name}: shell function (declare -f {name})")
        if name in ("for", "if"):
            hits.append(f"{name}: ShellCraft shell keyword (man {name})")
        elif name in BUILTINS:
            hits.append(f"{name}: ShellCraft builtin ({BUILTINS[name].category})")
        if (spec := ctx.registry.get(name)) is not None:
            hits.append(f"{name}: ShellCraft module ({spec.path})")
        elif ctx.registry.is_disabled(name):
            hits.append(f"{name}: ShellCraft module, disabled ({ctx.registry.files[name]})")
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


ENV_USAGE = "env [-a | NAME...] | env set [-s] NAME[=VALUE] [VALUE] | env unset NAME..."


@builtin("env", "ShellCraft's environment variables: list, set, unset (-a: all of them)", ENV_USAGE,
         category="info", sensitive=True, doc="""\
# env

The environment variables set in this ShellCraft session, and the commands to manage them.
`env` alone lists **only those** (set with `env set`, `export` or in your rc file), so they stay
easy to see; **`env -a`** prints the whole environment, as other shells' `env` does.

## Usage

```
env                          # the variables set in this session: name, value (in full), where from
env -a                       # every environment variable, NAME=value (also --all)
env NAME...                  # just those, from the whole environment
env set NAME=VALUE           # set one for this session (several: env set A=1 B=2)
env set NAME VALUE           # the same
env set -s NAME              # a secret: asks with hidden typing
env set -s NAME VALUE        # a secret in one go (the history keeps it masked)
env unset NAME...            # drop ShellCraft's value; a value your shell had comes back
```

## Who sees them

A variable goes into ShellCraft's own environment at once, so:

| Runs | Sees it |
| --- | --- |
| Modules and builtins in this shell | at once |
| OS programs, `sub` programs, lite subshells, `cmd &` | when they start after the `env set` |
| A subshell or an `mcp start` server already running | no: start it again (`mcp restart`) |
| An AI client's own `--mcp` server (its own process) | no: put it in the client's `env` block; `run_in_shell` runs here, so it does |

ShellCraft's value wins: `env set PATH=…` replaces your shell's PATH, and `env unset PATH`
brings it back. (The bash `unset NAME` removes a variable for the session instead.)

## Keeping them: the rc file

Variables last for the session. To set them at every start, put the lines in
`~/.shellcraft/rc` (`man startup`):

```
env set PROJECT_ROOT=~/projects
env set -s GITHUB_TOKEN          # a secret: asked with hidden typing at each start
```

`env` shows where each one came from (`typed`, `rc`, `export`), and `settings` lists them too,
under "Environment (this session)".

## Secrets

`env set -s` masks the value everywhere (`••••ab12`) and keeps `env set -s NAME VALUE` out of the
history file. With no value it asks with hidden typing (in an rc file: at every start). A
variable you already export in your own shell needs none of this: ShellCraft and everything it
runs see it as it is.

Environment variables often hold secrets, so `env` is **not** available to AI clients through
the MCP server.

## Examples

```
env set API_URL=https://api.example.com
curl $API_URL/health
env
env -a | grep -i proxy
env unset API_URL
```
""")
def env(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    if not args:
        return _env_list(ctx)
    if args in (["-a"], ["--all"]):
        return lines_out(f"{k}={v}" for k, v in sorted(os.environ.items(), key=lambda kv: kv[0].lower()))
    if args[0] == "set":
        return _env_set(ctx, args[1:])
    if args[0] == "unset":
        return _env_unset(ctx, args[1:])
    if any(a.startswith("-") for a in args):
        raise CommandError(f"env: usage: {ENV_USAGE}")
    return lines_out(f"{name}={os.environ[name]}" for name in args if name in os.environ)


def _env_list(ctx: ShellContext) -> Any:
    from rich.table import Table
    from rich.text import Text

    entries = envvars.session(ctx)
    if not entries:
        return Text("no variables set in this session: `env set NAME=VALUE` sets one, `env -a` shows "
                    "the whole environment", style="sc.muted")
    table = Table(title="Environment (this session)", title_style="sc.prompt", border_style="sc.border",
                  header_style="sc.accent", expand=False)
    table.add_column("name", style="sc.path", no_wrap=True)
    table.add_column("value", overflow="fold")  # long values wrap in full, never cut
    table.add_column("from", style="sc.muted", no_wrap=True)
    for e in entries:
        note = Text(" overrides your shell's value", style="sc.warning") if envvars.overrides(e) else ""
        value = Text(envvars.shown(e), style="sc.muted" if e.secret else "")
        table.add_row(e.name, value, Text.assemble(e.origin, note))
    plain = "".join(f"{e.name}={envvars.shown(e)}\n" for e in entries)
    return Styled(table, plain)


def _env_set(ctx: ShellContext, args: list[str]) -> Any:
    from rich.text import Text

    secret = args[:1] == ["-s"]
    if secret:
        args = args[1:]
    if not args:
        raise CommandError("env set: usage: env set [-s] NAME=VALUE | NAME VALUE" + (" | NAME" if secret else ""))
    if all("=" in a for a in args):
        pairs = [tuple(a.split("=", 1)) for a in args]
    elif len(args) == 2 and "=" not in args[0]:
        pairs = [(args[0], args[1])]
    elif len(args) == 1 and secret:
        pairs = [(args[0], _secret_value(ctx, args[0]))]
    else:
        raise CommandError(f"env set: expected NAME=VALUE or NAME VALUE, got: {' '.join(args)}")
    lines = []
    for name, value in pairs:
        if not envvars.valid(name):
            raise CommandError(f"env set: '{name}': not a valid name (letters, digits and _, not first a digit)")
        entry = envvars.set_var(ctx, name, value, secret=secret)
        line = Text.assemble(("✓ ", "sc.success"), (name, "sc.path"),
                             " set for this session (modules, programs and new subshells see it)")
        if envvars.overrides(entry):
            line.append("; it overrides your shell's value", style="sc.warning")
        lines.append(line)
    mcp = getattr(ctx, "mcp_http", None)
    if mcp is not None and mcp.running():
        lines.append(Text("  the running `mcp start` server keeps its old environment: `mcp restart` passes it on",
                          style="sc.muted"))
    return Text("\n").join(lines)


def _secret_value(ctx: ShellContext, name: str) -> str:
    from shellcraft import ask

    try:
        value = ask.ask(ctx, f"{name} (hidden): ", hidden=True)
    except ask.AskError as exc:
        raise CommandError(f"env set: {exc}; or: env set -s {name} VALUE", exc.status) from None
    if not value:
        raise CommandError(f"env set: empty value; {name} unchanged")
    return value


def _env_unset(ctx: ShellContext, args: list[str]) -> Any:
    from rich.text import Text

    if not args:
        raise CommandError("env unset: usage: env unset NAME...")
    lines = []
    for name in args:
        how = envvars.unset_var(ctx, name)
        if how == "restored":
            lines.append(Text.assemble(("✓ ", "sc.success"), (name, "sc.path"), " restored to your shell's value"))
        elif how == "removed":
            lines.append(Text.assemble(("✓ ", "sc.success"), (name, "sc.path"), " removed"))
        else:
            lines.append(Text(f"{name}: not set in this session (`env` lists them)", style="sc.muted"))
    return Text("\n").join(lines)
