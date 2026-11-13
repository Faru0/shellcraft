"""The built-in analyst role: host-analysis tools, the case file and the board, and no way to run a command."""

from pathlib import Path

import anyio
import pytest
from mcp.client import Client

from shellcraft import ai_policy, mainlink
from shellcraft.mcp_server import build_server


def _call(server, fn):
    async def main():
        async with Client(server) as client:
            return await fn(client)
    return anyio.run(main)


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / ".home"))
    monkeypatch.delenv(mainlink.ADDRESS_ENV, raising=False)
    monkeypatch.delenv(mainlink.KEY_ENV, raising=False)
    (tmp_path / ".home").mkdir()
    ai_policy._cache.clear()


def test_the_role_exists_and_runs_nothing():
    role = ai_policy.load().roles["analyst"]
    assert role.builtin and role.run == "none"
    assert "run_in_shell" not in role.tools and "request_session_grant" not in role.tools


def test_an_analyst_server_offers_the_host_tools_and_not_a_shell(registry):
    names = {t.name for t in _call(build_server(registry, role="analyst"), lambda c: c.list_tools()).tools}
    assert {"sshrun", "aggregator", "hostscan", "health", "baseline", "feed", "run_func", "add_finding", "claim_task",
            "watchplan", "report",
            "send_tui_alert", "shellcraft_pipeline", "ip2geo", "enrich", "ioc", "queryRdap", "queryShodan",
            "queryVirusTotal", "queryAbuseIPDB", "queryGreyNoise"} <= names
    assert not names & {"run_in_shell", "request_session_grant", "get_history", "get_clip"}


def test_an_analyst_cannot_run_a_command(registry):
    out = _call(build_server(registry, role="analyst"),
                lambda c: c.call_tool("run_in_shell", {"command": "ls", "reason": "x"}))
    assert out.is_error and "may not use" in out.content[0].text


def test_the_role_can_be_started_from_the_command_line(capsys):
    from shellcraft import cli

    assert "analyst" in cli._parser().format_help()


FAKE_SSH = """#!/bin/sh
for a; do last="$a"; done
exec sh -c "$last"
"""


def test_the_host_tools_work_through_the_mcp_server(registry, tmp_path, monkeypatch):
    """The typed parameters of the .skill files reach the modules in the right order."""
    import stat

    fake = tmp_path / "fakessh"
    fake.write_text(FAKE_SSH)
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    home = tmp_path / ".home"
    (home / "ssh-hosts.toml").write_text("[hosts.web01]\nai = true\n")
    (tmp_path / "cfg").write_text("Host web01\n")
    monkeypatch.setenv("SHELLCRAFT_SSH", str(fake))
    monkeypatch.setenv("SHELLCRAFT_SSH_CONFIG", str(tmp_path / "cfg"))
    server = build_server(registry, role="analyst")

    def call(name, args):
        return _call(server, lambda c: c.call_tool(name, args))

    hosts = call("sshrun", {"hosts": True})
    assert not hosts.is_error and "web01" in hosts.content[0].text
    ps = call("sshrun", {"host": "web01", "check": "ps"})
    assert not ps.is_error and ps.content[0].text.startswith("# sshrun web01 ps")
    status = call("sshrun", {"host": "web01", "check": "proc-status", "arg": [str(__import__("os").getpid())]})
    assert not status.is_error and "Name:" in status.content[0].text
    refused = call("sshrun", {"host": "web01", "check": "proc-status", "arg": ["1; id"]})
    assert refused.is_error and "not a valid pid" in refused.content[0].text
    flags = call("hostscan", {"subcommand": "ps", "stdin": ps.content[0].text})
    assert not flags.is_error and "flag" in flags.content[0].text
    learned = call("baseline", {"subcommand": "diff", "host": "web01", "check": "ps", "stdin": ps.content[0].text})
    assert not learned.is_error and "LEARNING" in learned.content[0].text
    plan_dir = home / "watchplans"
    plan_dir.mkdir()
    (plan_dir / "p.toml").write_text('[[hosts]]\nname = "web01"\nchecks = ["ps"]\n')
    tasks = call("watchplan", {"action": "tasks", "name": "p"})
    assert not tasks.is_error and "host:web01" in tasks.content[0].text


