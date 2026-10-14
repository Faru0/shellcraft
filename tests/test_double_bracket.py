"""`&&`, `||`, `!` and ( ) inside `[[ ]]`, with bash precedence, and the shell's own && / ||
around a `[[ ]]` test (the splitter must not cut a statement at an `&&` inside the brackets)."""

import pytest

from shellcraft import script
from shellcraft.context import to_text
from shellcraft.parser import tokenize
from shellcraft.pipeline import run_line


@pytest.fixture
def files(ctx, tmp_path):
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "empty").write_text("")
    return ctx


@pytest.mark.parametrize("test, status", [
    ('[[ -f a.txt && -z "" ]]', 0),
    ("[[ -f nope && -f a.txt ]]", 1),
    ("[[ -f nope || 1 -lt 2 ]]", 0),
    ("[[ -f nope || 1 -gt 2 ]]", 1),
    ("[[ a == a && b == c || x =~ ^x$ ]]", 0),  # (a && b) || c
    ("[[ a == b || b == b && c == d ]]", 1),  # a || (b && c)
    ("[[ ! ( a == a && b == c ) ]]", 0),
    ("[[ ( -f a.txt || -f nope ) && -n x ]]", 0),
    ("[[ -s empty || -d nope ]]", 1),
    ("[[ 3 -lt 5 && 5 -le 5 && x != y ]]", 0),
    ("[[ a.txt == *.txt && ! -d a.txt ]]", 0),
])
def test_and_or_not_and_grouping(files, test, status):
    run_line(test, files)
    assert files.last_status == status


def test_shell_and_or_around_a_bracket_test(files):
    assert to_text(run_line("[[ -n x && -f a.txt ]] && echo yes || echo no", files).output) == "yes\n"
    assert to_text(run_line("x=5; [[ $x -gt 3 && $x -lt 10 ]] && echo in-range", files).output) == "in-range\n"
    assert to_text(run_line('if [[ -f a.txt && -n "x" ]]; then echo ok; fi', files).output) == "ok\n"
    out = run_line("for f in a.txt empty; do [[ -f $f && -s $f ]] && echo $f; done", files).output
    assert to_text(out) == "a.txt\n"
    assert to_text(run_line("true && [[ a == a || b == b ]] && echo chained", files).output) == "chained\n"


def test_splitter_and_tokenizer_keep_the_brackets_whole():
    program = script.parse_script("[[ a == a && b == b ]] && echo yes")
    chain = program.stmts[0]
    assert [part.text for part in chain.parts] == ["[[ a == a && b == b ]]", "echo yes"]
    assert [t.value for t in tokenize("[[ a || b ]]")] == ["[[", "a", "||", "b", "]]"]
    with pytest.raises(Exception, match="not supported"):
        tokenize("echo a && echo b")  # outside [[ ]] the shell splits on them first


# ── text order and Python-style comparisons ──────────────────────────────────

@pytest.mark.parametrize("test, expected", [
    ("[[ apple < banana ]]", True), ("[[ banana < apple ]]", False),
    ("[[ b > a ]]", True), ("[[ 10 < 9 ]]", True),  # text order, as in bash
    ("[[ abc <= abc ]]", True), ("[[ abd >= abc ]]", True), ("[[ abc >= abd ]]", False),
    ("[[ ell in hello ]]", True), ("[[ xyz in hello ]]", False),
    ("[[ xyz not in hello ]]", True), ("[[ ell not in hello ]]", False),
    ("[[ hello is hello ]]", True), ("[[ hello is not bye ]]", True), ("[[ h* is hello ]]", False),  # no patterns
    ("[[ -n in ]]", True), ("[[ -n is ]]", True),  # a lone word after -n is still a string
    ("[[ a < b && c in abc || x > z ]]", True), ("[[ ! ( a > b ) ]]", True),
])
def test_text_order_and_python_comparisons(ctx, test, expected):
    run_line(test, ctx)
    assert (ctx.last_status == 0) is expected


def test_comparisons_in_if_and_loops_and_redirects_outside(ctx, tmp_path):
    out = lambda line: to_text(run_line(line, ctx).output)  # noqa: E731
    assert out('if [[ $USER_X < m ]]; then echo low; else echo high; fi'.replace("$USER_X", "alpha")) == "low\n"
    assert out('for w in pear apple; do [[ $w < b ]] && echo "$w first"; done') == "apple first\n"
    assert out('line="disk error"; [[ error in $line ]] && echo found') == "found\n"
    run_line(f"echo kept > {tmp_path / 'r.txt'}", ctx)  # outside [[ ]], > is still a redirection
    assert (tmp_path / "r.txt").read_text() == "kept\n"
    assert tokenize("[[ a <= b ]]")[2].value == "<=" and not tokenize("[[ a <= b ]]")[2].op
