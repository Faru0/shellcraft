"""Arithmetic commands (SCRIPTING-TODO item 20): `(( ))`, C-style for, let, readonly, declare,
$((n=4)), $RANDOM, $SECONDS, printf -v, [ -t FD ]."""

import pytest

from shellcraft import arith, startup
from shellcraft.context import to_text
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


# ── arith.execute ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, result, after", [
    ("n = 4", 4, {"n": "4"}),
    ("n += 2", 5, {"n": "5"}),
    ("n -= 1", 2, {"n": "2"}),
    ("n *= 3", 9, {"n": "9"}),
    ("n /= 2", 1, {"n": "1"}),
    ("n %= 2", 1, {"n": "1"}),
    ("n <<= 2", 12, {"n": "12"}),
    ("n >>= 1", 1, {"n": "1"}),
    ("n &= 1", 1, {"n": "1"}),
    ("n |= 4", 7, {"n": "7"}),
    ("n ^= 1", 2, {"n": "2"}),
    ("n **= 2", 9, {"n": "9"}),
    ("n++", 3, {"n": "4"}),
    ("++n", 4, {"n": "4"}),
    ("n--", 3, {"n": "2"}),
    ("--n", 2, {"n": "2"}),
    ("a = b = 5", 5, {"n": "3", "a": "5", "b": "5"}),
    ("a = 1, b = a + 1", 2, {"n": "3", "a": "1", "b": "2"}),
    ("n == 3", 1, {"n": "3"}),
    ("n <= 2", 0, {"n": "3"}),
    ("n != 3", 0, {"n": "3"}),
    ("1 + 2 * 3", 7, {"n": "3"}),
])
def test_execute(text, result, after):
    variables = {"n": "3"}
    assert arith.execute(text, variables, lambda name, value: variables.__setitem__(name, value)) == result
    assert variables == after


def test_execute_errors():
    with pytest.raises(arith.ArithError):
        arith.execute("n /= 0", {"n": "1"}, None)
    with pytest.raises(arith.ArithError):
        arith.execute("", {}, None)


# ── $(( )) assignments ───────────────────────────────────────────────────────

def test_arithmetic_expansion_assigns(ctx):
    assert out(ctx, "echo $((n=4)) $n") == "4 4\n"
    assert out(ctx, "echo $((n++)) $n $((++n)) $n") == "4 5 6 6\n"
    assert out(ctx, "echo $((n += 10, n * 2))") == "32\n"
    out(ctx, "x=$((y=7))")
    assert ctx.variables["x"] == "7" and ctx.variables["y"] == "7"


# ── (( )) ────────────────────────────────────────────────────────────────────

def test_double_parentheses_command(ctx):
    out(ctx, "n=3")
    assert out(ctx, "((n++)); echo $n") == "4\n"
    assert out(ctx, "(( n += 5 )); echo $n") == "9\n"
    assert out(ctx, "(( n > 2 )) && echo big") == "big\n"
    assert out(ctx, "(( n > 20 )) || echo small") == "small\n"
    assert out(ctx, "(( 0 )); echo $?") == "1\n"
    assert out(ctx, "(( 5 )); echo $?") == "0\n"
    assert out(ctx, "(( x = 5, y = x * 2 )); echo $x $y") == "5 10\n"
    assert out(ctx, "! (( 0 )) && echo negated") == "negated\n"


def test_double_parentheses_prints_nothing_and_reports_errors(ctx):
    assert out(ctx, "(( 1 + 1 ))") == ""
    result = out(ctx, "(( 1 / 0 )); echo status $?")
    assert "division by zero" in result and "status 1" in result


def test_double_parentheses_in_if_and_while(ctx):
    out(ctx, "a=3; b=2")
    assert out(ctx, "if (( a > b )); then echo yes; else echo no; fi") == "yes\n"
    assert out(ctx, "if (( a < b )); then echo yes; elif (( a == 3 )); then echo three; fi") == "three\n"
    assert out(ctx, "i=0; while (( i < 3 )); do echo w$i; ((i++)); done") == "w0\nw1\nw2\n"
    assert out(ctx, "i=3; until (( i == 0 )); do echo u$i; (( i -= 1 )); done") == "u3\nu2\nu1\n"


