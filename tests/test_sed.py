"""The sed builtin: the examples people paste from Linux, checked against GNU sed's behaviour."""

import re

import pytest

from shellcraft import ai_run
from shellcraft.context import to_text
from shellcraft.pipeline import PipelineError, run_line

TEXT = ("unix is unix\n# comment\n\nerror: false\nHello World foo\nabc abc abc\nline 7\nline 8\n"
        "foo bar baz\nlast line\n")


def out(line, ctx):
    return to_text(run_line(line, ctx).output)


@pytest.fixture
def f(tmp_path, ctx):
    (tmp_path / "f.txt").write_text(TEXT)
    return tmp_path / "f.txt"


@pytest.mark.parametrize("script, expected_start", [
    ("'s/unix/linux/'", "linux is unix\n"),
    ("'s/unix/linux/g'", "linux is linux\n"),
    ("'s/UNIX/linux/gI'", "linux is linux\n"),
    ("'s/o/0/2'", "unix is unix\n# comment\n\nerror: false\nHello W0rld foo\n"),
    ("'s/\\(abc\\) \\(abc\\)/\\2-\\1/'", "unix is unix\n# comment\n\nerror: false\nHello World foo\nabc-abc abc\n"),
    ("-E 's/(abc) (abc)/\\2-\\1/'", "unix is unix\n# comment\n\nerror: false\nHello World foo\nabc-abc abc\n"),
])
def test_substitute(ctx, f, script, expected_start):
    assert out(f"sed {script} f.txt", ctx).startswith(expected_start)


def test_the_replacement_has_groups_and_case_operators(ctx, f):
    assert out("echo hello world | sed 's/\\w\\+/\\u&/g'", ctx) == "Hello World\n"
    assert out("echo hello | sed 's/.*/\\U&/'", ctx) == "HELLO\n"
    assert out("echo 'ab' | sed -E 's/(a)(b)/\\2\\1&/'", ctx) == "baab\n"
    assert out("echo a/b | sed 's|/|-|'", ctx) == "a-b\n"
    assert out("echo a/b | sed 's/[/]/X/'", ctx) == "aXb\n"
    assert out("echo abxd | sed 's/x*/-/g'", ctx) == "-a-b-d-\n"


def test_delete_and_print_with_addresses(ctx, f):
    assert out("sed -n '2,3p' f.txt", ctx) == "# comment\n\n"
    assert out("sed -n '$p' f.txt", ctx) == "last line\n"
    assert out("sed '$d' f.txt", ctx).splitlines()[-1] == "foo bar baz"
    assert "# comment" not in out("sed '/^#/d' f.txt", ctx)
    assert "\n\n" not in out("sed '/^$/d' f.txt", ctx)
    assert out("sed '3,9d' f.txt", ctx).count("\n") == 3
    assert out("sed -n '11,15p' f.txt", ctx) == ""
    assert out("sed -n '0~3p' f.txt", ctx) == "\nabc abc abc\nfoo bar baz\n"
    assert out("sed '1~2d' f.txt", ctx).startswith("# comment\nerror: false\n")
    assert out("sed -n '/abc/,/foo/p' f.txt", ctx) == "abc abc abc\nline 7\nline 8\nfoo bar baz\n"
    assert out("sed '/line 7/,+1d' f.txt", ctx).count("line") == 1
    assert out("sed '2!d' f.txt", ctx) == "# comment\n"
    assert out("sed -n '5,2p' f.txt", ctx) == "Hello World foo\n"  # an end before the start: one line


def test_insert_append_change_and_more(ctx, f):
    assert out("sed -n '2{=;p}' f.txt", ctx) == "2\n# comment\n"
    assert out("sed '2i inserted' f.txt", ctx).splitlines()[1:3] == ["inserted", "# comment"]
    assert out("sed '2a\\\nadded' f.txt", ctx).splitlines()[1:3] == ["# comment", "added"]
    assert out("sed '2,4c changed' f.txt", ctx).splitlines()[:2] == ["unix is unix", "changed"]
    assert out("sed 'y/abc/xyz/' f.txt", ctx).splitlines()[5] == "xyz xyz xyz"
    assert out("sed 3q f.txt", ctx).count("\n") == 3
    assert out("sed 3Q f.txt", ctx).count("\n") == 2
    assert run_line("sed 3q7 f.txt", ctx).status == 7
    assert out("sed -e 's/unix/A/' -e 's/A/B/' f.txt", ctx).startswith("B is unix\n")
    assert out("sed -n '/Hello/{s/World/There/;p}' f.txt", ctx) == "Hello There foo\n"
    assert out("sed '/e/s//E/' f.txt", ctx).splitlines()[3] == "Error: false"


