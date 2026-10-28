"""Shell functions: name() { …; }, function name { …; }, $1/$#/$@, local, return, recursion,
pipes and redirects, precedence over builtins, \\name, declare / type / which / unset -f, MCP."""

import pytest

from shellcraft import script, startup
from shellcraft.context import to_text
from shellcraft.parser import ParseError
from shellcraft.pipeline import PipelineError, run_line


def out(ctx, line):
    result = run_line(line, ctx)
    return None if result is None else to_text(result.output)


def test_define_and_call_with_arguments(ctx):
    assert out(ctx, 'greet() { echo "hello $1, $# args: $@"; }') == ""
    assert out(ctx, "greet Ada x y") == "hello Ada, 3 args: Ada x y\n"
    assert out(ctx, "greet") == "hello , 0 args: \n"  # missing arguments are empty inside a function
    assert out(ctx, "function shout { echo LOUD $1; }") == "" and out(ctx, "shout it") == "LOUD it\n"
    assert out(ctx, "function both() { echo $2 $1; }") == "" and out(ctx, "both a b") == "b a\n"
    assert out(ctx, "f(){ echo tight; }") == "" and out(ctx, "f") == "tight\n"
    assert out(ctx, "echo $1 $#") == "0\n"  # outside a function: no arguments, as in bash


def test_local_return_and_dynamic_scope(ctx):
    run_line("x=global; y=keep", ctx)
    run_line("f() { local x=inner y; y=changed; echo $x; z=leaks; return 3; }", ctx)
    assert out(ctx, "f") == "inner\n" and ctx.last_status == 3
    assert ctx.variables["x"] == "global" and ctx.variables["y"] == "keep"  # put back
    assert ctx.variables["z"] == "leaks"  # not local: the shell's own, as in bash
    run_line("g() { local only=here; }", ctx)
    out(ctx, "g")
    assert "only" not in ctx.variables  # did not exist before: removed again
    run_line("ok() { false; }", ctx)
    out(ctx, "ok")
    assert ctx.last_status == 1  # no return: the last command's status
    run_line("r() { return $1; }", ctx)
    out(ctx, "r 7")
    assert ctx.last_status == 7
    assert out(ctx, "r 0 && echo yes || echo no") == "yes\n"


def test_arithmetic_recursion_and_the_depth_cap(ctx):
    run_line("add() { echo $(( $1 + $2 + $# )); }", ctx)
    assert out(ctx, "add 2 5") == "9\n"
    run_line("fact() { if [ $1 -le 1 ]; then echo 1; else local p=$(fact $(( $1 - 1 ))); echo $(( $1 * p )); fi; }",
             ctx)
    assert out(ctx, "fact 6") == "720\n"
    run_line("forever() { forever; }", ctx)
    assert "nested more than 100 deep" in out(ctx, "forever")
    assert ctx.positional == []  # every call's frame was popped


def test_pipes_redirects_and_input(ctx, tmp_path):
    run_line("lines() { echo b; echo a; }", ctx)
    assert out(ctx, "lines | sort") == "a\nb\n"
    run_line(f"lines > {tmp_path / 'out.txt'}", ctx)
    assert (tmp_path / "out.txt").read_text() == "b\na\n"
    run_line("upper() { tr a-z A-Z; }", ctx)
    assert out(ctx, "echo abc | upper") == "ABC\n"  # its commands read the function's input


def test_functions_shadow_builtins_except_special_ones_and_backslash_skips(ctx):
    run_line("echo() { \\echo wrapped $@; }", ctx)
    assert out(ctx, "echo hi") == "wrapped hi\n" and out(ctx, "\\echo plain") == "plain\n"
    run_line("unset -f echo", ctx)
    run_line("exit() { echo nope; }", ctx)  # defined, but `exit` stays the builtin
    assert "exit" in ctx.functions
    with pytest.raises(ParseError, match="'if' is a shell keyword"):
        run_line("if() { echo; }", ctx)


def test_declare_type_which_unset(ctx):
    run_line("greet() { echo hi; }", ctx)
    assert out(ctx, "declare -f") == "greet() { echo hi; }\n"
    assert out(ctx, "declare -F") == "declare -f greet\n"
    assert out(ctx, "type -t greet") == "function\n" and out(ctx, "type greet").startswith("greet is a function")
    assert "greet: shell function" in out(ctx, "which greet")
    with pytest.raises(PipelineError, match="only -f and -F"):
        run_line("declare -x X=1", ctx)
    run_line("unset -f greet", ctx)
    assert ctx.functions == {}
    with pytest.raises(PipelineError, match="command not found: greet"):
        run_line("greet", ctx)


def test_parse_errors_and_misuse(ctx):
    with pytest.raises(ParseError, match="'return' outside a function"):
        run_line("return 1", ctx)
    with pytest.raises(PipelineError, match="local: can only be used in a function"):
        run_line("local x=1", ctx)
    with pytest.raises(ParseError, match="a function body goes in braces"):
        run_line("f() echo hi", ctx)
    with pytest.raises(ParseError, match="'break' outside a for loop"):
        run_line("f() { break; }", ctx)  # break can't leave the function