def test_a_single_parenthesis_condition_is_still_python(ctx):
    with pytest.raises(Exception, match="Python"):
        run_line("if (x) { echo }", ctx)


def test_variables_and_substitutions_expand_in_double_parentheses(ctx):
    out(ctx, "a=4; arr=3")
    assert out(ctx, "(( a * ${arr} == 12 )) && echo ok") == "ok\n"
    assert out(ctx, "(( a == $(echo 4) )) && echo sub") == "sub\n"


def test_double_parentheses_under_set_e(ctx, tmp_path):
    result = run_file(ctx, tmp_path, "set -e\n(( 0 ))\necho not reached\n")
    assert "not reached" not in result


def test_double_parentheses_in_a_file(ctx, tmp_path):
    text = "total=0\nfor ((i = 1; i <= 4; i++)); do\n  (( total += i ))\ndone\necho total=$total\n"
    assert run_file(ctx, tmp_path, text) == "total=10\n"


# ── C-style for ──────────────────────────────────────────────────────────────

def test_c_style_for(ctx):
    assert out(ctx, "for ((i=0;i<3;i++)); do echo i=$i; done") == "i=0\ni=1\ni=2\n"
    assert out(ctx, "for (( i = 10; i > 0; i -= 4 )); do echo $i; done") == "10\n6\n2\n"
    assert out(ctx, "for ((i=0, j=10; i<j; i+=4, j-=1)); do echo $i $j; done") == "0 10\n4 9\n"
    assert out(ctx, "for ((;;)); do echo once; break; done") == "once\n"


def test_c_style_for_break_and_continue(ctx):
    assert out(ctx, "for ((i=0;i<6;i++)); do (( i == 4 )) && break; (( i % 2 )) && continue; echo $i; done") == "0\n2\n"


def test_c_style_for_nested_and_piped(ctx):
    assert out(ctx, "for ((i=1;i<3;i++)); do for ((j=1;j<3;j++)); do echo $i$j; done; done") == "11\n12\n21\n22\n"
    assert out(ctx, "for ((i=0;i<3;i++)); do echo $i; done | wc -l").strip() == "3"


def test_c_style_for_needs_three_parts(ctx):
    with pytest.raises(Exception, match="for"):
        run_line("for ((i=0;i<3)); do echo; done", ctx)


# ── let ──────────────────────────────────────────────────────────────────────

def test_let(ctx):
    out(ctx, "n=1")
    assert out(ctx, "let n=n+1; echo $n") == "2\n"
    assert out(ctx, 'let "m = n * 10" n++; echo $n $m') == "3 20\n"
    assert out(ctx, 'let "n > 100"; echo $?') == "1\n"
    assert out(ctx, 'let "n > 1"; echo $?') == "0\n"
    with pytest.raises(PipelineError, match="expression expected"):
        run_line("let", ctx)


# ── readonly and declare ─────────────────────────────────────────────────────

def test_readonly_refuses_changes_and_unset(ctx):
    out(ctx, "readonly PI=3")
    with pytest.raises(PipelineError, match="PI: readonly variable"):
        run_line("PI=4", ctx)
    with pytest.raises(PipelineError, match="readonly"):
        run_line("unset PI", ctx)
    assert out(ctx, "echo $PI") == "3\n"
    with pytest.raises(PipelineError, match="readonly"):
        run_line("let PI=5", ctx)
    with pytest.raises(PipelineError, match="readonly"):
        run_line("export PI=9", ctx)
    with pytest.raises(PipelineError, match="readonly"):
        run_line("read PI <<< x", ctx)
    assert "readonly" in out(ctx, "f() { local PI=1; }; f")


def test_readonly_without_a_value_and_listing(ctx):
    out(ctx, "x=7; readonly x")
    with pytest.raises(PipelineError):
        run_line("x=8", ctx)
    assert out(ctx, "readonly -p") == 'declare -r x="7"\n'
    assert out(ctx, "readonly") == 'declare -r x="7"\n'
    with pytest.raises(PipelineError, match="valid identifier"):
        run_line("readonly 1x", ctx)


