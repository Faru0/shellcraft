"""Built-in commands. Each takes (ctx, args, stdin) and returns str or a Rich renderable."""

from __future__ import annotations

import difflib
import os
import re
import shutil
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from rich.console import Group
from rich.table import Table
from rich.text import Text

from shellcraft import aliases, envvars, prefix, profiles, settings
from shellcraft import editor as editormod
from shellcraft.config import save_config
from shellcraft.context import CommandError, Paged, ShellExit, Styled, WithStatus, to_text
from shellcraft.themes import all_themes

if TYPE_CHECKING:
    from shellcraft.context import ShellContext


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
    streams: bool = False  # gets stdin as shellcraft.streams.Lines (lines as they arrive), not a str
    requires: str | None = None  # a setting that must be on: until then the command is hidden and not found


def available(b: Builtin, config: dict[str, Any]) -> bool:
    """Whether `b` is offered now: a command behind a setting (`diag`) only while that setting is on."""
    if b.requires is None:
        return True
    from shellcraft import settings

    return settings.get(config, b.requires)


BUILTINS: dict[str, Builtin] = {}


def builtin(name: str, summary: str, usage: str, stateful: bool = False, writes: bool = False,
            category: str = "shell", doc: str | None = None, sensitive: bool = False, hidden: bool = False,
            direct: bool = False, streams: bool = False, requires: str | None = None):
    def register(fn):
        BUILTINS[name] = Builtin(fn, summary, usage, stateful, writes, category, doc, sensitive, hidden, direct,
                                 streams, requires)
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


DIAG_DOC = """# diag

`diag` is hidden until the `diagnostics` setting is on: start ShellCraft once with `--diag`
(`python main.py --diag`, which turns it on and prints the report at startup) or run
`settings diagnostics on`. `settings diagnostics off` hides it again.

Print what ShellCraft detected about this terminal, as plain text to copy into a bug report: the
Rich and prompt_toolkit color depth and size sources, on Windows the CONOUT$ modes, and a
**footer colors** section: the escape sequence the footer writes for each state (running, done,
failed, the recording bubble, the alert marks), whether running / done / failed look different in
this theme, the footer's rows as last drawn, and the live state of every job (running, status,
and for a Windows pseudo-terminal whether pywinpty says the program is alive).

## Usage

```
diag          # the whole report
```

Run it with a subshell running (`sub pwsh`), and again after it has ended, when the footer line
should have turned green or red and does not. `shellcraft --diag` prints the same report at start.

## The key test

`diag keys` shows what each key press reaches the shell as, until Enter, then prints the list.
Alt-v pastes when it shows as `escape v` (or `escape c-v`, which is Ctrl+Alt+v: AltGr+v on
Windows). On Windows each line also shows the console's raw flags (`left-alt`, `right-alt`,
`left-ctrl`, `shift`). Try the left Alt, the right Alt and AltGr with v, and paste the result.

`diag keys --raw` goes below prompt_toolkit: on Windows it prints every key record the console
delivers (key down and key up, the virtual key, the scan code, the character and the modifier
flags), so it shows whether a key reaches ShellCraft at all and how; `--vt` turns the console's
VT input on first, as the direct key reader has it. On Linux it prints the raw bytes the terminal
sends. Enter ends it.

`diag keys --raw --mouse` shows only the mouse: it turns mouse reporting on and prints each
click and each wheel step (`wheel up at 12,5`), with the console's raw mouse record on Windows
(buttons, flags, wheel delta) or the SGR report elsewhere. With `--vt` on Windows the mouse comes
as SGR reports, as Windows Terminal sends them to the direct key reader. Plain moves are left out.
"""


@builtin("diag", "Console, color and footer diagnostics, to paste into a bug report",
         "diag [keys [--raw [--vt] [--mouse]]]",
         category="shell", stateful=True, doc=DIAG_DOC, requires="diagnostics")
