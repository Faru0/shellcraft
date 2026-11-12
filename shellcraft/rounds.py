"""Rounds with nobody at the keyboard: `shellcraft --watch-rounds PLAN`, and the timer that calls it.

One call is one round of one watch plan (modules/watchplan.py). Nothing is started when the next
round is not due yet, when a round of the same plan is still running (a lock file) or when
today's rounds already cost the day's budget. Otherwise the Claude Code client runs once, with no
terminal, as the `orchestrator` agent. Its only tools are ShellCraft's analyst server
(`--mcp --role analyst`) and its sub agents, and the agents are handed to it from this ShellCraft's
own ai/ folder with the skills they name, so nothing has to be installed in ~/.claude.

Each run adds one line to ~/.shellcraft/rounds.log: time, plan, result, cost, seconds, text. A
round that fails sends a `warn` alert; with no shell open it waits in the spool and the next shell
shows it as missed (shellcraft.alerts).

`--install-timer PLAN` writes a systemd user timer that makes this call every N minutes, and
`--remove-timer PLAN` takes it away again.
"""

from __future__ import annotations

import contextlib
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterator

from shellcraft import ai_install, alerts, analyst_setup, claude_run
from shellcraft.config import config_dir
from shellcraft.modkit import ModuleError

LOG = "rounds.log"
LOG_MAX = 512 * 1024  # bytes; past it the older half of the lines goes
DEFAULT_BUDGET = 2.0  # USD one round may cost
DEFAULT_DAY_BUDGET = 10.0  # USD all rounds of one day may cost (0: no limit)
MAX_SECONDS = 30 * 60  # a round that runs longer is stopped, so it cannot hold the lock for ever
GRACE = 90  # seconds a round may start early: a timer never fires at the exact second
AGENT = "orchestrator"
OWN_TOOLS = ("Agent", "ToolSearch")  # of the client's own tools: sub agents, and looking up a tool's description
ALLOWED = (f"mcp__{claude_run.SERVER}", "Agent")  # used without asking: nobody is there to ask
SYSTEMCTL_ENV = "SHELLCRAFT_SYSTEMCTL"  # the systemctl to call, when it is not the one on PATH
UNIT_PREFIX = "shellcraft-rounds-"
TEXT_SHOWN = 300  # characters of a result kept in the log
SOURCE = "rounds"  # who the failure alert is from

Say = Callable[[str], None]

PROMPT = """Run one round of the watch plan '{plan}' now, then stop.

A timer started this round (shellcraft --watch-rounds). Nobody is at the keyboard, and maybe no \
ShellCraft shell is open.

- The round is due: do not ask `watchplan due` first and do not wait. {quiet}
- Follow the `watch-rounds` skill from step 2 to step 7, once. There is no next round for you: \
the timer starts it.
- Do not call ask_user, open_subshell or get_events with a wait: nobody can answer. What needs a \
decision goes into the finding and, when it is urgent, into request_human_review.
- An alert you send with send_tui_alert is kept and shown when the user next opens a shell. Send \
the one summary alert of the round by the rule of the skill, and no alert when nothing is new.
- End with `watchplan done {plan}` and a summary of at most three lines: how many units you \
checked, what is new, and the worst finding.
"""


class RoundError(Exception):
    """A round or a timer could not be set up; the message is one line for the user."""


# ── the plan ─────────────────────────────────────────────────────────────────

def _watchplan(registry: Any, *args: str) -> str:
    """One `watchplan` call. Plans are read, checked and timed there, and only there."""
    spec = registry.get("watchplan")
    if spec is None:
        raise RoundError("the watchplan module is not there (or it is turned off: modules enable watchplan)")
    try:
        return str(spec.run(list(args), ""))
    except ModuleError as exc:
        raise RoundError(str(exc).removeprefix("watchplan: ")) from None


