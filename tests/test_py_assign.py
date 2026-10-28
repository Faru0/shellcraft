"""Python assignment in -py blocks and `def`: `count = 0`, `count += 1` and the other `op=` forms."""

import io

import pytest
from rich.console import Console

from shellcraft import check, indent, script
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


def test_set_a_variable_from_python(ctx):
    block(ctx, 'count = 0\nname = "  Ada  ".strip().lower()\ntotal = int("21") * 2\nbig = 50 > 10')
    assert (ctx.variables["count"], ctx.variables["name"]) == ("0", "ada")
    assert (ctx.variables["total"], ctx.variables["big"]) == ("42", "True")


def test_the_value_reads_other_variables(ctx):
    ctx.variables["a"] = "7"
    block(ctx, "double = int(a) * 2\nalso = int($a) + 1\nok = status == 0")
    assert (ctx.variables["double"], ctx.variables["also"], ctx.variables["ok"]) == ("14", "8", "True")


@pytest.mark.parametrize(("op", "value", "want"), [
    ("+=", "3", "13"), ("-=", "3", "7"), ("*=", "3", "30"), ("/=", "4", "2.5"),
    ("//=", "4", "2"), ("%=", "4", "2"), ("/=", "5", "2.0"),
])
def test_every_augmented_operator(ctx, op, value, want):
    ctx.variables["n"] = "10"
    block(ctx, f"n {op} {value}")
    assert ctx.variables["n"] == want and ctx.last_status == 0


def test_text_and_numbers(ctx):
    ctx.variables["s"] = "ab"
    ctx.variables["f"] = "1.5"
    block(ctx, 's += "cd"\nf += 1\nx = "5"\nx += "0"')
    assert (ctx.variables["s"], ctx.variables["f"], ctx.variables["x"]) == ("abcd", "2.5", "50")


def test_errors_are_reported_with_status_1(ctx):
    text = out(ctx, "for -py _once in 1:\n    count += 1\n    echo after")
    assert "count: not set, give it a value first" in text and text.endswith("after\n")
    ctx.variables["word"] = "abc"
    assert "word: its value 'abc' is not a number" in block(ctx, "word += 1")
    assert ctx.last_status == 1 and ctx.variables["word"] == "abc"
    ctx.variables["n"] = "1"
    assert "ZeroDivisionError" in block(ctx, "n //= 0")
    assert "unknown name 'nope'" in block(ctx, "x = nope + 1")


def test_loops_and_ifs(ctx):
    text = """count = 0
for -py n in 1..5:
    if int(n) % 2 == 0:
        count += 1
echo "even: $count"
"""
    ctx.variables["count"] = "x"
    assert out(ctx, "for -py _once in 1:\n" + "".join(f"    {line}\n" for line in text.splitlines())) == "even: 2\n"
    text = """for -py _once in 1:
    n = 0
    while n < 3:
        n += 1
        echo "n is $n"
"""
    # n is text: `n < 3` compares text with a number, so use int(n)
    assert "int(name)" in out(ctx, text)
    assert out(ctx, text.replace("while n < 3", "while int(n) < 3")) == "n is 1\nn is 2\nn is 3\n"


def test_brace_form(ctx):
    assert out(ctx, 'total=0\nfor -py x in 1 2 3 { total += int(x) }\necho $total') == "6\n"
    assert out(ctx, 'if -py (True) { msg = "yes"; msg += "!" }\necho $msg') == "yes!\n"


def test_def_parameters_and_locals(ctx):
    out(ctx, 'def add(a, b):\n    sum = int(a) + int(b)\n    echo $sum')
    assert out(ctx, "add 2 3") == "5\n" and ctx.variables["sum"] == "5"
    out(ctx, 'def bump(n):\n    n += 1\n    local tmp=1\n    tmp *= 10\n    echo "$n $tmp"')
    ctx.variables["n"] = "outer"
    assert out(ctx, "bump 41") == "42 10\n"
    assert ctx.variables["n"] == "outer" and "tmp" not in ctx.variables


def test_outside_py_mode_nothing_changes(ctx):
    assert script.parse_script("x = 5").stmts[0] == script.Simple("x = 5", 0, "top")
    with pytest.raises(PipelineError, match="command not found: x"):  # a command named x
        out(ctx, "x = 5")
    assert "x" not in ctx.variables
    out(ctx, "for i in 1; do y = 5; done")
    assert "y" not in ctx.variables
    out(ctx, "for -py i in 1:\n    z=5\n    w=a")  # the bash form still works in a -py block
    assert (ctx.variables["z"], ctx.variables["w"]) == ("5", "a")


def test_a_bad_value_is_a_parse_error(ctx):
    with pytest.raises(ParseError, match=r"count \+=: invalid value \(invalid syntax\): 1 \+"):
        script.parse_script("for -py i in 1:\n    count += 1 +")
    with pytest.raises(ParseError, match="only plain function and string-method calls"):
        script.parse_script("for -py i in 1 { x = (lambda: 1)() }")
    with pytest.raises(ParseError, match=r"x=\$\(command\)"):
        script.parse_script("for -py i in 1 { x = $(date) }")
    with pytest.raises(ParseError, match="missing a value"):
        script.parse_script("for -py i in 1 { x = }")


def test_check_reports_the_line_and_does_not_flag_the_name(ctx):
    r = check.check_text("def f(n):\n    total = 0\n    count = 1 +\n    echo $count\n", "f.sc", ctx)
    assert r.text().splitlines()[0] == "f.sc:3: count =: invalid value (invalid syntax): 1 +"
    assert check.check_text("def f(n):\n    total = 0\n    total += int(n)\n", "f.sc", ctx).text().startswith("ok")


def test_set_x_traces_it_and_set_u_does_not_matter(ctx):
    seen = []
    ctx.emit_stderr = seen.append
    out(ctx, "set -x")
    out(ctx, "set -u")
    block(ctx, 'count = 1\nmsg = "a b"')
    assert "+ count=1" in "".join(seen) and "+ msg='a b'" in "".join(seen)


def test_indent_leaves_assignments_alone():
    text = 'for -py n in 1 2:\n    count += 1\n    s = d[1:2]\n    t = "a: b"'
    assert indent.translate(text).splitlines()[1:4] == ["    count += 1", "    s = d[1:2]", '    t = "a: b"']


def test_input_keeps_its_path(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / "home"))
    ictx = ShellContext(registry=ModuleRegistry(tmp_path / "modules"))
    ictx.interactive = True
    ictx.ui = UI(all_themes({})["nord"], Console(file=io.StringIO()))
    queue = ["Ada", "7"]
    import prompt_toolkit

    monkeypatch.setattr(prompt_toolkit, "prompt", lambda prompt, is_password=False: queue.pop(0))
    out(ictx, 'for -py i in 1:\n    name = input("Name? ")\n    n = int(input("N? "))\n    n += 1')
    assert (ictx.variables["name"], ictx.variables["n"]) == ("Ada", "8")
