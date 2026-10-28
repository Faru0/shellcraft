"""`set`: the shell options (-e, -u, -x, -o pipefail), `set -o`, and `set --` for the arguments."""

import pytest

from shellcraft import startup
from shellcraft.context import to_text
from shellcraft.pipeline import PipelineError, UnboundError, run_line


def out(ctx, text):
    result = run_line(text, ctx)
    return None if result is None else to_text(result.output)


@pytest.fixture
def errors(ctx):
    """What goes to the error stream (`set -x` traces)."""
    seen = []
    ctx.emit_stderr = seen.append
    return seen


# ── turning options on and off ────────────────────────────────────────────────

def test_set_o_lists_the_options(ctx):
    assert out(ctx, "set -o") == "errexit        off\nnounset        off\npipefail       off\nxtrace         off\n"
    out(ctx, "set -euo pipefail")
    assert ctx.options == {"errexit", "nounset", "pipefail"}
    assert "set -o errexit\n" in out(ctx, "set +o") and "set +o xtrace\n" in out(ctx, "set +o")


def test_plus_turns_off_and_long_names_work(ctx):
    out(ctx, "set -eux")
    out(ctx, "set +ex")
    assert ctx.options == {"nounset"}
    out(ctx, "set -o errexit; set +o nounset")
    assert ctx.options == {"errexit"}


def test_unknown_option_is_an_error_and_changes_nothing(ctx):
    with pytest.raises(PipelineError, match="unknown option") as info:
        run_line("set -ek", ctx)
    assert info.value.status == 2 and not ctx.options
    with pytest.raises(PipelineError, match="unknown option"):
        run_line("set -o nosuch", ctx)


def test_set_dash_dash_replaces_the_arguments(ctx):
    assert out(ctx, 'set -- a "b c"; echo $# $2') == "2 b c\n"
    assert out(ctx, 'f() { set -- x; echo $# $1; }; f 1 2 3') == "1 x\n"
    assert out(ctx, "set --; echo $#") == "0\n"


def test_a_function_cannot_shadow_set(ctx):
    out(ctx, "set() { echo mine; }")
    assert out(ctx, "set -e; echo ok") == "ok\n" and "errexit" in ctx.options


# ── -e ────────────────────────────────────────────────────────────────────────

def test_errexit_stops_the_rest_of_the_line_with_its_status(ctx):
    out(ctx, "set -e")
    result = run_line("echo a; grep -q zz /dev/null; echo b", ctx)
    assert to_text(result.output) == "a\n" and result.status == 1 and result.stopped
    assert out(ctx, "echo next") == "next\n"  # never closes the shell


def test_errexit_skips_tests_chains_and_negation(ctx):
    out(ctx, "set -e")
    assert out(ctx, "false && echo x; echo after") == "after\n"
    assert out(ctx, "false || echo rescued; echo after") == "rescued\nafter\n"
    assert out(ctx, "! true; echo after") == "after\n"
    assert out(ctx, "if false; then echo t; fi; echo after") == "after\n"
    assert out(ctx, "if -py 1 == 2:\n    echo t\nelse:\n    false || echo no\necho after") == "no\nafter\n"
    assert out(ctx, "n=0; while [ $n -lt 2 ]; do n=$((n+1)); done; echo $n") == "2\n"
    assert out(ctx, "true && false; echo after") == ""
    assert ctx.last_status == 1


def test_errexit_does_not_apply_inside_a_function_used_as_a_test(ctx):
    out(ctx, "set -e")
    out(ctx, "check() { false; echo still; return 1; }")
    assert out(ctx, "if check; then echo yes; else echo no; fi") == "still\nno\n"


def test_errexit_stops_a_function_and_its_caller(ctx):
    out(ctx, "set -e")
    out(ctx, "f() { echo in; false; echo never; }")
    assert out(ctx, "f; echo after") == "in\n"
    out(ctx, "set +e")
    assert out(ctx, "f; echo after") == "in\nnever\nafter\n"


def test_errexit_in_a_python_style_block(ctx):
    out(ctx, "set -e")
    text = "for -py n in 1 2 3:\n    echo $n\n    false\n"
    assert out(ctx, text) == "1\n"


