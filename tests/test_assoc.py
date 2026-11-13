"""Dicts the bash way: declare -A, m[key]=v, ${m[key]}, ${!m[@]}; and declare -a / -p for arrays."""

import pytest

from shellcraft.context import to_text
from shellcraft.pipeline import PipelineError, run_line
from shellcraft.values import ShArray, ShMap


def out(ctx, line):
    result = run_line(line, ctx)
    return "" if result is None or result.output is None else to_text(result.output)


def pairs(ctx, name):
    held = ctx.variables[name]
    assert isinstance(held, ShMap), f"{name} is {held!r}"
    return dict(held.items)


def test_declare_then_fill(ctx):
    run_line("declare -A port", ctx)
    assert pairs(ctx, "port") == {}
    run_line("port[web]=80; port[db]=5432; port['my key']=1; k=web; port[$k]+=80", ctx)
    assert pairs(ctx, "port") == {"web": "8080", "db": "5432", "my key": "1"}
    assert out(ctx, 'echo "${port[web]} ${port[$k]} ${port[my key]} [${port[nope]}] ${port[nope]:-none}"') \
        == "8080 8080 1 [] none\n"
    assert out(ctx, "echo ${#port[@]} ${#port[web]}") == "3 4\n"
    assert out(ctx, 'printf "<%s>" "${!port[@]}"') == "<web><db><my key>"  # the order they were added
    assert out(ctx, 'printf "<%s>" "${port[@]}"') == "<8080><5432><1>"
    assert out(ctx, 'for k in "${!port[@]}"; do echo "$k=${port[$k]}"; done') == "web=8080\ndb=5432\nmy key=1\n"
    assert out(ctx, "echo $(( port[web] + 1 ))") == "8081\n"


def test_a_literal_with_declare(ctx):
    run_line('declare -A m=([web]=80 [db]="54 32")', ctx)
    assert pairs(ctx, "m") == {"web": "80", "db": "54 32"}
    run_line("m+=([cache]=6379 [web]=443)", ctx)  # += adds keys and replaces the ones that are there
    assert pairs(ctx, "m") == {"web": "443", "db": "54 32", "cache": "6379"}
    run_line("m=([only]=1)", ctx)  # the name is a dict: a new literal is one too
    assert pairs(ctx, "m") == {"only": "1"}
    with pytest.raises(PipelineError, match=r"m: a dict takes \[key\]=value words"):
        run_line("m=(x y)", ctx)
    with pytest.raises(PipelineError, match=r"m: a dict takes \[key\]=value words"):
        run_line("m=([two words]=1)", ctx)  # a key with a blank needs quotes: ["two words"]=1
    run_line("declare -A m", ctx)  # declaring it again keeps what is in it
    assert pairs(ctx, "m") == {"only": "1"}


def test_unset_a_key(ctx):
    run_line("declare -A m=([a]=1 [b]=2 [c]=3)", ctx)
    run_line("unset 'm[b]'", ctx)
    run_line("k=c; unset \"m[$k]\"", ctx)
    run_line("unset 'm[nope]'", ctx)
    assert pairs(ctx, "m") == {"a": "1"}
    run_line("unset m", ctx)
    assert "m" not in ctx.variables


def test_without_declare_a_word_key_is_refused(ctx):
    with pytest.raises(PipelineError, match="port: not a dict: declare -A port first"):
        run_line("port[web]=80", ctx)
    assert "port" not in ctx.variables
    run_line("web=0; a[web]=x", ctx)  # a word that is a variable is arithmetic: element 0
    assert list(ctx.variables["a"].items) == ["x"]


def test_declare_a_and_conversions(ctx):
    run_line("declare -a a", ctx)
    assert isinstance(ctx.variables["a"], ShArray) and ctx.variables["a"].items == ()
    run_line("a+=(x); declare -a a", ctx)  # kept
    assert ctx.variables["a"].items == ("x",)
    run_line("declare -a b=(p 'q r'); declare -a c=one; s=text; declare -a s; declare -A t=zero", ctx)
    assert ctx.variables["b"].items == ("p", "q r") and ctx.variables["c"].items == ("one",)
    assert ctx.variables["s"].items == ("text",) and pairs(ctx, "t") == {"0": "zero"}
    with pytest.raises(PipelineError, match="declare: a: it is an array"):
        run_line("declare -A a", ctx)
    with pytest.raises(PipelineError, match="declare: t: it is a dict"):
        run_line("declare -a t", ctx)
    with pytest.raises(PipelineError, match="a variable is an array or a dict, not both"):
        run_line("declare -aA z", ctx)
    with pytest.raises(PipelineError, match="-i with -a or -A is not supported"):
        run_line("declare -ai z", ctx)


def test_declare_p_prints_what_reads_back(ctx):
    run_line('a=(x "y z"); declare -A m=([web]=80 ["my key"]="a b"); readonly r=(1 2); n=5', ctx)
    assert out(ctx, "declare -p a m") == 'declare -a a=([0]="x" [1]="y z")\ndeclare -A m=([web]="80" [\'my key\']="a b")\n'
    listed = out(ctx, "declare -p")
    assert 'declare -a a=([0]="x" [1]="y z")\n' in listed and 'declare -ar r=([0]="1" [1]="2")\n' in listed
    assert "n=" not in listed  # a plain variable with no attribute is not listed, as before
    assert out(ctx, "readonly") == 'declare -ar r=([0]="1" [1]="2")\n'
    for line in out(ctx, "declare -p a m").splitlines():  # each line is a command that makes the same value
        name = line.split()[2].split("=")[0]
        before = ctx.variables.pop(name)
        run_line(line, ctx)
        assert ctx.variables[name].items == before.items and type(ctx.variables[name]) is type(before)
    assert "m=([web]=80 ['my key']='a b')\n" in out(ctx, "set")


def test_readonly_export_and_local(ctx):
    run_line("declare -rA fixed=([k]=v)", ctx)
    with pytest.raises(PipelineError, match="fixed: readonly variable"):
        run_line("fixed[k]=other", ctx)
    with pytest.raises(PipelineError, match="cannot unset: readonly variable"):
        run_line("unset 'fixed[k]'", ctx)
    run_line("declare -A m=([k]=v)", ctx)
    with pytest.raises(PipelineError, match="export: m: an array or dict is not passed on to programs"):
        run_line("declare -x m", ctx)
    run_line('f() { local -A m; m[inner]=1; local -A n=([x]=y); echo "${!m[@]} ${n[x]}"; }', ctx)
    assert out(ctx, "f").strip() == "inner y"
    assert pairs(ctx, "m") == {"k": "v"} and "n" not in ctx.variables
    assert out(ctx, "( m[sub]=1; echo ${#m[@]} ); echo ${#m[@]}") == "2\n1\n"
