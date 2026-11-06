"""Grouping, eval and exec (SCRIPTING-TODO item 21)."""

import os

import pytest

from shellcraft import startup
from shellcraft.context import ShellExit, to_text
from shellcraft.parser import ParseError
from shellcraft.pipeline import PipelineError, run_line


def out(ctx, line):
    result = run_line(line, ctx)
    return "" if result is None or result.output is None else (
        result.output if isinstance(result.output, str) else result.output.text)


def run_file(ctx, tmp_path, text):
    path = tmp_path / "script.sc"
    path.write_text(text)
    collected = []
    ctx.emit = lambda value: collected.append(to_text(value))
    ctx.emit_error = lambda exc, line: collected.append(f"ERR {exc}\n")
    startup.source(path, ctx)
    return "".join(collected)


# ── { } ──────────────────────────────────────────────────────────────────────

def test_a_brace_group_runs_here(ctx):
    assert out(ctx, "{ echo a; echo b; }") == "a\nb\n"
    assert out(ctx, "x=1; { x=2; y=3; }; echo $x $y") == "2 3\n"


def test_a_brace_group_over_lines(ctx):
    assert out(ctx, "{ echo 1\necho 2; }") == "1\n2\n"


def test_a_brace_group_in_a_pipe_and_a_redirect(ctx, tmp_path):
    assert out(ctx, "{ echo a; echo b; } | wc -l").strip() == "2"
    assert out(ctx, "{ echo c; echo a; echo b; } | sort") == "a\nb\nc\n"
    out(ctx, "{ echo hi; echo there; } > g.txt")
    assert (tmp_path / "g.txt").read_text() == "hi\nthere\n"
    out(ctx, "{ echo more; } >> g.txt")
    assert (tmp_path / "g.txt").read_text().endswith("more\n")
    assert out(ctx, "{ echo oops >&2; echo fine; } 2>&1 | wc -l").strip() == "2"


def test_a_group_with_and_or(ctx):
    assert out(ctx, "{ echo a; false; } && echo no || echo yes") == "a\nyes\n"
    assert out(ctx, "true && { echo x; echo y; }") == "x\ny\n"
    assert out(ctx, "false || { echo fallback; }") == "fallback\n"
    assert out(ctx, "{ echo a; } | cat && echo then") == "a\nthen\n"


def test_nested_groups(ctx):
    assert out(ctx, "{ echo 1; ( echo 2; { echo 3; } ); }") == "1\n2\n3\n"


def test_a_group_in_a_loop_and_a_function(ctx):
    assert out(ctx, "for i in 1 2; do { echo a$i; echo b$i; } | tr a-z A-Z; done") == "A1\nB1\nA2\nB2\n"
    out(ctx, "f() { { echo in; echo group; } | sort; }")
    assert out(ctx, "f") == "group\nin\n"
    assert out(ctx, "for i in 1 2 3; do { [ $i = 2 ] && continue; echo $i; }; done") == "1\n3\n"


def test_piping_into_a_group(ctx):
    assert out(ctx, "echo hello | { read a; echo got $a; }") == "got hello\n"
    assert out(ctx, "printf 'a\\nb\\n' | { read x; read y; echo $y$x; }") == "ba\n"
    assert out(ctx, "echo hello | ( read a; echo got $a )") == "got hello\n"


def test_a_brace_group_needs_a_command_and_a_closing_brace(ctx):
    with pytest.raises(ParseError):
        run_line("{ }", ctx)
    with pytest.raises(ParseError):
        run_line("{ echo hi", ctx)


def test_py_blocks_are_unchanged(ctx):
    assert out(ctx, "for -py x in a b { echo $x }") == "a\nb\n"
    assert out(ctx, "if -py (1 == 1) { echo yes }") == "yes\n"


# ── ( ) ──────────────────────────────────────────────────────────────────────

def test_a_subshell_keeps_its_variables_to_itself(ctx):
    assert out(ctx, "x=1; ( x=2; echo in $x ); echo out $x") == "in 2\nout 1\n"
    out(ctx, "( y=5 )")
    assert "y" not in ctx.variables


def test_a_subshell_restores_the_directory(ctx, tmp_path):
    (tmp_path / "sub").mkdir()
    before = os.getcwd()
    assert out(ctx, "( cd sub; pwd )").strip().endswith("sub")
    assert os.getcwd() == before


def test_a_subshell_restores_options_exports_and_functions(ctx, monkeypatch):
    monkeypatch.delenv("SC_SUB", raising=False)
    out(ctx, "( set -e; export SC_SUB=1; g() { echo g; }; set -o pipefail )")
    assert "errexit" not in ctx.options and "pipefail" not in ctx.options
    assert "SC_SUB" not in ctx.exported and "SC_SUB" not in os.environ
    assert "g" not in ctx.functions


