"""Bash arrays: a=(x y), a+=(z), a[1]=v, ${a[@]} and its relatives, unset 'a[-1]' (shellcraft.arrays)."""

import os

import pytest

from shellcraft import script
from shellcraft.context import to_text
from shellcraft.parser import ParseError
from shellcraft.pipeline import PipelineError, run_line
from shellcraft.values import ShArray


def out(ctx, line):
    result = run_line(line, ctx)
    return "" if result is None or result.output is None else to_text(result.output)


def run_text(ctx, text):
    return to_text(script.execute(script.parse_script(text), ctx).output)


def items(ctx, name):
    held = ctx.variables[name]
    assert isinstance(held, ShArray), f"{name} is {held!r}"
    return list(held.items)


# ── making one ────────────────────────────────────────────────────────────────

def test_a_literal_takes_words_like_a_command(ctx, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "one.log").write_text("")
    (tmp_path / "two.log").write_text("")
    run_line('who=ada; a=(x "y z" $who \'$who\' "$who!" {1..3} *.log $(echo p q))', ctx)
    assert items(ctx, "a") == ["x", "y z", "ada", "$who", "ada!", "1", "2", "3", "one.log", "two.log", "p", "q"]
    run_line("e=()", ctx)
    assert items(ctx, "e") == []
    run_line('b=("${a[@]:0:2}" last)', ctx)  # one array inside another: its elements, each kept whole
    assert items(ctx, "b") == ["x", "y z", "last"]
    run_line('c=([0]=p [1]="q r")', ctx)  # the form `declare -p` prints reads back
    assert items(ctx, "c") == ["p", "q r"]


def test_append_and_set_an_element(ctx):
    run_line("a=(x y)", ctx)
    run_line("a+=(z 'w w')", ctx)
    assert items(ctx, "a") == ["x", "y", "z", "w w"]
    run_line("a[1]=Y", ctx)
    run_line("a[-1]=W", ctx)
    run_line("a[4]=new", ctx)  # the next index: it appends
    run_line("a[0]+=tra", ctx)
    assert items(ctx, "a") == ["xtra", "Y", "z", "W", "new"]
    run_line("i=1; a[i+1]=two; a[$i]=one", ctx)  # the index is arithmetic
    assert items(ctx, "a")[1:3] == ["one", "two"]
    run_line("fresh[0]=first", ctx)  # a name that is not set becomes an array
    assert items(ctx, "fresh") == ["first"]
    run_line("plain=text; plain[1]=more", ctx)  # plain text is element 0
    assert items(ctx, "plain") == ["text", "more"]
    run_line("plain+=(third)", ctx)
    assert items(ctx, "plain") == ["text", "more", "third"]


def test_what_is_refused_says_why(ctx):
    run_line("a=(x y)", ctx)
    with pytest.raises(PipelineError, match=r"a\[9\]: arrays have no gaps \(next index is 2\)"):
        run_line("a[9]=far", ctx)
    assert ctx.last_status == 1 and items(ctx, "a") == ["x", "y"]
    with pytest.raises(PipelineError, match="a: not a dict: declare -A a first"):
        run_line("a[web]=80", ctx)
    with pytest.raises(PipelineError, match=r"arrays have no gaps \(next index is 0\)"):
        run_line("g=([3]=x)", ctx)
    with pytest.raises(ParseError, match=r"unexpected 'z' after the '\)'"):
        run_line("a=(x y) z", ctx)
    with pytest.raises(ParseError, match="not allowed in an array"):
        run_line("a=(x | y)", ctx)
    with pytest.raises(script.IncompleteError, match=r"missing '\)'"):
        run_line("a=(x y", ctx)


def test_a_literal_may_span_lines(ctx):
    assert run_text(ctx, "hosts=(\n  web01\n  db01\n)\necho ${#hosts[@]}") == "2\n"
    assert script.incomplete("hosts=(web01") is not None and script.incomplete("hosts=(web01)") is None


# ── reading it ────────────────────────────────────────────────────────────────

