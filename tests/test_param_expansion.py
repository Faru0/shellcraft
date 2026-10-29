"""Bash's parameter expansions in `${…}` (shellcraft.parser._parameter): defaults, lengths,
trimming, replacing, slices and case, in every place a word is expanded."""

import pytest

from shellcraft import startup
from shellcraft.context import to_text
from shellcraft.highlight import highlight
from shellcraft.parser import ParseError
from shellcraft.pipeline import UnboundError, run_line
from shellcraft.script import PyValue


def out(ctx, text):
    result = run_line(text, ctx)
    return None if result is None else to_text(result.output)


@pytest.fixture
def f(ctx):
    ctx.variables["f"] = "report.tar.gz"
    return ctx


@pytest.mark.parametrize("word, expected", [
    ("${f}", "report.tar.gz"),
    ("${#f}", "13"),
    ("${f%.gz}", "report.tar"),
    ("${f%.*}", "report.tar"),
    ("${f%%.*}", "report"),
    ("${f#*.}", "tar.gz"),
    ("${f##*.}", "gz"),
    ("${f%x}", "report.tar.gz"),
    ("${f/tar/zip}", "report.zip.gz"),
    ("${f//r/R}", "RepoRt.taR.gz"),
    ("${f//./}", "reporttargz"),
    ("${f/#report/data}", "data.tar.gz"),
    ("${f/#tar/x}", "report.tar.gz"),
    ("${f/%gz/bz2}", "report.tar.bz2"),
    ("${f/r*t/X}", "Xar.gz"),  # the longest match at the first place one starts
    ("${f//[.r]/_}", "_epo_t_ta__gz"),
    ("${f:0:6}", "report"),
    ("${f:7}", "tar.gz"),
    ("${f: -2}", "gz"),
    ("${f:(-6):3}", "tar"),
    ("${f:1:-3}", "eport.tar"),
    ("${f:20}", ""),
    ("${f:1+1:2}", "po"),
    ("${f^^}", "REPORT.TAR.GZ"),
    ("${f^}", "Report.tar.gz"),
    ("${f^^[rt]}", "RepoRT.TaR.gz"),
    ("${f:-x}", "report.tar.gz"),
    ("${f:+set}", "set"),
])
def test_each_form(f, word, expected):
    assert out(f, f"echo {word}") == expected + "\n"
    assert out(f, f'echo "{word}"') == expected + "\n"  # the same inside double quotes


def test_lower_case(ctx):
    ctx.variables["v"] = "HeLLo"
    assert out(ctx, "echo ${v,,} ${v,}") == "hello heLLo\n"


def test_defaults_tell_unset_from_empty(ctx):
    ctx.variables["empty"] = ""
    assert out(ctx, "echo [${empty:-d}] [${empty-d}] [${nope-d}]") == "[d] [] [d]\n"
    assert out(ctx, "echo [${empty:+a}] [${empty+a}] [${nope+a}]") == "[] [a] []\n"


def test_the_default_word_is_expanded(f):
    f.variables["other"] = "fallback"
    assert out(f, "echo ${nope:-$other} ${nope:-${f%%.*}.csv} ${nope:-$(echo sub)}") == \
        "fallback report.csv sub\n"
    assert out(f, "echo ${nope:-$((2 * 3))} ${nope:-'a  b'}") == "6 a  b\n"


def test_quotes_in_the_word(ctx):
    assert out(ctx, """echo "${nope:-"x y"}" "${nope:-'q'}" ${nope:-'q'}""") == "x y 'q' q\n"


def test_single_quotes_stay_literal(f):
    assert out(f, "echo '${f%.gz}'") == "${f%.gz}\n"


def test_a_quoted_pattern_matches_as_text(f):
    f.variables["p"] = "*.gz"
    assert out(f, 'echo ${f%$p} ${f%"$p"} "${f%$p}"') == "report.tar report.tar.gz report.tar\n"
    f.variables["s"] = "a*b"
    assert out(f, 'echo ${s/"*"/x} ${s/\\*/y}') == "axb ayb\n"


def test_assign_with_colon_equals(ctx):
    assert out(ctx, "echo ${new:=hello}") == "hello\n"
    assert ctx.variables["new"] == "hello"
    assert out(ctx, "echo ${new:=other}") == "hello\n"


