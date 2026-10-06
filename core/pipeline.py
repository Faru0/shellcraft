"""Pipeline executor: resolves commands, streams text between them, handles redirection."""

from __future__ import annotations

import difflib
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import core.commands  # noqa: F401 — registers the ported commands (ls, cat, grep, …)
from core import aliases
from core.builtins import BUILTINS
from core.context import CommandError, ShellContext, ShellExit, WithStatus, to_text
from core.modkit import ModuleError
from core.parser import Command, ParseError, Pipeline, parse

# cmd.exe built-ins that have no executable on PATH.
_WINDOWS_CMD_BUILTINS = {"dir", "type", "copy", "del", "erase", "move", "ren", "rename",
                         "md", "rmdir", "rd", "ver", "vol", "set"}


# Exit statuses, as in POSIX shells.
STATUS_ERROR = 1
STATUS_SYNTAX = 2
STATUS_NOT_FOUND = 127
STATUS_INTERRUPTED = 130


class PipelineError(Exception):
    def __init__(self, index: int, total: int, name: str, message: str, status: int = STATUS_ERROR):
        super().__init__(message)
        self.index = index  # 1-based
        self.total = total
        self.name = name
        self.message = message
        self.status = status  # becomes `$?`


@dataclass
class PipelineResult:
    output: Any  # str or a Rich renderable; None when redirected
    redirected_to: Path | None = None
    status: int = 0  # the last segment's exit status (`$?`)


def run_line(line: str, ctx: ShellContext) -> PipelineResult | None:
    """Parse and execute one command line: a pipeline, or a script (`for`, `if`, `a ; b`).
    Raises ParseError / PipelineError / ShellExit, and records the exit status in ctx.last_status."""
    from core import script

    try:
        program = script.parse_script(line)
    except ParseError:
        ctx.last_status = STATUS_SYNTAX
        raise
    if program is None:
        return None
    if program.is_simple:
        return run_simple(line, ctx)
    return script.execute(program, ctx)


def run_simple(line: str, ctx: ShellContext) -> PipelineResult | None:
    """One pipeline, with `$?` (and any `variables`) expanded; sets ctx.last_status."""
    return _run_parsed(line, ctx, {"?": str(ctx.last_status)})


def _run_parsed(line: str, ctx: ShellContext, variables: dict[str, str], split: bool = False,
                substitute: Any = None) -> PipelineResult | None:
    try:
        pipeline = parse(line, variables, split, substitute)
    except ParseError:
        ctx.last_status = STATUS_SYNTAX
        raise
    if pipeline is None:
        return None
    try:
        result = run_pipeline(pipeline, ctx)
    except PipelineError as exc:
        ctx.last_status = exc.status
        raise
    except KeyboardInterrupt:
        ctx.last_status = STATUS_INTERRUPTED
        raise
    ctx.last_status = result.status
    return result


