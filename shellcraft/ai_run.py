"""What an AI may run in the user's shell without asking (the `run_in_shell` MCP tool).

Three tiers. A **read-only** command line runs at once: every command in it is a builtin that
neither changes shell state, nor writes files, nor reveals secrets (the same rule the MCP
sandbox applies to `shellcraft_pipeline`), or a module; no redirection to a file, no `&`, no
`sub`, no loop or function, no OS program, no alias or shell function (either could hide
anything). With the **session grant** (`settings ai_run on`, or `request_session_grant`
approved with `review approve`) everything runs at once. Anything else goes to the review
queue, one item per command, and runs when the user approves it there.

A free run is a background command job named `✦ AI: command` (origin "ai"): the user sees
its trace (`[N] done: ✦ AI: …` and the output) above the prompt, as for `cmd &`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from shellcraft import aliases
from shellcraft.builtins import BUILTINS
from shellcraft.parser import ParseError, parse, split_background
from shellcraft.script import Simple, parse_script

if TYPE_CHECKING:
    from shellcraft.context import ShellContext

MAX_NAME = 40


def read_only(line: str, ctx: ShellContext) -> bool:
    """Whether `line` only reads: see the module doc. Anything the parser can't place is not."""
    if not line.strip() or split_background(line) is not None:
        return False
    try:
        program = parse_script(line)
    except ParseError:
        return False
    if program is None:
        return False
    names = aliases.get_all(ctx.config) if ctx.allow_aliases else {}
    for stmt in program.stmts:
        if not isinstance(stmt, Simple) or stmt.mode != "top" or stmt.negate:
            return False
        try:
            pipeline = parse(stmt.text)
        except ParseError:
            return False
        if pipeline is None or pipeline.redirect is not None:
            return False
        for cmd in pipeline.segments:
            if cmd.stderr is not None:
                return False
            if not _reads_only(cmd.name, ctx, names):
                return False
    return True


def _reads_only(name: str, ctx: ShellContext, names: dict[str, str]) -> bool:
    name = name[1:] if name.startswith("\\") else name
    if not name or name in names or name in ctx.functions:
        return False  # an alias or a function could stand for anything
    builtin = BUILTINS.get(name)
    if builtin is not None:
        return not (builtin.stateful or builtin.writes or builtin.sensitive)
    return ctx.registry.get(name) is not None


def tier(line: str, ctx: ShellContext) -> str:
    """"read-only", "granted" (the session grant is on) or "approval" (it has to be asked)."""
    if read_only(line, ctx):
        return "read-only"
    if getattr(ctx, "ai_run", False):
        return "granted"
    return "approval"


def job_name(command: str) -> str:
    """`✦ AI: grep root auth.log` for the footer and the jobs table."""
    short = " ".join(command.split())
    return f"✦ AI: {short[:MAX_NAME]}{'…' if len(short) > MAX_NAME else ''}"


def output_of(job: Any, limit: int = 256 * 1024) -> dict[str, Any]:
    """A finished (or running) job's output for the AI, the end kept."""
    text = job.output
    truncated = len(text) > limit
    return {"number": job.number, "output": text[-limit:] if truncated else text, "truncated": truncated,
            "status": job.status, "running": job.running}
