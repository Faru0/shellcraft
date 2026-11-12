"""shellcraft --install-ai / --check-ai / --uninstall-ai: the ai/ agents and skills into Claude Code's folders."""

from pathlib import Path

import pytest

from shellcraft import ai_install, cli

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def where(tmp_path):
    return tmp_path / ".claude", tmp_path / "home"


def run(items, target, home, **kw):
    return {r.rel: r for r in ai_install.install(items, target, home=home, **kw)}


def test_the_catalog_matches_the_ai_folder():
    items = ai_install.catalog()
    on_disk = {f"agents/{p.name}" for p in (ROOT / "ai" / "agents").glob("*.md")} | {
        f"skills/{p.parent.name}/SKILL.md" for p in (ROOT / "ai" / "skills").glob("*/SKILL.md")}
    assert {i.rel for i in items} == on_disk and len(items) == len(on_disk)


def test_an_agent_and_a_skill_of_the_same_name_are_both_installed(where):
    target, home = where
    both = {i.rel for i in ai_install.select(["soc-triage"])}
    assert {"agents/soc-triage.md", "skills/soc-triage/SKILL.md"} <= both
    written = run(ai_install.select([]), target, home)
    for name in ("shellcraft-copilot", "soc-triage"):
        assert written[f"agents/{name}.md"].status == "new" and written[f"skills/{name}/SKILL.md"].status == "new"
    assert {i.rel for i in ai_install.select(["soc-triage"], agents_only=True)} == {"agents/soc-triage.md"}


def test_the_wheel_packs_the_ai_folder():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"shellcraft.bundled_ai" = "ai"' in text and "agents/*.md" in text and "skills/*/SKILL.md" in text


def test_an_agent_brings_the_skills_it_names():
    names = {i.name for i in ai_install.select(["worker"])}
    assert names == {"worker", "task-board", "case-file"}


def test_team_is_three_agents_and_their_skills():
    names = {i.name for i in ai_install.select([], team=True)}
    assert {"orchestrator", "worker", "monitor", "task-board", "case-file", "shellcraft-copilot"} <= names
    assert "soc-triage" not in names


def test_skills_only_and_agents_only():
    assert {i.kind for i in ai_install.select([], skills_only=True)} == {"skill"}
    assert {i.kind for i in ai_install.select([], agents_only=True)} == {"agent"}
    with pytest.raises(ai_install.InstallError):
        ai_install.select([], skills_only=True, agents_only=True)


def test_an_unknown_name_lists_the_known_ones():
    with pytest.raises(ai_install.InstallError, match="nope.*known: .*worker"):
        ai_install.select(["nope"])


def test_install_writes_every_file_once(where):
    target, home = where
    items = ai_install.select([])
    first = run(items, target, home)
    assert {r.status for r in first.values()} == {"new"}
    assert (target / "agents" / "worker.md").is_file() and (target / "skills" / "task-board" / "SKILL.md").is_file()
    assert {r.status for r in run(items, target, home).values()} == {"unchanged"}


def test_dry_run_writes_nothing(where):
    target, home = where
    results = run(ai_install.select([]), target, home, dry_run=True)
    assert {r.status for r in results.values()} == {"new"}
    assert not target.exists() and not ai_install.manifest_path(home).exists()


def test_an_edited_file_is_skipped_with_a_diff_and_force_keeps_a_bak(where):
    target, home = where
    items = ai_install.select(["worker"])
    run(items, target, home)
    mine = target / "agents" / "worker.md"
    mine.write_text(mine.read_text(encoding="utf-8") + "my note\n", encoding="utf-8")
    result = run(items, target, home)["agents/worker.md"]
    assert result.status == "edited" and "my note" in result.diff
    assert "my note" in mine.read_text(encoding="utf-8")
    forced = run(items, target, home, force=True)["agents/worker.md"]
    assert forced.status == "forced"
    assert "my note" not in mine.read_text(encoding="utf-8")
    assert "my note" in (target / "agents" / "worker.md.bak").read_text(encoding="utf-8")


def test_a_file_that_is_not_ours_is_treated_as_edited(where):
    target, home = where
    other = target / "agents" / "worker.md"
    other.parent.mkdir(parents=True)
    other.write_text("someone else's worker\n", encoding="utf-8")
    assert run(ai_install.select(["worker"]), target, home)["agents/worker.md"].status == "edited"
    assert other.read_text(encoding="utf-8") == "someone else's worker\n"


