"""Python lists and dicts, the reading side: literals, `hosts[0]`, slices, `len`, `in`, `sorted`,
`", ".join(…)`, loops over a value, f-strings (shellcraft.script, shellcraft.values)."""

import pytest

from shellcraft import script, startup
from shellcraft.context import to_text
from shellcraft.parser import ParseError
from shellcraft.pipeline import PipelineError, run_line
from shellcraft.values import PyValue, ShArray, ShMap


def out(ctx, text):
    result = run_line(text, ctx)
    return None if result is None else to_text(result.output)


def lines(ctx, text):
    """Run `text` as a file (as rc and `source` do): every line's output, errors as `ERROR …`."""
    shown = []
    for _, line in startup.logical_lines(text, known=set(ctx.functions)):
        try:
            result = run_line(line, ctx)
        except PipelineError as exc:
            shown.append(f"ERROR {exc}\n")
            continue
        if result is not None and to_text(result.output):
            shown.append(to_text(result.output))
    return "".join(shown)


# --- literals ---

def test_a_list_literal_is_an_array(ctx):
    out(ctx, 'hosts = ["web01", "db01"]')
    held = ctx.variables["hosts"]
    assert isinstance(held, ShArray) and held.items == ("web01", "db01")
    assert out(ctx, 'echo "$hosts ${hosts[1]} ${#hosts[@]}"') == "web01 db01 2\n"


def test_a_dict_literal_is_a_map(ctx):
    out(ctx, 'port = {"web": 80, "db": 5432}')
    held = ctx.variables["port"]
    assert isinstance(held, ShMap) and held.to_python() == {"web": 80, "db": 5432}
    assert out(ctx, 'echo "${port[db]} ${!port[@]}"') == "5432 web db\n"


def test_numbers_stay_numbers_in_a_list(ctx):
    out(ctx, "sizes = [3, 1, 2]")
    assert all(isinstance(item, PyValue) for item in ctx.variables["sizes"].items)
    assert out(ctx, "print(sum(sizes), max(sizes), sorted(sizes))") == "6 3 [1, 2, 3]\n"


def test_empty_literals(ctx):
    out(ctx, "a = []")
    out(ctx, "d = {}")
    assert isinstance(ctx.variables["a"], ShArray) and isinstance(ctx.variables["d"], ShMap)
    assert out(ctx, 'print(len(a), len(d), a, d)') == "0 0 [] {}\n"


def test_a_tuple_is_stored_as_a_list(ctx):
    out(ctx, 'for -py _once in 1 { pair = ("a", 1) }')
    assert ctx.variables["pair"].to_python() == ["a", 1]


def test_split_gives_a_list_and_bash_sees_its_first_element(ctx):
    out(ctx, 'parts = "a b c".split()')
    assert out(ctx, "echo $parts") == "a\n"
    assert out(ctx, 'echo "${parts[@]}"') == "a b c\n"
    assert out(ctx, "print(parts)") == "['a', 'b', 'c']\n"


def test_a_nested_list_is_refused(ctx):
    assert out(ctx, "grid = [[1, 2], [3]]") == \
        "✗ grid: a list can hold only text and numbers, not another list or dict\n"
    assert "grid" not in ctx.variables


def test_a_dict_key_must_be_text(ctx):
    assert out(ctx, "d = {1: 2}") == '✗ d: dict keys must be text ("1", not 1)\n'


def test_a_dict_in_a_brace_block(ctx):
    assert out(ctx, 'if -py (1) { d = {"a": 1} ; print(d["a"]) }') == "1\n"
    assert out(ctx, "if -py (1) { y = [1, 2] ; print(y) }") == "[1, 2]\n"


def test_a_bracket_inside_quotes_does_not_open_a_list(ctx):
    out(ctx, 's = "["')
    assert ctx.variables["s"] == "["


def test_an_open_bracket_wants_more_lines(ctx):
    with pytest.raises(script.IncompleteError) as caught:
        run_line("x = [1,", ctx)
    assert caught.value.join == " "
    assert script.incomplete("x = [1,") and not script.incomplete("x = [1, 2]")
    assert script.incomplete('hosts.extend(["a",')


# --- reading ---

def test_subscripts_and_slices(ctx):
    out(ctx, 'h = ["a", "b", "c", "d"]')
    assert out(ctx, "print(h[0], h[-1], h[1:3], h[:2], h[::2])") == "a d ['b', 'c'] ['a', 'b'] ['a', 'c']\n"


