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


# ── run_in_shell: read-only runs free, the session grant, approval for the rest ──

def test_read_only_classification(real_shell):
    from shellcraft.ai_run import read_only, tier

    sh, ctx = real_shell
    ctx.config["aliases"] = {"ll": "ls -l"}
    for line in ("grep root auth.log", "cat a | sort | uniq -c", "echo hi", "jq .x data.json", "head -5 x",
                 "cat a 2>&1 | wc -l", "cat < in.txt", "grep x a ; grep y b"):
        assert read_only(line, ctx), line
    for line in ("cat f > g", "rm f", "cd /", "sub bash", "ls &", "for x in a b; do echo $x; done",
                 "ll", "export X=1", "env", "tee out", "echo hi 2> err", "nosuchcmd", "! grep x f",
                 "mkdir d", "x=1", "", "cat a |"):
        assert not read_only(line, ctx), line
    assert tier("rm f", ctx) == "approval"
    ctx.ai_run = True
    assert tier("rm f", ctx) == "granted" and tier("echo x", ctx) == "read-only"


def test_a_read_only_command_runs_at_once_with_its_trace(registry, real_shell):
    sh, ctx = real_shell
    server = build_server(registry)
    out = _json(_call(server, lambda c: c.call_tool("run_in_shell", {"command": "echo hi from the AI",
                                                                       "reason": "a test", "wait_seconds": 10})))
    assert out["mode"] == "read-only" and out["output"] == "hi from the AI\n" and out["status"] == 0
    assert not out["pending"] and out["number"] == 1
    job = ctx.jobs.get(1)
    assert job.origin == "ai" and job.title == "✦ AI: echo hi from the AI" and job.kind == "command"
    sh._report_finished()  # what the prompt does: the trace
    text = sh.ui.console.export_text()
    assert "[1] done: ✦ AI: echo hi from the AI" in text and "hi from the AI" in text
    bad = _call(server, lambda c: c.call_tool("run_in_shell", {"command": "echo x"}))
    assert bad.is_error and "`reason` is required" in bad.content[0].text


def test_a_command_that_changes_things_waits_for_approval_and_runs_on_approve(registry, real_shell, tmp_path):
    import threading

    from shellcraft import review_spool as spool
    from shellcraft.pipeline import run_line

    sh, ctx = real_shell
    server = build_server(registry)
    out = _json(_call(server, lambda c: c.call_tool(
        "run_in_shell", {"command": "mkdir made-by-the-ai", "reason": "a folder for the report", "wait_seconds": 0})))
    assert out["mode"] == "approval" and out["status"] == "pending" and out["result"] is None
    item_id = out["item_id"]
    assert not (tmp_path / "made-by-the-ai").exists()  # nothing ran
    sh._review_check()
    alert = ctx.alerts.current()[0]
    assert alert.message == "AI wants to run: mkdir made-by-the-ai" and alert.content == f"review show {item_id}"
    listing = run_line("review", ctx).output.renderable.plain
    assert "AI runs" in listing and item_id in listing
    from shellcraft.context import to_text

    note = to_text(run_line(f"review approve {item_id} fine", ctx).output)
    assert "approved and run: mkdir made-by-the-ai" in note and "exit 0" in note
    assert (tmp_path / "made-by-the-ai").is_dir()
    assert "❯ mkdir made-by-the-ai" in sh.ui.console.export_text()
    status = _json(_call(server, lambda c: c.call_tool("get_review_status", {"item_id": item_id})))
    assert status["status"] == "approved" and status["type"] == "command" and status["result"]["status"] == 0
    assert status["result"]["error"] is None and status["decision"]["notes"] == "fine"
    shown = to_text(run_line(f"review show {item_id}", ctx).output)
    assert "── result (exit 0" in shown
    sh._review_check()
    assert ctx.alerts.current() == []

    # the AI waits for the answer: a reject arrives while it waits
    def reject_soon():
        import time

        for _ in range(100):
            pending = spool.list_items("pending", "review")
            if pending:
                run_line(f"review reject {pending[0]['id']} not now", ctx)
                return
            time.sleep(0.05)

    threading.Thread(target=reject_soon, daemon=True).start()
    out = _json(_call(server, lambda c: c.call_tool(
        "run_in_shell", {"command": "rm -r made-by-the-ai", "reason": "clean up", "wait_seconds": 10})))
    assert out["mode"] == "rejected" and out["result"] is None and out["decision"]["notes"] == "not now"
    assert (tmp_path / "made-by-the-ai").is_dir()

    # a failing command: the AI gets the error
    out = _json(_call(server, lambda c: c.call_tool(
        "run_in_shell", {"command": "nosuchcommand-xyz", "reason": "x", "wait_seconds": 0})))
    note = to_text(run_line(f"review approve {out['item_id']}", ctx).output)
    assert "the AI is told" in note
    status = _json(_call(server, lambda c: c.call_tool("get_review_status", {"item_id": out["item_id"]})))
    assert status["result"]["error"] and status["result"]["status"] != 0


