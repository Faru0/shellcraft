"""ask: a question to the AI from the prompt (shellcraft.commands.ask_ai), and the code that runs the
Claude Code client for it (shellcraft.claude_run). A stand-in `claude` records what it was given and
prints the client's stream-json events, so the real command line, config file and stream reader run."""

import io
import json
import os
import stat
import sys
import threading
import time

import pytest
from rich.console import Console

from shellcraft import claude_run, events, settings
from shellcraft import jobs as jobmod
from shellcraft.commands import ask_ai
from shellcraft.context import ShellContext, to_text
from shellcraft.loader import ModuleRegistry
from shellcraft.pipeline import PipelineError, run_line
from shellcraft.themes import UI, all_themes

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="the stand-in client is a #! script")

FAKE_CLAUDE = r'''#!%(python)s
import json, os, sys, time

argv = sys.argv[1:]
config = argv[argv.index("--mcp-config") + 1]
with open(config, encoding="utf-8") as fh:
    servers = json.load(fh)
log = os.environ["FAKECLAUDE_LOG"]
calls = []
if os.path.exists(log):
    with open(log, encoding="utf-8") as fh:
        calls = json.load(fh)
calls.append({"argv": argv, "config": servers, "config_path": config, "config_mode": os.stat(config).st_mode & 0o777,
              "stdin": sys.stdin.read(), "claudecode": os.environ.get("CLAUDECODE"), "pid": os.getpid(),
              "cwd": os.getcwd()})
with open(log, "w", encoding="utf-8") as fh:
    json.dump(calls, fh)
mode = os.environ.get("FAKECLAUDE_MODE", "")
session = "sess-%%d" %% len(calls)


def say(event):
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


def delta(text):
    say({"type": "stream_event", "event": {"type": "content_block_delta",
                                            "delta": {"type": "text_delta", "text": text}}})


def block(**content):
    say({"type": "assistant", "message": {"content": [content]}})


if mode == "crash":
    sys.stderr.write("warming up\nboom: not logged in\n")
    sys.exit(1)
status = "failed" if mode == "noserver" else "connected"
say({"type": "system", "subtype": "init", "session_id": session,
     "mcp_servers": [{"name": "shellcraft", "status": status}]})
say({"type": "system", "subtype": "status"})
say("not an event")
sys.stdout.write("not json at all\n")
if mode == "hang":
    delta("thinking about it\n")
    time.sleep(60)
if mode == "budget":
    delta("I started\n")
    say({"type": "result", "subtype": "error_max_budget_usd", "is_error": True, "session_id": session,
         "total_cost_usd": 0.5, "num_turns": 3})
    sys.exit(1)
if mode == "whole":  # a client that does not stream: the text only comes as a finished block
    block(type="text", text="All of it at once.")
else:
    for piece in ("It failed because ", "/nope does not ", "exist.\nTry", " this:\n    ls /tmp"):
        delta(piece)
    block(type="text", text="It failed because /nope does not exist.\nTry this:\n    ls /tmp")
    block(type="tool_use", name="ToolSearch", input={"query": "select:mcp__shellcraft__set_clip"})
    block(type="tool_use", name="mcp__shellcraft__set_clip", input={"text": "ls /tmp"})
    say({"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}})
    delta("Alt-v pastes it.")
    block(type="text", text="Alt-v pastes it.")
say({"type": "rate_limit_event"})
say({"type": "result", "subtype": "success", "is_error": False, "result": "Alt-v pastes it.",
     "total_cost_usd": 0.0312, "session_id": session, "num_turns": 2, "duration_ms": 1200})
'''


@pytest.fixture
def client(tmp_path, monkeypatch):
    """The stand-in client as SHELLCRAFT_CLAUDE; `.calls()` is what each run of it was given."""
    path = tmp_path / "fakeclaude"
    path.write_text(FAKE_CLAUDE % {"python": sys.executable})
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    log = tmp_path / "claude-calls.json"
    monkeypatch.setenv(claude_run.CLIENT_ENV, str(path))
    monkeypatch.setenv("FAKECLAUDE_LOG", str(log))
    monkeypatch.delenv("FAKECLAUDE_MODE", raising=False)
    monkeypatch.setattr(ask_ai, "_last_session", "")

    class Client:
        mode = staticmethod(lambda name: monkeypatch.setenv("FAKECLAUDE_MODE", name))

        @staticmethod
        def calls():
            return json.loads(log.read_text()) if log.exists() else []

    return Client


