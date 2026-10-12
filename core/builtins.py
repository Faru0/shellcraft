"""Built-in commands. Each takes (ctx, args, stdin) and returns str or a Rich renderable."""

from __future__ import annotations

import difflib
import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from rich.console import Group
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text

from core import aliases, settings
from core.config import history_path, save_config
from core.context import CommandError, Paged, ShellExit, Styled
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
    hidden: bool = False  # left out of `help` (still runs and has a man page)
    direct: bool = False  # runs on the main thread, without a spinner (it prints as it goes)


BUILTINS: dict[str, Builtin] = {}


def builtin(name: str, summary: str, usage: str, stateful: bool = False, writes: bool = False,
            category: str = "shell", doc: str | None = None, sensitive: bool = False, hidden: bool = False,
            direct: bool = False):
    def register(fn):
        BUILTINS[name] = Builtin(fn, summary, usage, stateful, writes, category, doc, sensitive, hidden, direct)
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
    try:
        current: str | None = os.getcwd()
    except OSError:
        current = None  # the directory was deleted: there is no "previous" to come back to
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
    try:
        return os.getcwd() + "\n"
    except OSError:
        raise CommandError("pwd: the current directory no longer exists (it was deleted); try: cd ~") from None


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


HELP_DOC = """# help

List every command, grouped (Shell, Text, Files, Info, Modules), with a status line about the
modules. It is printed in full, never paged, so you can scroll back to it.

## Usage

```
help               # the table of commands
help NAME          # one command: its summary and usage (man NAME has the whole page)
help -k WORD       # the commands whose name or summary mentions WORD
```

## Options

| Option | Meaning |
| --- | --- |
| `-k WORD`, `--search WORD` | Search the summaries (`help -k dns`). |

On a narrow terminal (under 80 columns) the table becomes plain `name — summary` lines.
"""

SECTIONS = {"shell": "Shell", "text": "Text", "files": "Files", "info": "Info"}
KEYWORDS_HELP = ("for", "if", "while", "until", "case")


def _help_entries(ctx: ShellContext) -> list[tuple[str, str, str, str]]:
    """(section, name, summary, usage) for every command shown by `help`, in display order."""
    rows: list[tuple[str, str, str, str]] = []
    for category, label in SECTIONS.items():
        for name in sorted(n for n, b in BUILTINS.items() if b.category == category and not b.hidden
                           and n not in KEYWORDS_HELP):
            b = BUILTINS[name]
            rows.append((label, name, b.summary, b.usage))
    for name in ctx.registry.names():
        spec = ctx.registry.get(name)
        usage = (spec.skill.usage if spec.skill and spec.skill.usage else "") if spec else ""
        rows.append(("Modules", name, spec.summary if spec else "", usage))
    return rows


