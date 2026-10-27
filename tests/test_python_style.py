"""Python-style blocks: a `:` header and an indented body (shellcraft.indent), the brace forms
they become (`select -py`, `case -py`, `def`), mixing with bash, and the prompt's block mode."""

import io

import pytest
from rich.console import Console

from shellcraft import indent, script, startup
from shellcraft.context import ShellContext, to_text
from shellcraft.loader import ModuleRegistry
from shellcraft.parser import ParseError
from shellcraft.pipeline import run_line
from shellcraft.themes import UI, all_themes


def out(ctx, text):
    result = run_line(text, ctx)
    return None if result is None else to_text(result.output)


def lines(ctx, text):
    """Run `text` as a file of lines (as rc and `source` do); the output of every command."""
    shown = []
    for _, line in startup.logical_lines(text):
        result = run_line(line, ctx)
        if result is not None and to_text(result.output):
            shown.append(to_text(result.output))
    return "".join(shown)


def test_for_with_a_nested_if_and_continue(ctx, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "hosts.txt").write_text("web1 frontend\n# c\n\ndb1 database\n")
    text = """for -py line in (cat hosts.txt):
    if line == "" or line.startswith("#"):
        continue
    echo "host: $line"
"""
    assert out(ctx, text) == "host: web1 frontend\nhost: db1 database\n"
    assert lines(ctx, text) == "host: web1 frontend\nhost: db1 database\n"


def test_if_elif_else_on_its_own(ctx):
    text = """if -py age == "":
    echo "no age"
elif int(age) >= 18:
    echo adult
else:
    echo minor"""
    for age, said in (("20", "adult\n"), ("", "no age\n"), ("9", "minor\n")):
        ctx.variables["age"] = age
        assert out(ctx, text) == said


def test_a_condition_that_isnt_python_runs_as_a_command(ctx, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "app.log").write_text("ERROR disk\n")
    assert out(ctx, "if -py grep -q ERROR app.log:\n    echo found\nelse:\n    echo clean") == "found\n"
    assert out(ctx, "if -py grep -q NOPE app.log:\n    echo found\nelse:\n    echo clean") == "clean\n"
    assert indent.translate("if -py x > 3:\n    echo big").startswith("if -py (x > 3) {")
    assert indent.translate("if -py test -f x:\n    echo f").startswith("if -py test -f x {")


def test_while_until_and_one_liners(ctx):
    ctx.variables["n"] = "0"
    assert out(ctx, 'while -py int(n) < 3:\n    echo "n=$n"\n    n=$((n + 1))') == "n=0\nn=1\nn=2\n"
    assert out(ctx, "until -py int(n) == 0:\n    n=$((n - 1))\necho $n") == "0\n"
    assert out(ctx, 'for -py n in 1..3: echo "one $n"') == "one 1\none 2\none 3\n"
    assert out(ctx, 'if -py n == "3": echo yes') == "yes\n"  # the loop left n at 3
    assert out(ctx, 'x=a\nif -py x == "a": echo yes\nelse: echo no') == "yes\n"
    assert out(ctx, 'x=b\nif -py x == "a": echo yes\nelse: echo no') == "no\n"


def test_three_levels_blank_lines_and_pass(ctx):
    text = """for -py a in 1 2:

    for b in x y:
        if b == "y":
            pass
        else:
            echo "$a$b"
    # a comment line inside the block
    echo "end $a"
echo after"""
    assert out(ctx, text) == "1x\nend 1\n2x\nend 2\nafter\n"


def test_bash_inside_a_colon_block_and_a_colon_block_inside_bash(ctx):
    text = """for -py a in x y:
    if [ $a = y ]; then
        echo "bash if: $a"
    fi
    for z in p q; do echo "$a$z"; done"""
    assert out(ctx, text) == "xp\nxq\nbash if: y\nyp\nyq\n"
    rc = """old() {
  for -py k in 1..2:
      echo "in a bash function $k"
}
old"""
    assert lines(ctx, rc) == "in a bash function 1\nin a bash function 2\n"


def test_def_with_parameters_defaults_and_missing_ones(ctx):
    lines(ctx, '''def greet(name, greeting="Hello"):
    if name == "Ada":
        echo "Welcome back, Ada ($# args)"
    else:
        echo "$greeting, $name"''')
    assert out(ctx, "greet Ada") == "Welcome back, Ada (1 args)\n"
    assert out(ctx, "greet Sam Hi") == "Hi, Sam\n"
    assert "name" not in ctx.variables and "greeting" not in ctx.variables  # parameters are local
    ctx.variables["name"] = "outer"
    out(ctx, "greet Bo")
    assert ctx.variables["name"] == "outer"
    with pytest.raises(Exception, match="missing argument 'name'"):
        run_line("greet", ctx)
    out(ctx, 'def add(a, b=10) { echo $((a + b)); }')
    assert out(ctx, "add 5") == "15\n" and out(ctx, "add 5 1") == "6\n"
    with pytest.raises(ParseError, match="only plain parameters"):
        run_line("def bad(*rest) { echo; }", ctx)
    with pytest.raises(ParseError, match="a default must be text or a number"):
        run_line("def bad(a=[1]) { echo; }", ctx)


