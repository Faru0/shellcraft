"""`select NAME in WORD...; do …; done`: the menu, $PS3, $REPLY, invalid answers, break, Ctrl-D/C."""

import io

import pytest
from rich.console import Console

from shellcraft import script
from shellcraft.context import ShellContext, to_text
from shellcraft.loader import ModuleRegistry
from shellcraft.parser import ParseError
from shellcraft.pipeline import run_line
from shellcraft.themes import UI, all_themes


@pytest.fixture
def ictx(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / "home"))
    ctx = ShellContext(registry=ModuleRegistry(tmp_path / "modules"))
    ctx.interactive = True
    ctx.ui = UI(all_themes({})["nord"], Console(file=io.StringIO(), width=80))
    return ctx


@pytest.fixture
def answers(monkeypatch):
    asked, queue = [], []

    def fake(prompt, is_password=False):
        asked.append(prompt)
        answer = queue.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    import prompt_toolkit

    monkeypatch.setattr(prompt_toolkit, "prompt", fake)
    return queue, asked


def test_menu_choice_reply_and_break(ictx, answers):
    queue, asked = answers
    queue += ["7", "", "2"]
    result = run_line('select env in dev staging prod; do if [ -n "$env" ]; then echo "go $env"; break; fi; '
                      'echo "bad $REPLY"; done', ictx)
    assert to_text(result.output) == "bad 7\ngo staging\n"
    assert asked == ["#? "] * 3 and ictx.variables["env"] == "staging" and ictx.variables["REPLY"] == "2"
    screen = ictx.ui.console.file.getvalue()
    assert screen.count("1) dev\n2) staging\n3) prod") == 2  # shown at the start and after the empty answer


def test_ps3_lists_and_ending_with_ctrl_d_or_ctrl_c(ictx, answers):
    queue, asked = answers
    run_line('PS3="Pick: "', ictx)
    queue += ["1", EOFError()]
    run_line("select f in a{1,2}; do echo $f; done", ictx)
    assert asked == ["Pick: ", "Pick: "] and ictx.last_status == 0
    queue.append(KeyboardInterrupt())
    run_line("select f in x y; do echo $f; done", ictx)
    assert ictx.last_status == 130


def test_select_in_a_function_and_syntax_errors(ictx, answers):
    queue, _ = answers
    queue.append("1")
    run_line("pick() { select c in yes no; do echo chose $c; break; done; }", ictx)
    assert to_text(run_line("pick", ictx).output) == "chose yes\n"
    assert script.incomplete("select x in a b; do") is not None
    with pytest.raises(ParseError, match="give the choices"):
        run_line("select x; do echo; done", ictx)


def test_no_terminal_no_menu(ictx):
    ictx.interactive = False
    result = run_line("select x in a b; do echo $x; done", ictx)
    assert "select needs the interactive shell" in to_text(result.output)