def test_elements_count_and_indexes(ctx):
    run_line('a=(x "y z" w)', ctx)
    assert out(ctx, 'echo "${a[0]}|${a[1]}|${a[-1]}|${a[9]}|"') == "x|y z|w||\n"
    assert out(ctx, 'echo "$a ${a}"') == "x x\n"  # the bare name is element 0, as in bash
    assert out(ctx, "echo ${#a[@]} ${#a[*]} ${#a[1]} ${#a}") == "3 3 3 1\n"
    assert out(ctx, "echo ${!a[@]}") == "0 1 2\n"
    assert out(ctx, "i=1; echo ${a[i]} ${a[$i]} ${a[i+1]} ${a[$((i - 1))]}") == "y z y z w x\n"
    assert out(ctx, "echo ${a[9]:-none} ${a[9]-none} ${a[0]:+set} ${a[0]:0:1}${a[1]^^}") == "none none set xY Z\n"
    assert out(ctx, "echo ${nope[@]:-nothing} ${#nope[@]}") == "nothing 0\n"
    assert out(ctx, "t=text; echo ${t[0]} ${t[-1]} ${#t[@]} ${t[@]} [${t[1]}]") == "text text 1 text []\n"


def test_each_element_is_a_word_when_quoted(ctx):
    run_line('a=(x "y z" w)', ctx)
    assert out(ctx, 'printf "<%s>" "${a[@]}"') == "<x><y z><w>"
    # not quoted, in a bash block: split again, as in bash (a line at the prompt never splits a value)
    assert out(ctx, 'if true; then printf "<%s>" ${a[@]}; fi').strip() == "<x><y><z><w>"
    assert out(ctx, 'printf "<%s>" ${a[@]}') == "<x><y z><w>"
    assert out(ctx, 'printf "<%s>" "${a[*]}"') == "<x y z w>"  # one word
    assert out(ctx, 'printf "<%s>" "pre${a[@]}post"') == "<prex><y z><wpost>"
    assert out(ctx, 'printf "<%s>" "${a[@]:1}"') == "<y z><w>"
    assert out(ctx, 'printf "<%s>" "${a[@]:1:1}" "${a[@]: -1}"') == "<y z><w>"
    assert out(ctx, 'printf "<%s>" "${!a[@]}"') == "<0><1><2>"
    assert out(ctx, 'e=(); printf "<%s>" start "${e[@]}" end').strip() == "<start><end>"  # an empty array: no word at all
    assert out(ctx, 'IFS=,; echo "${a[*]}"; unset IFS') == "x,y z,w\n"
    assert out(ctx, 'joined=${a[@]}; echo "$joined"') == "x y z w\n"


def test_in_a_script_too(ctx):
    text = '''
a=(x "y z")
for v in "${a[@]}"; do echo "<$v>"; done
for i in "${!a[@]}"; do echo "$i=${a[$i]}"; done
e=()
for v in "${e[@]}"; do echo never; done
for w in "p${a[@]}s"; do echo "[$w]"; done
if [[ ${#a[@]} -eq 2 && "${a[1]}" == "y z" ]]; then echo yes; fi
'''
    assert run_text(ctx, text) == "<x>\n<y z>\n0=x\n1=y z\n[px]\n[y zs]\nyes\n"


def test_arguments_still_expand_as_before(ctx):
    run_line('f() { printf "<%s>" "pre$@post" "$@"; }', ctx)
    assert out(ctx, 'f 1 "2 3"').strip() == "<pre1><2 3post><1><2 3>"
    assert out(ctx, "f").strip() == "<prepost>"


def test_operators_on_a_whole_array_are_refused(ctx):
    run_line("a=(x y)", ctx)
    for form in ("${a[@]/x/y}", "${a[@]^^}", "${a[@]#x}", "${a[*]%y}"):
        with pytest.raises(ParseError, match="operators on a whole array are not supported: loop over it"):
            run_line(f"echo {form}", ctx)


