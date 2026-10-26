"""The event stream and the triggers: what an AI hears about without polling."""

import io
import json
import threading
import time

import anyio
import pytest
from mcp.client import Client
from rich.console import Console

from shellcraft import ai_policy, events, mainlink
from shellcraft import review_spool as spool
from shellcraft.context import ShellContext, to_text
from shellcraft.loader import ModuleRegistry
from shellcraft.mcp_server import build_server
from shellcraft.pipeline import run_line
from shellcraft.scrollback import Scrollback
from shellcraft.themes import UI, all_themes


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
    (tmp_path / ".home").mkdir()
    ai_policy._cache.clear()
    events.clear()
    return tmp_path / ".home"


@pytest.fixture
def real_shell(home, monkeypatch):
    from shellcraft import shell

    monkeypatch.setattr(shell, "create_prompt_toolkit_console", lambda cleanup: None)
    console = Console(file=io.StringIO(), width=120, force_terminal=False, color_system=None, record=True)
    ctx = ShellContext(registry=ModuleRegistry(home / "modules"), ui=UI(all_themes({})["nord"], console), config={})
    with shell.Shell(ctx) as sh:
        yield sh, ctx


def type_line(sh, monkeypatch, line):
    monkeypatch.setattr(sh, "_ask", lambda message: line)
    sh._iteration()


# ── the ring ──────────────────────────────────────────────────────────────────

def test_events_are_numbered_and_read_after_a_cursor(home):
    for i in range(5):
        events.emit("command", line=f"c{i}", status=0)
    first = events.since(0, limit=2)
    assert [e["line"] for e in first["events"]] == ["c0", "c1"] and first["cursor"] == 2
    rest = events.since(first["cursor"])
    assert [e["line"] for e in rest["events"]] == ["c2", "c3", "c4"] and rest["cursor"] == 5
    assert events.since(rest["cursor"])["events"] == []
    events.emit("job", number=1)
    assert [e["kind"] for e in events.since(0, ["job"])["events"]] == ["job"]


def test_since_can_wait_for_the_next_event(home):
    threading.Timer(0.2, lambda: events.emit("command", line="late", status=1)).start()
    started = time.monotonic()
    got = events.since(0, wait=5)
    assert [e["line"] for e in got["events"]] == ["late"] and time.monotonic() - started < 3
    assert events.since(got["cursor"], wait=0.2)["events"] == []


def test_the_ring_keeps_the_last_events_and_says_what_was_dropped(home):
    for i in range(events.MAX_EVENTS + 10):
        events.emit("command", line=str(i), status=0)
    got = events.since(3)
    assert got["dropped"] > 0 and len(got["events"]) == 100


# ── the screen ────────────────────────────────────────────────────────────────

def test_the_screen_numbers_lines_and_searches_them():
    s = Scrollback()
    s.command("echo one")
    first = s.mark()
    s.feed("alpha\nbeta\n")
    last = s.mark()
    s.command("echo two")
    s.feed("beta again\n")
    assert s.since(first, last) == ["alpha", "beta"]
    hits = s.search("beta", context=1)
    assert [h["command"] for h in hits] == ["echo one", "echo two"]
    assert hits[0]["context"] == ["alpha", "beta"]
    assert s.search("zzz") == []


# ── what the shell records ────────────────────────────────────────────────────

def test_a_command_becomes_an_event_with_its_output_range(real_shell, monkeypatch):
    sh, ctx = real_shell
    type_line(sh, monkeypatch, "echo hello")
    type_line(sh, monkeypatch, "cat nosuchfile")
    got = events.since(0, ["command"])["events"]
    assert [e["line"] for e in got] == ["echo hello", "cat nosuchfile"]
    assert got[0]["status"] == 0 and got[1]["status"] != 0
    assert got[0]["cwd"] and "seconds" in got[0]
    assert sh.scrollback.since(got[0]["first_line"], got[0]["last_line"]) == ["hello"]


def test_a_secret_is_hidden_in_the_event_like_in_the_history(real_shell, monkeypatch):
    sh, ctx = real_shell
    type_line(sh, monkeypatch, "settings VIRUSTOTAL_API_KEY sk-very-secret")
    assert "sk-very-secret" not in json.dumps(events.since(0)["events"])


def test_an_interrupt_is_an_event(real_shell):
    sh, ctx = real_shell
    sh._interrupted("a running command", "sleep 9", 1.5)
    got = events.since(0, ["interrupt"])["events"]
    assert got[0]["where"] == "a running command" and got[0]["line"] == "sleep 9"


def test_a_finished_job_is_one_event(real_shell):
    sh, ctx = real_shell
    run_line("echo hi &", ctx)
    for _ in range(100):
        if not any(j.running for j in ctx.jobs):
            break
        time.sleep(0.05)
    sh._report_finished()
    sh._report_finished()
    assert [e["kind"] for e in events.since(0)["events"]].count("job") == 1


