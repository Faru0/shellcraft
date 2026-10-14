"""Asking for values: `name = input("…")` lines, the `read` builtin, and both in an rc file."""

import io

import pytest
from rich.console import Console

from core import ask
from core.context import ShellContext, to_text
from core.loader import ModuleRegistry
from core.pipeline import PipelineError, run_line
from core.themes import UI, all_themes


@pytest.fixture
def ictx(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / "home"))
    ctx = ShellContext(registry=ModuleRegistry(tmp_path / "modules"))
    ctx.interactive = True
    ctx.ui = UI(all_themes({})["nord"], Console(file=io.StringIO()))
    return ctx


@pytest.fixture
def answers(monkeypatch):
    """Answer prompts from a list; records (prompt, hidden)."""
    asked = []
    queue = []

    def fake(prompt, is_password=False):
        asked.append((prompt, is_password))
        answer = queue.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    import prompt_toolkit

    monkeypatch.setattr(prompt_toolkit, "prompt", fake)
    return queue, asked


def test_parse_input_lines():
    assert ask.parse_input_line('name = input("What is your name? ")') == ("name", "What is your name? ")
    assert ask.parse_input_line("x=input('a\\tb')") == ("x", "a\tb")
    assert ask.parse_input_line("x = input()") == ("x", "")
    assert ask.parse_input_line("echo input(1)") is None and ask.parse_input_line("x=1") is None
    with pytest.raises(ask.AskError, match="must be a string"):
        ask.parse_input_line("x = input(prompt_var)")
    with pytest.raises(ask.AskError, match="must be a string"):
        ask.parse_input_line("x = input(42)")


def test_input_line_sets_a_shell_variable(ictx, answers):
    queue, asked = answers
    queue.append("Ada")
    assert run_line('name = input("What is your name? ")', ictx).status == 0
    assert asked == [("What is your name? ", False)] and ictx.variables["name"] == "Ada"
    assert to_text(run_line('echo "hello $name"', ictx).output) == "hello Ada\n"
    queue.append(KeyboardInterrupt())
    with pytest.raises(PipelineError, match="cancelled") as exc:
        run_line("name = input()", ictx)
    assert exc.value.status == 130 and ictx.variables["name"] == "Ada"  # unchanged
    with pytest.raises(PipelineError, match="must be a string"):
        run_line("x = input(nope)", ictx)


def test_read_builtin(ictx, answers):
    queue, asked = answers
    queue += ["staging", "s3cret"]
    run_line('read -p "Which environment? " env', ictx)
    run_line('read -s -p "Token: " token', ictx)
    assert asked == [("Which environment? ", False), ("Token: ", True)]
    assert ictx.variables == {"env": "staging", "token": "s3cret"}
    with pytest.raises(PipelineError, match="invalid variable name"):
        run_line("read 9lives", ictx)


def test_no_terminal_no_asking(ictx):
    ictx.interactive = False
    with pytest.raises(PipelineError, match="no terminal"):
        run_line('name = input("x? ")', ictx)
    ictx.allow_stateful = False
    with pytest.raises(PipelineError):
        run_line("read name", ictx)  # never over MCP


def test_rc_file_can_ask(ictx, answers, tmp_path):
    from core import startup

    queue, _ = answers
    queue.append("Grace")
    rc = tmp_path / "rc"
    rc.write_text('# ask once at startup\nname = input("What is your name? ")\nexport GREETING="hi $name"\necho $GREETING\n')
    shown = []
    ictx.emit = lambda value: shown.append(to_text(value))
    ictx.emit_error = lambda exc, where: shown.append(f"ERR {exc}")
    assert startup.source(rc, ictx) == (3, 0)
    assert shown == ["hi Grace\n"]
