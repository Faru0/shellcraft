"""`trap`: a command that runs when something ends (EXIT), on Ctrl-C (INT) or after a command
fails (ERR).

ctx.traps is a stack of frames, each a dict signal -> command. The bottom frame is the session's
(the prompt); a function call (shellcraft.script.call_function), a sourced file
(shellcraft.startup.source, which covers the rc file and startup/*.sc) and a func
(shellcraft.funcs.run) each push one with frame() and pop it when they end. `trap` sets a trap in
the innermost frame.

- EXIT runs when its frame ends, however it ends (the end, `return`, `set -e`, an error, Ctrl-C);
  the session's when the shell exits (session_exit).
- INT runs when Ctrl-C stops a unit: the innermost frame's INT trap (interrupted). The unit then
  stops as it always does.
- ERR runs after a command fails where `set -e` would stop (not in a test, on the left of `&&` /
  `||` or after `!`): the innermost frame's ERR trap (failed). A failure inside a trap never runs
  ERR again, and a function call whose failure already ran it doesn't run it a second time.

A trap's command runs in the current context (its variables, `$1`…), `$?` is the status at that
moment, and `$?` is put back afterwards.
"""

from __future__ import annotations

import contextlib
from typing import Any, Iterator

SIGNALS = ("EXIT", "INT", "ERR")
_NUMBERS = {"0": "EXIT", "2": "INT"}


def signal_name(word: str) -> str | None:
    """EXIT, INT or ERR for a name as `trap` takes it (any case, `SIG` in front, 0 for EXIT, 2 for
    INT); None for anything else."""
    name = word.upper()
    if name.startswith("SIG"):
        name = name[3:]
    name = _NUMBERS.get(name, name)
    return name if name in SIGNALS else None


def find(ctx: Any, signal: str) -> str | None:
    """The command the innermost frame that has a `signal` trap gives it ('' means ignore)."""
    for frame in reversed(ctx.traps):
        if signal in frame:
            return str(frame[signal])
    return None


def run(ctx: Any, command: str, status: int | None = None, runner: Any = None,
        collect: list[Any] | None = None) -> None:
    """Run a trap's command. `$?` is `status` (by default the current one) and is put back after.
    Its output goes through `runner` (a function's: into what the function prints), else into
    `collect`, else to ctx.emit."""
    from shellcraft import script

    if not command:
        return
    saved = ctx.last_status if status is None else status
    marker = ctx.err_ran_at
    ctx.last_status = saved
    ctx.in_trap += 1
    try:
        text = script.run_trap(ctx, command, runner)
    finally:
        ctx.in_trap -= 1
        ctx.last_status, ctx.err_ran_at = saved, marker
    if not text:
        return
    if collect is not None:
        collect.append(text)
    elif ctx.emit is not None:
        ctx.emit(text)


@contextlib.contextmanager
def frame(ctx: Any, runner: Any = None, collect: list[Any] | None = None) -> Iterator[None]:
    """A function call, sourced file or func: its own traps, and its EXIT trap when it ends."""
    depth = len(ctx.traps)
    ctx.traps.append({})
    try:
        yield
    finally:
        try:
            command = ctx.traps[depth].get("EXIT") if len(ctx.traps) > depth else None
            if command:
                with contextlib.suppress(KeyboardInterrupt):
                    run(ctx, command, runner=runner, collect=collect)
        finally:
            del ctx.traps[depth:]


def session_exit(ctx: Any) -> None:
    """The shell is closing: the session's EXIT trap, once."""
    from shellcraft.context import ShellExit

    command = ctx.traps[0].pop("EXIT", None) if ctx.traps else None
    if command:
        with contextlib.suppress(KeyboardInterrupt, ShellExit):
            run(ctx, command)


def interrupted(ctx: Any, exc: BaseException, runner: Any = None, collect: list[Any] | None = None) -> None:
    """Ctrl-C stopped a unit: the innermost INT trap runs, once for this Ctrl-C (the units around
    it see the same KeyboardInterrupt and skip it)."""
    if getattr(exc, "shellcraft_trapped", False):
        return
    with contextlib.suppress(AttributeError):
        exc.shellcraft_trapped = True  # type: ignore[attr-defined]
    command = find(ctx, "INT")
    if command:
        with contextlib.suppress(KeyboardInterrupt):
            run(ctx, command, status=130, runner=runner, collect=collect)


def failed(ctx: Any, status: int, runner: Any = None, collect: list[Any] | None = None) -> None:
    """A command failed with `status`: the innermost ERR trap, where `set -e` would apply."""
    if not status or ctx.in_trap or ctx.errexit_off or ctx.try_handlers:
        return
    depth = len(ctx.traps)
    if ctx.err_ran_at > depth:  # a function call that failed: its failing command already ran it
        ctx.err_ran_at = depth  # (and the call that called it, and so on up)
        return
    command = find(ctx, "ERR")
    if command:
        run(ctx, command, status=status, runner=runner, collect=collect)
        ctx.err_ran_at = depth


def new_command(ctx: Any) -> None:
    """A command starts: forget which frame last ran ERR (see failed)."""
    ctx.err_ran_at = 0


def listing(ctx: Any, signals: list[str] | None = None) -> list[str]:
    """`trap` / `trap -p`: the traps of the current frame, as commands that set them again."""
    current = ctx.traps[-1] if ctx.traps else {}
    return [f"trap -- {_quote(current[name])} {name}" for name in SIGNALS
            if name in current and (signals is None or name in signals)]


def _quote(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"
