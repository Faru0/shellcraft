"""Python-style `x = 5`, `print(...)` and `add(1, 2)` outside a -py block (SCRIPTING-TODO item 22)."""

import pytest

from shellcraft import startup
from shellcraft.context import to_text
from shellcraft.parser import ParseError
from shellcraft.pipeline import run_line


def out(ctx, line):
    result = run_line(line, ctx)
    return "" if result is None or result.output is None else (
        result.output if isinstance(result.output, str) else result.output.text)


def run_file(ctx, tmp_path, text):
    path = tmp_path / "script.sc"
    path.write_text(text)
    collected = []
    ctx.emit = lambda value: collected.append(to_text(value))
    ctx.emit_error = lambda exc, line: collected.append(f"ERR {exc}\n")
    startup.source(path, ctx)
    return "".join(collected)


def test_assignment_with_spaces_at_the_prompt(ctx):
    assert out(ctx, "x = 5") == ""
    assert ctx.variables["x"] == "5"
    assert out(ctx, "x += 2; echo $x") == "7\n"
    assert out(ctx, "x -= 1; x *= 3; echo $x") == "18\n"
    assert out(ctx, "x //= 4; echo $x") == "4\n"
    assert out(ctx, "x %= 3; echo $x") == "1\n"
    assert out(ctx, 'name = "Ada Lovelace"; echo "$name"') == "Ada Lovelace\n"


def test_values_keep_their_type(ctx):
    out(ctx, 'n = int("4")')
    assert out(ctx, "print(n + 1)") == "5\n"
    out(ctx, "ratio = 7 / 2")
    assert out(ctx, "print(ratio)") == "3.5\n"
    out(ctx, 'who = "Ada"; msg = f"hi {who}"')
    assert out(ctx, "echo $msg") == "hi Ada\n"


def test_x_equals_5_without_spaces_is_unchanged(ctx):
    out(ctx, "x=5")
    assert ctx.variables["x"] == "5"
    out(ctx, "y='a b'")
    assert ctx.variables["y"] == "a b"


def test_assignment_in_bash_blocks_and_functions(ctx):
    out(ctx, "for i in 1 2; do count = 0; count += int(i); done")
    assert ctx.variables["count"] == "2"
    out(ctx, "if [ 1 = 1 ]; then k = 9; fi")
    assert ctx.variables["k"] == "9"
    out(ctx, "f() { z = 4; echo $z; }")
    assert out(ctx, "f") == "4\n"


def test_a_name_that_is_a_builtin_stays_a_command(ctx):
    assert out(ctx, "echo = hello") == "= hello\n"
    assert out(ctx, "echo += 1") == "+= 1\n"


def test_a_module_alias_or_function_name_stays_a_command(ctx, monkeypatch):
    out(ctx, "greet() { echo \"greet got: $*\"; }")
    assert out(ctx, "greet = 5") == "greet got: = 5\n"
    from shellcraft import aliases

    monkeypatch.setattr(aliases, "get_all", lambda config: {"ll": "echo aliased"})
    assert out(ctx, "ll = 5") == "aliased = 5\n"
    assert "ll" not in ctx.variables


def test_a_bad_value_is_reported(ctx):
    with pytest.raises(ParseError, match="invalid value"):
        run_line("x = foo bar", ctx)
    with pytest.raises(ParseError, match="x ="):
        run_line("x =", ctx)
    with pytest.raises(ParseError, match="bash"):
        run_line("x = $(date)", ctx)


def test_unset_name_with_op_is_an_error_message(ctx):
    result = out(ctx, "nothing += 1")
    assert "not set" in result


def test_print_at_the_top_level(ctx):
    assert out(ctx, 'print("hello")') == "hello\n"
    out(ctx, 'name = "Ada"')
    assert out(ctx, 'print(f"hi {name}", 1 + 2, sep="-")') == "hi Ada-3\n"
    assert out(ctx, "echo f\"x\"") == "fx\n"  # echo is still echo


def test_print_in_bash_blocks(ctx):
    assert out(ctx, 'for i in 1 2; do print("n", i); done') == "n 1\nn 2\n"


def test_calling_a_def_at_the_top_level(ctx):
    out(ctx, "def add(a, b) { return a + b }")
    assert out(ctx, "add(1, 2)") == "3\n"
    assert out(ctx, "add(add(1, 2), 4)") == "7\n"
    out(ctx, "def hi(name) { echo hello $name }")
    assert out(ctx, 'hi("Ada")') == "hello Ada\n"
    out(ctx, "def wave() { echo wave }")
    assert out(ctx, "wave()") == "wave\n"


def test_calling_a_bash_function_with_parentheses(ctx):
    out(ctx, "f() { echo f ran; }")
    assert out(ctx, "f()") == "f ran\n"


def test_calling_something_that_is_not_a_function_says_so(ctx):
    assert "not a function" in out(ctx, "nosuch(1)")


def test_a_call_inside_blocks(ctx):
    out(ctx, "def twice(n) { return n * 2 }")
    assert out(ctx, "for i in 1 2; do twice(int(i)); done") == "2\n4\n"
    assert out(ctx, "for -py i in 1 2 { twice(int(i)) }") == "2\n4\n"


def test_bash_function_definitions_are_unchanged(ctx):
    assert out(ctx, "g() { echo g; }; g") == "g\n"
    assert out(ctx, "h()\n{\n echo h\n}\nh") == "h\n"


def test_in_a_file(ctx, tmp_path):
    text = ("def hi(who) {\n  echo hello $who\n}\n"
            'count = 0\nfor i in 1 2 3; do\n  count += int(i)\ndone\nprint(f"count is {count}")\n'
            'hi("you")\n'
            "wave() { echo wave; }\nwave()\nwave()\n")
    assert run_file(ctx, tmp_path, text) == "count is 6\nhello you\nwave\nwave\n"


def test_a_bash_definition_with_the_brace_on_the_next_line_in_a_file(ctx, tmp_path):
    text = "later()\n{\n  echo defined\n}\nlater\n"
    assert run_file(ctx, tmp_path, text) == "defined\n"


def test_chained_assignment_lines_in_py_blocks_still_work(ctx):
    assert out(ctx, "for -py i in 1 2 { n = int(i) * 2; print(n) }") == "2\n4\n"
