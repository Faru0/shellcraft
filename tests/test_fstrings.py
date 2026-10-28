"""f-strings, `.format()` and `print(…)` in -py blocks and `def`, and `input(f"…")`."""

import io

import pytest
from rich.console import Console

from shellcraft import ask, check, script
from shellcraft.context import ShellContext, to_text
from shellcraft.loader import ModuleRegistry
from shellcraft.parser import ParseError
from shellcraft.pipeline import PipelineError, run_line
from shellcraft.themes import UI, all_themes


def out(ctx, text):
    result = run_line(text, ctx)
    return None if result is None else to_text(result.output)


def block(ctx, body):
    """Run `body` (lines) inside a one-pass `for -py` colon block."""
    return out(ctx, "for -py _once in 1:\n" + "".join(f"    {line}\n" for line in body.splitlines()))


def value(expr, **names):
    return script.evaluate(script.compile_condition(expr), names)


# ── f-strings ────────────────────────────────────────────────────────────────

def test_an_fstring_in_an_assignment(ctx):
    ctx.variables.update(name="Ada", count="3")
    block(ctx, 'msg = f"{name} has {count} files"\nup = f"{name.upper()} {int(count) + 1}"')
    assert (ctx.variables["msg"], ctx.variables["up"]) == ("Ada has 3 files", "ADA 4")


def test_an_fstring_in_a_condition(ctx):
    ctx.variables.update(a="x", b="y")
    assert out(ctx, 'if -py f"{a}-{b}" == "x-y":\n    echo same\nelse:\n    echo other\n') == "same\n"


def test_an_fstring_in_return_and_in_call_arguments(ctx):
    out(ctx, 'def tag(name):\n    return f"<{name}>"\n')
    out(ctx, 'def twice(text):\n    return text + text\n')
    block(ctx, 'x = twice(f"{tag("a")}!")')
    assert ctx.variables["x"] == "<a>!<a>!"


@pytest.mark.parametrize(("expr", "want"), [
    ('f"{price:.2f}"', "3.14"),
    ('f"{name:<6}|"', "Ada   |"),
    ('f"{name:>6}|"', "   Ada|"),
    ('f"{name:*^7}"', "**Ada**"),
    ('f"{big:,}"', "1,234,567"),
    ('f"{n:05d}"', "00042"),
    ('f"{n:x}"', "2a"),
    ('f"{ratio:.0%}"', "50%"),
    ('f"{name!r}"', "'Ada'"),
    ('f"{name!s}"', "Ada"),
    ('f"{n:>{width}}|"', "    42|"),
    ('f"{int(n) + 1}"', "43"),
    ('f"{name.lower()}{len(name)}"', "ada3"),
    ('f"{{literal}} {n}"', "{literal} 42"),
    ('f"{n=}"', "n='42'"),
])
def test_format_specs(expr, want):
    names = {"price": "3.14159", "name": "Ada", "big": "1234567", "n": "42", "ratio": 0.5, "width": "6"}
    assert value(expr, **names) == want


def test_a_number_spec_on_text_that_isnt_a_number():
    with pytest.raises(script.ConditionError, match="'Ada' is not a number"):
        value('f"{name:05d}"', name="Ada")


# ── .format() ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("expr", "want"), [
    ('"{}-{}".format(a, b)', "x-y"),
    ('"{0} {0} {1}".format(a, b)', "x x y"),
    ('"{who} is {age}".format(who=a, age=7)', "x is 7"),
    ('"{:>5}|{:.1f}".format(a, 2.25)', "    x|2.2"),
    ('"{:>{}}|".format(a, 4)', "   x|"),
    ('"{0!r}".format(a)', "'x'"),
    ('"{{}} {}".format(a)', "{} x"),
])
def test_format_method(expr, want):
    assert value(expr, a="x", b="y") == want


