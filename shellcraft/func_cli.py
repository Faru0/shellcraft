"""`shellcraft [OPTIONS] func run NAME [ARG...]`: run a func from bash, cron or a script, with no prompt.

The shell starts without its screen: no banner, prompt_toolkit or prompt. The rc file, the startup
folder and the profile's rc load first, as at an interactive start (not with --no-startup). The
func's output goes to stdout as it runs (plain text when stdout isn't a terminal), errors go to
stderr, and the exit code is the func's status. `shellcraft func list / show / check / path` work
the same way.
"""

from __future__ import annotations

import sys
from typing import Any

USAGE = "usage: shellcraft [OPTIONS] func run [-i] [-q|-v] NAME [ARG...]  (or func list / show / check / path)"
READ_ONLY = ("list", "show", "view", "check", "path")  # the other actions edit funcs: inside ShellCraft


def _say(text: str) -> None:
    sys.stderr.write(f"shellcraft: {text}\n")
    sys.stderr.flush()


def _write(text: str) -> None:
    if not text:
        return
    sys.stdout.write(text if text.endswith("\n") else text + "\n")
    sys.stdout.flush()


def _error_text(exc: Exception) -> str:
    from shellcraft.parser import ParseError
    from shellcraft.pipeline import PipelineError

    if isinstance(exc, PipelineError):
        message = exc.message
        return message if message.startswith(f"{exc.name}:") else f"{exc.name}: {message}"
    if isinstance(exc, ParseError):
        return f"parse error: {exc.message}"
    return getattr(exc, "message", None) or str(exc) or type(exc).__name__


def context(args: Any, config: Any, registry: Any) -> Any:
    """A shell context with no screen: output to stdout, errors to stderr. It may ask (`read`) only
    when stdin is a terminal."""
    from rich.console import Console

    from shellcraft import settings
    from shellcraft.context import ShellContext, to_text
    from shellcraft.themes import UI, all_themes

    themes = all_themes(config, [])
    name = args.theme or config.get("theme", "cyberpunk")
    theme = themes.get(name) if isinstance(name, str) else None
    ui = UI(theme or themes["cyberpunk"], Console(file=sys.stdout, highlight=False))
    ctx = ShellContext(registry=registry, ui=ui, config=config,
                       allow_system=bool(args.allow_system or settings.get(config, "system_commands")))
    try:
        ctx.interactive = sys.stdin.isatty()
    except (AttributeError, ValueError):
        ctx.interactive = False

    def emit(value: Any) -> None:
        if ui.console.is_terminal:  # on a terminal it looks as it does at the prompt
            from shellcraft.output import show

            show(ui, value, pager=False)
        else:
            _write(to_text(value))

    ctx.emit = ctx.live = emit
    ctx.emit_error = lambda exc, line: _say(_error_text(exc))
    return ctx


def _startup(ctx: Any, args: Any, config: Any, profile_name: str | None) -> None:
    """What an interactive start runs before the first prompt: the profile's variables, then the rc
    file, the startup folder and the profile's rc (unless --no-startup)."""
    from shellcraft import profiles, settings, startup

    if profile_name and not args.lite:
        for warning in profiles.enter_env(ctx, profile_name):
            _say(f"profile {profile_name}: {warning}")
    if args.no_startup or args.lite or not settings.get(config, "startup"):
        return
    rc = startup.rc_path()
    if rc.is_file():
        try:
            startup.source(rc, ctx, session=True)  # its traps are the session's
        except (OSError, UnicodeDecodeError, RecursionError) as exc:
            _say(f"{rc}: {exc}")
    if startup.scripts(startup.folder()):
        startup.run_folder(ctx, startup.folder())
    if profile_name:
        profiles.run_rc(ctx, profile_name)


def _run_words(words: list[str]) -> tuple[list[str], dict[str, Any]] | int:
    """The NAME, ARGs and switches of `func run`, or the exit code for a bad command line."""
    from shellcraft.commands.func import _flags
    from shellcraft.context import CommandError

    try:
        positional, flags = _flags(words, "-i", "-b", "-q", "-v", stop_at_positional=True)
    except CommandError as exc:
        _say(f"{exc}\n{USAGE}")
        return 2
    if not positional:
        _say(USAGE)
        return 2
    if flags.get("-b"):
        _say("func run -b: there is no shell to keep a background job in; run it without -b "
             "(or put & after the command in bash)")
        return 2
    return positional, flags


def _run(ctx: Any, positional: list[str], flags: dict[str, Any]) -> int:
    from shellcraft import funcs

    name, rest = positional[0], positional[1:]
    try:
        f = funcs.find(ctx, name)
    except funcs.FuncError as exc:
        _say(f"func run: {exc}")
        return 127
    output = "quiet" if flags.get("-q") else "show" if flags.get("-v") else None
    try:
        outcome = funcs.run(ctx, f, rest, output, bool(flags.get("-i")))
    except funcs.FuncError as exc:
        _say(f"func run: {exc}")
        return 1
    if flags.get("-v"):  # the summary `func run` shows at the prompt; on stderr, so stdout stays the output
        steps = f"{outcome.steps} step{'s' if outcome.steps != 1 else ''}"
        sys.stderr.write(f"✓ {f.name}: {steps}, {outcome.seconds:.1f} s\n" if outcome.failed is None else
                         f"✗ {f.name}: step {outcome.failed} failed ({outcome.failed_line}): {outcome.error}\n")
        sys.stderr.flush()
    return outcome.status


def _builtin(ctx: Any, words: list[str]) -> int:
    """`func list`, `func show NAME`, `func check NAME`, `func path`: the builtin's output, its status."""
    from shellcraft.commands.func import func_cmd
    from shellcraft.context import CommandError, WithStatus

    try:
        result = func_cmd(ctx, words, "")
    except CommandError as exc:
        _say(str(exc))
        return getattr(exc, "status", 1) or 1
    status = 0
    if isinstance(result, WithStatus):
        result, status = result.value, result.status
    if ctx.emit is not None and result not in (None, ""):
        ctx.emit(result)
    return status


def main(words: list[str], args: Any, config: Any, registry: Any, profile_name: str | None) -> int:
    from shellcraft import traps
    from shellcraft.cli import _interrupts_as_usual
    from shellcraft.context import ShellExit

    if not words:
        _say(USAGE)
        return 2
    action = words[0]
    if action != "run" and action not in READ_ONLY:
        _say(f"func {action}: only inside ShellCraft (start shellcraft, then func {action}); "
             f"from outside: func run, list, show, check, path")
        return 2
    parsed = _run_words(words[1:]) if action == "run" else None
    if isinstance(parsed, int):
        return parsed
    _interrupts_as_usual()  # Ctrl-C is a KeyboardInterrupt again: the func's INT trap runs
    ctx = context(args, config, registry)
    code = 0
    try:
        try:
            _startup(ctx, args, config, profile_name)
            code = _run(ctx, *parsed) if parsed is not None else _builtin(ctx, words)
        except ShellExit as exc:  # `exit N` in the func, the rc file or a startup script
            code = int(exc.code)
        except KeyboardInterrupt:
            code = 130
        finally:
            traps.session_exit(ctx)  # `trap '…' EXIT` set in the rc file, as when the shell exits
    except KeyboardInterrupt:
        code = 130
    except Exception as exc:  # noqa: BLE001 — the last resort: one line, the details in the log
        from shellcraft import crashlog

        logged = crashlog.record(exc, "shellcraft func")
        _say(f"unexpected error: {type(exc).__name__}: {exc}" + (f" (details in {logged})" if logged else ""))
        return 1
    return code
