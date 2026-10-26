"""The task board: exclusive claims, leases, dependencies, the tools, the prompt and the alerts."""

import io
import json
import threading
import time

import anyio
import pytest
from mcp.client import Client
from rich.console import Console

from shellcraft import ai_policy, mainlink, tasks
from shellcraft.context import ShellContext, to_text
from shellcraft.loader import ModuleRegistry
from shellcraft.mcp_server import build_server
from shellcraft.pipeline import run_line
from shellcraft.themes import UI, all_themes


def _call(server, fn):
    async def main():
        async with Client(server) as client:
            return await fn(client)
    return anyio.run(main)


def _json(result):
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


def call(server, name, **arguments):
    return _call(server, lambda c: c.call_tool(name, arguments))


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / ".home"))
    monkeypatch.delenv(mainlink.ADDRESS_ENV, raising=False)
    monkeypatch.delenv(mainlink.KEY_ENV, raising=False)
    (tmp_path / ".home").mkdir()
    ai_policy._cache.clear()
    return tmp_path / ".home"


@pytest.fixture
def real_shell(home, monkeypatch):
    from shellcraft import shell

    monkeypatch.setattr(shell, "create_prompt_toolkit_console", lambda cleanup: None)
    console = Console(file=io.StringIO(), width=120, force_terminal=False, color_system=None, record=True)
    ctx = ShellContext(registry=ModuleRegistry(home / "modules"), ui=UI(all_themes({})["nord"], console), config={})
    with shell.Shell(ctx) as sh:
        yield sh, ctx


# ── the board ─────────────────────────────────────────────────────────────────

def test_a_task_is_claimed_by_one_agent_only(home):
    t = tasks.create("scan the logs", priority="high")
    first = tasks.claim(t["id"], "w1")
    assert first["status"] == "claimed" and first["owner"] == "w1"
    with pytest.raises(tasks.TaskError, match="held by w1"):
        tasks.claim(t["id"], "w2")
    assert tasks.claim(t["id"], "w1")["owner"] == "w1"  # its owner renews it


def test_two_workers_racing_for_one_task_only_one_wins(home):
    t = tasks.create("only one of us")
    winners, losers = [], []

    def worker(name):
        try:
            tasks.claim(t["id"], name)
            winners.append(name)
        except tasks.TaskError:
            losers.append(name)

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(8)]
    [x.start() for x in threads]
    [x.join() for x in threads]
    assert len(winners) == 1 and len(losers) == 7


def test_claiming_without_an_id_takes_the_most_urgent_free_task(home):
    low = tasks.create("low", priority="low")
    high = tasks.create("high", priority="high")
    assert tasks.claim(None, "w1")["id"] == high["id"]
    assert tasks.claim(None, "w2")["id"] == low["id"]
    with pytest.raises(tasks.TaskError, match="no task is free"):
        tasks.claim(None, "w3")


def test_a_task_waits_for_what_it_depends_on(home):
    a = tasks.create("first")
    b = tasks.create("second", depends_on=[a["id"]])
    with pytest.raises(tasks.TaskError, match="waits for"):
        tasks.claim(b["id"], "w1")
    assert [t["id"] for t in tasks.list_tasks(claimable=True)] == [a["id"]]
    tasks.claim(a["id"], "w1")
    tasks.update(a["id"], "w1", status="done", result="ok")
    assert tasks.claim(b["id"], "w2")["owner"] == "w2"


def test_only_the_owner_updates_and_every_update_renews_the_lease(home):
    t = tasks.create("work")
    tasks.claim(t["id"], "w1", lease=10)
    with pytest.raises(tasks.TaskError, match="held by w1"):
        tasks.update(t["id"], "w2", note="mine now")
    before = tasks.get(t["id"])["lease_until"]
    time.sleep(0.05)
    updated = tasks.update(t["id"], "w1", status="working", note="halfway", lease=600)
    assert updated["lease_until"] > before + 500 and updated["status"] == "working"
    assert [n["text"] for n in updated["notes"]] == ["claimed", "halfway"]
    done = tasks.update(t["id"], "w1", status="done", result="found it")
    assert done["status"] == "done" and done["lease_until"] is None and done["result"] == "found it"
    with pytest.raises(tasks.TaskError, match="already done"):
        tasks.update(t["id"], "w1", note="again")


def test_an_expired_lease_lets_someone_else_take_the_task(home):
    t = tasks.create("abandoned")
    tasks.claim(t["id"], "w1")
    path = tasks._path(t["id"], None)
    data = json.loads(path.read_text())
    data["lease_until"] = time.time() - 5
    path.write_text(json.dumps(data))
    assert tasks.get(t["id"])["stale"] is True
    assert tasks.counts() == {"stale": 1}
    assert tasks.claim(t["id"], "w2")["owner"] == "w2"


def test_release_and_the_users_override(home):
    t = tasks.create("x")
    tasks.claim(t["id"], "w1")
    with pytest.raises(tasks.TaskError, match="not yours"):
        tasks.release(t["id"], "w2")
    assert tasks.release(t["id"], "w1")["status"] == "open"
    tasks.claim(t["id"], "w2")
    assert tasks.finish(t["id"], "cancelled")["status"] == "cancelled"
    with pytest.raises(tasks.TaskError, match="already cancelled"):
        tasks.finish(t["id"], "done")