def test_a_missing_element_and_key(ctx):
    out(ctx, 'h = ["a"]')
    out(ctx, 'd = {"k": "v"}')
    assert out(ctx, "print(h[5])") == "✗ print: IndexError: list index out of range\n"
    assert out(ctx, 'print(d["zz"])') == "✗ print: there is no key 'zz' (.get('zz') gives None for a missing key)\n"
    assert out(ctx, 'print(d.get("zz"), d.get("zz", "none"), d.get("k"))') == "None none v\n"


def test_len_in_and_truth(ctx):
    out(ctx, 'h = ["a", "b"]')
    out(ctx, 'd = {"k": "v"}')
    out(ctx, "e = []")
    assert out(ctx, 'if -py (len(h) == 2 and "a" in h and "z" not in h and "k" in d) { echo yes }') == "yes\n"
    assert out(ctx, "if -py (e) { echo full } else { echo empty }") == "empty\n"
    assert out(ctx, "if -py (h) { echo full } else { echo empty }") == "full\n"


def test_functions_on_lists(ctx):
    out(ctx, 'h = ["b", "c", "a"]')
    assert out(ctx, "print(sorted(h), sorted(h, reverse=True), reversed(h))") == \
        "['a', 'b', 'c'] ['c', 'b', 'a'] ['a', 'c', 'b']\n"
    assert out(ctx, 'print(list("ab"), list(range(3)), range(1, 4), min(h), max(h))') == \
        "['a', 'b'] [0, 1, 2] [1, 2, 3] a c\n"
    assert out(ctx, "print(enumerate(h)[0], zip(h, [1, 2]), any([0, 1]), all([0, 1]))") == \
        "(0, 'b') [('b', 1), ('c', 2)] True False\n"
    assert out(ctx, 'print(h.index("c"), h.count("a"), h.copy())') == "1 1 ['b', 'c', 'a']\n"
    assert ctx.variables["h"].items == ("b", "c", "a")  # sorted() gave a new list


def test_join(ctx):
    out(ctx, 'h = ["a", "b"]')
    out(ctx, "n = [1, 2]")
    assert out(ctx, 'print(", ".join(h), "-".join(n), "+".join(sorted(h, reverse=True)))') == "a, b 1-2 b+a\n"
    assert out(ctx, 'print(", ".join("ab", "cd"))') == '✗ print: join takes one list: ", ".join(hosts)\n'


def test_dict_methods(ctx):
    out(ctx, 'd = {"web": 80, "db": 5432}')
    assert out(ctx, "print(d.keys(), d.values(), d.items())") == \
        "['web', 'db'] [80, 5432] [('web', 80), ('db', 5432)]\n"
    assert out(ctx, 'print(sorted(d), list(d), len(d), d.copy(), dict(d)["web"])') == \
        "['db', 'web'] ['web', 'db'] 2 {'web': 80, 'db': 5432} 80\n"


def test_sorted_takes_no_key(ctx):
    out(ctx, 'h = ["a"]')
    with pytest.raises(ParseError, match=r"sorted takes only reverse=True \(no key=\)"):
        out(ctx, "print(sorted(h, key=len))")


def test_no_comprehensions_or_lambdas(ctx):
    out(ctx, 'h = ["a"]')
    for text in ("x = [v.upper() for v in h]", "x = sorted(h, key=lambda v: v)", "x = {k: 1 for k in h}"):
        with pytest.raises(ParseError):
            out(ctx, text)


def test_adding_text_to_a_list_gives_a_hint(ctx):
    out(ctx, 'h = ["a"]')
    assert 'to add one element: hosts.append("x") or hosts += ["x"]' in out(ctx, 'x = h + "b"')
    assert "x" not in ctx.variables


def test_f_strings_read_elements(ctx):
    out(ctx, 'h = ["web01", "db01"]')
    out(ctx, 'd = {"web": 80}')
    assert out(ctx, 'print(f"{h[0]} of {len(h)}: port {d[\'web\']}, all {h}")') == \
        "web01 of 2: port 80, all ['web01', 'db01']\n"


def test_bash_array_spellings_in_a_condition(ctx):
    out(ctx, "a=(x y z)")
    assert out(ctx, 'if -py (len(${a[@]}) == ${#a[@]} and "y" in ${a[@]}) { echo same }') == "same\n"


# --- loops ---

def test_for_over_a_list(ctx):
    out(ctx, 'hosts = ["web01", "db 01"]')
    assert out(ctx, "for -py h in hosts { echo \"<$h>\" }") == "<web01>\n<db 01>\n"


