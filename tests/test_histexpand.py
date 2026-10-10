"""`!!` and `!$`, and how the shell uses them."""

import pytest

from core.histexpand import expand, last_word
from core.parser import ParseError


@pytest.mark.parametrize("line, previous, expected", [
    ("!!", "ls -l src", "ls -l src"),
    ("sudo !!", "ls", "sudo ls"),
    ("!! | wc -l", "cat f", "cat f | wc -l"),
    ("cat !$", "ls -l src", "cat src"),
    ("cat !$", "echo 'my dir'", "cat 'my dir'"),
    ("cat !$", "ls > out.txt", "cat out.txt"),
    ("cat !$", 'echo "a b"|wc', "cat wc"),
    ('echo "!!"', "x", 'echo "x"'),  # double quotes: expanded, as in bash
])
def test_expansions(line, previous, expected):
    assert expand(line, previous) == (expected, True)


@pytest.mark.parametrize("line", ["echo '!!'", "echo hi!", "if -py (a != b) { echo }", "echo ! x", "echo \"it's\" !x"])
def test_left_alone(line):
    assert expand(line, "prev") == (line, False)


def test_event_not_found():
    with pytest.raises(ParseError) as info:
        expand("echo !$", None)
    assert "event not found" in info.value.message and info.value.pos == 5


def test_last_word():
    assert last_word("for -py x in a { echo $x }") == "}"
    assert last_word("   ") is None


# ── in the shell ─────────────────────────────────────────────────────────────

class _ScriptedSession:
    def __init__(self, lines):
        self.lines = list(lines)
        self.prompts = []

    def prompt(self, message):
        self.prompts.append(message)
        if not self.lines:
            raise EOFError
        return self.lines.pop(0)


@pytest.fixture
def repl(monkeypatch, tmp_path):
    from rich.console import Console

    from core import shell
    from core.context import ShellContext
    from core.loader import ModuleRegistry
    from core.themes import UI, all_themes

    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(shell, "create_prompt_toolkit_console", lambda cleanup: None)
    console = Console(record=True, width=120, force_terminal=False, color_system=None)
    ui = UI(all_themes({})["nord"], console)

    def run(*lines):
        ctx = ShellContext(registry=ModuleRegistry(tmp_path), ui=ui, config={})
        with shell.Shell(ctx) as sh:
            sh.session = _ScriptedSession(lines)
            sh.loop()
            return sh, console.export_text()
    return run


def test_shell_expands_prints_and_saves_the_expanded_line(repl):
    sh, text = repl("echo one two", "echo !$ three", "!!")
    assert "echo two three\ntwo three\n" in text
    assert sh.ctx.history.get_strings()[-2:] == ["echo one two", "echo two three"]  # !! repeated it


def test_shell_event_not_found(repl):
    sh, text = repl("!!")
    assert "event not found" in text and sh.ctx.last_status == 2


def test_shell_joins_continuation_lines_into_one_history_entry(repl):
    sh, text = repl("for -py x in a b {", "if -py (x == 'a') { echo A }", "else { echo B }", "}")
    assert "A\nB\n" in text
    assert sh.ctx.history.get_strings()[-1] == "for -py x in a b { if -py (x == 'a') { echo A } else { echo B } }"


def test_shell_reports_an_unfinished_script_at_end_of_input(repl):
    sh, text = repl("for -py x in a {", "echo $x")
    assert "missing '}' (end of input)" in text and sh.ctx.last_status == 2


def test_prompt_shows_the_exit_status(repl):
    sh, _ = repl("nosuch")
    assert ("class:failed", "[✗ 127]") in list(sh._prompt())
    sh, _ = repl("echo fine")
    assert not any(style == "class:failed" for style, _ in sh._prompt())
