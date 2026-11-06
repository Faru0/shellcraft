"""Brace expansion on plain lines, backticks, and `VAR=x cmd` (SCRIPTING-TODO item 19)."""

import os

import pytest

from shellcraft.parser import ParseError, rewrite_backticks, tokenize
from shellcraft.pipeline import PipelineError, run_line


def out(ctx, line):
    result = run_line(line, ctx)
    return "" if result is None or result.output is None else (
        result.output if isinstance(result.output, str) else result.output.text)


# ── braces ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("line, expected", [
    ("echo {a,b}{1,2}", "a1 a2 b1 b2\n"),
    ("echo {1..4}", "1 2 3 4\n"),
    ("echo {01..03}", "01 02 03\n"),
    ("echo {a..d}", "a b c d\n"),
    ("echo {0..10..5}", "0 5 10\n"),
    ("echo x{,y}z", "xz xyz\n"),
    ("echo pre{a,b{1,2}}post", "preapost preb1post preb2post\n"),
    ("echo '{a,b}' \"{a,b}\"", "{a,b} {a,b}\n"),
    ("echo ${x:-{a,b}}", "{a,b}\n"),
    ("echo {a} {} a{b}c", "{a} {} a{b}c\n"),
    ("echo {a,b", "{a,b\n"),
    ("echo $(echo {a,b})", "a b\n"),
    ("echo {a,b}$(echo x)", "ax bx\n"),
    ("echo \"x y\"{1,2}", "x y1 x y2\n"),
])
def test_braces_expand_on_plain_lines(ctx, line, expected):
    assert out(ctx, line) == expected


def test_braces_make_files_and_a_range_in_a_path(ctx, tmp_path):
    out(ctx, "mkdir -p proj/{src,tests}")
    assert (tmp_path / "proj" / "src").is_dir() and (tmp_path / "proj" / "tests").is_dir()
    out(ctx, "touch f{1..3}.txt")
    assert out(ctx, "ls f*.txt").split() == ["f1.txt", "f2.txt", "f3.txt"]


def test_braces_are_left_alone_for_jq_and_in_a_variable(ctx):
    assert [t.value for t in tokenize("jq {a,b}", {}, glob=True)] == ["jq", "{a,b}"]
    out(ctx, "v='{a,b}'")
    assert out(ctx, "echo $v") == "{a,b}\n"  # a value is not brace-expanded, as in bash


def test_braces_in_a_block_and_a_pipe(ctx):
    assert out(ctx, "for x in 1 2; do echo {a,b}$x; done") == "a1 b1\na2 b2\n"
    assert out(ctx, "echo {1..3} | wc -w").strip() == "3"


def test_a_range_that_is_too_big_is_an_error(ctx):
    with pytest.raises(ParseError):
        run_line("echo {1..1000000}", ctx)


# ── backticks ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, expected", [
    ("echo `echo hi`", "echo $(echo hi)"),
    ('echo "a `echo hi` b"', 'echo "a $(echo hi) b"'),
    ("echo '`echo hi`'", "echo '`echo hi`'"),
    ("echo \\`x\\`", "echo \\`x\\`"),
    ("echo `echo \\$x`", "echo $(echo $x)"),
    ("echo hi # `not run`", "echo hi # `not run`"),
    ("echo `oops", "echo `oops"),
])
def test_rewrite_backticks(text, expected):
    assert rewrite_backticks(text) == expected


def test_backticks_work_like_dollar_parentheses(ctx):
    assert out(ctx, "echo `echo hi`") == "hi\n"
    assert out(ctx, 'echo "a `echo there` b"') == "a there b\n"
    assert out(ctx, "echo '`echo hi`'") == "`echo hi`\n"
    assert out(ctx, "x=`echo 5`; echo $x") == "5\n"
    assert out(ctx, "echo `echo a; echo b`") == "a\nb\n"  # a `;` inside belongs to the substitution; no word splitting on a plain line
    assert out(ctx, "echo a `echo b | tr b c` d") == "a c d\n"
    assert out(ctx, "for f in `echo 1 2`; do echo n$f; done") == "n1\nn2\n"


def test_an_unbalanced_backtick_is_an_error(ctx):
    with pytest.raises(ParseError):
        run_line("echo `echo hi", ctx)
    with pytest.raises(ParseError):
        run_line('echo "a `echo hi"', ctx)


# ── VAR=x cmd ────────────────────────────────────────────────────────────────

def test_a_prefix_assignment_is_for_that_command_only(ctx):
    out(ctx, "f() { echo \"[$FOO]\"; }")
    assert out(ctx, "FOO=1 f") == "[1]\n"
    assert out(ctx, "echo \"[$FOO]\"") == "[]\n"
    out(ctx, "FOO=old")
    assert out(ctx, "FOO=new f") == "[new]\n"
    assert ctx.variables["FOO"] == "old"  # put back, not removed


def test_a_prefix_assignment_reaches_the_environment_and_is_undone(ctx, monkeypatch):
    monkeypatch.delenv("SC_PREFIX", raising=False)
    seen = []

    def spy(c, args, stdin):
        seen.append(os.environ.get("SC_PREFIX"))
        return ""

    from shellcraft import builtins

    monkeypatch.setitem(builtins.BUILTINS, "spy", builtins.Builtin(spy, "spy", "spy"))
    out(ctx, "SC_PREFIX=1 spy")
    assert seen == ["1"] and "SC_PREFIX" not in os.environ and "SC_PREFIX" not in ctx.variables


def test_several_prefixes_and_quoted_values(ctx):
    out(ctx, "f() { echo \"$A-$B\"; }")
    assert out(ctx, 'A=1 B="x y" f') == "1-x y\n"
    assert out(ctx, "A=$(echo 9) f") == "9-\n"


def test_a_prefix_is_undone_when_the_command_fails(ctx):
    with pytest.raises(PipelineError):
        run_line("FOO=1 nonesuch_command", ctx)
    assert "FOO" not in ctx.variables


def test_a_prefix_in_each_pipe_segment(ctx):
    out(ctx, "f() { echo \"$A\"; }")
    assert out(ctx, "A=1 f | A=2 tr 1 x") == "x\n"


def test_assignments_alone_are_still_assignments(ctx):
    out(ctx, "a=1 b=2")
    assert (ctx.variables["a"], ctx.variables["b"]) == ("1", "2")
    out(ctx, 'c="x y" d=$a')
    assert (ctx.variables["c"], ctx.variables["d"]) == ("x y", "1")
    assert out(ctx, "echo $a$b") == "12\n"


def test_ifs_prefix_with_read(ctx):
    out(ctx, "IFS=: read -r a b <<< 'x:y z'")
    assert (ctx.variables["a"], ctx.variables["b"]) == ("x", "y z")
    assert "IFS" not in ctx.variables
    out(ctx, "IFS= read -r line <<< '  keep  '")
    assert ctx.variables["line"] == "  keep  "


def test_ifs_prefix_in_a_while_read_loop(ctx, tmp_path):
    (tmp_path / "f").write_text("  a b\n c\n")
    assert out(ctx, "while IFS= read -r l; do echo \"[$l]\"; done < f") == "[  a b]\n[ c]\n"
