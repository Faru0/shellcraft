"""`read` from attached input, and the loops and pipes that feed it (shellcraft.inputs)."""

import pytest

from shellcraft import startup
from shellcraft.context import to_text
from shellcraft.inputs import Input, split_fields, text_chars
from shellcraft.pipeline import PipelineError, run_line
from shellcraft.script import IncompleteError, parse_script


def out(ctx, line):
    result = run_line(line, ctx)
    return "" if result is None or result.output is None else (
        result.output if isinstance(result.output, str) else result.output.text)


def fields(line, ifs, count, raw=True):
    return split_fields(text_chars(line, raw), ifs, count)


# ── splitting a line on $IFS ─────────────────────────────────────────────────

@pytest.mark.parametrize("line, ifs, count, expected", [
    ("x y", " \t\n", 3, ["x", "y", ""]),
    ("  x   y   z  ", " \t\n", 2, ["x", "y   z"]),
    ("  x y  ", " \t\n", 1, ["x y"]),
    ("x:y:z", ":", 2, ["x", "y:z"]),
    ("x:y:", ":", 2, ["x", "y"]),  # one closing delimiter is dropped, as in bash
    ("x:y::", ":", 2, ["x", "y::"]),
    ("x:", ":", 1, ["x"]),
    ("x::z", ":", 3, ["x", "", "z"]),
    (":y:z", ":", 3, ["", "y", "z"]),
    ("x : y : z w", " :", 3, ["x", "y", "z w"]),
    ("  x y ", "", 2, ["  x y ", ""]),  # an empty IFS splits nothing
])
def test_split_fields_follows_bash(line, ifs, count, expected):
    assert fields(line, ifs, count) == expected


def test_backslash_makes_a_character_plain_unless_raw():
    assert fields(r"a\ b c", " ", 2, raw=False) == ["a b", "c"]
    assert fields(r"a\ b c", " ", 2, raw=True) == ["a\\", "b c"]
    assert "".join(c for c, _ in text_chars("one\\\ntwo", raw=False)) == "onetwo"  # backslash + new line continues


# ── read from a here-string, a file, a pipe ──────────────────────────────────

def test_read_splits_a_here_string_into_names(ctx):
    out(ctx, 'read a b c <<< "x y"')
    assert (ctx.variables["a"], ctx.variables["b"], ctx.variables["c"]) == ("x", "y", "")
    out(ctx, 'read first rest <<< "one two three"')
    assert ctx.variables["first"] == "one" and ctx.variables["rest"] == "two three"  # the last name gets the rest


def test_read_without_a_name_sets_reply(ctx):
    out(ctx, 'read <<< "  keep   spaces  "')
    assert ctx.variables["REPLY"] == "  keep   spaces  "


def test_read_honours_ifs(ctx):
    out(ctx, "IFS=:")
    out(ctx, 'read a b <<< "x:y:z"')
    assert (ctx.variables["a"], ctx.variables["b"]) == ("x", "y:z")


def test_read_takes_the_first_line_of_a_file_each_time(ctx, tmp_path):
    (tmp_path / "f").write_text("one\ntwo\n")
    out(ctx, "read a < f")
    out(ctx, "read b < f")  # as in bash: each `< f` opens the file again
    assert ctx.variables["a"] == ctx.variables["b"] == "one"


def test_end_of_input_is_status_1_and_empties_the_names(ctx):
    ctx.variables["a"] = "old"
    assert run_line("read a < /dev/null", ctx).status == 1
    assert ctx.variables["a"] == ""
    assert run_line('read a <<< ""', ctx).status == 0  # an empty line is input


def test_a_piped_read_stays_set(ctx):
    out(ctx, 'printf "one two\\n" | read a b')
    assert (ctx.variables["a"], ctx.variables["b"]) == ("one", "two")  # bash loses these (a subshell)
    assert run_line("printf '' | read a", ctx).status == 1  # a pipe that said nothing is input, not the terminal


def test_dash_r_keeps_backslashes(ctx):
    out(ctx, r"read -r a <<< 'a\b'")
    out(ctx, r"read b <<< 'a\b\ c'")
    assert ctx.variables["a"] == "a\\b" and ctx.variables["b"] == "ab c"


