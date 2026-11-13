"""Arrays, lists and dicts in the tools around the language: `source -n` (shellcraft.check), the
colours of the line you type (shellcraft.highlight), Tab after `hosts.` (shellcraft.completer) and
a value that goes on over lines inside a colon block (shellcraft.indent)."""

import pytest
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from shellcraft import check, indent
from shellcraft.completer import ShellCompleter
from shellcraft.highlight import highlight
from shellcraft.pipeline import run_line

CLEAN = '''a=(x y "z w")
a+=(q)
a[0]=first
declare -A port
port[web]=80
declare -a list=(1 2)
fn() { local l=(1 2); echo "${l[@]}"; }
mapfile -t lines < hosts.txt
read -a parts <<< "a b"
unset 'a[-1]'
echo "${a[0]} ${a[@]} ${#a[@]} ${!port[@]} $(( a[1] + 1 ))"
for v in "${a[@]}"; do echo "$v"; done
hosts = ["web01", "db01"]
d = {"web": 80}
hosts.append("c")
hosts[0] = "x"
d["db"] = 5432
del d["web"]
x = hosts.pop()
print(sum(map(int, [1, 2])), sorted(hosts), ", ".join(hosts))
for -py h in hosts:
    print(h)
for -py k, v in d.items():
    print(k, v)
multi=(
  one
  two
)
more = [
  1,
  2,
]
'''


def problems(ctx, text):
    return [(p.line, p.message, p.error) for p in check.check_text(text, "f.sc", ctx).problems]


# --- source -n ---

def test_the_checker_accepts_every_spelling(ctx):
    assert problems(ctx, CLEAN.replace("more = [", "extra = [")) == []


def test_the_checker_runs_nothing(ctx):
    check.check_text(CLEAN, "f.sc", ctx)
    assert "a" not in ctx.variables and "hosts" not in ctx.variables


def test_commands_inside_an_array_are_checked(ctx):
    found = problems(ctx, "b=( $(nosuchcmd) )\nc[0]=$(nosuchtwo)\n")
    assert [(line, error) for line, _, error in found] == [(1, False), (2, False)]
    assert "unknown command 'nosuchcmd'" in found[0][1] and "unknown command 'nosuchtwo'" in found[1][1]


def test_an_unclosed_literal_is_an_error(ctx):
    assert problems(ctx, "a=(x y\necho hi\n") == [(1, "missing ')': the block that starts here never ends", True)]
    found = problems(ctx, "h = [1,\n2\necho hi\n")
    assert len(found) == 1 and found[0][0] == 1 and found[0][2] and "missing a closing bracket" in found[0][1]


def test_what_python_refuses_is_an_error_with_its_line(ctx):
    text = "h = [1, 2]\nif -py (h.pop()) { echo x }\nh.sort(key=len)\nx = [v for v in h]\n"
    found = problems(ctx, text)
    assert [(line, error) for line, _, error in found] == [(2, True), (3, True), (4, True)]
    assert "'.pop()' changes h" in found[0][1] and "no key=" in found[1][1] and "'ListComp' is not allowed" in found[2][1]


def test_a_method_that_does_not_exist_is_a_warning(ctx):
    found = problems(ctx, 'hosts = []\nhosts.push("x")\n')
    assert len(found) == 1 and found[0][0] == 2 and not found[0][2]
    assert found[0][1].startswith("'.push()' is not a method that changes a list or dict (append, clear, ")


# --- colours ---

def styles(line, known=("echo", "cat")):
    return [(style.replace("class:", ""), text) for style, text in highlight(line, lambda n: n in known) if text.strip()]


@pytest.mark.parametrize("line, first", [
    ("a=(x y z)", "a=(x"), ("a+=(q)", "a+=(q)"), ("a[0]=first", "a[0]=first"), ("port[web]=80", "port[web]=80"),
    ('hosts = ["a"]', "hosts"), ('hosts[0] = "x"', "hosts[0]"), ("n += 1", "n"), ('hosts.append("c")', "hosts.append("),
])
def test_what_sets_a_variable_is_coloured_as_one(line, first):
    assert styles(line)[0] == ("variable", first)


def test_the_elements_of_an_array_are_not_commands():
    assert styles("a=(x y); nosuch arg") == [("variable", "a=(x"), ("", "y)"), ("operator", ";"),
                                             ("cmd.unknown", "nosuch"), ("", "arg")]


