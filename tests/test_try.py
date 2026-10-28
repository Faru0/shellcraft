"""`try:` / `except [as NAME]:` / `finally:`, the colon and brace forms."""

import pytest

from shellcraft import check, indent, script
from shellcraft.context import ShellExit, to_text
from shellcraft.parser import ParseError
from shellcraft.pipeline import PipelineError, run_line


def out(ctx, text):
    result = run_line(text, ctx)
    return None if result is None else to_text(result.output)


def lines(ctx, text):
    return [line for line in (out(ctx, text) or "").splitlines() if not line.startswith("✗")]


# ── the colon form ────────────────────────────────────────────────────────────

def test_the_first_failure_jumps_to_except(ctx):
    text = ("try:\n    echo one\n    false\n    echo two\n"
            "except as rc:\n    echo \"failed $rc $?\"\nfinally:\n    echo done\necho status $?")
    assert lines(ctx, text) == ["one", "failed 1 1", "done", "status 0"]


def test_no_failure_skips_except(ctx):
    text = "try:\n    echo ok\nexcept:\n    echo no\nfinally:\n    echo done"
    assert lines(ctx, text) == ["ok", "done"]
    assert ctx.last_status == 0


def test_one_line_bodies(ctx):
    assert lines(ctx, "try: false\nexcept: echo failed") == ["failed"]
    assert lines(ctx, "try -py: false\nexcept as s: echo got $s") == ["got 1"]


def test_except_as_holds_the_status(ctx):
    assert lines(ctx, "try:\n    nosuchcommand\nexcept as code:\n    echo $code") == ["127"]
    out(ctx, "three() { return 3; }")
    assert lines(ctx, "try:\n    three\nexcept as code:\n    echo $code") == ["3"]


def test_the_error_still_shows(ctx):
    text = out(ctx, "try:\n    nosuchcommand\nexcept:\n    echo handled")
    assert "nosuchcommand" in text and text.endswith("handled\n")


def test_except_status_is_the_try_status(ctx):
    out(ctx, "try:\n    false\nexcept:\n    echo a; false")
    assert ctx.last_status == 1
    out(ctx, "try:\n    false\nexcept:\n    true")
    assert ctx.last_status == 0


def test_without_except_the_failure_goes_on(ctx):
    assert lines(ctx, "try:\n    false\n    echo no\nfinally:\n    echo fin\necho $?") == ["fin", "1"]


def test_finally_keeps_the_status(ctx):
    out(ctx, "try:\n    true\nfinally:\n    false")
    assert ctx.last_status == 0


def test_tests_and_chains_dont_jump(ctx):
    text = ("try:\n    if grep -q x /nope: echo no\n    false || echo fallback\n    ! true\n"
            "    grep -q x /nope && echo no\n    echo reached\nexcept:\n    echo caught")
    assert lines(ctx, text) == ["fallback", "reached"]


def test_a_python_error_jumps_to_except(ctx):
    text = "try:\n    if nope > 1:\n        echo no\n    echo no\nexcept as rc:\n    echo caught $rc"
    assert lines(ctx, text) == ["caught 1"]
    text = "try:\n    x = 1 + \"a\"\n    echo no\nexcept:\n    echo bad value"
    assert lines(ctx, text) == ["bad value"]


def test_a_failing_function_is_one_failure(ctx):
    text = ("f() { echo in f; false; echo still in f; return 2; }\n"
            "try:\n    f\n    echo no\nexcept as rc:\n    echo f failed $rc")
    assert lines(ctx, text) == ["in f", "still in f", "f failed 2"]


def test_finally_on_break_and_continue(ctx):
    text = ("for -py n in 1..4:\n    try:\n        if n == \"2\": continue\n        if n == \"4\": break\n"
            "        echo n=$n\n    finally:\n        echo fin $n")
    assert lines(ctx, text) == ["n=1", "fin 1", "fin 2", "n=3", "fin 3", "fin 4"]


def test_a_failure_in_a_loop_body(ctx):
    text = ("for -py f in a b:\n    try:\n        [ $f = a ]\n        echo ok $f\n"
            "    except:\n        echo bad $f")
    assert lines(ctx, text) == ["ok a", "bad b"]


def test_finally_on_return(ctx):
    out(ctx, "def g():\n    try:\n        return 5\n    finally:\n        echo cleanup")
    assert lines(ctx, "g; echo status $?") == ["cleanup", "status 5"]


def test_a_bad_return_value_jumps_to_except(ctx):
    out(ctx, "def h():\n    try:\n        return nope + 1\n    except as rc:\n        echo caught $rc")
    assert lines(ctx, "h") == ["caught 1"]


def test_finally_on_exit(ctx, tmp_path):
    with pytest.raises(ShellExit):
        out(ctx, "try:\n    exit 3\nfinally:\n    touch ran")
    assert (tmp_path / "ran").exists()


def test_nesting(ctx):
    text = ("try:\n    try:\n        false\n    finally:\n        echo inner fin\n    echo no\n"
            "except as rc:\n    echo outer $rc")
    assert lines(ctx, text) == ["inner fin", "outer 1"]
    text = ("try:\n    try:\n        false\n    except:\n        echo inner\n        false\n"
            "    echo no\nexcept:\n    echo outer")
    assert lines(ctx, text) == ["inner", "outer"]