def test_exit_in_a_subshell_ends_only_the_subshell(ctx):
    assert out(ctx, "( exit 3 ); echo $?") == "3\n"
    assert out(ctx, "( echo before; exit 0; echo after ); echo done") == "before\ndone\n"


def test_a_subshell_status_and_redirect(ctx, tmp_path):
    assert out(ctx, "( false ); echo $?") == "1\n"
    assert out(ctx, "( true; false ) || echo failed") == "failed\n"
    out(ctx, "( echo e1; echo e2 ) > f.txt")
    assert (tmp_path / "f.txt").read_text() == "e1\ne2\n"
    assert out(ctx, "( echo b; echo a ) | sort") == "a\nb\n"


def test_a_subshell_has_its_own_traps(ctx):
    assert out(ctx, "( trap 'echo bye' EXIT; echo hi ); echo after") == "hi\nbye\nafter\n"


def test_subshell_errors(ctx):
    with pytest.raises(ParseError):
        run_line("( )", ctx)
    with pytest.raises(ParseError):
        run_line("( echo hi", ctx)


def test_a_subshell_in_a_file(ctx, tmp_path):
    text = "n=1\n(\n  n=2\n  echo inside $n\n)\necho outside $n\n"
    assert run_file(ctx, tmp_path, text) == "inside 2\noutside 1\n"


# ── eval ─────────────────────────────────────────────────────────────────────

def test_eval_runs_text_in_this_shell(ctx):
    assert out(ctx, 'cmd="echo hello"; eval $cmd') == "hello\n"
    out(ctx, 'name=count; eval "$name=5"')
    assert ctx.variables["count"] == "5"
    assert out(ctx, "eval echo a b") == "a b\n"


def test_eval_runs_blocks_and_lists(ctx):
    assert out(ctx, "eval 'for i in 1 2; do echo it$i; done'") == "it1\nit2\n"
    assert out(ctx, "eval 'echo a; echo b'") == "a\nb\n"
    assert out(ctx, "eval 'echo a' 'echo b'") == "a echo b\n"  # joined with spaces, as bash does


def test_eval_status_and_capture(ctx):
    assert out(ctx, "eval false; echo $?") == "1\n"
    assert out(ctx, "eval true; echo $?") == "0\n"
    assert out(ctx, "x=$(eval 'for i in a b; do echo $i; done'); echo $x") == "a\nb\n"
    assert out(ctx, "eval 'for i in a b; do echo $i; done' | wc -l").strip() == "2"
    assert out(ctx, "eval") == ""


def test_eval_with_an_error_stops_with_the_error(ctx):
    with pytest.raises(PipelineError, match="command not found"):
        run_line("eval nosuchcommand_xyz", ctx)


def test_eval_that_evals_itself_is_stopped(ctx):
    assert "nested" in out(ctx, "s='eval $s'; eval $s")


def test_eval_exit_ends_the_shell(ctx):
    with pytest.raises(ShellExit):
        run_line("eval exit", ctx)


def test_eval_is_not_available_over_mcp(ctx):
    ctx.allow_stateful = False
    with pytest.raises(PipelineError, match="not available"):
        run_line("eval echo hi", ctx)


# ── exec ─────────────────────────────────────────────────────────────────────

def test_exec_runs_the_command_then_ends(ctx):
    seen = []
    ctx.emit = lambda value: seen.append(to_text(value))
    with pytest.raises(ShellExit) as info:
        run_line("exec echo bye", ctx)
    assert info.value.code == 0 and seen == ["bye\n"]


def test_exec_passes_the_status_on(ctx):
    ctx.emit = lambda value: None
    with pytest.raises(ShellExit) as info:
        run_line("exec false", ctx)
    assert info.value.code == 1


def test_exec_with_no_command_does_nothing_and_a_redirect_is_refused(ctx):
    assert out(ctx, "exec") == ""
    with pytest.raises(PipelineError, match="exec: redirecting"):
        run_line("exec > out.txt", ctx)


def test_exec_in_a_file_ends_the_file(ctx, tmp_path):
    path = tmp_path / "s.sc"
    path.write_text("echo one\nexec echo two\necho three\n")
    seen = []
    ctx.emit = lambda value: seen.append(to_text(value))
    with pytest.raises(ShellExit):
        startup.source(path, ctx)
    assert "".join(seen) == "one\ntwo\n"


def test_exec_in_a_subshell_ends_only_the_subshell(ctx):
    seen = []
    ctx.emit = lambda value: seen.append(to_text(value))
    run_line("( exec echo x ); echo after $?", ctx)
    assert "".join(seen) == "x\nafter 0\n"


def test_exec_not_found_is_an_error_not_an_exit(ctx):
    with pytest.raises(PipelineError, match="command not found"):
        run_line("exec nosuchcommand_xyz", ctx)
