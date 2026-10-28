"""`trap`: EXIT, INT and ERR traps, the frame each belongs to, and `exit` in the rc file."""

import dataclasses

import pytest
from rich.console import Console

from shellcraft import shell, startup, traps
from shellcraft.builtins import BUILTINS
from shellcraft.context import ShellContext, to_text
from shellcraft.pipeline import PipelineError, run_line
from shellcraft.themes import UI, all_themes


def out(ctx, text):
    result = run_line(text, ctx)
    return None if result is None else to_text(result.output)


@pytest.fixture
def ctrl_c(monkeypatch):
    """`ctrlc`: a command that is stopped by Ctrl-C (it raises KeyboardInterrupt on the main thread)."""
    def interrupted(ctx, args, stdin):
        raise KeyboardInterrupt

    monkeypatch.setitem(BUILTINS, "ctrlc", dataclasses.replace(BUILTINS["true"], fn=interrupted, stateful=True,
                                                               direct=True))


@pytest.fixture
def shown(ctx):
    """What a sourced file or func shows (ctx.emit), as text."""
    seen = []
    ctx.emit = lambda value: seen.append(to_text(value))
    ctx.emit_error = lambda exc, line: seen.append(f"ERROR {exc}\n")
    return seen


# ── the builtin ───────────────────────────────────────────────────────────────

def test_set_list_and_remove(ctx):
    out(ctx, "trap 'echo bye' EXIT; trap \"echo it's\" int")
    assert out(ctx, "trap") == "trap -- 'echo bye' EXIT\ntrap -- 'echo it'\\''s' INT\n"
    assert out(ctx, "trap -p INT") == "trap -- 'echo it'\\''s' INT\n"
    out(ctx, "trap - SIGINT; trap - 0")
    assert out(ctx, "trap -p") == "" and ctx.traps == [{}]


def test_several_signals_and_ignore(ctx):
    out(ctx, "trap 'echo x' EXIT ERR; trap '' INT")
    assert ctx.traps[-1] == {"EXIT": "echo x", "ERR": "echo x", "INT": ""}
    assert out(ctx, "trap -l") == "EXIT\nINT\nERR\n"


def test_unknown_signal_and_bad_usage(ctx):
    with pytest.raises(PipelineError, match="SIGHUP: unknown signal") as info:
        run_line("trap 'echo x' SIGHUP", ctx)
    assert info.value.status == 2
    with pytest.raises(PipelineError, match="usage"):
        run_line("trap 'echo x'", ctx)


def test_a_function_cannot_shadow_trap(ctx):
    out(ctx, "trap() { echo mine; }")
    out(ctx, "trap 'echo x' EXIT")
    assert ctx.traps[0] == {"EXIT": "echo x"}


def test_signal_names():
    assert [traps.signal_name(w) for w in ("exit", "0", "SIGINT", "int", "2", "Err", "HUP")] == \
        ["EXIT", "EXIT", "INT", "INT", "INT", "ERR", None]


# ── EXIT ──────────────────────────────────────────────────────────────────────

def test_exit_runs_when_a_function_ends_and_sees_its_locals(ctx):
    out(ctx, "f() { local tmp=scratch; trap 'echo removing $tmp $1' EXIT; echo working; }")
    assert out(ctx, "f A; echo after") == "working\nremoving scratch A\nafter\n"
    assert ctx.traps == [{}] and "tmp" not in ctx.variables


def test_exit_runs_on_return_and_on_set_e(ctx, tmp_path):
    out(ctx, "f() { trap 'echo cleanup $?' EXIT; return 3; }")
    assert out(ctx, "f") == "cleanup 3\n" and ctx.last_status == 3
    out(ctx, "g() { trap 'rm -f tmp.txt' EXIT; echo data > tmp.txt; set -e; false; echo never; }")
    result = run_line("g", ctx)
    out(ctx, "set +e")
    assert to_text(result.output) == "" and result.status == 1 and not (tmp_path / "tmp.txt").exists()