def test_a_command_keeps_its_colour():
    assert styles("echo = hi")[0] == ("cmd.known", "echo")
    assert styles("nosuch == 5")[0] == ("cmd.unknown", "nosuch")
    assert styles("x=5 echo hi")[:2] == [("variable", "x=5"), ("cmd.known", "echo")]


# --- Tab ---

def complete(ctx, text):
    return [c.text for c in ShellCompleter(ctx).get_completions(Document(text), CompleteEvent())]


def test_tab_offers_the_methods_of_a_list(ctx):
    run_line('hosts = ["a"]', ctx)
    assert complete(ctx, "hosts.ap") == ["append("]
    assert complete(ctx, "hosts.") == ["append(", "clear()", "copy()", "count(", "extend(", "index(", "insert(",
                                       "pop(", "remove(", "reverse()", "sort()"]
    assert complete(ctx, "x = hosts.po") == ["pop("]
    assert complete(ctx, "print(hosts.co") == ["copy()", "count("]


def test_tab_offers_the_methods_of_a_dict(ctx):
    run_line('port = {"web": 80}', ctx)
    assert complete(ctx, "port.k") == ["keys()"]
    assert "append(" not in complete(ctx, "port.")


def test_tab_leaves_other_words_alone(ctx, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "notes.txt").write_text("")
    run_line("notes=text", ctx)
    assert complete(ctx, "cat notes.") == ["txt"]


# --- colon blocks ---

def braces(text):
    return [line for _, line in indent.to_braces(text)]


def test_a_value_over_lines_keeps_its_own_indentation():
    text = 'if -py ok:\n    d = {\n"a": 1,\n            "b": 2,\n    }\n    print(d)\necho after\n'
    assert braces(text) == ["if -py (ok) {", "    d = {", '"a": 1,', '            "b": 2,', "    }", "    print(d)",
                            "}", "echo after"]


def test_a_call_over_lines_in_a_block():
    text = 'for -py h in hosts:\n    print(h,\n  "x")\n    names.extend([\nh,\n    ])\n'
    assert braces(text)[1:6] == ["    print(h,", '  "x")', "    names.extend([", "h,", "    ])"]


def test_a_bracket_in_a_command_opens_nothing():
    text = "if -py ok:\n    echo [\n    echo done\n"
    assert braces(text) == ["if -py (ok) {", "    echo [", "    echo done", "}"]


def test_a_bracket_in_quotes_opens_nothing():
    text = 'if -py ok:\n    s = "["\n    echo $s\n'
    assert braces(text) == ["if -py (ok) {", '    s = "["', "    echo $s", "}"]


# --- man arrays ---

def test_man_arrays_is_a_topic_page(ctx):
    from shellcraft.context import to_text

    page = to_text(run_line("man -p arrays", ctx).output)
    assert page.startswith("# arrays\n") and "declare -A port" in page and "hosts.append" in page
    assert "help topic" in page
    assert to_text(run_line("help arrays", ctx).output) == "arrays: a help topic, not a command (see: man arrays)\n"
    assert "arrays" in complete(ctx, "man arr")
    run_line('arrays = ["a"]', ctx)  # the topic is not a command: the name stays free for a variable
    assert to_text(run_line("print(arrays)", ctx).output) == "['a']\n"


def test_the_examples_of_man_arrays_run(ctx, tmp_path, monkeypatch):
    import re

    from shellcraft import startup
    from shellcraft.builtins import ARRAYS_DOC

    monkeypatch.chdir(tmp_path)
    (tmp_path / "hosts.txt").write_text("web01\ndb01\n")
    (tmp_path / "app.log").write_text("")
    problems: list[str] = []
    ctx.emit = lambda value: None
    ctx.emit_error = lambda exc, line: problems.append(f"{line}: {exc}")
    blocks = re.findall(r"^```\n(.*?)^```", ARRAYS_DOC, re.M | re.S)
    assert len(blocks) == 4
    for number, code in enumerate(blocks):
        script = tmp_path / f"block{number}.sc"
        script.write_text(code)
        startup.source(script, ctx)
    assert problems == []
    assert ctx.variables["lines"].items == ("web01", "db01") and ctx.variables["files"].items == ("app.log",)
