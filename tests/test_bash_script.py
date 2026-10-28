"""Bash-syntax for / if (the default), &&, ||, !, $( … ), brace expansion and test / [ / [[."""

import pytest

from shellcraft import script
from shellcraft.context import to_text
from shellcraft.parser import ParseError, parse, tokenize
from shellcraft.pipeline import PipelineError, run_line
from shellcraft.script import brace_expand, expand_sh_word


def out(ctx, line):
    result = run_line(line, ctx)
    return "" if result is None or result.output is None else (
        result.output if isinstance(result.output, str) else result.output.text)


# ── word splitting and substitution in the tokenizer ─────────────────────────

def test_split_mode_splits_unquoted_expansions_only():
    v = {"x": "a  b", "e": ""}
    assert parse('echo $x "$x" p$x.q $e "$e"', v, split=True).segments[0].args == ["a", "b", "a  b", "pa", "b.q", ""]
    assert parse("echo $x", v).segments[0].args == ["a  b"]  # without split: one word, as before


def test_substitution_strips_trailing_newlines_and_splits_unquoted():
    sub = lambda cmd: f"<{cmd}> 2\n\n"  # noqa: E731
    args = parse('echo $(a | b) "$(c d)"', split=True, substitute=sub).segments[0].args
    assert args == ["<a", "|", "b>", "2", "<c d> 2"]


# ── brace expansion and for-list words ───────────────────────────────────────

@pytest.mark.parametrize("word, expected", [
    ("{1..3}", ["1", "2", "3"]),
    ("{3..1}", ["3", "2", "1"]),
    ("{0..10..5}", ["0", "5", "10"]),
    ("{01..03}", ["01", "02", "03"]),
    ("{a..c}", ["a", "b", "c"]),
    ("x{a,b}y", ["xay", "xby"]),
    ("{a,b}{1,2}", ["a1", "a2", "b1", "b2"]),
    ("{a,{b,c}}", ["a", "b", "c"]),
    ("{x}", ["{x}"]),
    ("'{a,b}'", ["'{a,b}'"]),
    ("${x}", ["${x}"]),
])
def test_brace_expand(word, expected):
    assert brace_expand(word) == expected


