"""Python lists and dicts, the changing side: `hosts.append("c")`, `hosts[0] = "x"`, `del d["k"]`,
`x = queue.pop()`. Every change stores a new value under the name (shellcraft.script)."""

import pytest

from shellcraft import startup
from shellcraft.context import to_text
from shellcraft.parser import ParseError
from shellcraft.pipeline import PipelineError, run_line
from shellcraft.values import ShArray, ShMap


def out(ctx, text):
    result = run_line(text, ctx)
    return None if result is None else to_text(result.output)


def run(ctx, *texts):
    """Run the lines one after another; the output of the last one."""
    shown = None
    for text in texts:
        shown = out(ctx, text)
    return shown


def lines(ctx, text):
    shown = []
    for _, line in startup.logical_lines(text, known=set(ctx.functions)):
        result = run_line(line, ctx)
        if result is not None and to_text(result.output):
            shown.append(to_text(result.output))
    return "".join(shown)


# --- methods on a line of their own ---

def test_append_stores_a_new_array(ctx):
    out(ctx, 'h = ["a"]')
    before = ctx.variables["h"]
    assert out(ctx, 'h.append("b")') in (None, "")
    assert ctx.variables["h"].items == ("a", "b") and before.items == ("a",)
    assert out(ctx, 'echo "${h[@]} ${#h[@]}"') == "a b 2\n"


def test_list_methods(ctx):
    assert run(ctx, 'n = ["b", "a"]', "n.sort()", 'n.extend(["z"])', 'n.insert(0, "first")', 'n.remove("a")',
               "n.reverse()", "print(n)") == "['z', 'b', 'first']\n"
    assert run(ctx, "n.sort(reverse=True)", "print(n)") == "['z', 'first', 'b']\n"
    assert run(ctx, "n.clear()", "print(n, len(n))") == "[] 0\n"
    assert isinstance(ctx.variables["n"], ShArray)


def test_dict_methods(ctx):
    assert run(ctx, 'd = {"a": 1}', 'd.update({"b": 2, "a": 5})', "print(d)") == "{'a': 5, 'b': 2}\n"
    assert run(ctx, 'print(d.setdefault("a", 9), d.setdefault("c", 9), d)') == "5 9 {'a': 5, 'b': 2, 'c': 9}\n"
    assert run(ctx, 'print(d.pop("a"), d.pop("gone", "none"), d)') == "5 none {'b': 2, 'c': 9}\n"
    assert run(ctx, "d.clear()", "print(d)") == "{}\n"
    assert isinstance(ctx.variables["d"], ShMap)


def test_a_method_call_over_lines(ctx):
    text = 'h = []\nh.extend([\n    "x",\n    "y",   # two of them\n])\nprint(h)\n'
    assert lines(ctx, text) == "['x', 'y']\n"


def test_pop_gives_the_element(ctx):
    out(ctx, 'q = ["a", "b", "c"]')
    assert run(ctx, "x = q.pop()", 'echo "$x ${q[@]}"') == "c a b\n"
    assert run(ctx, "print(q.pop(0), q)") == "a ['b']\n"
    assert ctx.variables["q"].items == ("b",)


def test_a_queue_in_a_while_loop(ctx):
    out(ctx, "q = [1, 2, 3]")
    assert out(ctx, "while -py (q) { print(q.pop(0), len(q)) }") == "1 2\n2 1\n3 0\n"


def test_a_changing_method_is_refused_in_a_condition(ctx):
    out(ctx, 'h = ["a"]')
    with pytest.raises(ParseError, match=r"'\.pop\(\)' changes h: use it on a line of its own \(h\.pop\(…\)\), "
                                         "not in a condition"):
        out(ctx, "if -py (h.pop()) { echo x }")
    assert ctx.variables["h"].items == ("a",)


def test_methods_on_the_wrong_value(ctx):
    out(ctx, "name=text")
    assert out(ctx, 'name.append("y")') == "✗ name: '.append()' works on a list, not on str\n"
    assert out(ctx, 'nothing.append("y")') == "✗ nothing: not set, give it a value first (nothing = [])\n"
    assert "nothing" not in ctx.variables
    out(ctx, 'h = ["a"]')
    assert out(ctx, 'h.remove("zz")') == "✗ h: ValueError: list.remove(x): x not in list\n"
    assert out(ctx, 'h.append("a", "b")') == "✗ h: list.append() takes exactly one argument (2 given)\n"
    with pytest.raises(ParseError, match=r"sort takes only reverse=True \(no key=\)"):
        out(ctx, "h.sort(key=len)")


def test_nothing_nested(ctx):
    run(ctx, 'h = ["a"]', 'd = {"a": 1}')
    refusal = "can hold only text and numbers, not another list or dict\n"
    assert out(ctx, 'h.append(["n"])') == "✗ h: a list " + refusal
    assert out(ctx, "h[0] = [1]") == "✗ h: a list " + refusal
    assert out(ctx, 'd["l"] = {"x": 1}') == "✗ d: a dict " + refusal
    assert ctx.variables["h"].items == ("a",) and ctx.variables["d"].to_python() == {"a": 1}