def test_the_aggregator_tool_works_through_the_mcp_server(registry, tmp_path, monkeypatch):
    """Option B end to end: read a device's EDR file from the aggregator, scan it, remember it."""
    import os
    import stat

    fake = tmp_path / "fakessh"
    fake.write_text(FAKE_SSH)
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    root = tmp_path / "edr"
    device = root / "192.168.1.2"
    device.mkdir(parents=True)
    ps_file = device / "ps-2026-11-07T1930.txt"
    ps_file.write_text("  PID  PPID USER ETIMES ARGS\n    1     0 root  900 /sbin/init\n"
                       "  812     1 www    90 nginx: worker process\n"
                       "  900   812 www    10 /bin/sh -c id\n")
    now = __import__("time").time()
    os.utime(ps_file, (now - 60, now - 60))
    home = tmp_path / ".home"
    (home / "ssh-hosts.toml").write_text("[hosts.edr-box]\nai = true\n")
    (home / "aggregators.toml").write_text(
        f'[aggregators.edr]\nhost = "edr-box"\nroot = "{root}"\n[aggregators.edr.files]\nps = "ps-*.txt"\n'
        '[aggregators.edr.devices]\nweb01 = "192.168.1.2"\n')
    (tmp_path / "cfg").write_text("Host edr-box\n")
    monkeypatch.setenv("SHELLCRAFT_SSH", str(fake))
    monkeypatch.setenv("SHELLCRAFT_SSH_CONFIG", str(tmp_path / "cfg"))
    server = build_server(registry, role="analyst")

    def call(name, args):
        return _call(server, lambda c: c.call_tool(name, args))

    listed = call("aggregator", {"list": True})
    assert not listed.is_error and "edr-box" in listed.content[0].text
    summary = call("aggregator", {"aggregator": "edr", "device": "web01"})
    assert not summary.is_error and "ps-2026-11-07T1930.txt" in summary.content[0].text
    read = call("aggregator", {"aggregator": "edr", "device": "web01", "kind": "ps"})
    assert not read.is_error and read.content[0].text.startswith("# aggregator edr web01 ps (untrusted EDR data")
    flags = call("hostscan", {"subcommand": "ps", "stdin": read.content[0].text})
    assert not flags.is_error and "web_child_shell" in flags.content[0].text
    learned = call("baseline", {"subcommand": "diff", "host": "web01", "check": "ps", "stdin": read.content[0].text})
    assert not learned.is_error and "LEARNING" in learned.content[0].text
    refused = call("aggregator", {"aggregator": "edr", "device": "../etc", "kind": "ps"})
    assert refused.is_error


def test_the_audit_log_says_which_host_and_check_a_tool_was_asked(registry, tmp_path, monkeypatch):
    import json
    import stat

    fake = tmp_path / "fakessh"
    fake.write_text(FAKE_SSH)
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    home = tmp_path / ".home"
    (home / "ssh-hosts.toml").write_text("[hosts.web01]\nai = true\n")
    (tmp_path / "cfg").write_text("Host web01\n")
    monkeypatch.setenv("SHELLCRAFT_SSH", str(fake))
    monkeypatch.setenv("SHELLCRAFT_SSH_CONFIG", str(tmp_path / "cfg"))
    server = build_server(registry, role="analyst")
    _call(server, lambda c: c.call_tool("sshrun", {"host": "web01", "check": "proc-status", "arg": ["1"],
                                                   "agent": "host-analyst"}))
    _call(server, lambda c: c.call_tool("hostscan", {"subcommand": "ps", "stdin": "1 0 root 5 /sbin/init\n"}))
    rows = [json.loads(line) for line in (home / "ai-audit.jsonl").read_text().splitlines()]
    ssh_row = next(r for r in rows if r["tool"] == "sshrun")
    assert ssh_row["summary"] == "host=web01 check=proc-status arg=1" and ssh_row["agent"] == "host-analyst"
    scan_row = next(r for r in rows if r["tool"] == "hostscan")
    assert scan_row["summary"] == "subcommand=ps" and "init" not in scan_row["summary"]   # piped text is left out


