"""The review loop: the spool shared by the shell and the MCP servers, `mark`, `review`, the MCP
tools and resource, and the shell's alerts for the AI's requests."""

import io
import json
import os
import threading
import time
from concurrent.futures import ProcessPoolExecutor

import pytest
from rich.console import Console

from shellcraft import review_spool as spool
from shellcraft.context import ShellContext, to_text
from shellcraft.loader import ModuleRegistry
from shellcraft.pipeline import PipelineError, run_line
from shellcraft.themes import UI, all_themes


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


@pytest.fixture
def ictx(home, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx = ShellContext(registry=ModuleRegistry(tmp_path / "modules"))
    ctx.interactive = True
    return ctx


# ── the spool ────────────────────────────────────────────────────────────────

def test_create_list_find_and_summary(home):
    flag = spool.create("flag", "tests fail", "see below", [spool.artifact("stdin", "E assert 1 == 2\n")],
                        "high", "test-failure")
    req = spool.create("review", "drop the users table", "migration 42", created_by="mcp · claude-code")
    assert flag["id"].startswith("r-") and flag["status"] == "pending" and flag["rev"] == 1
    assert (spool.folder() / f"{flag['id']}.json").exists()
    if os.name != "nt":
        assert (spool.folder().stat().st_mode & 0o777) == 0o700
    assert [i["id"] for i in spool.list_items()] == [req["id"], flag["id"]]  # newest first
    assert [i["id"] for i in spool.list_items(kind="review")] == [req["id"]]
    assert spool.find(flag["id"][-6:])["title"] == "tests fail"
    with pytest.raises(spool.SpoolError, match="no review item"):
        spool.find("zzzz")
    with pytest.raises(spool.SpoolError, match="not an item id"):
        spool.get("../config")
    summary = spool.summary(flag)
    assert "content" not in json.dumps(summary["artifacts"]) and summary["artifacts"][0]["bytes"] == 16
    with pytest.raises(spool.SpoolError, match="priority"):
        spool.create("flag", "x", priority="urgent")


def test_artifacts_keep_their_end():
    big = "".join(f"line {i}\n" for i in range(60000))
    a = spool.artifact("pytest", big)
    assert a["truncated"] and len(a["content"]) == spool.MAX_ARTIFACT and a["content"].endswith("line 59999\n")
    assert a["bytes"] == len(big.encode())
    small = spool.artifact("x", "ok", "nope")
    assert not small["truncated"] and small["kind"] == "text"


def test_transitions(home):
    flag = spool.create("flag", "f")
    with pytest.raises(spool.SpoolError, match="not a review request"):
        spool.decide(flag["id"], "approved")
    assert spool.resolve(flag["id"], "fixed in abc123", "mcp · x")["status"] == "resolved"
    with pytest.raises(spool.SpoolError, match="is resolved"):
        spool.resolve(flag["id"])
    req = spool.create("review", "r")
    done = spool.decide(req["id"], "rejected", "not today")
    assert done["status"] == "rejected" and done["decision"]["notes"] == "not today" and done["rev"] == 2
    with pytest.raises(spool.SpoolError, match="can't become approved"):
        spool.decide(req["id"], "approved")
    assert spool.resolve(req["id"], "left alone")["status"] == "resolved"


def test_old_closed_items_are_pruned_pending_ones_kept(home):
    old_done, old_pending = spool.create("flag", "a"), spool.create("flag", "b")
    spool.resolve(old_done["id"])
    for item_id in (old_done["id"], old_pending["id"]):
        path = spool.folder() / f"{item_id}.json"
        data = json.loads(path.read_text())
        data["updated"] = "2020-01-01T00:00:00Z"
        path.write_text(json.dumps(data))
    assert [i["id"] for i in spool.list_items()] == [old_pending["id"]]
    assert not (spool.folder() / f"{old_done['id']}.json").exists()


def test_locks_stale_and_held(home, monkeypatch):
    item = spool.create("review", "r")
    lock = spool.folder() / f"{item['id']}.lock"
    lock.write_text("12345")
    os.utime(lock, (time.time() - 60, time.time() - 60))  # left by a process that died
    assert spool.decide(item["id"], "approved")["status"] == "approved" and not lock.exists()
    other = spool.create("review", "r2")
    held = spool.folder() / f"{other['id']}.lock"
    held.write_text("1")
    monkeypatch.setattr(spool, "LOCK_WAIT", 0.1)
    with pytest.raises(spool.SpoolError, match="being changed by another process"):
        spool.decide(other["id"], "approved")


def _race(args):
    home, item_id, n = args
    os.environ["SHELLCRAFT_HOME"] = home
    try:
        spool.decide(item_id, "approved" if n % 2 else "rejected", f"process {n}", by=f"p{n}")
        return "won"
    except spool.SpoolError:
        return "lost"


def _make(home):
    os.environ["SHELLCRAFT_HOME"] = home
    return spool.create("flag", "from a process")["id"]


def test_several_processes_at_once(home):
    with ProcessPoolExecutor(8) as pool:
        made = list(pool.map(_make, [str(home)] * 16))
    assert len(set(made)) == 16 and len(spool.list_items()) == 16  # every file whole, no id twice
    item = spool.create("review", "who answers first")
    with ProcessPoolExecutor(8) as pool:
        results = list(pool.map(_race, [(str(home), item["id"], n) for n in range(8)]))
    assert results.count("won") == 1  # one decision; the others see it is no longer pending
    final = spool.get(item["id"])
    assert final["rev"] == 2 and final["decision"]["by"] == next(f"p{n}" for n, r in enumerate(results) if r == "won")
    assert not list(spool.folder().glob("*.lock")) and not list(spool.folder().glob(".*tmp*"))


# ── mark and review ──────────────────────────────────────────────────────────

def test_mark_pipe_files_diff_and_tee(ictx, tmp_path):
    out = to_text(run_line("echo FAILED test_login | mark --type test-failure -p high -n 'since this morning'", ictx).output)
    item = spool.list_items()[0]
    assert "✓ marked r-" in out and item["kind"] == "flag" and item["type"] == "test-failure"
    assert item["priority"] == "high" and item["description"] == "since this morning"
    assert item["artifacts"][0]["content"] == "FAILED test_login\n" and item["title"].startswith("test-failure in ")
    (tmp_path / "a.txt").write_text("one\ntwo\n")
    (tmp_path / "b.txt").write_text("one\nthree\n")
    run_line("mark --file a.txt --file b.txt -m 'two files'", ictx)
    files = spool.list_items()[0]
    assert [a["name"] for a in files["artifacts"]] == ["a.txt", "b.txt"] and files["title"] == "two files"
    run_line("mark --diff a.txt b.txt", ictx)
    diff = spool.list_items()[0]
    assert diff["type"] == "diff" and "-two\n+three" in diff["artifacts"][0]["content"]
    assert to_text(run_line("echo keep me | mark --tee -t log", ictx).output) == "keep me\n"  # passed on
    with pytest.raises(PipelineError, match="nothing to mark"):
        run_line("mark -t x", ictx)
    with pytest.raises(PipelineError, match="no such file"):
        run_line("mark --file missing.txt", ictx)


def test_review_list_show_approve_reject(ictx):
    assert "nothing to review" in to_text(run_line("review", ictx).output)
    req = spool.create("review", "force push main", "history rewrite", [spool.artifact("cmd", "git push -f")],
                       "high", created_by="mcp · claude-code")
    flag = spool.create("flag", "log", "", [spool.artifact("stdin", "boom")])
    listed = to_text(run_line("review", ictx).output)
    assert req["id"] in listed and flag["id"] in listed and "force push main" in listed
    shown = to_text(run_line(f"review show {req['id'][-6:]}", ictx).output)
    assert "title: force push main" in shown and "git push -f" in shown and "mcp · claude-code" in shown
    assert "approved" in to_text(run_line(f"review approve {req['id']} go ahead", ictx).output)
    assert spool.get(req["id"])["decision"] == {"status": "approved", "notes": "go ahead", "by": "user",
                                                "at": spool.get(req["id"])["decision"]["at"]}
    with pytest.raises(PipelineError, match="not a review request"):
        run_line(f"review reject {flag['id']}", ictx)
    with pytest.raises(PipelineError, match="can't become rejected"):
        run_line(f"review reject {req['id']}", ictx)
    assert req["id"] not in to_text(run_line("review", ictx).output)  # no longer pending
    assert req["id"] in to_text(run_line("review --all", ictx).output)
    with pytest.raises(PipelineError, match="usage: review"):
        run_line("review frobnicate", ictx)


def test_review_ids_complete_with_tab(ictx):
    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document

    from shellcraft.completer import ShellCompleter

    req = spool.create("review", "migrate", created_by="mcp · x")
    done = spool.create("review", "old")
    spool.decide(done["id"], "approved")
    comp = ShellCompleter(ictx)

    def words(line):
        return [c.text for c in comp.get_completions(Document(line, len(line)), CompleteEvent())]

    assert {"show", "approve", "reject"} <= set(words("review "))
    assert words("review approve ") == [req["id"]]  # only what is still pending
    assert set(words("review show ")) == {req["id"], done["id"]}


# ── the MCP side ─────────────────────────────────────────────────────────────

def _call(fn, registry):
    import anyio
    from mcp.client import Client

    from shellcraft.mcp_server import build_server

    async def main():
        async with Client(build_server(registry)) as client:
            return await fn(client)
    return anyio.run(main)


def test_mcp_tools_and_queue_resource(home, registry):
    from shellcraft.mcp_server import REVIEW_QUEUE_URI

    tools = {t.name: t for t in _call(lambda c: c.list_tools(), registry).tools}
    assert {"get_flagged_items", "request_human_review", "get_review_status", "resolve_review_item"} <= set(tools)
    assert tools["request_human_review"].input_schema["required"] == ["title", "description"]
    flag = spool.create("flag", "tests fail", "", [spool.artifact("stdin", "E boom")], type_="test-failure")
    got = json.loads(_call(lambda c: c.call_tool("get_flagged_items", {}), registry).content[0].text)
    assert [i["id"] for i in got] == [flag["id"]] and got[0]["artifacts"][0]["content"] == "E boom"
    lean = json.loads(_call(lambda c: c.call_tool("get_flagged_items", {"include_artifacts": False}), registry)
                      .content[0].text)
    assert "content" not in lean[0]["artifacts"][0]
    resources = [str(r.uri) for r in _call(lambda c: c.list_resources(), registry).resources]
    assert REVIEW_QUEUE_URI in resources
    queue = json.loads(_call(lambda c: c.read_resource(REVIEW_QUEUE_URI), registry).contents[0].text)
    assert queue[0]["id"] == flag["id"] and "content" not in json.dumps(queue)
    done = json.loads(_call(lambda c: c.call_tool("resolve_review_item",
                                                  {"item_id": flag["id"], "resolution_notes": "fixed"}),
                            registry).content[0].text)
    assert done["status"] == "resolved" and done["resolution"]["by"].startswith("mcp · ")
    again = _call(lambda c: c.call_tool("resolve_review_item", {"item_id": flag["id"], "resolution_notes": "x"}),
                  registry)
    assert again.is_error and "is resolved" in again.content[0].text


def test_request_human_review_waits_for_the_answer(home, registry):
    def answer_soon():
        for _ in range(100):
            pending = spool.list_items("pending", "review")
            if pending:
                spool.decide(pending[0]["id"], "approved", "ok, go")
                return
            time.sleep(0.05)

    threading.Thread(target=answer_soon, daemon=True).start()
    started = time.monotonic()
    result = json.loads(_call(lambda c: c.call_tool("request_human_review", {
        "title": "delete build/", "description": "clean rebuild", "priority": "high", "wait_seconds": 20,
        "artifacts": [{"name": "cmd", "content": "rm -rf build/", "kind": "text"}]}), registry).content[0].text)
    assert result["status"] == "approved" and result["decision"]["notes"] == "ok, go"
    assert time.monotonic() - started < 10  # it returned on the answer, not at the timeout
    item = spool.get(result["item_id"])
    assert item["created_by"].startswith("mcp · ") and item["artifacts"][0]["content"] == "rm -rf build/"
    quick = json.loads(_call(lambda c: c.call_tool("request_human_review", {"title": "t", "description": "d"}),
                             registry).content[0].text)
    assert quick["status"] == "pending"  # no wait: at once
    status = json.loads(_call(lambda c: c.call_tool("get_review_status", {"item_id": quick["item_id"]}),
                              registry).content[0].text)
    assert status["status"] == "pending" and status["decision"] is None
    bad = _call(lambda c: c.call_tool("request_human_review", {"title": "", "description": "d"}), registry)
    assert bad.is_error


def test_watch_reviews_announces_a_change(home, registry):
    import anyio

    from shellcraft.mcp_server import ShellcraftMCP

    app = ShellcraftMCP(registry)
    seen = []

    async def notified():
        seen.append(1)

    app.notify_reviews_changed = notified

    async def main():
        async with anyio.create_task_group() as tg:
            tg.start_soon(app.watch_reviews, 0.05)
            await anyio.sleep(0.2)
            assert seen == []  # nothing changed yet
            await anyio.to_thread.run_sync(lambda: spool.create("flag", "new"))
            with anyio.fail_after(3):
                while not seen:
                    await anyio.sleep(0.05)
            tg.cancel_scope.cancel()

    anyio.run(main)
    assert seen


# ── the shell's side: requests become alerts ────────────────────────────────

def test_the_shell_shows_a_request_as_an_alert_until_answered(home, tmp_path, monkeypatch):
    from shellcraft import shell

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(shell, "create_prompt_toolkit_console", lambda cleanup: None)
    console = Console(file=io.StringIO(), width=120, force_terminal=False, color_system=None)
    ctx = ShellContext(registry=ModuleRegistry(tmp_path), ui=UI(all_themes({})["nord"], console), config={})
    with shell.Shell(ctx) as sh:
        req = spool.create("review", "drop table users", priority="high", created_by="mcp · claude-code")
        spool.create("flag", "not an alert")  # the user's own flags are not announced
        sh._review_check()
        alerts = ctx.alerts.current()
        assert len(alerts) == 1 and alerts[0].message == "AI asks for review: drop table users"
        assert alerts[0].level == "crit" and alerts[0].timeout is None
        assert alerts[0].content == f"review show {req['id']}" and alerts[0].source == "mcp · claude-code"
        sh._review_check()
        assert len(ctx.alerts.current()) == 1  # announced once
        run_line(f"review approve {req['id']}", ctx)
        sh._review_check()
        assert ctx.alerts.current() == []  # answered: the alert goes
