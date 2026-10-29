"""Python values keep their type: `n = 5` in a `-py` block or `def` stores the number (bash reads
it as text), and `input()` works anywhere in a `-py` expression (shellcraft.script.PyValue, _input)."""

import io
import os

import pytest
from rich.console import Console

from shellcraft import check, startup
from shellcraft.context import ShellContext, to_text
from shellcraft.loader import ModuleRegistry
from shellcraft.pipeline import PipelineError, run_line
from shellcraft.script import PyValue
from shellcraft.themes import UI, all_themes


def out(ctx, text):
    result = run_line(text, ctx)
    return None if result is None else to_text(result.output)


def block(text):
    """`text` as the body of a one-pass `-py` loop, so `NAME = EXPR` lines are Python."""
    return "for -py _once in 1:\n" + "".join(f"    {line}\n" for line in text.strip("\n").splitlines())


def lines(ctx, text):
    """Run `text` as a file of lines (as rc and `source` do): every command's output and error."""
    shown = []
    for _, line in startup.logical_lines(text):
        try:
            result = run_line(line, ctx)
        except PipelineError as exc:
            shown.append(f"ERROR {exc}\n")
            continue
        if result is not None and to_text(result.output):
            shown.append(to_text(result.output))
    return "".join(shown)


@pytest.fixture
def ictx(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / "home"))
    ctx = ShellContext(registry=ModuleRegistry(tmp_path / "modules"))
    ctx.interactive = True
    ctx.ui = UI(all_themes({})["nord"], Console(file=io.StringIO()))
    return ctx


@pytest.fixture
def answers(monkeypatch):
    """Answer prompts from a list; records each prompt shown."""
    asked = []
    queue = []

    def fake(prompt, is_password=False):
        asked.append(prompt)
        answer = queue.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    import prompt_toolkit

    monkeypatch.setattr(prompt_toolkit, "prompt", fake)
    return queue, asked


# ── typed values ─────────────────────────────────────────────────────────────

def test_numbers_and_bools_keep_their_type(ctx):
    text = block("""
n = 5
ratio = 2.5
ok = n > 3
name = "Ada"
print(n + 1, ratio * 2, ok, not ok, name + "!")
""")
    assert out(ctx, text) == "6 5.0 True False Ada!\n"
    assert ctx.variables == {"_once": "1", "n": "5", "ratio": "2.5", "ok": "True", "name": "Ada"}
    assert isinstance(ctx.variables["n"], PyValue) and ctx.variables["n"].value == 5
    assert not isinstance(ctx.variables["name"], PyValue)  # text stays plain text
    assert out(ctx, block("print(n * n, f'{n * n}', n == 5, ok and n)")) == "25 25 True 5\n"


def test_bash_reads_text(ctx, monkeypatch):
    out(ctx, block("n = 5\nok = True\nr = 0.5"))
    assert out(ctx, 'echo $n "$n" ${n} $ok $r') == "5 5 5 True 0.5\n"
    assert out(ctx, '[[ $n == 5 ]] && echo yes') == "yes\n"
    assert out(ctx, "echo $((n + 1))") == "6\n"
    assert "n=5" in out(ctx, "set")
    monkeypatch.setenv("SC_PORT", "1")
    run_line("export SC_PORT", ctx)
    out(ctx, block("SC_PORT = 8080 + 1"))
    assert os.environ["SC_PORT"] == "8081" and ctx.exported["SC_PORT"] == "8081"


def test_bash_values_stay_text(ctx):
    text = 'n=5\nfor -py _once in 1:\n    print(n + "!")\n    print(int(n) + 1)'
    assert out(ctx, text) == "5!\n6\n"
    out(ctx, block("n = 5"))
    assert out(ctx, 'n=7\nfor -py _once in 1:\n    print(n + "!")') == "7!\n"  # bash wrote it last
    # a loop word is text, and the error says how to get a number
    assert "values set from bash are text: use int(name)" in out(ctx, "for -py x in 1 2:\n    print(x + 1)")


