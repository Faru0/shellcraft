"""The AI policy: access on by default, rules, roles, the audit log, the agent label."""

import io
import json

import anyio
import pytest
from mcp.client import Client
from rich.console import Console

from shellcraft import ai_audit, ai_policy, ai_run, mainlink, settings
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


def write_policy(home, text):
    (home / ai_policy.FILE).write_text(text)
    ai_policy._cache.clear()


# ── the setting ───────────────────────────────────────────────────────────────

def test_ai_access_is_on_by_default_and_saved():
    assert settings.get({}, "ai_access") is True
    config = {}
    settings.set_value(config, "ai_access", False)
    assert settings.get(config, "ai_access") is False


def test_the_old_name_still_works_and_is_saved(registry, home):
    ctx = ShellContext(registry=registry, config={})
    out = to_text(run_line("settings ai_run off", ctx).output)
    assert "Off" in out and ctx.ai_run is False and ctx.config["settings"]["ai_access"] is False
    run_line("settings ai_access on", ctx)
    assert ctx.ai_run is True


# ── the policy file ───────────────────────────────────────────────────────────

def test_no_file_means_no_rules(home):
    policy = ai_policy.load()
    assert policy.rules == [] and not policy.exists and set(policy.roles) >= {"monitor", "worker", "orchestrator"}


def test_a_file_is_read_and_a_bad_one_only_warns(home):
    write_policy(home, '''
[[rule]]
action = "deny"
match = "rm -r*"
[[rule]]
action = "maybe"
match = "x"
[role.scout]
run = "read-only"
tools = ["get_jobs"]
[budget]
module_calls = 3
[cache]
seconds = 12
''')
    policy = ai_policy.load()
    assert [r.match for r in policy.rules] == ["rm -r*"]
    assert any("action must be" in w for w in policy.warnings)
    assert policy.roles["scout"].tools == frozenset({"get_jobs"})
    assert policy.budget == 3 and policy.cache_seconds == 12
    write_policy(home, "not toml [[[")
    assert ai_policy.load().warnings


def test_deny_beats_ask_beats_allow_and_matches_any_command_of_a_line(home):
    policy = ai_policy.parse('''
[[rule]]
action = "allow"
match = "git *"
[[rule]]
action = "ask"
match = "git push*"
[[rule]]
action = "deny"
match = "rm -r*"
''')
    assert ai_policy.evaluate(policy, "git status")[0] == "allow"
    assert ai_policy.evaluate(policy, "git push origin main")[0] == "ask"
    assert ai_policy.evaluate(policy, "git status; rm -rf /tmp/x")[0] == "deny"
    assert ai_policy.evaluate(policy, "ls")[0] is None


def test_allow_needs_every_command_and_no_redirection(home):
    policy = ai_policy.parse('[[rule]]\naction = "allow"\nmatch = "git status*"\n')
    assert ai_policy.evaluate(policy, "git status -s")[0] == "allow"
    assert ai_policy.evaluate(policy, "git status && curl x")[0] is None
    assert ai_policy.evaluate(policy, "git status > ~/.bashrc")[0] is None
    assert ai_policy.evaluate(policy, "git status $(whoami)")[0] is None


def test_a_rule_can_be_for_a_role_a_folder_and_until_a_date(home):
    policy = ai_policy.parse('''
[[rule]]
action = "deny"
match = "make*"
role = "worker"
cwd = "/work/*"
[[rule]]
action = "deny"
match = "old*"
until = "2001-01-01 00:00"
''')
    assert ai_policy.evaluate(policy, "make", "worker", "/work/a")[0] == "deny"
    assert ai_policy.evaluate(policy, "make", "monitor", "/work/a")[0] is None
    assert ai_policy.evaluate(policy, "make", "worker", "/other")[0] is None
    assert ai_policy.evaluate(policy, "old thing")[0] is None  # expired


# ── the verdict ───────────────────────────────────────────────────────────────

