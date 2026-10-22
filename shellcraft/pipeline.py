"""Pipeline executor: resolves commands, streams text between them, handles redirection."""

from __future__ import annotations

import difflib
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

import shellcraft.commands  # noqa: F401 — registers the ported commands (ls, cat, grep, …)
from shellcraft import aliases, arith, streams
from shellcraft.builtins import BUILTINS
from shellcraft.context import CommandError, ShellContext, ShellExit, WithStatus, to_text
from shellcraft.modkit import ModuleError
from shellcraft.parser import Command, ParseError, Pipeline, parse, tokenize
from shellcraft.streams import Cancelled, Channel, Lines, UpstreamFailed

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
        if self.ctx.positional and (name.isdigit() or name in ("#", "@", "*")):
            args = self.ctx.positional[-1]  # inside a shell function: its arguments
            if name == "#":
                return str(len(args))
            if name in ("@", "*"):
                return " ".join(args)
            if name != "0":
                return args[int(name) - 1] if int(name) <= len(args) else ""
        if name in self.extra:
            return self.extra[name]
        if name in self.ctx.variables:
            return self.ctx.variables[name]
        if self.ctx.alerts is not None and re.fullmatch(r"CLIP\d?", name):
            return self.ctx.alerts.clip_at(int(name[4:] or 0))  # $CLIP the newest copy, $CLIP1 the one before…
        if self.ctx.allow_sensitive and name in os.environ:
            return os.environ[name]
        raise KeyError(name)

    def arguments(self) -> list[str] | None:
        """Inside a shell function, its arguments (for "$@"); None outside one."""
        return list(self.ctx.positional[-1]) if self.ctx.positional else None

    def __contains__(self, name: object) -> bool:
        try:
            self[name]  # type: ignore[index]
        except (KeyError, TypeError):
            return False
        return True

    def __iter__(self) -> Iterator[str]:
        names = {"?", *self.extra, *self.ctx.variables}
        if self.ctx.alerts is not None:
            names.add("CLIP")
            names |= {f"CLIP{n}" for n in range(1, len(self.ctx.alerts.clips()))}
        if self.ctx.allow_sensitive:
            names |= set(os.environ)
        return iter(names)

    def __len__(self) -> int:
        return len(list(iter(self)))


def run_line(line: str, ctx: ShellContext) -> PipelineResult | None:
    """Parse and execute one command line: a pipeline, or a script (`for`, `if`, `a ; b`).
    Raises ParseError / PipelineError / ShellExit, and records the exit status in ctx.last_status."""
    from shellcraft import ask, curlpaste, script
    from shellcraft.parser import join_continued_lines, split_background

    # A pasted curl command: Windows' "Copy as cURL (cmd)" form, and lines continued with `\`.
    line = join_continued_lines(curlpaste.from_cmd(line))

    background = split_background(line)
    if background is not None:  # `cmd &`: the same as `sub cmd`, without attaching to a program
        from shellcraft.builtins import start_background

        try:
            output = start_background(ctx, background)
        except CommandError as exc:
            ctx.last_status = exc.status
            raise PipelineError(1, 1, "&", str(exc), exc.status) from None
        ctx.last_status = 0
        return PipelineResult(output=output, status=0)
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
    return _run_parsed(line, ctx, variables, substitute=substitute, glob=True, live=True)


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
                allow_leading_pipe: bool = False, live: bool = False) -> PipelineResult | None:
    try:
        pipeline = parse(line, variables, split, substitute, glob, allow_leading_pipe)
    except ParseError:
        ctx.last_status = STATUS_SYNTAX
        raise
    if pipeline is None:
        return None
    try:
        result = run_pipeline(pipeline, ctx, stdin, live=live)
    except PipelineError as exc:
        ctx.last_status = exc.status
        raise
    except KeyboardInterrupt:
        ctx.last_status = STATUS_INTERRUPTED
        raise
    ctx.last_status = result.status
    return result


LIVE_AFTER = 0.3  # seconds of output still coming before a pipeline's output is printed live
POLL_MIN = 0.01