def test_for_over_a_dict_gives_its_keys(ctx):
    out(ctx, 'd = {"web": 80, "db": 5432}')
    assert out(ctx, "for -py k in d { print(k, d[k]) }") == "web 80\ndb 5432\n"


def test_for_with_two_names(ctx):
    out(ctx, 'd = {"web": 80, "db": 5432}')
    assert out(ctx, "for -py k, v in d.items() { print(k, v + 1) }") == "web 81\ndb 5433\n"
    assert out(ctx, 'for -py i, h in enumerate(["a", "b"]) { print(i, h) }') == "0 a\n1 b\n"
    assert out(ctx, 'for -py a, b in zip(["x", "y"], [1, 2]) { print(a, b) }') == "x 1\ny 2\n"


def test_two_names_need_two_values(ctx):
    out(ctx, 'h = ["a", "b"]')
    assert "for: 2 names (k, v) but the item 'a' is not 2 values" in out(ctx, "for -py k, v in h { echo $k }")


def test_for_over_a_function_a_method_and_a_literal(ctx):
    out(ctx, 'h = ["b", "a"]')
    assert out(ctx, "for -py x in sorted(h) { echo $x }") == "a\nb\n"
    assert out(ctx, "for -py n in range(3) { echo $n }") == "0\n1\n2\n"
    assert out(ctx, 'for -py w in "p q".split() { echo $w }') == "p\nq\n"
    assert out(ctx, 'for -py p in ["x", "y z"] { echo "<$p>" }') == "<x>\n<y z>\n"
    assert out(ctx, "for -py x in h[1:] { echo $x }") == "a\n"


def test_a_name_that_holds_no_list_stays_a_word(ctx, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "f.txt").write_text("")
    (tmp_path / "g.txt").write_text("")
    assert out(ctx, "for -py x in nolist { echo $x }") == "nolist\n"
    out(ctx, "word=text")
    assert out(ctx, "for -py x in word { echo $x }") == "word\n"
    assert out(ctx, "for -py f in [fg].txt { echo $f }") == "f.txt\ng.txt\n"


def test_the_loop_runs_over_a_copy(ctx):
    out(ctx, 'q = ["a", "b"]')
    assert out(ctx, 'for -py x in q { q.append("z") ; echo $x }') == "a\nb\n"
    assert ctx.variables["q"].items == ("a", "b", "z", "z")


def test_range_is_capped(ctx):
    assert out(ctx, "x = range(1000000000)") == "✗ x: range: more than 100000 items\n"


# --- colon blocks and files ---

SCRIPT = '''
hosts = [
    "web01",
    "db01",   # the database
]
ports = {
    "web": 80,
    "db": 5432,
}
for -py name, port in ports.items():
    if port > 100:
        print(name, "high")
    else:
        print(name, "low")
for -py h in hosts:
    row = {
"name": h,
        "size": len(h),
    }
    print(row["name"], row["size"],
          sep="=")
print(len(hosts))
'''


def test_literals_over_lines_in_a_file(ctx):
    assert lines(ctx, SCRIPT) == "web low\ndb high\nweb01=5\ndb01=4\n2\n"


def test_a_function_takes_and_returns_a_list(ctx):
    text = '''
def longest(items):
    best = ""
    for item in items:
        if len(item) > len(best):
            best = item
    return best

def doubled(items):
    return items + items

print(longest(["a", "ccc", "bb"]))
twice = doubled(["x"])
print(twice, len(twice))
'''
    assert lines(ctx, text) == "ccc\n['x', 'x'] 2\n"


# --- map ---

def test_map_turns_text_elements_into_numbers(ctx):
    out(ctx, 'w = "10 20 30".split()')
    assert out(ctx, "print(sum(map(int, w)), map(len, w), sorted(map(int, w), reverse=True))") == \
        "60 [2, 2, 2] [30, 20, 10]\n"
    assert out(ctx, "for -py v in map(int, w) { print(v + 1) }") == "11\n21\n31\n"


def test_map_calls_your_own_function(ctx):
    out(ctx, "def double(x) { return x * 2 }")
    assert out(ctx, "print(map(double, [1, 2]))") == "[2, 4]\n"


def test_map_needs_a_function_name_and_a_list(ctx):
    assert out(ctx, "print(map(int))") == "✗ print: map takes the name of a function and one list: map(int, sizes)\n"
    assert out(ctx, 'print(map(nope, ["a"]))').startswith("✗ print: nope is not a function (built in: abs, ")
    assert out(ctx, 'print(map(int, ["x"]))') == "✗ print: ValueError: invalid literal for int() with base 10: 'x'\n"
