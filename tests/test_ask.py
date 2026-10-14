"""Asking for values: `name = input("…")` lines, the `read` builtin, and both in an rc file."""

import io

import pytest
from rich.console import Console

from shellcraft import ask
from shellcraft.context import ShellContext, to_text
from shellcraft.loader import ModuleRegistry
from shellcraft.pipeline import PipelineError, run_line
from shellcraft.themes import UI, all_themes


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
    from shellcraft import startup

    queue, _ = answers
    queue.append("Grace")
    rc = tmp_path / "rc"
    rc.write_text('# ask once at startup\nname = input("What is your name? ")\nexport GREETING="hi $name"\necho $GREETING\n')
    shown = []
    ictx.emit = lambda value: shown.append(to_text(value))
    ictx.emit_error = lambda exc, where: shown.append(f"ERR {exc}")
    assert startup.source(rc, ictx) == (3, 0)
    assert shown == ["hi Grace\n"]


def test_int_and_float_input_ask_until_a_number(ictx, answers):
    queue, asked = answers
    assert ask.parse_ask_line('n = int(input("How many? "))') == ("n", "How many? ", "int")
    assert ask.parse_ask_line("r=float( input() )") == ("r", "", "float")
    assert ask.parse_ask_line('x = input("a")') == ("x", "a", "str")
    assert ask.parse_ask_line("x = str(input())") is None  # only int / float wrap input()
    queue += ["three", " 3 ", "2.5"]
    run_line('count = int(input("How many? "))', ictx)
    assert ictx.variables["count"] == "3" and asked == [("How many? ", False)] * 2  # asked again after "three"
    assert "'three' is not a whole number; try again" in ictx.ui.console.file.getvalue()
    run_line('ratio = float(input("Ratio? "))', ictx)
    assert ictx.variables["ratio"] == "2.5"
    assert to_text(run_line("echo $((count + 1))", ictx).output) == "4\n"
    queue.append(KeyboardInterrupt())
    with pytest.raises(PipelineError, match="cancelled"):
        run_line("n = int(input())", ictx)