def test_multi_line_definitions_at_the_prompt_and_in_rc_files(ctx):
    line = "greet() {"
    assert script.incomplete(line) is not None
    for more in ('echo "hi $1"', "}"):
        line = script.join_continuation(line, more)
    assert script.incomplete(line) is None
    run_line(line, ctx)
    assert out(ctx, "greet Ada") == "hi Ada\n"
    lines = startup.logical_lines("f() {\n  local n=$1\n  echo n=$n\n}\nf 4\n")
    assert [n for n, _ in lines] == [1, 5]
    for _, command in lines:
        result = run_line(command, ctx)
    assert to_text(result.output) == "n=4\n"


def test_not_over_mcp(ctx):
    ctx.allow_stateful = False
    ctx.allow_aliases = False
    assert "can't be defined here" in out(ctx, "g() { echo mcp; }")
    assert "g" not in ctx.functions
    ctx.functions["h"] = script.parse_script("h() { echo x; }").stmts[0]
    with pytest.raises(PipelineError, match="not available here"):
        run_line("h", ctx)


# ── "$@", shift, ${NAME:-default} ────────────────────────────────────────────

def test_quoted_at_keeps_each_argument_a_word(ctx):
    run_line('count() { echo "$# args"; }', ctx)
    run_line('fwd() { count "$@"; count $@; count "x$@y"; count "$*"; }', ctx)
    assert out(ctx, 'fwd a "b c" d') == "3 args\n4 args\n3 args\n1 args\n"  # as bash
    run_line('none() { count "$@"; count "pre$@"; }', ctx)
    assert out(ctx, "none") == "0 args\n1 args\n"  # "$@" with no arguments is no word at all
    run_line('loop() { for a in "$@"; do echo "[$a]"; done; }', ctx)
    assert out(ctx, 'loop one "two words" three') == "[one]\n[two words]\n[three]\n"
    assert out(ctx, "loop") == ""


def test_shift(ctx):
    run_line('each() { while [ $# -gt 0 ]; do echo "arg: $1"; shift; done; shift || echo "status $?"; }', ctx)
    assert out(ctx, 'each a "b c"') == "arg: a\narg: b c\nstatus 1\n"
    run_line('two() { shift 2; echo "$# left: $@"; }', ctx)
    assert out(ctx, "two a b c d") == "2 left: c d\n"
    with pytest.raises(PipelineError, match="only be used in a function"):
        run_line("shift", ctx)
    run_line('bad() { shift x; echo "after: $?"; }', ctx)  # inside a function: reported, and the call goes on
    text = out(ctx, "bad")
    assert "usage: shift [N]" in text and "after: 1" in text


def test_default_values(ctx):
    run_line('greet() { echo "hello ${1:-stranger}"; }', ctx)
    assert out(ctx, "greet") == "hello stranger\n" and out(ctx, "greet Ada") == "hello Ada\n"
    run_line('empty=""', ctx)
    run_line("who=Grace", ctx)
    assert out(ctx, 'echo "[${empty:-default}] [${empty-default}] [${nope-fall $who}] [${who:-x}]"') == \
        "[default] [] [fall Grace] [Grace]\n"


def test_return_break_and_continue_after_and_or(ctx):
    run_line("need() { [ -n \"$1\" ] || return 2; echo \"got $1\"; }", ctx)
    assert out(ctx, "need") == "" and ctx.last_status == 2
    assert out(ctx, "need x") == "got x\n" and ctx.last_status == 0
    run_line("seven() { true && return 7; echo never; }", ctx)
    out(ctx, "seven")
    assert ctx.last_status == 7
    run_line("last() { false || return; }", ctx)  # a bare return: the status so far
    out(ctx, "last")
    assert ctx.last_status == 1
    assert out(ctx, "for i in 1 2 3 4; do [ $i -eq 3 ] && break; echo $i; done") == "1\n2\n"
    assert out(ctx, "for i in 1 2 3; do [ $i -eq 2 ] && continue; echo $i; done") == "1\n3\n"


def test_a_function_line_ending_in_a_default_expansion_is_unfinished():
    # the } of ${1:-friend} is not the function's closing brace: the next lines belong to it
    text = 'greet() {\n  local name=${1:-friend}\n  echo "Hi $name"\n}\ngreet\n'
    assert [line for _, line in startup.logical_lines(text)] == [
        'greet() { local name=${1:-friend} ; echo "Hi $name" ; }', "greet"]


def test_a_function_on_its_own_line_shows_each_output_as_it_runs(ctx):
    shown = []
    ctx.emit = shown.append
    run_line("steps() { echo one; echo two; return 3; }", ctx)
    result = run_line("steps", ctx)
    assert [to_text(v) for v in shown] == ["one\n", "two\n"]  # one by one, before the next step runs
    assert to_text(result.output) == "" and ctx.last_status == 3
    shown.clear()
    assert out(ctx, "steps | cat") == "one\ntwo\n" and not shown  # piped: collected, as before
    run_line("x=$(steps)", ctx)
    assert ctx.variables["x"] == "one\ntwo" and not shown
