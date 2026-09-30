"""Built-in commands. Each takes (ctx, args, stdin) and returns str or a Rich renderable."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from rich.console import Group
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text

from core import aliases, settings
from core.config import history_path, save_config
from core.context import CommandError, ShellExit, Styled
from core.themes import all_themes

if TYPE_CHECKING:
    from core.context import ShellContext


CATEGORIES = ("shell", "text", "files", "info")


@dataclass(frozen=True)
class Builtin:
    fn: Callable[[ShellContext, list[str], str], Any]
    summary: str
    usage: str
    stateful: bool = False  # changes shell state; disabled for MCP pipelines
    writes: bool = False  # changes the filesystem; disabled for MCP pipelines
    category: str = "shell"
    doc: str | None = None  # Markdown manual shown by `man`
    sensitive: bool = False  # may reveal secrets (e.g. env vars); disabled for MCP pipelines


BUILTINS: dict[str, Builtin] = {}


def builtin(name: str, summary: str, usage: str, stateful: bool = False, writes: bool = False,
            category: str = "shell", doc: str | None = None, sensitive: bool = False):
    def register(fn):
        BUILTINS[name] = Builtin(fn, summary, usage, stateful, writes, category, doc, sensitive)
        return fn
    return register


@builtin("cd", "Change the working directory", "cd [DIR | - | ~]", stateful=True)
def _cd(ctx: ShellContext, args: list[str], stdin: str) -> str:
    if len(args) > 1:
        raise CommandError("cd: too many arguments")
    target = args[0] if args else os.path.expanduser("~")
    if target == "-":
        if not ctx.prev_dir:
            raise CommandError("cd: no previous directory")
        target = ctx.prev_dir
    target = os.path.expanduser(target)
    current = os.getcwd()
    try:
        os.chdir(target)
    except FileNotFoundError:
        raise CommandError(f"cd: no such directory: {target}") from None
    except NotADirectoryError:
        raise CommandError(f"cd: not a directory: {target}") from None
    except PermissionError:
        raise CommandError(f"cd: permission denied: {target}") from None
    ctx.prev_dir = current
    return ""


@builtin("pwd", "Print the working directory", "pwd")
def _pwd(ctx: ShellContext, args: list[str], stdin: str) -> str:
    return os.getcwd() + "\n"


@builtin("exit", "Leave ShellCraft", "exit [CODE]", stateful=True)
def _exit(ctx: ShellContext, args: list[str], stdin: str) -> str:
    try:
        code = int(args[0]) if args else 0
    except ValueError:
        raise CommandError(f"exit: numeric argument required: {args[0]}") from None
    raise ShellExit(code)


@builtin("clear", "Clear the screen", "clear", stateful=True)
def _clear(ctx: ShellContext, args: list[str], stdin: str) -> str:
    if ctx.ui:
        ctx.ui.console.clear()
    return ""


@builtin("help", "List built-ins and loaded modules", "help")
def _help(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    table = Table(title="ShellCraft commands", title_style="sc.prompt", border_style="sc.border",
                  header_style="sc.accent", expand=False)
    table.add_column("command", style="sc.path", no_wrap=True)
    table.add_column("kind", style="sc.muted")
    table.add_column("description")
    for category in CATEGORIES:
        names = sorted(n for n, b in BUILTINS.items() if b.category == category)
        for i, name in enumerate(names):
            table.add_row(name, category, BUILTINS[name].summary, end_section=i == len(names) - 1)
    for name in ctx.registry.names():
        table.add_row(name, "module", ctx.registry.get(name).summary)
    tips = Text.assemble(
        ("Pipes ", "sc.muted"), ("a | b", "sc.accent"), ("   redirect ", "sc.muted"), ("> file", "sc.accent"),
        (" / ", "sc.muted"), (">> file", "sc.accent"), ("   docs ", "sc.muted"), ("man <command>", "sc.accent"),
        ("   complete ", "sc.muted"), ("Tab", "sc.accent"), ("   accept suggestion ", "sc.muted"), ("→", "sc.accent"),
    )
    return Group(table, tips)


@builtin("man", "Show the manual page for a command", "man COMMAND")
def _man(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    if len(args) != 1:
        raise CommandError("usage: man COMMAND")
    doc = manual_text(ctx.registry, args[0])
    return Markdown(doc) if ctx.ui else doc


def manual_text(registry: Any, name: str) -> str:
    """The Markdown man page of a module or builtin; raises CommandError when there is none."""
    spec = registry.get(name)
    if spec is not None:
        if not spec.doc_md:
            raise CommandError(f"man: no manual entry for {name} (missing {name}.md)")
        return spec.doc_md
    if name in BUILTINS:
        b = BUILTINS[name]
        doc = b.doc or f"# {name}\n\n{b.summary}.\n\n## Usage\n\n```\n{b.usage}\n```\n"
        return doc + "\n*ShellCraft built-in command.*\n"
    raise CommandError(f"man: no manual entry for {name}")


@builtin("theme", "List color themes or switch theme", "theme [NAME]", stateful=True)
def _theme(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    themes = all_themes(ctx.config)
    if not args:
        active = ctx.ui.theme.name if ctx.ui else ctx.config.get("theme")
        table = Table(border_style="sc.border", header_style="sc.accent", title="Themes", title_style="sc.prompt")
        table.add_column("")
        table.add_column("name", style="sc.path")
        table.add_column("label")
        table.add_column("palette")
        for t in themes.values():
            swatch = Text()
            for color in (t.prompt, t.path, t.accent, t.success, t.warning, t.error):
                swatch.append("██", style=color)
            table.add_row("●" if t.name == active else "", t.name, t.label, swatch)
        return table
    name = args[0]
    if name not in themes:
        raise CommandError(f"theme: unknown theme '{name}' (try: {', '.join(themes)})")
    if ctx.ui is None:
        raise CommandError("theme: no interactive UI to theme")
    ctx.ui.set_theme(themes[name])
    ctx.config["theme"] = name
    try:
        save_config(ctx.config)
    except OSError as exc:
        return Text(f"theme set to {themes[name].label} (not saved: {exc})", style="sc.warning")
    return Text.assemble(("✓ ", "sc.success"), ("theme set to ", ""), (themes[name].label, "sc.accent"))


@builtin("modules", "List loaded modules and their files", "modules")
def _modules(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    table = Table(title=f"Modules in {ctx.registry.directory}", title_style="sc.prompt",
                  border_style="sc.border", header_style="sc.accent")
    table.add_column("name", style="sc.path")
    table.add_column(".py", justify="center")
    table.add_column(".md", justify="center")
    table.add_column(".skill", justify="center")
    table.add_column("summary")
    ok, missing = Text("✓", style="sc.success"), Text("·", style="sc.muted")
    for name in ctx.registry.names():
        spec = ctx.registry.get(name)
        table.add_row(name, ok, ok if spec.doc_md else missing, ok if spec.skill else missing, spec.summary)
    if not ctx.registry.warnings:
        return table
    warnings = Text("\n".join(f"⚠ {w}" for w in ctx.registry.warnings), style="sc.warning")
    return Group(table, warnings)


@builtin("reload", "Re-scan the modules directory", "reload", stateful=True)
def _reload(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    ctx.registry.load()
    msg = Text.assemble(("✓ ", "sc.success"), f"loaded {len(ctx.registry)} module(s)")
    for w in ctx.registry.warnings:
        msg.append(f"\n⚠ {w}", style="sc.warning")
    return msg


ALIAS_DOC = """# alias

