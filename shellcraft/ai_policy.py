"""What an AI may do in this shell: the policy file, roles, and the verdict on a command.

`~/.shellcraft/ai-policy.toml` is optional. Without it the AI runs commands as the `ai_access`
setting says (on by default: everything runs at once, off: read-only at once, the rest asks).
With it you add rules and roles:

    [[rule]]
    action = "deny"                # allow | ask | deny
    match  = "rm -r*"              # a glob on the command line
    # role = "worker"              # only for this role
    # cwd  = "/home/me/work/*"     # only while the shell is in a matching folder
    # until = "2026-12-31 18:00"   # the rule stops applying after this time

    [role.scout]
    run   = "read-only"            # none | read-only | policy
    tools = ["get_recent_output", "get_jobs", "send_tui_alert"]   # omit for every tool

    [[trigger]]                    # an event worth waking the AI for (watch does the same at the prompt)
    on      = "fail"               # fail (a command exited nonzero) | match (its output matches) | job-died
    # match = "denied|Traceback"   # for on = "match": a regex, case-insensitive
    # command = "make*"            # only for command lines matching this glob
    # priority = "high"            # low | medium | high

    [budget]
    module_calls = 200             # module and pipeline calls per MCP server process

    [cache]
    seconds = 300                  # how long a module's answer is reused (0 turns it off)

Rules are read top to bottom. `deny` and `ask` match when any command of a line does (a line
may hold several, joined with ; && || | or &). `allow` needs every command of the line to match an
allow rule, and a line with a redirection or a command substitution never matches an allow rule
unless the rule's own pattern names it, so `git status*` does not also allow `git status > ~/x`.

Three roles are built in: `monitor` (reads, alerts, never runs a command), `orchestrator` and
`worker` (the rules above apply). A server started with `--role NAME` offers only what that role
may use, and the shell checks the role again before it runs anything.
"""

from __future__ import annotations

import fnmatch
import re
import time
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

FILE = "ai-policy.toml"
ACTIONS = ("allow", "ask", "deny")
RUN_MODES = ("none", "read-only", "policy")
TRIGGER_ON = ("fail", "match", "job-died")
PRIORITIES = ("low", "medium", "high")
DEFAULT_CACHE_SECONDS = 300.0

# What a role that only watches may use (tool names; ones that don't exist yet are harmless).
READ_TOOLS = frozenset({
    "get_flagged_items", "get_review_status", "get_recent_output", "get_jobs", "get_job_output", "get_history",
    "get_clip", "send_tui_alert", "get_events", "search_output", "list_tasks", "list_findings", "get_finding",
})


@dataclass(frozen=True)
class Rule:
    action: str
    match: str
    role: str = ""
    cwd: str = ""
    until: float | None = None  # epoch seconds

    def expired(self, now: float | None = None) -> bool:
        return self.until is not None and (time.time() if now is None else now) > self.until


@dataclass(frozen=True)
class Role:
    name: str
    run: str = "policy"
    tools: frozenset[str] | None = None  # None: every tool
    builtin: bool = False


@dataclass(frozen=True)
class Trigger:
    on: str  # fail | match | job-died
    match: str = ""  # a regex on the output, for on = "match"
    command: str = ""  # a glob on the command line
    priority: str = "medium"
    id: str = ""


BUILTIN_ROLES = {
    "monitor": Role("monitor", "none", READ_TOOLS, True),
    "orchestrator": Role("orchestrator", "policy", None, True),
    "worker": Role("worker", "policy", None, True),
}


@dataclass
class Policy:
    rules: list[Rule] = field(default_factory=list)
    triggers: list[Trigger] = field(default_factory=list)
    roles: dict[str, Role] = field(default_factory=lambda: dict(BUILTIN_ROLES))
    budget: int | None = None
    cache_seconds: float = DEFAULT_CACHE_SECONDS
    warnings: list[str] = field(default_factory=list)
    path: Path | None = None
    exists: bool = False


def path(home: Path | None = None) -> Path:
    if home is None:
        from shellcraft.config import config_dir

        home = config_dir()
    return Path(home) / FILE


_cache: dict[str, tuple[float, Policy]] = {}


def load(home: Path | None = None) -> Policy:
    """The policy file, parsed again only when it changed. A bad file never raises: its problems
    are in `warnings` and the parts that parsed are used."""
    target = path(home)
    try:
        mtime = target.stat().st_mtime_ns
    except OSError:
        return Policy(path=target)
    key = str(target)
    cached = _cache.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    policy = parse(target.read_text(encoding="utf-8", errors="replace"), target)
    _cache[key] = (mtime, policy)
    return policy


def parse(text: str, source: Path | None = None) -> Policy:
    policy = Policy(path=source, exists=True)
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        policy.warnings.append(f"{FILE}: {exc}")
        return policy
    for number, raw in enumerate(data.get("rule") or [], 1):
        rule = _rule(raw, number, policy.warnings)
        if rule is not None:
            policy.rules.append(rule)
    for number, raw in enumerate(data.get("trigger") or [], 1):
        trigger = _trigger(raw, number, policy.warnings)
        if trigger is not None:
            policy.triggers.append(trigger)
    roles = data.get("role")
    for name, raw in (roles.items() if isinstance(roles, dict) else ()):
        role = _role(str(name), raw, policy.warnings)
        if role is not None:
            policy.roles[role.name] = role
    budget = (data.get("budget") or {}).get("module_calls") if isinstance(data.get("budget"), dict) else None
    if isinstance(budget, int) and not isinstance(budget, bool) and budget >= 0:
        policy.budget = budget
    cache = data.get("cache")
    seconds = cache.get("seconds") if isinstance(cache, dict) else None
    if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and seconds >= 0:
        policy.cache_seconds = float(seconds)
    return policy