@builtin("help", "List built-ins and loaded modules", "help [NAME | -k WORD]", doc=HELP_DOC)
def _help(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    search = None
    if args and args[0] in ("-k", "--search"):
        if len(args) != 2:
            raise CommandError("usage: help -k WORD")
        search = args[1].lower()
    elif len(args) > 1 or (args and args[0].startswith("-")):
        raise CommandError("usage: help [NAME | -k WORD]")
    elif args:
        return _help_one(ctx, args[0])

    rows = _help_entries(ctx)
    if search is not None:
        rows = [r for r in rows if search in r[1].lower() or search in r[2].lower()]
        if not rows:
            raise CommandError(f"help: nothing matches '{search}' (man -k is not available; try help)")
    width = ctx.ui.console.width if ctx.ui else 100
    plain = "".join(f"{name}  {summary}\n" for _, name, summary, _ in rows)

    footer = Text()
    registry = ctx.registry
    disabled = registry.disabled_names()
    footer.append(f"{len(registry)} module{'s' if len(registry) != 1 else ''} loaded", style="sc.muted")
    if disabled:
        footer.append(f", {len(disabled)} disabled (modules -a)", style="sc.muted")
    footer.append("   keywords ", style="sc.muted")
    footer.append(", ".join(KEYWORDS_HELP), style="sc.accent")
    footer.append(" (man for)", style="sc.muted")
    for warning in registry.warnings:
        footer.append(f"\n⚠ {warning}", style="sc.warning")
    tips = Text.assemble(
        ("Pipes ", "sc.muted"), ("a | b", "sc.accent"), ("   redirect ", "sc.muted"), ("> file", "sc.accent"),
        (" / ", "sc.muted"), (">> file", "sc.accent"), ("   docs ", "sc.muted"), ("man <command>", "sc.accent"),
        ("   one command ", "sc.muted"), ("help NAME", "sc.accent"),
        ("   complete ", "sc.muted"), ("Tab", "sc.accent"),
    )

    if width < 80:  # narrow: no table, one line per command
        lines = Text()
        section = None
        for sec, name, summary, _ in rows:
            if sec != section:
                section = sec
                lines.append(f"{sec}\n", style="sc.accent bold")
            lines.append(f"  {name}", style="sc.path")
            lines.append(f" — {summary}\n")
        return Styled(Group(lines, footer, tips), plain)

    show_usage = width >= 120
    table = Table(title="ShellCraft commands" + (f": {search}" if search else ""), title_style="sc.prompt",
                  border_style="sc.border", header_style="sc.accent", expand=False)
    table.add_column("command", style="sc.path", no_wrap=True)
    table.add_column("description")
    if show_usage:
        table.add_column("usage", style="sc.muted", max_width=max(30, width // 3))
    section = None
    for i, (sec, name, summary, usage) in enumerate(rows):
        if sec != section:
            if section is not None:
                table.add_section()
            section = sec
            table.add_row(Text(sec, style="sc.accent bold"), *([""] * (2 if show_usage else 1)))
        cells = [name, summary] + ([usage] if show_usage else [])
        table.add_row(*cells)
    return Styled(Paged(Group(table, footer, tips), page=False), plain)  # printed in full, to scroll back to


def _help_one(ctx: ShellContext, name: str) -> Any:
    """`help NAME`: the one-line summary and the usage, with a pointer to the man page."""
    spec = ctx.registry.get(name)
    if spec is not None:
        summary = spec.summary or f"module {name}"
        usage = (spec.skill.usage if spec.skill and spec.skill.usage else "") or f"{name} [ARGS...]"
        kind = f"module ({spec.path.name})"
    elif name in BUILTINS:
        b = BUILTINS[name]
        summary, usage = b.summary, b.usage
        kind = "shell keyword" if name in KEYWORDS_HELP else f"builtin ({b.category})"
    elif ctx.registry.is_disabled(name):
        raise CommandError(f"help: module '{name}' is disabled (modules enable {name})")
    else:
        known = list(BUILTINS) + ctx.registry.names()
        close = difflib.get_close_matches(name, known, n=1)
        hint = f" — did you mean '{close[0]}'?" if close else ""
        raise CommandError(f"help: no such command '{name}'{hint}")
    plain = f"{name}: {summary}\nusage: {usage}\n"
    styled = Text.assemble((name, "sc.path bold"), ("  ", ""), (kind, "sc.muted"), ("\n", ""),
                           (summary, ""), ("\n", ""), ("usage: ", "sc.muted"), (usage, "sc.accent"),
                           ("\n", ""), (f"man {name}", "sc.muted"), (" has the whole page", "sc.muted"))
    return Styled(styled, plain)


MAN_DOC = """# man

Show the manual page of a builtin or module. It opens in the scrollable viewer (`/` searches,
`q` closes it), however short the page is; `-p` prints it on the screen instead.

## Usage

```
man COMMAND
man -p COMMAND
```

## Options

| Option | Meaning |
| --- | --- |
| `-p`, `--print` | Print the page on the screen instead of opening the viewer. |

Piped or redirected, the page is plain text either way, so `man grep | grep -- -i` works.
With `settings pager off`, pages are always printed.

## Examples

```
man grep
man -p echo
man fetch > fetch.txt
```
"""


@builtin("man", "Show the manual page for a command", "man [-p] COMMAND", doc=MAN_DOC)
def _man(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    print_it = any(a in ("-p", "--print") for a in args)
    names = [a for a in args if a not in ("-p", "--print")]
    if len(names) != 1 or any(n.startswith("-") for n in names):
        raise CommandError("usage: man [-p] COMMAND")
    doc = manual_text(ctx.registry, names[0])
    # Like man(1), the viewer whatever the page's height; -p prints it.
    return Paged(Markdown(doc), page=not print_it) if ctx.ui else doc


def manual_text(registry: Any, name: str) -> str:
    """The Markdown man page of a module or builtin; raises CommandError when there is none."""
    spec = registry.get(name)
    if spec is not None:
        if not spec.doc_md:
            raise CommandError(f"man: no manual entry for {name} (missing {name}.md)")
        return spec.doc_md
    if registry.is_disabled(name):
        raise CommandError(f"man: module '{name}' is disabled (modules enable {name})")
    if name in BUILTINS:
        b = BUILTINS[name]
        doc = b.doc or f"# {name}\n\n{b.summary}.\n\n## Usage\n\n```\n{b.usage}\n```\n"
        return doc + "\n*ShellCraft built-in command.*\n"
    raise CommandError(f"man: no manual entry for {name}")


THEME_DOC = """# theme

List the color themes, or switch to one. The choice is saved, so it is back next time.

## Usage

```
theme                  # the list, with a swatch of each palette (● marks the active one)
theme NAME             # switch to NAME and save it
theme preview NAME     # try NAME in this session only (not saved)
theme reset            # back to the default theme (cyberpunk), saved
```

Themes made for a **light** terminal background: `solarized-light`, `catppuccin-latte`. Your own
themes go in `~/.shellcraft/config.json` (see the README): any color left out comes from `base`,
and `light = true` picks light menu surfaces.
"""


@builtin("theme", "List color themes or switch theme", "theme [NAME | preview NAME | reset]", stateful=True,
         doc=THEME_DOC)
def _theme(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    from core.themes import DEFAULT_THEME

    warnings: list[str] = []
    themes = all_themes(ctx.config, warnings)
    if not args:
        active = ctx.ui.theme.name if ctx.ui else ctx.config.get("theme")
        table = Table(border_style="sc.border", header_style="sc.accent", title="Themes", title_style="sc.prompt")
        table.add_column("")
        table.add_column("name", style="sc.path")
        table.add_column("label")
        table.add_column("palette")
        table.add_column("for", style="sc.muted")
        for t in themes.values():
            swatch = Text()
            for color in (t.prompt, t.path, t.accent, t.success, t.warning, t.error):
                swatch.append("██", style=color)
            table.add_row("●" if t.name == active else "", t.name, t.label, swatch,
                          "light terminals" if t.light else "")
        if not warnings:
            return table
        return Group(table, Text("\n".join(f"⚠ {w}" for w in warnings), style="sc.warning"))
    preview = save = False
    if args[0] == "preview" and len(args) == 2:
        name, preview = args[1], True
    elif args == ["reset"]:
        name, save = DEFAULT_THEME, True
    elif len(args) == 1:
        name, save = args[0], True
    else:
        raise CommandError("usage: theme [NAME | preview NAME | reset]")
    if name not in themes:
        close = difflib.get_close_matches(name, list(themes), n=1)
        hint = f" — did you mean '{close[0]}'?" if close else f" (try: {', '.join(themes)})"
        raise CommandError(f"theme: unknown theme '{name}'{hint}")
    if ctx.ui is None:
        raise CommandError("theme: no interactive UI to theme")
    ctx.ui.set_theme(themes[name])
    if preview:
        return Text.assemble(("👁 ", "sc.accent"), ("previewing ", ""), (themes[name].label, "sc.accent"),
                             (f"  (theme {name} keeps it)", "sc.muted"))
    assert save
    ctx.config["theme"] = name
    try:
        save_config(ctx.config)
    except OSError as exc:
        return Text(f"theme set to {themes[name].label} (not saved: {exc})", style="sc.warning")
    return Text.assemble(("✓ ", "sc.success"), ("theme set to ", ""), (themes[name].label, "sc.accent"))


PROMPT_DOC = """# prompt

Choose what the prompt shows. The prompt has two lines: the first holds the segments, the
second the `❯` where you type. A right prompt (`-r`) sits at the right edge of the second line.

## Usage

```
prompt                      # the current segments, and every segment there is
prompt SEGMENT...           # the first line, in this order
prompt -r SEGMENT...        # the right prompt (nothing by default)
prompt reset                # back to the default
```

## Segments

| Segment | Shows |
| --- | --- |
""" + "".join(f"| `{name}` | {what} |\n" for name, what in (
    ("name", "the ⚡shellcraft label"),
    ("user", "user@host"),
    ("path", "the working directory (~ for home)"),
    ("git", "⎇ branch, when the directory is in a git repository (read from .git/HEAD, no git needed)"),
    ("mcp", "● mcp :PORT while `mcp start` runs"),
    ("status", "[✗ N] after a command fails"),
    ("duration", "⏱ 4.2s when the last command took more than 2 seconds"),
    ("clock", "the time (HH:MM)"),
    ("python", "the Python version"),
)) + """
A segment that has nothing to show (no git repository, a successful command) is left out.
With `settings mouse_always on`, the segments can be clicked: `⚡` runs `help`, the path `ls`,
`⎇ branch` `git status`, `● mcp` `mcp log`, and `[✗ N]` shows the last error again.

The choice is saved in `~/.shellcraft/config.json` under `"prompt"`. `settings transient_prompt
on` collapses the prompt to one line after Enter, so the scrollback stays dense.

## Examples

```
prompt name path git status           # no user@host on a single-user machine
prompt -r clock
prompt reset
```
"""


@builtin("prompt", "Choose the prompt's segments", "prompt [SEGMENT... | -r SEGMENT... | reset]", stateful=True,
         doc=PROMPT_DOC)
def _prompt(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    from core.shell import DEFAULT_LEFT, DEFAULT_RIGHT, SEGMENTS, prompt_segments

    if not args:
        left, right = prompt_segments(ctx.config, "left"), prompt_segments(ctx.config, "right")
        table = Table(title="Prompt segments", title_style="sc.prompt", border_style="sc.border",
                      header_style="sc.accent")
        table.add_column("segment", style="sc.path")
        table.add_column("shows")
        table.add_column("where", style="sc.muted")
        for name, what in SEGMENTS.items():
            where = "left" if name in left else "right" if name in right else ""
            table.add_row(name, what, where)
        current = Text.assemble(("prompt ", "sc.muted"), (" ".join(left), "sc.accent"))
        if right:
            current.append_text(Text.assemble(("   prompt -r ", "sc.muted"), (" ".join(right), "sc.accent")))
        hint = Text.assemble(("prompt SEGMENT...", "sc.accent"), (" sets the first line, ", "sc.muted"),
                             ("prompt -r SEGMENT...", "sc.accent"), (" the right side, ", "sc.muted"),
                             ("prompt reset", "sc.accent"), (" goes back to the default (man prompt)", "sc.muted"))
        return Styled(Group(table, current, hint),
                      f"prompt {' '.join(left)}\nprompt -r {' '.join(right)}\nprompt reset  # back to the default\n")
    if args == ["reset"]:
        ctx.config.pop("prompt", None)
        return _saved(ctx, Text.assemble(("✓ ", "sc.success"), "prompt reset to ",
                                         (" ".join(DEFAULT_LEFT), "sc.accent")))
    side = "left"
    if args[0] in ("-r", "--right"):
        side, args = "right", args[1:]
    unknown = [a for a in args if a not in SEGMENTS]
    if unknown:
        raise CommandError(f"prompt: unknown segment{'s' if len(unknown) > 1 else ''} {', '.join(unknown)} "
                           f"(choose from: {', '.join(SEGMENTS)})")
    section = ctx.config.get("prompt")
    if not isinstance(section, dict):
        section = ctx.config["prompt"] = {}
    section[side] = list(dict.fromkeys(args))
    if section.get("left") == DEFAULT_LEFT:
        section.pop("left", None)
    if section.get("right") == DEFAULT_RIGHT:
        section.pop("right", None)
    if not section:
        ctx.config.pop("prompt", None)
    shown = " ".join(args) or "(nothing)"
    return _saved(ctx, Text.assemble(("✓ ", "sc.success"), f"{'right ' if side == 'right' else ''}prompt set to ",
                                     (shown, "sc.accent")))


MODULES_DOC = """# modules

List the modules in the modules folder, and turn them on or off. A disabled module is not
imported at all: it is not a command, not an MCP tool and has no man page until you enable it
again. The choice is saved in `~/.shellcraft/config.json`, so it lasts across sessions and also
applies to `--mcp` / `--mcp-http` (`mcp restart` passes a change on to a running server).

## Usage

```
modules                      # the enabled modules (the default)
modules -a                   # every module, enabled and disabled, with its state
modules enable NAME...       # turn modules on (loads them now)
modules disable NAME...      # turn modules off
```

## Options

| Option | Meaning |
| --- | --- |
| `-a`, `--all` | Show disabled modules too, with an *on/off* column. `modules all` works as well. |

A module whose file changes is reloaded as usual (`hot_reload`), and stays disabled if it was.
Running a disabled module says so, and how to enable it. AI clients over MCP can list modules
but not enable or disable them.

## Examples

```
modules
modules -a
modules disable queryCensys queryDns
modules enable queryDns
```
"""


@builtin("modules", "List modules; enable or disable them", "modules [-a] | modules enable|disable NAME...",
         doc=MODULES_DOC)
def _modules(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    if args and args[0] in ("enable", "disable"):
        return _modules_toggle(ctx, args[0], args[1:])
    if args not in ([], ["-a"], ["--all"], ["all"]):
        raise CommandError("usage: modules [-a] | modules enable NAME... | modules disable NAME...")
    show_all = bool(args)
    registry = ctx.registry
    names = registry.all_names() if show_all else registry.names()
    disabled = registry.disabled_names()
    title = f"{'All modules' if show_all else 'Modules'} in {registry.directory}"
    table = Table(title=title, title_style="sc.prompt", border_style="sc.border", header_style="sc.accent")
    if show_all:
        table.add_column("state", justify="center")
    table.add_column("name", style="sc.path")
    table.add_column(".py", justify="center")
    table.add_column(".md", justify="center")
    table.add_column(".skill", justify="center")
    table.add_column("summary")
    ok, missing = Text("✓", style="sc.success"), Text("·", style="sc.muted")
    plain: list[str] = []
    for name in names:
        py = registry.files.get(name)
        spec = registry.get(name)
        has_md = bool(spec.doc_md) if spec else bool(py and py.with_suffix(".md").is_file())
        has_skill = bool(spec.skill) if spec else bool(py and py.with_suffix(".skill").is_file())
        summary = registry.describe(name)
        row = [name, ok, ok if has_md else missing, ok if has_skill else missing, summary]
        if show_all:
            if name in disabled:
                state = Text("○ off", style="sc.muted")
            elif spec is None:
                state = Text("⚠ error", style="sc.warning")  # enabled, but failed to load
            else:
                state = Text("● on", style="sc.success")
            row.insert(0, state)
            plain.append(f"{state.plain[2:]:<5}  {name}  {summary}".rstrip())
        else:
            plain.append(f"{name}  {summary}".rstrip())
        table.add_row(*row, style="sc.muted" if name in disabled else None)
    parts: list[Any] = [table]
    notes: list[str] = []
    if not show_all and disabled:
        notes.append(f"{len(disabled)} disabled: {', '.join(disabled)}  (modules -a shows all, "
                     f"modules enable NAME turns one on)")
        parts.append(Text(notes[-1], style="sc.muted"))
    if registry.warnings:
        warning_text = "\n".join(f"⚠ {w}" for w in registry.warnings)
        notes.append(warning_text)
        parts.append(Text(warning_text, style="sc.warning"))
    text = "".join(f"{line}\n" for line in plain + notes)
    return Styled(Group(*parts) if len(parts) > 1 else table, text)


def _modules_toggle(ctx: ShellContext, action: str, names: list[str]) -> Any:
    if not ctx.allow_stateful:
        raise CommandError(f"modules {action} is not available in this context")
    if not names:
        raise CommandError(f"usage: modules {action} NAME...")
    registry = ctx.registry
    unknown = [n for n in names if n not in registry.files]
    if unknown:
        hints = []
        for name in unknown:
            if name in BUILTINS:
                hints.append(f"{name} is a builtin, not a module")
            else:
                close = difflib.get_close_matches(name, registry.all_names(), n=1)
                hints.append(f"no module '{name}'" + (f" — did you mean '{close[0]}'?" if close else ""))
        raise CommandError(f"modules {action}: {'; '.join(hints)}")

    msg = Text()
    changed: list[str] = []
    failed: list[str] = []
    for name in dict.fromkeys(names):  # each once, in the order given
        if action == "disable":
            if registry.is_disabled(name):
                msg.append(f"• {name} is already disabled\n", style="sc.muted")
                continue
            registry.disable(name)
            changed.append(name)
        else:
            was_disabled = registry.is_disabled(name)
            if not was_disabled and registry.get(name) is not None:
                msg.append(f"• {name} is already enabled\n", style="sc.muted")
                continue
            try:
                registry.enable(name)
            except Exception as exc:  # noqa: BLE001 — enabled, but it doesn't import: say why
                failed.append(f"{name}: {type(exc).__name__}: {exc}")
            else:
                if not was_disabled:  # it was on but had failed to load: this was a retry
                    msg.append(f"✓ {name} loaded\n", style="sc.success")
            if was_disabled:
                changed.append(name)
    ctx.config["disabled_modules"] = registry.disabled_names()
    if changed:
        mark = ("✓ ", "sc.success") if action == "enable" else ("○ ", "sc.muted")
        msg.append_text(Text.assemble(mark, f"{action}d ", (", ".join(changed), "sc.accent")))
    for line in failed:
        msg.append(f"\n⚠ enabled, but it failed to load — {line}", style="sc.warning")
    msg.rstrip()
    if changed:
        return _saved(ctx, msg)
    return msg


BANNER_DOC = """# banner

Show the ShellCraft welcome banner (the one printed when the shell starts) again, with the
version, theme, module count and any module warnings, in the current theme.

## Usage

```
banner
banner -c
```

## Options

| Option | Meaning |
| --- | --- |
| `-c`, `--clear` | Clear the screen first, like a fresh login. |

`settings banner off` only stops it at startup; the `banner` command always shows it.
"""


@builtin("banner", "Show the ShellCraft welcome banner", "banner [-c]", stateful=True, doc=BANNER_DOC)
def _banner(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    from core.banner import render_banner

    if any(a not in ("-c", "--clear") for a in args):
        raise CommandError("usage: banner [-c]")
    if ctx.ui is None:
        raise CommandError("banner: needs the interactive shell")
    if args:
        ctx.ui.console.clear()
    return Paged(render_banner(ctx.ui, ctx.registry), page=False)


FOR_DOC = """# for

Run commands once for each item of a list. Two syntaxes: **bash** (the default), and
**`-py`**, ShellCraft's own, where nothing needs quoting and conditions are Python.

## Bash syntax

```
for NAME in WORD...; do COMMANDS; done
```

`;` and new lines separate commands. Expansion works as in bash:

| In the list | Gives |
| --- | --- |
| `a b "c d"` | Each word (quotes keep spaces in one item). |
| `{1..5}`, `{5..1}`, `{0..100..10}`, `{01..10}`, `{a..e}`, `x{a,b}` | Brace expansion. |
| `*.log`, `src/*.py` | Matching file names, sorted (the word itself when nothing matches). |
| `$(COMMAND)` | The command's output, split into words at whitespace (`"$(COMMAND)"`: one item). |
| `$x` | An outer loop variable, split at whitespace unless quoted (`"$x"`). |

In the body, an unquoted `$x` or `$(…)` is split into words at whitespace, as in bash: write
`"$x"` to keep it one argument. `$?` is the last exit status. Conditions use `if …; then …; fi`
(see `man if`), `&&`, `||` and `!`.

## -py syntax

```
for -py NAME in ITEM... { COMMANDS }
```

Parsed by ShellCraft: `$NAME` is always **one** argument, whatever it holds (spaces, quotes,
`;`), so nothing needs quoting or escaping. Items: words, `1..5` / `5..1` / `0..100..10`
ranges, globs, and `(COMMAND)` (one item per non-empty output line). Conditions inside are
`if (python-expression) { … }` or `if COMMAND { … }` (see `man if`). Inside a `-py` block,
nested `for` and `if` use this syntax too. Braces must be words of their own: `{ echo $x }`.

## Both

- `break` leaves the loop, `continue` goes on to the next item.
- A command that fails shows its error and the loop goes on (`$?` is set), as in bash.
- Redirect inside the body to collect output: `… >> out.txt`. A whole loop can't be piped or
  redirected yet (`done | sort`).
- A line that ends inside a loop (no `done` / `}` yet) asks for more lines (`┆ …`); the whole
  loop is saved to the history as one line. Ctrl-C cancels it, or stops it while it runs.
- Environment variables (`$HOME`) are not expanded, and variables can't be assigned (`x=1`);
  `$( … )` and word splitting apply inside bash-syntax loops and ifs, not on plain lines.

## Examples

```
for ip in 1.1.1.1 8.8.8.8; do ip2geo $ip; done
for h in $(cat hosts.txt); do queryDns $h >> dns.txt; done
for n in {1..5}; do echo $n; done
for f in *.log; do if grep -q ERROR "$f"; then echo "$f"; fi; done
for -py ip in (cat ips.txt) { if (ip.startswith("10.")) { echo internal $ip } else { myip $ip } }
```
"""

IF_DOC = r"""# if

Run commands when a condition holds, in a loop or on its own. Two syntaxes, as for `for`.

## Bash syntax

```
if COMMANDS; then COMMANDS; [elif COMMANDS; then COMMANDS;] [else COMMANDS;] fi
```

The condition is commands: true when the last one's exit status is 0. Usual conditions:
`[ … ]`, `[[ … ]]` and `test` (see `man test`), `grep -q`, `true` / `false`, or any command.
`! cmd` negates; `a && b` and `a || b` work anywhere.

```
if [ "$n" -gt 3 ]; then echo big; fi
if [[ $host == *.gov ]]; then queryDns $host; else echo skip; fi
if ! grep -q TODO "$f"; then echo "$f is done"; fi
[ -f hosts.txt ] && echo found || echo missing
```

## -py syntax

```
if -py CONDITION { … } [elif CONDITION { … }] [else { … }]
```

`elif` and `else` follow the closing `}`. A CONDITION is a command (true when it succeeds;
`!` then a space negates it), or a **Python expression in parentheses**: loop variables are
plain names (or `$name`), `status` (or `$?`) is the last exit status as a number. Loop
variables are text: use `int(n)` for numbers.

```
if -py (ip.startswith("10.") and not ip.endswith(".1")) { … }
if -py (int(n) % 2 == 0) { … }
if -py (host in ("a.com", "b.com")) { … }
if -py ($? != 0) { echo "failed" }
if -py (match(r"^\d+\.\d+", ver)) { … }
```

Expressions are checked before anything runs and evaluated without `eval`: comparisons,
`and` / `or` / `not`, `in`, arithmetic (`+ - * / // %`), indexing and slicing, `x if c else y`,
these string methods: `startswith endswith lower upper casefold title strip lstrip rstrip split
rsplit splitlines removeprefix removesuffix replace count find rfind isdigit isnumeric isdecimal
isalpha isalnum isspace islower isupper`, and these functions: `len int float str bool abs min
max`, `match(PATTERN, TEXT)` (a regex search), `exists(PATH)`, `isfile(PATH)`, `isdir(PATH)`.
Imports, other attributes, other calls and names starting with `_` are refused. A condition
that fails to evaluate (an unknown name, comparing text with a number) stops the whole script.

Inside a `-py` block, a nested `if` uses this syntax without the flag.
"""


def _keyword(name: str) -> Callable[[ShellContext, list[str], str], Any]:
    def run(ctx: ShellContext, args: list[str], stdin: str) -> Any:
        raise CommandError(f"{name}: a shell keyword, not a command here (see: man {name})")
    return run


builtin("for", "Loop over items (bash syntax, or -py)",
        "for NAME in WORD...; do …; done | for -py NAME in ITEM... { … }", doc=FOR_DOC)(_keyword("for"))
builtin("if", "Run commands when a condition holds (bash syntax, or -py)",
        "if …; then …; [elif …; then …;] [else …;] fi | if -py (EXPR) | COMMAND { … }",
        doc=IF_DOC)(_keyword("if"))


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

An alias is a single command: it can't contain `|`, `>` or `>>`. Aliases apply in the shell,
never to AI clients over MCP. `which NAME` shows whether a name is an alias.

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
    """Saved commands, oldest first: the live REPL history, or the history file without one."""
    if ctx.history is not None:
        return list(ctx.history.get_strings())
    from prompt_toolkit.history import FileHistory

    path = history_path()
    if not path.is_file():
        return []
    return list(reversed(list(FileHistory(str(path)).load_history_strings())))


MCP_DOC = """# mcp

Run ShellCraft's MCP server over HTTP in the background while you keep using the shell, so an AI
client (Claude Code, Claude Desktop…) can call your modules. It is the same server as
`python main.py --mcp-http HOST:PORT`, as a child process of this shell: Ctrl-C at the prompt
doesn't stop it, and it stops when the shell exits. While it runs, the prompt shows
`● mcp :38765`; a red `✗ mcp :38765` means it exited on its own (`mcp log` says why).

## Usage

```
mcp                          # status: running or not, address, pid, uptime
mcp start [HOST:PORT]        # start it (default 127.0.0.1:38765)
mcp start --allow-system     # ... and let clients run OS commands
mcp stop                     # stop it
mcp restart                  # stop and start again at the same address
mcp log [N]                  # the last N lines of its log (default 20)
```

## Options

| Option | Meaning |
| --- | --- |
| `--allow-system` | Let MCP clients fall back to OS programs (off by default, like `--mcp-http`). |

The server only listens on this machine (`127.0.0.1`, `localhost` or `::1`), and every request
must carry a **bearer token** that `mcp start` generates and prints (`mcp` shows it again), so
other local users and processes can't use it. Its log is `~/.shellcraft/mcp-http.log`. API keys
set with `settings NAME` before `mcp start` are passed to it; set a key later and `mcp restart`
to pass it on. Point a client at the URL and token `mcp start` prints, for example:
`claude mcp add --transport http shellcraft http://127.0.0.1:38765/mcp --header "Authorization: Bearer TOKEN"`.
The server exits by itself when the shell that started it is gone, even after `kill -9`.

## Examples

```
mcp start
mcp start 127.0.0.1:9000
mcp log 50
mcp stop
```
"""


def _uptime(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def _mcp_running(ctx: ShellContext) -> Any:
    """The server started with `mcp start`, or None; forgets one that has exited by itself."""
    child = ctx.mcp_http
    if child is not None and not child.running():
        child.stop()
        ctx.mcp_http = None
        return None
    return child


@builtin("mcp", "Start or stop the MCP HTTP server in the background",
         "mcp [start [HOST:PORT] | stop | restart | log [N]]", stateful=True, doc=MCP_DOC)
def _mcp(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    from core import mcp_child

    action, rest = (args[0], args[1:]) if args else ("status", [])
    child = _mcp_running(ctx)

    if action == "status" and not rest:
        if child is None:
            return Text.assemble(("○ ", "sc.muted"), "MCP server not running",
                                 ("  (start it with: mcp start [HOST:PORT])", "sc.muted"))
        return Text.assemble(("● ", "sc.success"), "MCP server running at ", (child.url, "sc.accent"),
                             (f"  pid {child.pid}, up {_uptime(time.time() - child.started)}"
                              f"{', OS commands allowed' if child.allow_system else ''}\n", "sc.muted"),
                             ("  token ", "sc.muted"), (child.token, "sc.accent"),
                             ("  (header: Authorization: Bearer …)", "sc.muted"))

    if action in ("start", "restart"):
        allow_system = "--allow-system" in rest
        addresses = [a for a in rest if a != "--allow-system"]
        if len(addresses) > 1 or any(a.startswith("-") for a in addresses) or (action == "restart" and addresses):
            raise CommandError("usage: mcp start [HOST:PORT] [--allow-system] | mcp restart")
        token = None
        if action == "restart":
            if child is None:
                raise CommandError("mcp: the MCP server isn't running (start it with: mcp start)")
            address = f"{mcp_child._url_host(child.host)}:{child.port}"
            allow_system = allow_system or child.allow_system
            token = child.token  # clients keep working after a restart
            child.stop()
            ctx.mcp_http = None
        elif child is not None:
            raise CommandError(f"mcp: the MCP server is already running at {child.url} (mcp stop, or mcp restart)")
        else:
            address = addresses[0] if addresses else mcp_child.DEFAULT_ADDRESS
        def track(spawned: Any) -> None:
            ctx.mcp_http = spawned

        try:
            child = ctx.runner("starting the MCP server…",
                               lambda: mcp_child.start(address, ctx.registry.directory, allow_system, track, token))
        except Exception as exc:  # StartError; AddressError from parse_http_address; OSError from Popen
            ctx.mcp_http = None  # a failed start has already stopped its process
            raise CommandError(f"mcp: {exc}") from None
        ctx.mcp_http = child
        return Text.assemble(("● ", "sc.success"), f"MCP server {action}ed at ", (child.url, "sc.accent"),
                             (f"  pid {child.pid}{', OS commands allowed' if allow_system else ''}"
                              f" · log: {mcp_child.log_path()}\n", "sc.muted"),
                             ("  token ", "sc.muted"), (child.token, "sc.accent"),
                             ("   add it to a client with:\n  ", "sc.muted"),
                             (f"claude mcp add --transport http shellcraft {child.url} "
                              f"--header \"Authorization: Bearer {child.token}\"", "sc.path"))

    if action == "stop" and not rest:
        if child is None:
            raise CommandError("mcp: the MCP server isn't running")
        code = child.stop()
        ctx.mcp_http = None
        return Text.assemble(("■ ", "sc.muted"), "MCP server stopped", (f"  (exit code {code})", "sc.muted"))

    if action == "log" and len(rest) <= 1 and all(r.isdigit() for r in rest):
        tail = mcp_child.log_tail(int(rest[0]) if rest else 20)
        return (tail + "\n") if tail else Text("(the MCP server's log is empty)", style="sc.muted")

    raise CommandError("usage: mcp [start [HOST:PORT] [--allow-system] | stop | restart | log [N]]")


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
(the shell and `--mcp` alike) and replaces a value from your shell. `settings reset NAME`
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
    """Read a value without echoing it; only possible in the interactive shell."""
    label = f"{entry.label} ({entry.name}): "
    try:
        if ctx.interactive:
            from prompt_toolkit import prompt

            return prompt(label, is_password=True)
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
