"""shellcraft --watch-rounds PLAN and its timer (shellcraft.rounds): one round of a watch plan with nobody at
the keyboard. A stand-in `claude` records what it was given and answers like the client; a stand-in
`systemctl` records its calls. The plan, its timing and the lock are the real ones."""

import json
import os
import stat
import sys
import time

import pytest

from shellcraft import alerts, analyst_setup, claude_run, cli, rounds
from shellcraft.config import config_dir

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="the stand-ins are #! scripts")

FAKE_CLAUDE = r'''#!%(python)s
import json, os, sys, time

argv = sys.argv[1:]


def held(option):
    with open(argv[argv.index(option) + 1], encoding="utf-8") as fh:
        return json.load(fh)


log = os.environ["FAKECLAUDE_LOG"]
calls = []
if os.path.exists(log):
    with open(log, encoding="utf-8") as fh:
        calls = json.load(fh)
calls.append({"argv": argv, "config": held("--mcp-config"), "agents": held("--agents"),
              "agents_path": argv[argv.index("--agents") + 1], "stdin": sys.stdin.read(), "cwd": os.getcwd()})
with open(log, "w", encoding="utf-8") as fh:
    json.dump(calls, fh)
mode = os.environ.get("FAKECLAUDE_MODE", "")


def say(event):
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


def block(**content):
    say({"type": "assistant", "message": {"content": [content]}})


if mode == "crash":
    sys.stderr.write("boom: not logged in\n")
    sys.exit(1)
say({"type": "system", "subtype": "init", "session_id": "s1", "mcp_servers": [{"name": "shellcraft", "status": "connected"}]})
block(type="tool_use", name="mcp__shellcraft__watchplan", input={"action": "tasks", "name": "p"})
block(type="tool_use", name="Agent", input={"subagent_type": "host-analyst", "prompt": "host web01"})
if mode == "hang":
    time.sleep(60)
if mode == "budget":
    say({"type": "result", "subtype": "error_max_budget_usd", "is_error": True, "total_cost_usd": 2.0})
    sys.exit(1)
block(type="text", text="1 unit checked.\nNothing new.")
say({"type": "result", "subtype": "success", "is_error": False, "result": "1 unit checked.\nNothing new.",
     "total_cost_usd": 0.4211, "session_id": "s1", "num_turns": 6})
'''

FAKE_SYSTEMCTL = """#!/bin/sh
echo "$*" >> "$FAKESYSTEMCTL_LOG"
[ "$2" = is-active ] && echo "${FAKESYSTEMCTL_STATE:-active}"
if [ -n "$FAKESYSTEMCTL_FAIL" ]; then echo "Failed to connect to bus: No medium found" >&2; exit 1; fi
exit 0
"""

PLAN = 'interval_minutes = 15\n\n[[hosts]]\nname = "web01"\nchecks = ["ps"]\n'


