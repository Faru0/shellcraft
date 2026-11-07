"""The built-in analyst role: host-analysis tools, the case file and the board, and no way to run a command."""

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
    assert {"sshrun", "hostscan", "baseline", "feed", "run_func", "add_finding", "claim_task", "send_tui_alert",
            "shellcraft_pipeline", "ip2geo"} <= names
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
