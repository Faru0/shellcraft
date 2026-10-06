"""for loops, if conditions, `;` sequences, `$?` and exit statuses."""

import pytest

from core import script
from core.parser import ParseError, parse
from core.pipeline import PipelineError, run_line


def out(ctx, line):
    result = run_line(line, ctx)
    return "" if result is None or result.output is None else (
        result.output if isinstance(result.output, str) else result.output.text)


# ── variables in the tokenizer ───────────────────────────────────────────────

def test_variables_expand_outside_single_quotes_and_never_split():
    pipeline = parse("""echo $x "[$x]" '$x' ${x}! $? $HOME""", {"x": "a b; c", "?": "3"})
    assert pipeline.segments[0].args == ["a b; c", "[a b; c]", "$x", "a b; c!", "3", "$HOME"]


def test_empty_unquoted_variable_is_no_argument():
    assert parse('echo $e "$e" x', {"e": ""}).segments[0].args == ["", "x"]


def test_without_variables_dollar_is_literal():
    assert parse("echo $? $x").segments[0].args == ["$?", "$x"]


# ── exit status ──────────────────────────────────────────────────────────────

def test_status_of_success_failure_and_not_found(ctx):
    run_line("echo ok", ctx)
    assert ctx.last_status == 0
    with pytest.raises(PipelineError) as info:
        run_line("nosuchcommand", ctx)
    assert info.value.status == ctx.last_status == 127
    assert out(ctx, "echo $?") == "127\n"
    assert out(ctx, "echo $?") == "0\n"  # the echo itself succeeded


def test_failed_command_is_status_1(ctx):
    with pytest.raises(PipelineError):
        run_line("cat missing.txt", ctx)
    assert ctx.last_status == 1


def test_parse_error_is_status_2(ctx):
    with pytest.raises(ParseError):
        run_line("echo 'open", ctx)
    assert ctx.last_status == 2


def test_grep_no_match_is_status_1_without_an_error(ctx):
    result = run_line("echo hello | grep zzz", ctx)
    assert result.status == ctx.last_status == 1 and result.output.text == ""
    assert run_line("echo hello | grep ell", ctx).status == 0
    assert run_line("echo hello | grep -q ell", ctx).output.text == ""  # -q: status only
    assert ctx.last_status == 0
    assert run_line("echo hello | grep zz | wc -l", ctx).status == 0  # no pipefail: last command's


# ── sequences ────────────────────────────────────────────────────────────────

def test_semicolon_runs_each_statement_and_goes_on_after_a_failure(ctx):
    text = out(ctx, "echo a ; nosuch ; echo $?")
    assert text.splitlines()[0] == "a"
    assert text.splitlines()[1].startswith("✗ nosuch: command not found")
    assert text.splitlines()[2] == "127"


def test_quoted_semicolon_is_text(ctx):
    assert out(ctx, "echo 'a ; b'") == "a ; b\n"


def test_plain_lines_parse_as_before(ctx):
    assert out(ctx, "echo {a} }") == "{a} }\n"
    assert out(ctx, "echo for if") == "for if\n"


# ── for ──────────────────────────────────────────────────────────────────────

def test_for_words_quotes_and_ranges(ctx):
    assert out(ctx, 'for -py x in a "b c" 1..3 5..4 0..10..5 { echo [$x] }').split("\n")[:-1] == [
        "[a]", "[b c]", "[1]", "[2]", "[3]", "[5]", "[4]", "[0]", "[5]", "[10]"]


def test_loop_variable_is_one_argument_whatever_it_holds(ctx, tmp_path):
    (tmp_path / "keep.txt").write_text("x")
    text = out(ctx, 'for -py x in "keep.txt; rm keep.txt" { echo $x }')
    assert text == "keep.txt; rm keep.txt\n" and (tmp_path / "keep.txt").exists()


def test_for_over_command_output_lines(ctx, tmp_path):
    (tmp_path / "hosts.txt").write_text("a.com\n\n  b.com  \n")
    assert out(ctx, "for -py h in (cat hosts.txt) { echo host=$h }") == "host=a.com\nhost=b.com\n"
    assert out(ctx, "for -py h in $(cat hosts.txt | grep b) { echo $h }") == "b.com\n"


def test_for_globs_files_sorted_and_keeps_unmatched_pattern(ctx, tmp_path):
    for name in ("b.log", "a.log", "c.txt"):
        (tmp_path / name).write_text("")
    assert out(ctx, "for -py f in *.log { echo $f }") == "a.log\nb.log\n"
    assert out(ctx, "for -py f in *.none { echo $f }") == "*.none\n"
    assert out(ctx, "for -py f in '*.log' { echo $f }") == "*.log\n"  # quoted: no glob


def test_nested_loops_and_outer_variable(ctx):
    assert out(ctx, "for -py a in 1..2 { for b in x $a { echo $a$b } }") == "1x\n11\n2x\n22\n"


def test_break_and_continue(ctx):
    line = 'for -py i in 1..6 { if (i == "2") { continue }; if -py (i == "5") { break }; echo $i }'
    assert out(ctx, line) == "1\n3\n4\n"


def test_failure_in_body_is_reported_and_loop_goes_on(ctx):
    text = out(ctx, "for -py f in a.txt b.txt { cat $f }")
    assert text.count("✗ cat:") == 2 and ctx.last_status == 1