def _script(path, text):
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A ShellCraft folder with the watch plan `p`: one host, a round every 15 minutes."""
    home = tmp_path / "home"
    (home / "watchplans").mkdir(parents=True)
    (home / "watchplans" / "p.toml").write_text(PLAN)
    monkeypatch.setenv("SHELLCRAFT_HOME", str(home))
    return home


@pytest.fixture
def client(tmp_path, monkeypatch):
    """The stand-in client as SHELLCRAFT_CLAUDE; `.calls()` is what each run of it was given."""
    log = tmp_path / "claude-calls.json"
    monkeypatch.setenv(claude_run.CLIENT_ENV, str(_script(tmp_path / "fakeclaude", FAKE_CLAUDE % {"python": sys.executable})))
    monkeypatch.setenv("FAKECLAUDE_LOG", str(log))
    monkeypatch.delenv("FAKECLAUDE_MODE", raising=False)

    class Client:
        mode = staticmethod(lambda name: monkeypatch.setenv("FAKECLAUDE_MODE", name))

        @staticmethod
        def calls():
            return json.loads(log.read_text()) if log.exists() else []

    return Client


@pytest.fixture
def systemd(tmp_path, monkeypatch):
    """A stand-in systemctl and an empty unit folder; `.calls()` is what systemctl was asked."""
    log = tmp_path / "systemctl-calls"
    monkeypatch.setenv(rounds.SYSTEMCTL_ENV, str(_script(tmp_path / "fakesystemctl", FAKE_SYSTEMCTL)))
    monkeypatch.setenv("FAKESYSTEMCTL_LOG", str(log))
    monkeypatch.delenv("FAKESYSTEMCTL_FAIL", raising=False)
    monkeypatch.delenv("FAKESYSTEMCTL_STATE", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(rounds, "lingers", lambda: True)

    class Systemd:
        units = tmp_path / "xdg" / "systemd" / "user"

        @staticmethod
        def calls():
            return log.read_text().splitlines() if log.exists() else []

    return Systemd


@pytest.fixture
def said():
    return []


def _option(argv, name):
    return argv[argv.index(name) + 1]


def _log(home):
    path = home / "rounds.log"
    return [line.split("\t") for line in path.read_text().splitlines()] if path.exists() else []


def _alerts(home):
    spool = alerts.spool_dir(home)
    return [json.loads(path.read_text()) for path in sorted(spool.glob("*.json"))]


def _last_round(home, seconds_ago):
    state = home / "watchplans" / ".state"
    state.mkdir(exist_ok=True)
    (state / "p.json").write_text(json.dumps({"last": time.time() - seconds_ago}))


# ── one round ────────────────────────────────────────────────────────────────

@posix_only
def test_a_due_round_runs_the_orchestrator_once(home, client, registry, said):
    began = time.time()
    assert rounds.run_round(registry, "p", say=said.append) == 0
    (call,) = client.calls()
    argv = call["argv"]
    assert argv[0] == "-p" and _option(argv, "--agent") == "orchestrator"
    assert _option(argv, "--tools") == "Agent,ToolSearch" and _option(argv, "--max-budget-usd") == "2"
    assert argv[argv.index("--allowedTools") + 1:][:2] == ["mcp__shellcraft", "Agent"]
    assert "--strict-mcp-config" in argv
    # its only server is the analyst role, started the way --setup-analyst writes it
    assert call["config"] == {"mcpServers": {"shellcraft": analyst_setup.server_entry()}}
    assert call["config"]["mcpServers"]["shellcraft"]["args"][-3:] == ["--mcp", "--role", "analyst"]
    # the prompt names the plan and says nobody is there; it runs in the ShellCraft folder
    assert "watch plan 'p'" in call["stdin"] and "Nobody is at the keyboard" in call["stdin"]
    assert "Quiet hours do not apply now." in call["stdin"] and "`watchplan done p`" in call["stdin"]
    assert os.path.realpath(call["cwd"]) == os.path.realpath(home)
    # the agents come with the run, each with the skills it names, and the file is removed afterwards
    agents = call["agents"]
    assert {"orchestrator", "host-analyst", "edr-analyst", "feed-analyst", "log-reader"} <= set(agents)
    lead = agents["orchestrator"]
    assert lead["description"].startswith("Takes a job from the user")
    assert {"Agent", "ToolSearch", "mcp__shellcraft__watchplan", "mcp__shellcraft__send_tui_alert"} <= set(lead["tools"])
    assert lead["prompt"].startswith("You are the orchestrator.") and "---" not in lead["prompt"][:5]
    assert "# The `watch-rounds` skill\n\n# A watching round" in lead["prompt"]
    assert "# The `remote-triage` skill" in agents["host-analyst"]["prompt"]
    assert "mcp__shellcraft__sshrun" in agents["host-analyst"]["tools"] and "Agent" not in agents["host-analyst"]["tools"]
    assert not os.path.exists(call["agents_path"])
    # what it did is shown, and the run is one line in the log
    assert said[0].startswith("p: due now: no round has run yet; starting a round (at most $2.00, $0.00 spent today of $10.00)")
    assert "✦ watchplan: tasks" in said and "✦ Agent: host-analyst" in said
    assert "1 unit checked." in said and "Nothing new." in said and said[-1].startswith("p: round done in ")
    (row,) = _log(home)
    assert row[1:4] == ["p", "ok", "$0.42"] and row[4].endswith("s") and row[5] == "1 unit checked. Nothing new."
    assert abs(time.mktime(time.strptime(row[0], "%Y-%m-%d %H:%M:%S")) - began) < 5
    assert _alerts(home) == []
    # the next round is timed from the start of this one
    last = json.loads((home / "watchplans" / ".state" / "p.json").read_text())["last"]
    assert began - 1 <= last <= began + 5
    assert rounds.spent_today() == pytest.approx(0.42)


@posix_only
def test_nothing_is_started_before_a_round_is_due(home, client, registry, said):
    _last_round(home, 300)
    assert rounds.run_round(registry, "p", say=said.append) == 0
    assert client.calls() == [] and _log(home) == []
    assert len(said) == 1 and said[0].startswith("p: next round in ") and said[0].endswith("; nothing started")
    # a timer is never on the second: a round that is due within a minute and a half starts
    _last_round(home, 15 * 60 - 60)
    assert rounds.run_round(registry, "p", say=said.append) == 0
    assert len(client.calls()) == 1 and [row[2] for row in _log(home)] == ["ok"]


@posix_only
def test_quiet_hours_are_passed_on(home, client, registry, said):
    (home / "watchplans" / "p.toml").write_text('quiet_hours = "00:00-23:59"\n' + PLAN)
    if time.strftime("%H:%M") == "23:59":
        pytest.skip("the one minute of the day outside these quiet hours")
    assert rounds.run_round(registry, "p", say=said.append) == 0
    assert "Quiet hours apply now: only a `crit` alert is sent." in client.calls()[0]["stdin"]


@posix_only
def test_a_failed_round_is_logged_and_alerted_once(home, client, registry, said):
    client.mode("budget")
    assert rounds.run_round(registry, "p", say=said.append) == 1
    (row,) = _log(home)
    why = "stopped: the budget of $2 for one run was reached"
    assert row[1:4] == ["p", "failed", "$2.00"] and row[5] == why
    assert any(line.startswith("p: the round failed after ") and line.endswith(f", $2.00: {why}") for line in said)
    (alert,) = _alerts(home)
    assert alert["message"] == f"watch round p failed: {why}" and alert["level"] == "warn"
    assert alert["source"] == "rounds" and alert["content"] == "shellcraft --watch-rounds p"
    assert not (home / "watchplans" / ".state" / "p.json").exists()  # not recorded: the next call tries again
    # the same failure again: one more line, no second alert
    assert rounds.run_round(registry, "p", say=said.append) == 1
    assert len(_log(home)) == 2 and len(_alerts(home)) == 1
    # another failure is news
    client.mode("crash")
    assert rounds.run_round(registry, "p", say=said.append) == 1
    assert _log(home)[-1][2:] == ["failed", "-", _log(home)[-1][4], "boom: not logged in"]
    assert [a["message"] for a in _alerts(home)][-1] == "watch round p failed: boom: not logged in"
    assert rounds.spent_today() == pytest.approx(4.0)


def test_a_missing_client_is_a_failed_round(home, registry, said, monkeypatch):
    monkeypatch.setenv(claude_run.CLIENT_ENV, "/nonexistent/claude")
    assert rounds.run_round(registry, "p", say=said.append) == 1
    assert _log(home)[0][2] == "failed" and "is not a program" in _log(home)[0][5]
    assert len(_alerts(home)) == 1
    assert rounds.run_round(registry, "p", dry_run=True, say=said.append) == 1  # the dry run names it too
    assert "is not a program" in said[-1]


@posix_only
def test_the_days_budget_stops_further_rounds(home, client, registry, said):
    today = time.strftime("%Y-%m-%d")
    (home / "rounds.log").write_text(
        f"2020-01-01 03:00:00\tp\tok\t$50.00\t90s\tlong ago\n"
        f"{today} 00:00:01\tp\tok\t$6.00\t90s\tfine\n{today} 00:00:02\tother\tfailed\t$4.50\t90s\tbudget\n"
        "not a line of the log\n")
    assert rounds.spent_today() == pytest.approx(10.5)
    assert rounds.run_round(registry, "p", say=said.append) == 0
    assert client.calls() == []
    text = "the day's budget is used up ($10.50 spent today of $10.00); rounds start again tomorrow"
    assert said == [f"p: {text}"] and _log(home)[-1][1:] == ["p", "skipped", "-", "-", text]
    assert [a["message"] for a in _alerts(home)] == [f"watch plan p: {text}"]
    assert rounds.run_round(registry, "p", say=said.append) == 0  # said again, logged and alerted once
    assert len(_log(home)) == 5 and len(_alerts(home)) == 1
    assert rounds.run_round(registry, "p", day_budget=0, say=said.append) == 0  # 0: no limit
    assert len(client.calls()) == 1 and any(line.endswith("$10.50 spent today)") for line in said)


@posix_only
def test_two_rounds_of_one_plan_never_overlap(home, client, registry, said):
    with rounds.lock("p") as mine:
        assert mine
        with rounds.lock("p") as second:
            assert not second
        with rounds.lock("other") as other:
            assert other
        assert rounds.run_round(registry, "p", say=said.append) == 0
        assert said == ["p: a round of this plan is still running; nothing started"]
        assert client.calls() == [] and _log(home) == []
    assert rounds.run_round(registry, "p", say=said.append) == 0
    assert len(client.calls()) == 1


@posix_only
def test_a_round_that_runs_too_long_is_stopped(home, client, registry, said, monkeypatch):
    monkeypatch.setattr(rounds, "MAX_SECONDS", 1)
    client.mode("hang")
    began = time.monotonic()
    assert rounds.run_round(registry, "p", say=said.append) == 1
    assert time.monotonic() - began < 20
    assert _log(home)[0][2] == "failed" and _log(home)[0][5].startswith("stopped: it ran longer than ")
    with rounds.lock("p") as mine:  # and the lock is free again
        assert mine


@posix_only
def test_dry_run_shows_the_command_and_starts_nothing(home, client, registry, said):
    assert rounds.run_round(registry, "p", budget=0.75, dry_run=True, say=said.append) == 0
    assert client.calls() == [] and _log(home) == [] and not (home / "watchplans" / ".state").exists()
    assert said[0] == "p: due now: no round has run yet" and said[1] == "p: $0.00 spent today of $10.00"
    command = said[3]
    assert "--agent orchestrator" in command and "--max-budget-usd 0.75" in command
    assert "--mcp-config MCP-CONFIG.json" in command and "--agents AGENTS.json" in command
    assert any("--mcp --role analyst" in line for line in said) and any("host-analyst" in line for line in said)
    assert "  Run one round of the watch plan 'p' now, then stop." in said
    _last_round(home, 60)
    said.clear()
    assert rounds.run_round(registry, "p", dry_run=True, say=said.append) == 0
    assert said[0].endswith("; a round would not start now")


def test_a_plan_that_is_not_there_and_bad_amounts(home, registry, said):
    assert rounds.run_round(registry, "nope", say=said.append) == 2
    assert said[-1].startswith("shellcraft: no plan named 'nope'")
    assert rounds.run_round(registry, "../p", say=said.append) == 2
    assert rounds.run_round(registry, "p", budget=0, say=said.append) == 2
    assert rounds.run_round(registry, "p", day_budget=-1, say=said.append) == 2
    assert rounds.install_timer(registry, "nope", say=said.append) == 2
    assert rounds.install_timer(registry, "p", every=0, say=said.append) == 2
    assert _log(home) == []


def test_the_log_loses_its_older_half_when_it_grows(home, monkeypatch):
    monkeypatch.setattr(rounds, "LOG_MAX", 2000)
    for n in range(60):
        rounds.log("p", "ok", f"round {n}\twith a tab\nand a line", 0.1, 12.4)
    rows = rounds.runs("p")
    assert 10 < len(rows) < 60 and rows[-1][5] == "round 59 with a tab and a line" and rows[-1][3:5] == ["$0.10", "12s"]
    assert rounds.runs("other") == [] and (home / "rounds.log").stat().st_size <= 2000
    rounds.log("p", "ok", "x" * 1000)
    assert len(rounds.runs()[-1][5]) == rounds.TEXT_SHOWN


# ── the timer ────────────────────────────────────────────────────────────────

@posix_only
def test_install_timer_writes_two_units_and_starts_the_timer(home, systemd, registry, said, monkeypatch):
    monkeypatch.setenv("PATH", os.pathsep.join(["/opt/my tools/bin", *os.environ["PATH"].split(os.pathsep)[:2],
                                                "/opt/my tools/bin"]))
    monkeypatch.setenv("SSH_AUTH_SOCK", "/run/user/1000/ssh-agent")
    assert rounds.install_timer(registry, "p", say=said.append) == 0
    service = (systemd.units / "shellcraft-rounds-p.service").read_text()
    timer = (systemd.units / "shellcraft-rounds-p.timer").read_text()
    start = " ".join(analyst_setup.launcher())
    assert f"\nExecStart={start} --watch-rounds p --budget 2 --day-budget 10\n" in service
    assert "Type=oneshot\n" in service and "TimeoutStartSec=2100\n" in service
    # a user service has a bare environment: it gets the PATH (once each), the ssh agent and the ShellCraft folder
    path = os.pathsep.join(dict.fromkeys(os.environ["PATH"].split(os.pathsep)))
    assert f'\nEnvironment="PATH={path}"\n' in service and service.count("/opt/my tools/bin") == 1
    assert "\nEnvironment=SSH_AUTH_SOCK=/run/user/1000/ssh-agent\n" in service
    assert f"\nEnvironment=SHELLCRAFT_HOME={home}\n" in service
    assert timer == ("[Unit]\nDescription=ShellCraft: a round of the watch plan p every 15 min\n\n"
                     "[Timer]\nOnActiveSec=1min\nOnUnitActiveSec=15min\nAccuracySec=30s\n\n"
                     "[Install]\nWantedBy=timers.target\n")
    assert systemd.calls() == ["--user daemon-reload", "--user enable shellcraft-rounds-p.timer",
                               "--user restart shellcraft-rounds-p.timer"]
    assert said[-3].startswith("timer on: a round of 'p' every 15 min, at most $2.00 a round and $10.00 a day")
    assert said[-1] == "remove it:  shellcraft --remove-timer p" and rounds.timers() == ["p"]
    # again, with other numbers: the files are replaced and the timer restarted
    monkeypatch.setattr(rounds, "lingers", lambda: False)
    assert rounds.install_timer(registry, "p", every=45, budget=0.5, day_budget=0, say=said.append) == 0
    assert "OnUnitActiveSec=45min" in (systemd.units / "shellcraft-rounds-p.timer").read_text()
    assert "--budget 0.5 --day-budget 0\n" in (systemd.units / "shellcraft-rounds-p.service").read_text()
    assert "at most $0.50 a round;" in said[-4] and "loginctl enable-linger" in said[-1]


@posix_only
def test_install_timer_dry_run_and_a_systemd_that_says_no(home, systemd, registry, said, monkeypatch):
    assert rounds.install_timer(registry, "p", dry_run=True, say=said.append) == 0
    assert not systemd.units.exists() and systemd.calls() == []
    assert said[0] == f"would write  {systemd.units / 'shellcraft-rounds-p.service'}" and "    Type=oneshot" in said
    assert said[-1].endswith("systemctl --user enable --now shellcraft-rounds-p.timer")
    monkeypatch.setenv("FAKESYSTEMCTL_FAIL", "1")
    assert rounds.install_timer(registry, "p", say=said.append) == 1
    assert said[-1] == "shellcraft: systemctl --user daemon-reload failed: Failed to connect to bus: No medium found"


def test_without_systemd_a_crontab_line_is_printed(home, registry, said, monkeypatch, tmp_path):
    monkeypatch.setattr(rounds, "_systemctl", lambda: None)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert rounds.install_timer(registry, "p", say=said.append) == 0
    start = " ".join(analyst_setup.launcher())
    assert said[-1] == f"*/15 * * * * {start} --watch-rounds p --budget 2 --day-budget 10 >/dev/null 2>&1"
    assert "crontab -e" in said[0] and not (tmp_path / "xdg").exists()
    # minutes cron cannot say: ask every five minutes, a call with no round due starts nothing
    assert rounds.cron_line("p", 45, 2, 10).startswith("*/5 * * * * ")
    assert rounds.cron_line("p", 120, 2, 10).startswith("*/5 * * * * ")
    assert rounds.cron_line("p", 20, 2, 10).startswith("*/20 * * * * ")


@posix_only
def test_remove_timer(home, systemd, registry, said):
    assert rounds.remove_timer("p", say=said.append) == 0
    assert said == ["no timer is installed for 'p'"] and systemd.calls() == []
    rounds.install_timer(registry, "p", say=said.append)
    before = len(systemd.calls())
    assert rounds.remove_timer("p", dry_run=True, say=said.append) == 0
    assert rounds.timers() == ["p"] and len(systemd.calls()) == before
    assert rounds.remove_timer("p", say=said.append) == 0
    assert systemd.calls()[before:] == ["--user disable --now shellcraft-rounds-p.timer", "--user daemon-reload"]
    assert list(systemd.units.iterdir()) == [] and rounds.timers() == []
    assert said[-1] == "timer off: no more rounds of 'p' on their own"
    assert rounds.remove_timer("../x", say=said.append) == 2


def test_a_word_of_a_unit_file():
    assert rounds._unit_word("/usr/bin/python3") == "/usr/bin/python3"
    assert rounds._unit_word("/my tools/py") == '"/my tools/py"'
    assert rounds._unit_word('a"b\\c') == '"a\\"b\\\\c"'
    assert rounds._unit_word("PATH=/a%b/$c") == '"PATH=/a%%b/$c"'  # % is read everywhere,
    assert rounds._unit_word("/a%b/$c", command=True) == '"/a%%b/$$c"'  # $ only on a command line


@posix_only
def test_check_analyst_lists_the_timers_and_the_last_round(home, systemd, registry, said, monkeypatch, tmp_path):
    assert rounds.status() == []
    rounds.install_timer(registry, "p", every=30, say=said.append)
    assert rounds.status() == [("ok", "timer p: a round every 30 min"),
                               ("info", f"no round has run yet ({home / 'rounds.log'} is not there)")]
    rounds.log("p", "failed", "boom", 0.5, 12, at=time.mktime(time.strptime("2026-11-12 03:12:07", "%Y-%m-%d %H:%M:%S")))
    monkeypatch.setenv("FAKESYSTEMCTL_STATE", "inactive")
    assert rounds.status() == [
        ("fix", "timer p: a round every 30 min, but it is inactive: shellcraft --install-timer p"),
        ("fix", "last round: 2026-11-12 03:12:07 p failed, $0.50: boom")]
    rounds.log("p", "ok", "1 unit checked.")
    assert rounds.status()[-1][0] == "ok" and rounds.status()[-1][1].endswith(" p ok: 1 unit checked.")
    lines = analyst_setup.doctor(tmp_path, tmp_path / ".claude")  # --check-analyst shows them
    assert [line.text for line in lines][-2].startswith("timer p: a round every 30 min")


# ── the command line ─────────────────────────────────────────────────────────

@posix_only
def test_the_flags(home, client, systemd, capsys):
    assert cli.main(["--watch-rounds", "p", "--budget", "0.25", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "p: due now: no round has run yet" in out and "--max-budget-usd 0.25" in out
    assert cli.main(["--watch-rounds", "p", "--day-budget", "0"]) == 0
    assert "p: round done in " in capsys.readouterr().out and len(client.calls()) == 1
    assert config_dir() == home and [row[2] for row in _log(home)] == ["ok"]
    assert cli.main(["--install-timer", "p", "--every", "20", "--budget", "1"]) == 0
    assert "timer on: a round of 'p' every 20 min, at most $1.00 a round" in capsys.readouterr().out
    assert cli.main(["--remove-timer", "p"]) == 0
    assert "timer off" in capsys.readouterr().out
    for bad in (["--watch-rounds", "p", "--install-timer", "p"], ["--watch-rounds", "p", "--mcp"],
                ["--remove-timer", "p", "--install-ai"], ["--install-timer", "p", "func", "run", "x"]):
        assert cli.main(bad) == 2
        assert "go one at a time" in capsys.readouterr().err
    assert cli.main(["--watch-rounds", "nope"]) == 2


def test_the_agents_file_reaches_the_client_command():
    request = claude_run.Request(prompt="x", servers={}, agent="lead", agents={"lead": {"prompt": "y"}})
    argv = claude_run.command(request, "claude", "CONFIG", "AGENTS")
    assert _option(argv, "--agents") == "AGENTS" and _option(argv, "--agent") == "lead"
    assert "--agents" not in claude_run.command(request, "claude", "CONFIG")
