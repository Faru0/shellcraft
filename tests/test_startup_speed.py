"""Startup speed: what loads before the prompt, the early banner, the timing report."""

import json
import os
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest

from shellcraft import settings, timing

ROOT = Path(__file__).resolve().parents[1]
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX pty")

# Imported only when a command needs them, never before the first prompt.
HEAVY = ("markdown_it", "rich.markdown", "pygments", "mcp", "requests", "keyring", "pyte", "winpty")
MODULE_CEILING = 500  # sys.modules after the interactive startup imports (460 in October 2026)


def startup_imports():
    code = ("import shellcraft.cli, shellcraft.shell, shellcraft.commands, sys; "
            "print('\\n'.join(sorted(sys.modules)))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, check=True)
    return set(out.stdout.split())


def test_heavy_packages_are_not_imported_before_the_prompt():
    loaded = startup_imports()
    assert not [m for m in loaded if m.split(".")[0] in HEAVY or m in HEAVY]
    assert len(loaded) < MODULE_CEILING, len(loaded)  # a new heavy import shows up here


def test_the_banner_needs_no_prompt_toolkit():
    code = "import shellcraft.banner, shellcraft.themes, shellcraft.context, sys; print(any(m.startswith('prompt_toolkit') for m in sys.modules))"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, check=True)
    assert out.stdout.strip() == "False"


def test_the_prompt_toolkit_style_is_built_when_first_asked_for():
    from shellcraft.themes import UI, all_themes

    ui = UI(all_themes({})["nord"])
    assert ui._pt_style is None
    style = ui.pt_style
    assert style is ui.pt_style  # built once
    ui.set_theme(all_themes({})["matrix"])
    assert ui._pt_style is None and ui.pt_style is not style


def test_man_and_hotkeys_still_render_markdown(tmp_path):
    from rich.console import Console
    from rich.markdown import Markdown

    from shellcraft.context import ShellContext
    from shellcraft.loader import ModuleRegistry
    from shellcraft.pipeline import run_line
    from shellcraft.themes import UI, all_themes

    ctx = ShellContext(registry=ModuleRegistry(tmp_path), ui=UI(all_themes({})["nord"], Console()), config={})
    assert isinstance(run_line("man grep", ctx).output.value, Markdown)
    assert isinstance(run_line("hotkeys", ctx).output.renderable.value, Markdown)


def test_the_timing_report_lists_every_phase():
    timing.mark("phase one")
    timing.mark("phase two")
    report = timing.report()
    assert report.splitlines()[0].startswith("startup phase")
    assert "Python start (before ShellCraft)" in report and "phase one" in report and "phase two" in report
    if sys.platform.startswith("linux"):
        age = timing.process_age()
        assert age is not None and 0 <= age < 600


def test_stored_keys_are_exported_at_once_and_the_environment_wins(monkeypatch):
    monkeypatch.setenv("SHELL_SET_KEY", "from-my-shell")
    monkeypatch.delenv("PLAIN_KEY_X", raising=False)
    monkeypatch.delenv("OLD_KEY_Y", raising=False)
    config = {"env": {"PLAIN_KEY_X": "plain-value", "SHELL_SET_KEY": "stored", "OLD_KEY_Y": "@keyring"}}
    settings.export_env(config)
    assert os.environ["PLAIN_KEY_X"] == "plain-value"  # stored keys reach the environment
    assert os.environ["SHELL_SET_KEY"] == "from-my-shell"  # a variable from your shell wins
    assert "OLD_KEY_Y" not in os.environ  # a key an older version kept in the OS keyring: skipped quietly
    assert settings.env_status(config, "OLD_KEY_Y")[1].startswith("stored in the OS keyring by an older version")
    os.environ.pop("PLAIN_KEY_X", None)