def _diag(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    if not available(BUILTINS["diag"], ctx.config):  # hidden until `--diag` (or `settings diagnostics on`)
        raise CommandError("command not found: diag", 127)
    if args == ["keys"]:  # the key test: what each key press reaches the shell as
        if ctx.ui is None or not ctx.interactive:
            raise CommandError("diag keys: only in the interactive shell")
        from shellcraft.output import key_test

        lines = key_test(ctx.ui)
        return "".join(line + "\n" for line in lines)  # printed after, to copy into a report
    if args[:2] == ["keys", "--raw"] and set(args[2:]) <= {"--vt", "--mouse"}:  # below prompt_toolkit
        if ctx.ui is None or not ctx.interactive:
            raise CommandError("diag keys --raw: only in the interactive shell")
        from shellcraft.output import raw_keys

        console = ctx.ui.console
        mouse = "--mouse" in args
        console.print(Text("raw mouse: scroll the wheel and click; Enter ends" if mouse else
                           "raw keys: press the keys to test (Alt-v, right Alt-v, AltGr-v); Enter ends",
                           style="sc.muted"))
        console.file.flush()

        def control(seq: str) -> None:
            console.file.write(seq)
            console.file.flush()

        lines = raw_keys(lambda line: control(line + "\r\n"), vt="--vt" in args, mouse=mouse, control=control)
        return Styled(Text("✓ copy the lines above into a report", style="sc.success"),
                      "".join(line + "\n" for line in lines))
    if args:
        raise CommandError("usage: diag [keys [--raw [--vt] [--mouse]]]")
    if ctx.console_report is None:
        raise CommandError("diag: only in the interactive shell")
    report = ctx.console_report()
    return Styled(Text(report, no_wrap=True, overflow="ignore"), report + "\n")  # unwrapped: copy as is


@builtin("clear", "Clear the screen", "clear", stateful=True)
def _clear(ctx: ShellContext, args: list[str], stdin: str) -> str:
    if ctx.ui:
        from shellcraft.output import clear_screen

        clear_screen(ctx.ui)
    return ""


HELP_DOC = """# help

List every command, grouped by purpose: ShellCraft's own features first (with a description),
then Shell, Files, Text, Scripting and System as a grid of names, and the modules last (with a
description; ◆ marks one from your own folder). It is at most 100 columns wide and printed in
full, never paged, so you can scroll back to it.

## Usage

```
help               # the commands, grouped
help -a            # every command with its description
help NAME          # one command: its summary and usage (man NAME has the whole page)
help -k WORD       # the commands whose name or summary mentions WORD
```

## Options

| Option | Meaning |
| --- | --- |
| `-a`, `--all` | Every command with its description, not only ShellCraft's own and the modules. |
| `-k WORD`, `--search WORD` | Search the summaries (`help -k dns`), described like `-a`. |

In a pipe (`help | grep dns`) each command is one `name  summary` line.
"""

SECTIONS = {"shell": "Shell", "text": "Text", "files": "Files", "info": "Info"}
SCRIPT_HINT = "⚠ needs run() to work properly! see templates/README.md"  # a module file without run()
KEYWORDS_HELP = ("for", "if", "while", "until", "case", "select", "function")


# `help`: the groups, in order. ShellCraft's own features (and the modules) are listed with a
# short description; the commands you know from Linux are a grid of names (`help -a` describes
# every one). A builtin missing here goes to the group of its category.
HELP_GROUPS: tuple[tuple[str, str], ...] = (
    ("ShellCraft", "sub jobs alert clip record script mcp modules reload ingest startup profile func theme "
                   "prompt settings banner diag hotkeys mark tell review"),
    ("Shell", "cd pwd clear exit history alias unalias help man type which source"),
    ("Files", "ls tree find du cp mv rm mkdir touch stat basename dirname realpath"),
    ("Text", "cat grep head tail less more wc sort uniq cut tr tee diff jq echo printf seq yes"),
    ("Scripting", "test true false read unset export sleep time xargs"),
    ("System", "date env hostname uname whoami"),
)
CATEGORY_GROUP = {"shell": "Shell", "text": "Text", "files": "Files", "info": "System"}
# Commands that only make sense inside a shell function: not listed by `help` (they still
# complete, run and have their own man page), and their pages are part of `man function`.
FUNCTION_HELPERS = ("local", "shift", "declare")
DESCRIBED = ("ShellCraft", "Modules")  # the groups listed with descriptions by a bare `help`
HELP_SHORT = {  # one line each, for the ShellCraft group
    "profile": "Named setups: switch, create, save (bug bounty, OSINT…)",
    "func": "Named scripts of commands, run on demand (func run NAME)",
    "sub": "Open a subshell or a background job", "jobs": "List, switch, rename or stop subshells",
    "alert": "Show an alert in the footer", "clip": "The clip register (Alt-v pastes)",
    "record": "Record the session to a file", "script": "The same as record",
    "mcp": "Start or stop the MCP server", "modules": "List, enable or disable modules",
    "reload": "Re-scan the modules folders", "ingest": "Turn a script into a module",
    "startup": "List or rerun the startup scripts", "theme": "List or switch color themes",
    "prompt": "Choose the prompt's segments", "settings": "Settings and API keys",
    "banner": "Show the welcome banner", "diag": "Console, color and footer diagnostics",
    "hotkeys": "Keyboard shortcuts and mouse actions",
    "mark": "Flag output, files or a diff for an AI", "tell": "Send a message to the AI",
    "review": "Flags, the AI's requests and the commands it runs",
}
HELP_WIDTH = 100  # the help screen never grows wider than this, however wide the terminal


def _help_entries(ctx: ShellContext) -> list[tuple[str, str, str, str]]:
    """(group, name, summary, usage) for every command shown by `help`, in display order."""
    shown = {n for n, b in BUILTINS.items() if not b.hidden and available(b, ctx.config)
             and n not in KEYWORDS_HELP and n not in FUNCTION_HELPERS}
    groups: dict[str, list[str]] = {title: [n for n in names.split() if n in shown] for title, names in HELP_GROUPS}
    placed = {n for names in groups.values() for n in names}
    for name in sorted(shown - placed):
        groups[CATEGORY_GROUP.get(BUILTINS[name].category, "Shell")].append(name)
    rows: list[tuple[str, str, str, str]] = []
    for title, names in groups.items():
        rows += [(title, n, BUILTINS[n].summary, BUILTINS[n].usage) for n in names]
    from shellcraft.loader import is_script

    for name in ctx.registry.names():
        spec = ctx.registry.get(name)
        usage = (spec.skill.usage if spec.skill and spec.skill.usage else "") if spec else ""
        summary = spec.summary if spec else ""
        if spec is not None and is_script(spec.path):
            summary = (summary + " " if summary else "") + SCRIPT_HINT
        if ctx.registry.origin(name) == "user":
            summary = (summary + " " if summary else "") + "(user module)"
        rows.append(("Modules", name, summary, usage))
    return rows


def _brief(text: str, limit: int) -> str:
    """The first clause of a summary, within `limit` characters (cut at a word, with …)."""
    text = " ".join(text.split()).rstrip(".")
    for mark in (". ", ": ", " — ", " (", ", and ", ", or ", " and return"):
        head = text.split(mark)[0]
        if len(head) >= 20:
            text = head.rstrip(",;")
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:") + "…"


@builtin("help", "List built-ins and loaded modules", "help [NAME | -a | -k WORD]", doc=HELP_DOC)
def _help(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    search, every = None, False
    if args and args[0] in ("-k", "--search"):
        if len(args) != 2:
            raise CommandError("usage: help -k WORD")
        search = args[1].lower()
    elif args and args[0] in ("-a", "--all") and len(args) == 1:
        every = True
    elif len(args) > 1 or (args and args[0].startswith("-")):
        raise CommandError("usage: help [NAME | -a | -k WORD]")
    elif args:
        return _help_one(ctx, args[0])

    rows = _help_entries(ctx)
    if search is not None:
        rows = [r for r in rows if search in r[1].lower() or search in r[2].lower()]
        if not rows:
            raise CommandError(f"help: nothing matches '{search}' (man -k is not available; try help)")
        every = True
    plain = "".join(f"{name}  {summary}\n" for _, name, summary, _ in rows)
    if ctx.ui is None:
        return plain
    return Styled(Paged(_help_screen(ctx, rows, every, search), page=False), plain)  # printed in full


def _help_screen(ctx: ShellContext, rows: list[tuple[str, str, str, str]], every: bool,
                 search: str | None) -> Any:
    """The help screen: a heading line, a rule per group, then either name + description (the
    ShellCraft features, the modules, and everything with -a / -k) or a grid of names."""
    from rich.columns import Columns
    from rich.constrain import Constrain
    from rich.padding import Padding
    from rich.rule import Rule

    assert ctx.ui is not None
    width = min(ctx.ui.console.width, HELP_WIDTH)
    registry = ctx.registry
    builtins_shown = sum(1 for r in rows if r[0] != "Modules")
    if search is not None:
        head = Text.assemble((" ⚡ShellCraft ", "sc.prompt"),
                             (f" {len(rows)} match{'es' if len(rows) != 1 else ''} for ", "sc.muted"),
                             (search, "sc.accent"))
    else:
        head = Text.assemble((" ⚡ShellCraft ", "sc.prompt"),
                             (f" {builtins_shown} commands · {len(registry)} module{'s' if len(registry) != 1 else ''}",
                              "sc.muted"))
        if registry.disabled_names():
            head.append(f" · {len(registry.disabled_names())} disabled (modules -a)", style="sc.muted")
    parts: list[Any] = [head, Text("")]
    groups: dict[str, list[tuple[str, str]]] = {}
    for group, name, summary, _usage in rows:
        groups.setdefault(group, []).append((name, summary))
    # One cell width for every grid, so the names line up from one group to the next.
    cell = max([len(n) for g, items in groups.items() if g not in DESCRIBED for n, _ in items] or [8]) + 3
    for group, items in groups.items():
        color = "sc.success" if group == "Modules" else "sc.path"
        title = Text(f" {group} ", style="sc.success" if group == "Modules" else "sc.accent")
        title.stylize("bold")
        parts.append(Rule(title, align="left", style="sc.border"))
        if every or group in DESCRIBED:
            parts.append(_help_described(ctx, group, items, width, color, every))
        else:
            parts.append(Padding(Columns([Text(n, style=color) for n, _ in items], width=cell, padding=(0, 0)),
                                 (0, 0, 0, 1)))
        parts.append(Text(""))
    keywords = Text.assemble((" keywords  ", "sc.muted"), (" ".join(KEYWORDS_HELP), "sc.accent"))
    tips = Text()
    for i, (key, what) in enumerate((("help NAME", "one command"), ("help -a", "all described"),
                                     ("man NAME", "the manual"), ("help -k WORD", "search"),
                                     ("hotkeys", "keys and mouse"))):
        tips.append(" · " if i else " ", style="sc.muted")
        tips.append(key, style="sc.accent")
        if width >= 90:
            tips.append(f" {what}", style="sc.muted")
    parts += [keywords, tips]
    for warning in registry.warnings:
        parts.append(Text(f" ⚠ {warning}", style="sc.warning"))
    return Constrain(Group(*parts), width)


def _help_described(ctx: ShellContext, group: str, items: list[tuple[str, str]], width: int, color: str,
                    every: bool) -> Any:
    """Name and description, two columns of pairs when they fit (the ShellCraft group), one
    otherwise; module and -a descriptions are the first clause of the summary."""
    user = {n for n, _ in items if group == "Modules" and ctx.registry.origin(n) == "user"}

    def describe(name: str, summary: str, limit: int) -> str:
        if group == "ShellCraft" and not every and name in HELP_SHORT:
            return HELP_SHORT[name]
        return _brief(summary.replace(SCRIPT_HINT, "").replace("(user module)", ""), limit)

    name_width = max(len(n) + (2 if n in user else 0) for n, _ in items) + 1
    two = group == "ShellCraft" and not every and width >= 90
    grid = Table.grid(padding=(0, 2))
    columns = 2 if two else 1
    limit = (width // 2 - name_width - 2) if two else (width - name_width - 4)
    for _ in range(columns):
        grid.add_column(no_wrap=True, min_width=name_width)
        grid.add_column(style="sc.muted", no_wrap=True, overflow="ellipsis", max_width=max(10, limit))

    def cells(name: str, summary: str) -> list[Any]:
        label = Text(" " + name, style=color)
        if name in user:
            label.append(" ◆", style="sc.accent")
        text = Text(describe(name, summary, limit), style="sc.muted")
        if group == "Modules" and SCRIPT_HINT in summary:
            text = Text("⚠ needs run()", style="sc.warning")
        return [label, text]

    if two:
        half = (len(items) + 1) // 2
        for i in range(half):
            row = cells(*items[i])
            row += cells(*items[i + half]) if i + half < len(items) else ["", ""]
            grid.add_row(*row)
    else:
        for item in items:
            grid.add_row(*cells(*item))
    return grid


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
    from rich.markdown import Markdown  # loaded on the first `man` (markdown-it is ~90 modules)

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
        if name == "function":  # the commands used inside a function, on the same page
            for helper in FUNCTION_HELPERS:
                page = BUILTINS[helper].doc if helper in BUILTINS else ""
                if page:
                    body = page.split("\n", 1)[1] if page.startswith("# ") else page
                    body = body.replace("\n## ", "\n### ").replace(" See `man function`.", "")
                    body = body.replace(" (`man function`)", "")
                    doc = doc.rstrip("\n") + f"\n\n## {helper}\n" + body
        return doc + "\n*ShellCraft built-in command.*\n"
    raise CommandError(f"man: no manual entry for {name}")


THEME_DOC = """# theme

List the color themes, or switch to one. The choice is saved, so it is back next time.

In a subshell (`sub shellcraft`), `theme NAME` changes that subshell only and saves nothing.
Subshells start with the main theme, or with the subshell theme when one is set
(`theme --sub NAME`, from the main shell or a subshell).

## Usage

```
theme                  # the list, with a swatch of each palette (● marks the active one)
theme NAME             # switch to NAME and save it (in a subshell: this subshell only)
theme preview NAME     # try NAME in this session only (not saved)
theme reset            # back to the default theme (cyberpunk), saved (in a subshell: not saved)
theme --sub            # the theme subshells start with
theme --sub NAME       # subshells start with NAME from now on (saved)
theme --sub --clear    # subshells start with the main theme again (also: theme --sub off)
```

Themes made for a **light** terminal background: `solarized-light`, `catppuccin-latte`. Your own
themes go in `~/.shellcraft/config.json` (see docs/settings.md): any color left out comes from `base`,
and `light = true` picks light menu surfaces.
"""


THEME_USAGE = "theme [NAME | preview NAME | reset | --sub [NAME | --clear]]"


@builtin("theme", "List color themes or switch theme", THEME_USAGE, stateful=True,
         doc=THEME_DOC)
def _theme(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    from shellcraft.themes import DEFAULT_THEME

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
    if args[0] == "--sub":
        return _sub_theme(ctx, args[1:], themes)
    preview = save = False
    if args[0] == "preview" and len(args) == 2:
        name, preview = args[1], True
    elif args == ["reset"]:
        name, save = DEFAULT_THEME, True
    elif len(args) == 1:
        name, save = args[0], True
    else:
        raise CommandError(f"usage: {THEME_USAGE}")
    _check_theme(name, themes)
    if ctx.ui is None:
        raise CommandError("theme: no interactive UI to theme")
    ctx.ui.set_theme(themes[name])
    if preview:
        return Text.assemble(("👁 ", "sc.accent"), ("previewing ", ""), (themes[name].label, "sc.accent"),
                             (f"  (theme {name} keeps it)", "sc.muted"))
    assert save
    if ctx.lite:  # a subshell's theme is its own: the main shell's saved theme stays
        return Text.assemble(("✓ ", "sc.success"), ("theme set to ", ""), (themes[name].label, "sc.accent"),
                             (" for this subshell only", ""),
                             (f"  (theme --sub {name} makes it the default for subshells)", "sc.muted"))
    ctx.config["theme"] = name
    try:
        save_config(ctx.config)
    except OSError as exc:
        return Text(f"theme set to {themes[name].label} (not saved: {exc})", style="sc.warning")
    return Text.assemble(("✓ ", "sc.success"), ("theme set to ", ""), (themes[name].label, "sc.accent"))


def _check_theme(name: str, themes: dict[str, Any]) -> None:
    if name not in themes:
        close = difflib.get_close_matches(name, list(themes), n=1)
        hint = f" — did you mean '{close[0]}'?" if close else f" (try: {', '.join(themes)})"
        raise CommandError(f"theme: unknown theme '{name}'{hint}")


def _sub_theme(ctx: ShellContext, args: list[str], themes: dict[str, Any]) -> Any:
    """theme --sub [NAME | --clear | off]: the theme every subshell starts with (config "sub_theme")."""
    current = ctx.config.get("sub_theme")
    if not args:
        if isinstance(current, str) and current in themes:
            return Text.assemble(("subshell theme: ", ""), (themes[current].label, "sc.accent"),
                                 (f"  ({current}; theme --sub --clear goes back to the main theme)", "sc.muted"))
        return Text.assemble(("no subshell theme: subshells start with the main theme", ""),
                             ("  (theme --sub NAME sets one)", "sc.muted"))
    if len(args) != 1:
        raise CommandError(f"usage: {THEME_USAGE}")
    if args[0] in ("--clear", "off"):
        ctx.config.pop("sub_theme", None)
        done = Text.assemble(("✓ ", "sc.success"), ("subshells start with the main theme", ""))
    else:
        name = args[0]
        _check_theme(name, themes)
        ctx.config["sub_theme"] = name
        if ctx.lite and ctx.ui is not None:  # in a subshell, this one switches too
            ctx.ui.set_theme(themes[name])
        done = Text.assemble(("✓ ", "sc.success"), ("subshells start with ", ""), (themes[name].label, "sc.accent"))
    try:
        save_config(ctx.config)
    except OSError as exc:
        return Text(f"{done.plain[2:]} (not saved: {exc})", style="sc.warning")
    return done


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
    from shellcraft.shell import DEFAULT_LEFT, DEFAULT_RIGHT, SEGMENTS, prompt_segments

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


INGEST_DOC = """# ingest

Turn a Python script into a ShellCraft module, or check modules, without leaving the shell. It is
the ingest wizard (`tools/ingest`, also `shellcraft ingest` from a terminal) run in place, with this
shell's modules folder as the destination. A new module is picked up at the next prompt (hot
reload), so you can run it right away.

## Usage

```
ingest                                 # the wizard asks for everything
ingest SCRIPT.py [--name NAME]         # start with this script
ingest check MODULE.py...              # check modules against the framework
ingest check --all [--strict] [--network]
```

## Options

| Option | Meaning |
| --- | --- |
| `--name NAME` | The command name (asked for when left out). |
| `--modules DIR` | Where the module goes (default: your modules folder, `~/.shellcraft/modules`). |
| `--model MODEL` | The AI model to use for the conversion, instead of the default. |
| `check --all` | Every module in the modules folder. |
| `check --strict` | Warnings fail too (good for CI). |
| `check --network` | Also run the tests that need the internet. |

Your original script is never modified. The wizard talks to you on the terminal, so it only
runs in the interactive shell, never over MCP. The module guide (templates/README.md) has the
rules and a reference module.
"""


@builtin("ingest", "Turn a script into a module, or check modules",
         "ingest [SCRIPT.py] [--name NAME] | ingest check [--all] [MODULE.py...]",
         stateful=True, direct=True, doc=INGEST_DOC)
def _ingest(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    if not ctx.interactive or ctx.ui is None:
        raise CommandError("ingest: only in the interactive shell (from a terminal: shellcraft ingest ...)")
    from tools.ingest.__main__ import main as ingest_main

    try:
        target = ctx.registry.user_directory or ctx.registry.directory  # ~/.shellcraft/modules
        target.mkdir(parents=True, exist_ok=True)
        code = ingest_main(args, modules_dir=target, prog="ingest")
    except SystemExit as exc:  # argparse: --help, or a usage error already printed
        code = exc.code if isinstance(exc.code, int) else 1
    if code:
        raise CommandError(f"ingest: exited with status {code}", code)
    return ""


SUB_DOC = """# sub

Open a subshell: a program with the keyboard and the screen handed to it, a lite ShellCraft,
or a ShellCraft command running in the background. Every subshell is a numbered **job**, and the
**footer** on the last rows of the terminal lists them as a stack (`● main`, then `○ 1  bash …`,
newest at the bottom, growing as you open more), at the prompt and inside every subshell, with
the ● on the shell you are in. Hover or ↓ to pick, click or Enter to switch; `jobs` lists them
too, and **Ctrl-] N** or a click selects job N from the prompt or from inside another subshell.
**Ctrl-] c**, or the `▾` after `main`, folds the subshell lines into `main  3 subshells ▸`
when you need the room, and unfolds them again.

## Usage

```
sub                        # your own shell (bash, zsh, pwsh…); exit brings you back
sub PROGRAM [ARGS...]      # any program: sub vim notes.md, sub htop, sub ssh host, sub python
sub shellcraft [ARGS...]   # a lite ShellCraft in a subshell (its prompt says so: ⚡shellcraft [1])
sub sc                     # the same, short; `sc &` starts one in the background
sub COMMAND [ARGS...]      # a builtin or module, run in the background: sub grep -r TODO .
COMMAND [ARGS...] &        # the same, bash style: grep -r TODO . &   make &   tail -f log | grep x &
```

An AI client can open a subshell too (the `open_subshell` MCP tool, `man mcp`): a lite ShellCraft
named `✦ AI · finding` in the footer, started in the background, that prints the AI's brief first
(its findings, the evidence, what to do next); an alert points to it. Ctrl-] N or a click shows
it, `exit` returns. A command the AI runs for you (`run_in_shell`) is a background job named
`✦ AI: command`, reported above your prompt like any `cmd &`.

A trailing `&` (after a space, outside quotes) runs the whole line as a background job: a
builtin, a module, a pipeline or a loop becomes a command job (quotes and pipes kept as typed); a
single OS program starts as a program job **without** attaching (switch to it with Ctrl-] N).
An `&` anywhere else is still an error.

## How it works

- A **program** runs on its own pseudo-terminal and you are attached to it at once: what you
  type goes to it, what it prints you see. Press **Ctrl-]** to detach (or **Ctrl-] 0**): the
  program keeps running in the background, and `jobs N`, **Ctrl-] N** or a click on its tab
  attaches again (its last lines are shown, and full-screen programs redraw). **Ctrl-] N** inside
  one program switches straight to another; **Ctrl-] Ctrl-]** sends a Ctrl-] to the program,
  **Ctrl-] v** pastes the clip register (a right-clicked alert, `man clip`) into it, and
  **Ctrl-] x** ends the program you are in and returns to the prompt.
  When it exits you are back at the prompt with its status in `$?`. Jobs still running when you
  leave ShellCraft are stopped.
- A **command** (a builtin or a module) runs in a worker thread with its output collected.
  `jobs N`, **Ctrl-] N** or its tab shows the output so far (`jobs N | less` pages it); a finished
  job prints `[N] done: …` and its output at the next prompt, then leaves the stack (`settings
  jobs_autoclear off` keeps it until `jobs -c`). Background commands cannot change the shell's state
  (`cd`, `settings`…), and `tail -f` needs the terminal, so they are refused there.
- The footer looks the same at the prompt and inside a subshell: the line under the mouse is
  highlighted, the ● marks where you are, a running line is plain and a finished one is green
  (`done`) or red (`exit 2`, `killed`), with an × on the right that removes it (`jobs` has the
  times). It takes up to half the screen, then says how many more there are. An alert sent from
  a subshell shows inline on its line; the badge on the `main` line counts them all (`man alert`).
  `jobs -c` clears the finished ones, `jobs -k N` stops one, `settings footer off` goes back to a
  panel under the prompt only while something runs, and nothing inside subshells (Ctrl-] N
  still works).

Each program has its own terminal emulator: switching to it redraws its screen exactly as it
was, and the mouse wheel scrolls through its scrollback (any key returns to the live page).
Subshells are shown on the terminal's alternate screen, so the prompt comes back untouched.
On Windows programs run on ConPTY through the `pywinpty` package; without it, `sub PROGRAM`
runs the program in the foreground (you are back when it exits) and it is not listed as a job.

`sub shellcraft` runs a **lite** ShellCraft (`shellcraft --lite`): the builtins, the modules,
settings, aliases and history, without the banner, the startup scripts, hot reload, the alert
watcher or the MCP server (`mcp` says so). Every subshell belongs to the main ShellCraft; there
is no subshell inside a subshell. `sub …` or `cmd &` typed inside one hands the request to the
main shell, which runs it in the background: it appears in the footer under `main`, you stay
where you are, and Ctrl-] N switches to it when you want. `jobs` is for the main shell.

## Examples

```
sub                         # a bash to poke around in; `exit` returns
sub shellcraft --theme nord # try a theme in a nested shell
sub find / -name "*.log"    # slow search in the background, keep working
find / -name "*.log" &      # the same, bash style
jobs                        # what is running, and what finished
jobs 1                      # attach to job 1 (Ctrl-] leaves it running, Ctrl-] 2 jumps to job 2)
```
"""

ALERT_DOC = """# alert

Show an alert in the status panel: green for information (`info`), amber for a warning (`warn`),
red for something critical (`crit`). From the main shell it is shown at once; from a subshell (`sub bash`,
`sub pwsh`, `sub shellcraft`) the same command reaches the main shell's panel, and so does the
`send_tui_alert` MCP tool an AI client calls.

## Usage

```
alert MESSAGE...                     # an info alert, shown for 15 s
alert -l warn MESSAGE...             # amber, 60 s
alert -l crit MESSAGE...             # red, stays until dismissed
alert -t 5 MESSAGE...                # shown for 5 s; -s keeps it until dismissed
alert --list                         # the current alerts, numbered
alert -c                             # dismiss every alert (alert -c N: just number N)
```

## Options

| Option | Meaning |
| --- | --- |
| `-l LEVEL`, `--level LEVEL` | `info` (default), `warn` or `crit`. |
| `-t SECONDS`, `--timeout SECONDS` | How long it stays (default: 15 for info, 60 for warn, crit stays). |
| `-s`, `--sticky` | Stay until dismissed. |
| `-d DATA`, `--data DATA` | Data kept with the alert, up to 64 KB (`-d -`: stdin); a right-click copies it. |
| `-c [N]`, `--clear [N]` | Dismiss every alert, or alert N of `alert --list`. |
| `--list` | List the current alerts. |

The shell keeps at most three alerts; when more arrive, the oldest of the lowest level makes
room. In the panel under the prompt (and in the footer inside a subshell):

- An alert sent from a subshell shows **inline** on that subshell's line, after its command, in
  its color: `● Disk /home at 91%` (`+1` when it has more; the most severe shows).
- The **badge** at the right end of the `main` line counts every alert by level
  (`● 1  ● 1  ● 1 ▸`, a filled bubble in each level's color). A click on it unfolds one row per
  alert, the most severe first, with the message, where it came from and the time left; another
  click folds them back (`▾` / `▸`).
- A **crit** alert always keeps its full row, folded or not.
- To dismiss one: click its **✖** at the start of the row (`alert -c [N]` from the keyboard).
  Every level has the same mark, in its color: green info, amber warn, red crit.
- A **right-click** on a row copies the alert's data (`-d`, or the message when it has none)
  into the shell's clip register; the row says `copied`. **Alt-v** pastes it at the prompt
  (**Ctrl-] v** into another program inside a subshell); `clip` prints it and `$CLIP` holds it
  (`man clip`). From the keyboard: **Alt-c** copies the top alert, `clip -a
  N` alert N. While the footer lists a subshell or an alert the shell has the mouse: select text
  with Shift and a drag; the wheel hands the mouse back to the terminal, so it scrolls as usual.
- With no subshell to fold into, every alert shows as a row.

```
alert -l warn -d /var/log/app.log "errors in the app log"   # right-click, then: tail -f  Alt-v
make 2>&1 | alert -l crit build failed -d -                  # the build output as the data
```

## How it reaches the shell

Senders drop one small JSON file into `~/.shellcraft/alerts/`, a folder only you can write to,
and the shell's watcher picks it up within a quarter of a second. No port, no token. Each alert
names its sender: `main shell`, `sub 2 (bash)` (the subshell's number and command, from
`SHELLCRAFT_SUB` in its environment), a nested ShellCraft, `pid N` for an outside script, or
`mcp · <client name>` for an AI client. Subshells get an `alert` command on their PATH; outside
ShellCraft, `shellcraft-alert MESSAGE` (or `python -m shellcraft.alerts`) does the same. A file left
in the spool while no shell was running is dropped after five minutes.
"""


@builtin("alert", "Show an alert in the status panel",
         "alert [-l LEVEL (info,warn,crit)] [-t SECONDS | -s] [-d DATA] MESSAGE... | --list | -c [N]",
         stateful=True,
         doc=ALERT_DOC)
def _alert(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    from shellcraft import alerts as alertmod

    try:
        message, level, timeout, clear, listing, content = alertmod.parse_args(args)
    except SystemExit as exc:  # argparse: --help, or a usage error already printed
        if exc.code:
            raise CommandError("alert: bad arguments (man alert)") from None
        return ""
    bus = ctx.alerts
    if content == "-":  # `cmd | alert done -d -`: the data is what was piped in
        content = stdin
    if alertmod.depth_forward():  # a nested ShellCraft: the main shell shows it
        if clear or listing:
            raise CommandError("alert: --list and -c work in the main ShellCraft")
        if not message:
            raise CommandError("alert: a message is needed")
        try:
            source = f"{alertmod.source_label()} shellcraft [{alertmod.depth_forward()}]"
            alertmod.send(message, level, timeout, source=source, content=content)
        except OSError as exc:
            raise CommandError(f"alert: {exc}") from None
        return Text(f"✓ sent to the main shell: {message}", style="sc.success")
    if bus is None:
        raise CommandError("alert: only in the interactive shell")
    if listing:
        current = bus.current()
        if not current:
            return Text("no alerts", style="sc.muted")
        rows = Text()
        for i, alert in enumerate(current, 1):
            left = f"{i}. [{alert.level}] {alert.message}"
            when = "sticky" if alert.remaining is None else f"{int(alert.remaining) + 1}s left"
            extra = f", {len(alert.content)} characters of content" if alert.content else ""
            rows.append(f"{left}  ({when}{', from ' + alert.source if alert.source else ''}{extra})\n")
        return rows
    if clear:
        number = int(message) if message.strip().isdigit() else None
        gone = bus.dismiss(number)
        return Text(f"✓ {gone} alert{'s' if gone != 1 else ''} dismissed", style="sc.success")
    if not message:
        raise CommandError("alert: a message is needed (man alert)")
    bus.post(message, level, timeout, source="main shell", content=content)
    return ""


CLIP_DOC = """# clip

The clip register: the last ten texts you copied, newest first. A **right-click on an alert**
copies its content (or its message when it has none): it becomes **`$CLIP`**, the one copied
before it **`$CLIP1`**, then `$CLIP2` … `$CLIP9` (the oldest drops off; copying the same text again
moves it to the front). **Alt-v** (or Esc then v) pastes `$CLIP` at the prompt, in the main shell
and in a `sub shellcraft` alike; **press Alt-v again right away** and the paste becomes `$CLIP1`,
again `$CLIP2`, and so on round (typing anything in between starts over at the newest). In
another program inside a subshell (bash, pwsh, vim), where Alt-v is the program's key,
**Ctrl-] v** types it in. `clip -i` does it without a key chord: the next prompt starts with
the text on the line.

It is also the `$CLIP` variable (`echo $CLIP`, `grep "$CLIP" file`). An AI client can stage a
command in it with the `set_clip` MCP tool (it says so in an alert): Alt-v pastes it.

## Usage

```
clip                    # print the newest copy ($CLIP)
clip 2                  # print copy 2 ($CLIP2)
clip -l                 # list the copies, newest first
clip TEXT...            # set it
echo hello | clip       # set it from a pipe
clip | jq               # use it: pipe it on
echo $CLIP              # the same text as a variable
clip -i [N]             # the next prompt starts with copy N (default: the newest)
clip -a [N]             # copy alert N (default: the top one), as a right-click on it does
clip -c                 # clear every copy
echo $CLIP $CLIP1       # as variables
```

**Alt-c** at the prompt copies the top alert too. There is one register for the main shell and
every `sub shellcraft`: it lives in the main shell's memory, and a lite subshell asks for it over
a private channel (a local socket, a named pipe on Windows) locked with a key only it was
given. Nothing is written to disk.

The register is ShellCraft's own, not the system clipboard. To copy text from the screen into
other programs, select it with **Shift** and a drag (the terminal's own selection while the shell
has the mouse) and use the terminal's copy.

## Options

| Option | Meaning |
| --- | --- |
| `-i [N]`, `--insert [N]` | The next prompt starts with copy N (default: the newest), ready to edit or run. |
| `-l`, `--list` | The copies, newest first, as `$CLIP`, `$CLIP1` …. |
| `-a [N]`, `--alert [N]` | Copy alert N (`alert --list` numbers them; default 1, the top one). |
| `-c`, `--clear` | Empty the register. |

## See also

`alert -d DATA MESSAGE` (an alert with data), `man alert`.
"""


@builtin("clip", "The clip register: what a right-click on an alert copied; Alt-v pastes it",
         "clip [TEXT... | N] | -l | -i [N] | -a [N] | -c", category="text", doc=CLIP_DOC)
def _clip(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    bus = ctx.alerts
    if bus is None:
        raise CommandError("clip: only in the interactive shell")
    if args and args[0] in ("-c", "--clear"):
        bus.clip = ""
        return ""
    if args and args[0] in ("-i", "--insert"):  # the next prompt starts with a copy
        slot = int(args[1]) if len(args) > 1 and args[1].isdigit() else 0
        text = bus.clip_at(slot)
        if not text:
            raise CommandError("clip: the register is empty (right-click an alert, or clip TEXT)" if not slot
                               else f"clip: no copy {slot} (clip -l lists them)")
        ctx.prefill = text.rstrip("\n")
        return Text("✓ the next prompt starts with the clip" + (f" {slot}" if slot else ""), style="sc.success")
    if args and args[0] in ("-l", "--list"):  # the history, newest first
        listed = bus.clips()
        if not listed:
            return Text("no copies (right-click an alert, or clip TEXT)", style="sc.muted")
        rows = Text()
        for slot, text in enumerate(listed):
            name = "$CLIP" + (str(slot) if slot else "")
            line = " ".join(text.split())
            rows.append(f"{name:<7}", style="sc.accent")
            rows.append(f"{line[:60] + ('…' if len(line) > 60 else '')}")
            rows.append(f"  ({len(text)} characters)\n", style="sc.muted")
        return Styled(rows, "".join(f"{s}\t{' '.join(t.split())}\n" for s, t in enumerate(listed)))
    if len(args) == 1 and args[0].isdigit() and len(args[0]) == 1:  # `clip 2`: copy number 2
        text = bus.clip_at(int(args[0]))
        return text + "\n" if text else ""
    if args and args[0] in ("-a", "--alert"):  # copy an alert, as a right-click on it does
        current = bus.current()
        number = int(args[1]) if len(args) > 1 and args[1].isdigit() else 1
        if not 1 <= number <= len(current):
            raise CommandError("clip: no such alert (alert --list numbers them)" if current else "clip: no alerts")
        text = bus.copy(current[number - 1])
        return Text.assemble(("✓ copied: ", "sc.success"), (_brief(text, 60), "sc.accent"),
                             ("  (Alt-v pastes)", "sc.muted"))
    if args:
        bus.clip = " ".join(args)
        return ""
    if stdin:
        bus.clip = stdin.rstrip("\n")
        return ""
    return bus.clip + "\n" if bus.clip else ""


HOTKEYS_DOC = """# hotkeys

Every keyboard shortcut and mouse action in ShellCraft, by where it works, with what differs
between Linux and Windows. `hotkeys` prints this page; `man hotkeys` opens it in the viewer.

## At the prompt: editing

| Keys | What they do |
| --- | --- |
| `Enter` | Run the line (a line with an open `{` or `(` asks for more) |
| `Tab` | Complete a command, a switch, a value or a path |
| `→` or `End` | Accept the grey suggestion from your history |
| `↑` `↓` | The previous / next command in the history |
| `Ctrl-R` | Search the history as you type |
| `Ctrl-A` `Ctrl-E` | The start / end of the line |
| `Alt-B` `Alt-F` | One word back / forward |
| `Ctrl-W` | Delete the word before the cursor |
| `Ctrl-U` `Ctrl-K` | Delete to the start / end of the line |
| `Ctrl-L` | Clear the screen |
| `Ctrl-C` | Drop the line (or stop the running command); it never closes ShellCraft |
| `Ctrl-D` | Leave ShellCraft, on an empty line |

## The clip register and alerts

| Keys or mouse | What they do |
| --- | --- |
| `Alt-v` | Paste the newest copy (`$CLIP`). Press it again right away for `$CLIP1`, `$CLIP2`… |
| `Esc` then `Ctrl-V` | The same paste (Ctrl+Alt+v: what AltGr+v sends) |
| `Alt-c` | Copy the top alert's content |
| Right-click an alert | Copy its content (`clip -a N` from the keyboard) |
| Click an alert's `✖` | Dismiss it (`alert -c N` from the keyboard) |
| Click the badge on `main` | Show / fold the alert rows |
| `clip -i [N]` | Start the next prompt with copy N, no key chord at all |

## The footer and subshells

| Keys or mouse | Where | What they do |
| --- | --- | --- |
| `Ctrl-]` then a digit | anywhere | Switch to subshell N (`0`: back to the prompt) |
| `Ctrl-]` alone | in a subshell | Back to the prompt; the subshell keeps running |
| `Ctrl-]` then `x` | in a subshell | End the subshell you are in |
| `Ctrl-]` then `c` | anywhere | Fold the subshell lines into `main`, or unfold them |
| `Ctrl-]` then `v` | in a subshell | Type the newest copy into the program (bash, pwsh, vim) |
| `Ctrl-]` twice | in a subshell | Send one `Ctrl-]` to the program |
| `↓` on an empty line | at the prompt | Into the footer: `↑` `↓`, `Enter` switches, `x` stops, `Esc` leaves |
| Click a line | anywhere | Switch to that shell |
| Click `▾` / `▸` after `main` | anywhere | Fold / unfold the subshell lines |
| Click the `×` of a finished line | anywhere | Remove it from the footer |

`Ctrl-]` is the default prefix key. `settings prefix_key` lists the others (`ctrl-b`, `ctrl-a`,
`ctrl-t`, `ctrl-g`, `ctrl-s`) and what each one shadows; `settings prefix_key ctrl-b` switches
to Ctrl-B everywhere at once, and `settings reset prefix_key` goes back to Ctrl-].

## Mouse: scrolling and selecting

| Mouse | When | What it does |
| --- | --- | --- |
| Wheel | nothing to click in the footer | The terminal's own scrolling |
| Wheel | the footer lists subshells or alerts | The terminal's own scrolling (the first turn hands it the mouse back) |
| Wheel | inside a subshell | Scrolls that subshell's output (a full-screen program such as `less` gets arrow keys) |
| Drag | nothing to click in the footer | The terminal's own selection |
| `Shift` + drag | the shell has the mouse (footer items, or a subshell) | The terminal's own selection |

Copy and paste of selected text is the terminal's own: on Linux usually `Ctrl-Shift-C` and
`Ctrl-Shift-V` (and a middle click pastes the selection); in Windows Terminal `Ctrl-C` with a
selection and `Ctrl-V`, or `Ctrl-Shift-C` and `Ctrl-Shift-V`. `settings footer_mouse off` leaves
the mouse to the terminal at the prompt; `settings mouse off` turns ShellCraft's mouse off.

## The viewer (man, less, the scroll view)

| Keys | What they do |
| --- | --- |
| `↑` `↓` or `j` `k` | One line |
| `PgUp` `PgDn`, `b` `f`, `Space` | One screen |
| `d` `u` | Half a screen |
| `g` `G`, `Home` `End` | The top / the end |
| `←` `→` | Sideways, when lines are not wrapped |
| `w` | Wrap long lines on / off |
| `/` then `n` `N` | Search, then the next / previous match |
| Wheel, the bar on the right | Scroll; click or drag the bar |
| `?` | The keys |
| `q` or `Esc` | Close |

## Linux and Windows

| | Linux | Windows 11 |
| --- | --- | --- |
| Alt shortcuts (`Alt-v`, `Alt-c`) | Left and right Alt; AltGr unless it types a character | Left, right Alt and AltGr |
| Alt does not arrive | Press `Esc` then the key (Konsole's menu bar takes Alt-v) | Press `Esc` then the key |
| Subshells (`sub`) | Always | Need the `pywinpty` package (the banner says when it is missing) |
| Terminal | Any modern terminal | Windows Terminal; the classic console works with fewer colors |

`diag keys` shows what each key press reaches the shell as: Alt-v must show `escape v`. On
Windows it also shows the console's Alt flags, to paste into a bug report. When a key does not
show there at all, `diag keys --raw` prints what the console itself delivers, below the key
reader (`--vt` with the console's VT input on), and `diag keys --raw --mouse` the same for
the mouse wheel and clicks.
"""


@builtin("hotkeys", "Keyboard shortcuts and mouse actions, for Linux and Windows", "hotkeys",
         category="shell", doc=HOTKEYS_DOC)
def _hotkeys(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    if args:
        raise CommandError("usage: hotkeys  (man hotkeys opens it in the viewer)")
    if ctx.ui is None:
        return HOTKEYS_DOC
    from rich.markdown import Markdown

    return Styled(Paged(Markdown(HOTKEYS_DOC), page=False), HOTKEYS_DOC)  # printed in full, to scroll back to


RECORD_DOC = """# record

Write a transcript of the session to a file, like Linux `script` without the escape codes:
every command you type, with the time, and what the shell prints in return. `script` is the
same command. While it runs, the footer shows it on the shell's own line: its bubble (● for
the shell you are in, ○ for another one) turns **red**; a `sub shellcraft` that runs `record`
lights its own line, so you can see which shells are recording.

## Usage

```
record                          # start: ~/.shellcraft/records/session-DATE.txt
record start notes.txt          # start, into this file (appended to if it exists)
record status                   # where it is writing, for how long, how many commands
record stop                     # end it (leaving ShellCraft ends it too)
script [...]                    # the same command under its Linux name
```

## What goes in

```
# ShellCraft session · started 2026-10-15 14:03:12 · xwing · ~/work
[14:03:15] ❯ ls
CHANGELOG.md  README.md  …
[14:03:20] ❯ grep -n TODO *.md
…
# ended 14:10:01 · 6 min 49 s · 12 commands
```

Colors and cursor movements are stripped, so the file reads as plain text. The prompt itself,
the footer and what happens inside an attached subshell (`sub bash`) are not recorded; a line
says when a subshell was attached.
"""


@builtin("record", "Write a transcript of the session to a file (like Linux script)",
         "record [start [FILE] | stop | status]", stateful=True, doc=RECORD_DOC)
def _record(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    from pathlib import Path

    from shellcraft import recording

    if not ctx.interactive:
        raise CommandError("record: only in the interactive shell")
    action, rest = (args[0], args[1:]) if args else ("start", [])
    if action == "status":
        rec = ctx.recording
        if rec is None:
            return Text("not recording (record starts a transcript)", style="sc.muted")
        minutes, seconds = divmod(int(rec.elapsed), 60)
        return Text.assemble(("🔴 recording to ", "sc.error"), (str(rec.path), "sc.accent"),
                             (f"  {minutes} min {seconds} s · {rec.commands} command{'s' if rec.commands != 1 else ''}",
                              "sc.muted"))
    if action == "stop":
        rec = recording.stop(ctx)
        if rec is None:
            raise CommandError("record: not recording")
        return Text.assemble(("■ transcript saved: ", "sc.success"), (str(rec.path), "sc.accent"),
                             (f"  ({rec.commands} command{'s' if rec.commands != 1 else ''})", "sc.muted"))
    if action not in ("start", "-o", "--output"):
        rest = [action, *rest]  # `record notes.txt`
    if len(rest) > 1:
        raise CommandError("usage: record [start [FILE] | stop | status]")
    path = Path(rest[0]).expanduser() if rest else None
    try:
        rec = recording.start(ctx, path)
    except OSError as exc:
        raise CommandError(f"record: {exc}") from None
    return Text.assemble(("🔴 recording to ", "sc.error"), (str(rec.path), "sc.accent"),
                         ("  (record stop ends it)", "sc.muted"))


@builtin("script", "The same as record: a transcript of the session, under its Linux name",
         "script [start [FILE] | stop | status]", stateful=True, doc=RECORD_DOC)
def _script(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    return _record(ctx, args, stdin)


LESS_DOC = """# less

Read output in the scrollable viewer: `cmd | less`, or `less FILE`. `more` is the same command.
Nothing else opens the viewer by itself except `man`; long output just prints, and `less` is
how you page it when you want to.

## Usage

```
ls -l | less                    # page a listing
cat big.log | less              # or: less big.log
grep -n TODO *.py | less
```

In the viewer: `↑` `↓` `j` `k` scroll, `PgUp` `PgDn` `space` page, `d` `u` half a page, `g` `G`
top and end, `←` `→` sideways, `w` wraps long lines, `/` searches and `n` `N` step through the
matches, the wheel scrolls, `?` lists the keys, `q` closes it. With `settings pager off` the
text is printed instead; in a pipe or a file it passes through unchanged (`cat x | less | wc -l`).
"""


@builtin("less", "Read output in the scrollable viewer: cmd | less, or less FILE", "less [FILE]",
         category="text", doc=LESS_DOC)
def _less(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    from shellcraft.commands._io import read_file

    if len(args) > 1:
        raise CommandError("usage: less [FILE]  (or: cmd | less)")
    text = read_file("less", args[0]) if args else stdin
    if not text:
        return ""
    return Paged(text, page=True) if ctx.ui is not None and ctx.interactive else text


@builtin("more", "The same as less: read output in the scrollable viewer", "more [FILE]",
         category="text", doc=LESS_DOC)
def _more(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    return _less(ctx, args, stdin)


READ_DOC = """# read

Ask for a value and keep it in a shell variable, like bash's `read`. The Python spelling works too,
on a line of its own: `name = input("What is your name? ")`.

## Usage

```
read NAME                       # read a line into $NAME
read -p "Your name? " NAME      # with a prompt
read -s -p "Token: " TOKEN      # without echoing what is typed
name = input("Your name? ")     # the same, Python style (not inside for / if blocks)
count = int(input("How many? "))   # asks again until the answer is a whole number
ratio = float(input("Ratio? "))    # … or any number
```

## Options

| Option | Meaning |
| --- | --- |
| `-p PROMPT`, `--prompt PROMPT` | The text shown before the answer. |
| `-s`, `--silent` | Don't echo the answer (passwords, tokens). |

The variable lasts for the session: later lines use it as `$NAME` (`echo hello $name`), and
`export NAME` passes it to programs. Useful in `~/.shellcraft/rc` to ask something at startup.
Only the interactive shell can ask; Ctrl-C cancels (status 130), and over MCP `read` is refused.

## Examples

```
name = input("What is your name? ")
echo "hello $name"
read -p "Which environment? " env; export DEPLOY_ENV=$env
read -s -p "API token: " token
```
"""


@builtin("read", "Ask for a value and keep it in a shell variable", "read [-p PROMPT] [-s] NAME",
         stateful=True, direct=True, doc=READ_DOC)
def _read(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    from shellcraft import ask
    from shellcraft.modkit import ArgParser

    parser = ArgParser("read")
    parser.add_argument("-p", "--prompt", default="")
    parser.add_argument("-s", "--silent", action="store_true")
    parser.add_argument("name")
    opts = parser.parse_args(args)
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", opts.name):
        raise CommandError(f"read: invalid variable name '{opts.name}'")
    try:
        ctx.variables[opts.name] = ask.ask(ctx, opts.prompt, hidden=opts.silent)
    except ask.AskError as exc:
        raise CommandError(str(exc).replace("input:", "read:", 1), exc.status) from None
    return ""


SOURCE_DOC = """# source

Run a file of ShellCraft command lines, as if each one were typed at the prompt: builtins,
modules, aliases, `export`, `cd`, `theme`, `sub`, and `for` / `if` blocks over several lines.
At startup the shell sources `~/.shellcraft/rc` this way (`man startup`).

## Usage

```
source FILE
. FILE                       # the same, bash's short spelling
source ~/.shellcraft/rc      # run the startup file again
```

## Splitting the rc file

As in bash, a file can source others, so `~/.shellcraft/rc` can pull in separate files for
separate uses, and skip the ones that don't exist:

```
# ~/.shellcraft/rc
if [ -f ~/.shellcraft/rc.work ]; then
    . ~/.shellcraft/rc.work
fi
[ -f ~/.shellcraft/rc.home ] && source ~/.shellcraft/rc.home
for f in ~/.shellcraft/rc.d/*.rc; do [ -f "$f" ] && source "$f"; done
```

Blank lines and lines starting with `#` are skipped. Each command's output is shown as it runs;
a command that fails is reported with its line number (`rc:4: …`) and the next one runs anyway.
`exit` in the file leaves the shell. A file may `source` another, up to 8 deep.
"""


@builtin("source", "Run a file of ShellCraft commands", "source FILE", stateful=True, direct=True,
         doc=SOURCE_DOC)
def _source(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    from shellcraft import startup as startupmod
    from shellcraft.commands._io import user_path

    if len(args) != 1:
        raise CommandError("usage: source FILE")
    path = user_path(args[0])
    try:
        ran, failed = startupmod.source(path, ctx)
    except FileNotFoundError:
        raise CommandError(f"source: {args[0]}: no such file") from None
    except (OSError, UnicodeDecodeError) as exc:
        raise CommandError(f"source: {args[0]}: {exc}") from None
    except RecursionError as exc:
        raise CommandError(str(exc)) from None
    if failed:
        return WithStatus("", 1)
    return ""


# `. FILE`, bash's short spelling of `source FILE` (hidden from help; `man source` covers it).
builtin(".", "Run a file of ShellCraft commands (same as source)", "source FILE", stateful=True, direct=True,
        doc=SOURCE_DOC, hidden=True)(_source)


STARTUP_DOC = """# startup

Python scripts that run when the interactive shell starts, before the first prompt: set
environment variables for everything ShellCraft starts later, mount or sync something, check a
VPN, print a reminder.

## Usage

```
startup                  # the startup scripts, in order, with their last result
startup run [NAME]       # run them again now (or just NAME)
startup --folder         # where they live
startup stop [NAME]      # stop a background script (or all of them): no restart
startup start NAME       # start a background script again
startup restart NAME     # stop it and start it
```

## Writing one

Put a `.py` file in `~/.shellcraft/startup/`. Every file there runs once, in name order, so number
them: `10-env.py`, `20-mounts.py`. Files named `_name.py` are helpers the others can import.

A script runs as its own process, so changing `os.environ` inside it does not reach the shell. To
set variables for the shell and everything it starts later (subshells, modules, OS programs,
`mcp start`), append `NAME=value` lines to the file named in `$SHELLCRAFT_ENV_FILE`:

```python
import os

with open(os.environ["SHELLCRAFT_ENV_FILE"], "a") as f:
    f.write("PROJECT_ROOT=/srv/app\n")
    f.write("OLD_VAR=\n")          # NAME= with nothing after it unsets the variable
```

The variables apply only if the script exits with status 0, and a later script sees those of the
earlier ones. What a script prints is shown under the banner. A script that fails, or runs longer
than 30 seconds, is reported in one line and the shell starts anyway.

## Header lines: background, restart, timeout

Lines at the top of a script, before its first line of code, change how it runs:

```python
# shellcraft: background        start it as a subshell when the prompt appears; never waited for
# shellcraft: restart           start it again if it exits with an error
# shellcraft: timeout=120       a normal script may take this long (default 30 s)
```

A **background** script (a watcher, a tunnel, a periodic check) starts the moment the prompt is
ready, so the shell starts as fast as without it. It is a subshell like `sub python FILE`: listed
in the panel as `startup: NAME.py`, Ctrl-] N or a click attaches to it, **Ctrl-] x** ends it,
`jobs -k N` stops it, and it is stopped when you leave ShellCraft. `alert` is on its PATH, and its
alerts say which script sent them. It can't set variables for the shell (it starts after the
shell is ready): put those in a normal script, which runs first.

With **restart**, a script that exits with an error is started again after 2, 4, 8… up to 60
seconds; after 5 crashes in 10 minutes it is left stopped and a red alert says so. A clean exit
(status 0), or a stop by you, is never restarted. `startup` shows each background script's state.

Scripts run in name order (`10-env.py` before `20-watcher.py`); there are no dependency
declarations and no arguments. A failing script never stops the shell from starting.

## The rc file: ShellCraft commands

After the scripts, the shell runs `~/.shellcraft/rc`, a file of ShellCraft command lines, like
bash's `~/.bashrc`. Each line runs as if you typed it at the prompt, so anything you can type works:

```
# ~/.shellcraft/rc
theme nord
env set PROJECT_ROOT=~/projects   # environment variables for modules and programs (man env)
env set -s GITHUB_TOKEN           # a secret: asked with hidden typing at each start
cd ~/projects
alert -l info "welcome back"
sub find ~/projects -name "*.log" -mtime -1
for h in web1 web2; do myip $h; done
```

A line can ask you something, Python style, and later lines use the answer as a variable:
`name = input("What is your name? ")`, then `echo hello $name` (`man read`).

Output is shown under the banner; a failing line is reported with its line number and the rest
still run. `source ~/.shellcraft/rc` runs it again (`man source`). It never runs for `--mcp` /
`--mcp-http`: AI clients get a clean shell.

`settings startup off`, or `shellcraft --no-startup` for one session, skips both the scripts and
the rc file.
"""


def _startup_control(ctx: ShellContext, action: str, name: str | None) -> Any:
    """startup stop [NAME] / start NAME / restart NAME: the background startup scripts."""
    supervisor = ctx.supervisor
    if supervisor is None or not ctx.interactive:
        raise CommandError(f"startup {action}: only in the interactive shell")
    if action != "stop" and not name:
        raise CommandError(f"usage: startup {action} NAME (startup lists them)")
    try:
        if action == "stop":
            if name is not None and name not in supervisor.scripts:
                raise KeyError(name)
            done = supervisor.stop(name)
            names = ", ".join(m.path.name for m in done) or "nothing to stop"
            return Text(f"✓ stopped: {names}", style="sc.success")
        managed = supervisor.start(name) if action == "start" else supervisor.restart(name)
    except KeyError:
        raise CommandError(f"startup {action}: no background script named {name} "
                           "(a startup script with a `# shellcraft: background` line; startup lists them)") from None
    return Text(f"✓ {action}ed {managed.path.name} as subshell [{managed.job.number}]", style="sc.success")


@builtin("startup", "List or rerun the startup scripts",
         "startup [run [NAME] | stop [NAME] | start NAME | restart NAME]", stateful=True,
         doc=STARTUP_DOC)
def _startup(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    from shellcraft import startup as startupmod

    if args in (["--folder"], ["-f"]):
        return f"{startupmod.folder()}\n"
    if args[:1] in (["stop"], ["start"], ["restart"]):
        return _startup_control(ctx, args[0], args[1] if len(args) > 1 else None)
    if args[:1] == ["run"]:
        only = args[1] if len(args) > 1 else None
        results = startupmod.run_all(only=only)
        if only is not None and not results:
            raise CommandError(f"startup: no startup script named {only} (startup lists them)")
        ctx.startup_results.update({r.name: r for r in results})
        lines = Text()
        for r in results:
            lines.append(f"⚙ {r.name}  {r.label} ({r.seconds:.1f}s)\n", style="sc.success" if r.ok else "sc.warning")
            if r.output.strip():
                lines.append(r.output.rstrip("\n") + "\n", style="sc.muted")
        return lines if results else Text("no startup scripts", style="sc.muted")
    if args:
        raise CommandError("usage: startup [run [NAME] | stop [NAME] | start NAME | restart NAME"
                           " | --folder]")
    found = startupmod.scripts()
    rc = startupmod.rc_path()
    rc_note = (f"rc file: {rc} ({len(startupmod.logical_lines(rc.read_text(encoding='utf-8', errors='replace')))} "
               "commands; source it to run it again)") if rc.is_file() else f"no rc file ({rc}): man startup"
    if not found:
        return Text(f"no startup scripts in {startupmod.folder()}\n{rc_note}", style="sc.muted")
    table = Table(title="Startup scripts", title_style="sc.prompt", border_style="sc.border", header_style="sc.accent")
    for col in ("#", "script", "last run", "set"):
        table.add_column(col)
    plain = []
    supervisor = ctx.supervisor
    for i, script in enumerate(found, 1):
        r = ctx.startup_results.get(script.stem)
        info = startupmod.header(script)
        if info["background"]:
            managed = supervisor.scripts.get(script.stem) if supervisor is not None else None
            job = managed.job if managed is not None else None
            if job is None:
                last = Text("background · not started", style="sc.muted")
            else:
                stopped = managed.stopped and not job.running
                state = "gave up" if managed.gave_up else "stopped" if stopped else job.label
                restarts = " · restarts" if managed.restart else ""
                last = Text(f"background · [{job.number}] {state}{restarts}",
                            style="sc.success" if job.running else "sc.warning")
            table.add_row(str(i), Text(script.name, style="sc.path"), last, "")
            plain.append(f"{i}\t{script.name}\t{last.plain}\t")
            continue
        last = Text("not run this session", style="sc.muted") if r is None else \
            Text(f"{r.label} ({r.seconds:.1f}s)", style="sc.success" if r.ok else "sc.warning")
        sets = ", ".join(sorted(n for n, v in (r.env if r else {}).items() if v is not None))
        table.add_row(str(i), Text(script.name, style="sc.path"), last, sets)
        plain.append(f"{i}\t{script.name}\t{last.plain}\t{sets}")
    return Styled(Group(table, Text(rc_note, style="sc.muted")), "\n".join(plain) + f"\n{rc_note}\n")


JOBS_DOC = """# jobs

List the subshells started with `sub`, select one, rename one, stop one, or clear the finished
ones.

## Usage

```
jobs                 # the table: number, state, elapsed time, command
jobs N               # select job N: attach to a program (Ctrl-] detaches), or show a command's output
jobs -f N            # follow a command job's output as it grows, like tail -f (Ctrl-C stops following)
jobs -r N NAME...    # rename job N: the footer and this table show NAME instead of the command
jobs -r N            # back to its command
jobs -k N            # stop job N
jobs -c              # forget the finished jobs now (their logs stay in ~/.shellcraft/jobs/)
```

**Ctrl-] N** does the same as `jobs N`, at the prompt and inside an attached program, and so does
a click on the job's tab in the footer. `man sub` explains the two kinds of job. A `✦` before a
job's name means an AI opened it, or runs that command for you (`man mcp`).

Selecting a **command** job that is still running follows it: what it printed so far, then each
new line as it comes, until it finishes (its status line ends the view) or you press Ctrl-C, which
returns to the prompt and leaves the job running. A finished command job shows its whole output.

A job that ended is reported at the next prompt (`[N] done: …`, and a command job's output), and
then leaves the stack by itself, as a finished `ls &` leaves bash's job list; `settings
jobs_autoclear off` keeps finished jobs listed until `jobs -c`.
`jobs -f N` follows explicitly. Background commands record their output as it streams, so
`sub tail -f app.log | grep ERROR` (or the same line with `&`) can be followed like any other.
"""


def _jobs_or_refuse(ctx: ShellContext, name: str) -> Any:
    from shellcraft.jobs import depth

    if not ctx.interactive or ctx.jobs is None:
        raise CommandError(f"{name}: only in the interactive shell")
    if depth():
        raise CommandError(f"{name}: only the main ShellCraft starts subshells (this one is [{depth()}]: "
                           f"press {prefix.label()} to go back to it)")
    return ctx.jobs


@builtin("sub", "Open a subshell: a program, a nested ShellCraft, or a command in the background",
         "sub [PROGRAM | shellcraft | COMMAND] [ARGS...]", stateful=True, direct=True, doc=SUB_DOC)
def _sub(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    from shellcraft import jobs as jobmod

    if jobmod.depth() and ctx.interactive:  # inside a subshell: the main shell opens it, in the background
        from shellcraft import alerts as alertmod

        try:
            alertmod.request_sub(args, os.getcwd())
        except OSError as exc:
            raise CommandError(f"sub: cannot reach the main shell: {exc}") from None
        return Text.assemble(("asked the main shell to open ", "sc.muted"),
                             ("sub " + " ".join(args) if args else "sub", "sc.accent"),
                             (f" in the background (it appears in the footer; {prefix.label()} N switches to it)",
                              "sc.muted"))
    table = _jobs_or_refuse(ctx, "sub")
    kind, started = _start_sub(ctx, table, args, os.getcwd())
    return _attach(ctx, started) if kind == "program" else started


def _start_sub(ctx: ShellContext, table: Any, args: list[str], cwd: str, name: str | None = None,
               origin: str = "") -> tuple[str, Any]:
    """Start what `sub ARGS` names: ("program", job) for a program, the login shell or a nested
    ShellCraft; ("note", text) for a builtin or module started as a command job, or a program
    run in the foreground (no pseudo-terminal). `name` and `origin` ("ai") label the job."""
    from shellcraft import jobs as jobmod

    command = " ".join(args) if args else os.path.basename(jobmod.login_shell()[0])
    env: dict[str, str] | None = None
    if not args:
        argv = jobmod.login_shell()
    elif args[0] in ("shellcraft", "sc"):  # a lite ShellCraft (`sc` is short for it)
        argv, env = jobmod.nested_shellcraft(args[1:])
    elif _is_shell_command(ctx, args[0]):
        return "note", _sub_command(ctx, table, command)
    else:
        argv = list(args)
    from shellcraft.alerts import SUB_ENV, with_shim
    from shellcraft.config import config_dir

    env = with_shim(env if env is not None else dict(os.environ), config_dir())  # `alert` on the PATH
    env[SUB_ENV] = f"{table.next_number}:{command}"  # so an alert from inside says which subshell sent it
    try:
        job = table.start_program(argv, command, env=env, cwd=cwd, name=name, origin=origin)
    except jobmod.JobsUnsupported:
        if ctx.ui is not None:
            ctx.ui.console.file.flush()
        code = jobmod.run_foreground(argv, env)
        return "note", (WithStatus("", code) if code else "")
    except FileNotFoundError:
        raise CommandError(f"sub: {args[0]}: not a command, a module or a program on PATH") from None
    return "program", job


def open_requested(ctx: ShellContext) -> list[str]:
    """Open the subshells typed as `sub …` inside a subshell (ctx.jobs.requests), in the
    background: a program starts on its own pseudo-terminal without being attached, a builtin or
    module as a command job. Returns one note per request, for the prompt or the notifications."""
    notes: list[str] = []
    while ctx.jobs is not None and ctx.jobs.requests:
        data = ctx.jobs.requests.pop(0)
        args = [str(a) for a in data.get("args", [])]
        cwd = data.get("cwd") if isinstance(data.get("cwd"), str) and os.path.isdir(data.get("cwd")) else os.getcwd()
        who = f"asked by {data.get('source') or 'a subshell'}"
        line = data.get("line")
        if isinstance(line, str) and line.strip():  # `cmd &` typed inside a subshell
            try:
                notes.append(f"{to_text(start_background(ctx, line)).strip()} ({who})")
            except CommandError as exc:
                notes.append(f"{line} & ({who}): {exc}")
            continue
        asked = f"sub {' '.join(args)}".rstrip() + f" ({who})"
        try:
            kind, started = _start_sub(ctx, ctx.jobs, args, cwd)
        except CommandError as exc:
            notes.append(f"{asked}: {exc}")
            continue
        if kind == "program":
            n = started.number
            notes.append(f"[{n}] {asked} started in the background ({prefix.label()} {n} switches to it)")
        else:
            notes.append(f"{asked}: {to_text(started).strip()}" if started else asked)
    return notes


def start_background(ctx: ShellContext, line: str) -> Any:
    """`LINE &`: a background job, like `sub LINE`. A builtin, module, alias or anything more
    than one simple command (a pipe, `;`, a loop) runs as a command job, from the line as typed
    (quotes kept); a single OS program starts as a program job and is not attached (switch to
    it with Ctrl-] N). Only in the interactive shell."""
    import shlex

    from shellcraft import jobs as jobmod

    if not ctx.interactive or ctx.jobs is None:
        raise CommandError("background jobs (cmd &) only work in the interactive shell")
    if jobmod.depth():  # inside a subshell: the main shell runs it, in the background, under main
        from shellcraft import alerts as alertmod

        try:
            alertmod.request_sub([], os.getcwd(), line=line)
        except OSError as exc:
            raise CommandError(f"&: cannot reach the main shell: {exc}") from None
        return Text.assemble(("asked the main shell to run ", "sc.muted"), (line, "sc.accent"),
                             (f" & in the background (it appears in the footer; {prefix.label()} N switches to it)",
                              "sc.muted"))
    table = _jobs_or_refuse(ctx, "&")
    try:
        words = shlex.split(line, posix=True)
    except ValueError:
        words = line.split()
    simple = not any(op in line for op in ("|", ";", "&&", "||", ">", "<")) and words
    if not simple or _is_shell_command(ctx, words[0]) or words[0] in ("for", "while", "until", "if", "case"):
        return _sub_command(ctx, table, line)
    from shellcraft.alerts import SUB_ENV, with_shim
    from shellcraft.config import config_dir

    argv, env = (jobmod.nested_shellcraft(words[1:]) if words[0] in ("shellcraft", "sc")
                 else (words, dict(os.environ)))
    env = with_shim(env, config_dir())
    env[SUB_ENV] = f"{table.next_number}:{line}"
    try:
        job = table.start_program(argv, line, env=env, cwd=os.getcwd())
    except jobmod.JobsUnsupported:
        raise CommandError("background programs need a pseudo-terminal (pip install pywinpty on Windows)") from None
    except FileNotFoundError:
        raise CommandError(f"{words[0]}: not a command, a module or a program on PATH", 127) from None
    return Text.assemble((f"[{job.number}] ", "sc.muted"), (line, "sc.accent"),
                         (f"  started in the background ({prefix.label()} {job.number} switches to it)", "sc.muted"))


def _is_shell_command(ctx: ShellContext, name: str) -> bool:
    name = name[1:] if name.startswith("\\") else name
    resolved = aliases.expand_command(script_command(name), aliases.get_all(ctx.config)).name
    return resolved in BUILTINS or ctx.registry.get(resolved) is not None


def script_command(name: str) -> Any:
    from shellcraft.script import shparser

    return shparser.Command(name, [])


def _sub_command(ctx: ShellContext, table: Any, line: str) -> Text:
    """A ShellCraft command line as a background job: its own context (no shell state changes,
    output collected), run through the normal pipeline in a worker thread."""
    job = command_job(ctx, table, line)
    return Text.assemble((f"[{job.number}] ", "sc.muted"), (line, "sc.accent"),
                         (f"  running in the background (jobs {job.number} or {prefix.label()} {job.number} shows it)",
                          "sc.muted"))


def command_job(ctx: ShellContext, table: Any, line: str, name: str | None = None, origin: str = "",
                work: Any = None) -> Any:
    """Start `line` as a background command job (see _sub_command) and return the job. `work`, when
    given, is what runs instead of the line: (its context) -> (status, output) (`func run -b`)."""
    import dataclasses

    from shellcraft.context import _direct
    from shellcraft.parser import ParseError
    from shellcraft.pipeline import PipelineError, run_line
    from shellcraft.streams import item_text

    holder: list[Any] = []

    def record(item: Any) -> None:  # output that keeps coming is added to the job as it arrives
        if holder:
            holder[0].append(item_text(item).encode("utf-8", errors="replace"))

    errors: list[str] = []  # its `>&2` output: part of the job's output, never over the prompt
    from shellcraft.streams import StopFlag

    stop = StopFlag()  # `jobs -k` sets it: every pipeline the job runs stops
    background = dataclasses.replace(ctx, runner=_direct, interactive=False, allow_stateful=False,
                                     emit=None, emit_error=None, jobs=None, emit_stderr=None,
                                     stderr_sinks=[errors.append], live=record, stop=stop, positional=[])

    def run() -> tuple[int, str]:
        if work is not None:
            status, text = work(background)
            return status, text + "".join(errors)
        try:
            result = run_line(line, background)
        except (ParseError, PipelineError) as exc:
            return getattr(exc, "status", 1) or 1, "".join(errors) + f"✗ {exc}\n"
        return background.last_status, (to_text(result.output) if result is not None else "") + "".join(errors)

    def started(job: Any) -> None:
        holder.append(job)
        job.stop = stop

    return table.start_command(line, run, before_start=started, name=name, origin=origin)


def _attach(ctx: ShellContext, job: Any) -> Any:
    """Hand the terminal to a program job; back with its status, detached, or switched to
    another tab (Ctrl-] N, or a click on the footer)."""
    assert ctx.jobs is not None
    from shellcraft.jobs import Pages, Terminal

    footer = ctx.footer if settings.get(ctx.config, "footer") else None
    if ctx.ui is not None:
        ctx.ui.console.file.flush()
    terminal = Terminal.real()
    pages = Pages(terminal)  # subshells live on the alternate screen: the prompt comes back untouched
    pages.begin()
    if ctx.recording is not None:
        ctx.recording.note(f"subshell {job.number} attached: {job.command}; what happens inside is not recorded")
    if footer is not None:
        footer.enable()  # drawn on the alternate screen (already enabled when it lives at the prompt too)
    try:
        return _attach_loop(ctx, job, footer, terminal)
    finally:
        if footer is not None and not footer.at_prompt:
            footer.disable()
        pages.end()
        if footer is not None and footer.at_prompt:
            footer.current = 0
            footer.draw()  # back on the main screen: the scroll region and the rows, at the prompt
        terminal.close()


def _attach_loop(ctx: ShellContext, job: Any, footer: Any, terminal: Any) -> Any:
    assert ctx.jobs is not None
    while True:
        how, target = ctx.jobs.attach(job, terminal=terminal, footer=footer)
        if how == "switch":
            if target == 0:
                how = "detached"
            else:
                nxt = ctx.jobs.get(target)
                if nxt is None:
                    raise CommandError(f"jobs: no job {target} (jobs lists them)")
                if nxt.kind != "program" or not nxt.running:
                    return select_job(ctx, target)
                job = nxt
                continue
        if how == "kill":  # Ctrl-] x: end the subshell we are in, back to the prompt
            ctx.jobs.kill(job)
            ctx.jobs.pop_notifications()
            return Text.assemble(("\n", ""), (f"[{job.number}] killed: ", "sc.muted"),
                                 (job.command, "sc.accent"))
        if how == "detached":
            return Text.assemble(("\n", ""), (f"[{job.number}] detached: ", "sc.muted"),
                                 (job.command, "sc.accent"),
                                 (f" keeps running (jobs {job.number} or {prefix.label()} {job.number} to return)",
                                  "sc.muted"))
        # `exit` inside it: the pty closed, and the reader marks the job finished a moment later
        finished = ctx.jobs.wait_finished(job)
        ctx.jobs.pop_notifications()  # its end is reported here, not at the next prompt
        if settings.get(ctx.config, "jobs_autoclear"):  # it leaves the footer now, no × to click
            if finished:
                ctx.jobs.remove(job)
            else:
                job.clear_on_done = True  # still winding down: it leaves when it ends
        status = job.status or 0
        note = Text.assemble(("\n", ""), (f"[{job.number}] ", "sc.muted"), (job.command, "sc.accent"),
                             (f" {job.label if finished else 'done'}", "sc.success" if status == 0 else "sc.error"))
        return WithStatus(note, status) if status else note


def select_job(ctx: ShellContext, number: int, follow: bool | None = None) -> Any:
    """`jobs N` / Ctrl-] N: attach to a program; for a command, follow its output while it runs
    (or with `follow`), else show what it printed."""
    table = _jobs_or_refuse(ctx, "jobs")
    job = table.get(number)
    if job is None:
        raise CommandError(f"jobs: no job {number} (jobs lists them)")
    if job.kind == "program":
        if not job.running:
            raise CommandError(f"jobs: [{number}] {job.command} has finished ({job.label})")
        return _attach(ctx, job)
    if (job.running if follow is None else follow) and ctx.ui is not None:
        return follow_job(ctx, job)
    text = job.output
    head = Text.assemble((f"[{job.number}] ", "sc.muted"), (job.command, "sc.accent"),
                         (f"  {job.label}, {job.elapsed:.1f}s", "sc.muted"))
    body = text if text else "(no output yet)\n" if job.running else "(no output)\n"
    return Styled(Group(head, Text.from_ansi(body.rstrip("\n"))), f"{to_text(head)}\n{body}")


def follow_job(ctx: ShellContext, job: Any, poll: float = 0.2) -> Any:
    """Print a command job's output so far, then each new piece as the job writes it, like
    `tail -f`, until it finishes (its status line closes it) or Ctrl-C (the job keeps running)."""
    import time as _time

    console = ctx.ui.console
    console.print(Text.assemble((f"[{job.number}] ", "sc.muted"), (job.command, "sc.accent"),
                                ("  following (Ctrl-C returns to the prompt; the job keeps running)", "sc.muted")),
                  markup=False, highlight=False)
    position = 0

    def print_new() -> None:
        nonlocal position
        try:
            with open(job.log, "rb") as fh:
                fh.seek(position)
                data = fh.read()
                position = fh.tell()
        except OSError:
            return
        if data:
            console.print(Text.from_ansi(data.decode("utf-8", errors="replace").rstrip("\n")),
                          markup=False, highlight=False)
            console.file.flush()

    # Ctrl-] N runs this inside prompt_toolkit's run_in_terminal, whose event loop owns SIGINT
    # (its handler can't run while this loop blocks it): Python's own handler, while following,
    # turns Ctrl-C into KeyboardInterrupt here, as at the prompt.
    import signal
    import threading

    previous = None
    if threading.current_thread() is threading.main_thread():
        try:
            previous = signal.signal(signal.SIGINT, signal.default_int_handler)
        except (ValueError, OSError):
            previous = None
    try:
        print_new()
        while job.running:
            _time.sleep(poll)
            print_new()
        print_new()
    except KeyboardInterrupt:
        return Text.assemble(("\n", ""), (f"[{job.number}] ", "sc.muted"), (job.command, "sc.accent"),
                             (f"  still running (jobs -f {job.number} to follow again)", "sc.muted"))
    finally:
        if previous is not None:
            signal.signal(signal.SIGINT, previous)
    ctx.jobs.pop_notifications()  # its end is reported here, not again at the next prompt
    style = "sc.success" if job.status == 0 else "sc.error"
    note = Text.assemble((f"[{job.number}] ", "sc.muted"), (job.command, "sc.accent"),
                         (f"  {job.label} ({job.elapsed:.1f}s)", style))
    return WithStatus(note, job.status or 0) if job.status else note


@builtin("jobs", "List, select, rename or stop the subshells started with sub",
         "jobs [N | -f N | -k N | -r N [NAME] | -c]", stateful=True, direct=True, doc=JOBS_DOC)
def _jobs(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    table = _jobs_or_refuse(ctx, "jobs")
    if args and args[0] in ("-c", "--clear"):
        n = table.clear_finished()
        return Text(f"✓ {n} finished job{'s' if n != 1 else ''} cleared", style="sc.success")
    if args and args[0] in ("-f", "--follow"):
        return select_job(ctx, _job_number(args[1:2]), follow=True)
    if args and args[0] in ("-r", "--rename"):
        job = table.get(_job_number(args[1:2]))
        if job is None:
            raise CommandError("jobs: -r needs the number of a job, then its new name (jobs lists them)")
        name = " ".join(args[2:]).strip()
        table.rename(job, name or None)
        if not name:
            return Text(f"✓ [{job.number}] is {job.command} again", style="sc.success")
        return Text.assemble(("✓ [", "sc.success"), (str(job.number), "sc.success"), ("] ", "sc.success"),
                             (job.command, "sc.muted"), (" is now ", "sc.success"), (name, "sc.accent"))
    if args and args[0] in ("-k", "--kill"):
        job = table.get(_job_number(args[1:2]))
        if job is None:
            raise CommandError("jobs: -k needs the number of a job (jobs lists them)")
        if not table.kill(job):
            return Text(f"[{job.number}] {job.command} had already finished", style="sc.muted")
        return Text(f"✓ [{job.number}] {job.command} stopped", style="sc.success")
    if args:
        return select_job(ctx, _job_number(args))
    if not len(table):
        return Text("no subshells: sub PROGRAM, sub shellcraft, or sub COMMAND starts one (man sub)", style="sc.muted")
    tbl = Table(title="Subshells", title_style="sc.prompt", border_style="sc.border", header_style="sc.accent")
    for col in ("#", "state", "time", "kind", "command"):
        tbl.add_column(col, style="sc.muted" if col in ("time", "kind") else "")
    plain = []
    for job in table:
        style = "sc.warning" if job.running else "sc.success" if job.status == 0 else "sc.error"
        ai = getattr(job, "origin", "") == "ai"
        shown = Text(("✦ " if ai else "") + job.title, style="sc.accent")
        if job.name and not ai:
            shown.append(f"  ({job.command})", style="sc.muted")
        tbl.add_row(str(job.number), Text(job.label, style=style), f"{job.elapsed:.0f}s", job.kind, shown)
        plain.append(f"{job.number}\t{job.label}\t{job.elapsed:.0f}s\t{job.kind}\t{job.title}"
                     + (f"\t{job.command}" if job.name and not ai else ""))
    key = prefix.label()
    hint = Text(f"jobs N or {key} N selects · {key} detaches · jobs -r N NAME renames · jobs -k N stops · "
                "jobs -c clears", style="sc.muted")
    return Styled(Group(tbl, hint), "\n".join(plain) + "\n")


def _job_number(words: list[str]) -> int:
    word = (words[0] if words else "").lstrip("%")
    if not word.isdigit():
        raise CommandError(f"jobs: expected a job number, got {words[0] if words else 'nothing'}")
    return int(word)


MODULES_DOC = """# modules

List the modules, and turn them on or off. Modules come from two folders: the bundled one (or
`--modules DIR`) and yours, `~/.shellcraft/modules`, where a module shadows a bundled one of the
same name; the `from` column says which (`user` or `bundled`). A disabled module is not
imported at all: it is not a command, not an MCP tool and has no man page until you enable it
again. The choice is saved in `~/.shellcraft/config.json`, so it lasts across sessions and also
applies to `--mcp` / `--mcp-http` (`mcp restart` passes a change on to a running server).

Below the modules, a **Helpers** table lists the `_name.py` files: they are not commands, the
modules import them. Each one shows whether it has a `_name.md`, which modules import it, and a
description: the first line of prose in `_name.md`, else the first line of its docstring.

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
    folders = " + ".join(str(d) for d in registry.directories if d.is_dir() or d == registry.directory)
    title = f"{'All modules' if show_all else 'Modules'} in {folders}"
    table = Table(title=title, title_style="sc.prompt", border_style="sc.border", header_style="sc.accent")
    if show_all:
        table.add_column("state", justify="center")
    table.add_column("name", style="sc.path")
    table.add_column(".py", justify="center")
    table.add_column(".md", justify="center")
    table.add_column(".skill", justify="center")
    table.add_column("from", style="sc.muted")
    table.add_column("summary")
    ok, missing = Text("✓", style="sc.success"), Text("·", style="sc.muted")
    plain: list[str] = []
    for name in names:
        py = registry.files.get(name)
        spec = registry.get(name)
        has_md = bool(spec.doc_md) if spec else bool(py and py.with_suffix(".md").is_file())
        has_skill = bool(spec.skill) if spec else bool(py and py.with_suffix(".skill").is_file())
        summary = registry.describe(name)
        row = [name, ok, ok if has_md else missing, ok if has_skill else missing, registry.origin(name), summary]
        if show_all:
            if name in disabled:
                state = Text("○ off", style="sc.muted")
            elif spec is None:
                state = Text("⚠ error", style="sc.warning")  # enabled, but failed to load
            else:
                state = Text("● on", style="sc.success")
            row.insert(0, state)
            plain.append(f"{state.plain[2:]:<5}  {name}  {registry.origin(name)}  {summary}".rstrip())
        else:
            plain.append(f"{name}  {registry.origin(name)}  {summary}".rstrip())
        table.add_row(*row, style="sc.muted" if name in disabled else None)
    parts: list[Any] = [table]
    notes: list[str] = []
    from shellcraft.loader import helpers

    found = [h for d in registry.directories for h in helpers(d)]
    if found:
        aid = Table(title="Helpers (_name.py: imported by modules, not commands)", title_style="sc.prompt",
                    border_style="sc.border", header_style="sc.accent")
        aid.add_column("name", style="sc.path")
        aid.add_column(".md", justify="center")
        aid.add_column("used by", style="sc.accent")
        aid.add_column("description")
        for name, py, description, used_by in found:
            has_md = py.with_suffix(".md").is_file()
            aid.add_row(name, ok if has_md else missing, ", ".join(used_by) or Text("—", style="sc.muted"),
                        description or Text("no description (add a docstring or a .md)", style="sc.muted"))
            plain.append(f"helper  {name}  {description}".rstrip()
                         + (f"  (used by {', '.join(used_by)})" if used_by else ""))
        parts.append(aid)
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
    from shellcraft.banner import render_banner

    if any(a not in ("-c", "--clear") for a in args):
        raise CommandError("usage: banner [-c]")
    if ctx.ui is None:
        raise CommandError("banner: needs the interactive shell")
    if args:
        from shellcraft.output import clear_screen

        clear_screen(ctx.ui)
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


FUNCTION_DOC = r"""# function

Shell functions, as in bash: a name for a list of commands that takes arguments.

## Usage

```
NAME() { COMMANDS; }               # define (also over several lines)
function NAME { COMMANDS; }        # the same, with the keyword
NAME ARG...                        # call it, like any command
declare -f [NAME...]               # show the definitions (declare -F: just the names)
unset -f NAME...                   # forget functions
```

Inside the body:

| | |
| --- | --- |
| `$1` … `$9` | The arguments (empty when not given). |
| `$#` | How many arguments. |
| `$@`, `$*` | All the arguments. Quoted, `"$@"` is one word per argument; `"$*"` is one word. |
| `shift [N]` | Drop the first N arguments (default 1): `$2` becomes `$1`. |
| `${1:-word}`, `${NAME-word}` | A default: `word` when the variable is unset (`-`) or unset or empty (`:-`). |
| `local NAME[=VALUE]` | A variable of this call only: its old value comes back when the function returns. |
| `return [N]` | Leave the function with status N (default: the last command's status). |

The body uses bash syntax: `if`, `for`, `while`, `case`, `&&` / `||`, `$( … )`, `$(( … ))`.
Variables that are not `local` are the shell's own, as in bash. A function can call others
and itself, up to 100 calls deep.

A function is a command: `NAME | grep x`, `NAME > file`, `echo text | NAME` (its commands read
that input) and `NAME && echo ok` all work. Its output is what its commands print.

A function takes precedence over builtins, modules and programs of the same name, as in bash,
except `exit`, `export`, `unset`, `source`, `local`, `declare`, `alias` and `unalias`, which can't
be replaced. `\NAME` runs the builtin or module itself, skipping both aliases and functions.

Functions last for the session. Put them in `~/.shellcraft/rc` to have them every time. They are
never available to AI clients over MCP.

## Examples

```
greet() { echo "hello $1, you gave $# argument(s)"; }
greet Ada

backup() {
  local stamp=$(date +%Y%m%d)
  cp -r "$1" "$1.$stamp" && echo "saved $1.$stamp"
}

countdown() { local n=$1; while [ $n -gt 0 ]; do echo $n; n=$((n - 1)); done; }
countdown 3 | sort -n
```
"""

SELECT_DOC = """# select

A numbered menu, as in bash: pick a choice by number, the body runs with it, and the menu asks
again until `break`.

## Usage

```
select NAME in WORD...; do COMMANDS; done
```

The words are read like a `for` list (`$(…)`, `{a,b}`, globs). Each round, ShellCraft asks with
`$PS3` (default `#? `); NAME is the chosen word, or empty when the answer is not one of the
numbers, and `$REPLY` is exactly what was typed. An empty answer shows the menu again. `break`
leaves the menu; Ctrl-D does too, and Ctrl-C leaves it with status 130.

Only the interactive shell can ask, so `select` is refused over MCP and anywhere without a
terminal. It works in functions and in `~/.shellcraft/rc`.

## Example

```
PS3="Deploy which environment? "
select env in dev staging prod; do
  if [ -n "$env" ]; then echo "deploying to $env"; break; fi
  echo "pick 1, 2 or 3 (you typed $REPLY)"
done
```
"""

builtin("select", "Ask the user to pick from a numbered menu", "select NAME in WORD...; do …; done",
        doc=SELECT_DOC)(_keyword("select"))

builtin("function", "Define a shell function: NAME() { …; }", "NAME() { COMMANDS; } | function NAME { COMMANDS; }",
        doc=FUNCTION_DOC)(_keyword("function"))

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

Give a command a shorter name, or change what a command does by default. Aliases are kept in
`~/.shellcraft/aliases`, a plain text file like bash's `~/.bash_aliases`, so they last across
sessions and you can edit them in any editor.

## Usage

```
alias                      # list every alias
alias NAME                 # show one alias
alias NAME='COMMAND ARGS'  # define (or replace) an alias
alias --file               # where the aliases file is
alias -g NAME='COMMAND'    # in a profile: define it in the default setup (shared) instead
unalias NAME...            # remove aliases
unalias -a                 # remove every alias
```

## In a profile

While a profile is active (`man profile`), `alias NAME=…` and `unalias NAME` change the profile's
own aliases file, which is added on top of the shared one (a profile alias wins); `alias` marks
them `# profile NAME`. `alias -g` and `unalias -g` change the shared file instead.

## The aliases file

`~/.shellcraft/aliases` has one alias per line, in the same form as `~/.bash_aliases`:

```
# my shortcuts
alias ll='ls -l'
alias errors='grep -i error'
alias q="grep -F 'x'"
```

Comments and blank lines are kept. Edit the file in any editor: the change applies to the next
command, no restart or `reload` needed. `alias NAME=…` and `unalias NAME` from the shell edit the
same file and only touch the lines of the names you give. A line that can't be used (a pipe in
the value, an unclosed quote) is skipped, and `alias` lists it with its line number so you can
fix it. You can copy alias lines from your `~/.bash_aliases`; those that use `|`, `>` or `;` are
reported, since a ShellCraft alias is a single command.

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


@builtin("alias", "Define or list command aliases", "alias [-g] [NAME[=COMMAND]] | alias --file",
         stateful=True, doc=ALIAS_DOC)
def _alias(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    global_ = args[:1] in (["-g"], ["--global"])  # a profile is active: write the base file instead
    if global_:
        args = args[1:]
    defined = aliases.get_all(ctx.config)
    if args in (["--file"], ["-f"]):
        return f"{aliases.target(ctx.config, global_)}\n"
    if not args:
        in_profile = getattr(ctx.config, "profile", None)
        def mark(n: str) -> str:
            return f"   # profile {in_profile}" if in_profile and aliases.defined_in(ctx.config, n) == "profile" else ""

        listing = "".join(f"alias {n}={_quote(v)}{mark(n)}\n" for n, v in sorted(defined.items()))
        problems = aliases.file_errors(ctx.config)
        if not problems:
            return listing
        note = "\n".join(f"⚠ {aliases.path().name} {p} (skipped)" for p in problems)
        return Styled(Group(Text(listing.rstrip("\n")), Text(note, style="sc.warning")),
                      listing + "".join(f"# {p} (skipped)\n" for p in problems))
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
    where = aliases.target(ctx.config, global_)
    try:
        aliases.set_alias(ctx.config, name, value, global_)
    except aliases.AliasError as exc:
        raise CommandError(str(exc)) from None
    except OSError as exc:
        raise CommandError(f"alias: cannot write {where}: {exc}") from None
    return _saved(ctx, Text.assemble(("✓ ", "sc.success"), ("alias ", ""), (name, "sc.accent"),
                                     (f" → {value}", ""), (f"  (saved in {where})", "sc.muted")))


@builtin("unalias", "Remove command aliases", "unalias [-g] NAME... | unalias [-g] -a", stateful=True,
         doc=ALIAS_DOC)
def _unalias(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    global_ = args[:1] in (["-g"], ["--global"])
    if global_:
        args = args[1:]
    if not args:
        raise CommandError("unalias: usage: unalias [-g] NAME... | unalias [-g] -a")
    if args == ["-a"]:
        count = aliases.remove_all(ctx.config, global_)
        return _saved(ctx, Text.assemble(("✓ ", "sc.success"), f"removed {count} alias(es)"))
    missing = [n for n in args if not aliases.remove(ctx.config, n, global_)]
    removed = [n for n in args if n not in missing]
    if removed:
        msg = _saved(ctx, Text.assemble(("✓ ", "sc.success"), f"removed {', '.join(removed)}"))
    if missing:
        base = [n for n in missing if not global_ and aliases.defined_in(ctx.config, n) == "base"]
        hint = f" ({', '.join(base)}: from the default setup; unalias -g removes it there)" if base else ""
        raise CommandError(f"unalias: {', '.join(missing)}: not found{hint}")
    return msg


def _quote(value: str) -> str:
    """Quote an alias value so the line can be pasted back into the shell."""
    return aliases.quote(value)


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
        path = profiles.history_file(ctx.config)  # the active profile's own history, or the base one
        try:
            path.write_text("", encoding="utf-8")
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise CommandError(f"history: cannot clear {path}: {exc.strerror or exc}") from None
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

    path = profiles.history_file(ctx.config)
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
mcp show                     # the token and the `claude mcp add` line for it
mcp token rotate             # a new token, saved (a running server restarts with it)
mcp log [N]                  # the last N lines of its log (default 20)
```

## Options

| Option | Meaning |
| --- | --- |
| `--allow-system` | Let MCP clients fall back to OS programs (off by default, like `--mcp-http`). |

The server only listens on this machine (`127.0.0.1`, `localhost` or `::1`), and every request
must carry a **bearer token**, so other local users and processes can't use it. The token is made
once and **saved** in `~/.shellcraft/config.json` (owner-only, like an API key), so every `mcp
start`, in any session, uses the same one and a client added once keeps working. `mcp show` prints
it with the `claude mcp add` line; `mcp token rotate` makes a new one (do it if the old one
leaked) and prints what to run in your client:

```
claude mcp remove shellcraft
claude mcp add --transport http shellcraft http://127.0.0.1:38765/mcp --header "Authorization: Bearer NEWTOKEN"
```

## The co-pilot tools

Every ShellCraft MCP server (this one, or the `--mcp` one a client starts) can reach the
interactive shell through its private link, published in `~/.shellcraft/mainlink.json` (readable
by you only, removed when the shell exits): `get_recent_output` (your screen), `get_jobs` and
`get_job_output`, `get_history` (keys redacted), `get_clip` / `set_clip` (what Alt-v pastes),
`open_subshell` (a `✦ AI · name` subshell that prints the AI's brief; `man sub`), and
`run_in_shell`: a read-only command runs at once as a `✦ AI:` background job with its trace above
your prompt; so does anything while `settings ai_run on` (the AI can ask for it once with
`request_session_grant`); the rest waits in the review queue until `review approve` (`man review`).
The `ai/` folder of the repository has agents and skills that teach an AI to use them well.

The address of the last `mcp start` is saved too, for `mcp show` and the next start.
Its log is `~/.shellcraft/mcp-http.log`. API keys
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


MCP_TOKEN_KEY = "SHELLCRAFT_MCP_TOKEN"  # the server's bearer token, kept in config.json like an API key


def _mcp_token(ctx: ShellContext, rotate: bool = False) -> str:
    """The MCP server's bearer token: the saved one, so clients keep working across `mcp start`s
    and shell sessions; a new one the first time, or with `rotate`. Kept like an API key, in
    config.json."""
    import secrets

    token = None if rotate else settings.env_get(ctx.config, MCP_TOKEN_KEY)
    if not token:
        token = secrets.token_urlsafe(24)
        try:
            settings.env_set(ctx.config, MCP_TOKEN_KEY, token)
            save_config(ctx.config)
        except OSError as exc:
            raise CommandError(f"mcp: the token could not be saved: {exc}") from None
    return token


def _mcp_address(ctx: ShellContext) -> str:
    """The address `mcp start` used last (saved), else the default."""
    from shellcraft import mcp_child

    section = ctx.config.get("mcp")
    address = section.get("address") if isinstance(section, dict) else None
    return address if isinstance(address, str) and address else mcp_child.DEFAULT_ADDRESS


def _mcp_url(address: str) -> str:
    from shellcraft import mcp_child

    host, _, port = address.rpartition(":")
    return mcp_child._url(host.strip("[]") or "127.0.0.1", int(port)) if port.isdigit() else f"http://{address}/mcp"


def _mcp_add_line(url: str, token: str) -> str:
    return f"claude mcp add --transport http shellcraft {url} --header \"Authorization: Bearer {token}\""


def _unwrapped(text: Text) -> Any:
    """Printed without Rich's line breaks: the `claude mcp add` line must copy as one line (the
    terminal still folds it on screen)."""
    from shellcraft.context import SoftWrapped

    return SoftWrapped(text)


def _mcp_running(ctx: ShellContext) -> Any:
    """The server started with `mcp start`, or None; forgets one that has exited by itself."""
    child = ctx.mcp_http
    if child is not None and not child.running():
        child.stop()
        ctx.mcp_http = None
        return None
    return child


@builtin("mcp", "Start or stop the MCP HTTP server in the background",
         "mcp [start [HOST:PORT] | stop | restart | show | token rotate | log [N]]", stateful=True, doc=MCP_DOC)
def _mcp(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    from shellcraft import mcp_child

    if ctx.lite:
        raise CommandError("mcp: not in a lite shell (sub shellcraft); run it in the main ShellCraft "
                           f"({prefix.label()} goes back to it)")
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

    if action == "show" and not rest:  # the token and the line to add the server to a client
        url = child.url if child is not None else _mcp_url(_mcp_address(ctx))
        token = child.token if child is not None else _mcp_token(ctx)
        state = Text.assemble(("● ", "sc.success"), "running at ", (url, "sc.accent")) if child is not None else \
            Text.assemble(("○ ", "sc.muted"), "not running (mcp start); it will listen at ", (url, "sc.accent"))
        return _unwrapped(Text.assemble(state, "\n", ("  token ", "sc.muted"), (token, "sc.accent"),
                             ("  (saved: the same after every mcp start)\n", "sc.muted"),
                             ("  add it to a client with:\n  ", "sc.muted"), (_mcp_add_line(url, token), "sc.path")))

    if action == "token" and rest == ["rotate"]:  # a new token, saved; a running server takes it at once
        token = _mcp_token(ctx, rotate=True)
        url = _mcp_url(_mcp_address(ctx))
        note = ""
        if child is not None:
            address = f"{mcp_child._url_host(child.host)}:{child.port}"
            allow_system = child.allow_system
            child.stop()
            ctx.mcp_http = None
            try:
                child = ctx.runner("restarting the MCP server with the new token…",
                                   lambda: mcp_child.start(address, ctx.registry.directory, allow_system,
                                                           lambda c: setattr(ctx, "mcp_http", c), token))
            except Exception as exc:  # noqa: BLE001
                ctx.mcp_http = None
                raise CommandError(f"mcp: the new token is saved, but the server did not restart: {exc}") from None
            ctx.mcp_http = child
            url, note = child.url, "  (the running server was restarted with it)"
        return _unwrapped(Text.assemble(("✓ ", "sc.success"), "new MCP token saved", (note + "\n", "sc.muted"),
                             ("  token ", "sc.muted"), (token, "sc.accent"), "\n\n",
                             ("You need to remove and add the MCP server with the new bearer token:\n", "sc.warning"),
                             ("  claude mcp remove shellcraft\n", "sc.path"),
                             (f"  {_mcp_add_line(url, token)}", "sc.path")))

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
            address = addresses[0] if addresses else _mcp_address(ctx)
            token = _mcp_token(ctx)  # the saved one: clients keep working
        def track(spawned: Any) -> None:
            ctx.mcp_http = spawned

        try:
            child = ctx.runner("starting the MCP server…",
                               lambda: mcp_child.start(address, ctx.registry.directory, allow_system, track, token))
        except Exception as exc:  # StartError; AddressError from parse_http_address; OSError from Popen
            ctx.mcp_http = None  # a failed start has already stopped its process
            raise CommandError(f"mcp: {exc}") from None
        ctx.mcp_http = child
        if action == "start":  # the address too, so `mcp show` and the next start use it
            section = ctx.config.get("mcp") if isinstance(ctx.config.get("mcp"), dict) else {}
            if section.get("address") != address:
                ctx.config["mcp"] = {**section, "address": address}
                try:
                    save_config(ctx.config)
                except OSError:
                    pass
        return _unwrapped(Text.assemble(("● ", "sc.success"), f"MCP server {action}ed at ", (child.url, "sc.accent"),
                             (f"  pid {child.pid}{', OS commands allowed' if allow_system else ''}"
                              f" · log: {mcp_child.log_path()}\n", "sc.muted"),
                             ("  token ", "sc.muted"), (child.token, "sc.accent"),
                             ("   add it to a client with:\n  ", "sc.muted"),
                             (_mcp_add_line(child.url, child.token), "sc.path")))

    if action == "stop" and not rest:
        if child is None:
            raise CommandError("mcp: the MCP server isn't running")
        code = child.stop()
        ctx.mcp_http = None
        return Text.assemble(("■ ", "sc.muted"), "MCP server stopped", (f"  (exit code {code})", "sc.muted"))

    if action == "log" and len(rest) <= 1 and all(r.isdigit() for r in rest):
        tail = mcp_child.log_tail(int(rest[0]) if rest else 20)
        return (tail + "\n") if tail else Text("(the MCP server's log is empty)", style="sc.muted")

    raise CommandError("usage: mcp [start [HOST:PORT] [--allow-system] | stop | restart | show | token rotate"
                       " | log [N]]")


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
settings prefix_key [KEY]     # the subshell prefix key (Ctrl-] by default); alone: the choices
settings editor [COMMAND]     # the editor for func edit / profile edit (code, vim, nano…); alone: the one in use
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

**Where keys are kept.** A key you set is kept in `~/.shellcraft/config.json`, in plain text:
mode 600 keeps other users out on Linux and macOS (it does nothing on Windows). A key you export
in your own shell is never stored and always wins, so if you'd rather not keep keys in a file,
export them in your shell profile instead. (A key an older ShellCraft kept in the OS keyring shows
as "set it again".)
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
         "settings [KEY on|off|toggle] | settings NAME [VALUE] | settings prefix_key [KEY] | "
         "settings editor [COMMAND] | settings reset KEY",
         stateful=True, doc=SETTINGS_DOC)
def _settings(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    keys = settings.env_settings(ctx.registry)
    if not args:
        return _settings_table(ctx, keys)

    if args[0] == prefix.CONFIG_KEY or args[:2] == ["reset", prefix.CONFIG_KEY]:
        return _prefix_key(ctx, args)
    if args[0] == editormod.CONFIG_KEY or args[:2] == ["reset", editormod.CONFIG_KEY]:
        return _editor_setting(ctx, args)

    if args[0] == "reset":
        if len(args) != 2:
            raise CommandError("usage: settings reset KEY")
        if args[1] in ctx.env_vars and args[1] not in keys:  # a session variable: as `env unset`
            how = envvars.unset_var(ctx, args[1])
            return Text.assemble(("✓ ", "sc.success"), (args[1], "sc.path"),
                                 " restored to your shell's value" if how == "restored" else " removed")
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
        try:
            settings.env_set(ctx.config, entry.name, value.strip())
        except OSError as exc:
            raise CommandError(f"settings: {exc}") from None
        return _store_env(ctx, entry, "set", value.strip())

    if args[0] == AI_RUN_KEY:  # the session grant: never saved
        if len(args) != 2:
            raise CommandError(f"usage: settings {AI_RUN_KEY} on|off|toggle")
        value = not ctx.ai_run if args[1].lower() == "toggle" else settings.parse_bool(args[1])
        if value is None:
            raise CommandError(f"settings: expected on, off or toggle, got '{args[1]}'")
        ctx.ai_run = bool(value)
        state = ("On", "sc.success") if value else ("Off", "sc.error")
        note = "  (this session only; ✦ run shows in the prompt while it is on)" if value else "  (this session only)"
        return Text.assemble(("✓ ", "sc.success"), f"{AI_RUN_LABEL} ", state, (note, "sc.muted"))
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


PREFIX_LABEL = "Subshell prefix key"
PREFIX_DESCRIPTION = ("The key before a digit (switch), x (end), c (fold), v (paste) or alone (back to the prompt), "
                      "at the prompt and inside subshells; pressed twice it reaches the program. Choices: "
                      + ", ".join(prefix.KEYS) + ".")


def _prefix_key(ctx: ShellContext, args: list[str]) -> Any:
    """`settings prefix_key` (show it and the choices), `settings prefix_key KEY`, `settings reset prefix_key`."""
    if args == [prefix.CONFIG_KEY]:
        table = Table(title=f"{PREFIX_LABEL}: {prefix.label()}", title_style="sc.prompt", border_style="sc.border",
                      header_style="sc.accent")
        table.add_column("key", style="sc.path", no_wrap=True)
        table.add_column("")
        table.add_column("what it shadows", style="sc.muted")
        for name, spec in prefix.KEYS.items():
            table.add_row(name, Text("● in use", style="sc.success") if name == prefix.name() else "", spec.note)
        plain = "".join(f"{name}\t{'*' if name == prefix.name() else ''}\t{spec.note}\n"
                        for name, spec in prefix.KEYS.items())
        return Styled(table, plain)
    if args[0] == "reset":
        ctx.config.pop(prefix.CONFIG_KEY, None)
        chosen = prefix.set_key(prefix.DEFAULT)
    elif len(args) == 2:
        try:
            chosen = prefix.set_key(args[1])
        except ValueError as exc:
            raise CommandError(f"settings: {exc}") from None
        if chosen == prefix.DEFAULT:
            ctx.config.pop(prefix.CONFIG_KEY, None)
        else:
            ctx.config[prefix.CONFIG_KEY] = chosen
    else:
        raise CommandError(f"usage: settings {prefix.CONFIG_KEY} [{'|'.join(prefix.KEYS)}]")
    msg = Text.assemble(("✓ ", "sc.success"), f"{PREFIX_LABEL} set to ", (prefix.label(), "sc.accent"),
                        (f"  ({prefix.KEYS[chosen].note}; pressed twice it reaches the program)", "sc.muted"))
    try:
        save_config(ctx.config)
    except OSError as exc:
        msg.append(f"  (not saved: {exc})", style="sc.warning")
    return msg


EDITOR_LABEL = "Editor"
EDITOR_DESCRIPTION = ("What func edit, func new -e and profile edit open files in: a terminal editor (vim, nano…) "
                      "runs in a subshell, a window editor (code, subl…) in its own window, waited for. Unset: "
                      "$VISUAL, $EDITOR, then nano (notepad on Windows).")


def _editor_setting(ctx: ShellContext, args: list[str]) -> Any:
    """`settings editor` (show it and the editors found), `settings editor CMD…`, `settings reset editor`."""
    if args == [editormod.CONFIG_KEY]:
        argv = editormod.command(ctx.config)
        lines = [Text.assemble(("editor: ", "sc.muted"), (" ".join(argv), "sc.accent"),
                               (f"  ({editormod.label(argv)}, from {editormod.source(ctx.config)}; "
                                f"{'window' if editormod.is_window(argv) else 'terminal'})", "sc.muted"))]
        available = editormod.found()
        if available:
            lines.append(Text("found here: " + ", ".join(f"{k} ({v})" for k, v in available.items()), style="sc.muted"))
        lines.append(Text("settings editor NAME chooses one (e.g. settings editor code); settings reset editor "
                          "goes back", style="sc.muted"))
        return Text("\n").join(lines)
    if args[0] == "reset":
        ctx.config.pop(editormod.CONFIG_KEY, None)
        message = Text.assemble(("✓ ", "sc.success"), "editor reset: ", (" ".join(editormod.command(ctx.config)),
                                                                         "sc.accent"),
                                (f" (from {editormod.source(ctx.config)})", "sc.muted"))
    else:
        value = " ".join(args[1:]).strip()
        if not value:
            raise CommandError("usage: settings editor COMMAND (e.g. code, vim, nano, \"code --wait\")")
        ctx.config[editormod.CONFIG_KEY] = value
        argv = editormod.command(ctx.config)
        note = "" if shutil.which(argv[0]) else "  (not found on the PATH yet)"
        how = "a window editor, waited for" if editormod.is_window(argv) else "opened in a subshell"
        message = Text.assemble(("✓ ", "sc.success"), "editor set to ", (value, "sc.accent"),
                                (f"  ({editormod.label(argv)}, {how})", "sc.muted"), (note, "sc.warning"))
    try:
        save_config(ctx.config)
    except OSError as exc:
        message.append(f"  (not saved: {exc})", style="sc.warning")
    return message


AI_RUN_KEY = "ai_run"
AI_RUN_LABEL = "AI runs any command"
AI_RUN_DESCRIPTION = ("An AI client may run any command here at once (its trace shows above the prompt). "
                      "Off: it runs read-only commands at once and asks for the rest in the review queue. "
                      "Never saved: off at every start; the AI can ask for it (request_session_grant).")


def _settings_table(ctx: ShellContext, keys: dict[str, settings.EnvEntry]) -> Table:
    current = profiles.active(ctx.config)
    title = f"Settings · profile {current} (theme, settings and prompt changes go to it)" if current else "Settings"
    table = Table(title=title, title_style="sc.prompt", border_style="sc.border", header_style="sc.accent")
    table.add_column("key", style="sc.path", no_wrap=True)
    table.add_column("setting")
    table.add_column("value", justify="center", overflow="fold")  # long values wrap, never cut
    table.add_column("description", style="sc.muted")
    rows = list(settings.SETTINGS.values())
    for i, s in enumerate(rows):
        on = settings.get(ctx.config, s.key)
        value = Text("On", style="sc.success") if on else Text("Off", style="sc.error")
        table.add_row(s.key, s.label, value, s.description)
    table.add_row(prefix.CONFIG_KEY, PREFIX_LABEL, Text(prefix.label(), style="sc.accent"), PREFIX_DESCRIPTION)
    table.add_row(editormod.CONFIG_KEY, EDITOR_LABEL, Text(" ".join(editormod.command(ctx.config)), style="sc.accent",
                                                           overflow="fold"), EDITOR_DESCRIPTION)
    on = bool(getattr(ctx, "ai_run", False))
    session_vars = envvars.session(ctx)
    table.add_row(AI_RUN_KEY, f"{AI_RUN_LABEL} (this session)", Text("On", style="sc.success") if on
                  else Text("Off", style="sc.error"), AI_RUN_DESCRIPTION, end_section=bool(keys or session_vars))
    styles = {"set": "sc.success", "environment": "sc.accent", "unset": "sc.muted"}
    last_key = sorted(keys, key=lambda n: (keys[n].module.lower(), n))[-1] if keys else None
    for name in sorted(keys, key=lambda n: (keys[n].module.lower(), n)):
        entry = keys[name]
        state, text = settings.env_status(ctx.config, name)
        table.add_row(name, entry.label, Text(text, style=styles[state]), entry.setting.description,
                      end_section=bool(session_vars) and name == last_key)
    for e in session_vars:  # `env set`, `export`, the rc file: this session only
        where = f"from {e.origin}" + ("; overrides your shell's value" if envvars.overrides(e) else "")
        table.add_row(e.name, f"Environment (this session) · {where}",
                      Text(envvars.shown(e), style="sc.accent", overflow="fold", justify="left"),
                      "Set with env set (or export); gone at exit. Put the env set line in ~/.shellcraft/rc "
                      "to set it at every start; env unset NAME (or settings reset NAME) removes it.")
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