def test_a_new_version_updates_a_file_we_wrote(where, tmp_path):
    target, home = where
    source = tmp_path / "ai"
    (source / "agents").mkdir(parents=True)
    (source / "skills").mkdir()
    (source / "agents" / "a.md").write_text("one\n", encoding="utf-8")
    run(ai_install.select([], source=source), target, home)
    (source / "agents" / "a.md").write_text("two\n", encoding="utf-8")
    assert run(ai_install.select([], source=source), target, home)["agents/a.md"].status == "update"
    assert (target / "agents" / "a.md").read_text(encoding="utf-8") == "two\n"


def test_check_reports_without_writing(where):
    target, home = where
    items = ai_install.select(["worker"])
    assert {r.status for r in ai_install.check(items, target, home=home)} == {"missing"}
    run(items, target, home)
    assert {r.status for r in ai_install.check(items, target, home=home)} == {"unchanged"}
    (target / "agents" / "worker.md").write_text("changed\n", encoding="utf-8")
    statuses = {r.rel: r.status for r in ai_install.check(items, target, home=home)}
    assert statuses["agents/worker.md"] == "edited"


def test_uninstall_removes_only_what_we_wrote_and_unedited(where):
    target, home = where
    other = target / "agents" / "homelab.md"
    other.parent.mkdir(parents=True)
    other.write_text("not ours\n", encoding="utf-8")
    items = ai_install.select(["worker"])
    run(items, target, home)
    (target / "agents" / "worker.md").write_text("edited\n", encoding="utf-8")
    results = {r.rel: r.status for r in ai_install.uninstall(items, target, home=home)}
    assert results["agents/worker.md"] == "kept" and results["skills/task-board/SKILL.md"] == "removed"
    assert not (target / "skills" / "task-board").exists()
    assert other.read_text(encoding="utf-8") == "not ours\n" and (target / "agents" / "worker.md").exists()


def test_crlf_checkouts_install_as_lf(where, tmp_path):
    target, home = where
    source = tmp_path / "ai"
    (source / "agents").mkdir(parents=True)
    (source / "skills").mkdir()
    (source / "agents" / "a.md").write_bytes(b"one\r\ntwo\r\n")
    run(ai_install.select([], source=source), target, home)
    assert (target / "agents" / "a.md").read_bytes() == b"one\ntwo\n"


# ── the command line ────────────────────────────────────────────────────────

@pytest.fixture
def cli_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / "sc"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "user")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_cli_install_user_and_project(cli_home, capsys):
    assert cli.main(["--install-ai", "worker", "--user"]) == 0
    assert (cli_home / "user" / ".claude" / "agents" / "worker.md").is_file()
    assert cli.main(["--install-ai", "monitor", "--project"]) == 0
    assert (cli_home / ".claude" / "agents" / "monitor.md").is_file()
    assert "new" in capsys.readouterr().out


def test_cli_without_a_target_and_without_a_terminal_says_where(cli_home, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", type("S", (), {"isatty": lambda self: False})())
    assert cli.main(["--install-ai"]) == 2
    assert "--user" in capsys.readouterr().err
    assert not (cli_home / "user").exists()


def test_cli_asks_on_a_terminal(cli_home, monkeypatch):
    monkeypatch.setattr("sys.stdin", type("S", (), {"isatty": lambda self: True})())
    monkeypatch.setattr("builtins.input", lambda prompt="": "p")
    assert cli.main(["--install-ai", "monitor"]) == 0
    assert (cli_home / ".claude" / "agents" / "monitor.md").is_file()


def test_cli_unknown_name_and_conflicts(cli_home, capsys):
    assert cli.main(["--install-ai", "nope", "--user"]) == 1
    assert "no agent or skill named nope" in capsys.readouterr().err
    assert cli.main(["--install-ai", "--user", "--project"]) == 2
    assert cli.main(["--install-ai", "--user", "--mcp"]) == 2
    assert cli.main(["--install-ai", "--check-ai", "--user"]) == 2


def test_cli_check_and_uninstall(cli_home, capsys):
    cli.main(["--install-ai", "worker", "--user"])
    capsys.readouterr()
    assert cli.main(["--check-ai", "worker", "--user"]) == 0
    assert "unchanged" in capsys.readouterr().out
    assert cli.main(["--uninstall-ai", "worker", "--user"]) == 0
    assert "removed" in capsys.readouterr().out
    assert not (cli_home / "user" / ".claude" / "agents" / "worker.md").exists()
