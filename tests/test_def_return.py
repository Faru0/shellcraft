"""`def` return values: `return EXPR`, a def called as a command, and functions called from Python."""

import pytest

from shellcraft import check, script
from shellcraft.context import to_text
from shellcraft.parser import ParseError
from shellcraft.pipeline import run_line


def out(ctx, text):
    result = run_line(text, ctx)
    return None if result is None else to_text(result.output)


def define(ctx, text):
    out(ctx, text)
    assert ctx.last_status == 0


ADD = "def add(a, b):\n    return int(a) + int(b)\n"
GREET = 'def greet(name):\n    return "Hello, " + name\n'
WEEKEND = 'def is_weekend(day):\n    return day in ["Sat", "Sun"]\n'


def test_a_returned_string_is_printed(ctx):
    define(ctx, GREET)
    assert out(ctx, "greet Ada") == "Hello, Ada\n" and ctx.last_status == 0
    assert out(ctx, 'echo "[$(greet Ada)]"') == "[Hello, Ada]\n"
    assert out(ctx, "greet Ada | tr a-z A-Z") == "HELLO, ADA\n"


def test_numbers_0_to_255_are_the_status(ctx):
    define(ctx, ADD)
    assert out(ctx, "add 1 2") == "" and ctx.last_status == 3
    assert out(ctx, "add 0 0") == "" and ctx.last_status == 0
    assert out(ctx, "add 200 100") == "300\n" and ctx.last_status == 0
    define(ctx, "def neg():\n    return -1\n")
    assert out(ctx, "neg") == "-1\n" and ctx.last_status == 0
    define(ctx, "def half():\n    return 1 / 2\n")
    assert out(ctx, "half") == "0.5\n"


def test_true_false_and_none(ctx):
    define(ctx, WEEKEND)
    assert out(ctx, "is_weekend Sat") == "" and ctx.last_status == 0
    assert out(ctx, "is_weekend Mon") == "" and ctx.last_status == 1
    assert out(ctx, "is_weekend Sun && echo rest") == "rest\n"
    define(ctx, "def nothing():\n    echo hi\n    return None\n")
    assert out(ctx, "nothing") == "hi\n" and ctx.last_status == 0


def test_echoed_output_comes_first(ctx):
    define(ctx, 'def both():\n    echo working\n    return "done"\n')
    assert out(ctx, "both") == "working\ndone\n"


def test_bare_return_and_bash_return_are_unchanged(ctx):
    define(ctx, "def last():\n    false\n    return\n")
    assert out(ctx, "last") == "" and ctx.last_status == 1
    ctx.variables["rc"] = "4"
    define(ctx, "f() { return $rc; }")
    assert out(ctx, "f") == "" and ctx.last_status == 4
    define(ctx, "def g() { return 2 }")
    assert out(ctx, "g") == "" and ctx.last_status == 2
    define(ctx, "def h(x=\"\"):\n    [ -n \"$x\" ] || return 7\n    return 0\n")
    assert out(ctx, "h") == "" and ctx.last_status == 7


def test_bash_function_inside_a_def_keeps_bash_return(ctx):
    define(ctx, "def outer():\n    function inner { return 5; }\n    inner\n    return status\n")
    assert out(ctx, "outer") == "" and ctx.last_status == 5


def test_call_from_python(ctx):
    define(ctx, ADD)
    out(ctx, "for -py _once in 1:\n    total = add(1, 2)\n    big = add(total, 10) * 2\n")
    assert ctx.variables["total"] == "3" and ctx.variables["big"] == "26"


def test_if_with_a_def_check(ctx):
    define(ctx, WEEKEND)
    script_text = ("for -py day in Sat Mon:\n    if is_weekend(day):\n        echo $day rest\n"
                   "    else:\n        echo $day work\n")
    assert out(ctx, script_text) == "Sat rest\nMon work\n"
    assert out(ctx, "if -py not is_weekend(\"Tue\"):\n    echo weekday\n") == "weekday\n"


def test_a_bash_function_gives_its_output(ctx):
    define(ctx, "slug() { echo \"$1\" | tr ' ' - | tr A-Z a-z; }")
    out(ctx, 'for -py _once in 1:\n    s = slug("Hello World") + ".md"\n')
    assert ctx.variables["s"] == "hello-world.md"


def test_output_during_a_python_call_is_captured(ctx):
    define(ctx, 'def noisy():\n    echo chatter\n    return "value"\n')
    assert out(ctx, "for -py _once in 1:\n    v = noisy()\n    echo $v\n") == "value\n"


def test_status_of_a_python_call(ctx):
    define(ctx, "fails() { echo no; return 3; }")
    out(ctx, "for -py _once in 1:\n    v = fails()\n    echo $v\n")
    assert ctx.variables["v"] == "no"


def test_return_calls_another_function_and_recursion(ctx):
    define(ctx, "def fact(n):\n    if (int(n) <= 1):\n        return 1\n    return int(n) * fact(int(n) - 1)\n")
    assert out(ctx, "fact 6") == "720\n"
    out(ctx, "for -py _once in 1:\n    f = fact(10)\n")
    assert ctx.variables["f"] == "3628800"


def test_runaway_recursion_is_stopped(ctx):
    define(ctx, "def down(n):\n    return down(int(n) + 1)\n")
    text = out(ctx, "down 0")
    assert "nested more than" in text


def test_unknown_function_and_keywords(ctx):
    text = out(ctx, "for -py _once in 1:\n    x = nope(1)\n")
    assert "nope is not a function" in text and ctx.last_status == 1
    with pytest.raises(ParseError, match="keyword"):
        script.parse_script("for -py _once in 1 {\n x = add(1, b=2)\n}")


def test_a_bad_return_expression_is_a_parse_error(ctx):
    with pytest.raises(ParseError, match="return: invalid value"):
        script.parse_script("def f() {\n return 1 +\n}")
    with pytest.raises(ParseError, match=r"return: \$\( … \) is bash"):
        script.parse_script("def f() {\n return $(date)\n}")
    report = check.check_text("def f(n):\n    echo hi\n    return n n\n    echo more\n", "f.sc", ctx)
    assert report.text().splitlines()[0] == "f.sc:3: return: invalid value (invalid syntax): n n"
    report = check.check_text("def f(n):\n    return __import__('os')\n", "f.sc", ctx)
    assert "function '__import__' is not available" in report.text()


def test_an_error_while_returning_is_reported(ctx):
    define(ctx, "def bad(x):\n    return int(x)\n")
    text = out(ctx, "bad abc; echo after $?")
    assert "invalid literal" in text and text.endswith("after 1\n")


def test_command_conditions_still_work(ctx, tmp_path):
    (tmp_path / "f").write_text("x\n")
    assert out(ctx, "if -py grep -q x f:\n    echo found\n") == "found\n"


def test_python_builtins_outside_the_list_are_not_reachable(ctx, tmp_path):
    text = out(ctx, "if -py (open('x.txt', 'w')) { echo x }; echo after")
    assert "open is not a function" in text and "after" not in text
    assert not (tmp_path / "x.txt").exists()
