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
    assert out(ctx, "echo $1 $#") == "$1 $#\n"  # outside a function they stay as typed


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
