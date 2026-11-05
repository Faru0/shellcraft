"""Questions from scripts (`input()`, `read -p`) are asked on the shell's own prompt session
(Shell.ask_line, ctx.ask_line), never a second prompt_toolkit application, with completion,
suggestions, the shell's keys and history turned off while answering."""

import pytest
from prompt_toolkit.application.current import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from shellcraft import ask, shell, startup
from shellcraft.context import ShellContext
from shellcraft.loader import ModuleRegistry
from shellcraft.pipeline import run_line
from shellcraft.themes import UI, all_themes

WATCHED = ("completer", "auto_suggest", "complete_while_typing", "lexer", "rprompt", "bottom_toolbar",
           "mouse_support", "key_bindings", "is_password")


@pytest.fixture
def repl(monkeypatch, tmp_path):
    """A real Shell whose prompt reads from a pipe: `pipe.send_text(...)` types the keys."""
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(shell, "create_prompt_toolkit_console", lambda cleanup: None)

    def second_app(*args, **kwargs):
        raise AssertionError("a second prompt_toolkit application was started")

    monkeypatch.setattr("prompt_toolkit.prompt", second_app)
    console = Console(record=True, width=120, force_terminal=False, color_system=None)
    ui = UI(all_themes({})["nord"], console)
    ctx = ShellContext(registry=ModuleRegistry(tmp_path), ui=ui, config={})
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        with shell.Shell(ctx) as sh:
            yield sh, ctx, pipe


def _settings(sh):
    return {name: getattr(sh.session, name) for name in WATCHED}


def test_an_answer_comes_from_the_shells_own_session(repl):
    sh, ctx, pipe = repl
    assert ctx.ask_line == sh.ask_line
    before = _settings(sh)
    pipe.send_text("Ada\r")
    assert ask.ask(ctx, "Name? ") == "Ada"
    assert _settings(sh) == before  # completion, keys and the rest are back for the next command


def test_no_completion_and_no_history_while_answering(repl):
    sh, ctx, pipe = repl
    sh.session.history.append_string("echo an old command")
    pipe.send_text("ec\t\r")
    assert ask.ask(ctx, "Word? ") == "ec"  # Tab completed nothing
    pipe.send_text("\x1b[Ax\r")
    assert ask.ask(ctx, "Word? ") == "x"  # ↑ brought back no old command
    assert "x" not in sh.session.history.get_strings()  # and the answer isn't saved


def test_a_hidden_answer_and_ctrl_c(repl):
    sh, ctx, pipe = repl
    seen = {}
    real = sh.session.prompt

    def prompt(message, **kwargs):
        seen["password"] = sh.session.is_password
        seen["completer"] = sh.session.completer
        return real(message, **kwargs)

    sh.session.prompt = prompt
    before = _settings(sh)
    pipe.send_text("s3cret\r")
    assert ask.ask(ctx, "Key: ", hidden=True) == "s3cret"
    assert seen == {"password": True, "completer": None}
    pipe.send_text("\x03")
    with pytest.raises(ask.AskError) as caught:
        ask.ask(ctx, "Name? ")
    assert caught.value.status == 130
    assert _settings(sh) == before  # put back after an interrupted answer too


def test_startup_files_ask_on_the_shells_session(repl, tmp_path):
    sh, ctx, pipe = repl
    folder = tmp_path / "startup"
    folder.mkdir()
    (folder / "10-ask.sc").write_text('name = input("Your name? ")\n'
                                      'read -p "Color? " color\n'
                                      'for -py i in 1..1:\n'
                                      '    n = int(input(f"Number {i}? "))\n'
                                      '    total = n + 1\n')
    pipe.send_text("Ada\rred\r41\r")
    startup.run_folder(ctx, folder)
    assert (ctx.variables["name"], ctx.variables["color"], ctx.variables["total"]) == ("Ada", "red", "42")
    assert run_line("echo $name", ctx) is not None


def test_the_settings_key_prompt_asks_the_same_way(repl, monkeypatch):
    from types import SimpleNamespace

    from shellcraft import builtins

    sh, ctx, pipe = repl
    asked = []
    monkeypatch.setattr(ask, "ask", lambda c, prompt, hidden=False: asked.append((prompt, hidden)) or "k3y")
    entry = SimpleNamespace(name="TEST_API_KEY", label="Test key")
    assert builtins._ask_secret(ctx, entry) == "k3y"
    assert asked == [("Test key (TEST_API_KEY): ", True)]


def test_ask_line_is_gone_after_the_shell_closes(monkeypatch, tmp_path):
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(shell, "create_prompt_toolkit_console", lambda cleanup: None)
    ui = UI(all_themes({})["nord"], Console(record=True, width=120, force_terminal=False, color_system=None))
    ctx = ShellContext(registry=ModuleRegistry(tmp_path), ui=ui, config={})
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        with shell.Shell(ctx):
            assert ctx.ask_line is not None
    assert ctx.ask_line is None