def test_augmented_assignment_keeps_the_type(ctx):
    text = block("""
n = 1
n += 1
n *= 2.5
s = "a"
s += "b"
t = 3
t /= 2
print(n, s, t)
""")
    assert out(ctx, text) == "5.0 ab 1.5\n"
    assert ctx.variables["n"].value == 5.0
    # text set from bash is still read as a number by `+= 1`
    assert out(ctx, "count=4\n" + block("count += 1\nprint(count * 2)")) == "10\n"


def test_while_counter_without_int(ctx):
    text = block("""
n = 0
while n < 3:
    n += 1
    echo "n is $n"
""")
    assert out(ctx, text) == "n is 1\nn is 2\nn is 3\n"


def test_other_values_are_stored_as_text(ctx):
    out(ctx, block('items = [1, 2]\nnothing = None'))
    assert ctx.variables["items"] == "[1, 2]" and not isinstance(ctx.variables["items"], PyValue)
    assert ctx.variables["nothing"] == "None"


def test_def_return_and_arguments_keep_their_type(ctx):
    text = """def add(a, b):
    return a + b

def double(n):
    echo "doubling $n"
    return n * 2

for -py _once in 1:
    total = add(2, 3)
    print(total + 1, double(total))
    print(add("a", "b"))
"""
    assert out(ctx, text) == "6 10\nab\n"
    # as a command the arguments are words, so text: add 2 3 joins them
    assert out(ctx, "add 2 3") == "23\n"
    assert out(ctx, 'greet() { echo "hi $1"; }\nfor -py _once in 1:\n    msg = greet(5)\n    print(msg)') == "hi 5\n"


def test_local_keeps_and_restores_typed_values(ctx):
    text = """def f():
    local n
    n = 1
    print(n + 1)

for -py _once in 1:
    n = 10
f
for -py _once in 1:
    print(n + 1)
"""
    assert out(ctx, text) == "2\n11\n"


def test_format_specs_with_typed_numbers(ctx):
    assert out(ctx, block('n = 3\nprint(f"{n:>3}|{n * 1.5:.2f}|{n:,}")')) == "  3|4.50|3\n"


# ── input() ──────────────────────────────────────────────────────────────────

def test_int_input_line_stores_a_number(ictx, answers):
    queue, asked = answers
    queue += ["x", "41"]
    run_line('age = int(input("Age? "))', ictx)  # at the prompt
    assert asked == ["Age? ", "Age? "] and ictx.variables["age"] == "41"
    assert "'x' is not a whole number; try again" in ictx.ui.console.file.getvalue()
    assert out(ictx, block("print(age + 1)")) == "42\n"
    queue.append("2.5")
    assert out(ictx, block('r = float(input("R? "))\nprint(r * 2)')) == "5.0\n"
    queue.append("Ada")
    assert out(ictx, block('name = input("Name? ")\nprint(name + "!")')) == "Ada!\n"


def test_input_in_an_expression(ictx, answers):
    queue, asked = answers
    queue += ["Ada", "3", "4"]
    text = block("""
greeting = "Hello " + input("Name? ") + "!"
print(greeting)
print(int(input("A? ")) + int(input("B? ")))
""")
    assert out(ictx, text) == "Hello Ada!\n7\n"
    assert asked == ["Name? ", "A? ", "B? "]
    queue += ["Bo"]
    assert out(ictx, block('who = "x"\nprint(f"hi {input(f\'Name for {who}? \')}")')) == "hi Bo\n"
    assert asked[-1] == "Name for x? "