def test_list_words_split_quote_and_glob(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("a.log", "b.log"):
        (tmp_path / name).write_text("")
    v = {"x": "p q", "?": "0"}
    sub = lambda cmd: "l1\nl2 l3\n"  # noqa: E731
    assert expand_sh_word("$x", v, sub) == ["p", "q"]
    assert expand_sh_word('"$x"', v, sub) == ["p q"]
    assert expand_sh_word("$(cat f)", v, sub) == ["l1", "l2", "l3"]
    assert expand_sh_word('"$(cat f)"', v, sub) == ["l1\nl2 l3"]
    assert expand_sh_word("*.log", v, sub) == ["a.log", "b.log"]
    assert expand_sh_word('"*.log"', v, sub) == ["*.log"]
    assert expand_sh_word("*.none", v, sub) == ["*.none"]
    assert expand_sh_word('""', v, sub) == [""]
    assert expand_sh_word("$nope", v, sub) == []  # unset: empty, so no word at all (as in bash)


# ── for / if ─────────────────────────────────────────────────────────────────

def test_for_do_done(ctx):
    assert out(ctx, 'for x in a "b c" {1..2}; do echo "[$x]"; done') == "[a]\n[b c]\n[1]\n[2]\n"


def test_for_body_splits_unquoted_variables_like_bash(ctx):
    assert out(ctx, 'for v in "a  b"; do echo $v; echo "$v"; done') == "a b\na  b\n"


def test_for_over_command_substitution(ctx, tmp_path):
    (tmp_path / "hosts.txt").write_text("a.com b.com\nc.com\n")
    assert out(ctx, "for h in $(cat hosts.txt); do echo host=$h; done") == "host=a.com\nhost=b.com\nhost=c.com\n"
    assert out(ctx, 'for h in "$(cat hosts.txt)"; do echo "[$h]"; done') == "[a.com b.com\nc.com]\n"


def test_for_globs(ctx, tmp_path):
    for name in ("b.log", "a.log"):
        (tmp_path / name).write_text("")
    assert out(ctx, "for f in *.log; do echo $f; done") == "a.log\nb.log\n"


def test_multiline_bash_script(ctx):
    text = "for n in 1 2 3\ndo\n  if [ $n -eq 2 ]\n  then\n    continue\n  fi\n  echo $n\ndone"
    assert out(ctx, text) == "1\n3\n"


def test_if_then_elif_else_fi(ctx):
    line = ('for n in {1..4}; do if [ $n -eq 1 ]; then echo one; elif [ $n -lt 3 ]; then echo two; '
            'else echo many $n; fi; done')
    assert out(ctx, line) == "one\ntwo\nmany 3\nmany 4\n"


def test_if_condition_is_a_list_and_shows_its_output(ctx):
    assert out(ctx, "if echo checking; [ 1 -eq 1 ]; then echo yes; fi") == "checking\nyes\n"


def test_if_with_grep_and_negation(ctx, tmp_path):
    (tmp_path / "a.log").write_text("ERROR x\n")
    (tmp_path / "b.log").write_text("fine\n")
    assert out(ctx, "for f in *.log; do if grep -q ERROR $f; then echo $f; fi; done") == "a.log\n"
    assert out(ctx, "for f in *.log; do if ! grep -q ERROR $f; then echo clean $f; fi; done") == "clean b.log\n"


def test_break_continue_and_nesting(ctx):
    line = "for a in 1 2 3; do for b in x y; do if [ $b = y ]; then break; fi; echo $a$b; done; done"
    assert out(ctx, line) == "1x\n2x\n3x\n"


def test_substitution_in_conditions_and_commands(ctx):
    assert out(ctx, 'for x in a; do if [ "$(echo hi)" = hi ]; then echo ok $(echo  two  words); fi; done') \
        == "ok two words\n"


def test_py_blocks_still_available_with_flag(ctx):
    assert out(ctx, 'for -py ip in 10.1 8.8 { if (ip.startswith("10")) { echo in $ip } else { echo out $ip } }') \
        == "in 10.1\nout 8.8\n"
    assert out(ctx, "for x in a b; do if -py (x == 'b') { echo got $x }; done") == "got b\n"


# ── && || ! ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("line, expected, status", [
    ("echo a && echo b", "a\nb\n", 0),
    ("false && echo b", "", 1),
    ("false || echo c", "c\n", 0),
    ("true && false || echo c", "c\n", 0),
    ("true || echo never && echo then", "then\n", 0),
    ("! false && echo negated", "negated\n", 0),
    ("! true", "", 1),
])
def test_and_or_not(ctx, line, expected, status):
    assert out(ctx, line) == expected and ctx.last_status == status


def test_and_or_reports_errors_and_goes_on(ctx):
    text = out(ctx, "nosuch || echo fallback")
    assert text.startswith("✗ nosuch") and text.endswith("fallback\n")


# ── test, [, [[ ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("line, status", [
    ("[ a = a ]", 0), ("[ a = b ]", 1), ("[ a != b ]", 0), ("[ -z '' ]", 0), ("[ -n '' ]", 1), ("[ x ]", 0),
    ("[ '' ]", 1), ("[ ]", 1), ("[ 2 -lt 10 ]", 0), ("[ 10 -le 9 ]", 1), ("[ 3 -eq 3 -a 4 -ne 4 ]", 1),
    ("[ 3 -eq 3 -o 4 -ne 4 ]", 0), ("[ ! -e nope ]", 0), ("[ -d . ]", 0), ("[ -f . ]", 1),
    ("[ ( a = a ) -a ( b = b ) ]", 0), ("test 5 -gt 1", 0), ("test", 1),
    ("[[ app.log == *.log ]]", 0), ("[[ app.txt == *.log ]]", 1), ("[[ ab != a* ]]", 1),
    ("[[ v1.22 =~ ^v1\\.[0-9]+$ ]]", 0), ("[[ abc =~ ^z ]]", 1), ("true", 0), ("false", 1),
])
def test_conditions(ctx, line, status):
    run_line(line, ctx)
    assert ctx.last_status == status