def test_no_way_out_of_the_sandbox(ctx):
    run(ctx, 'h = ["a"]', 'd = {"a": 1}')
    for text in ("x = h.__class__", "x = h.append.__self__", 'x = d.get.__call__("a")', "x = h.copy.__globals__"):
        with pytest.raises(ParseError, match="is not available"):
            out(ctx, text)
    assert "x" not in ctx.variables


def test_a_list_cannot_grow_without_end(ctx):
    out(ctx, 'a = ["x" * 1000]')
    shown = out(ctx, "for -py i in range(40) { a += a }")
    assert "✗ a: '+' would build more than 10000000 characters" in shown
    assert len(ctx.variables["a"].items) == 8192


# --- one element ---

def test_set_an_element(ctx):
    out(ctx, 'h = ["a", "b"]')
    assert run(ctx, 'h[0] = "first"', 'h[-1] += "!"', "print(h)") == "['first', 'b!']\n"
    assert out(ctx, 'h[9] = "z"') == "✗ h: there is no element 9 (it has 2; h.append(…) adds one)\n"
    assert out(ctx, 'h["x"] = 2') == "✗ h: TypeError: list indices must be integers or slices, not str\n"
    assert run(ctx, 'h[0:1] = ["s", "t"]', "print(h)") == "['s', 't', 'b!']\n"


def test_set_a_key(ctx):
    out(ctx, 'd = {"n": 1}')
    assert run(ctx, 'd["db"] = 5432', 'd["n"] += 5', "print(d)") == "{'n': 6, 'db': 5432}\n"
    assert out(ctx, 'echo "${d[db]} ${!d[@]}"') == "5432 n db\n"
    assert out(ctx, 'd["new"] += 1') == "✗ d: there is no key 'new' for += to add to (set it first: d['new'] = …)\n"
    assert out(ctx, "d[1] = 2") == '✗ d: dict keys must be text ("1", not 1)\n'


def test_no_space_before_the_equals_sign_is_bash(ctx):
    out(ctx, "a=(x y)")
    out(ctx, 'a[0]="quoted"')
    assert ctx.variables["a"].items == ("quoted", "y")
    out(ctx, 'a[1] = "py"')
    assert ctx.variables["a"].items == ("quoted", "py")


def test_plus_equals_on_a_list(ctx):
    out(ctx, 'h = ["a"]')
    assert run(ctx, 'h += ["p", "q"]', "print(h)") == "['a', 'p', 'q']\n"
    assert 'to add one element: hosts.append("x") or hosts += ["x"]' in out(ctx, 'h += "r"')
    assert ctx.variables["h"].items == ("a", "p", "q")


# --- del ---

def test_del_an_element_moves_the_rest_up(ctx):
    out(ctx, 'h = ["a", "b", "c"]')
    assert run(ctx, "del h[0]", 'echo "${h[@]} ${h[0]}"') == "b c b\n"
    assert out(ctx, "del h[7]") == "✗ h: there is no element 7 (it has 2)\n"
    assert run(ctx, "del h[-1]", "print(h)") == "['b']\n"


def test_del_a_key(ctx):
    out(ctx, 'd = {"web": 80, "db": 5432}')
    assert run(ctx, 'del d["web"]', "print(d)") == "{'db': 5432}\n"
    assert out(ctx, 'del d["zz"]') == "✗ d: there is no key 'zz'\n"
    assert out(ctx, "del gone[0]") == "✗ gone: not set, give it a value first (gone = [] or gone = {})\n"


def test_del_a_name_in_python_code(ctx):
    run(ctx, 'h = ["a"]', 'd = {"a": 1}')
    out(ctx, 'for -py _once in 1 { del h ; del d["a"] }')
    assert "h" not in ctx.variables and ctx.variables["d"].to_python() == {}


def test_del_at_the_prompt_stays_a_command(ctx):
    out(ctx, "notes=keep")
    with pytest.raises(PipelineError, match="command not found: del"):
        out(ctx, "del notes")
    assert ctx.variables["notes"] == "keep"


# --- readonly, errexit ---

def test_a_readonly_list_cannot_change(ctx):
    out(ctx, "readonly ro=(1 2)")
    for text in ('ro.append("3")', 'ro[0] = "9"', "del ro[0]", 'ro += ["4"]'):
        with pytest.raises(PipelineError, match="ro: readonly variable"):
            out(ctx, text)
    assert ctx.variables["ro"].items == ("1", "2")


def test_a_failed_change_sets_the_status(ctx):
    out(ctx, 'h = ["a"]')
    assert out(ctx, "h.pop(9); echo status $?") == "✗ h: IndexError: pop index out of range\nstatus 1\n"
    assert out(ctx, 'h.append("b"); echo status $?') == "status 0\n"
