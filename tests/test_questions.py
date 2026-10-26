"""Asking the user: ask_user, review answer, threads, tell -r."""

import io
import json
import threading
import time

import anyio
import pytest
from mcp.client import Client
from rich.console import Console

from shellcraft import ai_audit, ai_policy, mainlink
from shellcraft import review_spool as spool
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


def test_a_question_is_answered_with_a_choice_number_or_words(home):
    q = spool.create("question", "Block 203.0.113.9?", "50 failed logins", [], "high", "question",
                     choices=["block it", "watch it", "ignore"])
    assert q["status"] == "pending" and q["choices"] == ["block it", "watch it", "ignore"] and q["thread"] == []
    done = spool.answer(q["id"], "2")
    assert done["status"] == "answered" and done["answer"]["text"] == "watch it" and done["answer"]["choice"] == 2
    assert done["thread"][-1]["kind"] == "answer"
    with pytest.raises(spool.SpoolError, match="can't become answered"):
        spool.answer(q["id"], "again")
    other = spool.create("question", "Why?", "", [], "low", "question")
    assert spool.answer(other["id"], "7")["answer"] == {**spool.get(other["id"])["answer"], "text": "7", "choice": None}
    with pytest.raises(spool.SpoolError, match="some words"):
        spool.answer(spool.create("question", "q", "", [], "low", "question")["id"], "  ")


def test_only_questions_are_answered_and_questions_are_not_approved(home):
    flag = spool.create("flag", "f", "", [], "low", "")
    review = spool.create("review", "r", "", [], "low", "")
    question = spool.create("question", "q", "", [], "low", "question")
    with pytest.raises(spool.SpoolError, match="not a question"):
        spool.answer(flag["id"], "x")
    with pytest.raises(spool.SpoolError, match="not a question"):
        spool.answer(review["id"], "x")
    with pytest.raises(spool.SpoolError, match="review answer"):
        spool.decide(question["id"], "approved")


def test_the_ai_resolves_an_answered_question(home):
    q = spool.create("question", "q", "", [], "low", "question")
    spool.answer(q["id"], "yes")
    assert spool.resolve(q["id"], "went ahead")["status"] == "resolved"


def test_replies_go_in_the_thread_of_any_item(home):
    flag = spool.create("flag", "look at this", "", [], "low", "")
    spool.reply(flag["id"], "also the nginx log", by="user")
    spool.reply(flag["id"], "and last week's", by="user")
    thread = spool.get(flag["id"])["thread"]
    assert [m["text"] for m in thread] == ["also the nginx log", "and last week's"]
    with pytest.raises(spool.SpoolError, match="some words"):
        spool.reply(flag["id"], " ")
    with pytest.raises(spool.SpoolError, match="no review item"):
        spool.reply("r-nope", "x")


def test_ask_user_waits_for_the_answer(registry, real_shell, home):
    sh, ctx = real_shell
    server = build_server(registry)

    def answer_soon():
        for _ in range(200):
            pending = spool.list_items("pending", "question")
            if pending:
                sh._review_check()
                alert = [a for a in ctx.alerts.current() if a.message.startswith("AI asks: ")][0]
                assert "[1] block it [2] watch it" in alert.message and alert.level == "crit"
                run_line(f"review answer {pending[0]['id'][-6:]} 1", ctx)
                return
            time.sleep(0.05)

    threading.Thread(target=answer_soon, daemon=True).start()
    out = _json(call(server, "ask_user", question="Block 203.0.113.9?", details="50 failed logins",
                     choices=["block it", "watch it"], priority="high", wait_seconds=10, agent="worker-1"))
    assert out["answered"] is True and out["answer"] == "block it" and out["status"] == "answered"
    assert out["choices"] == ["block it", "watch it"] and out["question"] == "Block 203.0.113.9?"
    sh._review_check()
    assert not [a for a in ctx.alerts.current() if a.message.startswith("AI asks: ")]  # gone once answered
    assert ai_audit.tail(5, agent="worker-1")[-1]["tool"] == "ask_user"


def test_ask_user_without_waiting_can_be_polled(registry, home):
    server = build_server(registry)
    out = _json(call(server, "ask_user", question="Which branch?", wait_seconds=0))
    assert out["answered"] is False and out["status"] == "pending" and out["answer"] is None
    spool.answer(out["item_id"], "main")
    again = _json(call(server, "get_review_status", item_id=out["item_id"]))
    assert again["status"] == "answered" and again["answer"]["text"] == "main"


def test_a_question_about_an_item_lands_in_its_thread_and_the_reply_comes_back(registry, home):
    server = build_server(registry)
    flag = spool.create("flag", "auth.log", "", [], "medium", "")
    out = _json(call(server, "ask_user", question="Is 10.0.0.5 yours?", about=flag["id"][-6:], wait_seconds=0))
    assert spool.get(flag["id"])["thread"][0]["text"] == "Is 10.0.0.5 yours?"
    assert spool.get(out["item_id"])["parent"] == flag["id"]
    spool.reply(flag["id"], "yes, it's the scanner", by="user")
    flagged = _json(call(server, "get_flagged_items", kind="flag"))
    assert flagged[0]["thread"][-1]["text"] == "yes, it's the scanner"
    assert call(server, "ask_user", question="x", about="r-nosuch", wait_seconds=0).is_error
    assert call(server, "ask_user", question=" ").is_error


def test_questions_are_listed_for_the_ai(registry, home):
    q = spool.create("question", "q?", "", [], "low", "question")
    server = build_server(registry)
    listed = _json(call(server, "get_flagged_items", kind="question"))
    assert [i["id"] for i in listed] == [q["id"]]
    assert _json(call(server, "get_flagged_items", kind="question", status="answered")) == []
    spool.answer(q["id"], "yes")
    assert len(_json(call(server, "get_flagged_items", kind="question", status="answered"))) == 1


def test_the_review_command_answers_and_shows_the_thread(real_shell, home):
    sh, ctx = real_shell
    q = spool.create("question", "Block it?", "50 failed logins", [], "high", "question", choices=["yes", "no"])
    listing = to_text(run_line("review", ctx).output)
    assert "question" in listing and "Block it?" in listing
    shown = to_text(run_line(f"review show {q['id'][-6:]}", ctx).output)
    assert "1. yes" in shown and "2. no" in shown and "50 failed logins" in shown
    run_line(f"tell -r {q['id'][-6:]} careful, it is the CEOs laptop", ctx)
    out = to_text(run_line(f"review answer {q['id'][-6:]} 2 not today", ctx).output)
    assert "answered: no today" not in out and "answered: " in out
    shown = to_text(run_line(f"review show {q['id'][-6:]}", ctx).output)
    assert "answer: " in shown and "── thread" in shown and "careful, it is the CEOs laptop" in shown
    with pytest.raises(Exception, match="not a question"):
        run_line(f"review answer {spool.create('flag', 'f', '', [], 'low', '')['id'][-6:]} x", ctx)
    with pytest.raises(Exception, match="review answer"):
        run_line(f"review approve {spool.create('question', 'q', '', [], 'low', 'question')['id'][-6:]}", ctx)
    assert ai_audit.tail(3)[-1]["decision"] == "answered"


def test_tell_reply_needs_an_existing_item(real_shell):
    sh, ctx = real_shell
    with pytest.raises(Exception, match="no review item"):
        run_line("tell -r r-nosuch hello", ctx)