def run_pipeline(pipeline: Pipeline, ctx: ShellContext) -> PipelineResult:
    if ctx.allow_aliases:
        pipeline = aliases.expand(pipeline, aliases.get_all(ctx.config))
    if pipeline.redirect and not ctx.allow_redirect:
        raise PipelineError(len(pipeline.segments), len(pipeline.segments), "redirect",
                            "file redirection is disabled in this context")

    total = len(pipeline.segments)
    for index, cmd in enumerate(pipeline.segments, start=1):
        if cmd.name == "cd" and (total > 1 or pipeline.redirect):
            raise PipelineError(index, total, "cd", "cd only works on its own line, not in a pipeline")
    data: Any = ""
    status = 0
    for index, cmd in enumerate(pipeline.segments, start=1):
        stdin = to_text(data)
        try:
            data = _run_command(cmd, stdin, ctx)
        except (ShellExit, KeyboardInterrupt):
            raise
        except CommandError as exc:
            raise PipelineError(index, total, cmd.name, str(exc), exc.status) from exc
        except ModuleError as exc:
            raise PipelineError(index, total, cmd.name, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 — surface module bugs as styled errors
            raise PipelineError(index, total, cmd.name, f"{type(exc).__name__}: {exc}") from exc
        # Like a shell without pipefail, the status is the last command's.
        status = data.status if isinstance(data, WithStatus) else 0
        if isinstance(data, WithStatus):
            data = data.value

    if pipeline.redirect:
        target = Path(pipeline.redirect.path).expanduser()
        text = to_text(data)
        if text and not text.endswith("\n"):
            text += "\n"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "a" if pipeline.redirect.append else "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
        except OSError as exc:
            raise PipelineError(total, total, pipeline.redirect.path, f"cannot write file: {exc}") from exc
        return PipelineResult(output=None, redirected_to=target.resolve(), status=status)
    return PipelineResult(output=data, status=status)


def _run_command(cmd: Command, stdin: str, ctx: ShellContext) -> Any:
    builtin = BUILTINS.get(cmd.name)
    if builtin is not None:
        if ((builtin.stateful and not ctx.allow_stateful) or (builtin.writes and not ctx.allow_writes)
                or (builtin.sensitive and not ctx.allow_sensitive)):
            raise CommandError(f"builtin '{cmd.name}' is not available in this context")
        return builtin.fn(ctx, cmd.args, stdin)

    spec = ctx.registry.get(cmd.name)
    if spec is not None:
        label = spec.spinner_text or f"running {spec.name}…"
        result = ctx.runner(label, lambda: spec.run(list(cmd.args), stdin))
        return "" if result is None else result

    argv = _resolve_system(cmd)
    if argv is not None:
        if ctx.allow_system:
            return ctx.runner(f"running {cmd.name}…", lambda: _run_system(argv, stdin))
        if ctx.allow_stateful:  # only suggest the setting where the user can change it
            raise CommandError(f"command not found: {cmd.name} "
                               f"(OS commands are off — run: settings system_commands on)", STATUS_NOT_FOUND)

    if ctx.registry.is_disabled(cmd.name):
        hint = f" — run: modules enable {cmd.name}" if ctx.allow_stateful else ""
        raise CommandError(f"module '{cmd.name}' is disabled{hint}", STATUS_NOT_FOUND)
    known = list(BUILTINS) + ctx.registry.names()
    hint = difflib.get_close_matches(cmd.name, known, n=1)
    suffix = f" — did you mean '{hint[0]}'?" if hint else ""
    raise CommandError(f"command not found: {cmd.name}{suffix}", STATUS_NOT_FOUND)


def _resolve_system(cmd: Command) -> list[str] | None:
    exe = shutil.which(cmd.name)
    if exe:
        return [exe, *cmd.args]
    if sys.platform == "win32" and cmd.name.lower() in _WINDOWS_CMD_BUILTINS:
        return [*_CMD_UNICODE, cmd.name, *cmd.args]
    return None


# cmd.exe with /u writes its internal commands' output to a pipe as UTF-16 (without /u it's the
# OEM code page, e.g. cp437, which garbles accented file names from `dir`).
_CMD_UNICODE = ["cmd", "/d", "/u", "/c"]


def _run_system(argv: list[str], stdin: str) -> str:
    try:
        proc = subprocess.run(argv, input=stdin.encode("utf-8"), capture_output=True, cwd=os.getcwd())
    except OSError as exc:
        raise CommandError(str(exc)) from exc
    utf16 = argv[:len(_CMD_UNICODE)] == _CMD_UNICODE
    stdout, stderr = _decode(proc.stdout, utf16), _decode(proc.stderr, utf16)
    if proc.returncode != 0:
        detail = stderr.strip() or stdout.strip() or "no output"
        raise CommandError(f"exited with status {proc.returncode}: {detail}", proc.returncode)
    return stdout


def _decode(data: bytes, utf16: bool = False) -> str:
    """A program's output as text with \n line ends (Windows programs write \r\n)."""
    if utf16 and len(data) % 2 == 0 and b"\x00" in data:
        text = data.decode("utf-16-le", errors="replace")
    else:
        text = data.decode("utf-8", errors="replace")
    return text.replace("\r\n", "\n")