def test_exit_in_a_def_function(ctx):
    out(ctx, "def greet(name):\n    trap 'echo bye $name' EXIT\n    echo hi $name")
    assert out(ctx, "greet Ann") == "hi Ann\nbye Ann\n"


def test_exit_goes_with_the_function_output(ctx):
    out(ctx, "f() { trap 'echo done' EXIT; echo one; }")
    assert out(ctx, "f | sort -r") == "one\ndone\n"


def test_exit_trap_in_a_sourced_file_runs_when_it_ends(ctx, shown, tmp_path):
    path = tmp_path / "steps.sc"
    path.write_text("trap 'echo file done' EXIT\necho one\n")
    out(ctx, f"source {path}")
    assert "".join(shown) == "one\nfile done\n" and ctx.traps == [{}]


def test_rc_and_startup_scripts_set_session_traps(ctx, shown, tmp_path):
    path = tmp_path / "rc"
    path.write_text("trap 'echo leaving' EXIT\n")
    startup.source(path, ctx, session=True)
    assert shown == [] and ctx.traps == [{"EXIT": "echo leaving"}]
    traps.session_exit(ctx)
    assert "".join(shown) == "leaving\n" and ctx.traps == [{}]


def test_exit_trap_in_a_func(ctx, shown):
    run_line("func new tidy 'trap \"echo tidied\" EXIT' 'echo step' 'false' 'echo never'", ctx)
    shown.clear()
    result = run_line("func run tidy", ctx)
    assert result.status == 1
    assert "step\ntidied\n" in "".join(shown) and "never" not in "".join(shown)


# ── INT ───────────────────────────────────────────────────────────────────────

def test_int_in_a_function_then_it_stops(ctx, ctrl_c, shown):
    out(ctx, "f() { trap 'echo caught' INT; trap 'echo exit' EXIT; echo start; ctrlc; echo never; }")
    with pytest.raises(KeyboardInterrupt):
        run_line("f", ctx)
    assert "".join(shown) == "start\ncaught\nexit\n"


def test_int_uses_the_callers_trap_once(ctx, ctrl_c, shown, tmp_path):
    path = tmp_path / "steps.sc"
    path.write_text("trap 'echo file caught' INT\nf() { ctrlc; }\necho one\nf\necho never\n")
    startup.source(path, ctx)
    text = "".join(shown)
    assert text.count("file caught") == 1 and "never" not in text and ctx.last_status == 130


def test_int_ignored_with_an_empty_trap(ctx, ctrl_c, shown):
    out(ctx, "f() { trap '' INT; ctrlc; }")
    with pytest.raises(KeyboardInterrupt):
        run_line("f", ctx)
    assert "".join(shown) == ""


def test_int_in_a_func(ctx, ctrl_c, shown):
    run_line("func new stopper 'trap \"echo stopped\" INT' 'ctrlc' 'echo never'", ctx)
    shown.clear()
    assert run_line("func run stopper", ctx).status == 130
    assert "stopped" in "".join(shown) and "never" not in "".join(shown)


# ── ERR ───────────────────────────────────────────────────────────────────────

def test_err_runs_after_a_failing_command_with_its_status(ctx):
    out(ctx, "trap 'echo failed with $?' ERR")
    assert out(ctx, "echo a; grep -q zz /dev/null; echo b $?") == "a\nfailed with 1\nb 1\n"
    assert out(ctx, "grep -q zz /dev/null") == "failed with 1\n" and ctx.last_status == 1


def test_err_skips_tests_chains_and_negation(ctx):
    out(ctx, "trap 'echo ERR' ERR")
    assert out(ctx, "if false; then :; fi; false && true; ! true; while false; do :; done; echo ok") == "ok\n"
    assert out(ctx, "false || false") == "ERR\n"


def test_err_does_not_recurse(ctx):
    out(ctx, "trap 'echo in trap; false' ERR")
    assert out(ctx, "false; echo next") == "in trap\nnext\n"


def test_err_runs_once_for_a_failing_function(ctx):
    out(ctx, "trap 'echo ERR $?' ERR; set -e")
    out(ctx, "f() { false; echo never; }; g() { f; }")
    assert out(ctx, "g; echo never") == "ERR 1\n"
    out(ctx, "set +e")