def run_pipeline(pipeline: Pipeline, ctx: ShellContext, stdin: str = "", live: bool = False) -> PipelineResult:
    """Run the segments at the same time, each reading the previous one's output as it is
    produced (shellcraft.streams); `stdin` feeds the first (the collected output of a loop for
    `done | sort`, or nothing). With `live` and ctx.live set, output that keeps coming is handed
    to ctx.live item by item (the REPL prints it, a background job records it) instead of being
    collected; a pipeline that finishes within LIVE_AFTER of its first output returns it whole,
    so it looks as before (colors, the pager)."""
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
        if total > 1 and _func_command(cmd, ctx) is not None:
            raise PipelineError(index, total, cmd.name, f"{cmd.name} is a func: it runs on its own line, not in a "
                                                        f"pipeline (func show {cmd.name})")
    data: Any = stdin
    if pipeline.stdin_path is not None:
        try:
            data = Path(pipeline.stdin_path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise PipelineError(1, total, pipeline.segments[0].name or "<",
                                f"cannot read {pipeline.stdin_path}: {exc.strerror or exc}") from exc

    segments = [(index, cmd) for index, cmd in enumerate(pipeline.segments, start=1) if cmd.name]
    output: Any = data
    status = 0
    first = pipeline.segments[0] if pipeline.segments else None
    if first is not None and not first.name and first.to_stderr:
        data = _to_stderr(first, data, ctx, 1, total)  # `done >&2`: the block's output goes to the error stream
        output = data
    if segments:  # none: `done |` alone, the loop's output is the output
        sink = ctx.live if live and pipeline.redirect is None else None
        output, status = _run_segments(segments, total, data, ctx, sink)

    if pipeline.redirect:
        # The tokenizer already expanded an unquoted `~`; a quoted one stays literal, as in bash.
        target = Path(pipeline.redirect.path)
        text = to_text(output)
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
    return PipelineResult(output=output, status=status)


def _run_segments(segments: list[tuple[int, Command]], total: int, data: Any, ctx: ShellContext,
                  sink: Any) -> tuple[Any, int]:
    """Start every segment, wire them with channels, and gather the last one's output."""
    cancel = streams.StopFlag(ctx.stop)  # a child of a background job's flag: `jobs -k` stops it too
    upstream: Any = data
    try:
        for index, cmd in segments[:-1]:
            if _main_thread(cmd, ctx):
                # Shell-state builtins (`history`, `time`, `read`…) run here, on the main thread, as
                # they always have; their output then feeds the next segment.
                value, _ = _run_inline(cmd, upstream, index, total, ctx)
                upstream = to_text(value)
                continue
            channel = Channel(cancel)
            _start(cmd, index, total, ctx, upstream, channel, cancel, final=False)
            upstream = channel
        index, cmd = segments[-1]
        if _main_thread(cmd, ctx):
            return _run_inline(cmd, upstream, index, total, ctx)
        out = Channel(cancel)
        _start(cmd, index, total, ctx, upstream, out, cancel, final=True)
        return _gather(out, ctx, sink, _label(segments, ctx))
    finally:
        cancel.set()  # producers still writing (the last segment finished early, or Ctrl-C) stop


def _main_thread(cmd: Command, ctx: ShellContext | None = None) -> bool:
    builtin = BUILTINS.get(cmd.name)
    if builtin is not None:
        return builtin.stateful or builtin.direct
    return ctx is not None and _func_command(cmd, ctx) is not None  # a func runs its lines here


def _func_command(cmd: Command, ctx: ShellContext) -> Any:
    """The func a typed name runs (header `command: yes`), when no builtin or module has the name."""
    if cmd.bypass or not cmd.name or cmd.name in BUILTINS or ctx.registry.get(cmd.name) is not None:
        return None
    from shellcraft import funcs

    return funcs.as_command(ctx, cmd.name)


def _label(segments: list[tuple[int, Command]], ctx: ShellContext) -> str:
    """The spinner's text: the first module's SPINNER_TEXT, else `running <last command>…`."""
    for _, cmd in segments:
        spec = ctx.registry.get(cmd.name) if cmd.name not in BUILTINS else None
        if spec is not None and spec.spinner_text:
            return spec.spinner_text
    return f"running {segments[-1][1].name}…"


def _as_error(cmd: Command, exc: BaseException, index: int, total: int) -> PipelineError:
    if isinstance(exc, PipelineError):
        return exc
    if isinstance(exc, CommandError):
        return PipelineError(index, total, cmd.name, str(exc), exc.status)
    if isinstance(exc, ModuleError):
        return PipelineError(index, total, cmd.name, str(exc))
    error = PipelineError(index, total, cmd.name, f"{type(exc).__name__}: {exc}")
    error.__cause__ = exc
    return error


def _stdin_for(cmd: Command, upstream: Any) -> Any:
    """What a command reads: Lines for a streaming builtin, else the whole text (waiting for it)."""
    builtin = BUILTINS.get(cmd.name)
    if builtin is not None and builtin.streams:
        return Lines(upstream if isinstance(upstream, Channel) else to_text(upstream))
    if isinstance(upstream, Channel):
        return Lines(upstream).read()
    return to_text(upstream)


def _run_inline(cmd: Command, upstream: Any, index: int, total: int, ctx: ShellContext) -> tuple[Any, int]:
    """Run one command on this thread, to completion: (output, status)."""
    try:
        result = _call(cmd, _stdin_for(cmd, upstream), ctx)
        if streams.is_stream(result):
            result = streams.drain(result)
    except (ShellExit, KeyboardInterrupt, Cancelled):
        raise
    except UpstreamFailed as up:
        raise up.error from None
    except Exception as exc:  # noqa: BLE001 — surface module bugs as styled errors
        return _failed(cmd, _as_error(cmd, exc, index, total), index, total)
    if isinstance(result, WithStatus):
        return result.value, result.status
    return ("" if result is None else result), 0


def _start(cmd: Command, index: int, total: int, ctx: ShellContext, upstream: Any, out: Channel,
           cancel: threading.Event, final: bool) -> None:
    threading.Thread(target=_produce, args=(cmd, index, total, ctx, upstream, out, cancel, final),
                     daemon=True, name=f"shellcraft:{cmd.name}").start()


def _produce(cmd: Command, index: int, total: int, ctx: ShellContext, upstream: Any, out: Channel,
             cancel: threading.Event, final: bool) -> None:
    """One segment in its own thread: run it and write what it makes into `out`. The last
    segment writes ("value", obj) for a whole result and ("item", obj) per streamed item, so the
    caller can keep a single result as it is (a table, a man page); the others write text."""
    streams.set_cancel(cancel)
    result: Any = None
    try:
        try:
            result = _call(cmd, _stdin_for(cmd, upstream), ctx)
            status = 0
            if isinstance(result, WithStatus):
                status, result = result.status, result.value
            if streams.is_stream(result):
                while True:
                    try:
                        item = next(result)
                    except StopIteration as stop:
                        if isinstance(stop.value, int):
                            status = stop.value
                        break
                    if item is None or item == "":
                        continue
                    out.put(("item", item) if final else streams.item_text(item))
            elif final:
                out.put(("value", "" if result is None else result))
            else:
                text = to_text(result)
                if text:
                    out.put(text if text.endswith("\n") else text + "\n")
            out.end(status)
        except Cancelled:
            return
        except UpstreamFailed as up:
            out.fail(up.error)
        except (ShellExit, KeyboardInterrupt) as exc:
            out.fail(_as_error(cmd, exc, index, total))
        except Exception as exc:  # noqa: BLE001 — surface module bugs as styled errors
            try:
                text, status = _failed(cmd, _as_error(cmd, exc, index, total), index, total)
            except PipelineError as error:
                out.fail(error)
                return
            if text:
                out.put(("value", text) if final else text)
            out.end(status)
        except BaseException as exc:  # noqa: BLE001 — SystemExit, GeneratorExit…: the channel must still close
            out.fail(PipelineError(index, total, cmd.name, f"{type(exc).__name__}: {exc}"))
    except Cancelled:
        return
    finally:
        if streams.is_stream(result) and hasattr(result, "close"):
            try:
                result.close()
            except BaseException:  # noqa: BLE001 — a generator's cleanup must not escape the thread
                pass
        streams.set_cancel(None)


def _gather(out: Channel, ctx: ShellContext, sink: Any, label: str) -> tuple[Any, int]:
    """Read the last segment's channel on this thread: (output, status). The spinner shows until
    something arrives; output that is still coming LIVE_AFTER seconds later is handed to `sink`
    as it arrives (and the result is then empty), else it is returned whole."""
    entry = ctx.runner(label, out.get)
    items: list[Any] = []
    value: Any = None
    printing = False
    deadline: float | None = None
    status = 0
    while True:
        assert entry is not None
        kind, payload = entry
        if kind == "error":
            raise payload
        if kind == "end":
            status = payload
            break
        tag, obj = payload
        if tag == "value":
            value = obj
        elif printing:
            sink(obj)
        else:
            items.append(obj)
            if sink is not None and deadline is None:
                deadline = time.monotonic() + LIVE_AFTER
        timeout = None
        if sink is not None and not printing and deadline is not None:
            timeout = max(POLL_MIN, deadline - time.monotonic())
        entry = out.get(timeout=timeout)
        if entry is None:  # still coming: print what there is, and the rest as it arrives
            for item in items:
                sink(item)
            items, printing = [], True
            entry = out.get()
    if printing:
        return "", status
    if value is not None and not items:
        return value, status
    return streams.combine(items), status


def write_stderr(ctx: ShellContext, text: str) -> None:
    """Text for the error stream: the innermost sink a function call or block pushed (its
    `2> file` / `2>&1`), else the REPL's error printer, else the process's stderr (MCP, tests)."""
    if not text:
        return
    if not text.endswith("\n"):
        text += "\n"
    if ctx.stderr_sinks:
        ctx.stderr_sinks[-1](text)
    elif ctx.emit_stderr is not None:
        ctx.emit_stderr(text)
    else:
        sys.stderr.write(text)
        sys.stderr.flush()


def stderr_file_sink(redirect: Any) -> Callable[[str], None]:
    """A sink writing to a `2> file` / `2>> file` target. The file is created (or emptied) now, as
    a shell does at the redirection, and appended to by each write."""
    with open(redirect.path, "a" if redirect.append else "w", encoding="utf-8", newline=""):
        pass

    def write(text: str) -> None:
        with open(redirect.path, "a", encoding="utf-8", newline="") as fh:
            fh.write(text)
    return write


_STDERR_STARTED: set[int] = set()  # commands whose `2> FILE` a module already wrote to (later writes append)


def _with_error_stream(cmd: Command, ctx: ShellContext, call: Callable[[], Any]) -> Any:
    """Run a builtin or module with modkit.error_stream() (curl's `-v` trace, a warning) going where
    the command's own `2> FILE` / `2>&1` say: with `2>&1` it comes before the output, as curl -v's does."""
    from shellcraft import modkit

    previous = getattr(modkit._error_sink, "write", None)
    merged: list[str] = []
    modkit._error_sink.write = merged.append if cmd.merge_stderr and cmd.stderr is None else _command_stderr(cmd, ctx)
    try:
        result = call()
    finally:
        modkit._error_sink.write = previous
    if merged and not streams.is_stream(result):
        status = result.status if isinstance(result, WithStatus) else 0
        combined = "".join(merged) + to_text(result)
        return WithStatus(combined, status) if status else combined
    return result


def _command_stderr(cmd: Command, ctx: ShellContext) -> Callable[[str], None]:
    """Where a command's modkit.error_stream() text goes: its own `2> FILE`, else the shell's
    error stream."""
    redirect = cmd.stderr
    if redirect is None:
        return lambda text: write_stderr(ctx, text)

    def write(text: str) -> None:
        append = redirect.append or id(cmd) in _STDERR_STARTED
        _STDERR_STARTED.add(id(cmd))
        with open(redirect.path, "a" if append else "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
    return write


def _to_stderr(cmd: Command, data: Any, ctx: ShellContext, index: int, total: int) -> Any:
    """`>&2`: the segment's output goes to the error stream (its own `2> file` if it has one;
    with `2>&1` too it stays output). The next segment gets nothing; the status is unchanged."""
    if cmd.merge_stderr:
        return data
    text = to_text(data)
    if cmd.stderr is not None:
        try:
            with open(cmd.stderr.path, "a" if cmd.stderr.append else "w", encoding="utf-8", newline="") as fh:
                fh.write(text if not text or text.endswith("\n") else text + "\n")
        except OSError as exc:
            raise PipelineError(index, total, cmd.stderr.path, f"cannot write file: {exc.strerror or exc}") from exc
    else:
        write_stderr(ctx, text)
    return ""


def _failed(cmd: Command, error: PipelineError, index: int, total: int) -> tuple[Any, int]:
    """A segment failed: with `2>&1` its message becomes its output, with `2> file` it goes to
    the file; otherwise the pipeline stops here."""
    message = error.message
    if message.startswith(f"{cmd.name}: "):
        message = message[len(cmd.name) + 2:]
    text = f"{cmd.name}: {message}\n"
    if cmd.stderr is not None:
        started = id(cmd) in _STDERR_STARTED
        _STDERR_STARTED.discard(id(cmd))
        try:
            with open(cmd.stderr.path, "a" if cmd.stderr.append or started else "w", encoding="utf-8",
                      newline="") as fh:
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


# Shell functions take precedence over builtins, modules and programs, as in bash, except for
# these, which keep the shell in charge of itself (the "special builtins" that matter here).
SPECIAL_BUILTINS = frozenset({"exit", "export", "unset", "source", ".", "local", "declare", "alias", "unalias",
                              "shift"})


def _run_command(cmd: Command, stdin: str, ctx: ShellContext) -> Any:
    """One command to completion (what `xargs` and `time` run): through the spinner runner,
    except the builtins that stay on the main thread; a streamed result is collected."""
    if _main_thread(cmd, ctx):
        result = _call(cmd, stdin, ctx)
    else:
        result = ctx.runner(f"running {cmd.name}…", lambda: _call(cmd, stdin, ctx))
    return streams.drain(result) if streams.is_stream(result) else result


def _call(cmd: Command, stdin: Any, ctx: ShellContext) -> Any:
    """Resolve a command (builtin, module, OS program) and call it with `stdin` (a str, or Lines
    for a streaming builtin). May return a value, a WithStatus or an iterator of items."""
    if ctx.functions and not cmd.bypass and cmd.name in ctx.functions and cmd.name not in SPECIAL_BUILTINS:
        from shellcraft import script

        result: Any = script.call_function(ctx.functions[cmd.name], cmd.args, streams.text(stdin), ctx, cmd)
    else:
        result = _resolve(cmd, stdin, ctx)
    if cmd.to_stderr:  # `>&2`: the output goes to the error stream, the status stays
        if streams.is_stream(result):
            result = streams.drain(result)
        status = 0
        if isinstance(result, WithStatus):
            status, result = result.status, result.value
        kept = _to_stderr(cmd, result, ctx, 1, 1)  # with 2>&1 too, the output stays output
        return WithStatus(kept, status) if status else kept
    return result


def _resolve(cmd: Command, stdin: Any, ctx: ShellContext) -> Any:
    """Find the builtin, module or OS program for `cmd` and run it with `stdin`."""
    builtin = BUILTINS.get(cmd.name)
    if builtin is not None:
        if ((builtin.stateful and not ctx.allow_stateful) or (builtin.writes and not ctx.allow_writes)
                or (builtin.sensitive and not ctx.allow_sensitive)):
            raise CommandError(f"builtin '{cmd.name}' is not available in this context")
        if builtin.streams:
            return _with_error_stream(cmd, ctx, lambda: builtin.fn(ctx, cmd.args, Lines.of(stdin)))
        return _with_error_stream(cmd, ctx, lambda: builtin.fn(ctx, cmd.args, streams.text(stdin)))

    spec = ctx.registry.get(cmd.name)
    if spec is not None:
        if ctx.module_filter is not None and not ctx.module_filter(cmd.name):
            raise CommandError(f"module '{cmd.name}' is not available to MCP clients", STATUS_NOT_FOUND)

        def call() -> Any:
            result = spec.run(list(cmd.args), streams.text(stdin))
            if streams.is_stream(result):
                return result  # a streaming module: its lines flow on as it yields them
            result = _collect(result)
            return "" if result is None else result
        return _with_error_stream(cmd, ctx, call)

    func = _func_command(cmd, ctx)
    if func is not None:  # `recon example.com`: a func whose header says `command: yes`
        from shellcraft.commands.func import run_func

        return run_func(ctx, func, list(cmd.args))

    argv = _resolve_system(cmd)
    if argv is not None:
        if ctx.allow_system:
            return _stream_system(argv, stdin)
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
        return [*_CMD_UNICODE, cmd.name, *cmd_safe_args(cmd.name, cmd.args)]
    return None


_CMD_UNSAFE = '"%!\r\n'


def cmd_safe_args(name: str, args: list[str]) -> list[str]:
    """The arguments of a cmd.exe command (`dir`, `copy`…), checked: cmd.exe parses its own
    command line again after subprocess has quoted it, so a `"` would end the quoting and let
    `&`, `|` or `>` in the argument run a second command, and `%NAME%` / `!NAME!` would expand a
    variable. Such arguments are refused with a clear error; everything else is safe inside the
    quotes subprocess puts around it."""
    for arg in args:
        bad = next((ch for ch in arg if ch in _CMD_UNSAFE), None)
        if bad is not None:
            shown = {"\r": "a carriage return", "\n": "a newline"}.get(bad, repr(bad))
            raise CommandError(f"{name}: an argument can't contain {shown} ({arg!r}): cmd.exe would "
                               "read it as part of its own command line")
    return list(args)


# cmd.exe with /u writes its internal commands' output to a pipe as UTF-16 (without /u it's the
# OEM code page, e.g. cp437, which garbles accented file names from `dir`).
_CMD_UNICODE = ["cmd", "/d", "/u", "/c"]


def _stream_system(argv: list[str], stdin: Any) -> Any:
    """An OS program in a pipeline: its stdin is fed from a thread as the input arrives, and its
    output lines flow on as it writes them. A nonzero exit is an error, as before. cmd.exe's own
    commands (UTF-16 output) keep the collected path."""
    if argv[:len(_CMD_UNICODE)] == _CMD_UNICODE:
        return _run_system(argv, streams.text(stdin))
    return _system_lines(argv, stdin)


def _system_lines(argv: list[str], stdin: Any) -> Any:
    import tempfile

    errors = tempfile.TemporaryFile()  # noqa: SIM115 — closed in the finally below; a file, not a pipe, so stderr never blocks stdout
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors,
                                cwd=os.getcwd())
    except OSError as exc:
        errors.close()
        raise CommandError(str(exc)) from exc
    failed: list[BaseException] = []
    cancel = streams.current_cancel()

    def feed() -> None:
        streams.set_cancel(cancel)
        assert proc.stdin is not None
        try:
            source = stdin if isinstance(stdin, Lines) else Lines("" if stdin is None else str(stdin))
            for line in source:
                proc.stdin.write(line.encode("utf-8"))
                proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass  # the program stopped reading: fine, as in a shell
        except (UpstreamFailed, Cancelled) as exc:
            failed.append(exc)
            proc.kill()
        finally:
            try:
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass

    threading.Thread(target=feed, daemon=True, name=f"shellcraft:{argv[0]}-stdin").start()

    def watch() -> None:  # a quiet program (`tail -f` waiting) can't notice the stop: kill it
        while proc.poll() is None:
            if cancel is not None and cancel.wait(0.2):
                if proc.poll() is None:
                    proc.kill()
                return
            if cancel is None:
                return

    threading.Thread(target=watch, daemon=True, name=f"shellcraft:{argv[0]}-watch").start()
    assert proc.stdout is not None
    try:
        for raw in proc.stdout:
            yield raw.decode("utf-8", errors="replace").replace("\r\n", "\n")
        code = proc.wait()
        if failed:
            raise failed[0]
        if code != 0:
            errors.seek(0)
            detail = errors.read().decode("utf-8", errors="replace").strip() or "no output"
            raise CommandError(f"exited with status {code}: {detail}", code)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        proc.stdout.close()
        errors.close()


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