def _request(**more):
    return claude_run.Request(prompt="why did that fail?", servers={"shellcraft": {"command": "x", "args": []}}, **more)


def _option(argv, name):
    return argv[argv.index(name) + 1]


def _alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _wait(pred, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


# ── claude_run ───────────────────────────────────────────────────────────────

@posix_only
def test_the_client_gets_its_options_a_private_config_and_the_prompt_on_stdin(client, tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    request = claude_run.Request(
        prompt="why did that fail?", servers={"shellcraft": claude_run.shell_server(tmp_path / "mods", True)},
        own_tools=("Read", "Agent"), blocked=("Bash", "Edit"), system="be brief", budget=0.5, model="some-model", resume="sess-0",
        cwd=str(tmp_path))
    list(claude_run.run(request))
    (call,) = client.calls()
    argv = call["argv"]
    assert argv[0] == "-p" and "--strict-mcp-config" in argv and "--include-partial-messages" in argv
    assert _option(argv, "--output-format") == "stream-json" and "--verbose" in argv
    assert _option(argv, "--allowedTools") == "mcp__shellcraft" and _option(argv, "--tools") == "Read,Agent"
    assert argv[argv.index("--disallowedTools") + 1:][:2] == ["Bash", "Edit"]
    assert _option(argv, "--max-budget-usd") == "0.5" and _option(argv, "--model") == "some-model"
    assert _option(argv, "--resume") == "sess-0" and _option(argv, "--append-system-prompt") == "be brief"
    assert call["stdin"] == "why did that fail?" and "why did that fail?" not in argv   # never in `ps`
    server = call["config"]["mcpServers"]["shellcraft"]
    assert server["command"] == sys.executable
    assert server["args"] == ["-m", "shellcraft.cli", "--mcp", "--modules", str(tmp_path / "mods"), "--allow-system"]
    assert server["env"]["PYTHONPATH"].split(os.pathsep)[0] == str(claude_run.Path(claude_run.__file__).parents[1])
    assert call["config_mode"] == 0o600 and not os.path.exists(call["config_path"])   # it held the key: gone
    assert call["claudecode"] is None and call["cwd"] == str(tmp_path)


@posix_only
def test_the_shells_own_link_goes_into_the_server_entry(monkeypatch):
    monkeypatch.setitem(jobmod.MAIN_LINK_ENV, "SHELLCRAFT_MAIN_LINK", "/run/link")
    monkeypatch.setitem(jobmod.MAIN_LINK_ENV, "SHELLCRAFT_MAIN_KEY", "aa")
    env = claude_run.shell_server()["env"]
    assert env["SHELLCRAFT_MAIN_LINK"] == "/run/link" and env["SHELLCRAFT_MAIN_KEY"] == "aa"
    assert "--modules" not in claude_run.shell_server()["args"]


@posix_only
def test_the_answer_comes_piece_by_piece_with_tools_and_a_result(client):
    seen = list(claude_run.run(_request()))
    assert [v for k, v in seen if k == "text"] == ["It failed because ", "/nope does not ", "exist.\nTry",
                                                   " this:\n    ls /tmp", "Alt-v pastes it."]
    assert [k for k, _ in seen] == ["text"] * 4 + ["break", "tool", "tool", "text", "break", "result"]
    assert seen[5][1][0] == "ToolSearch" and seen[6][1] == ("mcp__shellcraft__set_clip", {"text": "ls /tmp"})
    result = seen[-1][1]
    assert result.text == "Alt-v pastes it." and result.cost == pytest.approx(0.0312)
    assert result.session == "sess-1" and result.turns == 2 and result.error == "" and result.seconds > 0


@posix_only
def test_a_client_that_does_not_stream_still_gives_its_text(client):
    client.mode("whole")
    seen = list(claude_run.run(_request()))
    assert [k for k, _ in seen] == ["text", "break", "result"] and seen[0][1] == "All of it at once."


@posix_only
def test_stopping_ends_the_client(client):
    client.mode("hang")
    stop = threading.Event()
    began = time.monotonic()
    seen = []
    for kind, value in claude_run.run(_request(), stop):
        seen.append((kind, value))
        if kind == "text":
            stop.set()
    assert seen[-1][0] == "result" and seen[-1][1].error == "stopped"
    assert time.monotonic() - began < 20
    assert _wait(lambda: not _alive(client.calls()[0]["pid"]))


@posix_only
def test_closing_the_run_early_ends_the_client_too(client):
    client.mode("hang")
    run = claude_run.run(_request())
    assert next(run) == ("text", "thinking about it\n")
    run.close()
    assert _wait(lambda: not _alive(client.calls()[0]["pid"]))


@posix_only
def test_a_spent_budget_is_said_in_one_line(client):
    client.mode("budget")
    result = list(claude_run.run(_request(budget=0.5)))[-1][1]
    assert result.error == "stopped: the budget of $0.5 for one run was reached" and result.cost == 0.5


@posix_only
def test_a_client_that_dies_reports_its_last_error_line(client):
    client.mode("crash")
    assert list(claude_run.run(_request())) [-1][1].error == "boom: not logged in"


@posix_only
def test_an_mcp_server_that_did_not_start_is_a_note(client):
    client.mode("noserver")
    notes = [v for k, v in claude_run.run(_request()) if k == "note"]
    assert notes == ["the shellcraft tools did not start (failed)"]


def test_a_missing_client_is_one_clear_line(monkeypatch, tmp_path):
    monkeypatch.setenv(claude_run.CLIENT_ENV, str(tmp_path / "nope"))
    with pytest.raises(claude_run.ClaudeError, match="SHELLCRAFT_CLAUDE names .*nope, which is not a program"):
        claude_run.find_client()
    monkeypatch.delenv(claude_run.CLIENT_ENV)
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(claude_run.ClaudeError, match=r"`claude`\) is not on PATH; install it .* or set SHELLCRAFT_CLAUDE"):
        list(claude_run.run(_request()))


# ── how an `ask` line is read ────────────────────────────────────────────────

@pytest.mark.parametrize("line, args", [
    ("ask why didn't that work?", ["--", "why didn't that work?"]),
    ("ask is # a comment here? and `this`, $HOME, a | b > c", ["--", "is # a comment here? and `this`, $HOME, a | b > c"]),
    ("  ask   fix it!  ", ["--", "fix it!"]),
    ("ask -b summarise today's auth.log", ["-b", "--", "summarise today's auth.log"]),
    ("ask -bc --budget 1 --model=x fix it", ["-bc", "--budget", "1", "--model=x", "--", "fix it"]),
    ("ask --continue --dry-run and for IPv6?", ["--continue", "--dry-run", "--", "and for IPv6?"]),
    ("ask -- -x is what?", ["--", "-x is what?"]),
    ("ask what does this do:", ["--", "what does this do:"]),
])
def test_an_unquoted_question_is_taken_as_typed(line, args):
    assert ask_ai.free_text(line) == args


@pytest.mark.parametrize("line", [
    'ask "why did that fail" | tee answer.txt', "ask 'quoted' > f", "ask", "ask -c", "ask --help", "ask -x why",
    "ask --budget", "echo ask why", "asking for a friend", "ask=5", "ask () { echo hi; }", "ask | cat",
])
def test_other_lines_are_ordinary_shell_syntax(line):
    assert ask_ai.free_text(line) is None


def test_your_own_ask_function_or_alias_wins(ctx):
    assert ask_ai.is_free_text("ask why didn't it work", ctx)
    ctx.config["aliases"] = {"ask": "echo"}
    assert not ask_ai.is_free_text("ask why didn't it work", ctx)
    ctx.config.pop("aliases")
    run_line("ask() { echo mine: $1; }", ctx)
    assert not ask_ai.is_free_text("ask why didn't it work", ctx)
    assert to_text(run_line("ask hello", ctx).output).strip() == "mine: hello"


# ── the command ──────────────────────────────────────────────────────────────

@pytest.fixture
def linked(ctx, client, monkeypatch):
    """A context that looks like the interactive shell's: it has a link for the AI to come in by."""
    monkeypatch.setitem(jobmod.MAIN_LINK_ENV, "SHELLCRAFT_MAIN_LINK", "/run/link")
    monkeypatch.setitem(jobmod.MAIN_LINK_ENV, "SHELLCRAFT_MAIN_KEY", "aa")
    events.clear()
    return ctx


@posix_only
def test_the_question_goes_out_as_typed_and_the_answer_comes_back(linked, client):
    result = run_line("ask why didn't that work? # really | no pipe", linked)
    assert to_text(result.output) == ("It failed because /nope does not exist.\nTry this:\n    ls /tmp\n"
                                      "Alt-v pastes it.\n")   # the notes are for the screen only
    assert result.status == 0 and linked.last_status == 0
    (call,) = client.calls()
    assert call["stdin"].startswith("why didn't that work? # really | no pipe\n")
    assert f"Working folder: {os.getcwd()}" in call["stdin"]
    assert "No command has been run in this shell yet." in call["stdin"]
    argv = call["argv"]
    assert _option(argv, "--max-budget-usd") == "0.5" and "--model" not in argv and "--resume" not in argv
    assert _option(argv, "--tools") == "ToolSearch" and "--disallowedTools" not in argv   # only the shell's tools
    assert _option(argv, "--append-system-prompt") == ask_ai.SYSTEM
    server = call["config"]["mcpServers"]["shellcraft"]
    assert server["env"]["SHELLCRAFT_MAIN_LINK"] == "/run/link"
    assert _option(server["args"], "--modules") == str(linked.registry.directory)


@posix_only
def test_the_screen_gets_the_tool_notes_and_the_cost(linked, client):
    shown = []
    linked.live = shown.append
    items = list(ask_ai.answer(ask_ai._request(linked, ask_ai._parser().parse_args([]), "why?", ""), None))
    lines = [i if isinstance(i, str) else i.renderable.plain for i in items]
    assert lines == ["It failed because /nope does not exist.\n", "Try this:\n", "    ls /tmp\n",
                     "✦ set_clip: ls /tmp", "Alt-v pastes it.\n", lines[-1]]
    assert lines[-1].startswith("✦ ") and "$0.03" in lines[-1] and "ask -c carries on" in lines[-1]
    assert [i.text for i in items if not isinstance(i, str)] == ["", ""]   # nothing of them in a pipe or a file


@posix_only
def test_a_quoted_question_pipes_and_redirects(linked, client, tmp_path):
    result = run_line('ask "why did that fail" | grep -c .', linked)
    assert to_text(result.output).strip() == "4"
    run_line("ask 'and now' > answer.txt", linked)
    assert (tmp_path / "answer.txt").read_text().endswith("Alt-v pastes it.\n")
    assert "✦" not in (tmp_path / "answer.txt").read_text()
    assert [c["stdin"].splitlines()[0] for c in client.calls()] == ["why did that fail", "and now"]


@posix_only
def test_piped_input_is_added_redacted(linked, client):
    run_line("echo 'login --password hunter2secret failed' | ask what is this", linked)
    sent = client.calls()[0]["stdin"]
    assert sent.startswith("what is this\n") and "Piped into the question:" in sent
    assert "hunter2secret" not in sent and "REDACTED" in sent


@posix_only
def test_continue_carries_on_from_the_last_question(linked, client):
    with pytest.raises(PipelineError, match="ask -c: there is no earlier question"):
        run_line("ask -c and for IPv6?", linked)
    run_line("ask why did that fail", linked)
    run_line("ask -c and for IPv6?", linked)
    first, second = client.calls()
    assert "--resume" not in first["argv"] and _option(second["argv"], "--resume") == "sess-1"
    assert second["stdin"].startswith("and for IPv6?\n")


@posix_only
def test_budget_and_model_from_the_options_and_the_settings(linked, client):
    run_line("settings ask_budget 2", linked)
    run_line("settings ask_model my-model", linked)
    run_line("ask one", linked)
    run_line("ask --budget 0.05 --model other two", linked)
    one, two = (c["argv"] for c in client.calls())
    assert _option(one, "--max-budget-usd") == "2" and _option(one, "--model") == "my-model"
    assert _option(two, "--max-budget-usd") == "0.05" and _option(two, "--model") == "other"
    with pytest.raises(PipelineError, match="ask: --budget: "):
        run_line("ask --budget lots three", linked)
    assert len(client.calls()) == 2


@posix_only
def test_dry_run_shows_the_command_and_runs_nothing(linked, client):
    out = to_text(run_line("ask -n why did that fail", linked).output)
    assert out.splitlines()[0].startswith(os.environ[claude_run.CLIENT_ENV] + " -p --mcp-config MCP-CONFIG.json")
    assert "--append-system-prompt SYSTEM-TEXT" in out and "--max-budget-usd 0.5" in out
    assert "\nwhy did that fail\n" in out and "Working folder:" in out
    assert client.calls() == []


@posix_only
def test_a_client_that_stops_short_is_the_commands_error(linked, client):
    client.mode("budget")
    with pytest.raises(PipelineError, match=r"ask: stopped: the budget of \$0.5 for one run was reached"):
        run_line("ask why", linked)
    assert linked.last_status == 1
    client.mode("crash")
    with pytest.raises(PipelineError, match="ask: boom: not logged in"):
        run_line("ask why", linked)


def test_no_client_no_question_no_shell(ctx, monkeypatch, tmp_path):
    with pytest.raises(PipelineError, match="ask: ask what"):
        run_line("ask", ctx)
    monkeypatch.setenv(claude_run.CLIENT_ENV, sys.executable)
    with pytest.raises(PipelineError, match="ask: needs a running interactive shell"):
        run_line("ask why", ctx)
    monkeypatch.setitem(jobmod.MAIN_LINK_ENV, "SHELLCRAFT_MAIN_LINK", "/run/link")
    monkeypatch.setenv(claude_run.CLIENT_ENV, str(tmp_path / "nope"))
    with pytest.raises(PipelineError, match="ask: SHELLCRAFT_CLAUDE names"):
        run_line("ask why", ctx)


@posix_only
def test_an_ai_client_cannot_ask(linked, client):
    linked.allow_sensitive = False   # what a pipeline run over MCP gets
    with pytest.raises(PipelineError):
        run_line("ask why did that fail", linked)
    assert client.calls() == []


def test_the_spinner_says_what_it_waits_for(ctx):
    from shellcraft import pipeline
    from shellcraft.parser import Command

    assert pipeline._label([(1, Command("ask", ["why"]))], ctx) == "asking the AI…"
    assert pipeline._label([(1, Command("sort", []))], ctx) == "running sort…"


def test_ai_access_off_tells_the_ai_to_stage_not_run(ctx):
    events.clear()
    ctx.ai_run = True
    assert "AI access is on: you may run commands with run_in_shell." in ask_ai.build_prompt(ctx, "fix it")
    ctx.ai_run = False
    off = ask_ai.build_prompt(ctx, "fix it")
    assert "AI access is off" in off and "set_clip" in off and "run_in_shell" not in off


def test_tool_notes_are_short():
    assert ask_ai._tool_note("mcp__shellcraft__run_in_shell", {"command": "ls   /nope"}) == "✦ run_in_shell: ls /nope"
    assert ask_ai._tool_note("mcp__shellcraft__get_jobs", {}) == "✦ get_jobs"
    assert len(ask_ai._tool_note("Agent", {"command": "x" * 500})) < 120


# ── in a real shell: the last command, the background, the prompt ────────────

@pytest.fixture
def real_shell(tmp_path, monkeypatch, client):
    from shellcraft import mainlink, shell

    home = tmp_path / ".home"
    monkeypatch.setenv("SHELLCRAFT_HOME", str(home))
    monkeypatch.delenv(mainlink.ADDRESS_ENV, raising=False)
    monkeypatch.delenv(mainlink.KEY_ENV, raising=False)
    monkeypatch.delenv("SHELLCRAFT_DEPTH", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(shell, "create_prompt_toolkit_console", lambda cleanup: None)
    console = Console(file=io.StringIO(), width=120, force_terminal=False, color_system=None, record=True)
    ctx = ShellContext(registry=ModuleRegistry(home / "modules"), ui=UI(all_themes({})["nord"], console), config={})
    events.clear()
    with shell.Shell(ctx) as sh:
        yield sh, ctx


def typed(sh, line):
    """`line` as if typed at the prompt: on the screen, run, and recorded as a command event."""
    sh.scrollback.command(line)
    first = sh.scrollback.mark()
    began = time.monotonic()
    try:
        sh._execute(line)
    finally:
        sh._command_done(line, first, time.monotonic() - began)


@posix_only
def test_the_prompt_holds_the_last_commands_and_the_redacted_output(real_shell, client):
    sh, ctx = real_shell
    typed(sh, "echo first")
    typed(sh, "echo login --password hunter2secret; ls /nope-such-folder")
    typed(sh, "ask why did that fail")
    sent = client.calls()[0]["stdin"]
    assert sent.startswith("why did that fail\n")
    assert "  [exit 0] echo first\n" in sent
    listed = [line for line in sent.splitlines() if line.startswith("  [exit ")]
    assert len(listed) == 2 and "ls /nope-such-folder" in listed[1] and "[exit 0]" not in listed[1]
    body = sent.split("<<<\n", 1)[1].split("\n>>>", 1)[0]
    assert "nope-such-folder" in body and "hunter2secret" not in body and "REDACTED" in body
    assert "echo first" not in body                      # only what the newest command printed
    screen = sh.scrollback.text()
    assert "It failed because /nope does not exist." in screen and "✦ set_clip: ls /tmp" in screen
    assert "ask -c carries on" in screen
    typed(sh, "ask -c and now?")                         # an `ask` line is not "the last command"
    again = client.calls()[1]["stdin"]
    assert [line for line in again.splitlines() if line.startswith("  [exit ")] == listed


@posix_only
def test_background_is_an_ai_job_with_the_answer_as_its_output(real_shell, client):
    sh, ctx = real_shell
    typed(sh, "ask -b summarise today's auth.log")
    (job,) = list(ctx.jobs)
    assert job.origin == "ai" and job.command == "ask summarise today's auth.log"
    assert _wait(lambda: not job.running)
    text = job.output
    assert "It failed because /nope does not exist." in text and "✦ set_clip: ls /tmp" in text and "$0.03" in text
    assert client.calls()[0]["stdin"].startswith("summarise today's auth.log\n")
    assert "asking in the background" in sh.scrollback.text()


@posix_only
def test_killing_the_background_job_ends_the_client(real_shell, client):
    sh, ctx = real_shell
    client.mode("hang")
    typed(sh, "ask -b take your time")
    (job,) = list(ctx.jobs)
    assert _wait(lambda: client.calls() and _alive(client.calls()[0]["pid"]))
    typed(sh, f"jobs -k {job.number}")
    assert _wait(lambda: not _alive(client.calls()[0]["pid"]))
    assert _wait(lambda: not job.running)


@posix_only
def test_the_prompt_takes_a_question_as_one_line(real_shell):
    sh, ctx = real_shell
    sh._ask = lambda prompt: pytest.fail("a question never asks for more lines")
    for line in ("ask what's wrong", "ask what does this do:", "ask is it { open"):
        assert sh._read_continuation(line) == line


def test_the_settings_are_listed_checked_and_reset(ctx):
    listing = to_text(run_line("settings", ctx).output)
    assert "ask_budget" in listing and "ask_model" in listing
    assert settings.text(ctx.config, "ask_budget") == "0.50" and settings.text(ctx.config, "ask_model") == ""
    run_line("settings ask_budget 1.5", ctx)
    assert settings.text(ctx.config, "ask_budget") == "1.5" and ctx.config["ask_budget"] == "1.5"
    assert "1.5" in to_text(run_line("settings ask_budget", ctx).output)
    for bad in ("0", "-1", "lots", "500"):
        with pytest.raises(PipelineError):
            run_line(f"settings ask_budget {bad}", ctx)
    run_line("settings reset ask_budget", ctx)
    assert "ask_budget" not in ctx.config and settings.text(ctx.config, "ask_budget") == "0.50"
    run_line("settings ask_model some-model", ctx)
    assert settings.text(ctx.config, "ask_model") == "some-model"
    assert "not set" in to_text(run_line("settings reset ask_model", ctx).output)