def test_input_in_conditions(ictx, answers):
    queue, asked = answers
    queue += ["y", "y", "n"]
    text = """count=0
while -py input("Again? ") == "y":
    count=$((count + 1))
echo "ran $count times"
"""
    assert lines(ictx, text) == "ran 2 times\n"
    assert asked == ["Again? "] * 3
    queue += ["yes"]
    assert out(ictx, 'if -py input("Sure? ").startswith("y"):\n    echo sure\nelse:\n    echo no') == "sure\n"
    queue += ["no", "x", "5"]
    text = 'if -py input("A? ") == "yes":\n    echo a\nelif int(input("Num? ")) > 3:\n    echo big'
    assert out(ictx, text) == "big\n"


def test_keep_asking_loop(ictx, answers):
    queue, _ = answers
    queue += ["", "  ", "Ada"]
    text = block("""
name = ""
while name.strip() == "":
    name = input("Name? ")
print("Hello", name)
""")
    assert out(ictx, text) == "Hello Ada\n"


def test_input_in_a_def_return(ictx, answers):
    queue, _ = answers
    queue += ["7"]
    text = """def ask_number(question):
    return int(input(question))

for -py _once in 1:
    n = ask_number("N? ")
    print(n * 2)
"""
    assert out(ictx, text) == "14\n"


def test_input_errors(ictx, answers):
    queue, _ = answers
    shown = []
    ictx.emit_error = lambda exc, where: shown.append(f"{exc}")
    queue.append(KeyboardInterrupt())
    out(ictx, block('x = input("X? ")\necho after $?'))
    assert ictx.variables.get("x") is None and any("cancelled" in s for s in shown)
    queue.append(EOFError())
    result = run_line('while -py input("Again? ") == "y":\n    echo loop\necho never', ictx)
    assert result.status == 1 and "loop" not in to_text(result.output)
    queue.append(KeyboardInterrupt())
    result = run_line('if -py input("Go? ") == "y":\n    echo go', ictx)
    assert result.status == 130
    queue.append(KeyboardInterrupt())
    out(ictx, block('x = input("X? ") + "!"\necho "after $?"'))
    assert ictx.variables.get("x") is None and shown[-1] == "x: input: cancelled"
    out(ictx, block("print(input(5))"))
    assert shown[-1] == "print: input: the prompt must be text, not int"
    out(ictx, block('print(input("a", "b"))'))
    assert "takes one prompt" in shown[-1]


def test_input_errors_jump_to_except_and_set_e(ictx, answers):
    queue, _ = answers
    queue.append(KeyboardInterrupt())
    text = 'try:\n    x = input("X? ")\nexcept as status:\n    echo "failed with $status"'
    assert out(ictx, text) == "✗ input: cancelled\nfailed with 130\n"
    queue.append(EOFError())
    text = 'set -e\nfor -py _once in 1:\n    print(input("X? "))\necho never'
    result = run_line(text, ictx)
    assert "never" not in to_text(result.output) and result.status == 1


def test_input_without_a_terminal(ctx):
    shown = []
    ctx.emit_error = lambda exc, where: shown.append(str(exc))
    out(ctx, block('print("hi " + input("Name? "))'))
    assert any("no terminal" in s for s in shown)


def test_input_is_a_known_function(ctx):
    shown = []
    ctx.emit_error = lambda exc, where: shown.append(str(exc))
    out(ctx, block("print(nope(1))"))
    assert any("built in: " in s and "input" in s for s in shown)
    text = """while -py input("Again? ") == "y":
    echo hi
x = int(input("N? "))
def f():
    total = int(input("A? ")) + 1
    return input("B? ") + "!"
"""
    assert check.check_text(text, "f.sc", ctx).problems == []


def test_input_line_shapes_in_a_block(ictx, answers):
    queue, asked = answers
    queue += ["a", "b", "c"]
    text = block("""
who = "Ada"
question = "Q? "
x = input("Age of $who? ")
y = input(question)
z = input("Z? ") + str(1)
print(x, y, z)
""")
    assert out(ictx, text) == "a b c1\n"
    assert asked == ["Age of Ada? ", "Q? ", "Z? "]  # a string prompt still fills in $who