def test_n_and_d(ctx):
    out(ctx, 'read -n 3 a <<< "abcdef"')
    out(ctx, 'read -d , b <<< "ab,cd"')
    out(ctx, 'read -n1 -d "" c <<< "xyz"')
    assert (ctx.variables["a"], ctx.variables["b"], ctx.variables["c"]) == ("abc", "ab", "x")


def test_options_are_checked(ctx):
    with pytest.raises(PipelineError, match="line editing is not supported"):
        run_line("read -e x <<< a", ctx)
    with pytest.raises(PipelineError, match="invalid option"):
        run_line("read -z x <<< a", ctx)
    with pytest.raises(PipelineError, match="invalid variable name"):
        run_line("read 9lives <<< a", ctx)
    assert run_line("read -t 5 a <<< hi", ctx).status == 0 and ctx.variables["a"] == "hi"  # input is there already


def test_terminal_only_options_are_refused_at_the_prompt(ctx):
    ctx.interactive = True
    for line, text in (("read -t 5 a", "-t does not work at the prompt"), ("read -n 2 a", "-n and -d read piped")):
        with pytest.raises(PipelineError, match=text):
            run_line(line, ctx)


# ── loops over input ─────────────────────────────────────────────────────────

def test_while_read_over_a_file(ctx, tmp_path):
    (tmp_path / "pairs").write_text("a 1\nb 2\n")
    assert out(ctx, 'while read k v; do echo "$v=$k"; done < pairs') == "1=a\n2=b\n"
    assert ctx.last_status == 0


def test_a_last_line_without_a_newline_is_stored_but_ends_the_loop(ctx, tmp_path):
    (tmp_path / "f").write_bytes(b"x y\nz w")
    assert out(ctx, "while read a b; do echo $a; done < f") == "x\n"  # bash: read returns 1 on the partial line
    assert ctx.variables["a"] == "z"
    assert out(ctx, 'while read a b || [ -n "$a" ]; do echo $b; a=; done < f') == "y\nw\n"


def test_loop_input_does_not_print_the_file(ctx, tmp_path):
    (tmp_path / "f").write_text("secret\n")
    assert out(ctx, "for x in 1 2; do echo $x; done < f") == "1\n2\n"  # it used to print the file
    assert out(ctx, 'for x in 1 2; do read l; echo "$x $l"; done < f') == "1 secret\n2 \n"


def test_pipe_into_while_read(ctx):
    assert out(ctx, "printf 'x y\\nz w\\n' | while read a b; do echo \"$b-$a\"; done") == "y-x\nw-z\n"


def test_a_piped_loop_does_not_leak_variables_but_a_redirected_one_does(ctx, tmp_path):
    (tmp_path / "f").write_text("a\nb\n")
    out(ctx, "n=none")
    out(ctx, "printf 'a\\nb\\n' | while read l; do n=$l; done")
    assert ctx.variables["n"] == "none" and "l" not in ctx.variables
    out(ctx, "while read l; do n=$l; done < f")
    assert ctx.variables["n"] == "b"


def test_pipe_into_other_blocks(ctx):
    assert out(ctx, 'printf "a\\nb\\n" | for x in 1 2; do read l; echo "$x $l"; done') == "1 a\n2 b\n"
    assert out(ctx, 'printf "a\\nb\\n" | if read l; then echo "got $l"; fi') == "got a\n"
    assert out(ctx, 'printf "a\\nb\\n" | case x in x) read l; echo "c $l";; esac') == "c a\n"
    assert out(ctx, 'printf "a\\nb\\n" | until ! read l; do echo "u $l"; done') == "u a\nu b\n"


def test_break_and_a_tail_after_a_piped_block(ctx):
    assert out(ctx, "printf '1\\n2\\n3\\n' | while read n; do if [ $n = 2 ]; then break; fi; echo $n; done") == "1\n"
    assert out(ctx, "printf 'a b\\n' | while read x y; do echo $y; done | tr a-z A-Z") == "B\n"


def test_a_piped_block_after_and_in_a_chain(ctx):
    assert out(ctx, "true && printf 'k\\n' | while read l; do echo got $l; done") == "got k\n"
    assert out(ctx, "false && printf 'k\\n' | while read l; do echo got $l; done") == ""