Give a command a shorter name, or change what a command does by default. Aliases are saved in
`~/.shellcraft/config.json`, so they last across sessions.

## Usage

```
alias                      # list every alias
alias NAME                 # show one alias
alias NAME='COMMAND ARGS'  # define (or replace) an alias
unalias NAME...            # remove aliases
unalias -a                 # remove every alias
```

An alias replaces the first word of a command; anything you type after it is added at the end.
With `alias ls='ls -a -l'`, typing `ls -r src` runs `ls -a -l -r src`. It works in every step of
a pipeline (`ls | grep py`). An alias may use its own name, as above, without looping.

Put a backslash in front of a command to skip its alias: `\\ls` runs the plain `ls`.

An alias is a single command: it can't contain `|`, `>` or `>>`. Aliases apply in the shell and
with `-c`, never to AI clients over MCP. `which NAME` shows whether a name is an alias.

## Examples

```
alias ls='ls -a -l'
alias ll='ls -l'
alias errors='grep -i error'
fetch app.log | errors | wc -l
unalias ll
```
"""


@builtin("alias", "Define or list command aliases", "alias [NAME[=COMMAND]]", stateful=True, doc=ALIAS_DOC)
def _alias(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    defined = aliases.get_all(ctx.config)
    if not args:
        return "".join(f"alias {n}={_quote(v)}\n" for n, v in sorted(defined.items()))
    if len(args) == 1 and "=" not in args[0]:
        name = args[0]
        if name not in defined:
            raise CommandError(f"alias: {name}: not found")
        return f"alias {name}={_quote(defined[name])}\n"
    # alias NAME='ls -l' arrives as one word "NAME=ls -l"; also accept alias NAME = ls -l / NAME=ls -l
    words = " ".join(args)
    name, sep, value = words.partition("=")
    name, value = name.strip(), value.strip()
    if not sep:
        raise CommandError("alias: usage: alias NAME='COMMAND ARGS'")
    try:
        aliases.set_alias(ctx.config, name, value)
    except aliases.AliasError as exc:
        raise CommandError(str(exc)) from None
    return _saved(ctx, Text.assemble(("✓ ", "sc.success"), ("alias ", ""), (name, "sc.accent"),
                                     (f" → {value}", "")))


@builtin("unalias", "Remove command aliases", "unalias NAME... | unalias -a", stateful=True, doc=ALIAS_DOC)
def _unalias(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    if not args:
        raise CommandError("unalias: usage: unalias NAME... | unalias -a")
    if args == ["-a"]:
        count = len(aliases.get_all(ctx.config))
        ctx.config["aliases"] = {}
        return _saved(ctx, Text.assemble(("✓ ", "sc.success"), f"removed {count} alias(es)"))
    missing = [n for n in args if not aliases.remove(ctx.config, n)]
    removed = [n for n in args if n not in missing]
    if removed:
        msg = _saved(ctx, Text.assemble(("✓ ", "sc.success"), f"removed {', '.join(removed)}"))
    if missing:
        raise CommandError(f"unalias: {', '.join(missing)}: not found")
    return msg


def _quote(value: str) -> str:
    """Quote an alias value so the line can be pasted back into the shell."""
    if "'" not in value:
        return f"'{value}'"
    return '"' + value.replace('"', '\\"') + '"'


def _saved(ctx: ShellContext, msg: Text) -> Text:
    try:
        save_config(ctx.config)
    except OSError as exc:
        msg.append(f" (not saved: {exc})", style="sc.warning")
    return msg


HISTORY_DOC = """# history

