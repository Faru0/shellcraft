import json
import shutil

import pytest

from core import settings
from core.cli import main
from core.context import to_text
from core.pipeline import PipelineError, run_line

needs_tr = pytest.mark.skipif(shutil.which("tr") is None, reason="needs the tr executable")


def test_defaults(ctx):
    assert settings.get(ctx.config, "system_commands") is False
    assert settings.get(ctx.config, "pager") is True
    assert ctx.allow_system is False


@needs_tr
def test_os_commands_off_gives_hint(ctx):
    with pytest.raises(PipelineError, match="OS commands are off"):
        run_line("echo a | tr a b", ctx)


def test_unknown_command_without_hint(ctx):
    with pytest.raises(PipelineError) as info:
        run_line("definitely-not-a-program-xyz", ctx)
    assert "OS commands are off" not in info.value.message


@needs_tr
def test_turn_on_applies_and_persists(ctx, tmp_path):
    run_line("settings system_commands on", ctx)
    assert ctx.allow_system is True
    assert run_line("echo a | tr a b", ctx).output == "b\n"
    saved = json.loads((tmp_path / ".home" / "config.json").read_text())
    assert saved["settings"]["system_commands"] is True


def test_toggle_reset_and_errors(ctx):
    run_line("settings pager toggle", ctx)
    assert settings.get(ctx.config, "pager") is False
    run_line("settings reset pager", ctx)
    assert settings.get(ctx.config, "pager") is True
    with pytest.raises(PipelineError, match="unknown setting"):
        run_line("settings nope on", ctx)
    with pytest.raises(PipelineError, match="expected on, off or toggle"):
        run_line("settings pager maybe", ctx)


def test_settings_table_lists_all(ctx):
    text = to_text(run_line("settings", ctx).output)
    for key in settings.SETTINGS:
        assert key in text


@needs_tr
def test_cli_override_and_saved_setting(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    assert main(["-c", "echo a | tr a b"]) == 1
    assert main(["--allow-system", "-c", "echo a | tr a b"]) == 0
    assert capsys.readouterr().out.endswith("b\n")
    (tmp_path / "config.json").write_text(json.dumps({"settings": {"system_commands": True}}))
    assert main(["-c", "echo a | tr a b"]) == 0
