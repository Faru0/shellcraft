"""Startup scripts (skeleton): order, helpers, the env file hand-back, failures, timeouts, the builtin."""

import io
import os

import pytest
from rich.console import Console

from core import startup
from core.context import ShellContext, to_text
from core.loader import ModuleRegistry
from core.pipeline import PipelineError, run_line
from core.themes import UI, all_themes


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

    from core.cli import main

    main([])
    assert started.startup_runs == 0  # no scripts: nothing to run
    write(home, "10-x.py", "pass\n")
    main([])
    main(["--no-startup"])
    assert started.startup_runs == 1
    (home / "config.json").write_text(json.dumps({"settings": {"startup": False}}))
    main([])
    assert started.startup_runs == 1  # settings startup off