def test_the_head_of_a_pipe_into_a_block_may_be_a_pipeline(ctx):
    assert out(ctx, "printf 'b\\na\\n' | sort | while read l; do echo \"> $l\"; done") == "> a\n> b\n"


def test_pipefail_applies_to_the_head(ctx):
    out(ctx, "set -o pipefail")
    out(ctx, "false | while read l; do :; done")
    assert ctx.last_status == 1


def test_a_function_reads_its_piped_input_line_by_line(ctx):
    out(ctx, "f() { read a b; echo \"$b $a\"; }")
    assert out(ctx, 'echo "p q" | f') == "q p\n"
    out(ctx, 'g() { while read l; do echo "[$l]"; done; }')
    assert out(ctx, "printf 'a\\nb\\n' | g") == "[a]\n[b]\n"


def test_a_while_read_over_a_heredoc_inside_a_function(ctx):
    out(ctx, "h() { while read a b; do echo \"$b$a\"; done <<EOF\n1 2\n3 4\nEOF\n}")
    assert out(ctx, "h") == "21\n43\n"


def test_in_a_script_file(ctx, tmp_path):
    (tmp_path / "data").write_text("x 1\ny 2\n")
    script = tmp_path / "t.sc"
    script.write_text("IFS=' '\nwhile read -r k v; do\n  echo \"$k:$v\"\ndone < data\n"
                      "cat data | while read k v; do echo \"[$v]\"; done\n")
    shown = []
    ctx.emit = lambda value: shown.append(to_text(value))
    assert startup.source(script, ctx) == (3, 0)
    assert shown == ["x:1\ny:2\n", "[1]\n[2]\n"]


def test_a_pipe_into_a_block_parses_and_asks_for_more(ctx):
    program = parse_script("printf 'a\\n' | while read l; do echo $l; done")
    assert program is not None and type(program.stmts[0]).__name__ == "PipedInto"
    with pytest.raises(IncompleteError):
        parse_script("printf 'a\\n' | while read l; do")
    assert out(ctx, "echo a | grep a || echo no") == "a\n"  # an ordinary pipe is untouched
    assert out(ctx, "echo for | cat") == "for\n"


def test_the_checker_walks_a_piped_block(ctx):
    from shellcraft import check

    report = check.check_text("printf 'a\\n' | while read l; do ecoh $l; done\n", "f.sc", ctx)
    assert [(p.line, p.error) for p in report.problems] == [(1, False)]
    assert "unknown command 'ecoh'" in report.problems[0].message


def test_input_is_text_that_commands_can_read_whole():
    source = Input("a\nb\n")
    assert source == "a\nb\n" and source.pos == 0 and not source.at_end()


def test_streaming_commands_read_a_redirected_file_whole(ctx, tmp_path):
    (tmp_path / "f").write_text("a b\nb c\nx y\n")
    for command, expected in (("grep b < f", "a b\nb c\n"), ("head -n 1 < f", "a b\n"), ("wc -l < f", "3\n"),
                              ("sort -r < f", "x y\nb c\na b\n"), ("sed -n 2p < f", "b c\n"),
                              ("cat < f | grep x", "x y\n"), ('grep b <<< "abc"', "abc\n")):
        assert out(ctx, command).strip().split() == expected.strip().split(), command
    (tmp_path / "other").write_text("o\n")
    # a command in the loop body that ignores stdin leaves the loop's input alone
    assert out(ctx, "while read l; do cat other; done < f") == "o\n" * 3


def test_a_line_that_will_not_parse_ends_a_while_loop(ctx, tmp_path):
    (tmp_path / "f").write_text("a\nb\n")
    result = run_line("while read -r l && echo $((1/0)); do echo got $l; done < f", ctx)  # fails only when it runs
    assert "got a" not in to_text(result.output)  # it used to loop forever
    assert out(ctx, "n=0; until nosuchcmd; do n=$((n+1)); [ $n = 3 ] && break; done; echo $n").endswith("3\n")


def test_an_empty_loop_piped_into_read_is_input_not_a_prompt(ctx):
    ctx.interactive = True  # a prompt would be tried (and fail: no terminal) if nothing were attached
    assert run_line("for x in; do echo $x; done | read v", ctx).status == 1
