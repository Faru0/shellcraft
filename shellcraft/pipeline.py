"""Pipeline executor: resolves commands, streams text between them, handles redirection."""

from __future__ import annotations

import difflib
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

import shellcraft.commands  # noqa: F401 — registers the ported commands (ls, cat, grep, …)
from shellcraft import aliases, arith
from shellcraft.builtins import BUILTINS
from shellcraft.context import CommandError, ShellContext, ShellExit, WithStatus, to_text
from shellcraft.modkit import ModuleError
from shellcraft.parser import Command, ParseError, Pipeline, parse, tokenize

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


class Variables(Mapping[str, str]):
    """What `$name` expands to: `$?`, loop variables (`extra`), the shell's variables (`x=1`), and
    in the interactive shell the environment (`$HOME`). MCP clients never see the environment."""

    def __init__(self, ctx: ShellContext, extra: Mapping[str, str] | None = None):
        self.ctx = ctx
        self.extra = extra or {}

    def __getitem__(self, name: str) -> str:
        if name == "?":
            return str(self.ctx.last_status)
        if name in self.extra:
            return self.extra[name]
        if name in self.ctx.variables:
            return self.ctx.variables[name]
        if self.ctx.allow_sensitive and name in os.environ:
            return os.environ[name]
        raise KeyError(name)

    def __contains__(self, name: object) -> bool:
        try:
            self[name]  # type: ignore[index]
        except (KeyError, TypeError):
            return False
        return True

    def __iter__(self) -> Iterator[str]:
        names = {"?", *self.extra, *self.ctx.variables}
        if self.ctx.allow_sensitive:
            names |= set(os.environ)
        return iter(names)

    def __len__(self) -> int:
        return len(list(iter(self)))


def run_line(line: str, ctx: ShellContext) -> PipelineResult | None:
    """Parse and execute one command line: a pipeline, or a script (`for`, `if`, `a ; b`).
    Raises ParseError / PipelineError / ShellExit, and records the exit status in ctx.last_status."""
    from shellcraft import ask, script

    try:
        asking = ask.parse_ask_line(line)  # `name = input("…")`, `n = int(input("…"))`: Python, not shell
    except ask.AskError as exc:
        ctx.last_status = STATUS_SYNTAX
        raise PipelineError(1, 1, "input", str(exc), STATUS_SYNTAX) from None
    if asking is not None:
        name, prompt, kind = asking

        def say(message: str) -> None:
            if ctx.ui is not None:
                from rich.text import Text

                ctx.ui.console.print(Text(message, style="sc.warning"), markup=False, highlight=False)

        try:
            ctx.variables[name] = ask.ask_as(ctx, prompt, kind, say)
        except ask.AskError as exc:
            ctx.last_status = exc.status
            raise PipelineError(1, 1, "input", str(exc), exc.status) from None
        ctx.last_status = 0
        return PipelineResult(output="", status=0)
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
    """One plain pipeline typed at the prompt: `$?`, shell variables, `$( … )`, `$(( … ))` and
    globs expand (an expansion is never split into words); `x=1` sets a shell variable."""
    from shellcraft import script

    substitute = script._Runner(ctx).substitute
    variables = Variables(ctx)
    if assigned := assign(line, ctx, variables, substitute):
        ctx.last_status = 0
        return PipelineResult(output="", status=0) if assigned else None
    return _run_parsed(line, ctx, variables, substitute=substitute, glob=True)


def assign(line: str, ctx: ShellContext, variables: Mapping[str, str],
           substitute: Callable[[str], str] | None, target: dict[str, str] | None = None) -> bool:
    """`NAME=VALUE` on a line of its own: set the variable (in `target`, default the shell's) and
    return True. Anything else returns False. Raises ParseError for `x=a b` (quote the value)."""
    match = arith.is_assignment(line)
    if not match:
        return False
    name, raw = match.group(1), match.group(2)
    try:
        tokens = tokenize(raw, variables, substitute=substitute)
    except ParseError:
        return False  # `x=1 | y`: let the pipeline parser report it
    if any(t.op for t in tokens):
        return False
    if len(tokens) > 1:
        raise ParseError(f"{name}={raw.strip()}: quote a value that contains spaces: {name}=\"…\"",
                         match.start(2))
    value = tokens[0].value if tokens else ""
    store = ctx.variables if target is None else target
    store[name] = value
    if name in ctx.exported:
        ctx.exported[name] = value
        os.environ[name] = value
    return True