def test_live_and_state_changing_tools_are_never_answered_from_the_cache(registry, tmp_path, monkeypatch):
    """The server remembers module answers for a few minutes. sshrun, aggregator, baseline, watchplan and report
    must not use that memory: a host can change, and `watchplan done`, `baseline diff` and `report save` write
    state."""
    import stat
    import time

    counter = tmp_path / "calls"
    fake = tmp_path / "countingssh"
    fake.write_text(f'#!/bin/sh\nfor a; do last="$a"; done\necho x >> {counter}\nexec sh -c "$last"\n')
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    home = tmp_path / ".home"
    (home / "ssh-hosts.toml").write_text("[hosts.web01]\nai = true\n")
    (home / "watchplans").mkdir()
    (home / "watchplans" / "p.toml").write_text('[[hosts]]\nname = "web01"\nchecks = ["ps"]\n')
    (tmp_path / "cfg").write_text("Host web01\n")
    monkeypatch.setenv("SHELLCRAFT_SSH", str(fake))
    monkeypatch.setenv("SHELLCRAFT_SSH_CONFIG", str(tmp_path / "cfg"))
    server = build_server(registry, role="analyst")

    def call(name, args):
        return _call(server, lambda c: c.call_tool(name, args))

    tools = {t.name: t for t in _call(server, lambda c: c.list_tools()).tools}
    assert "fresh" not in tools["sshrun"].input_schema["properties"]        # nothing to ask for: it is always fresh
    assert "fresh" in tools["hostscan"].input_schema["properties"]          # a pure analyser may be remembered
    call("sshrun", {"host": "web01", "check": "sysinfo"})
    call("sshrun", {"host": "web01", "check": "sysinfo"})
    assert len(counter.read_text().split()) == 2                              # two logins, not one
    state = home / "watchplans" / ".state" / "p.json"
    call("watchplan", {"action": "done", "name": "p"})
    first = state.read_text()
    time.sleep(1.1)
    call("watchplan", {"action": "done", "name": "p"})
    assert state.read_text() != first                                         # the second `done` ran
    saved = {"action": "save", "unit": "host:web01", "status": "warn", "summary": "a new listener on 4444"}
    call("report", saved)
    call("report", saved)
    assert len(list((home / "reports" / "host" / "web01").glob("*.md"))) == 2  # both rounds left their report


def test_an_analyst_ends_a_unit_with_a_report(registry, tmp_path):
    """`report save` over MCP: the typed parameters and the body reach the module, the answer names the file, and
    the user's `fleet` view reads the same record."""
    from shellcraft import fleet

    server = build_server(registry, role="analyst")

    def call(args):
        return _call(server, lambda c: c.call_tool("report", args))

    quiet = call({"action": "save", "unit": "host:db01", "status": "ok", "summary": "7 checks, no change",
                  "changed": 0, "flags": 0, "plan": "prod"})
    assert not quiet.is_error and quiet.content[0].text == "host:db01: ok recorded\n"
    found = call({"action": "save", "unit": "host:web01", "status": "warn", "summary": "a new listener on 4444",
                  "changed": 2, "flags": 1, "plan": "prod", "stdin": "## What changed\n- ss: 0.0.0.0:4444\n"})
    assert not found.is_error
    path = found.content[0].text.split("report: ", 1)[1].strip()
    assert path.startswith(str(tmp_path / ".home" / "reports" / "host" / "web01")) and path.endswith("-warn.md")
    assert "0.0.0.0:4444" in Path(path).read_text()
    shown = call({"action": "show", "unit": "host:web01"})
    assert "# host:web01: warn" in shown.content[0].text and "## What changed" in shown.content[0].text
    listed = call({"action": "list"}).content[0].text
    assert "host:web01  warn" in listed and "host:db01  ok" in listed
    units = {unit.id: unit for unit in fleet.gather()}
    assert units["host:web01"].status == "warn" and units["host:web01"].changed == 2
    assert units["host:web01"].report == path and units["host:db01"].report == ""
    refused = call({"action": "save", "unit": "web01", "status": "ok", "summary": "x"})
    assert refused.is_error and "is not a unit" in refused.content[0].text


def test_an_analyst_can_enrich_an_indicator(registry, http, clean_env):
    server = build_server(registry, role="analyst")
    out = _call(server, lambda c: c.call_tool("enrich", {"targets": ["10.0.0.5"]}))
    assert not out.is_error and out.content[0].text.startswith("VERDICT unknown  10.0.0.5  a private address")
    assert http.urls == []  # a private address is sent to nobody
    out = _call(server, lambda c: c.call_tool("ioc", {"text": ["seen hxxp://evil[.]com/x from 10.0.0.5"],
                                                      "public": True}))
    assert out.content[0].text.split() == ["http://evil.com/x", "evil.com"]