Show the commands you typed, oldest first and numbered, like bash's `history`. It is the same
history that ↑ and the ghost-text suggestions use, kept in `~/.shellcraft/history`.

## Usage

```
history          # every saved command
history N        # the last N commands
history -c       # clear the history (the file and this session's ↑ list)
```

Piped output is plain `  N  command` lines, so `history | grep ssh` finds old commands. Lines you
typed with a leading space were never saved, and API keys typed as `settings NAME VALUE` are
saved as `••••`.

Because history can contain secrets, AI clients can't use `history` over MCP.

## Examples

```
history 20
history | grep -i docker
history | tail -5
```
"""


@builtin("history", "Show or clear the command history", "history [N] | history -c", stateful=True,
         sensitive=True, doc=HISTORY_DOC)
def _history(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    if args == ["-c"]:
        if ctx.history is not None and hasattr(ctx.history, "_loaded_strings"):
            ctx.history._loaded_strings = []  # prompt_toolkit keeps ↑ entries in memory
        try:
            history_path().write_text("", encoding="utf-8")
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise CommandError(f"history: cannot clear {history_path()}: {exc.strerror or exc}") from None
        return Text.assemble(("✓ ", "sc.success"), "history cleared")
    if len(args) > 1 or (args and not args[0].isdigit()):
        raise CommandError("history: usage: history [N] | history -c")
    entries = _history_entries(ctx)
    start = 0
    if args:
        start = max(0, len(entries) - int(args[0]))
    width = len(str(len(entries)))
    plain, styled = [], Text()
    for number, command in enumerate(entries[start:], start=start + 1):
        command = command.replace("\n", " ")
        plain.append(f"{number:>{width + 2}}  {command}")
        styled.append(f"{number:>{width + 2}}  ", style="sc.muted")
        styled.append(command + "\n")
    styled.rstrip()
    return Styled(styled, "".join(f"{line}\n" for line in plain))


def _history_entries(ctx: ShellContext) -> list[str]:
    """Saved commands, oldest first: the live REPL history, or the history file (for -c mode)."""
    if ctx.history is not None:
        return list(ctx.history.get_strings())
    from prompt_toolkit.history import FileHistory

    path = history_path()
    if not path.is_file():
        return []
    return list(reversed(list(FileHistory(str(path)).load_history_strings())))


SETTINGS_DOC = """# settings