def test_def_return_and_status(ctx):
    lines(ctx, '''def is_weekend(day):
    case -py $day:
        "Sat" | "Sun":
            return 0
        _:
            return 1''')
    assert out(ctx, "is_weekend Sat && echo off || echo work") == "off\n"
    assert out(ctx, "is_weekend Mon && echo off || echo work") == "work\n"


def test_case_py_patterns(ctx):
    text = """for choice in 1 q Q x.py "two words" zz; do
  case -py "$choice":
      "1":
          echo starting
      "q" | "Q":
          echo bye
      "*.py":
          echo "a python file"
      "two words":
          echo spaces
      _:
          echo "not on the menu"
done"""
    assert lines(ctx, text) == ("starting\nbye\nbye\na python file\nspaces\nnot on the menu\n")
    assert out(ctx, 'c=b; case -py $c { a { echo A } b | c { echo BC } _ { echo other } }') == "BC\n"
    assert out(ctx, 'c=zz; case -py $c { a { echo A } }') == ""
    assert out(ctx, 'c=b; case $c in a) echo A;; b) echo bash;; esac') == "bash\n"  # bash case unchanged


def test_select_py(ictx, answers):
    queue, _ = answers
    queue += ["9", "2"]
    text = """PS3="Which? "
select -py env in dev prod:
    if env == "":
        echo "pick 1 or 2"
        continue
    echo "deploying to $env"
    break"""
    assert out(ictx, text) == "pick 1 or 2\ndeploying to prod\n"


def test_indentation_errors_name_the_line(ctx):
    cases = {
        "for -py x in a b:\necho $x": "line 2: expected an indented block",
        "for -py x in a:\n    if x == 'a':\n        echo a\n  echo bad": "line 4: unindent does not match",
        "for -py x in a:\n\techo a\n        echo b": "line 3: inconsistent use of tabs",
        "echo hi\nelse:\n    echo x": "line 2: 'else' without a matching if",
        "case -py $x:\n    echo hi": "each choice is PATTERN:",
    }
    for text, message in cases.items():
        with pytest.raises(indent.IndentError, match=message):
            indent.to_braces(text)


def test_a_badly_indented_file_does_not_run(ctx, tmp_path):
    shown = []
    ctx.emit_error = lambda exc, line: shown.append(str(exc))
    rc = tmp_path / "rc"
    rc.write_text("echo first > ran.txt\nfor -py x in a:\necho $x\n")
    assert startup.source(rc, ctx) == (0, 1)
    assert shown and "expected an indented block" in shown[0]
    assert not (tmp_path / "ran.txt").exists()


def test_the_brace_form_and_plain_lines_are_unchanged(ctx):
    assert out(ctx, 'for -py n in 1..2 { if (n == "2") { echo two } else { echo $n } }') == "1\ntwo\n"
    assert out(ctx, "echo time: now") == "time: now\n"
    assert out(ctx, "for x in a: b; do echo $x; done") == "a:\nb\n"  # bash, with a colon in it
    assert not indent.has_colon_block("echo for -py x in a:")
    assert indent.translate("for -py x in a b {\n echo $x\n}") == "for -py x in a b {\n echo $x\n}"


def test_prompt_block_mode_prefills_indentation_and_ends_on_a_blank_line(repl_shell):
    sh, ctx = repl_shell
    typed = iter(["    if n == \"2\":", "        echo two", "    else:", "        echo $n", "        "])
    prefills = []

    def fake_ask(message):
        prefills.append(ctx.prefill)
        ctx.prefill = ""
        return next(typed)

    sh._ask = fake_ask
    text = sh._read_continuation("for -py n in 1..3:")
    assert prefills == ["    ", "        ", "        ", "        ", "        "]
    assert text == 'for -py n in 1..3:\n    if n == "2":\n        echo two\n    else:\n        echo $n'
    run_line(text, ctx)  # shown as it runs, through the shell's console
    assert ctx.ui.console.file.getvalue().split() == ["1", "two", "3"]


def test_prompt_block_mode_reports_bad_indentation(repl_shell):
    sh, ctx = repl_shell
    typed = iter(["echo not indented", ""])
    sh._ask = lambda message: next(typed)
    assert sh._read_continuation("for -py n in 1..3:") is None
    assert ctx.last_status == 2
    assert script.incomplete("for -py n in 1..3: echo $n") is None  # a one-liner needs no more lines


@pytest.fixture
def ictx(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / "home"))
    ctx = ShellContext(registry=ModuleRegistry(tmp_path / "modules"))
    ctx.interactive = True
    ctx.ui = UI(all_themes({})["nord"], Console(file=io.StringIO(), width=80))
    return ctx


@pytest.fixture
def answers(monkeypatch):
    queue = []

    def fake(prompt, is_password=False):
        return queue.pop(0)

    import prompt_toolkit

    monkeypatch.setattr(prompt_toolkit, "prompt", fake)
    return queue, None


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


def test_input_inside_blocks_and_def(ictx, answers):
    queue, _ = answers
    queue += ["", "Bo", "x", "40"]
    text = """name=""
while -py name == "":
    name = input("Name: ")
def ask_age(who):
    age = int(input("Age of $who? "))
    if int(age) >= 18:
        echo "$who ($name) is an adult"
ask_age Ada"""
    assert lines(ictx, text) == "Ada (Bo) is an adult\n"
    assert ictx.variables["age"] == "40"  # not local: the shell's own, as with read
