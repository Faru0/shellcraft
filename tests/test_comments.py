"""Comments: a `# …` line, or ` # note` (whitespace before and after the #s) to the end of a line.
A # touching other text, or inside quotes, is plain text."""

import pytest

from shellcraft import aliases, funcs, script, startup
from shellcraft.context import ShellContext, to_text
from shellcraft.highlight import highlight
from shellcraft.parser import strip_comments
from shellcraft.pipeline import run_line


@pytest.mark.parametrize("line, expected", [
    ("echo one   # note", "echo one"),
    ("echo one ## note", "echo one"),
    ("echo a\t#\ttabs", "echo a"),
    ("echo hi #", "echo hi"),
    ("# a whole line", ""),
    ("   # indented", ""),
    ("#05d9e8", ""),  # the first thing on the line: a comment
    ("profile set work color #05d9e8", "profile set work color #05d9e8"),
    ("grep #TODO notes.txt", "grep #TODO notes.txt"),
    ("echo $# ${#name}", "echo $# ${#name}"),
    ("curl https://x.com/p#top  # fetch it", "curl https://x.com/p#top"),
    ("echo hi #note", "echo hi #note"),
    ('echo "a # b" # c', 'echo "a # b"'),
    ("echo 'a # b'", "echo 'a # b'"),
    ("echo $'it\\'s # x' # y", "echo $'it\\'s # x'"),
    ("echo a\\ # b", "echo a\\ # b"),  # an escaped space is part of the word
    ("cut -d # -f2", "cut -d"),  # a lone # is a comment: quote it, as in bash
    ("for x in a b; do # loop\n  echo $x # each\ndone", "for x in a b; do\n  echo $x\ndone"),
])
def test_strip_comments(line, expected):
    assert strip_comments(line) == expected


def test_running_lines_with_comments(registry):
    ctx = ShellContext(registry=registry, config={})
    assert to_text(run_line("echo one   # a note", ctx).output) == "one\n"
    assert run_line("# nothing to do", ctx) is None and ctx.last_status == 0
    assert to_text(run_line("echo #TODO #05d9e8", ctx).output) == "#TODO #05d9e8\n"
    run_line("x=5 ## set it", ctx)
    assert to_text(run_line("echo $x", ctx).output) == "5\n"
    loop = "for n in 1 2; do # loop\n  echo $n # each\ndone"
    assert to_text(run_line(loop, ctx).output) == "1\n2\n"


def test_a_comment_never_opens_or_swallows_a_block():
    assert script.incomplete("for x in a b; do  # loop") is not None  # still waiting for the body
    assert script.incomplete("echo hi  # { do then") is None  # words in a comment open nothing
    joined = script.join_continuation("for x in a b; do  # loop", "echo $x  # each")
    assert "#" not in joined and "echo $x" in joined


def test_rc_and_func_lines(tmp_path):
    rc = "theme nord   # my favourite\n# a whole line\nfor n in 1 2; do  # loop\n  echo $n  ## each\ndone\n"
    assert [line for _, line in startup.logical_lines(rc)] == ["theme nord", "for n in 1 2; do echo $n ; done"]
    func = funcs.parse("# func: x\n# description: d\necho hi   # say hi\n# between\necho bye\n", "x")
    assert func.steps == ["echo hi", "echo bye"] and func.description == "d"


def test_the_aliases_file():
    found, bad = aliases.parse_file("alias ll='ls -l'  ## long\nalias c='curl -s' # note\n# alias z=y\n"
                                    "alias tag='grep #TODO'\n")
    assert found == {"ll": "ls -l", "c": "curl -s", "tag": "grep #TODO"} and bad == []


def test_the_prompt_colors_what_the_shell_ignores():
    def comment(line):
        return "".join(text for style, text in highlight(line, lambda n: True) if style == "class:comment")
    assert comment("echo one  # note") == "# note"
    assert comment("profile set w color #05d9e8") == ""  # not a comment: not colored as one
    assert comment("echo hi #note") == ""
    assert comment("# a whole line") == "# a whole line"