def test_the_verdict_follows_access_rules_and_roles(registry, home):
    ctx = ShellContext(registry=registry, config={})
    assert ai_run.tier("mkdir x", ctx) == "approval"
    ctx.ai_run = True
    assert ai_run.tier("mkdir x", ctx) == "granted"
    assert ai_run.tier("cat a", ctx) == "read-only"
    write_policy(home, '''
[[rule]]
action = "deny"
match = "mkdir*"
[[rule]]
action = "ask"
match = "touch*"
[[rule]]
action = "allow"
match = "tee*"
[role.reader]
run = "read-only"
''')
    assert ai_run.tier("mkdir x", ctx) == "denied"
    assert ai_run.tier("touch x", ctx) == "approval"  # an ask rule beats access on
    assert ai_run.check("mkdir x", ctx).reason.startswith("the policy denies `mkdir*`")
    ctx.ai_run = False
    assert ai_run.tier("tee x", ctx) == "granted"  # an allow rule works with access off
    ctx.ai_run = True
    assert ai_run.tier("monitor-only", ctx, "monitor") == "denied"
    assert ai_run.tier("cat a", ctx, "reader") == "read-only"
    assert ai_run.tier("rm a", ctx, "reader") == "denied"
    assert ai_run.tier("rm a", ctx, "worker") == "granted"


# ── through MCP ───────────────────────────────────────────────────────────────

def test_a_denied_command_is_not_run_or_queued(registry, real_shell, home, tmp_path):
    from shellcraft import review_spool as spool

    sh, ctx = real_shell
    ctx.ai_run = True
    write_policy(home, '[[rule]]\naction = "deny"\nmatch = "mkdir*"\n')
    server = build_server(registry)
    out = _call(server, lambda c: c.call_tool("run_in_shell", {"command": "mkdir nope", "reason": "x"}))
    assert out.is_error and "not run" in out.content[0].text and "mkdir*" in out.content[0].text
    assert not (tmp_path / "nope").exists() and spool.list_items("pending") == []


def test_an_ask_rule_queues_even_with_access_on(registry, real_shell, home):
    from shellcraft import review_spool as spool

    sh, ctx = real_shell
    ctx.ai_run = True
    write_policy(home, '[[rule]]\naction = "ask"\nmatch = "mkdir*"\n')
    server = build_server(registry)
    out = _call(server, lambda c: c.call_tool("run_in_shell", {"command": "mkdir q", "reason": "x", "wait_seconds": 0}))
    data = json.loads(out.content[0].text)
    assert data["mode"] == "approval"
    item = spool.list_items("pending", "review")[0]
    assert "the policy asks about `mkdir*`" in item["description"]


def test_the_monitor_role_is_offered_only_reading_tools_and_cannot_run(registry, real_shell, home):
    sh, ctx = real_shell
    ctx.ai_run = True
    server = build_server(registry, role="monitor")
    names = {t.name for t in _call(server, lambda c: c.list_tools()).tools}
    assert "get_recent_output" in names and "send_tui_alert" in names
    assert not names & {"run_in_shell", "open_subshell", "request_session_grant", "set_clip", "shellcraft_pipeline"}
    out = _call(server, lambda c: c.call_tool("run_in_shell", {"command": "ls", "reason": "x"}))
    assert out.is_error and "monitor role may not use this tool" in out.content[0].text
    entry = ai_audit.tail(1)[0]
    assert entry["tool"] == "run_in_shell" and entry["tier"] == "denied" and entry["role"] == "monitor"


def test_the_shell_checks_the_role_again(registry, real_shell, home):
    sh, ctx = real_shell
    ctx.ai_run = True
    answer = mainlink.ask(("run", {"command": "mkdir x", "reason": "r", "role": "monitor"}), None)
    assert answer["denied"] and "monitor" in answer["reason"]


def test_every_tool_takes_an_agent_label_that_names_the_job(registry, real_shell, tmp_path):
    sh, ctx = real_shell
    ctx.ai_run = True
    server = build_server(registry)
    tools = {t.name: t for t in _call(server, lambda c: c.list_tools()).tools}
    assert all("agent" in t.input_schema["properties"] for t in tools.values())
    out = _call(server, lambda c: c.call_tool("run_in_shell", {"command": "mkdir w", "reason": "x",
                                                                "agent": "worker-2", "wait_seconds": 10}))
    number = json.loads(out.content[0].text)["number"]
    assert ctx.jobs.get(number).title == "✦ AI worker-2: mkdir w"


