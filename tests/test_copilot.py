"""The co-pilot tools: what an AI reads from, and asks of, the interactive shell over MCP."""

import io
import json
import sys

import anyio
import pytest
from mcp.client import Client
from rich.console import Console

from shellcraft import mainlink
from shellcraft.context import ShellContext
from shellcraft.loader import ModuleRegistry
from shellcraft.mcp_server import SHELL_TOOLS, build_server, link_request
from shellcraft.themes import UI, all_themes

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="pseudo-terminals")


def _call(server, fn):
    async def main():
        async with Client(server) as client:
            return await fn(client)
    return anyio.run(main)


def _json(result):
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / ".home"))
    monkeypatch.delenv(mainlink.ADDRESS_ENV, raising=False)
    monkeypatch.delenv(mainlink.KEY_ENV, raising=False)
    monkeypatch.delenv("SHELLCRAFT_DEPTH", raising=False)
    return tmp_path / ".home"


@pytest.fixture
def real_shell(home, monkeypatch):
    """A real Shell, publishing its link, on a recording console."""
    from shellcraft import shell

    monkeypatch.setattr(shell, "create_prompt_toolkit_console", lambda cleanup: None)
    console = Console(file=io.StringIO(), width=120, force_terminal=False, color_system=None, record=True)
    ctx = ShellContext(registry=ModuleRegistry(home / "modules"), ui=UI(all_themes({})["nord"], console), config={})
    with shell.Shell(ctx) as sh:
        yield sh, ctx


def test_the_tools_are_listed_with_their_schemas(registry):
    tools = {t.name: t for t in _call(build_server(registry), lambda c: c.list_tools()).tools}
    assert set(SHELL_TOOLS) <= set(tools)
    assert tools["get_job_output"].input_schema["required"] == ["number"]
    assert tools["get_recent_output"].input_schema["properties"]["strip_ansi"]["default"] is True
    assert tools["set_clip"].input_schema["required"] == ["text"]
    assert "never echo it back" in tools["get_history"].description


def test_link_requests():
    assert link_request("get_recent_output", {}, "mcp · x") == ("scrollback", 200, True)
    assert link_request("get_recent_output", {"lines": 5, "strip_ansi": False}, "x") == ("scrollback", 5, False)
    assert link_request("get_job_output", {"number": 3}, "x") == ("job-output", 3, 200, True)
    assert link_request("get_history", {"n": 7}, "x") == ("history", 7)
    assert link_request("get_clip", {}, "x") == ("clip-get", 0)
    assert link_request("set_clip", {"text": "ls"}, "x") == ("clip-set", "ls")
    for name, args in (("get_recent_output", {"lines": 0}), ("get_recent_output", {"lines": "9"}),
                       ("get_job_output", {}), ("set_clip", {"text": ""}), ("get_history", {"n": 501})):
        with pytest.raises(ValueError):
            link_request(name, args, "x")


def test_the_read_tools_need_a_main_shell(registry, home):
    result = _call(build_server(registry), lambda c: c.call_tool("get_recent_output", {}))
    assert result.is_error and "mainlink.json" in result.content[0].text
    result = _call(build_server(registry), lambda c: c.call_tool("set_clip", {"text": "x"}))
    assert result.is_error


def test_the_read_tools_through_a_real_shell(registry, real_shell):
    sh, ctx = real_shell
    from shellcraft.pipeline import run_line

    sh.ui.console.print("[bold]hello[/bold] from a command")
    sh.scrollback.command("echo typed")
    run_line("x=1", ctx)
    ctx.history.append_string("settings CENSYS_API_TOKEN abc123")
    ctx.history.append_string("grep root auth.log")
    ctx.jobs.start_command("seq 2", lambda: (0, "1\n2\n"))
    server = build_server(registry)
    out = _json(_call(server, lambda c: c.call_tool("get_recent_output", {"lines": 50})))
    assert "hello from a command" in out["text"] and "❯ echo typed" in out["text"] and not out["truncated"]
    colored = _json(_call(server, lambda c: c.call_tool("get_recent_output", {"strip_ansi": False})))
    assert "\x1b[" in colored["text"]
    hist = _json(_call(server, lambda c: c.call_tool("get_history", {"n": 2})))["lines"]
    assert hist[-1] == "grep root auth.log" and "abc123" not in hist[-2] and "••" in hist[-2]
    jobs = _json(_call(server, lambda c: c.call_tool("get_jobs", {})))
    assert jobs[0]["number"] == 1 and jobs[0]["command"] == "seq 2" and jobs[0]["origin"] == ""
    import time

    for _ in range(100):
        if not ctx.jobs.get(1).running:
            break
        time.sleep(0.02)
    job = _json(_call(server, lambda c: c.call_tool("get_job_output", {"number": 1})))
    assert job["text"].strip() == "1\n2" and job["label"] == "done"
    missing = _call(server, lambda c: c.call_tool("get_job_output", {"number": 9}))
    assert missing.is_error and "no subshell 9" in missing.content[0].text
    assert _json(_call(server, lambda c: c.call_tool("set_clip", {"text": "jobs 1"}))) == {"set": True, "characters": 6}
    assert ctx.alerts.clip == "jobs 1"
    assert _json(_call(server, lambda c: c.call_tool("get_clip", {}))) == {"text": "jobs 1"}


def test_the_mcp_child_gets_the_link_in_its_environment(home, monkeypatch):
    from shellcraft import jobs as jobmod
    from shellcraft import mcp_child

    captured = {}

    class FakePopen:
        def __init__(self, argv, **kw):
            captured.update(kw)
            self.pid = 1

    monkeypatch.setattr(mcp_child.subprocess, "Popen", FakePopen)
    jobmod.MAIN_LINK_ENV.update({mainlink.ADDRESS_ENV: "/tmp/x", mainlink.KEY_ENV: "ab"})
    try:
        child = mcp_child.McpHttpChild("127.0.0.1", 38765, home / "modules", token="t")
        child._log.close()
        assert captured["env"][mainlink.ADDRESS_ENV] == "/tmp/x" and captured["env"][mainlink.KEY_ENV] == "ab"
    finally:
        jobmod.MAIN_LINK_ENV.clear()
