"""Startup: ~/.shellcraft/rc, then the .sc files in ~/.shellcraft/startup/ (like ~/.bashrc.d), then
a profile's rc and its startup folder; the startup builtin; source."""

import io
import os

import pytest
from rich.console import Console

from shellcraft import startup
from shellcraft.context import ShellContext, to_text
from shellcraft.loader import ModuleRegistry
from shellcraft.pipeline import PipelineError, run_line
from shellcraft.themes import UI, all_themes


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / "home"))
    for name in ("SC_FIRST", "SC_SECOND", "SC_PROJECT", "SC_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "home" / "startup").mkdir(parents=True)
    return tmp_path / "home"


def write(home, name, body):
    (home / "startup" / name).write_text(body)


# ── the startup folder ───────────────────────────────────────────────────────

def test_sc_files_run_in_name_order_and_share_what_they_set(home, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write(home, "20-second.sc", "env set SC_SECOND=$SC_FIRST-and-more   # sees what 10 set\necho second\n")
    write(home, "10-first.sc", "# the first one\nenv set SC_FIRST=one\necho first\n")
    write(home, "_helper.sc", "echo never on its own\n")
    write(home, "notes.txt", "not a startup file\n")
    ctx, shown, errors = _shell_ctx(home)
    results = startup.run_folder(ctx, startup.folder())
    assert [r.name for r in results] == ["10-first.sc", "20-second.sc"]
    assert all(r.ok and r.ran == 2 for r in results) and errors == []
    assert os.environ["SC_SECOND"] == "one-and-more"
    assert [line for line in shown if not line.startswith("✓")] == ["first\n", "second\n"]
    assert set(ctx.startup_results) == {str(home / "startup" / "10-first.sc"), str(home / "startup" / "20-second.sc")}


def test_a_helper_can_be_sourced_and_a_failing_line_names_its_file(home, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write(home, "_common.sc", "env set SC_FIRST=from-helper\n")
    write(home, "10-a.sc", f"source {home / 'startup' / '_common.sc'}\nnosuchcommand\necho after\n")
    ctx, shown, errors = _shell_ctx(home)
    (result,) = startup.run_folder(ctx, startup.folder())
    assert os.environ["SC_FIRST"] == "from-helper" and result.failed == 1 and result.label == "3 commands, 1 failed"
    assert "✗ 10-a.sc:2: nosuchcommand\n" in shown and "after\n" in shown


def test_python_files_are_not_run_and_startup_says_so(home, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write(home, "10-old.py", "open('ran.txt', 'w').write('x')\n")
    write(home, "20-new.sc", "echo new\n")
    ctx, shown, errors = _shell_ctx(home)
    assert [r.name for r in startup.run_folder(ctx, startup.folder())] == ["20-new.sc"]
    assert not (tmp_path / "ran.txt").exists()
    listing = to_text(run_line("startup", ctx).output)
    assert "20-new.sc" in listing and "1 command, ok" in listing
    assert "10-old.py is a Python startup script, which isn't run any more" in listing


def test_the_startup_builtin(home, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx, shown, errors = _shell_ctx(home)
    assert "no startup files" in to_text(run_line("startup", ctx).output)
    write(home, "10-a.sc", "env set SC_FIRST=a\n")
    write(home, "20-b.sc", "env set SC_SECOND=b\n")
    assert "not run this session" in to_text(run_line("startup", ctx).output)
    out = to_text(run_line("startup run 20-b", ctx).output)
    assert "20-b.sc" in out and "10-a.sc" not in out and os.environ["SC_SECOND"] == "b" and "SC_FIRST" not in os.environ
    out = to_text(run_line("startup run", ctx).output)
    assert "10-a.sc  1 command, ok" in out and os.environ["SC_FIRST"] == "a"
    assert to_text(run_line("startup --folder", ctx).output).strip() == str(home / "startup")
    with pytest.raises(PipelineError, match="no startup file named nope"):
        run_line("startup run nope", ctx)
    with pytest.raises(PipelineError, match="usage: startup"):
        run_line("startup stop", ctx)


def test_the_cli_runs_rc_then_the_folder_unless_told_not_to(home, started):
    import json

    from shellcraft.cli import main

    (home / "rc").write_text("env set SC_FIRST=rc\n")
    write(home, "10-a.sc", "env set SC_SECOND=$SC_FIRST-then-folder\n")
    main([])
    assert started.rc_runs == 1 and len(started[-1].startup_results) == 1
    main(["--no-startup"])
    assert started.rc_runs == 1 and started[-1].startup_results == {}
    (home / "config.json").write_text(json.dumps({"settings": {"startup": False}}))
    main([])
    assert started.rc_runs == 1 and started[-1].startup_results == {}  # settings startup off


def test_a_profile_runs_its_own_folder_at_start_and_on_switch(home, tmp_path, monkeypatch, started):
    from shellcraft import profiles
    from shellcraft.cli import main

    monkeypatch.chdir(tmp_path)
    profiles.create("acme", {"description": "acme"}, "env set SC_FIRST=profile-rc\n", "")
    folder = profiles.folder("acme") / "startup"
    folder.mkdir(exist_ok=True)
    (folder / "10-p.sc").write_text("env set SC_PROFILE=$SC_FIRST-then-its-folder\n")
    main(["--profile", "acme"])
    assert os.environ["SC_PROFILE"] == "profile-rc-then-its-folder"
    os.environ.pop("SC_PROFILE")
    ctx, shown, errors = _shell_ctx(home)
    run_line("profile use acme", ctx)
    assert os.environ["SC_PROFILE"] == "profile-rc-then-its-folder"
    listing = to_text(run_line("startup", ctx).output)
    assert "profile:acme/10-p.sc" in listing
    run_line("profile off", ctx)


def test_profile_export_import_and_copy_carry_its_startup_files(home, tmp_path):
    from shellcraft import profiles

    profiles.create("acme", {"description": "acme"}, "", "")
    folder = profiles.folder("acme") / "startup"
    folder.mkdir(exist_ok=True)
    (folder / "10-p.sc").write_text("echo hi\n")
    bundle = profiles.export_bundle("acme")
    assert bundle["startup"] == {"10-p.sc": "echo hi\n"}
    bundle["startup"]["../escape.sc"] = "echo no"  # a path, not a file name: skipped
    profiles.import_bundle(bundle, "other")
    assert (profiles.folder("other") / "startup" / "10-p.sc").read_text() == "echo hi\n"
    assert not (profiles.folder("other") / "escape.sc").exists()
    profiles.copy_profile("acme", "third")
    assert (profiles.folder("third") / "startup" / "10-p.sc").is_file()


# ── the rc file and source ───────────────────────────────────────────────────

def test_logical_lines_skip_comments_and_join_blocks():
    text = "# a comment\n\ntheme nord\nfor h in a b\ndo\n  echo $h\ndone\nif true; then\n  # inside\n  echo yes\nfi\nfor x in 1\n"
    lines = startup.logical_lines(text)
    assert [n for n, _ in lines] == [3, 4, 8, 12]
    assert lines[0][1] == "theme nord" and lines[1][1].startswith("for h in a b") and lines[1][1].endswith("done")
    assert "echo yes" in lines[2][1] and lines[3][1] == "for x in 1"  # unfinished at the end: left as is


def _shell_ctx(home):
    ctx = ShellContext(registry=ModuleRegistry(home / "modules"))
    ctx.ui = UI(all_themes({})["nord"], Console(file=io.StringIO()))
    ctx.interactive = True
    shown, errors = [], []
    ctx.emit = lambda value: shown.append(to_text(value))
    ctx.emit_error = lambda exc, where: errors.append(where)
    return ctx, shown, errors


def test_source_runs_lines_as_typed_and_keeps_going(home, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "work").mkdir()
    rc = home / "rc"
    rc.write_text("# startup\nexport SC_FIRST=from-rc\ncd work\necho hello\nnosuchcommand\nfor n in 1 2\ndo\n  echo n$n\ndone\nalias hi='echo hi'\nhi there\n")
    ctx, shown, errors = _shell_ctx(home)
    ran, failed = startup.source(rc, ctx)
    assert ran == 7 and failed == 1
    assert os.environ["SC_FIRST"] == "from-rc" and os.getcwd() == str(tmp_path / "work")
    assert "hello\n" in shown and "n1\n" in shown and "n2\n" in shown and "hi there\n" in shown
    assert errors == ["nosuchcommand"] and "✗ rc:5: nosuchcommand\n" in shown
    # the builtin: by hand, with a status when a line failed
    ctx.emit = None  # outside the prompt, outputs are not printed
    assert run_line(f"source {rc}", ctx).status == 1
    with pytest.raises(PipelineError, match="no such file"):
        run_line("source nope.rc", ctx)
    (home / "loop.rc").write_text(f"source {home / 'loop.rc'}\n")
    ctx.emit_error = lambda exc, where: errors.append(str(exc))
    startup.source(home / "loop.rc", ctx)
    assert any("8 deep" in e for e in errors)
    ctx.allow_stateful = False
    with pytest.raises(PipelineError):
        run_line(f"source {rc}", ctx)  # never over MCP


def test_rc_can_source_other_files_the_bash_way(home, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".shellcraft").mkdir()
    (tmp_path / ".shellcraft" / "rc.work").write_text("export SC_FIRST=work\necho work loaded\n")
    (tmp_path / "rc.d").mkdir()
    (tmp_path / "rc.d" / "10-a.rc").write_text("echo a loaded\n")
    rc = home / "rc"
    rc.write_text("if [ -f ~/.shellcraft/rc.work ]; then\n    . ~/.shellcraft/rc.work\nfi\n"
                  "[ -f ~/.shellcraft/rc.missing ] && source ~/.shellcraft/rc.missing\n"
                  'for f in rc.d/*.rc; do [ -f "$f" ] && source "$f"; done\n')
    ctx, shown, errors = _shell_ctx(home)
    startup.source(rc, ctx)
    assert "work loaded\n" in shown and "a loaded\n" in shown and errors == []
    assert os.environ["SC_FIRST"] == "work"
    assert "." not in to_text(run_line("help", ctx).output).split()  # hidden; man source covers it


def test_env_set_and_export_expand_a_leading_tilde(home, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SC_TILDE", raising=False)
    ctx, shown, errors = _shell_ctx(home)
    run_line("p=demo", ctx)
    run_line("env set SC_TILDE=~/work/$p", ctx)
    assert os.environ["SC_TILDE"] == f"{tmp_path}/work/demo"
    run_line("export SC_TILDE=~/x", ctx)
    assert os.environ["SC_TILDE"] == f"{tmp_path}/x"
    run_line("env set SC_TILDE=a~b", ctx)
    assert os.environ["SC_TILDE"] == "a~b"  # only a leading ~
    os.environ.pop("SC_TILDE", None)