def test_arithmetic_reads_elements(ctx):
    run_line("a=(1 2 3); i=2", ctx)
    assert out(ctx, "echo $(( a[1] + 1 )) $(( a[-1] * 2 )) $(( a[9] )) $(( a[i] + a[i-1] ))") == "3 6 0 5\n"
    assert out(ctx, "echo $(( ${a[1]} + ${#a[@]} ))") == "5\n"
    assert out(ctx, 'for ((k=0; k<${#a[@]}; k++)); do echo "${a[k]}"; done') == "1\n2\n3\n"
    run_line("a[1]=$(( a[1] + 10 ))", ctx)
    assert items(ctx, "a") == ["1", "12", "3"]
    result = out(ctx, "true; (( a[0]++ ))")
    assert "an array element is read here, not set" in result


# ── removing ──────────────────────────────────────────────────────────────────

def test_unset_an_element_or_the_whole_array(ctx):
    run_line("a=(x y z)", ctx)
    run_line("unset 'a[-1]'", ctx)
    assert items(ctx, "a") == ["x", "y"]
    run_line("unset 'a[1]'", ctx)  # the last one, by its number
    assert items(ctx, "a") == ["x"]
    run_line("unset 'a[5]'", ctx)  # nothing there
    run_line("a+=(y z)", ctx)
    with pytest.raises(PipelineError, match=r"unset: a\[0\]: would leave a gap"):
        run_line("unset 'a[0]'", ctx)
    run_line("unset a", ctx)
    assert "a" not in ctx.variables and out(ctx, "echo ${#a[@]}") == "0\n"
    run_line("readonly r=(1 2)", ctx)
    with pytest.raises(PipelineError, match="r: cannot unset: readonly variable"):
        run_line("unset 'r[1]'", ctx)
    with pytest.raises(PipelineError, match="r: readonly variable"):
        run_line("r+=(3)", ctx)


# ── where it must not leak, and what must stay as it was ──────────────────────

def test_subshells_and_functions_keep_their_own(ctx):
    run_line("a=(x y)", ctx)
    assert out(ctx, "( a[1]=q; a+=(r); echo ${a[@]} ); echo ${a[@]}") == "x q r\nx y\n"
    assert out(ctx, "v=$(a+=(z); echo ${#a[@]}); echo $v ${#a[@]}") == "3 2\n"
    run_line("f() { local a=(L M); local -a b; b+=(n); echo ${a[1]} ${#b[@]}; }", ctx)
    assert out(ctx, "f; echo ${a[@]} ${b[@]:-unset}") == "M 1\nx y unset\n"
    run_line("g() { a+=(global); }", ctx)  # not local: the shell's own, as in bash
    run_line("g", ctx)
    assert items(ctx, "a") == ["x", "y", "global"]


def test_plain_values_stay_plain(ctx, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a0").write_text("")  # `a[0]=x` is not a file pattern
    run_line('x=$(echo hi); y="(a b)"; z=\'(c)\'; n=5; n+=1', ctx)
    assert [type(ctx.variables[k]) for k in "xyz"] == [str, str, str]
    assert (ctx.variables["x"], ctx.variables["y"], ctx.variables["z"], ctx.variables["n"]) == ("hi", "(a b)", "(c)", "6")
    run_line("IFS= read -r line <<< 'a b'", ctx)
    assert type(ctx.variables["line"]) is str
    run_line("a[0]=x", ctx)
    assert items(ctx, "a") == ["x"]


def test_an_array_is_not_exported(ctx):
    run_line("export SC_LIST=1", ctx)
    assert os.environ.get("SC_LIST") == "1"
    run_line("SC_LIST=(x y)", ctx)  # the name now holds an array: programs no longer get it
    assert "SC_LIST" not in os.environ and "SC_LIST" not in ctx.exported
    with pytest.raises(PipelineError, match="export: SC_LIST: an array or dict is not passed on to programs"):
        run_line("export SC_LIST", ctx)
    run_line("declare -i n=1", ctx)
    with pytest.raises(PipelineError, match=r"n: an integer variable \(declare -i\) cannot hold an array"):
        run_line("n=(1 2)", ctx)


def test_set_and_xtrace_show_the_literal(ctx, capsys):
    run_line('a=(x "y z")', ctx)
    assert "a=(x 'y z')\n" in out(ctx, "set")
    run_line("set -x", ctx)
    run_line("b=(1 '2 3')", ctx)
    run_line("set +x", ctx)
    assert "+ b=(1 '2 3')\n" in capsys.readouterr().err
