import json

import pytest

from core import settings
from core.cli import main
from core.context import ShellContext, to_text
from core.pipeline import PipelineError, run_line
from tests.conftest import OS_UPPER



def test_defaults(ctx):
    assert settings.get(ctx.config, "system_commands") is False
    assert settings.get(ctx.config, "pager") is True
    assert ctx.allow_system is False


def test_os_commands_off_gives_hint(ctx):
    with pytest.raises(PipelineError, match="OS commands are off"):
        run_line(f"echo a | {OS_UPPER}", ctx)


def test_unknown_command_without_hint(ctx):
    with pytest.raises(PipelineError) as info:
        run_line("definitely-not-a-program-xyz", ctx)
    assert "OS commands are off" not in info.value.message


def test_turn_on_applies_and_persists(ctx, tmp_path):
    run_line("settings system_commands on", ctx)
    assert ctx.allow_system is True
    assert run_line(f"echo a | {OS_UPPER}", ctx).output.strip() == "A"
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


def test_cli_override_and_saved_setting(tmp_path, monkeypatch, started):
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    assert main([]) == 0
    assert main(["--allow-system"]) == 0
    (tmp_path / "config.json").write_text(json.dumps({"settings": {"system_commands": True}}))
    assert main([]) == 0
    assert [ctx.allow_system for ctx in started] == [False, True, True]
    # And the shell's context really runs OS programs then.
    with pytest.raises(PipelineError):
        run_line(f"echo a | {OS_UPPER}", ShellContext(registry=started[0].registry))
    assert to_text(run_line(f"echo a | {OS_UPPER}", started[1]).output).strip() == "A"


def test_there_is_no_one_shot_mode(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["-c", "echo hi"])
    assert exc.value.code == 2  # argparse: unrecognized arguments
    assert "unrecognized arguments: -c" in capsys.readouterr().err


@pytest.mark.parametrize("flag", [["--mcp"], ["--mcp-http", "127.0.0.1:8765"]])
def test_mcp_without_the_mcp_package_explains_instead_of_crashing(tmp_path, monkeypatch, capsys, flag):
    import importlib.util

    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path))
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: None if name == "mcp_types" else real(name, *a))
    assert main(flag) == 1
    err = capsys.readouterr().err
    assert "needs the 'mcp' package" in err and "missing: mcp_types" in err
    assert "-m pip install -r requirements.txt" in err
