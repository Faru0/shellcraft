"""What an AI may run in the user's shell without asking (the `run_in_shell` MCP tool).

Four answers, in this order (shellcraft.ai_policy has the rules and the roles):

- **denied**: the AI's role may not run it, or a `deny` rule in `ai-policy.toml` matches. It is
  never queued; the AI is told why.
- **ask** (a matching `ask` rule) goes to the review queue, one item per command, and runs when
  the user approves it there.
- **read-only**: every command in the line is a builtin that neither changes shell state, nor
  writes files, nor reveals secrets (the same rule the MCP sandbox applies to
  `shellcraft_pipeline`), or a module; no redirection to a file, no `&`, no `sub`, no loop or
  function, no OS program, no alias or shell function (either could hide anything). It runs at
  once.
- **granted**: an `allow` rule matches, or the `ai_access` setting is on (the default; a session
  grant approved with `review approve` also turns it on). Everything runs at once.
- Otherwise the command waits in the review queue for `review approve`.

A free run is a background command job named `✦ AI: command` (origin "ai"): the user sees
its trace (`[N] done: ✦ AI: …` and the output) above the prompt, as for `cmd &`.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, NamedTuple

from shellcraft import ai_policy, aliases
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


class Verdict(NamedTuple):
    tier: str  # "read-only", "granted", "approval" or "denied"
    reason: str = ""  # why, for "denied" and for an "approval" a rule asked for


def check(line: str, ctx: ShellContext, role: str | None = None) -> Verdict:
    """The verdict on `line` for an AI with `role` (see the module doc for the order)."""
    policy = ai_policy.load()
    run = ai_policy.role_run(policy, role)
    if run == "none":
        return Verdict("denied", f"the {role} role may not run commands")
    verdict, rule = ai_policy.evaluate(policy, line, role, os.getcwd())
    if verdict == "deny" and rule is not None:
        return Verdict("denied", f"the policy denies `{rule.match}` ({ai_policy.FILE})")
    reads = read_only(line, ctx)
    if run == "read-only":
        return Verdict("read-only") if reads else Verdict("denied", f"the {role} role runs read-only commands only")
    if verdict == "ask" and rule is not None:
        return Verdict("approval", f"the policy asks about `{rule.match}`")
    if reads:
        return Verdict("read-only")
    if verdict == "allow" or getattr(ctx, "ai_run", False):
        return Verdict("granted")
    return Verdict("approval")


def tier(line: str, ctx: ShellContext, role: str | None = None) -> str:
    """"read-only", "granted" (the policy or the ai_access setting allows it), "approval" (it has
    to be asked) or "denied"."""
    return check(line, ctx, role).tier


def job_name(command: str, agent: str = "") -> str:
    """`✦ AI: grep root auth.log` (or `✦ AI worker-2: …`) for the footer and the jobs table."""
    short = " ".join(command.split())
    return f"✦ AI{' ' + agent if agent else ''}: {short[:MAX_NAME]}{'…' if len(short) > MAX_NAME else ''}"


def output_of(job: Any, limit: int = 256 * 1024) -> dict[str, Any]:
    """A finished (or running) job's output for the AI, the end kept."""
    text = job.output
    truncated = len(text) > limit
    return {"number": job.number, "output": text[-limit:] if truncated else text, "truncated": truncated,
            "status": job.status, "running": job.running}
