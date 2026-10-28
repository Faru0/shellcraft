"""`match WORD:` with `case PATTERN:` choices (Python's spelling of `case -py`), the brace form,
and two fixes found on the way: a bash test after `if -py` / `while -py`, and `source -n` after a
bad line inside a block."""

import io

import pytest
from rich.console import Console

from shellcraft import check, indent, script
from shellcraft.context import ShellContext, to_text
from shellcraft.highlight import highlight
from shellcraft.loader import ModuleRegistry
from shellcraft.parser import ParseError
from shellcraft.pipeline import PipelineError, run_line
from shellcraft.themes import UI, all_themes


def out(ctx, text):
    result = run_line(text, ctx)
    return None if result is None else to_text(result.output)


MENU = """match choice:
    case "1":
        echo one
    case "q" | "Q":
        echo bye
    case "*.py":
        echo python
    case _:
        echo "not on the menu"
"""


@pytest.mark.parametrize("value, shown", [("1", "one"), ("q", "bye"), ("Q", "bye"), ("x.py", "python"),
                                          ("zz", "not on the menu")])
def test_choices_bars_globs_and_the_fallback(ctx, value, shown):
    out(ctx, f"choice={value}")
    assert out(ctx, MENU) == f"{shown}\n"


def test_the_subject_is_a_variable_or_any_word(ctx):
    out(ctx, "x=b")
    assert out(ctx, 'match $x:\n    case "a": echo A\n    case "b": echo B') == "B\n"
    assert out(ctx, 'match -py $x:\n    case "b": echo B') == "B\n"
    assert out(ctx, 'match "$x-y":\n    case "b-*": echo glob') == "glob\n"
    assert out(ctx, 'match x:\n    case "b": echo var') == "var\n"  # a bare name reads the variable


def test_nothing_matching_runs_nothing(ctx):
    assert out(ctx, 'match nope:\n    case "a": echo A') == ""
    assert ctx.last_status == 0


def test_one_line_choices_and_pass(ctx):
    out(ctx, "c=q")
    text = 'match c:\n    case "q": echo bye\n    case "w": pass\n    case _: echo other'
    assert out(ctx, text) == "bye\n"
    out(ctx, "c=w")
    assert out(ctx, text) == ""


def test_nested_in_for_and_def(ctx):
    text = """for -py n in a b c:
    match n:
        case "a" | "c":
            echo "$n: yes"
        case _:
            echo "$n: no"
"""
    assert out(ctx, text) == "a: yes\nb: no\nc: yes\n"
    out(ctx, 'def kind(name):\n    match name:\n        case "*.py":\n            return "python"\n'
             '        case _:\n            return "other"')
    assert out(ctx, "kind x.py") == "python\n"
    assert out(ctx, "kind x.sh") == "other\n"


def test_case_py_choices_may_be_written_with_case(ctx):
    out(ctx, "v=b")
    assert out(ctx, 'case -py $v:\n    case "b": echo B\n    _: echo other') == "B\n"
    assert out(ctx, 'case -py case:\n    case: echo literal') == "literal\n"  # a pattern named case


def test_translation():
    assert indent.translate('match $x:\n    case "a" | "b":\n        echo ab\n    case _: echo o') == \
        'case -py $x {\n    "a" | "b" {\n        echo ab\n}\n    _ { echo o\n}\n}'


@pytest.mark.parametrize("text, message", [
    ('match x:\n    "a":\n        echo a', "match: each choice is case PATTERN: (for anything else: case _:)"),
    ("match x:\n    case:\n        echo a", "match: each choice is case PATTERN:"),
    ('match x:\n    case "a" if y == 1:\n        echo a', "match: no 'if' guards on a choice"),
    ('match x:\n    case a | b if y:\n        echo a', "no 'if' guards"),
    ("match x: echo a", "match: the choices go on the lines below, indented"),
    ("match:\n    case a: echo a", "match: missing a word before ':' (match $choice:)"),
    ('match x:\necho a', "expected an indented block"),
])
def test_errors(text, message):
    with pytest.raises(indent.IndentError) as caught:
        indent.translate(text)
    assert message in str(caught.value)


def test_a_guard_word_inside_quotes_is_a_pattern(ctx):
    assert out(ctx, 'match "a if b":\n    case "a if b": echo quoted') == "quoted\n"


def test_match_without_a_colon_is_a_command_and_match_is_still_a_condition(ctx):
    assert not indent.opens_block("match x")
    assert not indent.has_colon_block("match x")
    with pytest.raises(PipelineError, match="keyword"):
        run_line("match x", ctx)
    assert out(ctx, 'if -py match(r"^v\\d", "v2"): echo yes') == "yes\n"
    assert out(ctx, 'if -py (match(r"^v\\d", "x")) { echo yes } else { echo no }') == "no\n"
    text = 'for -py v in v1 x:\n    if match(r"^v", v):\n        echo $v'
    assert out(ctx, text) == "v1\n"


