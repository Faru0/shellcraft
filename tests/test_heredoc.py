"""Here-documents and here-strings (shellcraft.heredocs), in lines, scripts, funcs and at the prompt."""

import pytest

from shellcraft import check, funcs, heredocs, startup
from shellcraft.context import to_text
from shellcraft.parser import ParseError, parse
from shellcraft.pipeline import run_line


class _ScriptedSession:
    def __init__(self, lines):
        from types import SimpleNamespace

        self.lines = list(lines)
        self.app = SimpleNamespace(erase_when_done=False, output=SimpleNamespace(flush=lambda: None))
        self.bottom_toolbar = None

    def prompt(self, message, **kwargs):
        if not self.lines:
            raise EOFError
        return self.lines.pop(0)


@pytest.fixture
def repl(monkeypatch, tmp_path):
    from rich.console import Console

    from shellcraft import shell
    from shellcraft.context import ShellContext
    from shellcraft.loader import ModuleRegistry
    from shellcraft.themes import UI, all_themes

    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(shell, "create_prompt_toolkit_console", lambda cleanup: None)
    console = Console(record=True, width=120, force_terminal=False, color_system=None)
    ui = UI(all_themes({})["nord"], console)

    def run(*lines):
        ctx = ShellContext(registry=ModuleRegistry(tmp_path), ui=ui, config={})
        with shell.Shell(ctx) as sh:
            sh.session = _ScriptedSession(lines)
            sh.loop()
            return sh, console.export_text()
    return run


def out(ctx, line):
    result = run_line(line, ctx)
    return "" if result is None or result.output is None else (
        result.output if isinstance(result.output, str) else result.output.text)


def run_file(ctx, tmp_path, text):
    path = tmp_path / "t.sc"
    path.write_text(text)
    shown = []
    ctx.emit = lambda value: shown.append(to_text(value))
    ctx.emit_error = lambda exc, where: shown.append(f"ERR {exc} @ {where}\n")
    startup.source(path, ctx)
    return "".join(shown)


# ── the pre-pass ─────────────────────────────────────────────────────────────

def test_encode_moves_the_body_into_the_command_and_keeps_the_line_count():
    text = "cat <<EOF\none\ntwo\nEOF\necho after\n"
    encoded = heredocs.encode(text)
    lines = encoded.split("\n")
    assert len(lines) == len(text.split("\n"))
    assert lines[0].startswith("cat <<<@@HD") and lines[1:4] == ["", "", ""] and lines[4] == "echo after"
    word = lines[0].split("<<<")[1]
    assert heredocs.decode(word) == ("one\ntwo\n", False)


def test_encode_leaves_other_text_alone():
    for text in ("echo hi", "echo $((1<<3))", "echo '<<EOF'", 'echo "<<EOF"', "echo x # <<EOF", "cat <<< word",
                 "(( a = 1 << 2 ))", "echo a<<"):
        assert heredocs.encode(text) == text


def test_quoted_delimiters_are_literal_and_dash_strips_tabs():
    quoted = heredocs.encode("cat <<'E'\n$x\nE")
    assert heredocs.decode(quoted.split("<<<")[1].split("\n")[0]) == ("$x\n", True)
    for form in ('cat <<"E"\n$x\nE', "cat <<\\E\n$x\nE"):
        assert heredocs.decode(heredocs.encode(form).split("<<<")[1].split("\n")[0])[1] is True
    dashed = heredocs.encode("cat <<-E\n\ta\n\t\tb\n\tE")
    assert heredocs.decode(dashed.split("<<<")[1].split("\n")[0]) == ("a\nb\n", False)


def test_two_here_documents_on_one_line_and_a_missing_delimiter():
    encoded = heredocs.encode("cat <<A; cat <<B\n1\nA\n2\nB")
    assert encoded.count("<<<@@HD") == 2
    assert heredocs.encode("cat <<EOF\nno end") .count("<<<@@HD") == 1  # bash warns and reads to the end
    with pytest.raises(heredocs.Unterminated, match="wanted 'EOF'"):
        heredocs.encode("cat <<EOF\nno end", strict=True)


