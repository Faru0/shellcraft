"""func: named, on-demand scripts of ShellCraft lines (shellcraft.funcs), managed per profile."""

from __future__ import annotations

import json
import shlex
import tempfile
from pathlib import Path
from typing import Any

from rich.console import Group
from rich.table import Table
from rich.text import Text

from shellcraft import funcs
from shellcraft.builtins import builtin
from shellcraft.commands._io import user_path
from shellcraft.context import CommandError, ShellContext, Styled, WithStatus

USAGE = ("func [list | show NAME | check NAME | run [-i] [-b] [-q|-v] NAME [ARG...] | new NAME [-e] [--save] [-g] [-d DESC] "
         "[--system] [--quiet] [--command] | save NAME [-g] | edit NAME | add NAME COMMAND | insert NAME N COMMAND | "
         "rm NAME N | move NAME FROM TO | set NAME FIELD VALUE | rename OLD NEW | copy SRC DST [-g] | "
         "delete NAME [-y] | export NAME|--all [FILE] | import FILE [-f] [-g] | path [NAME]]")

FUNC_DOC = """# func

Named scripts of ShellCraft lines, run when you call them: like the rc file, on demand. A func
runs its steps one after another in your shell, as if you typed them (a `cd` or `env set`
stays), with `$1`, `$@` and `$#` set to its arguments, and stops at the first step that fails.
It is not a pipe stage: it runs on its own line.

## Usage

```
func                                   # the funcs you can run (same as func list)
func show NAME                         # its header and every step, numbered (also: func view)
func check NAME                        # read it and run nothing: lines that won't parse, unknown commands
func run NAME [ARG...]                 # run it
func run -i NAME [ARG...]              # isolated: the directory, variables, env vars, aliases, theme, settings and set options come back afterwards
func run -b NAME [ARG...]              # in the background, as a job in the footer (always isolated; jobs N shows its output)
func run -q NAME / -v NAME             # quiet / show this time, whatever its header says
func new NAME                          # guided: the header, then the steps one per line (an empty line ends)
func new NAME -e                       # write it in your editor instead (settings editor)
func new NAME --save                   # keep it on disk (the active profile's, or -g the default's); else this session only
func save NAME [-g]                    # put a session func on disk
func edit NAME                         # open it in your editor
func add NAME 'COMMAND'                # append a step; insert NAME N 'COMMAND' / rm NAME N / move NAME FROM TO
func set NAME FIELD VALUE              # description, system (yes/no), output (show/quiet), command (yes/no), usage
func rename OLD NEW · copy SRC DST [-g] · delete NAME [-y]
func export NAME|--all [FILE] · import FILE [-f] [-g]
func path [NAME]                       # where it lives
```

## The file

```
# func: recon
# description: Quick domain recon: certificates, DNS, owners
# system: no
# output: show
# command: yes
# usage: recon DOMAIN
queryCert -s $1 > subs-$1.txt
queryDns $1
@ cat subs-$1.txt | wc -l
echo "recon of $1 done"
```

| Header | Meaning |
| --- | --- |
| `description` | One line, shown by `func list` |
| `system` | `yes`: it runs OS programs, so it is refused while OS commands are off (`settings system_commands on`) |
| `output` | `show`: each step's output prints. `quiet`: only `echo` / `printf` steps and steps starting with `@` print; errors always do |
| `command` | `yes`: typing `recon example.com` runs it, besides `func run recon example.com`. Builtins and modules with the same name win |
| `usage` | How to call it, shown by `func list` and `func show` |

The command lines themselves are never printed (as in a bash script); a muted line ends the run:
`✓ recon: 4 steps, 2.3 s`, or which step failed and why. A step is a whole command: a
`for … done` over several lines is one step. Other `#` lines are comments.

## Where funcs live

`~/.shellcraft/funcs/` holds the default setup's funcs, available in every profile;
`~/.shellcraft/profiles/NAME/funcs/` holds a profile's, which win over a default one of the same
name. `func new --save` and `func save` write to the active profile (`-g`: the default setup). A
func made without `--save` lasts until you leave ShellCraft. `func list` shows where each comes
from.

## Checking a func

`func check NAME` reads the func and runs nothing. Each problem is a line `NAME:LINE: message`,
where LINE is the line in the func's file (the header lines count), so it matches what
`func edit` shows. A line that won't parse (a `for` with no `done`, a colon block indented
wrongly) is an error; a command nothing answers to is a warning. The status is 0 when it is
clean, 1 for warnings only, 2 for errors. `source -n FILE` does the same for any file.

Funcs are never available to AI clients over MCP.

## Examples

```
func new recon --save -d "domain recon" --command
func add recon 'queryDns $1'
func run recon example.com
recon example.com
func run -b recon example.com
func show recon
```
"""


