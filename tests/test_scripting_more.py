"""Shell variables, arithmetic, while / until / case, piped blocks, and the new redirections."""

import os

import pytest

from shellcraft.arith import ArithError, evaluate
from shellcraft.context import to_text
from shellcraft.parser import ParseError, parse
from shellcraft.pipeline import PipelineError, run_line


def out(ctx, line):
    result = run_line(line, ctx)
    return "" if result is None else to_text(result.output)


# ── variables ────────────────────────────────────────────────────────────────

def test_assignment_and_expansion_on_plain_lines(ctx):
    assert out(ctx, "x=5") == ""
    assert out(ctx, "echo $x ${x}0") == "5 50\n"
    assert out(ctx, 'y="a b"') == ""
    assert out(ctx, "echo $y | wc -w") == "2\n"  # never split on a plain line
    assert out(ctx, "for w in $y; do echo $w; done") == "a\nb\n"  # split inside bash blocks
    assert ctx.variables == {"x": "5", "y": "a b", "w": "b"}


def test_assignment_with_spaces_must_be_quoted(ctx):
    with pytest.raises(ParseError, match="quote a value"):
        run_line("z=a b", ctx)


def test_assignment_from_substitution_and_arithmetic(ctx):
    assert out(ctx, "n=$(echo 4); m=$((n * 2)); echo $m") == "8\n"


def test_environment_expands_only_where_sensitive_output_is_allowed(ctx, monkeypatch):
    monkeypatch.setenv("SC_TEST_SECRET", "hunter2")
    assert out(ctx, "echo $SC_TEST_SECRET") == "hunter2\n"
    ctx.allow_sensitive = False  # MCP
    assert out(ctx, "echo $SC_TEST_SECRET") == "$SC_TEST_SECRET\n"
    ctx.variables["SC_TEST_SECRET"] = "own"
    assert out(ctx, "echo $SC_TEST_SECRET") == "own\n"


def test_export_and_unset(ctx, monkeypatch):
    monkeypatch.delenv("SC_EXPORTED", raising=False)
    out(ctx, "export SC_EXPORTED=yes")
    assert os.environ["SC_EXPORTED"] == "yes" and ctx.variables["SC_EXPORTED"] == "yes"
    out(ctx, "SC_EXPORTED=changed")
    assert os.environ["SC_EXPORTED"] == "changed"  # an exported variable follows its assignments
    assert "SC_EXPORTED" in out(ctx, "export")
    out(ctx, "unset SC_EXPORTED")
    assert "SC_EXPORTED" not in os.environ and "SC_EXPORTED" not in ctx.variables
    with pytest.raises(PipelineError, match="not a valid identifier"):
        run_line("export 1x=2", ctx)


# ── arithmetic ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("expr, value", [
    ("1 + 2 * 3", 7), ("7 / 2", 3), ("-7 / 2", -3), ("7 % 3", 1), ("2 ** 10", 1024), ("(1 + 2) * 3", 9),
    ("3 > 2", 1), ("3 == 2", 0), ("1 && 0", 0), ("1 || 0", 1), ("!0", 1), ("5 & 3", 1), ("1 << 4", 16),
    ("n + 1", 6), ("missing + 1", 1), ("n > 3 ? 10 : 20", 10), ("0x10 + 1", 17),
])
def test_arithmetic(expr, value):
    assert evaluate(expr, {"n": "5"}) == value


@pytest.mark.parametrize("expr, message", [
    ("1 / 0", "division by zero"), ("2 ** 99999999", "too large"), ("1.5 + 1", "only integers"),
    ("len(x)", "not allowed"), ("", "empty"), ("1 +", "invalid"),
])
def test_arithmetic_errors(expr, message):
    with pytest.raises(ArithError, match=message):
        evaluate(expr, {})


def test_arithmetic_in_a_line(ctx):
    assert out(ctx, "echo $((2 + 3)) $(( 10 / 3 ))") == "5 3\n"
    with pytest.raises(ParseError, match="division by zero"):
        run_line("echo $((1 / 0))", ctx)


# ── while / until / case ─────────────────────────────────────────────────────

def test_while_and_until(ctx):
    assert out(ctx, "n=0; while [ $n -lt 3 ]; do echo $n; n=$((n+1)); done") == "0\n1\n2\n"
    assert ctx.last_status == 0 and ctx.variables["n"] == "3"
    assert out(ctx, "until [ $n -le 1 ]; do n=$((n-1)); done; echo $n") == "1\n"
    assert out(ctx, "n=0; while [ $n -lt 9 ]; do n=$((n+1)); if [ $n = 2 ]; then break; fi; echo $n; done") == "1\n"


def test_while_py(ctx):
    assert out(ctx, "n=0; while -py (int(n) < 2) { echo n=$n; n=$((n+1)) }") == "n=0\nn=1\n"
    assert out(ctx, "until -py (int(n) == 0) { n=$((n-1)) }; echo $n") == "0\n"


def test_while_with_a_command_condition(ctx, tmp_path):
    (tmp_path / "f").write_text("x\n")
    assert out(ctx, "i=0; while grep -q x f; do i=$((i+1)); echo gone > f; done; echo $i") == "1\n"