def test_err_trap_of_a_function_is_its_own(ctx):
    out(ctx, "f() { trap 'echo f failed' ERR; false; echo on; }")
    assert out(ctx, "f; false; echo end") == "f failed\non\nend\n"


def test_err_for_an_unknown_command(ctx):
    out(ctx, "trap 'echo ERR $?' ERR")
    assert out(ctx, "nosuchcommand; echo next").endswith("ERR 127\nnext\n")


def test_err_in_a_sourced_file_for_an_error_line(ctx, shown, tmp_path):
    path = tmp_path / "steps.sc"
    path.write_text("trap 'echo ERR $?' ERR\nnosuchcommand\necho next\n")
    startup.source(path, ctx)
    text = "".join(shown)
    assert "ERR 127\nnext\n" in text


# ── the session: the prompt, exit and Ctrl-D ─────────────────────────────────

@pytest.fixture
def repl(monkeypatch, tmp_path, registry):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(shell, "create_prompt_toolkit_console", lambda cleanup: None)
    console = Console(record=True, width=120, force_terminal=False, color_system=None)
    ctx = ShellContext(registry=registry, ui=UI(all_themes({})["nord"], console), config={})
    with shell.Shell(ctx) as sh:
        yield sh, ctx, console


def scripted(sh, monkeypatch, *steps):
    queue = list(steps)

    def ask(message):
        item = queue.pop(0) if queue else EOFError()
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(sh, "_ask", ask)


def test_exit_trap_at_the_prompt_runs_on_exit(repl, monkeypatch):
    sh, ctx, console = repl
    scripted(sh, monkeypatch, "trap 'echo see you' EXIT", "exit 4")
    assert sh.loop() == 4 and "see you" in console.export_text()


def test_exit_trap_at_the_prompt_runs_on_ctrl_d(repl, monkeypatch):
    sh, ctx, console = repl
    scripted(sh, monkeypatch, "trap 'echo see you' EXIT")
    assert sh.loop() == 0 and console.export_text().count("see you") == 1


def test_int_and_err_traps_at_the_prompt(repl, monkeypatch, ctrl_c):
    sh, ctx, console = repl
    scripted(sh, monkeypatch, "trap 'echo got ctrl-c' INT", "ctrlc", "trap 'echo oops $?' ERR", "nosuchcommand")
    sh.loop()
    text = console.export_text()
    assert "got ctrl-c" in text and "^C interrupted" in text and "oops 127" in text


def test_exit_in_the_rc_file_ends_shellcraft_cleanly(monkeypatch, tmp_path, registry, capsys):
    from shellcraft import cli

    console = Console(record=True, width=120, force_terminal=False, color_system=None)

    class RcShell:
        run_rc = shell.Shell.run_rc
        _goodbye = shell.Shell._goodbye
        _trap = shell.Shell._trap
        _quietly = staticmethod(shell.Shell._quietly)
        _interruptible = shell.Shell._interruptible

        def __init__(self, ctx):
            self.ctx = ctx
            self.ui = UI(all_themes({})["nord"], console)
            ctx.emit = lambda value: console.print(value)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

        def loop(self):
            raise AssertionError("exit in the rc file must not reach the prompt")

    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path))
    (tmp_path / "rc").write_text("trap 'echo rc trap ran' EXIT\nexit 5\necho never\n")
    monkeypatch.setattr(shell, "Shell", RcShell)
    assert cli.main(["--no-banner"]) == 5
    text = console.export_text() + capsys.readouterr().out
    assert "rc trap ran" in text and "startup failed" not in text and "never" not in text


def test_completion_offers_the_signals(ctx):
    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document

    from shellcraft.completer import ShellCompleter

    completer = ShellCompleter(ctx)

    def complete(text):
        return [c.text for c in completer.get_completions(Document(text), CompleteEvent())]

    assert complete("trap 'rm x' ") == ["ERR", "EXIT", "INT"]
    assert complete("trap 'rm x' EXIT e") == ["ERR"]
