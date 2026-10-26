"""ai: what an AI may do here, and what it did (shellcraft.ai_policy, shellcraft.ai_audit)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from rich.table import Table
from rich.text import Text

from shellcraft import ai_audit, ai_policy, settings
from shellcraft.builtins import builtin
from shellcraft.context import CommandError, Styled
from shellcraft.modkit import ArgParser

if TYPE_CHECKING:
    from shellcraft.context import ShellContext

AI_DOC = """# ai

What an AI client may do in your shell, and what it did. An AI reaches your shell through
ShellCraft's MCP server (`man mcp`); this command is where you see and steer that.

## Usage

```
ai                       # is AI access on, the policy, the roles, the last few actions
ai on | off              # let it run any command at once / only read-only ones (same as settings ai_access)
ai log [-n N] [--agent NAME] [--json]   # what AI clients did: every tool call, newest last
ai policy                # the rules and roles in ~/.shellcraft/ai-policy.toml
ai roles                 # the roles a server can be started with (--role NAME)
```

## Access

`settings ai_access` is **on** by default. With it on, an AI runs any command at once, and the
trace shows above your prompt as a `✦ AI:` job. `✦ run` shows in the prompt while it is on. With it
off, an AI runs read-only commands at once and the rest waits for `review approve`.

## The policy file

`~/.shellcraft/ai-policy.toml` is optional. It adds rules (deny, ask or allow a command by
pattern, for a role, in a folder, until a date), roles, a budget of module calls, and how long a
module's answer is reused:

```toml
[[rule]]
action = "deny"
match  = "rm -r*"

[[rule]]
action = "ask"            # waits for your review approve, even with access on
match  = "git push*"

[role.scout]
run   = "read-only"
tools = ["get_recent_output", "get_jobs", "send_tui_alert"]
```

The first rule that decides wins in the order deny, ask, allow. A starter with every option
explained is `ai-policy.toml.sample` in your ShellCraft folder.

## Roles

Start a server for one role and it offers only what the role may use, and the shell checks the
role again before it runs anything:

```
claude mcp add shellcraft-monitor -- /path/to/python /path/to/main.py --mcp --role monitor
```

`monitor` reads and alerts but never runs a command. `orchestrator` and `worker` follow your
rules. Any tool also takes an optional `agent` name, which shows in the footer and in `ai log`.

## The log

`ai log` reads `~/.shellcraft/ai-audit.jsonl`: when, which tool, who (client, agent, role), a short
summary such as the command, how a command was answered (read-only, granted, approval, denied)
and whether it worked. Your `review approve` and `review reject` answers are in it too.
"""


def _on(ctx: ShellContext) -> bool:
    return bool(ctx.ai_run)


def _state(ctx: ShellContext) -> Text:
    policy = ai_policy.load()
    out = Text()
    on = _on(ctx)
    out.append("AI access: ", style="sc.muted")
    out.append("on" if on else "off", style="sc.success" if on else "sc.error")
    out.append("  (any command runs at once)\n" if on else "  (read-only commands run at once, the rest asks)\n",
               style="sc.muted")
    where = policy.path if policy.exists and policy.path else None
    out.append("policy:    ", style="sc.muted")
    out.append(f"{len(policy.rules)} rule{'s' if len(policy.rules) != 1 else ''} in {where}\n" if where
               else f"no {ai_policy.FILE} (ai policy shows the starter)\n")
    for warning in policy.warnings:
        out.append(f"           ! {warning}\n", style="sc.warning")
    custom = [r for r in policy.roles.values() if not r.builtin]
    out.append("roles:     ", style="sc.muted")
    out.append(", ".join(sorted(policy.roles)) + (f"  ({len(custom)} of yours)" if custom else "") + "\n")
    entries = ai_audit.tail(5)
    if entries:
        out.append("\nlast actions (ai log has the rest):\n", style="sc.muted")
        for entry in entries:
            out.append("  " + _log_line(entry) + "\n")
    return out


def _log_line(entry: dict[str, Any]) -> str:
    who = " · ".join(x for x in (entry.get("agent"), entry.get("role")) if x) or entry.get("client", "")
    how = entry.get("decision") or entry.get("tier") or ("ok" if entry.get("ok") else "failed")
    stamp = str(entry.get("at", ""))[11:19] or str(entry.get("at", ""))
    return f"{stamp}  {entry.get('tool', ''):<22} {how:<9} {who:<18} {entry.get('summary', '')}"


def _log(args: list[str]) -> Any:
    parser = ArgParser("ai log")
    parser.add_argument("-n", type=int, default=20)
    parser.add_argument("--agent", default="")
    parser.add_argument("--json", action="store_true")
    opts = parser.parse_args(args)
    entries = ai_audit.tail(opts.n, opts.agent)
    if not entries:
        return Text("nothing logged yet" + (f" for {opts.agent}" if opts.agent else ""), style="sc.muted")
    if opts.json:
        return "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries)
    text = Text("\n".join(_log_line(e) for e in entries))
    return Styled(text, "\n".join(_log_line(e) for e in entries) + "\n")


def _policy() -> Any:
    policy = ai_policy.load()
    if not policy.exists:
        return Text(f"no policy file: AI access follows the ai_access setting.\n"
                    f"Copy ai-policy.toml.sample to {ai_policy.path()} to add rules.", style="sc.muted")
    table = Table(title=str(policy.path), title_style="sc.prompt", border_style="sc.border", header_style="sc.accent")
    for col in ("action", "match", "role", "folder", "until"):
        table.add_column(col)
    import time as _time

    for rule in policy.rules:
        until = "" if rule.until is None else _time.strftime("%Y-%m-%d %H:%M", _time.localtime(rule.until))
        style = {"deny": "sc.error", "ask": "sc.warning", "allow": "sc.success"}[rule.action]
        table.add_row(Text(rule.action, style=style), rule.match, rule.role, rule.cwd, until)
    if not policy.rules:
        table.add_row("(no rules)", "", "", "", "")
    return table


def _roles() -> Any:
    policy = ai_policy.load()
    table = Table(title="Roles (start a server with --role NAME)", title_style="sc.prompt",
                  border_style="sc.border", header_style="sc.accent")
    for col in ("role", "runs commands", "tools"):
        table.add_column(col)
    for role in sorted(policy.roles.values(), key=lambda r: (not r.builtin, r.name)):
        table.add_row(role.name + ("" if not role.builtin else " (built in)"),
                      {"none": "never", "read-only": "read-only", "policy": "as the policy says"}[role.run],
                      "all" if role.tools is None else ", ".join(sorted(role.tools)))
    return table


@builtin("ai", "What an AI may do here and what it did: access, policy, roles, log",
         "ai [on|off | log [-n N] [--agent NAME] [--json] | policy | roles]",
         category="shell", stateful=True, doc=AI_DOC)
def ai(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    if not args:
        return _state(ctx)
    action, rest = args[0], args[1:]
    if action in ("on", "off") and not rest:
        value = action == "on"
        settings.set_value(ctx.config, "ai_access", value)
        from shellcraft.builtins import _store_setting

        return _store_setting(ctx, "ai_access", value, "set to")
    if action == "log":
        return _log(rest)
    if action == "policy" and not rest:
        return _policy()
    if action == "roles" and not rest:
        return _roles()
    raise CommandError("usage: ai [on|off | log [-n N] [--agent NAME] [--json] | policy | roles]")