# ── triggers ──────────────────────────────────────────────────────────────────

def test_on_fail_flags_a_failing_command_but_not_ctrl_c_or_success(real_shell, monkeypatch):
    sh, ctx = real_shell
    run_line("watch on-fail", ctx)
    type_line(sh, monkeypatch, "echo fine")
    sh._interrupted("a running command", "sleep 5", 1)
    assert spool.list_items("pending", "flag") == []
    type_line(sh, monkeypatch, "cat nosuchfile")
    (flag,) = spool.list_items("pending", "flag")
    assert flag["type"] == "event" and flag["title"].startswith("exit ") and "cat nosuchfile" in flag["title"]
    assert {a["name"] for a in flag["artifacts"]} >= {"event"}
    assert any("flagged for the AI" in a.message for a in ctx.alerts.current())


def test_a_match_trigger_reads_the_output_and_a_glob_narrows_it(real_shell, monkeypatch):
    sh, ctx = real_shell
    run_line("watch match 'denied|traceback' --cmd 'echo *' -p high", ctx)
    type_line(sh, monkeypatch, "echo all fine")
    type_line(sh, monkeypatch, "echo Permission DENIED here")
    (flag,) = spool.list_items("pending", "flag")
    assert flag["priority"] == "high" and "Permission DENIED here" in flag["artifacts"][1]["content"]


def test_the_same_command_is_not_flagged_again_at_once(real_shell, monkeypatch):
    sh, ctx = real_shell
    run_line("watch on-fail", ctx)
    type_line(sh, monkeypatch, "cat nosuchfile")
    type_line(sh, monkeypatch, "cat nosuchfile")
    assert len(spool.list_items("pending", "flag")) == 1


def test_a_dead_job_trigger(real_shell):
    sh, ctx = real_shell
    run_line("watch job-died", ctx)
    sh._fire_triggers(events.emit("job", number=3, title="build", command="make", status=2, killed=False, origin=""))
    sh._fire_triggers(events.emit("job", number=4, title="sleep", command="sleep", status=2, killed=True, origin=""))
    sh._fire_triggers(events.emit("job", number=5, title="ai", command="x", status=2, killed=False, origin="ai"))
    (flag,) = spool.list_items("pending", "flag")
    assert "job 3 died" in flag["title"]


def test_policy_file_triggers_and_the_watch_command(real_shell, home):
    sh, ctx = real_shell
    (home / ai_policy.FILE).write_text('[[trigger]]\non = "fail"\ncommand = "make*"\npriority = "high"\n'
                                       '[[trigger]]\non = "match"\n')
    ai_policy._cache.clear()
    assert [t.command for t in ai_policy.load().triggers] == ["make*"]
    assert any("needs a match" in w for w in ai_policy.load().warnings)
    run_line("watch job-died", ctx)
    listing = to_text(run_line("watch", ctx).output)
    assert "p1" in listing and "w1" in listing and "ai-policy.toml" in listing
    assert "removed 1" in to_text(run_line("watch off w1", ctx).output)
    with pytest.raises(Exception, match="ai-policy.toml"):
        run_line("watch off p1", ctx)
    with pytest.raises(Exception, match="regular expression"):
        run_line("watch match '('", ctx)


# ── through MCP ───────────────────────────────────────────────────────────────

def test_get_events_and_search_output_over_mcp(registry, real_shell, monkeypatch):
    sh, ctx = real_shell
    type_line(sh, monkeypatch, "echo needle in a haystack")
    server = build_server(registry)
    got = _json(_call(server, lambda c: c.call_tool("get_events", {"output_lines": 5})))
    command = [e for e in got["events"] if e["kind"] == "command"][0]
    assert command["line"] == "echo needle in a haystack" and command["output"] == "needle in a haystack"
    again = _json(_call(server, lambda c: c.call_tool("get_events", {"since": got["cursor"]})))
    assert again["events"] == []
    found = _json(_call(server, lambda c: c.call_tool("search_output", {"pattern": "NEEDLE"})))
    assert found["count"] >= 1 and found["matches"][0]["command"] == "echo needle in a haystack"
    bad = _call(server, lambda c: c.call_tool("search_output", {"pattern": "("}))
    assert bad.is_error and "regular expression" in bad.content[0].text


def test_get_events_waits_for_the_next_event(registry, real_shell, monkeypatch):
    sh, ctx = real_shell
    server = build_server(registry)
    threading.Timer(0.3, lambda: events.emit("command", line="soon", status=0)).start()
    started = time.monotonic()
    got = _json(_call(server, lambda c: c.call_tool("get_events", {"wait_seconds": 10})))
    assert [e["line"] for e in got["events"]] == ["soon"] and time.monotonic() - started < 5


def test_the_monitor_role_can_read_events(registry, real_shell):
    names = {t.name for t in _call(build_server(registry, role="monitor"), lambda c: c.list_tools()).tools}
    assert {"get_events", "search_output"} <= names