def test_the_grant_request_is_answered_at_once_when_access_is_on(registry, real_shell):
    sh, ctx = real_shell
    ctx.ai_run = True
    out = _call(build_server(registry), lambda c: c.call_tool("request_session_grant", {"reason": "triage"}))
    data = json.loads(out.content[0].text)
    assert data["granted"] is True and data["item_id"] is None


def test_the_budget_stops_module_calls(registry, home, tmp_path):
    (tmp_path / "echoargs.py").write_text("def run(args, stdin):\n    return ' '.join(args)\n")
    (tmp_path / "echoargs.skill").write_text('summary = "echo"\n')
    local = ModuleRegistry(tmp_path)
    local.load()
    write_policy(home, "[budget]\nmodule_calls = 1\n")
    server = build_server(local)
    first = _call(server, lambda c: c.call_tool("echoargs", {"args": ["a"]}))
    assert not first.is_error
    from shellcraft.mcp_server import ShellcraftMCP

    app = ShellcraftMCP(local)
    app.module_calls = 1
    assert app._over_budget(ai_policy.load(), "echoargs") is True


# ── the audit log and the command ─────────────────────────────────────────────

def test_the_audit_log_records_calls_and_rotates(home):
    ai_audit.record("run_in_shell", "mkdir x", agent="w1", role="worker", client="claude-code", tier="granted", ok=True,
                    seconds=0.5)
    ai_audit.record("get_jobs", agent="w2")
    entries = ai_audit.tail(10)
    assert [e["tool"] for e in entries] == ["run_in_shell", "get_jobs"]
    assert ai_audit.tail(10, agent="w1")[0]["tier"] == "granted"
    target = ai_audit.path()
    target.write_text("x" * (ai_audit.MAX_BYTES + 1) + "\n")
    ai_audit.record("later")
    assert target.with_name(target.name + ".1").exists() and ai_audit.tail(5)[-1]["tool"] == "later"


def test_a_tool_call_is_logged_with_how_it_was_answered(registry, real_shell, home):
    sh, ctx = real_shell
    ctx.ai_run = True
    server = build_server(registry)
    _call(server, lambda c: c.call_tool("run_in_shell", {"command": "ls", "reason": "x", "agent": "w9",
                                                         "wait_seconds": 10}))
    entry = ai_audit.tail(1)[0]
    assert entry["tool"] == "run_in_shell" and entry["agent"] == "w9" and entry["tier"] == "read-only"
    assert entry["summary"] == "ls" and entry["ok"] is True


def test_the_ai_command(registry, home):
    ctx = ShellContext(registry=registry, config={}, ai_run=True)
    ai_audit.record("run_in_shell", "mkdir x", agent="w1", tier="granted", ok=True)
    state = to_text(run_line("ai", ctx).output)
    assert "AI access: on" in state and "mkdir x" in state
    assert "w1" in to_text(run_line("ai log", ctx).output)
    assert json.loads(to_text(run_line("ai log --json", ctx).output).splitlines()[0])["tool"] == "run_in_shell"
    assert "monitor" in to_text(run_line("ai roles", ctx).output)
    assert "no policy file" in to_text(run_line("ai policy", ctx).output)
    run_line("ai off", ctx)
    assert ctx.ai_run is False and ctx.config["settings"]["ai_access"] is False
    write_policy(home, '[[rule]]\naction = "deny"\nmatch = "rm*"\n')
    assert "rm*" in to_text(run_line("ai policy", ctx).output)


def test_approving_is_logged(registry, real_shell, home):
    from shellcraft import review_spool as spool

    sh, ctx = real_shell
    item = spool.create("review", "mkdir z", "r", [spool.artifact("command", "mkdir z")], "medium", "command",
                        created_by="mcp · claude-code · w3")
    run_line(f"review reject {item['id']} no", ctx)
    entry = ai_audit.tail(1)[0]
    assert entry["tool"] == "review" and entry["decision"] == "rejected" and "w3" in entry["client"]


def test_role_flag_is_checked_at_start(capsys):
    from shellcraft.cli import main

    assert main(["--mcp", "--role", "nosuch"]) == 2
    assert "unknown role 'nosuch'" in capsys.readouterr().err
    assert main(["--role", "monitor"]) == 2