def test_empty_loop_is_status_0(ctx):
    run_line("nosuch ; for -py x in (cat missing) { echo $x }", ctx)
    assert ctx.last_status == 0


def test_range_limits(ctx):
    assert "more than" in out(ctx, "for -py i in 1..1000000 { echo $i }")
    assert ctx.last_status == 1


def test_loop_respects_the_context_permissions(ctx, tmp_path):
    ctx.allow_writes = False
    (tmp_path / "f").write_text("")
    text = out(ctx, "for -py x in f { rm $x }")
    assert "not available in this context" in text and (tmp_path / "f").exists()


# ── if ───────────────────────────────────────────────────────────────────────

def test_if_python_condition_with_elif_and_else(ctx):
    line = ('for -py n in 1..4 { if (int(n) % 2 == 0) { echo even $n } elif ($n == "3") { echo three } '
            'else { echo odd $n } }')
    assert out(ctx, line) == "odd 1\neven 2\nthree\neven 4\n"


def test_if_on_its_own_and_string_methods(ctx):
    assert out(ctx, 'for -py ip in 10.0.0.1 8.8.8.8 { if (ip.startswith("10.")) { echo internal $ip } }') \
        == "internal 10.0.0.1\n"
    assert out(ctx, 'if -py ("a,b".split(",")[1] == "b" and len("abc") == 3) { echo yes }') == "yes\n"
    assert out(ctx, 'if -py (match(r"^\\d+$", "123")) { echo digits }') == "digits\n"


def test_if_command_condition_and_negation(ctx, tmp_path):
    (tmp_path / "a.log").write_text("ERROR x\n")
    (tmp_path / "b.log").write_text("fine\n")
    assert out(ctx, "for -py f in *.log { if grep -q ERROR $f { echo $f } }") == "a.log\n"
    assert out(ctx, "for -py f in *.log { if ! grep -q ERROR $f { echo clean $f } }") == "clean b.log\n"


def test_status_in_conditions(ctx):
    assert out(ctx, "nosuch ; if -py ($? == 127) { echo was-not-found }").endswith("was-not-found\n")
    assert out(ctx, "nosuch ; if -py (status != 0) { echo failed }").endswith("failed\n")


def test_condition_error_stops_the_script(ctx):
    text = out(ctx, "for -py n in 1..3 { if (n > 1) { echo big } } ; echo after")
    assert text.count("✗ if") == 1 and "int(name)" in text and "after" not in text
    assert ctx.last_status == 1
    assert "unknown name 'nope'" in out(ctx, "if -py (nope) { echo x }")


@pytest.mark.parametrize("condition, problem", [
    ('__import__("os")', "function '__import__'"),
    ('os.system("id")', "method '.system()'"),
    ('"".__class__', "attribute '.__class__'"),
    ("(lambda: 1)()", "only plain function"),
    ("[x for x in 'ab']", "is not allowed"),
    ("open('x')", "function 'open'"),
    ("_secret", "name '_secret'"),
    ("2 ** 99999", "is not allowed"),
    ("f'{1}'", "is not allowed"),
])
def test_unsafe_conditions_are_refused_before_running(ctx, tmp_path, condition, problem):
    with pytest.raises(ParseError) as info:
        run_line(f"echo before > ran.txt ; if -py ({condition}) {{ echo x }}", ctx)
    assert problem in info.value.message
    assert not (tmp_path / "ran.txt").exists()  # nothing ran


def test_big_repetition_is_refused(ctx):
    assert "'*' would build" in out(ctx, 'if -py ("a" * 10000000) { echo x }')


def test_string_methods_only_on_text(ctx):
    assert "works on text" in out(ctx, 'if -py ((1, 2).count(1)) { echo x }')


# ── syntax ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("line, message", [
    ("break", "outside a for loop"),
    ("else { echo x }", "without a matching if"),
    ("for -py 1x in a { echo }", "expected a variable name"),
    ("for -py x on a { echo }", "expected 'in'"),
    ("for -py x in a|b { echo }", "in parentheses"),
    ("for -py x in a { echo $x } | sort", "can't be piped"),
    ("for -py x in a { echo $x}", "word of its own"),
    ("for -py x in a { echo 'open }", "unterminated"),
])
def test_syntax_errors(ctx, line, message):
    with pytest.raises(ParseError) as info:
        run_line(line, ctx)
    assert message in info.value.message


@pytest.mark.parametrize("text, join", [
    ("for -py x in a b {", " ; "),
    ("for -py x in a { if (x) { echo }", " ; "),
    ("for -py x in a b", " "),
    ("for -py x in (cat", " "),
    ("if -py (a and", " "),
    ("if -py grep -q x f", " "),
    ("for -py x in a { echo }", None),
    ("echo {", None),
    ("echo 'open", None),
])
def test_incomplete_input_asks_for_more(text, join):
    assert script.incomplete(text) == join


def test_multiline_script_text_runs(ctx):
    assert out(ctx, "for -py x in a b {\n  if (x == 'a') {\n echo A\n }\n  else { echo other }\n}") == "A\nother\n"


def test_error_position_points_into_the_whole_line(ctx):
    with pytest.raises(ParseError) as info:
        run_line("echo ok ; echo 'bad", ctx)
    assert info.value.pos == len("echo ok ; echo ")