# ── running ──────────────────────────────────────────────────────────────────

def test_unquoted_bodies_expand_and_quoted_ones_do_not(ctx):
    out(ctx, "x=5")
    body = "v=$x \\$x ${x}0 $(echo hi) $((1+2)) \"q\" 'q' \\\\ \\z"
    assert out(ctx, f"cat <<EOF\n{body}\nEOF") == 'v=5 $x 50 hi 3 "q" \'q\' \\ \\z\n'
    assert out(ctx, f"cat <<'EOF'\n{body}\nEOF") == body + "\n"
    assert out(ctx, f'cat <<"EOF"\n{body}\nEOF') == body + "\n"
    assert out(ctx, "cat <<\\EOF\n$x\nEOF") == "$x\n"


def test_dash_strips_leading_tabs_from_the_body_and_the_delimiter(ctx):
    assert out(ctx, "cat <<-EOF\n\tone\n\t\ttwo\n\tEOF") == "one\ntwo\n"
    assert out(ctx, "cat <<EOF\n\tkept\nEOF") == "\tkept\n"


def test_a_backslash_at_the_end_of_a_line_joins_lines_in_an_unquoted_body(ctx):
    assert out(ctx, "cat <<EOF\nline \\\njoined\nEOF") == "line joined\n"
    assert out(ctx, "cat <<'EOF'\nline \\\njoined\nEOF") == "line \\\njoined\n"


def test_comments_and_blank_lines_in_a_body_are_text(ctx):
    assert out(ctx, "cat <<EOF\n# not a comment\n\n  indented\nEOF") == "# not a comment\n\n  indented\n"


def test_here_documents_with_pipes_redirects_and_more_than_one(ctx, tmp_path):
    assert out(ctx, "cat <<EOF | sort\nb\na\nEOF") == "a\nb\n"
    out(ctx, "cat <<EOF > out.txt\nsaved\nEOF")
    assert (tmp_path / "out.txt").read_text() == "saved\n"
    assert out(ctx, "cat <<A; cat <<B\none\nA\ntwo\nB") == "one\ntwo\n"
    assert out(ctx, "cat <<EOF\nx\nEOF\necho after") == "x\nafter\n"


def test_here_strings(ctx):
    out(ctx, "who=Ada")
    assert out(ctx, 'cat <<< "hello $who"') == "hello Ada\n"
    assert out(ctx, "cat <<< 'no $expansion'") == "no $expansion\n"
    assert out(ctx, "cat <<< word") == "word\n"
    assert out(ctx, 'cat <<< "$(echo sub)"') == "sub\n"
    assert out(ctx, 'cat <<< "a  b" | tr a-z A-Z') == "A  B\n"


def test_here_strings_and_documents_feed_loops(ctx):
    assert out(ctx, 'while read a b; do echo "$a:$b"; done <<< "x y"') == "x:y\n"
    assert out(ctx, "while read a b; do echo \"$a:$b\"; done <<EOF\n1 2\n3 4\nEOF") == "1:2\n3:4\n"
    assert out(ctx, "for x in 1 2; do read l; echo \"$x $l\"; done <<EOF\np\nq\nEOF") == "1 p\n2 q\n"
    assert out(ctx, "n=0; while read l; do n=$((n+1)); done <<EOF\na\nb\nEOF\necho $n") == "2\n"


def test_here_documents_inside_blocks_and_functions(ctx):
    assert out(ctx, "for i in 1 2; do\n  cat <<EOF\nitem $i\nEOF\ndone") == "item 1\nitem 2\n"
    assert out(ctx, "if true; then\n  cat <<EOF\n  body\nEOF\nfi") == "  body\n"
    out(ctx, "f() { cat <<EOF\nin f $1\nEOF\n}")
    assert out(ctx, "f z") == "in f z\n"


