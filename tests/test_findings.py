"""The case file and the module-answer memory."""

import io
import json
import threading

import anyio
import pytest
from mcp.client import Client
from rich.console import Console

from shellcraft import ai_policy, findings, mainlink, modcache
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


# ── the case ──────────────────────────────────────────────────────────────────

def test_the_same_thing_found_twice_is_one_finding(home):
    a, new_a = findings.add("ip", "203.0.113.9", "brute force", "50 failed logins",
                            [{"command": "grep Failed auth.log", "output": "50 lines"}], "medium", "high", "w1")
    b, new_b = findings.add("IP".lower(), " 203.0.113.9 ", "", "also in nginx", ["seen in access.log"],
                            "high", "medium", "w2", tags=["bruteforce"])
    assert new_a and not new_b and a["id"] == b["id"]
    assert b["seen"] == 2 and b["severity"] == "high" and b["confidence"] == "high"
    assert b["agents"] == ["w1", "w2"] and len(b["evidence"]) == 2 and b["tags"] == ["bruteforce"]
    assert "50 failed logins" in b["summary"] and "also in nginx" in b["summary"]
    assert len(findings.list_findings()) == 1


def test_domains_and_hashes_ignore_case_but_users_do_not(home):
    findings.add("domain", "Example.COM")
    findings.add("domain", "example.com")
    findings.add("user", "Root")
    findings.add("user", "root")
    assert sorted(f["value"] for f in findings.list_findings()) == ["Root", "example.com", "root"]


def test_two_agents_adding_at_once_make_one_finding(home):
    results = []

    def worker(name):
        results.append(findings.add("host", "db1", agent=name, evidence=[f"seen by {name}"])[1])

    threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count(True) == 1
    (only,) = findings.list_findings()
    assert only["seen"] == 6 and len(only["agents"]) == 6


def test_listing_filters_and_orders_by_severity(home):
    findings.add("ip", "10.0.0.1", severity="low", tags=["lan"])
    findings.add("ip", "10.0.0.2", severity="critical", agent="w1")
    findings.add("domain", "evil.test", "C2 beacon", severity="high")
    assert [f["value"] for f in findings.list_findings()] == ["10.0.0.2", "evil.test", "10.0.0.1"]
    assert [f["value"] for f in findings.list_findings(severity="high")] == ["10.0.0.2", "evil.test"]
    assert [f["value"] for f in findings.list_findings(kind="domain")] == ["evil.test"]
    assert [f["value"] for f in findings.list_findings(tag="lan")] == ["10.0.0.1"]
    assert [f["value"] for f in findings.list_findings(agent="w1")] == ["10.0.0.2"]
    assert [f["value"] for f in findings.list_findings(query="beacon")] == ["evil.test"]


def test_update_changes_the_rating_and_adds_evidence(home):
    item, _ = findings.add("ip", "10.0.0.1")
    changed = findings.update(item["id"], severity="high", status="confirmed", add_tags=["a", "b"],
                              remove_tags=["a"], evidence=["new proof"], agent="w3")
    assert changed["severity"] == "high" and changed["status"] == "confirmed" and changed["tags"] == ["b"]
    assert changed["evidence"][-1]["note"] == "new proof" and changed["agents"] == ["w3"]
    with pytest.raises(findings.FindingError, match="severity must be"):
        findings.update(item["id"], severity="awful")


def test_bad_values_are_refused(home):
    with pytest.raises(findings.FindingError, match="kind must be"):
        findings.add("planet", "x")
    with pytest.raises(findings.FindingError, match="needs a value"):
        findings.add("ip", "  ")
    with pytest.raises(findings.FindingError, match="case name"):
        findings.use("../escape")
    with pytest.raises(findings.FindingError, match="not a finding id"):
        findings.get("../../etc/passwd")


def test_cases_are_separate_and_one_is_current(home):
    findings.add("ip", "1.1.1.1")
    assert findings.use("acme") == "acme" and findings.current() == "acme"
    assert findings.list_findings() == []
    findings.add("ip", "2.2.2.2")
    assert findings.cases() == {"acme": 1, "default": 1}
    assert [f["value"] for f in findings.list_findings("default")] == ["1.1.1.1"]