@pytest.mark.parametrize("line, message", [
    ("[ a = a", "missing ']'"),
    ("[[ a == a", "missing ']]'"),
    ("[ x -lt 3 ]", "integer expression expected"),
    ("[ a = ]", "argument expected"),
])
def test_condition_errors_are_status_2(ctx, line, message):
    with pytest.raises(PipelineError) as info:
        run_line(line, ctx)
    assert message in info.value.message and ctx.last_status == 2


def test_double_brackets_never_split_variables(ctx):
    assert out(ctx, 'for v in "a b"; do [[ $v == "a b" ]] && echo same; done') == "same\n"


# ── syntax ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("line, message", [
    ("for x in a b { echo $x }", "for braces use `for -py"),
    ("if (1 == 1) { echo y }", "use `if -py"),
    ("if grep -q x f { echo y }", "for braces use `if -py"),
    ("for x; do echo; done", "give the list"),
    ("for x in a b do echo; done", "before 'do'"),
    ("for x in a; echo; done", "expected 'do'"),
    ("done", "without a matching for"),
    ("fi", "without a matching if"),
    ("if then echo; fi", "missing a condition"),
    ("for x in $(echo a |); do echo; done", "pipeline ends with '|'"),
])
def test_bash_syntax_errors(ctx, line, message):
    with pytest.raises(ParseError) as info:
        run_line(line, ctx)
    assert message in info.value.message


@pytest.mark.parametrize("text, more, joined", [
    ("for x in a b", "do", "for x in a b ; do"),
    ("for x in a b; do", "echo $x", "for x in a b; do echo $x"),
    ("for x in a b; do echo $x", "done", "for x in a b; do echo $x ; done"),
    ("if [ 1 ]", "then", "if [ 1 ] ; then"),
    ("if [ 1 ]; then", "echo y", "if [ 1 ]; then echo y"),
    ("if [ 1 ]; then echo y", "else", "if [ 1 ]; then echo y ; else"),
    ("true &&", "echo y", "true && echo y"),
    ("for -py x in a {", "echo $x", "for -py x in a { echo $x"),
    ("for -py x in a { if -py (x) { echo }", "else { echo }", "for -py x in a { if -py (x) { echo } else { echo }"),
])
def test_continuation_join(text, more, joined):
    assert script.incomplete(text) is not None
    assert script.join_continuation(text, more) == joined


def test_plain_lines_expand_substitutions_and_globs_but_not_braces(ctx):
    assert out(ctx, "echo $(echo hi) {a,b} *.x") == "hi {a,b} *.x\n"  # no brace expansion outside blocks
    assert out(ctx, "echo '$(echo hi)' \"$(echo there)\"") == "$(echo hi) there\n"


# ── && || ! ( ) inside [[ ]] ──────────────────────────────────────────────────

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


def test_unset_variables_are_empty_and_quoted_patterns_are_text(ctx):
    from shellcraft.context import to_text
    from shellcraft.pipeline import run_line

    def out(line):
        result = run_line(line, ctx)
        return to_text(result.output) if result else ""

    assert out('echo "[$nope]" $#') == "[] 0\n"  # as in bash: empty, and no arguments outside a function
    assert out('[ -z "$nope" ] && echo empty') == "empty\n"
    out('f=data.csv; pat="*.csv"')
    assert out("[[ $f == *.csv ]] && echo y || echo n") == "y\n"
    assert out('[[ $f == "*.csv" ]] && echo y || echo n') == "n\n"  # quoted: plain text
    assert out('[[ "*.csv" == "*.csv" ]] && echo y || echo n') == "y\n"
    assert out("[[ $f == $pat ]] && echo y || echo n") == "y\n"  # unquoted variable: a pattern
    assert out('[[ $f == "$pat" ]] && echo y || echo n') == "n\n"
    assert out('[[ $f == "data".* ]] && echo y || echo n') == "y\n"  # only the quoted part is text
    assert out('[[ $f != "*.csv" ]] && echo y || echo n') == "y\n"
    assert out("[[ $f = d*.csv ]] && echo y || echo n") == "y\n"