def test_approving_a_command_item_needs_the_interactive_shell(home, tmp_path):
    from shellcraft import review_spool as spool
    from shellcraft.pipeline import PipelineError, run_line

    ctx = ShellContext(registry=ModuleRegistry(tmp_path / "modules"))
    item = spool.create("review", "rm x", "why", [spool.artifact("command", "rm x", "text")], "medium", "command")
    with pytest.raises(PipelineError, match="interactive shell"):
        run_line(f"review approve {item['id']}", ctx)
    assert spool.get(item["id"])["status"] == "pending"


def test_the_session_grant(registry, real_shell, tmp_path):
    import threading

    from shellcraft import review_spool as spool
    from shellcraft.context import to_text
    from shellcraft.pipeline import run_line

    sh, ctx = real_shell
    server = build_server(registry)
    assert not ctx.ai_run and not any("✦ run" in t for _s, t, *_ in sh._segment("mcp"))
    assert "On" in to_text(run_line("settings ai_run on", ctx).output)  # the old name of ai_access
    assert ctx.ai_run and any("✦ run" in t for _s, t, *_ in sh._segment("mcp"))
    assert "ai_access" in to_text(run_line("settings", ctx).output)
    out = _json(_call(server, lambda c: c.call_tool(
        "run_in_shell", {"command": "mkdir granted-dir", "reason": "x", "wait_seconds": 10})))
    assert out["mode"] == "granted" and out["status"] == 0 and (tmp_path / "granted-dir").is_dir()
    assert ctx.jobs.get(out["number"]).title == "✦ AI: mkdir granted-dir"
    run_line("settings ai_run toggle", ctx)
    assert not ctx.ai_run
    assert ctx.config["settings"]["ai_access"] is False  # saved, like any setting

    # the AI asks for the grant; the user approves it
    def approve_soon():
        import time

        for _ in range(100):
            pending = spool.list_items("pending", "review")
            if pending:
                assert pending[0]["type"] == "grant"
                sh._review_check()
                assert ctx.alerts.current()[0].message.startswith("AI asks to run commands for this session: ")
                run_line(f"review approve {pending[0]['id']}", ctx)
                return
            time.sleep(0.05)

    threading.Thread(target=approve_soon, daemon=True).start()
    out = _json(_call(server, lambda c: c.call_tool(
        "request_session_grant", {"reason": "triage auth.log for the next hour", "wait_seconds": 10})))
    assert out["granted"] is True and out["status"] == "approved" and ctx.ai_run
    assert "AI grant" in run_line("review --all", ctx).output.renderable.plain
    missing = _call(server, lambda c: c.call_tool("request_session_grant", {"reason": " "}))
    assert missing.is_error


def test_tell_sends_a_message_the_ai_reads_as_a_flag(registry, home, tmp_path):
    from shellcraft import review_spool as spool
    from shellcraft.context import to_text
    from shellcraft.pipeline import PipelineError, run_line

    ctx = ShellContext(registry=registry)
    ctx.interactive = True
    note = to_text(run_line("tell -p high look at auth.log, something is off", ctx).output)
    assert "✓ told the AI" in note
    item = spool.list_items("pending", "flag")[0]
    assert item["type"] == "message" and item["priority"] == "high"
    assert item["description"] == "look at auth.log, something is off" and item["title"].startswith("look at")
    run_line("echo three failed | tell -m 'failed count'", ctx)
    piped = spool.list_items("pending", "flag")[0]
    assert piped["title"] == "failed count" and piped["description"] == "three failed"
    assert "message" in run_line("review", ctx).output.renderable.plain
    with pytest.raises(PipelineError, match="say something"):
        run_line("tell", ctx)
    server = build_server(registry)
    items = _json(_call(server, lambda c: c.call_tool("get_flagged_items", {"kind": "flag"})))
    assert [i["type"] for i in items] == ["message", "message"]
    assert items[1]["description"] == "look at auth.log, something is off"