def test_declare_integer(ctx):
    assert out(ctx, "declare -i n=2+3; echo $n") == "5\n"
    assert out(ctx, "n=n*2; echo $n") == "10\n"
    assert out(ctx, "n=7; echo $n") == "7\n"
    out(ctx, "declare -i z; z=")
    assert ctx.variables["z"] == "0"
    with pytest.raises(PipelineError, match="n:"):
        run_line("n=1/0", ctx)


def test_declare_readonly_export_and_print(ctx):
    out(ctx, "declare -r K=1")
    with pytest.raises(PipelineError):
        run_line("K=2", ctx)
    out(ctx, "declare -ix Z=7")
    assert ctx.exported["Z"] == "7"
    assert out(ctx, "declare -p Z") == 'declare -ix Z="7"\n'
    assert 'declare -r K="1"' in out(ctx, "declare -p")
    with pytest.raises(Exception, match="not found"):
        run_line("declare -p nonesuch", ctx)
    with pytest.raises(Exception, match="not supported"):
        run_line("declare -u arr", ctx)


def test_declare_functions_still_work(ctx):
    out(ctx, "hello() { echo hi; }")
    assert out(ctx, "declare -F") == "declare -f hello\n"
    assert "hello() { echo hi; }" in out(ctx, "declare -f hello")


# ── $RANDOM and $SECONDS ─────────────────────────────────────────────────────

def test_random_is_in_range_and_changes(ctx):
    values = {int(out(ctx, "echo $RANDOM")) for _ in range(30)}
    assert all(0 <= v <= 32767 for v in values) and len(values) > 5
    assert out(ctx, "echo $((RANDOM % 6 + 1))").strip() in {"1", "2", "3", "4", "5", "6"}


def test_seeding_random_repeats(ctx):
    out(ctx, "RANDOM=42")
    first = out(ctx, "echo $RANDOM $RANDOM")
    out(ctx, "RANDOM=42")
    assert out(ctx, "echo $RANDOM $RANDOM") == first


def test_seconds_counts_and_resets(ctx):
    assert out(ctx, "echo $SECONDS") == "0\n"
    out(ctx, "SECONDS=100")
    assert int(out(ctx, "echo $SECONDS")) in (100, 101)
    out(ctx, "SECONDS=0")
    assert out(ctx, "echo $SECONDS") == "0\n"


# ── printf -v and [ -t ] ─────────────────────────────────────────────────────

def test_printf_v_sets_a_variable(ctx):
    assert out(ctx, "printf -v padded %05d 42; echo $padded") == "00042\n"
    assert out(ctx, 'printf -v line "%s-%s" a b; echo "$line"') == "a-b\n"
    assert out(ctx, "printf '%s\\n' plain") == "plain\n"
    with pytest.raises(Exception, match="usage"):
        run_line("printf -v", ctx)


def test_printf_v_in_a_piped_block_sets_its_copy(ctx):
    out(ctx, "v=old")
    assert out(ctx, "echo a | while read x; do printf -v v new-$x; echo $v; done") == "new-a\n"
    assert ctx.variables["v"] == "old"  # a piped block runs on a copy, as in bash


def test_printf_v_is_a_write_for_mcp():
    from shellcraft.builtins import BUILTINS

    printf = BUILTINS["printf"]
    assert printf.writes_if is not None and printf.writes_if(["-v", "x", "%s", "1"])
    assert not printf.writes_if(["%s\\n", "1"]) and not printf.stateful


def test_test_dash_t(ctx):
    assert out(ctx, "[ -t 99 ]; echo $?") == "1\n"
    assert out(ctx, "[ -t x ]; echo $?") == "1\n"
    assert out(ctx, "[ ! -t 99 ] && echo notty") == "notty\n"
    import os
    fd = os.open(os.devnull, os.O_RDONLY)
    try:
        assert out(ctx, f"[ -t {fd} ]; echo $?") == "1\n"
    finally:
        os.close(fd)