def _run_parsed(line: str, ctx: ShellContext, variables: Mapping[str, str], split: bool = False,
                substitute: Any = None, glob: bool = False, stdin: str = "",
                allow_leading_pipe: bool = False) -> PipelineResult | None:
    try:
        pipeline = parse(line, variables, split, substitute, glob, allow_leading_pipe)
    except ParseError:
        ctx.last_status = STATUS_SYNTAX
        raise
    if pipeline is None:
        return None
    try:
        result = run_pipeline(pipeline, ctx, stdin)
    except PipelineError as exc:
        ctx.last_status = exc.status
        raise
    except KeyboardInterrupt:
        ctx.last_status = STATUS_INTERRUPTED
        raise
    ctx.last_status = result.status
    return result


def run_pipeline(pipeline: Pipeline, ctx: ShellContext, stdin: str = "") -> PipelineResult:
    """Run the segments left to right, feeding each the previous one's output; `stdin` feeds the
    first (the collected output of a loop for `done | sort`, or nothing)."""
    if ctx.allow_aliases:
        pipeline = aliases.expand(pipeline, aliases.get_all(ctx.config))
    redirects = [pipeline.redirect, pipeline.stdin_path, *(c.stderr for c in pipeline.segments)]
    if any(r is not None for r in redirects) and not ctx.allow_redirect:
        raise PipelineError(len(pipeline.segments), len(pipeline.segments), "redirect",
                            "file redirection is disabled in this context")

    total = len(pipeline.segments)
    for index, cmd in enumerate(pipeline.segments, start=1):
        if cmd.name == "cd" and (total > 1 or pipeline.redirect):
            raise PipelineError(index, total, "cd", "cd only works on its own line, not in a pipeline")
    data: Any = stdin
    if pipeline.stdin_path is not None:
        try:
            data = Path(pipeline.stdin_path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise PipelineError(1, total, pipeline.segments[0].name or "<",
                                f"cannot read {pipeline.stdin_path}: {exc.strerror or exc}") from exc
    status = 0
    for index, cmd in enumerate(pipeline.segments, start=1):
        if not cmd.name:  # the empty first segment of `done | sort`: the loop's output is the input
            continue
        stdin_text = to_text(data)
        try:
            data = _run_command(cmd, stdin_text, ctx)
        except (ShellExit, KeyboardInterrupt):
            raise
        except PipelineError as exc:
            data, status = _failed(cmd, exc, index, total)
            continue
        except CommandError as exc:
            data, status = _failed(cmd, PipelineError(index, total, cmd.name, str(exc), exc.status), index, total)
            continue
        except ModuleError as exc:
            data, status = _failed(cmd, PipelineError(index, total, cmd.name, str(exc)), index, total)
            continue
        except Exception as exc:  # noqa: BLE001 — surface module bugs as styled errors
            error = PipelineError(index, total, cmd.name, f"{type(exc).__name__}: {exc}")
            error.__cause__ = exc
            data, status = _failed(cmd, error, index, total)
            continue
        # Like a shell without pipefail, the status is the last command's.
        status = data.status if isinstance(data, WithStatus) else 0
        if isinstance(data, WithStatus):
            data = data.value

    if pipeline.redirect:
        # The tokenizer already expanded an unquoted `~`; a quoted one stays literal, as in bash.
        target = Path(pipeline.redirect.path)
        text = to_text(data)
        if text and not text.endswith("\n"):
            text += "\n"
        try:
            # No mkdir: like a POSIX shell, `> typo/dir/f` fails instead of creating the directory.
            with open(target, "a" if pipeline.redirect.append else "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
        except (OSError, RuntimeError, ValueError) as exc:
            detail = getattr(exc, "strerror", None) or str(exc)
            raise PipelineError(total, total, pipeline.redirect.path, f"cannot write file: {detail}") from exc
        return PipelineResult(output=None, redirected_to=target.resolve(), status=status)
    return PipelineResult(output=data, status=status)


def _failed(cmd: Command, error: PipelineError, index: int, total: int) -> tuple[Any, int]:
    """A segment failed: with `2>&1` its message becomes its output, with `2> file` it goes to
    the file; otherwise the pipeline stops here."""
    message = error.message
    if message.startswith(f"{cmd.name}: "):
        message = message[len(cmd.name) + 2:]
    text = f"{cmd.name}: {message}\n"
    if cmd.stderr is not None:
        try:
            with open(cmd.stderr.path, "a" if cmd.stderr.append else "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
        except OSError as exc:
            raise PipelineError(index, total, cmd.stderr.path,
                                f"cannot write file: {exc.strerror or exc}") from exc
        return (text if cmd.merge_stderr else ""), error.status
    if cmd.merge_stderr:
        return text, error.status
    raise error


def run_argv(argv: list[str], ctx: ShellContext, stdin: str = "") -> Any:
    """Run one command given as its words (what `xargs` and `time` build); raises PipelineError."""
    if not argv:
        raise PipelineError(1, 1, "", "no command given")
    cmd = Command(argv[0], list(argv[1:]))
    if ctx.allow_aliases:
        cmd = aliases.expand_command(cmd, aliases.get_all(ctx.config))
    try:
        return _run_command(cmd, stdin, ctx)
    except (ShellExit, KeyboardInterrupt, PipelineError):
        raise
    except CommandError as exc:
        raise PipelineError(1, 1, cmd.name, str(exc), exc.status) from exc
    except ModuleError as exc:
        raise PipelineError(1, 1, cmd.name, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise PipelineError(1, 1, cmd.name, f"{type(exc).__name__}: {exc}") from exc


def _run_command(cmd: Command, stdin: str, ctx: ShellContext) -> Any:
    builtin = BUILTINS.get(cmd.name)
    if builtin is not None:
        if ((builtin.stateful and not ctx.allow_stateful) or (builtin.writes and not ctx.allow_writes)
                or (builtin.sensitive and not ctx.allow_sensitive)):
            raise CommandError(f"builtin '{cmd.name}' is not available in this context")
        if builtin.stateful or builtin.direct:
            # Shell-state builtins talk to the console themselves (`settings NAME` asks for a key,
            # `clear`, `banner`, `mcp start` has its own spinner): they stay on the main thread.
            return builtin.fn(ctx, cmd.args, stdin)
        return ctx.runner(f"running {cmd.name}…", lambda: builtin.fn(ctx, cmd.args, stdin))

    spec = ctx.registry.get(cmd.name)
    if spec is not None:
        if ctx.module_filter is not None and not ctx.module_filter(cmd.name):
            raise CommandError(f"module '{cmd.name}' is not available to MCP clients", STATUS_NOT_FOUND)
        label = spec.spinner_text or f"running {spec.name}…"
        result = ctx.runner(label, lambda: _collect(spec.run(list(cmd.args), stdin)))
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


def _collect(result: Any) -> Any:
    """A module's result as text: a `run()` may return a str, a Rich renderable, or an iterator /
    generator of lines (a streaming module), which is drained here."""
    if isinstance(result, (str, bytes)) or result is None or hasattr(result, "__rich_console__") \
            or hasattr(result, "__rich__") or isinstance(result, WithStatus):
        return result.decode("utf-8", errors="replace") if isinstance(result, bytes) else result
    if hasattr(result, "__iter__"):
        parts = []
        for chunk in result:
            text = chunk if isinstance(chunk, str) else str(chunk)
            parts.append(text if text.endswith("\n") else text + "\n")
        return "".join(parts)
    return str(result)


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