def interval_minutes(registry: Any, plan: str) -> int:
    """How often the plan wants a round. Raises RoundError for a plan that is missing or not valid."""
    found = re.search(r"every (\d+) min", _watchplan(registry, "check", plan))
    return int(found.group(1)) if found else 15


def due(registry: Any, plan: str) -> tuple[bool, str, bool]:
    """(a round is due, what watchplan said, quiet hours apply now)."""
    said = _watchplan(registry, "due", plan).strip()
    wait = re.match(r"next round in (\d+) s", said)
    return wait is None or int(wait.group(1)) <= GRACE, said, "quiet hours now" in said


# ── the agents ───────────────────────────────────────────────────────────────

def _split(text: str) -> tuple[dict[str, str], str]:
    """An agent or skill file as (the `key: value` lines between its two --- lines, the text after them)."""
    if not text.startswith("---"):
        return {}, text
    head, _, body = text[3:].partition("\n---")
    fields: dict[str, str] = {}
    for line in head.splitlines():
        key, colon, value = line.partition(":")
        if colon:
            fields[key.strip()] = value.strip()
    return fields, body.lstrip("\n")


def team() -> dict[str, Any]:
    """The orchestrator and its analysts in the shape the client's --agents takes. Each one carries
    the skills its text names, so a round does not depend on what is installed in ~/.claude."""
    items = ai_install.catalog()
    agents: dict[str, Any] = {}
    for item in items:
        if item.kind != "agent" or item.name not in ai_install.TEAM:
            continue
        fields, body = _split(item.source.read_text(encoding="utf-8"))
        for skill in ai_install.needs(item, items):
            body += f"\n\n# The `{skill.name}` skill\n\n{_split(skill.source.read_text(encoding='utf-8'))[1].strip()}\n"
        tools = [tool.strip() for tool in fields.get("tools", "").split(",") if tool.strip()]
        if "ToolSearch" not in tools:
            tools.append("ToolSearch")  # with it a tool's description is read when needed, not sent every turn
        agents[item.name] = {"description": fields.get("description", ""), "prompt": body, "tools": tools}
    if AGENT not in agents:
        raise RoundError(f"the {AGENT} agent is missing from {ai_install.SOURCE}")
    return agents


def request(plan: str, budget: float, quiet: bool = False) -> claude_run.Request:
    home = config_dir()
    home.mkdir(parents=True, exist_ok=True)
    note = "Quiet hours apply now: only a `crit` alert is sent." if quiet else "Quiet hours do not apply now."
    return claude_run.Request(
        prompt=PROMPT.format(plan=plan, quiet=note), servers={claude_run.SERVER: analyst_setup.server_entry()},
        tools=ALLOWED, own_tools=OWN_TOOLS, budget=budget, agent=AGENT, agents=team(), cwd=str(home))


# ── the log ──────────────────────────────────────────────────────────────────

def log_path() -> Path:
    return config_dir() / LOG


def _one_line(text: str, shown: int = TEXT_SHOWN) -> str:
    line = " ".join(text.split())
    return line if len(line) <= shown else line[:shown - 1].rstrip() + "…"