def test_the_brace_form(ctx):
    out(ctx, "x=b")
    assert out(ctx, 'match -py $x { case "a" | "b" { echo ab } case _ { echo o } }') == "ab\n"
    assert out(ctx, 'match -py zz { "a" { echo a } _ { echo o } }') == "o\n"
    assert out(ctx, 'if -py (True) { match $x { case "b" { echo inner } } }') == "inner\n"
    assert script.incomplete("match -py $x {") is not None
    with pytest.raises(ParseError):
        script.parse_script('match -py $x')


@pytest.fixture
def repl_shell(monkeypatch, tmp_path):
    from shellcraft import shell

    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(shell, "create_prompt_toolkit_console", lambda cleanup: None)
    console = Console(file=io.StringIO(), width=120, force_terminal=False, color_system=None)
    ctx = ShellContext(registry=ModuleRegistry(tmp_path), ui=UI(all_themes({})["nord"], console), config={})
    with shell.Shell(ctx) as sh:
        yield sh, ctx


def test_prompt_block_mode(repl_shell):
    sh, ctx = repl_shell
    assert indent.opens_block("match choice:")
    typed = iter(['    case "q":', "        echo bye", "    case _:", "        echo other", ""])
    sh._ask = lambda message: next(typed)
    text = sh._read_continuation("match choice:")
    assert text == 'match choice:\n    case "q":\n        echo bye\n    case _:\n        echo other'
    run_line("choice=q", ctx)
    run_line(text, ctx)
    assert ctx.ui.console.file.getvalue().split() == ["bye"]


def test_prompt_block_mode_reports_a_choice_without_case(repl_shell):
    sh, ctx = repl_shell
    typed = iter(['    "q":', "        echo bye", ""])
    sh._ask = lambda message: next(typed)
    assert sh._read_continuation("match choice:") is None
    assert ctx.last_status == 2


def test_source_n_checks_a_match(ctx):
    good = check.check_text('match $x:\n    case "a":\n        echo a\n    case _:\n        nosuchcmd\n', "f.sc", ctx)
    assert [(p.line, p.error) for p in good.problems] == [(5, False)]
    bad = check.check_text('match $x:\n    "a":\n        echo a\n', "f.sc", ctx)
    assert bad.problems[0].line == 2 and "case PATTERN:" in bad.problems[0].message


def test_highlight_match_as_a_keyword():
    tokens = highlight('match $x:', lambda name: True)
    assert ("class:keyword", "match") in tokens


def test_man_match(ctx):
    page = out(ctx, "man match")
    assert "case PATTERN:" in page


# ── a bash test after if -py / while -py ─────────────────────────────────────

def test_a_bash_test_is_a_command_not_a_python_list(ctx, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "notes.txt").write_text("x")
    assert out(ctx, "if -py [ -f notes.txt ]:\n    echo yes") == "yes\n"
    assert out(ctx, "if -py [[ -f notes.txt ]]: echo yes") == "yes\n"
    assert out(ctx, "if -py test -f notes.txt: echo yes") == "yes\n"
    assert out(ctx, "if -py ! [ -f other.txt ]: echo missing") == "missing\n"
    assert out(ctx, "if -py [ -f notes.txt ] { echo brace }") == "brace\n"
    assert out(ctx, "while -py [ ! -f done.txt ]:\n    touch done.txt\n    echo once") == "once\n"
    assert out(ctx, 'if -py [1, 2] == [1, 2]: echo list') == "list\n"  # a Python list is still Python


# ── source -n after a bad line inside a block ────────────────────────────────

def _problems(ctx, text):
    return [(p.line, p.message) for p in check.check_text(text, "f.sc", ctx).problems]


def test_a_bad_line_inside_a_def_reports_once_at_its_line(ctx):
    text = "def add(a, b):\n    echo a\n    fi\n    total = a + b\n    echo $total\n\necho after\n"
    assert _problems(ctx, text) == [(3, "'fi' without a matching if")]


def test_a_bad_last_line_of_a_body_is_reported_at_that_line(ctx):
    text = "def add(a, b):\n    echo hi\n    total = a +\n\necho after\n"
    problems = _problems(ctx, text)
    assert len(problems) == 1 and problems[0][0] == 3


def test_a_bad_line_in_a_bash_function_keeps_the_rest_in_it(ctx):
    text = "foo() {\n    echo hi\n    done\n    total=1\n    nosuch\n}\necho after\n"
    problems = _problems(ctx, text)
    assert problems[0] == (3, "'done' without a matching for")
    assert [line for line, _ in problems] == [3, 5]  # nosuch is a real unknown command in the body
    assert "unknown command 'nosuch'" in problems[1][1]