def test_regular_expression_translation(ctx):
    assert out("echo 'a+b (c)' | sed 's/a+b/X/'", ctx) == "X (c)\n"  # basic: + and ( are plain
    assert out("echo 'aab' | sed 's/a\\+/X/'", ctx) == "Xb\n"
    assert out("echo 'a+b' | sed -E 's/a\\+b/X/'", ctx) == "X\n"
    assert out("echo 'aaa' | sed 's/a\\{2\\}/X/'", ctx) == "Xa\n"
    assert out("echo 'a1b22' | sed 's/[[:digit:]]\\+/N/g'", ctx) == "aNbN\n"
    assert out("echo '*a' | sed 's/*a/X/'", ctx) == "X\n"  # a leading * is literal
    assert out("echo 'a^b$c' | sed 's/a^b$c/X/'", ctx) == "X\n"
    assert out("echo 'cat dog' | sed 's/\\<d/D/'", ctx) == "cat Dog\n"
    assert out("echo 'a|b' | sed 's/a|b/X/'", ctx) == "X\n"
    assert out("echo 'a.b' | sed 's/\\./!/'", ctx) == "a!b\n"


def test_files_and_streams(ctx, f, tmp_path):
    (tmp_path / "g.txt").write_text("one\ntwo\nthree")
    assert out("sed -n '$p' f.txt g.txt", ctx) == "three\n"
    assert out("sed -s -n '$p' f.txt g.txt", ctx) == "last line\nthree\n"
    assert out("ls | sed 's/^/> /'", ctx).startswith("> ")
    assert out("echo a | sed s/a/b/", ctx) == "b\n"
    (tmp_path / "script.sed").write_text("s/one/1/\n2d\n")
    assert out("sed -f script.sed g.txt", ctx) == "1\nthree\n"


def test_in_place(ctx, f, tmp_path):
    out("sed -i 's/unix/linux/g' f.txt", ctx)
    assert f.read_text().startswith("linux is linux\n")
    out("sed -i.bak '1d' f.txt", ctx)
    assert (tmp_path / "f.txt.bak").read_text().startswith("linux is linux\n")
    assert f.read_text().startswith("# comment\n")
    (tmp_path / "g.txt").write_text("one\ntwo\nthree")  # no final newline: kept
    out("sed -i 's/t/T/' g.txt", ctx)
    assert (tmp_path / "g.txt").read_text() == "one\nTwo\nThree"
    (tmp_path / "crlf.txt").write_bytes(b"a\r\nb\r\n")
    out("sed -i 's/a/A/' crlf.txt", ctx)
    assert (tmp_path / "crlf.txt").read_bytes() == b"A\r\nb\r\n"
    out("sed -i 2q g.txt", ctx)
    assert (tmp_path / "g.txt").read_text() == "one\nTwo\n"
    out("sed -i -e 's/one/1/' g.txt", ctx)
    assert (tmp_path / "g.txt").read_text() == "1\nTwo\n"
    out("sed -ni 1p g.txt", ctx)
    assert (tmp_path / "g.txt").read_text() == "1\n"


@pytest.mark.parametrize("line, message", [
    ("sed 'k' f.txt", "unknown command: `k'"),
    ("sed 'x' f.txt", "command `x' is not supported"),
    ("sed 's/a/b' f.txt", "unterminated `s' command"),
    ("sed 's/a/b/z' f.txt", "unknown option to `s'"),
    ("sed 's/\\(a/b/' f.txt", "invalid regular expression"),
    ("sed 's/a/\\1/' f.txt", "invalid reference \\1"),
    ("sed '{p' f.txt", "unmatched `{'"),
    ("sed 'p}' f.txt", "unexpected `}'"),
    ("sed 'y/ab/c/' f.txt", "different lengths"),
    ("sed 'p' nope.txt", "can't read nope.txt"),
    ("sed", "no script given"),
    ("sed -i p", "no input files"),
    ("sed -z p f.txt", "unknown option"),
    ("sed '2,p' f.txt", "unexpected `,'"),
])
def test_errors(ctx, f, line, message):
    with pytest.raises(PipelineError, match=re.escape(message)):
        run_line(line, ctx)


def test_the_ai_gate(ctx, f):
    assert ai_run.read_only("sed s/a/b/ f.txt", ctx)
    assert ai_run.read_only("sed -n '1,5p' f.txt | wc -l", ctx)
    assert not ai_run.read_only("sed -i s/a/b/ f.txt", ctx)
    assert not ai_run.read_only("sed -i.bak s/a/b/ f.txt", ctx)
    assert not ai_run.read_only("sed -ni 1p f.txt", ctx)
    assert not ai_run.read_only("sed --in-place s/a/b/ f.txt", ctx)
    assert ai_run.read_only("sed -e s/i/x/ f.txt", ctx)  # the i is part of the script, not the -i flag


def test_the_sandbox_refuses_in_place_only(ctx, f):
    ctx.allow_writes = False
    assert out("sed s/unix/linux/ f.txt", ctx).startswith("linux")
    with pytest.raises(PipelineError, match="not available in this context"):
        run_line("sed -i s/unix/linux/ f.txt", ctx)
    assert f.read_text() == TEXT
