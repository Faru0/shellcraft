"""The loading line while the shell starts: styles, the estimate, the setting, and a real start."""

import json
import os
import re
import select
import sys
import time
from pathlib import Path

import pytest

from shellcraft import loading, timing
from shellcraft.context import ShellContext, to_text
from shellcraft.pipeline import PipelineError, run_line
from shellcraft.themes import all_themes

ROOT = Path(__file__).resolve().parents[1]
THEME = all_themes({})["cyberpunk"]
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def plain(text):
    return ANSI.sub("", text)


def test_the_style_comes_from_the_config():
    assert loading.style({}) == "bar"
    assert loading.style({"loader": "spinner"}) == "spinner"
    assert loading.style({"loader": "rainbow"}) == "bar"


def test_the_bar_fills_against_the_last_start_and_never_reaches_100():
    loader = loading.Loader("bar", THEME, expected=2.0)
    assert plain(loader.line(0, elapsed=0.0)).startswith("  ╸")
    half = plain(loader.line(0, elapsed=1.0))
    assert " 48%" in half and "loading prompt_toolkit…" in half and half.count("━") == 29
    late = plain(loader.line(0, elapsed=60.0))
    assert " 98%" in late or " 99%" in late
    loader.step("prompt_toolkit imported")
    assert "loading the shell's commands…" in plain(loader.line(0, elapsed=1.0))
    loader.step("something else")  # an unknown mark keeps the step
    assert "loading the shell's commands…" in plain(loader.line(0, elapsed=1.0))


def test_the_spinner_turns():
    loader = loading.Loader("spinner", THEME, expected=2.0)
    assert plain(loader.line(0)) == "  ⠋ loading ShellCraft…"
    assert plain(loader.line(1)) == "  ⠙ loading ShellCraft…"


def test_the_line_is_cut_at_the_terminal_width():
    loader = loading.Loader("bar", THEME, expected=2.0)
    loader.width = 20
    assert len(plain(loader.line(0, elapsed=1.0))) == 20


def test_the_estimate_comes_from_the_saved_report():
    report = ("startup phase                     own    total\n"
              "Python start (before ShellCraft)   20 ms    20 ms\n"
              "config and theme                   12 ms    56 ms\n"
              "banner                              3 ms    59 ms\n"
              "prompt_toolkit + shell imported  2441 ms  2500 ms\n")
    assert loading.expected_seconds(report) == pytest.approx(2.441)
    assert loading.expected_seconds("") == loading.DEFAULT_EXPECTED
    no_banner = report.replace("banner                              3 ms    59 ms\n", "")
    assert loading.expected_seconds(no_banner) == pytest.approx(2.444)


def test_it_draws_nothing_without_a_terminal(monkeypatch):
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False, raising=False)
    loading.start("bar", THEME)
    assert loading._active is None
    loading.stop()  # harmless


def test_marks_reach_the_listener():
    seen = []
    timing.on_mark(seen.append)
    try:
        timing.mark("prompt_toolkit imported")
    finally:
        timing.on_mark(None)
    timing.mark("after")
    assert seen == ["prompt_toolkit imported"]


def test_a_fast_start_never_draws_it():
    class Out:
        def __init__(self):
            self.text = ""

        def write(self, text):
            self.text += text

        def flush(self):
            pass
    out = Out()
    loader = loading.Loader("bar", THEME, expected=2.0, out=out)
    loader.start()
    loader.finish()  # before DELAY
    assert out.text == ""


# ── the setting ──────────────────────────────────────────────────────────────

def test_settings_loader(registry, tmp_path, monkeypatch):
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path))
    ctx = ShellContext(registry=registry, config={})
    listing = to_text(run_line("settings loader", ctx).output)
    assert "bar\t*\t" in listing and "spinner\t\t" in listing  # piped: the plain form, * marks the one in use
    assert "set to spinner" in to_text(run_line("settings loader spinner", ctx).output)
    assert json.loads((tmp_path / "config.json").read_text())["loader"] == "spinner"
    assert "loader" in to_text(run_line("settings", ctx).output)
    assert "set to bar" in to_text(run_line("settings reset loader", ctx).output)
    assert "loader" not in json.loads((tmp_path / "config.json").read_text())
    with pytest.raises(PipelineError, match=r"usage: settings loader \[bar\|spinner\]"):
        run_line("settings loader rainbow", ctx)


def test_settings_loader_completes(registry):
    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document

    from shellcraft.completer import ShellCompleter

    completer = ShellCompleter(ShellContext(registry=registry, config={}))
    line = "settings loader "
    assert [c.text for c in completer.get_completions(Document(line, len(line)), CompleteEvent())] == ["bar", "spinner"]


# ── a real start, slowed down so the line shows ──────────────────────────────

CHILD = r'''
import sys, time, importlib.abc
class Slow(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name == "shellcraft.shell":
            time.sleep(1.0)
        return None
sys.meta_path.insert(0, Slow())
from shellcraft import cli
sys.exit(cli.main(sys.argv[1:]))
'''


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX pty")
@pytest.mark.filterwarnings("ignore:This process .* is multi-threaded, use of forkpty")
@pytest.mark.parametrize("style, args, drawn", [("bar", [], "%"), ("spinner", [], "loading ShellCraft…"),
                                                ("bar", ["--lite"], "%")])
def test_a_slow_start_shows_the_line_then_erases_it(tmp_path, style, args, drawn):
    import pty

    (tmp_path / "config.json").write_text(json.dumps({"settings": {"clear_on_start": False}, "loader": style}))
    env = dict(os.environ, SHELLCRAFT_HOME=str(tmp_path), TERM="xterm-256color", PYTHONPATH=str(ROOT))
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(tmp_path)
        os.execve(sys.executable, [sys.executable, "-c", CHILD, *args], env)
    out = b""
    try:
        end = time.time() + 15
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
                if "❯".encode() in ANSI.sub("", out.decode("utf-8", "replace")).encode():
                    break
    finally:
        os.kill(pid, 9)
        os.waitpid(pid, 0)
        os.close(fd)
    raw = out.decode("utf-8", "replace")
    frames = [plain(f) for f in raw.split("\r") if drawn in plain(f)]
    assert len(frames) >= 3  # it animates
    last = raw.rfind(drawn)
    erase = raw.find("\x1b[2K", last)
    assert erase != -1 and raw.find("❯", last) > erase  # erased before the prompt