Show and change ShellCraft's settings: on/off switches, and the API keys that modules need.
Changes apply immediately and are saved to `~/.shellcraft/config.json`.

## Usage

```
settings                      # table of all settings and API keys
settings KEY on|off|toggle    # change an on/off setting
settings reset KEY            # back to the default
settings NAME                 # enter an API key (typing is hidden)
settings NAME VALUE           # set an API key in one go
settings reset NAME           # forget a stored API key
```

## Settings

| Key | Default | Meaning |
| --- | --- | --- |
""" + "".join(
    f"| `{s.key}` | {'on' if s.default else 'off'} | {s.description} |\n" for s in settings.SETTINGS.values()
) + """
## API keys

Modules that call paid or registered APIs declare the environment variables they need, and
`settings` lists them after the on/off settings as *module · label*. The value is never shown:
the table says `set ••••ab12` (stored by ShellCraft), `from environment` (exported by your own
shell) or `not set`. A stored key is exported to the environment when ShellCraft starts
(interactive, `-c` and `--mcp` alike) and replaces a value from your shell. `settings reset NAME`
forgets the stored key and brings back your shell's value, if it had one.

Keys are kept in plain text in `config.json`, which is made readable by you only (mode 600).
`settings NAME VALUE` is saved to history as `settings NAME ••••`, but prefer `settings NAME`,
which asks for the value without echoing it.

## Examples

