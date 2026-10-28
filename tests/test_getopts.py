"""`getopts`: options for functions and funcs (OPTARG, OPTIND, `?`, silent mode), bash and `def`."""

import pytest

from shellcraft import funcs, startup
from shellcraft.context import ShellContext, to_text
from shellcraft.pipeline import PipelineError, run_line


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


@pytest.fixture
def errors(ctx):
    seen = []
    ctx.emit_stderr = seen.append
    return seen


BACKUP = """backup() {
    local verbose=no file=notes.txt
    while getopts "vf:" opt; do
        case $opt in
            v) verbose=yes ;;
            f) file=$OPTARG ;;
            \\?) echo bad; return 2 ;;
        esac
    done
    shift $((OPTIND - 1))
    echo "verbose=$verbose file=$file rest=$@"
}
"""


@pytest.mark.parametrize("call, expected", [
    ("backup", "verbose=no file=notes.txt rest="),
    ("backup -v -f todo.txt a b", "verbose=yes file=todo.txt rest=a b"),
    ("backup -vf todo.txt a", "verbose=yes file=todo.txt rest=a"),
    ("backup -ftodo.txt -v", "verbose=yes file=todo.txt rest="),
    ("backup -v -- -f x", "verbose=yes file=notes.txt rest=-f x"),
    ("backup a -v", "verbose=no file=notes.txt rest=a -v"),
    ("backup -v - x", "verbose=yes file=notes.txt rest=- x"),
])
def test_bash_function(ctx, call, expected):
    lines(ctx, BACKUP)
    assert out(ctx, call) == expected + "\n"
    assert "OPTIND" not in ctx.variables  # the caller's (none) is back


def test_unknown_option_and_missing_value(ctx, errors):
    lines(ctx, BACKUP)
    assert out(ctx, "backup -x") == "bad\n"
    assert errors == ["getopts: illegal option -- x\n"]
    assert ctx.last_status == 2
    errors.clear()
    assert out(ctx, "backup -f") == "bad\n"
    assert errors == ["getopts: option requires an argument -- f\n"]


def test_step_by_step_with_words(ctx, errors):
    run_line("set -- -ab -c val -x rest", ctx)
    seen = []
    for _ in range(6):
        result = run_line('getopts "abc:" o', ctx)
        seen.append((ctx.variables["o"], ctx.variables.get("OPTARG"), ctx.variables["OPTIND"], ctx.last_status))
        assert result is not None
    assert seen == [("a", None, "1", 0), ("b", None, "2", 0), ("c", "val", "4", 0), ("?", None, "5", 0),
                    ("?", None, "5", 1), ("?", None, "5", 1)]
    assert errors == ["getopts: illegal option -- x\n"]
    out(ctx, "OPTIND=1")  # starts over
    run_line('getopts "abc:" o', ctx)
    assert ctx.variables["o"] == "a"
    out(ctx, "OPTIND=1")
    run_line('getopts "q" o -q', ctx)  # words given after NAME
    assert ctx.variables["o"] == "q" and ctx.variables["OPTIND"] == "2"


def test_silent_mode(ctx, errors):
    lines(ctx, """f() {
    while getopts ":vf:" opt; do
        case $opt in
            v) echo verbose ;;
            f) echo "file $OPTARG" ;;
            :) echo "-$OPTARG needs a value" ;;
            \\?) echo "no option -$OPTARG" ;;
        esac
    done
}
""")
    assert out(ctx, "f -v -x -f") == "verbose\nno option -x\n-f needs a value\n"
    assert errors == []


def test_nested_functions_keep_their_own_optind(ctx):
    lines(ctx, """inner() {
    while getopts "q" o; do echo "inner $o"; done
}
outer() {
    while getopts "ab" o; do echo "outer $o"; inner -q; done
    echo "OPTIND=$OPTIND"
}
""")
    assert out(ctx, "outer -a -b") == "outer a\ninner q\nouter b\ninner q\nOPTIND=3\n"


def test_local_optind_is_put_back(ctx):
    lines(ctx, 'f() { local OPTIND; getopts v o -v; echo "$o $OPTIND"; }\n')
    out(ctx, "OPTIND=7")
    assert out(ctx, "f") == "v 2\n"
    assert ctx.variables["OPTIND"] == "7"


def test_case_backslash_question_mark_is_literal(ctx):
    assert out(ctx, "case a in \\?) echo q ;; *) echo other ;; esac") == "other\n"
    assert out(ctx, "case '?' in \\?) echo q ;; esac") == "q\n"


def test_usage_errors_and_outside_a_function(ctx):
    with pytest.raises(PipelineError, match="usage") as info:
        run_line("getopts v", ctx)
    assert info.value.status == 2
    with pytest.raises(PipelineError, match="not a valid variable name"):
        run_line("getopts v 1x", ctx)
    run_line("getopts v o", ctx)  # no arguments at the prompt: the end
    assert ctx.last_status == 1 and ctx.variables["o"] == "?"


def test_def_with_bash_loop_inside(ctx):
    lines(ctx, """def backup():
    verbose=no
    while getopts "vf:" opt; do case $opt in v) verbose=yes ;; f) echo "file $OPTARG" ;; esac; done
    shift $((OPTIND - 1))
    echo "verbose=$verbose rest=$@"
""")
    assert out(ctx, "backup -v -f a.txt b") == "file a.txt\nverbose=yes rest=b\n"


def test_def_python_style(ctx):
    lines(ctx, """def backup():
    while -py getopts "vf:" opt:
        case -py $opt:
            "v":
                echo verbose
            "f":
                echo "file $OPTARG"
            _:
                return 2
    shift $((OPTIND - 1))
    echo "rest=$@"
""")
    assert out(ctx, "backup -vf a.txt b c") == "verbose\nfile a.txt\nrest=b c\n"


def test_func_reads_its_options(ctx: ShellContext):
    func = funcs.Func(name="greet", body='while getopts "n:" o; do name=$OPTARG; done\n'
                                         'shift $((OPTIND - 1))\necho "hi $name, $@"\n')
    outcome = funcs.run(ctx, func, ["-n", "Ana", "extra"])
    assert outcome.status == 0
    assert [to_text(x) for x in outcome.collected] == ["hi Ana, extra\n"]
    assert "OPTIND" not in ctx.variables