def test_keyring_is_never_imported(tmp_path):
    code = ("import sys, shellcraft.cli, shellcraft.shell, shellcraft.commands, shellcraft.settings as s; "
            "s.export_env({'env': {'K': '@keyring'}}); print('keyring' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, check=True)
    assert out.stdout.strip() == "False"


@posix_only
@pytest.mark.filterwarnings("ignore:This process .* is multi-threaded, use of forkpty")
def test_the_banner_shows_before_the_prompt_and_timing_prints(tmp_path):
    import pty
    import re

    (tmp_path / "config.json").write_text(json.dumps({"settings": {"clear_on_start": False}}))
    env = dict(os.environ, SHELLCRAFT_HOME=str(tmp_path), TERM="xterm-256color")
    pid, fd = pty.fork()
    if pid == 0:
        os.execve(sys.executable, [sys.executable, str(ROOT / "main.py"), "--timing"], env)
    out = b""
    try:
        end = time.time() + 10
        while time.time() < end:
            ready, _, _ = select.select([fd], [], [], 0.02)
            if ready:
                try:
                    data = os.read(fd, 65536)
                except OSError:
                    break
                out += data
                if b"\x1b[6n" in data:
                    os.write(fd, b"\x1b[1;1R")
                if "❯".encode() in re.sub(rb"\x1b\[[0-9;?]*[A-Za-z]", b"", out):
                    break
    finally:
        os.kill(pid, 9)
        os.waitpid(pid, 0)
        os.close(fd)
    text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", out.decode("utf-8", "replace"))
    assert "welcome, operator" in text and "startup phase" in text
    assert text.index("welcome, operator") < text.index("prompt_toolkit + shell imported")
    for phase in ("modules found", "banner", "shell set up", "first prompt"):
        assert phase in text
    assert "first prompt" in (tmp_path / timing.FILE).read_text()


def test_termsize_asks_the_terminal_not_columns(monkeypatch):
    import os as _os

    from shellcraft import termsize

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("COLUMNS", "80")
    sizes = {1: OSError, 2: _os.terminal_size((150, 40))}

    def fake(fd):
        value = sizes.get(fd, OSError)
        if value is OSError:
            raise OSError
        return value
    monkeypatch.setattr(termsize.os, "get_terminal_size", fake)
    assert termsize.columns() == 150  # stdout isn't a terminal: stderr is asked; COLUMNS is ignored
    sizes.clear()
    assert termsize.columns() is None


@posix_only
@pytest.mark.filterwarnings("ignore:This process .* is multi-threaded, use of forkpty")
def test_the_early_banner_is_as_wide_as_the_terminal(tmp_path):
    import fcntl
    import pty
    import re
    import struct
    import termios

    (tmp_path / "config.json").write_text(json.dumps({"settings": {"clear_on_start": False}}))
    env = dict(os.environ, SHELLCRAFT_HOME=str(tmp_path), TERM="xterm-256color", COLUMNS="60")  # a stale COLUMNS
    pid, fd = pty.fork()
    if pid == 0:
        fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 150, 0, 0))
        os.execve(sys.executable, [sys.executable, str(ROOT / "main.py")], env)
    out = b""
    try:
        end = time.time() + 10
        while time.time() < end and "╮".encode() not in out:
            ready, _, _ = select.select([fd], [], [], 0.02)
            if ready:
                try:
                    out += os.read(fd, 65536)
                except OSError:
                    break
    finally:
        os.kill(pid, 9)
        os.waitpid(pid, 0)
        os.close(fd)
    text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", out.decode("utf-8", "replace"))
    top = next(line for line in text.splitlines() if line.startswith("╭") and line.rstrip().endswith("╮"))
    assert len(top.rstrip()) == 150


def test_a_bad_option_is_one_line_on_stderr_and_exit_2(capsys):
    from shellcraft import cli

    assert cli.main(["--testingoption"]) == 2
    out, err = capsys.readouterr()
    assert out == "" and err.startswith("shellcraft: unrecognized arguments: --testingoption")
    assert err.count("\n") == 1  # no usage block, no traceback, no SystemExit out of main()