def _rule(raw: Any, number: int, warnings: list[str]) -> Rule | None:
    if not isinstance(raw, dict):
        warnings.append(f"{FILE}: rule {number} is not a table")
        return None
    action, match = str(raw.get("action", "")).lower(), raw.get("match")
    if action not in ACTIONS:
        warnings.append(f"{FILE}: rule {number}: action must be one of {', '.join(ACTIONS)}")
        return None
    if not isinstance(match, str) or not match.strip():
        warnings.append(f"{FILE}: rule {number}: match is required")
        return None
    until = raw.get("until")
    stamp: float | None = None
    if until is not None:
        stamp = _time(until)
        if stamp is None:
            warnings.append(f"{FILE}: rule {number}: until is not a date and time")
            return None
    return Rule(action, match.strip(), str(raw.get("role", "") or ""), str(raw.get("cwd", "") or ""), stamp)


def _trigger(raw: Any, number: int, warnings: list[str]) -> Trigger | None:
    if not isinstance(raw, dict):
        warnings.append(f"{FILE}: trigger {number} is not a table")
        return None
    on, match = str(raw.get("on", "")).lower(), str(raw.get("match", "") or "")
    priority = str(raw.get("priority", "medium") or "medium").lower()
    if on not in TRIGGER_ON:
        warnings.append(f"{FILE}: trigger {number}: on must be one of {', '.join(TRIGGER_ON)}")
        return None
    if priority not in PRIORITIES:
        warnings.append(f"{FILE}: trigger {number}: priority must be one of {', '.join(PRIORITIES)}")
        return None
    if on == "match":
        try:
            re.compile(match)
        except re.error as exc:
            warnings.append(f"{FILE}: trigger {number}: match is not a regex ({exc})")
            return None
        if not match:
            warnings.append(f"{FILE}: trigger {number}: on = \"match\" needs a match")
            return None
    return Trigger(on, match, str(raw.get("command", "") or ""), priority, f"p{number}")


def _time(value: Any) -> float | None:
    if isinstance(value, datetime):
        return value.timestamp()
    try:
        return datetime.fromisoformat(str(value).strip().replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _role(name: str, raw: Any, warnings: list[str]) -> Role | None:
    if not isinstance(raw, dict) or not re.fullmatch(r"[A-Za-z][\w.-]{0,31}", name):
        warnings.append(f"{FILE}: role {name!r} is not valid")
        return None
    run = str(raw.get("run", "policy")).lower()
    if run not in RUN_MODES:
        warnings.append(f"{FILE}: role {name}: run must be one of {', '.join(RUN_MODES)}")
        return None
    tools = raw.get("tools")
    allowed = frozenset(str(t) for t in tools) if isinstance(tools, list) else None
    return Role(name, run, allowed)


# ── asking the policy ─────────────────────────────────────────────────────────

_SPLIT = re.compile(r"\|\||&&|[;|&\n]")
_UNSAFE = re.compile(r"[<>`]|\$\(")


def segments(command: str) -> list[str]:
    """The commands of a line, split on ; && || | & and newlines (quotes are not understood: a
    quoted operator splits too, which only ever makes a deny or ask rule match more)."""
    return [s.strip() for s in _SPLIT.split(command) if s.strip()]


def _applies(rule: Rule, role: str | None, cwd: str, now: float) -> bool:
    if rule.expired(now) or (rule.role and rule.role != (role or "")):
        return False
    return not rule.cwd or fnmatch.fnmatchcase(cwd, rule.cwd)


def evaluate(policy: Policy, command: str, role: str | None = None, cwd: str = "") -> tuple[str | None, Rule | None]:
    """("deny" | "ask" | "allow", the rule) for a command line, or (None, None) when no rule
    decides. A deny anywhere beats an ask, an ask beats an allow."""
    now = time.time()
    rules = [r for r in policy.rules if _applies(r, role, cwd, now)]
    if not rules or not command.strip():
        return None, None
    parts = segments(command)
    for action in ("deny", "ask"):
        for rule in rules:
            if rule.action == action and any(fnmatch.fnmatchcase(p, rule.match) for p in [command.strip(), *parts]):
                return action, rule
    allows = [r for r in rules if r.action == "allow"]
    first: Rule | None = None
    for part in parts:
        hit = next((r for r in allows if fnmatch.fnmatchcase(part, r.match)
                    and (not _UNSAFE.search(part) or _UNSAFE.search(r.match))), None)
        if hit is None:
            return None, None
        first = first or hit
    return ("allow", first) if first is not None else (None, None)


def role_of(policy: Policy, name: str | None) -> Role | None:
    return policy.roles.get(name) if name else None


def role_run(policy: Policy, name: str | None) -> str:
    """`none`, `read-only` or `policy` for a role; an unlabelled caller follows the policy."""
    role = role_of(policy, name)
    return role.run if role is not None else "policy"


def role_allows_tool(policy: Policy, name: str | None, tool: str) -> bool:
    role = role_of(policy, name)
    return role is None or role.tools is None or tool in role.tools
