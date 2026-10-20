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


def _wait(pred, timeout=8.0):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


@posix_only
def test_open_subshell_opens_a_named_lite_shell_that_prints_the_brief(registry, real_shell):
    sh, ctx = real_shell
    server = build_server(registry)
    out = _json(_call(server, lambda c: c.call_tool(
        "open_subshell", {"name": "auth.log malware", "title": "Possible malware in auth.log",
                          "text": "3 root logins from 203.0.113.9\nnext: grep 203.0.113.9 auth.log",
                          "alert_level": "warn"})))
    assert out["number"] == 1 and out["title"] == "AI · auth.log malware"
    job = ctx.jobs.get(1)
    try:
        assert job.origin == "ai" and job.name == "AI · auth.log malware" and job.kind == "program"
        assert "--brief" in job.command and "--no-banner" in job.command
        alert = ctx.alerts.current()[0]
        assert alert.message == "Possible malware in auth.log" and alert.level == "warn"
        assert alert.content == "jobs 1" and alert.source.startswith("mcp · ")
        assert ctx.jobs.notifications[-1].startswith("[1] ✦ AI · auth.log malware opened by mcp · ")
        # the lite shell printed the brief and consumed the file
        assert _wait(lambda: "203.0.113.9" in job.output)
        assert "Possible malware in auth.log" in job.output  # the header (colored, so not one string)
        brief = next(w for w in job.command.split() if w.endswith(".json"))
        assert _wait(lambda: not __import__("os").path.exists(brief))
        jobs = _json(_call(server, lambda c: c.call_tool("get_jobs", {})))
        assert jobs[0]["origin"] == "ai" and jobs[0]["title"] == "AI · auth.log malware"
        from shellcraft.footer import panel_lines

        row = panel_lines(list(ctx.jobs), 0, width=100, stack=True)[-1]
        assert "✦ AI · auth.log malware" in row.text and any(s[2] == "ai" for s in row.spans)
    finally:
        ctx.jobs.kill(job)
    bad = _call(server, lambda c: c.call_tool("open_subshell", {"name": "x", "text": "   "}))
    assert bad.is_error and "`text` is required" in bad.content[0].text


def test_show_brief_prints_the_brief_and_deletes_the_file(real_shell, home):
    from shellcraft import briefs

    sh, ctx = real_shell
    path = briefs.write("Possible malware", "malware", "line one\nline two", "mcp · claude-code", str(home), home)
    assert path.is_file() and (path.stat().st_mode & 0o777) == 0o600
    sh.show_brief(path)
    text = sh.ui.console.export_text()
    assert "✦ AI · Possible malware" in text and "from mcp · claude-code" in text and "line two" in text
    assert "`exit` returns to main" in text and not path.exists()
    sh.show_brief(path)  # gone: says so, no crash
    assert "missing or" in sh.ui.console.export_text()
    long = briefs.write("t", "n", "x" * 70000, "s", str(home), home)
    data = briefs.read(long)
    assert data["text"].startswith("x" * 100) and "[cut:" in data["text"] and len(data["text"]) < 66000
    old = briefs.write("t", "n", "old", "s", str(home), home)
    import os

    os.utime(old, (1, 1))
    assert briefs.sweep(home) == 1 and not old.exists()


def test_the_lite_shell_takes_a_brief_on_the_command_line(tmp_path, started):
    (tmp_path / "m.py").write_text("def run(args, stdin):\n    return ''\n")
    from shellcraft import cli

    brief = tmp_path / "b.json"
    assert cli.main(["--lite", "--brief", str(brief), "--modules", str(tmp_path)]) == 0
    assert started[-1].brief == brief and started.briefs[-1] == brief
    assert cli.main(["--no-banner", "--brief", str(brief), "--modules", str(tmp_path)]) == 0
    assert started[-1].brief is None  # only a lite shell prints one
