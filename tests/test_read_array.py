"""`read -a NAME` and `mapfile` / `readarray`: input into an array."""

import pytest

from shellcraft.context import to_text
from shellcraft.inputs import split_all, text_chars
from shellcraft.pipeline import PipelineError, run_line
from shellcraft.values import ShArray


def out(ctx, line):
    result = run_line(line, ctx)
    return "" if result is None or result.output is None else to_text(result.output)


def items(ctx, name):
    held = ctx.variables[name]
    assert isinstance(held, ShArray), f"{name} is {held!r}"
    return list(held.items)


@pytest.mark.parametrize("line, ifs, expected", [
    ("x y  z", " \t\n", ["x", "y", "z"]),
    ("  x  ", " \t\n", ["x"]),
    ("", " \t\n", []),
    ("a::b:", ":", ["a", "", "b"]),  # one closing delimiter ends the last field, as in bash
    (":a", ":", ["", "a"]),
    ("a : b", ": ", ["a", "b"]),
    ("a b", "", ["a b"]),  # IFS empty: no splitting
    (r"a\ b c", " ", ["a b", "c"]),  # an escaped blank does not split
])
def test_split_all(line, ifs, expected):
    assert split_all(text_chars(line, raw=False), ifs) == expected


def test_read_a_takes_every_word(ctx):
    run_line('read -a parts <<< "x y  z"', ctx)
    assert items(ctx, "parts") == ["x", "y", "z"]
    run_line('IFS=: read -ra f <<< "a::b:"', ctx)
    assert items(ctx, "f") == ["a", "", "b"]
    run_line('read -a none <<< ""', ctx)
    assert items(ctx, "none") == []
    run_line("echo one two | read -r -a piped", ctx)  # a pipe keeps the variable here
    assert items(ctx, "piped") == ["one", "two"]
    with pytest.raises(PipelineError, match="invalid variable name"):
        run_line("read -a 9x <<< a", ctx)
    with pytest.raises(PipelineError, match="-a: option requires an argument"):
        run_line("read -a <<< a", ctx)


def test_read_a_in_a_loop(ctx, tmp_path):
    table = tmp_path / "t.txt"
    table.write_text("web01 10.0.0.1 up\ndb01 10.0.0.2\n")
    assert out(ctx, f'while read -ra row; do echo "${{#row[@]}} ${{row[0]}} ${{row[-1]}}"; done < {table}') \
        == "3 web01 up\n2 db01 10.0.0.2\n"


def test_mapfile_reads_lines(ctx, tmp_path):
    hosts = tmp_path / "hosts.txt"
    hosts.write_text("web01\ndb01\ncache 01\n")
    run_line(f"mapfile -t lines < {hosts}", ctx)
    assert items(ctx, "lines") == ["web01", "db01", "cache 01"]
    run_line(f"mapfile raw < {hosts}", ctx)  # without -t each line keeps its new line
    assert items(ctx, "raw") == ["web01\n", "db01\n", "cache 01\n"]
    run_line(f"cat {hosts} | readarray -t piped", ctx)
    assert items(ctx, "piped") == ["web01", "db01", "cache 01"]
    run_line(f"mapfile -t < {hosts}", ctx)  # no name: MAPFILE
    assert items(ctx, "MAPFILE") == ["web01", "db01", "cache 01"]
    run_line(f"mapfile -t -s 1 -n 1 one < {hosts}", ctx)
    assert items(ctx, "one") == ["db01"]
    run_line(f"mapfile -ts2 two < {hosts}", ctx)
    assert items(ctx, "two") == ["cache 01"]
    (tmp_path / "open.txt").write_text("a\nb")  # a last line without a new line is still a line
    run_line(f"mapfile -t open < {tmp_path / 'open.txt'}", ctx)
    assert items(ctx, "open") == ["a", "b"]
    run_line("lines=(old); mapfile -t lines < /dev/null", ctx)  # replaced, not added to
    assert items(ctx, "lines") == []
    (tmp_path / "csv.txt").write_text("a,b,c")
    run_line(f"mapfile -t -d , cells < {tmp_path / 'csv.txt'}", ctx)
    assert items(ctx, "cells") == ["a", "b", "c"]
    assert out(ctx, f'mapfile -t hosts < {hosts}; for h in "${{hosts[@]}}"; do echo "<$h>"; done') \
        == "<web01>\n<db01>\n<cache 01>\n"


def test_mapfile_options_are_checked(ctx):
    with pytest.raises(PipelineError, match="mapfile: -x: not supported"):
        run_line("mapfile -x lines <<< a", ctx)
    with pytest.raises(PipelineError, match="invalid number"):
        run_line("mapfile -n many lines <<< a", ctx)
    with pytest.raises(PipelineError, match="option requires an argument"):
        run_line("mapfile -n <<< a", ctx)
    with pytest.raises(PipelineError, match="usage: mapfile"):
        run_line("mapfile a b <<< a", ctx)
    with pytest.raises(PipelineError, match="invalid variable name"):
        run_line("mapfile 9x <<< a", ctx)
    run_line("readonly locked=1", ctx)
    with pytest.raises(PipelineError, match="locked: readonly variable"):
        run_line("mapfile locked <<< a", ctx)


def test_man_pages(ctx):
    assert "one element per line" in out(ctx, "man mapfile") and "# mapfile" in out(ctx, "man readarray")
    assert "-a ARRAY" in out(ctx, "man read")