```
settings system_commands on   # allow git, python, uname… from your PATH
settings pager off
settings DNSDUMPSTER_API_KEY  # prompts for the key
settings reset CENSYS_ORG_ID
```
"""


@builtin("settings", "Show or change settings and API keys",
         "settings [KEY on|off|toggle] | settings NAME [VALUE] | settings reset KEY",
         stateful=True, doc=SETTINGS_DOC)
def _settings(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    keys = settings.env_settings(ctx.registry)
    if not args:
        return _settings_table(ctx, keys)

    if args[0] == "reset":
        if len(args) != 2:
            raise CommandError("usage: settings reset KEY")
        if args[1] in keys:
            settings.env_reset(ctx.config, args[1])
            return _store_env(ctx, keys[args[1]], "cleared", None)
        key = _setting_key(args[1], keys)
        settings.reset(ctx.config, key)
        return _store_setting(ctx, key, settings.get(ctx.config, key), "reset to")

    if args[0] in keys:
        entry = keys[args[0]]
        if len(args) > 2:
            raise CommandError(f"usage: settings {entry.name} [VALUE]  (quote a value that contains spaces)")
        value = args[1] if len(args) == 2 else _ask_secret(ctx, entry)
        if not value.strip():
            raise CommandError(f"settings: empty value; {entry.name} unchanged "
                               f"(use `settings reset {entry.name}` to clear it)")
        settings.env_set(ctx.config, entry.name, value.strip())
        return _store_env(ctx, entry, "set", value.strip())

    if len(args) != 2:
        key = _setting_key(args[0], keys)
        raise CommandError(f"usage: settings {key} on|off|toggle")
    key = _setting_key(args[0], keys)
    if args[1].lower() == "toggle":
        value = not settings.get(ctx.config, key)
    else:
        value = settings.parse_bool(args[1])
        if value is None:
            raise CommandError(f"settings: expected on, off or toggle, got '{args[1]}'")
    settings.set_value(ctx.config, key, value)
    return _store_setting(ctx, key, value, "set to")


def _settings_table(ctx: ShellContext, keys: dict[str, settings.EnvEntry]) -> Table:
    table = Table(title="Settings", title_style="sc.prompt", border_style="sc.border", header_style="sc.accent")
    table.add_column("key", style="sc.path", no_wrap=True)
    table.add_column("setting")
    table.add_column("value", justify="center")
    table.add_column("description", style="sc.muted")
    rows = list(settings.SETTINGS.values())
    for i, s in enumerate(rows):
        on = settings.get(ctx.config, s.key)
        value = Text("On", style="sc.success") if on else Text("Off", style="sc.error")
        table.add_row(s.key, s.label, value, s.description, end_section=bool(keys) and i == len(rows) - 1)
    styles = {"set": "sc.success", "environment": "sc.accent", "unset": "sc.muted"}
    for name in sorted(keys, key=lambda n: (keys[n].module.lower(), n)):
        entry = keys[name]
        state, text = settings.env_status(ctx.config, name)
        table.add_row(name, entry.label, Text(text, style=styles[state]), entry.setting.description)
    return table


def _setting_key(key: str, keys: dict[str, settings.EnvEntry] | None = None) -> str:
    if key not in settings.SETTINGS:
        names = list(settings.SETTINGS) + sorted(keys or ())
        raise CommandError(f"settings: unknown setting '{key}' (try: {', '.join(names)})")
    return key


def _ask_secret(ctx: ShellContext, entry: settings.EnvEntry) -> str:
    """Read a value without echoing it; only possible in an interactive terminal."""
    label = f"{entry.label} ({entry.name}): "
    try:
        if ctx.interactive:
            from prompt_toolkit import prompt

            return prompt(label, is_password=True)
        from core.stdio import is_console

        if is_console(sys.stdin):  # not isatty(): on Windows that is True for NUL too
            import getpass

            return getpass.getpass(label)
    except (EOFError, KeyboardInterrupt):
        raise CommandError(f"settings: cancelled; {entry.name} unchanged") from None
    raise CommandError(f"settings: no terminal to ask for {entry.name}; use: settings {entry.name} VALUE")


def _store_env(ctx: ShellContext, entry: settings.EnvEntry, verb: str, value: str | None) -> Text:
    msg = Text.assemble(("✓ ", "sc.success"), f"{entry.label} {verb}")
    if value is not None:
        msg.append(f" ({settings.mask(value)})", style="sc.muted")
    elif os.environ.get(entry.name):
        msg.append(" (your environment's value is used again)", style="sc.muted")
    try:
        save_config(ctx.config)
    except OSError as exc:
        msg.append(f"  (not saved: {exc})", style="sc.warning")
    return msg


def _store_setting(ctx: ShellContext, key: str, value: bool, verb: str) -> Text:
    setting = settings.SETTINGS[key]
    if setting.apply:
        setting.apply(ctx, value)
    msg = Text.assemble(("✓ ", "sc.success"), f"{setting.label} {verb} ",
                        ("On", "sc.success") if value else ("Off", "sc.error"))
    try:
        save_config(ctx.config)
    except OSError as exc:
        msg.append(f"  (not saved: {exc})", style="sc.warning")
    return msg