@builtin("func", "Named scripts of ShellCraft lines, run on demand (list, show, run, new, edit…)", USAGE,
         category="shell", stateful=True, doc=FUNC_DOC)
def func_cmd(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    action, rest = (args[0], args[1:]) if args else ("list", [])
    handler = ACTIONS.get(action)
    if handler is None:
        raise CommandError(f"func: unknown action '{action}'; usage: {USAGE}")
    try:
        return handler(ctx, rest)
    except funcs.FuncError as exc:
        raise CommandError(f"func: {exc}") from None
    except OSError as exc:
        raise CommandError(f"func: {exc}") from None


def _flags(args: list[str], *names: str, values: tuple[str, ...] = (), stop_at_positional: bool = False
           ) -> tuple[list[str], dict[str, Any]]:
    found: dict[str, Any] = {}
    positional: list[str] = []
    words = list(args)
    i = 0
    while i < len(words):
        word = words[i]
        if stop_at_positional and positional:
            positional.extend(words[i:])  # `func run NAME -x`: -x belongs to the func
            break
        if word in values:
            if i + 1 >= len(words):
                raise CommandError(f"func: {word} needs a value")
            found[word] = words[i + 1]
            i += 2
            continue
        if word in names:
            found[word] = True
        elif word.startswith("-") and len(word) > 1 and not stop_at_positional:
            raise CommandError(f"func: unknown switch {word}")
        elif word.startswith("-") and len(word) > 1:
            raise CommandError(f"func run: unknown switch {word} (the switches come before NAME)")
        else:
            positional.append(word)
        i += 1
    return positional, found


def _where(func: funcs.Func) -> str:
    return {"session": "session", "default": "default"}.get(func.where, f"◆ {func.where}")


# ── list / show / path ───────────────────────────────────────────────────────

def _list(ctx: ShellContext, args: list[str]) -> Any:
    found = funcs.available(ctx)
    if not found:
        return Text("no funcs yet: func new NAME makes one (man func)", style="sc.muted")
    table = Table(title="Funcs", title_style="sc.prompt", border_style="sc.border", header_style="sc.accent")
    table.add_column("name", style="sc.path", no_wrap=True)
    table.add_column("where", style="sc.muted", no_wrap=True)
    table.add_column("description", overflow="fold")
    table.add_column("steps", style="sc.muted", overflow="ellipsis", no_wrap=True, max_width=48)
    table.add_column("", style="sc.accent", no_wrap=True)
    plain = []
    for name in sorted(found):
        f = found[name]
        flags = " ".join(x for x in ("sys" if f.system else "", "quiet" if f.output == "quiet" else "",
                                      "cmd" if f.command else "") if x)
        hidden = funcs.shadowed(ctx, name)
        where = _where(f) + (f" (hides {hidden})" if hidden else "")
        desc = f.description + (f"  · {f.usage}" if f.usage else "")
        table.add_row(name, where, desc, f"{len(f.steps)}: {funcs.preview(f, 44)}", flags)
        plain.append(f"{name}\t{f.where}{f' (hides {hidden})' if hidden else ''}\t{f.description}\t{funcs.preview(f)}")
    hint = Text("func show NAME · func run NAME [ARGS] · func new NAME · man func", style="sc.muted")
    return Styled(Group(table, hint), "\n".join(plain) + "\n")


def _show(ctx: ShellContext, args: list[str]) -> Any:
    if len(args) != 1:
        raise CommandError("func show: usage: func show NAME")
    f = funcs.find(ctx, args[0])
    from shellcraft.builtins import BUILTINS
    from shellcraft.highlight import highlight

    names = set(funcs.available(ctx)) | set(ctx.functions)

    def known(word: str) -> bool:
        return word in BUILTINS or word in names or ctx.registry.get(word) is not None

    head = Table.grid(padding=(0, 2))
    head.add_column(style="sc.muted", no_wrap=True)
    head.add_column(overflow="fold")
    rows = [("description", f.description or "—"), ("usage", f.usage or f"{f.name} [ARG...]"),
            ("system", "yes (needs OS commands)" if f.system else "no"), ("output", f.output),
            ("command", f"yes (typing {f.name} runs it)" if f.command else f"no (func run {f.name})"),
            ("where", f"{_where(f)}" + (f"  {f.path}" if f.path else "  (this session only: func save "
                                                                         f"{f.name} keeps it)"))]
    for key, value in rows:
        head.add_row(key, value)
    steps = Text()
    for number, step in enumerate(f.steps, 1):
        steps.append(f"{number:>3} ❯ ", style="sc.accent")
        lines = step.split("\n")
        for i, part in enumerate(lines):
            if i:
                steps.append("      ")
            steps.append_text(_rich_line(highlight(part, known)))
            steps.append("\n")
    steps.rstrip()
    title = Text.assemble(("◆ " if f.command else "", "sc.accent"), (f.name, "bold sc.path"))
    body = Group(title, head, Text(""), steps if f.steps else Text("(no steps yet)", style="sc.muted"))
    plain = funcs.render(f)
    return Styled(body, plain)


def _check(ctx: ShellContext, args: list[str]) -> Any:
    """Parse the func and look up its commands; run nothing. Line numbers are its file's."""
    if len(args) != 1:
        raise CommandError("func check: usage: func check NAME")
    f = funcs.find(ctx, args[0])
    from shellcraft import check

    text = f.path.read_text(encoding="utf-8") if f.path is not None and f.path.is_file() else funcs.render(f)
    report = check.check_text(text, f.name, ctx, steps=True)
    return WithStatus(report.text(), report.status) if report.status else report.text()


# The input line's highlighting classes, in the theme's Rich styles (for `func show`).
LEXER_STYLES = {"class:cmd.known": "sc.success", "class:cmd.unknown": "sc.error", "class:comment": "sc.muted",
                "class:keyword": "sc.prompt", "class:operator": "sc.accent", "class:string": "sc.warning",
                "class:switch": "sc.path", "class:variable": "sc.accent"}


def _rich_line(fragments: list[tuple[str, str]]) -> Text:
    line = Text()
    for style, text in fragments:
        line.append(text, style=LEXER_STYLES.get(style.strip(), ""))
    return line


def _path(ctx: ShellContext, args: list[str]) -> str:
    if args:
        f = funcs.find(ctx, args[0])
        return f"{f.path or '(session only)'}\n"
    folder, _ = funcs.target_dir(ctx.config)
    return f"{folder}\n"


# ── running ──────────────────────────────────────────────────────────────────

def run_func(ctx: ShellContext, f: funcs.Func, args: list[str], output: str | None = None,
             isolated: bool = False) -> Any:
    """Run a func and return what the command line shows: the outputs not printed yet and the summary."""
    try:
        outcome = funcs.run(ctx, f, args, output, isolated)
    except funcs.FuncError as exc:
        raise CommandError(str(exc)) from None
    steps = f"{outcome.steps} step{'s' if outcome.steps != 1 else ''}"
    if outcome.failed is None:
        summary = Text(f"✓ {f.name}: {steps}, {outcome.seconds:.1f} s" + (" (isolated)" if isolated else ""),
                       style="sc.muted")
    else:
        summary = Text(f"✗ {f.name}: step {outcome.failed} failed ({outcome.failed_line}): {outcome.error}",
                       style="sc.error")
    from shellcraft.context import Paged, to_text

    shown: list[Any] = []
    for item in outcome.collected:  # outputs nothing printed as they came (a background job, tests)
        while isinstance(item, (Styled, Paged)):
            item = item.renderable if isinstance(item, Styled) else item.value
        shown.append(Text.from_ansi(item.rstrip("\n")) if isinstance(item, str) else item)
    texts = [to_text(item) for item in outcome.collected]
    plain = "".join(x if x.endswith("\n") else x + "\n" for x in texts) + summary.plain + "\n"
    value: Any = Styled(Group(*shown, summary), plain)
    return WithStatus(value, outcome.status) if outcome.status else value


def _run(ctx: ShellContext, args: list[str]) -> Any:
    positional, flags = _flags(args, "-i", "-b", "-q", "-v", stop_at_positional=True)
    if not positional:
        raise CommandError("func run: usage: func run [-i] [-b] [-q|-v] NAME [ARG...]")
    name, rest = positional[0], positional[1:]
    f = funcs.find(ctx, name)
    output = "quiet" if flags.get("-q") else "show" if flags.get("-v") else None
    if flags.get("-b"):
        return _background(ctx, f, rest, output)
    return run_func(ctx, f, rest, output, bool(flags.get("-i")))


def _background(ctx: ShellContext, f: funcs.Func, args: list[str], output: str | None) -> Any:
    """`func run -b`: the steps run in a background command job (the footer, `jobs N`), in the job's own
    context, so they can't change your shell (a `cd` or `env set` step is refused there)."""
    from shellcraft.builtins import command_job

    if not ctx.interactive or ctx.jobs is None:
        raise CommandError("func run -b: background jobs only work in the interactive shell")
    if f.system and not ctx.allow_system:
        raise CommandError(f"{f.name} runs OS commands (system: yes): settings system_commands on first")
    line = shlex.join(["func", "run", f.name, *args])

    def work(background: ShellContext) -> tuple[int, str]:
        from shellcraft.context import to_text

        try:
            result = run_func(background, f, args, output)
        except CommandError as exc:
            return exc.status or 1, f"✗ {exc}\n"
        status = result.status if isinstance(result, WithStatus) else 0
        value = result.value if isinstance(result, WithStatus) else result
        return status, to_text(value)

    job = command_job(ctx, ctx.jobs, line, name=f"func {f.name}", work=work)
    return Text.assemble((f"[{job.number}] ", "sc.muted"), (f"func {f.name}", "sc.accent"),
                         (f"  running in the background (jobs {job.number} shows its output)", "sc.muted"))


# ── creating ─────────────────────────────────────────────────────────────────

def _ask(ctx: ShellContext, question: str, default: str = "") -> str:
    from shellcraft import ask

    shown = f"{question} [{default}]: " if default else (f"{question} " if question.endswith("❯") else f"{question}: ")
    try:
        answer = ask.ask(ctx, shown)
    except ask.AskError as exc:
        raise CommandError(f"func: {exc}", exc.status) from None
    return answer.strip() or default


def _yes(ctx: ShellContext, question: str, default: bool) -> bool:
    answer = _ask(ctx, question + (" (Y/n)" if default else " (y/N)"))
    return default if not answer else answer.lower().startswith("y")


def _say(ctx: ShellContext, text: str, style: str = "sc.muted") -> None:
    if ctx.ui is not None:
        ctx.ui.console.print(Text(text, style=style), markup=False, highlight=False)


def _new(ctx: ShellContext, args: list[str]) -> Any:
    positional, flags = _flags(args, "-e", "--save", "-g", "--system", "--quiet", "--command", "-f",
                               values=("-d",))
    if not positional:
        raise CommandError("func new: usage: func new NAME [-e] [--save] [-g] [-d DESC] [--system] [--quiet] "
                           "[--command] [COMMAND...]")
    name = funcs.check_name(positional[0])
    steps = positional[1:]
    if name in funcs.available(ctx) and not flags.get("-f"):
        raise CommandError(f"func new: '{name}' exists already (func edit {name}; -f replaces it)")
    interactive = ctx.interactive and ctx.ui is not None
    f = funcs.Func(name=name, description=flags.get("-d", ""), system=bool(flags.get("--system")),
                   output="quiet" if flags.get("--quiet") else "show", command=bool(flags.get("--command")))
    if steps:
        f.set_steps(steps)
    elif flags.get("-e"):
        if not interactive:
            raise CommandError("func new -e: only in the interactive shell")
        f = _edit_text(ctx, f)
    elif interactive:
        f = _guided(ctx, f, flags)
    f.name = name
    if flags.get("--save"):
        folder, where = funcs.target_dir(ctx.config, bool(flags.get("-g")))
        saved = funcs.save(f, folder, where)
        ctx.funcs.pop(name, None)
        return Text.assemble(("✓ ", "sc.success"), "func ", (name, "sc.path"), f" saved ({len(saved.steps)} steps, ",
                             (_where(saved), "sc.accent"), ")", (f"  func run {name} · func show {name}", "sc.muted"))
    ctx.funcs[name] = f
    return Text.assemble(("✓ ", "sc.success"), "func ", (name, "sc.path"), f" made for this session ({len(f.steps)} steps)",
                         (f"  func save {name} keeps it · func run {name}", "sc.muted"))


def _guided(ctx: ShellContext, f: funcs.Func, flags: dict[str, Any]) -> funcs.Func:
    _say(ctx, f"New func '{f.name}'. Enter keeps the value in [brackets]; Ctrl-C stops.", "sc.accent")
    if not flags.get("-d"):
        f.description = _ask(ctx, "Description (what it does)")
    if not flags.get("--system"):
        f.system = _yes(ctx, "Does it run OS commands (programs outside ShellCraft)?", False)
    if not flags.get("--quiet"):
        f.output = "quiet" if _ask(ctx, "Output: show each step's output, or quiet (only echo and @ steps)",
                                   "show").lower().startswith("q") else "show"
    if not flags.get("--command"):
        f.command = _yes(ctx, f"Run it by typing just `{f.name}`?", False)
    f.usage = _ask(ctx, "Usage line (optional, e.g. recon DOMAIN)")
    _say(ctx, "The steps, one per line: any ShellCraft command, $1 $2 $@ for the arguments, @ in front to "
              "always show a step's output. An empty line finishes.")
    steps: list[str] = []
    while True:
        step = _ask(ctx, f"  step {len(steps) + 1} ❯")
        if not step:
            break
        steps.append(step)
    f.set_steps(steps)
    return f


def _edit_text(ctx: ShellContext, f: funcs.Func) -> funcs.Func:
    """Open the func's text in the editor (a temp file for a session func) and read it back."""
    from shellcraft import editor

    if f.path is not None and f.where != "session":
        editor.open_file(ctx, f.path)
        edited = funcs.parse(f.path.read_text(encoding="utf-8"), f.name)
    else:
        with tempfile.TemporaryDirectory(prefix="shellcraft-func-") as tmp:
            path = Path(tmp) / f"{f.name}{funcs.SUFFIX}"
            text = funcs.render(f)
            if not f.body:
                text += "# one command per line; $1 $2 $@ are the arguments\n"
            path.write_text(text, encoding="utf-8")
            editor.open_file(ctx, path)
            edited = funcs.parse(path.read_text(encoding="utf-8"), f.name)
    edited.name, edited.where, edited.path = f.name, f.where, f.path
    return edited


# ── changing ─────────────────────────────────────────────────────────────────

def _save(ctx: ShellContext, args: list[str]) -> Any:
    positional, flags = _flags(args, "-g")
    if len(positional) != 1:
        raise CommandError("func save: usage: func save NAME [-g]")
    f = funcs.find(ctx, positional[0])
    folder, where = funcs.target_dir(ctx.config, bool(flags.get("-g")))
    saved = funcs.save(f, folder, where)
    ctx.funcs.pop(f.name, None)
    return Text.assemble(("✓ ", "sc.success"), "func ", (f.name, "sc.path"), " saved to ", (_where(saved), "sc.accent"),
                         (f"  {saved.path}", "sc.muted"))


def _edit(ctx: ShellContext, args: list[str]) -> Any:
    if len(args) != 1:
        raise CommandError("func edit: usage: func edit NAME")
    if not (ctx.interactive and ctx.ui is not None):
        f = funcs.find(ctx, args[0])
        return f"{f.path or '(session only)'}\n"
    f = _edit_text(ctx, funcs.find(ctx, args[0]))
    if f.where == "session":
        ctx.funcs[f.name] = f
    return Text.assemble(("✓ ", "sc.success"), "func ", (f.name, "sc.path"), f" edited ({len(f.steps)} steps)")


def _steps_changed(ctx: ShellContext, f: funcs.Func, steps: list[str], what: str) -> Any:
    f.set_steps(steps)
    funcs.store(ctx, f)
    return Text.assemble(("✓ ", "sc.success"), (f.name, "sc.path"), f": {what} ({len(steps)} steps)",
                         (f"  func show {f.name}", "sc.muted"))


def _number(word: str, steps: list[str], allow_end: bool = False) -> int:
    if not word.isdigit() or not 1 <= int(word) <= len(steps) + (1 if allow_end else 0):
        raise CommandError(f"func: step '{word}' doesn't exist (1–{len(steps)})")
    return int(word)


def _add(ctx: ShellContext, args: list[str]) -> Any:
    if len(args) < 2:
        raise CommandError("func add: usage: func add NAME 'COMMAND'")
    f = funcs.find(ctx, args[0])
    return _steps_changed(ctx, f, f.steps + [" ".join(args[1:])], "step added")


def _insert(ctx: ShellContext, args: list[str]) -> Any:
    if len(args) < 3:
        raise CommandError("func insert: usage: func insert NAME N 'COMMAND'")
    f = funcs.find(ctx, args[0])
    steps = f.steps
    n = _number(args[1], steps, allow_end=True)
    steps.insert(n - 1, " ".join(args[2:]))
    return _steps_changed(ctx, f, steps, f"step inserted at {n}")


def _rm(ctx: ShellContext, args: list[str]) -> Any:
    if len(args) != 2:
        raise CommandError("func rm: usage: func rm NAME N")
    f = funcs.find(ctx, args[0])
    steps = f.steps
    n = _number(args[1], steps)
    removed = steps.pop(n - 1)
    return _steps_changed(ctx, f, steps, f"step {n} removed ({removed})")


def _move(ctx: ShellContext, args: list[str]) -> Any:
    if len(args) != 3:
        raise CommandError("func move: usage: func move NAME FROM TO")
    f = funcs.find(ctx, args[0])
    steps = f.steps
    a, b = _number(args[1], steps), _number(args[2], steps)
    steps.insert(b - 1, steps.pop(a - 1))
    return _steps_changed(ctx, f, steps, f"step {a} moved to {b}")


FIELDS = ("description", "system", "output", "command", "usage")


def _set(ctx: ShellContext, args: list[str]) -> Any:
    if len(args) < 3 or args[1] not in FIELDS:
        raise CommandError(f"func set: usage: func set NAME FIELD VALUE (FIELD: {', '.join(FIELDS)})")
    f = funcs.find(ctx, args[0])
    field, value = args[1], " ".join(args[2:])
    if field in ("system", "command"):
        if value.lower() not in ("yes", "no", "on", "off", "true", "false"):
            raise CommandError(f"func set: {field} is yes or no")
        setattr(f, field, value.lower() in ("yes", "on", "true"))
    elif field == "output":
        if value not in funcs.OUTPUTS:
            raise CommandError("func set: output is show or quiet")
        f.output = value
    else:
        setattr(f, field, value)
    funcs.store(ctx, f)
    return Text.assemble(("✓ ", "sc.success"), (f.name, "sc.path"), f": {field} set to {value}")


def _rename(ctx: ShellContext, args: list[str]) -> Any:
    if len(args) != 2:
        raise CommandError("func rename: usage: func rename OLD NEW")
    f = funcs.find(ctx, args[0])
    new = funcs.check_name(args[1])
    if new in funcs.available(ctx):
        raise CommandError(f"func rename: '{new}' exists already")
    funcs.remove(ctx, f)
    f.name = new
    if f.where == "session" or f.path is None:
        ctx.funcs[new] = f
    else:
        funcs.save(f, f.path.parent, f.where)
    return Text.assemble(("✓ ", "sc.success"), f"renamed {args[0]} to ", (new, "sc.path"))


def _copy(ctx: ShellContext, args: list[str]) -> Any:
    positional, flags = _flags(args, "-g")
    if len(positional) != 2:
        raise CommandError("func copy: usage: func copy SRC DST [-g]")
    f = funcs.find(ctx, positional[0]).copy()
    f.name = funcs.check_name(positional[1])
    if f.name in funcs.available(ctx):
        raise CommandError(f"func copy: '{f.name}' exists already")
    if f.where == "session" and not flags.get("-g"):
        f.path = None
        ctx.funcs[f.name] = f
        return Text.assemble(("✓ ", "sc.success"), f"copied {positional[0]} to ", (f.name, "sc.path"), " (session)")
    folder, where = funcs.target_dir(ctx.config, bool(flags.get("-g")))
    saved = funcs.save(f, folder, where)
    return Text.assemble(("✓ ", "sc.success"), f"copied {positional[0]} to ", (f.name, "sc.path"), " in ",
                         (_where(saved), "sc.accent"))


def _delete(ctx: ShellContext, args: list[str]) -> Any:
    positional, flags = _flags(args, "-y")
    if len(positional) != 1:
        raise CommandError("func delete: usage: func delete NAME [-y]")
    f = funcs.find(ctx, positional[0])
    if f.where != "session" and not flags.get("-y"):
        if not (ctx.interactive and ctx.ui is not None):
            raise CommandError("func delete: add -y to confirm (no terminal to ask on)")
        if not _yes(ctx, f"Delete func '{f.name}' ({_where(f)})?", False):
            return Text("nothing deleted", style="sc.muted")
    funcs.remove(ctx, f)
    hidden = funcs.available(ctx).get(f.name)
    note = f"  ({_where(hidden)} one of the same name is in use now)" if hidden else ""
    return Text.assemble(("✓ ", "sc.success"), "deleted func ", (f.name, "sc.path"), (note, "sc.muted"))


def _export(ctx: ShellContext, args: list[str]) -> Any:
    if not args or len(args) > 2:
        raise CommandError("func export: usage: func export NAME|--all [FILE]")
    chosen = list(funcs.available(ctx).values()) if args[0] == "--all" else [funcs.find(ctx, args[0])]
    text = json.dumps(funcs.export_bundle(chosen), indent=2) + "\n"
    if len(args) == 1:
        return text
    path = user_path(args[1])
    path.write_text(text, encoding="utf-8")
    return Text.assemble(("✓ ", "sc.success"), f"exported {len(chosen)} func(s) to ", (str(path), "sc.path"))


def _import(ctx: ShellContext, args: list[str]) -> Any:
    positional, flags = _flags(args, "-f", "-g")
    if len(positional) != 1:
        raise CommandError("func import: usage: func import FILE [-f] [-g]")
    try:
        bundle = json.loads(user_path(positional[0]).read_text(encoding="utf-8"))
    except ValueError as exc:
        raise CommandError(f"func import: {positional[0]}: not JSON ({exc})") from None
    folder, where = funcs.target_dir(ctx.config, bool(flags.get("-g")))
    names = funcs.import_bundle(bundle, folder, where, bool(flags.get("-f")))
    return Text.assemble(("✓ ", "sc.success"), f"imported {', '.join(names)} into ", (where, "sc.accent"))


ACTIONS = {
    "list": _list, "ls": _list, "show": _show, "view": _show, "check": _check, "run": _run, "new": _new, "create": _new,
    "save": _save, "edit": _edit, "add": _add, "insert": _insert, "rm": _rm, "remove": _rm, "move": _move,
    "set": _set, "rename": _rename, "copy": _copy, "delete": _delete, "export": _export, "import": _import,
    "path": _path,
}