@pytest.mark.parametrize(("expr", "problem"), [
    ('"{0.__class__}".format(x)', "field '{0.__class__}' is not allowed"),
    ('"{0[a]}".format(x)', "field '{0[a]}' is not allowed"),
    ('"{x.upper}".format(x=x)', "field '{x.upper}' is not allowed"),
    ('"{:{0.__class__}}".format(x)', "field '{0.__class__}' is not allowed"),
    ('f"{x:>999999999}"', "999999999 in '>999999999' is too big"),
    ('"{:.999999f}".format(1.5)', "999999 in '.999999f' is too big"),
    ('"{:>99999}{:>99999}".format(1, 2)', "longer than 100000"),
    ('f"{x * 99999}{x * 99999}"', "longer than 100000"),
    ('"{} {0}".format(x)', "can't mix"),
    ('"{} {}".format(x)', "no value for field 1"),
    ('"{name}".format(x)', "no value for {name}"),
    ('"{!x}".format(x)', "unknown conversion"),
    ('"{".format(x)', "Single '{'"),
    ('"{:{:{}}}".format(1, 2, 3)', "nested too deep"),
    ('x.format(1)', "'.format()' works on text"),
])
def test_format_refuses_the_unsafe_and_the_huge(expr, problem):
    names = {"x": "a"} if not expr.startswith("x.") else {"x": 5}
    with pytest.raises(script.ConditionError, match=None) as info:
        value(expr, **names)
    assert problem in str(info.value)


@pytest.mark.parametrize(("expr", "problem"), [
    ("len(x, key=1)", "keyword arguments are not allowed"),
    ('"{a}".format(**x)', "* and ** arguments are not allowed"),
    ('"{}".format_map(x)', "method '.format_map()' is not available"),
])
def test_keywords_only_where_they_are_allowed(expr, problem):
    with pytest.raises(ParseError) as info:
        script.compile_condition(expr)
    assert problem in info.value.message


# ── print ────────────────────────────────────────────────────────────────────

def test_print_in_a_colon_block(ctx):
    text = 'for -py n in 1..3:\n    print(f"{n:>3} | {int(n) * int(n):,}")\n'
    assert out(ctx, text) == "  1 | 1\n  2 | 4\n  3 | 9\n"


def test_print_in_a_brace_block_with_braces_in_the_string(ctx):
    assert out(ctx, 'for -py x in a b { print(f"{x}!") }') == "a!\nb!\n"
    assert out(ctx, 'for -py x in a { print("} {", f"{{{x}}}") ; echo done }') == "} { {a}\ndone\n"


def test_print_sep_end_and_several_values(ctx):
    assert block(ctx, 'print("a", 1, True, None)\nprint("a", "b", sep="-", end="!\\n")\nprint()') == \
        "a 1 True None\na-b!\n\n"
    assert block(ctx, 'print("x", end="")') == "x\n"  # one statement ends its own line, as echo -n


def test_print_in_a_def(ctx):
    out(ctx, 'def greet(name, n="2"):\n    print(f"{name}: {int(n) * 2}")\n')
    assert out(ctx, "greet Ada") == "Ada: 4\n"
    assert out(ctx, "greet Ada 5 | tr a-z A-Z") == "ADA: 10\n"


def test_print_output_goes_through_pipes_and_substitution(ctx):
    assert out(ctx, "for -py x in b a { print(x) } | sort") == "a\nb\n"
    assert out(ctx, 'y=$(for -py x in a b { print(f"<{x}>") }); echo "$y"') == "<a>\n<b>\n"
    out(ctx, 'def tag(name):\n    print(f"[{name}]")\n')
    block(ctx, 'v = tag("a")')
    assert ctx.variables["v"] == "[a]"


def test_a_bad_print_is_reported(ctx):
    said = block(ctx, "print(nope)\necho after")
    assert "print: unknown name 'nope'" in said and said.endswith("after\n")
    assert ctx.last_status == 0
    block(ctx, "print(nope)")
    assert ctx.last_status == 1


def test_print_under_set_e_and_try(ctx):
    out(ctx, "set -e")
    said = block(ctx, "print(nope)\necho after")
    assert "unknown name 'nope'" in said and "after" not in said
    out(ctx, "set +e")
    said = out(ctx, "try:\n    for -py i in 1:\n        print(nope)\n        echo after\nexcept as rc:\n"
                    "    echo \"caught $rc\"\n")
    assert said.endswith("caught 1\n") and "after" not in said


def test_print_is_checked_when_the_block_is_read(ctx):
    for line, problem in (("print(1, foo=2)", "unknown keyword 'foo'"), ("print(1) | sort", "nothing may follow"),
                          ("print(**d)", "** arguments"), ("print(x.__class__)", "attribute '.__class__'")):
        with pytest.raises(ParseError) as info:
            out(ctx, f"for -py i in 1 {{ {line} }}")
        assert problem in info.value.message