def test_the_report_groups_by_severity_with_evidence(home):
    findings.add("ip", "203.0.113.9", "Brute force", "50 failed logins", [{"command": "grep x", "output": "50"}],
                 severity="high", agent="w1")
    findings.add("host", "web1", severity="info")
    report = findings.export_markdown()
    assert report.startswith("# Findings: default")
    assert report.index("## High") < report.index("## Info")
    assert "### Brute force" in report and "$ grep x" in report and "Found by: w1" in report
    assert json.loads(findings.export_json())["findings"][0]["value"] == "203.0.113.9"


# ── through MCP ───────────────────────────────────────────────────────────────

def test_agents_share_one_case_through_the_tools(registry, home):
    server = build_server(registry)
    first = _json(call(server, "add_finding", kind="ip", value="203.0.113.9", severity="high", agent="worker-1",
                       evidence=[{"command": "grep Failed auth.log", "output": "12 hits"}]))
    again = _json(call(server, "add_finding", kind="ip", value="203.0.113.9", summary="also nginx",
                       agent="worker-2"))
    assert first["created"] is True and again["created"] is False and again["agents"] == ["worker-1", "worker-2"]
    listed = _json(call(server, "list_findings", severity="high", agent="worker-2"))
    assert len(listed) == 1 and listed[0]["evidence"] == 1 and "output" not in json.dumps(listed)
    full = _json(call(server, "get_finding", finding_id=first["id"]))
    assert full["evidence"][0]["output"] == "12 hits"
    done = _json(call(server, "update_finding", finding_id=first["id"][-6:], status="confirmed", add_tags=["bf"]))
    assert done["status"] == "confirmed" and done["tags"] == ["bf"]
    assert call(server, "add_finding", kind="planet", value="x").is_error
    assert call(server, "get_finding", finding_id="nope").is_error


def test_the_monitor_can_read_the_case_but_not_write_it(registry, home):
    names = {t.name for t in _call(build_server(registry, role="monitor"), lambda c: c.list_tools()).tools}
    assert {"list_findings", "get_finding"} <= names and not names & {"add_finding", "update_finding"}


# ── the memory of module answers ──────────────────────────────────────────────

@pytest.fixture
def counted(tmp_path, home):
    """A module that counts how often it really runs."""
    (tmp_path / "ping.py").write_text(
        "import pathlib\ndef run(args, stdin):\n    p = pathlib.Path('ran.txt')\n"
        "    n = int(p.read_text() or 0) + 1 if p.exists() else 1\n    p.write_text(str(n))\n"
        "    if args and args[0] == 'boom':\n        raise SystemExit('boom')\n    return 'pong ' + ' '.join(args)\n")
    (tmp_path / "ping.skill").write_text('summary = "ping"\n')
    (tmp_path / "myip.py").write_text("def run(args, stdin):\n    return 'ip'\n")
    (tmp_path / "myip.skill").write_text('summary = "ip"\n')
    registry = ModuleRegistry(tmp_path)
    registry.load()
    return registry, tmp_path


def runs(tmp_path):
    return int((tmp_path / "ran.txt").read_text())


def test_the_same_question_is_answered_from_memory(counted):
    registry, tmp_path = counted
    server = build_server(registry)
    first = call(server, "ping", args=["a"])
    second = call(server, "ping", args=["a"], agent="worker-2")
    assert first.content[0].text == second.content[0].text == "pong a" and runs(tmp_path) == 1
    assert second.meta["shellcraft"]["cached"] is True and "shellcraft" not in (first.meta or {})
    call(server, "ping", args=["b"])
    assert runs(tmp_path) == 2  # another question
    call(server, "ping", args=["a"], fresh=True)
    assert runs(tmp_path) == 3  # asked again on request