def test_arithmetic_shift_is_not_a_here_document(ctx):
    assert out(ctx, "echo $((1<<3))") == "8\n"
    assert out(ctx, "for i in 1; do echo $((i<<2)); done") == "4\n"


def test_a_bare_here_document_operator_says_what_is_missing():
    with pytest.raises(ParseError, match="delimiter"):
        parse("cat <<")
    with pytest.raises(ParseError, match="missing word after '<<<'"):
        parse("cat <<<")


def test_the_input_is_the_first_commands_only():
    with pytest.raises(ParseError, match="only applies to the first command"):
        parse("a | b <<< x")
    assert parse("cat <<< x").stdin_text == "x\n"


# ── files, funcs, source -n ──────────────────────────────────────────────────

def test_in_a_script_file_with_errors_on_the_right_lines(ctx, tmp_path):
    text = ("echo start\n"
            "cat <<EOF\n"
            "body $((1+1))\n"
            "EOF\n"
            "ecoh typo\n")
    shown = run_file(ctx, tmp_path, text)
    assert shown.startswith("start\nbody 2\n")
    assert "t.sc:5: ecoh typo" in shown  # the body's three lines are still counted


def test_source_n_reports_lines_after_a_here_document(ctx):
    text = "cat <<EOF\n# one\ntwo\nEOF\nfor x in 1; do\necho $x\n"
    report = check.check_text(text, "f.sc", ctx)
    assert [(p.line, p.error) for p in report.problems] == [(5, True)]  # the `for` that never ends
    clean = check.check_text("cat <<'EOF'\nfor x in a; do\nEOF\necho ok\n", "f.sc", ctx)
    assert clean.problems == []  # the body is text, not a loop with no `done`
    ended = check.check_text("cat <<EOF\nnever ends\n", "f.sc", ctx)
    assert ended.problems[0].line == 1 and "here-document" in ended.problems[0].message


def test_logical_lines_numbers(ctx):
    lines = startup.logical_lines("a\ncat <<EOF\nx\nEOF\nb\n")
    assert [number for number, _ in lines] == [1, 2, 5]


def test_a_func_with_a_here_document():
    func = funcs.Func(name="greet", body="cat <<EOF\nhello $1\nEOF\necho done\n")
    assert [step.split("<<<")[0] for step in func.steps] == ["cat ", "echo done"]


def test_source_runs_a_file_with_a_here_document(ctx, tmp_path):
    (tmp_path / "s.sc").write_text("name=Bob\ncat <<EOF\nhi $name\nEOF\n")
    shown = []
    ctx.emit = lambda value: shown.append(to_text(value))
    out(ctx, "source s.sc")
    assert "".join(shown) == "hi Bob\n"


# ── typed at the prompt ──────────────────────────────────────────────────────

def test_the_prompt_reads_lines_until_the_delimiter(repl):
    sh, text = repl("x=7", "cat <<EOF", "value $x", "", "  two", "EOF", "echo after")
    assert "value 7\n\n  two\nafter\n" in text
    assert sh.ctx.history.get_strings()[-2] == "cat <<EOF\nvalue $x\n\n  two\nEOF"


def test_the_prompt_reads_a_here_document_inside_a_block(repl):
    sh, text = repl("for i in 1 2; do", "cat <<EOF", "n=$i", "EOF", "done")
    assert "n=1\nn=2\n" in text


def test_the_prompt_reads_a_loop_over_a_here_document(repl):
    sh, text = repl("while read a b; do echo $b$a; done <<EOF", "1 2", "3 4", "EOF")
    assert "21\n43\n" in text


def test_an_unfinished_here_document_is_reported_at_end_of_input(repl):
    sh, text = repl("cat <<EOF", "never ends")
    assert "wanted 'EOF'" in text and sh.ctx.last_status == 2