def test_assigning_a_positional_is_an_error(ctx):
    with pytest.raises(ParseError, match="cannot assign"):
        run_line("echo ${1:=x}", ctx)


def test_assign_in_a_function_and_a_substitution(ctx):
    run_line("setit() { true ${dflt:=fromfunc}; }", ctx)
    run_line("setit", ctx)
    assert ctx.variables["dflt"] == "fromfunc"
    assert out(ctx, "r=$(echo ${insub:=x}); echo \"[${insub-unset}] $r\"") == "[unset] x\n"


def test_question_mark_stops_a_script(ctx):
    result = run_line("echo start; echo ${q:?need q}; echo after", ctx)
    assert "after" not in to_text(result.output)
    assert ctx.last_status == 1
    with pytest.raises(UnboundError, match="q: parameter null or not set"):
        run_line("echo ${q:?}", ctx)
    ctx.variables["q"] = "ok"
    assert out(ctx, "echo ${q:?need q}") == "ok\n"


def test_question_mark_stops_a_sourced_file(ctx, tmp_path):
    errors = []
    ctx.emit_error = lambda exc, line: errors.append(str(exc))
    shown = []
    ctx.emit = lambda value: shown.append(to_text(value))
    script = tmp_path / "s.sc"
    script.write_text("echo one\necho ${q:?need q}\necho two\n")
    startup.source(script, ctx)
    assert "".join(shown).startswith("one\n") and "two" not in "".join(shown)
    assert any("q: need q" in e for e in errors)


def test_set_u(ctx):
    run_line("set -u", ctx)
    assert out(ctx, "echo ${nope:-ok} [${nope-}] [${nope+x}] [${nope:=}]") == "ok [] [] []\n"
    with pytest.raises(UnboundError, match="nope2: unbound variable"):
        run_line("echo ${#nope2}", ctx)
    with pytest.raises(UnboundError, match="nope3"):
        run_line("echo ${nope3%x}", ctx)


def test_function_default_argument(ctx):
    run_line('greet() { name=${1:-world}; echo "hello $name ${#1} ${2-none}"; }', ctx)
    assert out(ctx, "greet") == "hello world 0 none\n"
    assert out(ctx, "greet Ada") == "hello Ada 3 none\n"
    assert out(ctx, 'greet Ada ""') == "hello Ada 3 \n"
    assert out(ctx, "echo ${#}") == "0\n"


def test_python_values_are_plain_text(ctx):
    ctx.variables["n"] = PyValue(6)
    ctx.variables["z"] = PyValue(2.5)
    ctx.variables["ok"] = PyValue(True)
    assert out(ctx, "echo ${#n} ${z%.*} ${ok^^} ${n:-x} ${n/6/7}") == "1 2 TRUE 6 7\n"
    run_line("m=${n:=9}", ctx)
    assert type(ctx.variables["m"]) is str


def test_splitting_in_bash_blocks(ctx):
    ctx.variables["x"] = "a b"
    assert out(ctx, 'for w in ${x:-z}; do echo "[$w]"; done') == "[a]\n[b]\n"
    assert out(ctx, 'for w in "${x:-z}"; do echo "[$w]"; done') == "[a b]\n"


def test_case_test_and_arithmetic(f):
    assert out(f, "case ${f##*.} in gz) echo gzip;; *) echo other;; esac") == "gzip\n"
    assert out(f, "[[ ${f%%.*} == report ]] && echo yes") == "yes\n"
    assert out(f, "echo $(( ${#f} + ${nope:-2} ))") == "15\n"


@pytest.mark.parametrize("word", ["${f@Q}", "${!f}", "${f:}", "${f:1:}", "${f!x}", "${-f}", "${#f%x}"])
def test_bad_substitution(f, word):
    with pytest.raises(ParseError, match="bad substitution"):
        run_line(f"echo {word}", f)


def test_highlighting_keeps_one_variable_token():
    styles = highlight("echo ${v:-x} ${a:-${b}}", lambda name: True)
    assert ("class:variable", "${v:-x}") in styles
    assert ("class:variable", "${a:-${b}}") in styles
