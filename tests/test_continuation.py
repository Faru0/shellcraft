"""A trailing backslash joins the next line in `.sc` files, funcs and `source` (shellcraft.heredocs.encode)."""

from shellcraft import check, funcs, heredocs, startup
from shellcraft.context import to_text
from shellcraft.parser import join_continued_lines


def out_of(ctx, tmp_path, text):
    path = tmp_path / "t.sc"
    path.write_text(text)
    shown = []
    ctx.emit = lambda value: shown.append(to_text(value))
    ctx.emit_error = lambda exc, where: shown.append(f"ERR {exc} @ {where}\n")
    startup.source(path, ctx)
    return "".join(shown)


def commands(text):
    return startup.logical_lines(text)


def test_a_trailing_backslash_joins_the_next_line():
    assert commands("echo one \\\n  two\n") == [(1, "echo one   two")]
    assert commands("echo a \\\nb \\\nc\necho d\n") == [(1, "echo a b c"), (4, "echo d")]


def test_numbers_stay_the_file_lines():
    assert commands("echo a \\\nb\n\necho c\n") == [(1, "echo a b"), (4, "echo c")]


def test_an_even_number_of_backslashes_does_not_continue():
    assert commands("echo a\\\\\necho b\n") == [(1, "echo a\\\\"), (2, "echo b")]
    assert commands("echo a\\\\\\\nb\n") == [(1, "echo a\\\\b")]  # three: the last one continues


def test_single_quotes_keep_the_backslash_double_quotes_join():
    assert heredocs.encode("echo 'a \\\nb'") == "echo 'a \\\nb'"
    assert commands('echo "a \\\nb"') == [(1, 'echo "a b"')]


def test_a_comment_never_continues():
    assert commands("echo a # note \\\necho b\n") == [(1, "echo a"), (2, "echo b")]
    assert commands("# whole line comment \\\necho b\n") == [(2, "echo b")]
    assert commands("echo a \\# not a comment\\\nb\n") == [(1, "echo a \\# not a comment" + "b")]


def test_a_quoted_here_document_body_is_not_joined(ctx, tmp_path):
    text = "cat <<'EOF'\nkeeps \\\nits break\nEOF\ncat <<EOF\njoins \\\nthese\nEOF\n"
    assert out_of(ctx, tmp_path, text) == "keeps \\\nits break\njoins these\n"


def test_a_continued_line_can_carry_a_here_document():
    lines = commands("cat <<EOF \\\n  | sort\nb\na\nEOF\necho after\n")
    assert [number for number, _ in lines] == [1, 6]
    assert lines[0][1].startswith("cat <<<@@HD") and lines[0][1].endswith("  | sort")


def test_a_continued_block_header_and_a_blocks_lines(ctx, tmp_path):
    text = "for x in a \\\n  b; do\n  echo $x \\\n    z\ndone\n"
    assert out_of(ctx, tmp_path, text) == "a z\nb z\n"


def test_source_runs_joined_lines_and_errors_name_the_first_line(ctx, tmp_path):
    text = "echo one \\\n  two\nnosuchcmd \\\n  arg\necho end\n"
    shown = out_of(ctx, tmp_path, text)
    assert shown.startswith("one two\n")
    assert "t.sc:3: nosuchcmd" in shown and shown.endswith("end\n")


def test_a_func_joins_its_continued_lines():
    func = funcs.Func(name="f", body="echo a \\\n  b\necho c\n")
    assert func.steps == ["echo a   b", "echo c"]


def test_the_checker_counts_physical_lines(ctx):
    report = check.check_text("echo ok \\\n  more\necoh typo\n", "f.sc", ctx)
    assert [(p.line, p.error) for p in report.problems] == [(3, False)]


def test_the_typed_line_keeps_its_own_joining(ctx):
    from shellcraft.pipeline import run_line

    result = run_line("echo one \\\n  two", ctx)
    assert result.output == "one two\n"
    assert join_continued_lines("echo a\\\\\nb") == "echo a\\\\\nb"  # an even count is no continuation
