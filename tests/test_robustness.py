"""Crashes and wrong output found by the fourth assessment (2026-10-11), and their fixes."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from shellcraft import config as config_mod
from shellcraft import settings
from shellcraft.context import ShellContext, to_text
from shellcraft.parser import ParseError, parse
from shellcraft.pipeline import PipelineError, run_line
from tests.conftest import ROOT

# ── the launchers ────────────────────────────────────────────────────────────

def test_main_py_version_prints_only_the_version():
    """`main.py` used to clear the screen before parsing arguments, in every mode (MCP included)."""
    out = subprocess.run([sys.executable, str(ROOT / "main.py"), "--version"], capture_output=True,
                         text=True, cwd=ROOT, timeout=60)
    from shellcraft import __version__

    assert out.stdout == f"ShellCraft {__version__}\n"
    assert "\x1b" not in out.stdout and out.returncode == 0


def test_clear_and_banner_c_clear_screen_and_scrollback(ctx, monkeypatch):
    """`clear` used Rich's console.clear(): ESC[2J only, which Windows Terminal scrolls away instead
    of clearing, and nothing at all when Rich did not see a terminal."""
    import io

    from rich.console import Console

    from shellcraft.output import CLEAR_SCREEN
    from shellcraft.themes import UI, all_themes

    screen = io.StringIO()  # not a terminal, as Rich sees it: the clear must still be written
    ctx.ui = UI(all_themes({})["nord"], Console(file=screen))
    ctx.interactive = True
    assert run_line("clear", ctx).status == 0
    assert screen.getvalue() == CLEAR_SCREEN == "\x1b[H\x1b[2J\x1b[3J"
    screen.truncate(0)
    screen.seek(0)
    run_line("banner -c", ctx)
    assert screen.getvalue() == CLEAR_SCREEN


def test_cli_clears_the_screen_only_in_the_interactive_shell(tmp_path, monkeypatch, started):
    from shellcraft import cli

    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path))
    cleared: list = []
    import shellcraft.themes as themes

    class FakeFile:
        def write(self, text):
            cleared.append(text)

        def flush(self):
            pass

    class FakeConsole:
        is_terminal = True
        width, height = 100, 30
        file = FakeFile()

        def push_theme(self, theme):
            pass

    real_ui = themes.UI
    monkeypatch.setattr(themes, "UI", lambda theme, console: real_ui(theme, FakeConsole()))
    cli.main(["--no-banner", "--modules", str(tmp_path)])
    assert cleared == ["\x1b[H\x1b[2J\x1b[3J"]  # home, clear the screen and the scrollback
    (tmp_path / "config.json").write_text(json.dumps({"settings": {"clear_on_start": False}}))
    cleared.clear()
    cli.main(["--no-banner", "--modules", str(tmp_path)])
    assert cleared == []


# ── redirects and paths ──────────────────────────────────────────────────────

def test_redirect_to_an_unknown_user_does_not_crash(ctx, tmp_path):
    run_line("echo hi > ~nosuchuser", ctx)  # used to raise RuntimeError out of the shell loop
    assert (tmp_path / "~nosuchuser").read_text() == "hi\n"
    assert to_text(run_line("cat ~nosuchuser", ctx).output) == "hi\n"


def test_quoted_tilde_in_a_redirect_stays_literal(ctx, tmp_path):
    with pytest.raises(PipelineError, match="cannot write file"):
        run_line("echo hi > '~/f'", ctx)  # no ~ directory here: the shell must not write to $HOME
    (tmp_path / "~").mkdir()
    run_line("echo hi > '~/f'", ctx)
    assert (tmp_path / "~" / "f").read_text() == "hi\n"


def test_redirect_does_not_create_missing_directories(ctx, tmp_path):
    with pytest.raises(PipelineError, match="cannot write file"):
        run_line("echo a > typo/dir/f.txt", ctx)
    assert not (tmp_path / "typo").exists()


def test_tilde_user_forms_in_the_tokenizer(monkeypatch):
    from shellcraft.parser import expand_tilde

    assert expand_tilde("~nosuchuser_xyz/f") == "~nosuchuser_xyz/f"
    assert expand_tilde("~") == os.path.expanduser("~")
    assert expand_tilde("~/x") == os.path.expanduser("~/x")
    assert expand_tilde("~=") == "~="


# ── operators ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("line", ["echo hi & echo there", "rm -f a & ls", "sleep 1 &"])
def test_ampersand_is_rejected_not_passed_on(line):
    with pytest.raises(ParseError) as info:
        parse(line)
    assert info.value.message.startswith("'&' is not supported")


def test_ampersand_inside_a_word_is_text():
    assert parse("fetch 'https://x/?a=1&b=2'").segments[0].args == ["https://x/?a=1&b=2"]
    assert parse("fetch https://x/?a=1&b=2").segments[0].args == ["https://x/?a=1&b=2"]


# ── commands ─────────────────────────────────────────────────────────────────

def test_cp_refuses_to_copy_a_directory_into_itself(ctx, tmp_path):
    (tmp_path / "d" / "sub").mkdir(parents=True)
    (tmp_path / "d" / "f").write_text("x")
    for line in ("cp -r d d/sub", "cp -r d d"):
        with pytest.raises(PipelineError, match="into itself"):
            run_line(line, ctx)
    assert sorted(p.name for p in (tmp_path / "d").iterdir()) == ["f", "sub"]


def test_grep_m_zero_prints_nothing(ctx):
    assert to_text(run_line("echo -e 'x\\ny' | grep -m 0 x", ctx).output) == ""
    assert to_text(run_line("echo -e 'x\\nx' | grep -m 1 x", ctx).output) == "x\n"


def test_sort_u_k_dedupes_by_key(ctx):
    assert to_text(run_line("echo -e 'a 1\\nb 1\\na 2' | sort -u -k 2", ctx).output) == "a 1\na 2\n"
    assert to_text(run_line("echo -e 'b\\na\\nb' | sort -u", ctx).output) == "a\nb\n"


def test_wc_c_counts_bytes_and_m_characters(ctx):
    assert to_text(run_line("echo é | wc -c", ctx).output) == "3\n"
    assert to_text(run_line("echo é | wc -m", ctx).output) == "2\n"


def test_pwd_in_a_deleted_directory_fails_cleanly(ctx, tmp_path):
    from shellcraft import shell

    gone = tmp_path / "gone"
    gone.mkdir()
    os.chdir(gone)
    try:
        gone.rmdir()
        assert shell._pretty_cwd() == "(deleted)"
        with pytest.raises(PipelineError, match="no longer exists"):
            run_line("pwd", ctx)
        run_line("cd ~", ctx)
    finally:
        os.chdir(tmp_path)


def test_unknown_switch_error_shows_usage_and_a_suggestion(ctx):
    with pytest.raises(PipelineError) as info:
        run_line("grep -ignore x", ctx)
    assert "did you mean -i, --ignore-case" in info.value.message
    assert "usage: grep" in info.value.message
    with pytest.raises(PipelineError) as info:
        run_line("ls -z", ctx)
    assert "did you mean" not in info.value.message and "usage: ls" in info.value.message


# ── help ─────────────────────────────────────────────────────────────────────

def test_help_name_shows_one_command(ctx):
    text = to_text(run_line("help grep", ctx).output)
    assert text.startswith("grep: Print lines matching a pattern\nusage: grep ")
    assert "cat:" not in text
    with pytest.raises(PipelineError, match="did you mean 'grep'"):
        run_line("help grpe", ctx)


def test_help_k_searches_summaries(ctx):
    text = to_text(run_line("help -k directory", ctx).output)
    assert "ls" in text and "cd" in text and "grep" not in text
    with pytest.raises(PipelineError, match="nothing matches"):
        run_line("help -k zzzz", ctx)


def test_help_hides_keywords_and_brackets_but_man_still_works(ctx):
    text = to_text(run_line("help", ctx).output)
    names = [line.split()[0] for line in text.splitlines() if line.strip()]
    assert "[" not in names and "[[" not in names and "for" not in names and "if" not in names
    assert "grep" in names and "fetch" in names
    assert "[ EXPRESSION ]" in to_text(run_line("man [", ctx).output)
    assert "for NAME in" in to_text(run_line("man for", ctx).output)


# ── config.json ──────────────────────────────────────────────────────────────

def test_malformed_config_sections_are_dropped_with_a_warning(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text(json.dumps(
        {"env": [1], "settings": "x", "themes": [], "aliases": 3, "disabled_modules": "fetch", "theme": 5}))
    config = config_mod.load_config()
    settings.export_env(config)  # used to raise AttributeError
    assert config == {"theme": "cyberpunk", "themes": {}}
    err = capsys.readouterr().err
    for key in ("env", "settings", "themes", "aliases", "disabled_modules", "theme"):
        assert f"ignoring '{key}'" in err


def test_malformed_config_does_not_crash_startup(tmp_path, monkeypatch, started):
    from shellcraft import cli

    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text(json.dumps({"env": [1], "settings": [1]}))
    assert cli.main(["--no-banner", "--modules", str(tmp_path)]) == 0


def test_two_sessions_do_not_overwrite_each_other(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path))
    first = config_mod.load_config()
    first["aliases"] = {"ll": "ls -l"}
    config_mod.save_config(first)  # session A saves an alias

    second = config_mod.load_config()  # session B started after that
    second["theme"] = "nord"
    config_mod.save_config(second)

    first["aliases"]["la"] = "ls -a"  # session A, still running with its older picture of the file
    config_mod.save_config(first)
    on_disk = json.loads((tmp_path / "config.json").read_text())
    assert on_disk["theme"] == "nord"  # B's change survived A's save
    assert on_disk["aliases"] == {"ll": "ls -l", "la": "ls -a"}

    del first["aliases"]["ll"]  # a removal is a change too
    config_mod.save_config(first)
    assert json.loads((tmp_path / "config.json").read_text())["aliases"] == {"la": "ls -a"}
    assert not list(tmp_path.glob(".config-*"))  # the temp file is gone


def test_config_is_written_atomically(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path))
    config = config_mod.load_config()
    config["env"] = {"KEY": "secret"}
    config_mod.save_config(config)
    path = tmp_path / "config.json"
    if os.name != "nt":
        assert oct(path.stat().st_mode & 0o777) == "0o600"
    calls = []

    def broken_replace(src, dst):
        calls.append(src)
        raise OSError("disk full")

    monkeypatch.setattr(config_mod.os, "replace", broken_replace)
    config["theme"] = "nord"
    with pytest.raises(OSError):
        config_mod.save_config(config)
    assert json.loads(path.read_text())["env"] == {"KEY": "secret"}  # the old file is intact
    assert not Path(calls[0]).exists()


# ── history ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("line, stored", [
    (r"\settings KEY abc", r"\settings KEY ••••"),
    ("echo x ; settings KEY abc", "echo x ; settings KEY ••••"),
    ("settings KEY abc && echo ok", "settings KEY •••• && echo ok"),
    ("for x in a; do settings KEY abc; done", "for x in a; do settings KEY ••••; done"),
    ("s KEY abc", "s KEY ••••"),  # an alias of settings
    ("echo KEY abc", "echo KEY abc"),
])
def test_history_redaction_covers_every_statement(line, stored):
    assert settings.redact_line(line, {"s": "settings"}) == stored


# ── the shell loop ───────────────────────────────────────────────────────────

def test_shell_loop_survives_an_unexpected_exception(monkeypatch, tmp_path):
    from rich.console import Console

    from shellcraft import shell
    from shellcraft.loader import ModuleRegistry
    from shellcraft.themes import UI, all_themes
    from tests.test_histexpand import _ScriptedSession

    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(shell, "create_prompt_toolkit_console", lambda cleanup: None)
    console = Console(record=True, width=120, force_terminal=False, color_system=None)
    ui = UI(all_themes({})["nord"], console)

    def boom(line, ctx):
        raise RuntimeError("Could not determine home directory")

    monkeypatch.setattr(shell, "run_line", boom)
    ctx = ShellContext(registry=ModuleRegistry(tmp_path), ui=ui, config={})
    with shell.Shell(ctx) as sh:
        sh.session = _ScriptedSession(["echo hi"])
        assert sh.loop() == 0  # reached Ctrl-D instead of dying
    assert "RuntimeError: Could not determine home directory" in console.export_text()
    assert ctx.last_status == 1


def test_help_groups_grids_and_all(ctx):
    import io

    from rich.console import Console

    from shellcraft.themes import UI, all_themes

    ctx.ui = UI(all_themes({})["nord"], Console(file=io.StringIO(), width=160, record=True))
    ctx.ui.console.print(run_line("help", ctx).output.renderable.value)
    screen = ctx.ui.console.export_text()
    lines = screen.splitlines()
    assert max(len(line.rstrip()) for line in lines) <= 100  # capped, however wide the terminal
    for group in ("ShellCraft", "Shell", "Files", "Text", "Scripting", "System", "Modules"):
        assert any(line.startswith(f" {group} ") and "──" in line for line in lines), group
    assert "Record the session to a file" in screen  # ShellCraft's own: described
    assert "Print lines matching a pattern" not in screen  # grep: a name in the Text grid
    assert "help -a" in screen and "keywords" in screen
    ctx.ui.console.file = io.StringIO()
    ctx.ui.console.print(run_line("help -a", ctx).output.renderable.value)
    every = ctx.ui.console.export_text()
    assert "Print lines matching a pattern" in every
    with pytest.raises(PipelineError, match="usage: help"):
        run_line("help -x", ctx)


def test_function_helpers_are_off_help_but_on_the_function_page(ctx):
    listed = [line.split()[0] for line in to_text(run_line("help", ctx).output).splitlines() if line.strip()]
    assert not {"local", "shift", "declare"} & set(listed) and "unset" in listed
    page = to_text(run_line("man -p function", ctx).output)
    assert "## local" in page and "## shift" in page and "## declare" in page
    assert page.index("## local") < page.index("## shift") < page.index("## declare")
    assert "declare -f NAME..." in page and "each a" in page and "man function" not in page
    assert "local" in to_text(run_line("man -p local", ctx).output)  # its own page still works
    assert run_line("f() { local x=1; echo $x; } ; f", ctx).status == 0  # and the command