# ── the brace form ────────────────────────────────────────────────────────────

def test_brace_form(ctx):
    assert lines(ctx, 'try { echo a; false; echo b } except as rc { echo "rc=$rc" }') == ["a", "rc=1"]
    assert lines(ctx, "try { false } finally { echo fin }") == ["fin"]
    assert ctx.last_status == 1
    assert lines(ctx, "try -py { false } except { echo x } finally { echo y }") == ["x", "y"]


def test_brace_form_over_lines(ctx):
    assert lines(ctx, "try {\n  false\n}\nexcept {\n  echo caught\n}") == ["caught"]


def test_brace_form_in_a_bash_function(ctx):
    text = ("check() {\n  try {\n    echo one\n    [ -f /nope ]\n    echo two\n"
            "  } except as rc {\n    echo \"missing ($rc)\"\n  } finally {\n    echo \"$1 checked\"\n  }\n}\n"
            "check site")
    assert lines(ctx, text) == ["one", "missing (1)", "site checked"]


def test_bash_lines_stay_bash_in_the_brace_form(ctx):
    out(ctx, 'files="a b"')
    assert lines(ctx, "try { for f in $files; do echo $f; done } except { echo no }") == ["a", "b"]


def test_try_as_a_word(ctx):
    assert lines(ctx, "echo try") == ["try"]
    with pytest.raises(PipelineError, match="a shell keyword"):
        out(ctx, "try x")


@pytest.mark.parametrize("text, message", [
    ("try { a }", "missing 'except' or 'finally'"),
    ("try { a } ; echo b", "needs an 'except' or a 'finally'"),
    ("try { a } except as { b }", "except as: expected a name"),
    ("try { a } except ValueError { b }", "no error types"),
    ("try { a } finally { b } except { c }", "'except' goes before 'finally'"),
    ("try { a } except { b } except { c }", "only one 'except'"),
    ("echo x; finally { y }", "'finally' without a matching try"),
])
def test_brace_errors(text, message):
    with pytest.raises(ParseError, match=message):
        script.parse_script(text)


# ── indentation errors ────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, message", [
    ("try:\n    echo x", "line 1: try: needs an 'except:' or a 'finally:'"),
    ("try:\n    echo x\necho y", "line 1: try: needs an 'except:' or a 'finally:'"),
    ("for -py x in a:\n    except:\n        echo y", "'except' without a matching try"),
    ("echo a\nfinally:\n    echo y", "'finally' without a matching try"),
    ("try:\n    a\nexcept ValueError:\n    b", "line 3: except ValueError: there are no error types"),
    ("try:\n    a\nfinally:\n    b\nexcept:\n    c", "line 5: 'except:' goes before 'finally:'"),
    ("try:\n    a\nexcept:\n    b\nexcept:\n    c", "line 5: a try has only one 'except:'"),
    ("try:\n    a\nfinally x:\n    b", "finally: nothing goes between"),
    ("try:\n    a\nexcept\n    b", "'except' needs a ':' at the end"),
])
def test_indent_errors(text, message):
    with pytest.raises(indent.IndentError, match=message):
        indent.translate(text)


def test_translation():
    text = "try:\n    a\nexcept as rc:\n    b\nfinally:\n    c"
    assert indent.translate(text) == "try -py {\n    a\n} except as rc {\n    b\n} finally {\n    c\n}"


# ── set -e and the ERR trap ───────────────────────────────────────────────────

def test_set_e_handled_goes_on(ctx):
    out(ctx, "set -e")
    text = "try:\n    false\nexcept:\n    echo handled\necho goes on"
    assert lines(ctx, text) == ["handled", "goes on"]


def test_set_e_unhandled_stops_after_finally(ctx):
    out(ctx, "set -e")
    text = "try:\n    false\nfinally:\n    echo fin\necho not here"
    assert lines(ctx, text) == ["fin"]
    assert ctx.last_status == 1


def test_err_trap_skips_handled_failures(ctx):
    out(ctx, "trap 'echo ERR ran' ERR")
    assert lines(ctx, "try:\n    false\nexcept:\n    echo handled") == ["handled"]
    assert lines(ctx, "f() { false; }\ntry:\n    f\nexcept:\n    echo handled") == ["handled"]
    assert lines(ctx, "try:\n    false\nfinally:\n    echo fin") == ["fin", "ERR ran"]
    assert lines(ctx, "try:\n    false\nexcept:\n    false") == ["ERR ran"]


# ── source -n / func check ────────────────────────────────────────────────────

def test_check_walks_every_block(ctx):
    text = ("try:\n    echo a\n    nosuchthing\nexcept as rc:\n    alsonot\nfinally:\n    rm -f lock\n"
            "b() {\n  try { frob } finally { echo x }\n}\n")
    report = check.check_text(text, "f.sc", ctx)
    found = [(p.line, p.message) for p in report.problems]
    assert (3, "unknown command 'nosuchthing'") in found
    assert (5, "unknown command 'alsonot'") in found
    assert (9, "unknown command 'frob'") in found
    assert report.errors == 0


def test_check_reports_try_errors(ctx):
    report = check.check_text("try:\n    echo a\necho b\n", "f.sc", ctx)
    assert report.errors == 1 and "needs an 'except:'" in report.text()


def test_man_page(ctx):
    assert "except as NAME" in out(ctx, "man try")
