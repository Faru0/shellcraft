"""One storage for both styles: a bash array is a Python list, a `declare -A` array is a Python
dict. What one style writes the other reads, and a copy (`$( … )`, `( … )`, `local`, a function's
argument) never changes the original."""

import pytest

from shellcraft.context import to_text
from shellcraft.pipeline import PipelineError, run_line
from shellcraft.values import ShArray, ShMap


def out(ctx, text):
    result = run_line(text, ctx)
    return None if result is None else to_text(result.output)


def run(ctx, *texts):
    shown = None
    for text in texts:
        shown = out(ctx, text)
    return shown


# --- bash writes, Python reads ---

def test_python_reads_a_bash_array(ctx):
    out(ctx, 'a=(x "y z")')
    assert out(ctx, 'print(a, len(a), a[-1], "x" in a)') == "['x', 'y z'] 2 y z True\n"
    assert out(ctx, 'for -py v in a { echo "<$v>" }') == "<x>\n<y z>\n"


def test_python_changes_a_bash_array(ctx):
    assert run(ctx, "a=(x y)", 'a.append("z")', 'a[0] = "first"', 'echo "${a[@]} ${#a[@]}"') == "first y z 3\n"


def test_python_reads_and_changes_a_declared_dict(ctx):
    run(ctx, "declare -A m", "m[web]=80", 'm["db"] = 5432')
    assert isinstance(ctx.variables["m"], ShMap)
    assert out(ctx, 'print(m, sorted(m), int(m["web"]) + 1)') == "{'web': '80', 'db': 5432} ['db', 'web'] 81\n"
    assert out(ctx, 'echo "${m[db]} ${!m[@]}"') == "5432 web db\n"


def test_values_set_from_bash_are_text(ctx):
    run(ctx, "n = [3, 1]", "n+=(5)")
    assert out(ctx, "print(n)") == "[3, 1, '5']\n"
    assert "values set from bash are text" in out(ctx, "print(sum(n))")
    assert out(ctx, "print(sum(map(int, n)))") == "9\n"


def test_read_and_mapfile_give_lists(ctx, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "hosts.txt").write_text("web01\ndb01\n")
    out(ctx, "mapfile -t l < hosts.txt")
    assert out(ctx, 'print(l[0], len(l), ", ".join(l))') == "web01 2 web01, db01\n"
    out(ctx, 'read -a parts <<< "one two three"')
    assert out(ctx, "print(parts[-1], sorted(parts))") == "three ['one', 'three', 'two']\n"


# --- Python writes, bash reads ---

def test_bash_reads_a_python_list(ctx):
    out(ctx, 'h = ["p", "q r"]')
    assert out(ctx, 'echo "$h|${h[1]}|${#h[@]}|${h[@]}|${!h[@]}"') == "p|q r|2|p q r|0 1\n"
    assert out(ctx, 'for v in "${h[@]}"; do echo "<$v>"; done') == "<p>\n<q r>\n"
    assert out(ctx, "declare -p h") == 'declare -a h=([0]="p" [1]="q r")\n'


def test_bash_changes_a_python_list(ctx):
    assert run(ctx, 'h = ["p", "q"]', "h+=(r)", "h[0]=first", "print(h)") == "['first', 'q', 'r']\n"
    assert run(ctx, "unset 'h[-1]'", "print(h)") == "['first', 'q']\n"


def test_bash_reads_a_python_dict(ctx):
    out(ctx, 'd = {"web": 80, "db": 5432}')
    assert out(ctx, 'echo "${d[web]} ${#d[@]} ${!d[@]} ${d[@]}"') == "80 2 web db 80 5432\n"
    assert out(ctx, "declare -p d") == 'declare -A d=([web]="80" [db]="5432")\n'
    assert run(ctx, "d[web]=8080", 'print(d["web"])') == "8080\n"


def test_arithmetic_reads_a_python_list(ctx):
    out(ctx, "n = [3, 4]")
    assert out(ctx, "echo $(( n[0] * n[1] ))") == "12\n"


def test_words_of_a_command(ctx):
    run(ctx, 'h = ["a", "b c"]', 'show() { echo "$# args: $1"; }')
    assert out(ctx, 'show "${h[@]}"') == "2 args: a\n"
    assert out(ctx, "show $h") == "1 args: a\n"


def test_a_bash_loop_body_may_use_python(ctx):
    out(ctx, 'c = ["a", "b"]')
    assert out(ctx, 'for v in "${c[@]}"; do w = v.upper(); echo $w; done') == "A\nB\n"


# --- copies stay apart ---

def test_a_command_substitution_changes_its_own_copy(ctx):
    out(ctx, 'h = ["a"]')
    out(ctx, 'x=$(h.append("sub"); echo "${#h[@]}")')
    assert out(ctx, 'echo "$x ${#h[@]}"') == "2 1\n"


def test_a_subshell_changes_its_own_copy(ctx):
    out(ctx, 'h = ["a"]')
    assert out(ctx, '( h.append("inner"); echo "${#h[@]}" )') == "2\n"
    assert ctx.variables["h"].items == ("a",)


def test_a_local_array_ends_with_its_function(ctx):
    out(ctx, 'h = ["outer"]')
    out(ctx, 'f() { local h; h=(only); h.append("mine"); echo "${h[@]}"; }')
    assert out(ctx, 'f; echo "${h[@]}"') == "only mine\nouter\n"


def test_a_function_changes_a_global_array(ctx):
    out(ctx, 'h = ["a"]')
    out(ctx, 'g() { h.append("more"); }')
    assert out(ctx, 'g; echo "${h[@]}"') == "a more\n"


def test_a_list_argument_is_a_copy(ctx):
    run(ctx, 'h = ["a"]', 'def grow(items) { items.append("more") ; return len(items) }')
    assert out(ctx, "print(grow(h), len(h))") == "2 1\n"
    run(ctx, 'def push(items, v) { items.append(v) ; return items }', 'h = push(h, "b")')
    assert ctx.variables["h"].items == ("a", "b")


def test_another_name_is_another_list(ctx):
    run(ctx, 'h = ["a"]', "b = h", 'b.append("b")', "c = h.copy()", 'c.append("c")')
    assert ctx.variables["h"].items == ("a",)
    assert ctx.variables["b"].items == ("a", "b") and ctx.variables["c"].items == ("a", "c")


def test_an_array_is_not_exported(ctx):
    out(ctx, 'h = ["a"]')
    with pytest.raises(PipelineError, match="an array or dict is not passed on to programs"):
        out(ctx, "export h")
    assert isinstance(ctx.variables["h"], ShArray)


def test_unset_removes_it_for_both(ctx):
    run(ctx, 'h = ["a"]', "unset h")
    assert out(ctx, 'echo "[${h[@]}]"') == "[]\n"
    assert out(ctx, 'h.append("x")') == "✗ h: not set, give it a value first (h = [])\n"