def test_case(ctx):
    assert out(ctx, "case abc in a*) echo starts-a;; *) echo other;; esac") == "starts-a\n"
    assert out(ctx, "case zzz in a*) echo starts-a;; *) echo other;; esac") == "other\n"
    assert out(ctx, "case zzz in a*) echo starts-a;; esac") == ""
    line = "for f in x.py y.txt z; do case $f in *.py) echo py $f;; *.txt|*.md) echo doc $f;; esac; done"
    assert out(ctx, line) == "py x.py\ndoc y.txt\n"
    assert out(ctx, 'v="a b"; case "$v" in "a b") echo quoted;; esac') == "quoted\n"
    assert out(ctx, "case x in (x) echo paren;; esac") == "paren\n"


def test_case_multiline_and_errors(ctx):
    assert out(ctx, "case b in\n  a) echo A ;;\n  b)\n    echo B\n    ;;\nesac") == "B\n"
    with pytest.raises(ParseError, match="expected 'in'"):
        run_line("case x y) echo ;; esac", ctx)


@pytest.mark.parametrize("line", ["while [ 1 ]; do echo x", "case x in", "case x in a) echo"])
def test_while_and_case_continue_on_the_next_line(line):
    from shellcraft.script import incomplete

    assert incomplete(line) is not None


# ── piped and redirected blocks ──────────────────────────────────────────────

def test_a_whole_loop_can_be_piped_and_redirected(ctx, tmp_path):
    assert out(ctx, "for i in 3 1 2; do echo $i; done | sort") == "1\n2\n3\n"
    assert out(ctx, "for -py i in 3 1 2 { echo $i } | sort -r") == "3\n2\n1\n"
    out(ctx, "for i in a b; do echo $i; done > loop.txt")
    assert (tmp_path / "loop.txt").read_text() == "a\nb\n"
    assert out(ctx, "if true; then echo yes; fi | tr a-z A-Z") == "YES\n"
    assert out(ctx, "n=0; while [ $n -lt 3 ]; do echo $n; n=$((n+1)); done | wc -l") == "3\n"
    assert ctx.variables["n"] == "3"  # not a subshell: the variable survives


def test_a_piped_loop_reports_a_bad_tail(ctx):
    with pytest.raises(ParseError, match="command not found|empty pipeline|expected"):
        run_line("for i in a; do echo $i; done | | sort", ctx)


# ── redirections ─────────────────────────────────────────────────────────────

def test_input_redirect(ctx, tmp_path):
    (tmp_path / "in.txt").write_text("b\na\n")
    assert out(ctx, "sort < in.txt") == "a\nb\n"
    with pytest.raises(PipelineError, match="cannot read"):
        run_line("sort < missing.txt", ctx)
    with pytest.raises(ParseError, match="first command"):
        parse("a | b < in.txt")


def test_stderr_redirects(ctx, tmp_path):
    assert out(ctx, "cat nosuch 2>&1 | wc -l") == "1\n"
    assert out(ctx, "cat nosuch 2>&1") == "cat: nosuch: no such file\n" and ctx.last_status == 1
    out(ctx, "cat nosuch 2> err.txt")
    assert (tmp_path / "err.txt").read_text() == "cat: nosuch: no such file\n"
    out(ctx, "cat other 2>> err.txt")
    assert (tmp_path / "err.txt").read_text().count("\n") == 2
    out(ctx, "cat nosuch &> both.txt")
    assert (tmp_path / "both.txt").read_text() == "cat: nosuch: no such file\n"
    assert out(ctx, "echo fine 2> err2.txt") == "fine\n" and not (tmp_path / "err2.txt").exists()


def test_redirects_stay_blocked_without_permission(ctx):
    ctx.allow_redirect = False
    for line in ("cat x 2> e", "sort < f", "cat x &> f"):
        with pytest.raises(PipelineError, match="redirection is disabled"):
            run_line(line, ctx)


def test_globs_on_plain_lines(ctx, tmp_path):
    (tmp_path / "a.log").write_text("")
    (tmp_path / "b.log").write_text("")
    assert out(ctx, "echo *.log") == "a.log b.log\n"
    assert out(ctx, "echo '*.log' \"*.log\"") == "*.log *.log\n"
    assert out(ctx, "echo *.none") == "*.none\n"
    assert out(ctx, "echo a[0-9]") == "a[0-9]\n"  # no match: the regex-looking word stays


# ── history expansion ────────────────────────────────────────────────────────

def test_history_designators():
    from shellcraft.histexpand import expand

    history = ["ls -l", "cat f", "grep x y"]
    assert expand("!1", "grep x y", history) == ("ls -l", True)
    assert expand("!-2 | wc", "grep x y", history) == ("cat f | wc", True)
    assert expand("!ca", "grep x y", history) == ("cat f", True)
    assert expand("echo !*", "grep x y", history) == ("echo x y", True)
    assert expand("[[ ! -f x ]]", "grep x y", history) == ("[[ ! -f x ]]", False)
    with pytest.raises(ParseError, match="event not found"):
        expand("!zz", "grep x y", history)
    with pytest.raises(ParseError, match="event not found"):
        expand("!9", "grep x y", history)
