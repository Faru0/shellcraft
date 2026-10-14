"""`>&2` / `1>&2`: a command's output to the error stream, with `2> file` / `2>&1`, inside
functions and blocks, the default destinations, and the parser's handling of other `>&` forms."""

import pytest

from shellcraft.context import to_text
from shellcraft.parser import ParseError, parse, tokenize
from shellcraft.pipeline import run_line


@pytest.fixture
def err(ctx, tmp_path):
    seen: list[str] = []
    ctx.emit_stderr = seen.append
    return ctx, seen


def out(ctx, line):
    result = run_line(line, ctx)
    return None if result is None else to_text(result.output)


def test_output_goes_to_the_error_stream_and_the_status_is_unchanged(err):
    ctx, seen = err
    assert out(ctx, 'echo "error: bad input" >&2') == "" and seen == ["error: bad input\n"]
    assert ctx.last_status == 0
    seen.clear()
    assert out(ctx, "echo x 1>&2 | wc -l") == "0\n" and seen == ["x\n"]  # the next command gets nothing
    seen.clear()
    assert out(ctx, "echo a>&2") == "" and seen == ["a\n"]  # glued to the word, as in bash


def test_with_a_file_or_merged(err, tmp_path):
    ctx, seen = err
    assert out(ctx, f"echo x >&2 2> {tmp_path / 'e.txt'}") == "" and seen == []
    assert (tmp_path / "e.txt").read_text() == "x\n"
    assert out(ctx, "echo kept >&2 2>&1") == "kept\n"  # 2>&1 too: it stays output


def test_inside_functions_follows_the_calls_redirection(err, tmp_path):
    ctx, seen = err
    run_line('warn() { echo "warn: $1" >&2; echo normal; }', ctx)
    assert out(ctx, "warn a") == "normal\n" and seen == ["warn: a\n"]
    seen.clear()
    assert out(ctx, f"warn b 2> {tmp_path / 'w.txt'}") == "normal\n" and seen == []
    assert (tmp_path / "w.txt").read_text() == "warn: b\n"
    assert out(ctx, "warn c 2>&1 | tr a-z A-Z") == "WARN: C\nNORMAL\n"
    run_line("outer() { warn d; nosuchcmd; }", ctx)  # nested call and a failing command
    out(ctx, f"outer 2> {tmp_path / 'o.txt'}")
    assert (tmp_path / "o.txt").read_text() == "warn: d\nnosuchcmd: command not found: nosuchcmd\n"
    assert ctx.stderr_sinks == []  # every pushed sink popped


def test_blocks(err, tmp_path):
    ctx, seen = err
    target = tmp_path / "loop.txt"
    assert out(ctx, f"for i in 1 2; do echo e$i >&2; echo o$i; done 2> {target}") == "o1\no2\n"
    assert target.read_text() == "e1\ne2\n" and seen == []
    assert out(ctx, "for i in 1; do echo e$i >&2; done 2>&1 | tr a-z A-Z") == "E1\n"
    assert out(ctx, "for i in 1; do echo blk; done >&2") == "" and seen == ["blk\n"]


def test_without_a_printer_it_goes_to_the_process_stderr(ctx, capsys):
    assert out(ctx, "echo to-stderr >&2") == ""
    assert capsys.readouterr().err == "to-stderr\n"


def test_parser():
    pipeline = parse("echo x >&2 | cat")
    assert pipeline.segments[0].to_stderr and not pipeline.segments[1].to_stderr
    assert parse("echo x 1>&2").segments[0].to_stderr
    assert [t.value for t in tokenize("a1>&2")] == ["a1", ">&2"]  # 1>&2 only starts a word
    with pytest.raises(ParseError, match="only >&2, 1>&2 and 2>&1 are"):
        parse("echo x >&3")
    with pytest.raises(ParseError, match="missing command before '>&2'"):
        parse(">&2")
