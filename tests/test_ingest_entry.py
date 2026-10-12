"""The ingest wizard's two new doors: `shellcraft ingest` (CLI) and the `ingest` builtin."""

import io
from pathlib import Path

import pytest
from rich.console import Console

from core.cli import main
from core.pipeline import PipelineError, run_line
from core.themes import UI, all_themes

FILTER = Path(__file__).resolve().parents[1] / "modules" / "filter.py"


def test_shellcraft_ingest_check_runs_the_checker(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path))
    assert main(["ingest", "check", str(FILTER)]) == 0
    assert "1/1 module(s) passed" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["ingest", "--help"])
    assert "shellcraft ingest" in capsys.readouterr().out


def test_ingest_builtin_checks_modules_in_the_shell(tmp_path, monkeypatch, capsys):
    from core.context import ShellContext
    from core.loader import ModuleRegistry

    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / "home"))
    modules = tmp_path / "modules"  # the shell's own folder, not the bundled one
    modules.mkdir()
    ctx = ShellContext(registry=ModuleRegistry(modules))
    with pytest.raises(PipelineError, match="interactive"):
        run_line(f"ingest check {FILTER}", ctx)  # not interactive: MCP never gets the wizard
    ctx.interactive = True
    ctx.ui = UI(all_themes({})["nord"], Console(file=io.StringIO()))
    result = run_line(f"ingest check {FILTER}", ctx)
    assert result.status == 0 and "1/1 module(s) passed" in capsys.readouterr().out
    (modules / "broken.py").write_text("def run(args, stdin):\n    return 1\n")
    with pytest.raises(PipelineError, match="status 1"):
        run_line("ingest check --all", ctx)  # --all means this shell's modules folder
    assert "broken" in capsys.readouterr().out
