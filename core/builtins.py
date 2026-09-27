"""Built-in commands. Each takes (ctx, args, stdin) and returns str or a Rich renderable."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from rich.console import Group
from rich.markdown import Markdown
from rich.table import Table
from rich.text import Text

from core.config import save_config
from core.context import CommandError, ShellExit
from core.themes import all_themes

if TYPE_CHECKING:
    from core.context import ShellContext


@dataclass(frozen=True)
class Builtin:
    fn: Callable[[ShellContext, list[str], str], Any]
    summary: str
    usage: str
    stateful: bool = False  # changes shell state; disabled for MCP pipelines


BUILTINS: dict[str, Builtin] = {}


def builtin(name: str, summary: str, usage: str, stateful: bool = False):
    def register(fn):
        BUILTINS[name] = Builtin(fn, summary, usage, stateful)
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


@builtin("echo", "Print arguments to the output stream", "echo [TEXT...]")
def _echo(ctx: ShellContext, args: list[str], stdin: str) -> str:
    return " ".join(args) + "\n"


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
    for name in sorted(BUILTINS):
        table.add_row(name, "builtin", BUILTINS[name].summary)
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
    name = args[0]
    spec = ctx.registry.get(name)
    if spec is not None:
        if not spec.doc_md:
            raise CommandError(f"man: no manual entry for {name} (missing {name}.md)")
        doc = spec.doc_md
    elif name in BUILTINS:
        b = BUILTINS[name]
        doc = f"# {name}\n\n{b.summary}.\n\n## Usage\n\n```\n{b.usage}\n```\n\n*ShellCraft built-in command.*\n"
    else:
        raise CommandError(f"man: no manual entry for {name}")
    return Markdown(doc) if ctx.ui else doc


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