def test_errexit_stops_a_sourced_file_and_says_where_once(ctx, tmp_path):
    shown = []
    ctx.emit = lambda value: shown.append(to_text(value))
    ctx.emit_error = lambda exc, line: shown.append(f"ERR {exc}\n")
    path = tmp_path / "steps.sc"
    path.write_text("set -e\necho one\ngrep -q zz /dev/null\necho two\n")
    ran, failed = startup.source(path, ctx)
    assert (ran, failed) == (3, 1) and ctx.last_status == 1
    text = "".join(shown)
    assert "one" in text and "two" not in text
    assert text.count("steps.sc:3") == 1 and "set -e" in text


def test_errexit_stops_a_sourced_file_on_an_error_reported_once(ctx, tmp_path):
    shown = []
    ctx.emit = lambda value: shown.append(to_text(value))
    ctx.emit_error = lambda exc, line: shown.append(f"ERR {exc}\n")
    path = tmp_path / "rc"
    path.write_text("set -e\nnosuchcommand\necho two\n")
    startup.source(path, ctx)
    text = "".join(shown)
    assert "two" not in text and text.count("ERR") == 1 and ctx.last_status == 127


def test_without_errexit_a_file_goes_on(ctx, tmp_path):
    shown = []
    ctx.emit = lambda value: shown.append(to_text(value))
    path = tmp_path / "rc"
    path.write_text("false\necho two\n")
    startup.source(path, ctx)
    assert "two" in "".join(shown)


def test_errexit_stops_a_piped_block_but_not_the_tail(ctx):
    out(ctx, "set -e")
    assert out(ctx, "for i in 1 2; do echo $i; false; done | cat; echo after") == "1\nafter\n"


def test_func_run_isolated_puts_the_options_back(ctx):
    out(ctx, "func new opts 'set -ex'")
    out(ctx, "func run -i opts")
    assert not ctx.options


# ── -u ────────────────────────────────────────────────────────────────────────

def test_nounset_at_the_prompt(ctx):
    out(ctx, "set -u")
    with pytest.raises(UnboundError, match="nope: unbound variable"):
        run_line("echo $nope", ctx)
    assert ctx.last_status == 1
    assert out(ctx, "echo ${nope:-fallback} $?") == "fallback 1\n"
    assert out(ctx, "x=1; echo ${x}") == "1\n"


def test_nounset_stops_the_line_without_errexit(ctx):
    out(ctx, "set -u")
    result = run_line("echo a; echo $nope; echo b", ctx)
    text = to_text(result.output)
    assert text.startswith("a\n") and "nope: unbound variable" in text and "b\n" not in text
    assert result.status == 1 and result.stopped


def test_nounset_in_a_for_list_and_a_case_word(ctx):
    out(ctx, "set -u")
    assert "unbound" in out(ctx, "for i in $nope; do echo $i; done; echo after")
    assert "after" not in out(ctx, "case $nope in *) echo hit;; esac; echo after")


def test_nounset_leaves_function_arguments_alone(ctx):
    out(ctx, "set -u")
    assert out(ctx, 'f() { echo "[$1]" $#; }; f') == "[] 0\n"


# ── -x ────────────────────────────────────────────────────────────────────────

def test_xtrace_shows_each_command_expanded(ctx, errors):
    out(ctx, "set -x")
    assert out(ctx, "name='a b'; echo $name") == "a b\n"
    out(ctx, "for i in 1 2; do echo $i | cat; done")
    out(ctx, "set +x")
    out(ctx, "echo quiet")
    assert errors == ["+ name='a b'\n", "+ echo 'a b'\n", "+ echo 1\n", "+ cat\n", "+ echo 2\n", "+ cat\n",
                      "+ set +x\n"]


def test_xtrace_at_the_prompt(ctx, errors):
    out(ctx, "set -x")
    out(ctx, "echo hi > /dev/null")
    assert errors == ["+ echo hi\n"]


# ── pipefail ──────────────────────────────────────────────────────────────────

def test_pipefail_takes_the_last_failure(ctx):
    run_line("false | cat", ctx)
    assert ctx.last_status == 0
    out(ctx, "set -o pipefail")
    run_line("false | cat", ctx)
    assert ctx.last_status == 1
    run_line("echo hi | grep -q zz | cat", ctx)
    assert ctx.last_status == 1
    run_line("echo hi | cat", ctx)
    assert ctx.last_status == 0


def test_pipefail_with_errexit_stops(ctx):
    out(ctx, "set -e -o pipefail")
    assert out(ctx, "false | cat; echo after") == ""