def test_errors_are_not_remembered_and_excluded_modules_always_run(counted, home):
    registry, tmp_path = counted
    server = build_server(registry)
    assert call(server, "ping", args=["boom"]).is_error
    assert call(server, "ping", args=["boom"]).is_error and runs(tmp_path) == 2
    (home / ai_policy.FILE).write_text('[cache]\nexclude = ["ping"]\n')
    ai_policy._cache.clear()
    call(server, "ping", args=["x"])
    call(server, "ping", args=["x"])
    assert runs(tmp_path) == 4


def test_the_memory_can_be_turned_off_and_expires(counted, home):
    registry, tmp_path = counted
    (home / ai_policy.FILE).write_text("[cache]\nseconds = 0\n")
    ai_policy._cache.clear()
    server = build_server(registry)
    call(server, "ping", args=["a"])
    call(server, "ping", args=["a"])
    assert runs(tmp_path) == 2
    key = modcache.key("ping", ["z"], "")
    modcache.put(key, "ping", "old", 60)
    assert modcache.get(key, 60) is not None and modcache.get(key, -1) is None


def test_editing_a_module_forgets_what_it_said(counted):
    registry, tmp_path = counted
    server = build_server(registry)
    call(server, "ping", args=["a"])
    spec = registry.get("ping")
    import os

    os.utime(spec.path, ns=(1, 1))
    call(server, "ping", args=["a"])
    assert runs(tmp_path) == 2


def test_a_remembered_answer_costs_no_budget(counted, home):
    registry, tmp_path = counted
    (home / ai_policy.FILE).write_text("[budget]\nmodule_calls = 2\n")
    ai_policy._cache.clear()
    server = build_server(registry)
    for _ in range(5):
        assert not call(server, "ping", args=["same"]).is_error
    assert runs(tmp_path) == 1
    assert not call(server, "ping", args=["other"]).is_error
    assert call(server, "ping", args=["third"]).is_error


# ── the shell ─────────────────────────────────────────────────────────────────

def test_the_findings_command(real_shell, home, tmp_path):
    sh, ctx = real_shell
    assert "no findings" in to_text(run_line("findings", ctx).output)
    assert "added" in to_text(run_line("findings add ip 203.0.113.9 -s high -t 'Brute force' -n 'from auth.log'",
                                        ctx).output)
    assert "merged" in to_text(run_line("findings add ip 203.0.113.9 -c high", ctx).output)
    listing = to_text(run_line("findings", ctx).output)
    assert "203.0.113.9" in listing and "high" in listing and "Brute force" in listing and "\t2\t" in listing
    short = findings.list_findings()[0]["id"][-6:]
    shown = to_text(run_line(f"findings show {short}", ctx).output)
    assert "from auth.log" in shown and "confidence high" in shown
    run_line(f"findings set {short} --status confirmed --tag bf", ctx)
    assert findings.list_findings()[0]["status"] == "confirmed"
    assert "# Findings: default" in to_text(run_line("findings export", ctx).output)
    run_line("findings export report.md", ctx)
    assert (tmp_path / "report.md").read_text().startswith("# Findings: default")
    assert json.loads(to_text(run_line("findings export --json", ctx).output))["case"] == "default"
    run_line("findings case acme", ctx)
    assert "● acme" in to_text(run_line("findings case", ctx).output)
    run_line("findings case default", ctx)
    run_line(f"findings rm {short}", ctx)
    assert findings.list_findings() == []
    with pytest.raises(Exception, match="kind"):
        run_line("findings add planet x", ctx)


def test_a_new_high_finding_raises_an_alert_but_old_ones_do_not(real_shell, home):
    sh, ctx = real_shell
    findings.add("ip", "10.0.0.5", "old news", severity="critical")
    sh._review_check()  # the first look only notes what is there
    assert not [a for a in ctx.alerts.current() if "AI finding" in a.message]
    findings.add("ip", "10.0.0.6", "Brute force", severity="high", agent="worker-1")
    findings.add("ip", "10.0.0.7", "quiet", severity="low")
    sh._review_check()
    alerts = [a for a in ctx.alerts.current() if "AI finding" in a.message]
    assert len(alerts) == 1 and "Brute force" in alerts[0].message and "worker-1" in alerts[0].message
    sh._review_check()
    assert len([a for a in ctx.alerts.current() if "AI finding" in a.message]) == 1