def test_print_at_the_prompt_stays_a_command(ctx):
    with pytest.raises(PipelineError) as info:
        out(ctx, 'print("x")')
    assert "command not found" in str(info.value) and "use echo" in str(info.value)
    with pytest.raises(PipelineError, match="here use echo"):
        out(ctx, "print hello")
    assert "command not found" in out(ctx, 'f() { print("x"); }; f')  # a bash function body stays bash


def test_print_traces_with_set_x(ctx):
    seen = []
    ctx.emit_stderr = seen.append
    out(ctx, "set -x")
    block(ctx, 'print("a b", 1)')
    out(ctx, "set +x")
    assert "+ print 'a b' 1" in "".join(seen)


# ── input(f"…") ──────────────────────────────────────────────────────────────

@pytest.fixture
def ictx(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / "home"))
    ctx = ShellContext(registry=ModuleRegistry(tmp_path / "modules"))
    ctx.interactive = True
    ctx.ui = UI(all_themes({})["nord"], Console(file=io.StringIO()))
    return ctx


@pytest.fixture
def asked(monkeypatch):
    prompts = []

    def fake(prompt, is_password=False):
        prompts.append(prompt)
        return "7"

    import prompt_toolkit

    monkeypatch.setattr(prompt_toolkit, "prompt", fake)
    return prompts


def test_input_with_an_fstring_prompt_in_a_block(ictx, asked):
    out(ictx, 'for -py who in Ada Sam:\n    age = int(input(f"Age of {who.upper()}? "))\n')
    assert asked == ["Age of ADA? ", "Age of SAM? "]
    assert ictx.variables["age"] == "7"


def test_input_with_an_fstring_prompt_at_the_top(ictx, asked):
    ictx.variables["who"] = "Ada"
    out(ictx, 'age = input(f"Age of {who}? ")')
    out(ictx, 'n = input("Plain {who} $who? ")')
    assert asked == ["Age of Ada? ", "Plain {who} Ada? "]
    with pytest.raises(PipelineError, match="unknown name 'nobody'"):
        out(ictx, 'age = input(f"{nobody}? ")')


def test_parse_ask_line_keeps_an_fstring_as_typed():
    name, prompt, kind = ask.parse_ask_line('x = input(f"Hi {who}")')
    assert isinstance(prompt, ask.FPrompt) and prompt == 'f"Hi {who}"' and (name, kind) == ("x", "str")
    with pytest.raises(ask.AskError):
        ask.parse_ask_line('x = input(f"Hi {")')


# ── source -n ────────────────────────────────────────────────────────────────

def test_source_n_is_happy_with_all_of_it(ctx):
    text = """def show(name, n="2"):
    msg = f"{name}: {int(n):>4}"
    print(msg, "{}-{}".format(1, 2), sep=" | ")
    return f"<{name}>"
for -py x in a b { print(f"{x}!") }
for -py who in Ada:
    age = input(f"Age of {who}? ")
    if f"{who}" == "Ada":
        print("hi", end="!\\n")
"""
    assert check.check_text(text, "f.sc", ctx).text().startswith("ok")


def test_source_n_reports_a_bad_print(ctx):
    report = check.check_text('for -py x in a:\n    print(f"{x}", nope=1)\n', "f.sc", ctx)
    assert "unknown keyword 'nope'" in report.text()


def test_source_n_checks_an_fstring_prompt(ctx):
    for text in ('age = input(f"{who.__class__}? ")\n', 'for -py w in a:\n    age = input(f"{w.__class__}? ")\n'):
        assert "attribute '.__class__'" in check.check_text(text, "f.sc", ctx).text()


def test_nested_lists_and_repeated_joins_cannot_build_huge_text():
    from shellcraft.script import ConditionError, compile_condition, evaluate

    for text in ('["a" * 99999] * 99999', '[["ab"] * 1000] * 1000'):
        with pytest.raises(ConditionError, match="would build more than"):
            evaluate(compile_condition(text), {})
    big = "x" * 9_000_000
    with pytest.raises(ConditionError, match="'\\+' would build more than"):
        evaluate(compile_condition("s + s"), {"s": big})
    assert evaluate(compile_condition('["ab"] * 3'), {}) == ["ab", "ab", "ab"]
    assert evaluate(compile_condition('"a" + "b"'), {}) == "ab"