def test_finished_tasks_are_deleted_after_a_week(home):
    t = tasks.create("old")
    tasks.finish(t["id"], "done")
    path = tasks._path(t["id"], None)
    data = json.loads(path.read_text())
    data["updated"] = "2020-01-01T00:00:00Z"
    path.write_text(json.dumps(data))
    assert tasks.list_tasks("all") == [] and not path.exists()


# ── through MCP ───────────────────────────────────────────────────────────────

def test_the_tools_run_a_whole_job(registry, home):
    server = build_server(registry)
    names = {t.name for t in _call(server, lambda c: c.list_tools()).tools}
    assert set(tasks_tools()) <= names
    a = _json(call(server, "create_task", title="triage auth.log", priority="high", agent="orchestrator"))
    b = _json(call(server, "create_task", title="write it up", depends_on=[a["id"]]))
    assert a["created_by"].endswith("orchestrator")
    listed = _json(call(server, "list_tasks", claimable=True))
    assert [t["id"] for t in listed] == [a["id"]]
    got = _json(call(server, "claim_task", agent="worker-1"))
    assert got["id"] == a["id"] and got["owner"] == "worker-1"
    rival = call(server, "claim_task", task_id=a["id"], agent="worker-2")
    assert rival.is_error and "held by worker-1" in rival.content[0].text
    _json(call(server, "update_task", task_id=a["id"], status="working", note="reading", agent="worker-1"))
    done = _json(call(server, "update_task", task_id=a["id"], status="done", result="12 bad logins", agent="worker-1"))
    assert done["status"] == "done"
    nxt = _json(call(server, "claim_task", agent="worker-2"))
    assert nxt["id"] == b["id"]
    full = _json(call(server, "list_tasks", task_id=a["id"]))
    assert full["result"] == "12 bad logins" and len(full["notes"]) == 2
    bad = call(server, "update_task", task_id=a["id"], status="open", agent="worker-1")
    assert bad.is_error


def tasks_tools():
    from shellcraft.mcp_server import TASK_TOOLS

    return TASK_TOOLS


def test_the_monitor_role_may_list_tasks_but_not_change_them(registry, home):
    server = build_server(registry, role="monitor")
    names = {t.name for t in _call(server, lambda c: c.list_tools()).tools}
    assert "list_tasks" in names and not names & {"create_task", "claim_task", "update_task", "release_task"}


def test_the_board_is_a_resource_that_is_announced(registry, home):
    tasks.create("visible")
    server = build_server(registry)

    async def main():
        async with Client(server) as client:
            listed = await client.list_resources()
            assert "shellcraft://tasks" in [str(r.uri) for r in listed.resources]
            read = await client.read_resource("shellcraft://tasks")
            return json.loads(read.contents[0].text)

    assert [t["title"] for t in anyio.run(main)] == ["visible"]


# ── the shell ─────────────────────────────────────────────────────────────────

def test_the_tasks_command(real_shell, home):
    sh, ctx = real_shell
    assert "no tasks" in to_text(run_line("tasks", ctx).output)
    out = to_text(run_line("tasks add check the certs -p high -d 'look at crt.sh'", ctx).output)
    assert "added: check the certs" in out
    first = tasks.list_tasks()[0]
    run_line("tasks add and then sign off --after " + first["id"][-6:], ctx)
    listing = to_text(run_line("tasks", ctx).output)
    assert "check the certs" in listing and "waits for" in listing
    tasks.claim(first["id"], "w1")
    shown = to_text(run_line(f"tasks show {first['id'][-6:]}", ctx).output)
    assert "owner w1" in shown and "look at crt.sh" in shown
    run_line(f"tasks done {first['id'][-6:]} fine", ctx)
    assert "check the certs" not in to_text(run_line("tasks", ctx).output)
    assert "check the certs" in to_text(run_line("tasks --all", ctx).output)
    assert "removed 1" in to_text(run_line("tasks clear", ctx).output)
    with pytest.raises(Exception, match="no task"):
        run_line("tasks show zzzzzz", ctx)


def test_the_prompt_counts_the_work_and_blocked_tasks_alert(real_shell, home):
    sh, ctx = real_shell
    sh._review_check()
    assert not any("task" in t for _s, t, *_ in sh._segment("mcp"))
    a = tasks.create("one")
    tasks.create("two")
    tasks.claim(a["id"], "w1")
    sh._review_check()
    assert any("✦ 2 tasks: 1 working" in t for _s, t, *_ in sh._segment("mcp"))
    tasks.update(a["id"], "w1", status="blocked", note="need the VPN password")
    sh._review_check()
    alert = [x for x in ctx.alerts.current() if "blocked" in x.message][0]
    assert "need the VPN password" in alert.message and alert.level == "warn"
    sh._review_check()
    assert len([x for x in ctx.alerts.current() if "blocked" in x.message]) == 1  # announced once
