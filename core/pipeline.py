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
from core.builtins import BUILTINS
from core.context import CommandError, ShellContext, ShellExit, to_text
from core.modkit import ModuleError
from core.parser import Command, Pipeline, parse

# cmd.exe built-ins that have no executable on PATH.
_WINDOWS_CMD_BUILTINS = {"dir", "type", "copy", "del", "erase", "move", "ren", "rename",
                         "md", "rmdir", "rd", "ver", "vol", "set"}


class PipelineError(Exception):
    def __init__(self, index: int, total: int, name: str, message: str):
        super().__init__(message)
        self.index = index  # 1-based
        self.total = total
        self.name = name
        self.message = message


@dataclass
class PipelineResult:
    output: Any  # str or a Rich renderable; None when redirected
    redirected_to: Path | None = None


def run_line(line: str, ctx: ShellContext) -> PipelineResult | None:
    """Parse and execute one command line. Raises ParseError / PipelineError / ShellExit."""
    pipeline = parse(line)
    if pipeline is None:
        return None
    return run_pipeline(pipeline, ctx)


def run_pipeline(pipeline: Pipeline, ctx: ShellContext) -> PipelineResult:
    if pipeline.redirect and not ctx.allow_redirect:
        raise PipelineError(len(pipeline.segments), len(pipeline.segments), "redirect",
                            "file redirection is disabled in this context")

    total = len(pipeline.segments)
    data: Any = ""
    for index, cmd in enumerate(pipeline.segments, start=1):
        stdin = to_text(data)
        try:
            data = _run_command(cmd, stdin, ctx)
        except (ShellExit, KeyboardInterrupt):
            raise
        except (CommandError, ModuleError) as exc:
            raise PipelineError(index, total, cmd.name, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 — surface module bugs as styled errors
            raise PipelineError(index, total, cmd.name, f"{type(exc).__name__}: {exc}") from exc

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
        return PipelineResult(output=None, redirected_to=target.resolve())
    return PipelineResult(output=data)


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
                               f"(OS commands are off — run: settings system_commands on)")

    known = list(BUILTINS) + ctx.registry.names()
    hint = difflib.get_close_matches(cmd.name, known, n=1)
    suffix = f" — did you mean '{hint[0]}'?" if hint else ""
    raise CommandError(f"command not found: {cmd.name}{suffix}")


def _resolve_system(cmd: Command) -> list[str] | None:
    exe = shutil.which(cmd.name)
    if exe:
        return [exe, *cmd.args]
    if sys.platform == "win32" and cmd.name.lower() in _WINDOWS_CMD_BUILTINS:
        return ["cmd", "/d", "/c", cmd.name, *cmd.args]
    return None


def _run_system(argv: list[str], stdin: str) -> str:
    try:
        proc = subprocess.run(
            argv, input=stdin, capture_output=True, text=True,
            encoding="utf-8", errors="replace", cwd=os.getcwd(),
        )
    except OSError as exc:
        raise CommandError(str(exc)) from exc
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or "no output"
        raise CommandError(f"exited with status {proc.returncode}: {detail}")
    return proc.stdout