def log(plan: str, result: str, text: str, cost: float | None = None, seconds: float | None = None,
        at: float | None = None) -> None:
    """Add one run: `2026-11-12 03:12:07<TAB>plan<TAB>ok|failed|skipped<TAB>$0.42<TAB>95s<TAB>text`."""
    path = log_path()
    when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(at))
    fields = [when, plan, result, "-" if cost is None else f"${cost:.2f}",
              "-" if seconds is None else f"{seconds:.0f}s", _one_line(text)]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\t".join(fields) + "\n")
    try:
        if path.stat().st_size > LOG_MAX:
            lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
            path.write_text("".join(lines[len(lines) // 2:]), encoding="utf-8")
    except OSError:
        pass  # a long log is not worth failing a round for


def runs(plan: str | None = None) -> list[list[str]]:
    """The logged runs, oldest first, as [time, plan, result, cost, seconds, text]; of one plan when named."""
    try:
        lines = log_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = [line.split("\t", 5) for line in lines]
    return [row for row in rows if len(row) == 6 and (plan is None or row[1] == plan)]


def spent_today() -> float:
    """What the rounds of today cost so far, all plans together."""
    today = time.strftime("%Y-%m-%d")
    total = 0.0
    for row in runs():
        if row[0].startswith(today) and row[3].startswith("$"):
            try:
                total += float(row[3][1:])
            except ValueError:
                pass
    return total


# ── one round ────────────────────────────────────────────────────────────────

@contextlib.contextmanager
def lock(plan: str) -> Iterator[bool]:
    """True while this process is the only one running a round of the plan. The lock is on an open
    file, so it is gone when the process is, however it ended."""
    path = config_dir() / "watchplans" / ".state" / f"{plan}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as fh:
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        yield True


def _tool_line(name: str, given: dict[str, Any]) -> str:
    """One tool call as a line: `✦ sshrun: hugo ps`, `✦ Agent: host-analyst`."""
    short = name.removeprefix(f"mcp__{claude_run.SERVER}__")
    detail = next((given[key] for key in ("subagent_type", "pipeline", "args", "action", "title", "message", "name")
                   if given.get(key)), "")
    if isinstance(detail, list):
        detail = " ".join(str(part) for part in detail)
    return f"✦ {short}" + (f": {_one_line(str(detail))[:100]}" if detail else "")


def _alert(plan: str, level: str, message: str, say: Say) -> None:
    try:
        alerts.send(message, level, source=SOURCE, content=f"shellcraft --watch-rounds {plan}")
    except OSError as exc:
        say(f"{plan}: the alert could not be written ({exc.strerror or exc})")


def _money(amount: float) -> str:
    return f"${amount:.2f}"


def run_round(registry: Any, plan: str, *, budget: float = DEFAULT_BUDGET, day_budget: float = DEFAULT_DAY_BUDGET,
              dry_run: bool = False, say: Say = print) -> int:
    """One round of the plan when one is due. 0: done, or nothing to do; 1: the round failed;
    2: the plan or the numbers are not right."""
    if budget <= 0 or day_budget < 0:
        say("shellcraft: --budget takes an amount above 0, --day-budget 0 (no limit) or more")
        return 2
    try:
        is_due, said, quiet = due(registry, plan)
        wanted = request(plan, budget, quiet)
    except RoundError as exc:
        say(f"shellcraft: {exc}")
        return 2
    spent = spent_today()
    over = bool(day_budget) and spent >= day_budget
    today = f"{_money(spent)} spent today" + (f" of {_money(day_budget)}" if day_budget else "")
    if dry_run:
        return _dry_run(plan, wanted, said, is_due, over, today, say)
    if not is_due:
        say(f"{plan}: {said}; nothing started")
        return 0
    before = runs(plan)[-1:]  # the run before this one: what it says twice in a row is only alerted once
    if over:
        text = f"the day's budget is used up ({today}); rounds start again tomorrow"
        say(f"{plan}: {text}")
        if not (before and before[0][2] == "skipped" and before[0][0][:10] == time.strftime("%Y-%m-%d")):
            log(plan, "skipped", text)
            _alert(plan, "warn", f"watch plan {plan}: {text}", say)
        return 0
    with lock(plan) as mine:
        if not mine:
            say(f"{plan}: a round of this plan is still running; nothing started")
            return 0
        say(f"{plan}: {said}; starting a round (at most {_money(budget)}, {today})")
        began = time.time()
        result = _run(wanted, say)
        if not result.error:
            try:
                _watchplan(registry, "done", plan, f"{began:.0f}")  # the next round is timed from the start
            except RoundError as exc:
                say(f"{plan}: the round could not be recorded ({exc})")
    cost = "" if result.cost is None else f", {_money(result.cost)}"
    if result.error:
        log(plan, "failed", result.error, result.cost, result.seconds, began)
        say(f"{plan}: the round failed after {result.seconds:.0f} s{cost}: {result.error}")
        if not (before and before[0][2] == "failed" and before[0][5] == _one_line(result.error)):
            _alert(plan, "warn", f"watch round {plan} failed: {result.error}", say)
        return 1
    log(plan, "ok", result.text or "the round ended without a summary", result.cost, result.seconds, began)
    say(f"{plan}: round done in {result.seconds:.0f} s{cost}")
    return 0


def _dry_run(plan: str, wanted: claude_run.Request, said: str, is_due: bool, over: bool, today: str, say: Say) -> int:
    say(f"{plan}: {said}" + ("" if is_due else "; a round would not start now"))
    say(f"{plan}: {today}" + ("; the day's budget is used up, a round would not start" if over else ""))
    try:
        client = claude_run.find_client()
    except claude_run.ClaudeError as exc:
        say(f"shellcraft: {exc}")
        return 1
    argv = claude_run.command(wanted, client, "MCP-CONFIG.json", "AGENTS.json")
    say("would run, in " + str(wanted.cwd) + ":")
    say("  " + " ".join(shlex.quote(word) for word in argv))
    server = wanted.servers[claude_run.SERVER]
    say("MCP-CONFIG.json: the shellcraft server is "
        + " ".join(shlex.quote(str(word)) for word in [server["command"], *server["args"]]))
    say(f"AGENTS.json: {', '.join(sorted(wanted.agents or {}))}")
    say("the prompt, on its standard input:")
    for line in wanted.prompt.strip().splitlines():
        say("  " + line)
    return 0


def _run(wanted: claude_run.Request, say: Say) -> claude_run.Result:
    """Run the client to its end, showing what it writes and the tools it calls."""
    stop = threading.Event()
    limit = threading.Timer(MAX_SECONDS, stop.set)
    limit.daemon = True
    limit.start()
    result = claude_run.Result(error="the client ended without a result")
    partial = ""
    events = claude_run.run(wanted, stop)
    try:
        for kind, value in events:
            if kind == "text":
                *whole, partial = (partial + value).split("\n")
                for line in whole:
                    say(line)
            elif kind in ("break", "tool", "note", "result") and partial:
                say(partial)
                partial = ""
            if kind == "tool":
                say(_tool_line(*value))
            elif kind == "note":
                say(f"! {value}")
            elif kind == "result":
                result = value
    except claude_run.ClaudeError as exc:
        result = claude_run.Result(error=str(exc))
    except KeyboardInterrupt:
        stop.set()
        result = claude_run.Result(error="interrupted")
    finally:
        limit.cancel()
        events.close()
    if result.error == "stopped":
        result.error = f"stopped: it ran longer than {MAX_SECONDS / 60:.0f} minutes"
    return result


# ── the timer ────────────────────────────────────────────────────────────────

def unit_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME", "").strip()
    return (Path(base) if base else Path.home() / ".config") / "systemd" / "user"


def unit_name(plan: str) -> str:
    return f"{UNIT_PREFIX}{plan}"


def _systemctl() -> str | None:
    named = os.environ.get(SYSTEMCTL_ENV, "").strip()
    if named:
        return shutil.which(named) or (named if os.path.isfile(named) else None)
    return shutil.which("systemctl") if sys.platform.startswith("linux") else None


def _user_systemd(systemctl: str, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run([systemctl, "--user", *args], capture_output=True, text=True, timeout=30,
                              stdin=subprocess.DEVNULL, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess([systemctl, "--user", *args], 1, "", str(exc))


def own_command(plan: str, budget: float, day_budget: float) -> list[str]:
    """The command a timer runs: this ShellCraft, the way the analyst server is started too."""
    return [*analyst_setup.launcher(), "--watch-rounds", plan, "--budget", f"{budget:g}",
            "--day-budget", f"{day_budget:g}"]


def _unit_word(word: str, command: bool = False) -> str:
    """One word of an Environment= line, or of an ExecStart= line (`command`), where systemd reads $ too."""
    word = word.replace("%", "%%")
    if command:
        word = word.replace("$", "$$")
    if re.fullmatch(r"[A-Za-z0-9_@%+=:,./-]+", word):
        return word
    return '"' + word.replace("\\", "\\\\").replace('"', '\\"') + '"'


def unit_files(plan: str, every: int, budget: float, day_budget: float) -> dict[str, str]:
    """The two unit files of a plan's timer, by file name."""
    # A user service starts with a bare environment. It gets what a round needs: PATH (claude, ssh),
    # the ssh agent that holds your keys, and the ShellCraft folder and client when you named them.
    kept = {name: os.environ[name] for name in ("PATH", "SSH_AUTH_SOCK", "SHELLCRAFT_HOME", claude_run.CLIENT_ENV,
                                                "SHELLCRAFT_SSH", "SHELLCRAFT_SSH_CONFIG") if os.environ.get(name)}
    if "PATH" in kept:
        kept["PATH"] = os.pathsep.join(dict.fromkeys(kept["PATH"].split(os.pathsep)))  # each folder once
    env = "".join(f"Environment={_unit_word(f'{name}={value}')}\n" for name, value in kept.items())
    command = " ".join(_unit_word(word, command=True) for word in own_command(plan, budget, day_budget))
    service = (f"[Unit]\nDescription=ShellCraft: one round of the watch plan {plan}\n\n"
               f"[Service]\nType=oneshot\nExecStart={command}\n{env}TimeoutStartSec={MAX_SECONDS + 300}\n")
    timer = (f"[Unit]\nDescription=ShellCraft: a round of the watch plan {plan} every {every} min\n\n"
             f"[Timer]\nOnActiveSec=1min\nOnUnitActiveSec={every}min\nAccuracySec=30s\n\n"
             "[Install]\nWantedBy=timers.target\n")
    return {f"{unit_name(plan)}.service": service, f"{unit_name(plan)}.timer": timer}


def cron_line(plan: str, every: int, budget: float, day_budget: float) -> str:
    """A crontab line for a machine without systemd. A call when no round is due costs nothing,
    so a plan whose minutes do not fit cron's fields is simply asked more often."""
    when = f"*/{every} * * * *" if every < 60 and 60 % every == 0 else "*/5 * * * *"
    return f"{when} {' '.join(shlex.quote(word) for word in own_command(plan, budget, day_budget))} >/dev/null 2>&1"


def install_timer(registry: Any, plan: str, *, every: int | None = None, budget: float = DEFAULT_BUDGET,
                  day_budget: float = DEFAULT_DAY_BUDGET, dry_run: bool = False, say: Say = print) -> int:
    if budget <= 0 or day_budget < 0:
        say("shellcraft: --budget takes an amount above 0, --day-budget 0 (no limit) or more")
        return 2
    try:
        minutes = interval_minutes(registry, plan) if every is None else every
    except RoundError as exc:
        say(f"shellcraft: {exc}")
        return 2
    if not 1 <= minutes <= 7 * 1440:
        say("shellcraft: --every takes minutes, 1 or more")
        return 2
    systemctl = _systemctl()
    if systemctl is None:
        say("systemd is not here, so no timer was installed. This crontab line does the same (crontab -e):")
        say(cron_line(plan, minutes, budget, day_budget))
        return 0
    folder = unit_dir()
    files = unit_files(plan, minutes, budget, day_budget)
    for name, text in files.items():
        say(f"{'would write' if dry_run else 'wrote'}  {folder / name}")
        if dry_run:
            for line in text.splitlines():
                say("    " + line)
            continue
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(text, encoding="utf-8")
    timer = f"{unit_name(plan)}.timer"
    if dry_run:
        say(f"would run    systemctl --user daemon-reload; systemctl --user enable --now {timer}")
        return 0
    for args in (("daemon-reload",), ("enable", timer), ("restart", timer)):  # restart: a changed --every counts now
        done = _user_systemd(systemctl, *args)
        if done.returncode != 0:
            say(f"shellcraft: systemctl --user {' '.join(args)} failed: "
                f"{_one_line(done.stderr or done.stdout) or f'status {done.returncode}'}")
            return 1
    say(f"timer on: a round of '{plan}' every {minutes} min, at most {_money(budget)} a round"
        + (f" and {_money(day_budget)} a day" if day_budget else "") + "; the first one in a minute")
    say(f"see it:     systemctl --user list-timers '{UNIT_PREFIX}*'    and    tail {log_path()}")
    say(f"remove it:  shellcraft --remove-timer {plan}")
    if not lingers():
        say("note: the timer only runs while you are logged in. For rounds at any time, once: "
            "loginctl enable-linger")
    return 0


def lingers() -> bool:
    """Whether this user's services keep running after the last logout (True when it cannot be told)."""
    loginctl = shutil.which("loginctl")
    if loginctl is None:
        return True
    try:
        import getpass

        said = subprocess.run([loginctl, "show-user", getpass.getuser(), "-p", "Linger"], capture_output=True,
                              text=True, timeout=10, stdin=subprocess.DEVNULL, check=False).stdout
    except (OSError, subprocess.TimeoutExpired, KeyError):
        return True
    return "Linger=no" not in said


def remove_timer(plan: str, *, dry_run: bool = False, say: Say = print) -> int:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,40}", plan):
        say(f"shellcraft: '{plan}' is not a plan name")
        return 2
    folder = unit_dir()
    files = [folder / f"{unit_name(plan)}{ending}" for ending in (".timer", ".service")]
    there = [path for path in files if path.exists()]
    if not there:
        say(f"no timer is installed for '{plan}'" + (f" (installed: {', '.join(timers())})" if timers() else ""))
        return 0
    systemctl = _systemctl()
    if dry_run:
        for path in there:
            say(f"would remove  {path}")
        return 0
    if systemctl is not None:
        _user_systemd(systemctl, "disable", "--now", files[0].name)  # not there or not running: nothing to stop
    for path in there:
        path.unlink()
        say(f"removed  {path}")
    if systemctl is not None:
        _user_systemd(systemctl, "daemon-reload")
    say(f"timer off: no more rounds of '{plan}' on their own")
    return 0


def timers() -> list[str]:
    """The plans that have a timer installed."""
    try:
        return sorted(path.name[len(UNIT_PREFIX):-len(".timer")] for path in unit_dir().glob(f"{UNIT_PREFIX}*.timer"))
    except OSError:
        return []


def status() -> list[tuple[str, str]]:
    """For --check-analyst: one (status, text) per installed timer, and the last run in the log."""
    lines: list[tuple[str, str]] = []
    systemctl = _systemctl()
    for plan in timers():
        text = (unit_dir() / f"{unit_name(plan)}.timer").read_text(encoding="utf-8", errors="replace")
        every = re.search(r"OnUnitActiveSec=(\d+)min", text)
        state = _user_systemd(systemctl, "is-active", f"{unit_name(plan)}.timer").stdout.strip() if systemctl else ""
        on = state == "active"
        lines.append(("ok" if on else "fix",
                      f"timer {plan}: a round every {every.group(1) if every else '?'} min"
                      + ("" if on else f", but it is {state or 'not running'}: shellcraft --install-timer {plan}")))
    last = runs()[-1:]
    if last:
        when, plan, result, cost, seconds, text = last[0]
        spent = "" if cost == "-" else f", {cost}"
        lines.append(("ok" if result != "failed" else "fix",
                      f"last round: {when} {plan} {result}{spent}" + (f": {_one_line(text, 120)}" if text else "")))
    elif lines:
        lines.append(("info", f"no round has run yet ({log_path()} is not there)"))
    return lines
