"""Startup scripts (skeleton): order, helpers, the env file hand-back, failures, timeouts, the builtin."""

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
    for name in ("SC_FIRST", "SC_SECOND", "SC_GONE"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "home" / "startup").mkdir(parents=True)
    return tmp_path / "home"


def write(home, name, body):
    (home / "startup" / name).write_text(body)


ENV = "import os\nf = open(os.environ['SHELLCRAFT_ENV_FILE'], 'a')\n"


def test_scripts_run_in_order_and_hand_back_variables(home, monkeypatch):
    monkeypatch.setenv("SC_GONE", "still here")
    write(home, "20-second.py", ENV + "f.write('SC_SECOND=' + os.environ['SC_FIRST'] + '+2\\n')\nprint('second ran')\n")
    write(home, "10-first.py", ENV + "f.write('SC_FIRST=one\\nSC_GONE=\\nnot a variable line\\n')\n")
    write(home, "_helper.py", "raise SystemExit('a helper never runs on its own')\n")
    assert [p.name for p in startup.scripts(home)] == ["10-first.py", "20-second.py"]
    results = startup.run_all(home)
    assert [r.name for r in results] == ["10-first", "20-second"] and all(r.ok for r in results)
    assert os.environ["SC_FIRST"] == "one" and os.environ["SC_SECOND"] == "one+2"  # the second saw the first
    assert "SC_GONE" not in os.environ  # NAME= unsets
    assert results[1].output == "second ran\n" and results[0].env == {"SC_FIRST": "one", "SC_GONE": None}


def test_a_failing_or_slow_script_changes_nothing_and_is_reported(home, monkeypatch):
    write(home, "10-fails.py", ENV + "f.write('SC_FIRST=nope\\n')\nf.close()\nraise SystemExit('broken')\n")
    write(home, "20-slow.py", "import time\ntime.sleep(5)\n")
    monkeypatch.setattr(startup, "TIMEOUT", 0.5)
    fails, slow = [startup.run_one(p, timeout=0.5) for p in startup.scripts(home)]
    assert fails.status == 1 and "broken" in fails.output and fails.label == "exit 1"
    assert "SC_FIRST" not in os.environ  # only a clean exit applies the variables
    assert slow.status == -1 and slow.label.startswith("timed out")


def test_startup_builtin_lists_and_reruns(home):
    write(home, "10-env.py", ENV + "f.write('SC_FIRST=builtin\\n')\nprint('hello')\n")
    ctx = ShellContext(registry=ModuleRegistry(home / "modules"))
    ctx.ui = UI(all_themes({})["nord"], Console(file=io.StringIO()))
    assert "10-env.py\tnot run this session" in to_text(run_line("startup", ctx).output)
    ran = to_text(run_line("startup run", ctx).output)
    assert "⚙ 10-env  ok" in ran and "hello" in ran and os.environ["SC_FIRST"] == "builtin"
    assert "10-env.py\tok (" in to_text(run_line("startup", ctx).output)
    assert "\tSC_FIRST" in to_text(run_line("startup", ctx).output)
    assert to_text(run_line("startup --folder", ctx).output).strip().endswith("startup")
    with pytest.raises(PipelineError, match="no startup script named nope"):
        run_line("startup run nope", ctx)
    ctx.allow_stateful = False
    with pytest.raises(PipelineError):
        run_line("startup run", ctx)  # never over MCP


def test_the_cli_runs_them_unless_told_not_to(home, started):
    import json

    from shellcraft.cli import main

    main([])
    assert started.startup_runs == 0  # no scripts: nothing to run
    write(home, "10-x.py", "pass\n")
    main([])
    main(["--no-startup"])
    assert started.startup_runs == 1
    (home / "config.json").write_text(json.dumps({"settings": {"startup": False}}))
    main([])
    assert started.startup_runs == 1  # settings startup off


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


def test_cli_sources_the_rc_file_after_the_scripts(home, started):
    from shellcraft.cli import main

    (home / "rc").write_text("echo hi\n")
    calls = []
    import shellcraft.shell

    shellcraft.shell.Shell.run_rc = lambda self: calls.append("rc")  # the stand-in shell from `started`
    main([])
    main(["--no-startup"])
    assert calls == ["rc"]


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
