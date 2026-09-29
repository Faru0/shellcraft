import os
import shutil
import subprocess

import pytest
from prompt_toolkit.history import InMemoryHistory

from core.context import ShellContext, Styled, to_text
from core.pipeline import PipelineError, run_line


def out(line, ctx):
    return to_text(run_line(line, ctx).output)


# ── diff ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def pair(tmp_path):
    (tmp_path / "a.txt").write_text("one\ntwo\nthree\nfour\nfive\n")
    (tmp_path / "b.txt").write_text("one\nTWO\nthree\n  four  \nfive\nsix\n")
    return tmp_path


def test_diff_classic_format(ctx, pair):
    assert out("diff a.txt b.txt", ctx) == (
        "2c2\n< two\n---\n> TWO\n4c4\n< four\n---\n>   four  \n5a6\n> six\n")


def test_diff_unified(ctx, pair):
    lines = out("diff -u a.txt b.txt", ctx).splitlines()
    assert lines[0].startswith("--- a.txt\t") and lines[1].startswith("+++ b.txt\t")
    assert lines[2:] == ["@@ -1,5 +1,6 @@", " one", "-two", "+TWO", " three", "-four", "+  four  ", " five", "+six"]


def test_diff_context_size(ctx, pair):
    assert "@@ -2 +2 @@" in out("diff -U 0 a.txt b.txt", ctx)


def test_diff_ignore_case_and_space(ctx, pair):
    assert out("diff -i -w a.txt b.txt", ctx) == "5a6\n> six\n"
    assert out("diff -b a.txt b.txt", ctx).count("c") >= 1  # -b keeps leading-space changes
    (pair / "c.txt").write_text("one\ntwo\nthree\nfour\nfive\nsix\n")
    assert out("diff -iw a.txt c.txt", ctx) == "5a6\n> six\n"


def test_diff_brief_identical_and_stdin(ctx, pair):
    assert out("diff -q a.txt b.txt", ctx) == "Files a.txt and b.txt differ\n"
    assert out("diff a.txt a.txt", ctx) == ""
    assert out("diff -s a.txt a.txt", ctx) == "Files a.txt and a.txt are identical\n"
    assert out("cat b.txt | diff -q - b.txt", ctx) == ""
    assert out("echo one | diff - a.txt", ctx).startswith("1a2,5")


def test_diff_directories(ctx, tmp_path):
    for side, text in (("L", "x\n"), ("R", "y\n")):
        (tmp_path / side / "sub").mkdir(parents=True)
        (tmp_path / side / "same.txt").write_text("same\n")
        (tmp_path / side / "sub" / "deep.txt").write_text(text)
    (tmp_path / "L" / "only.txt").write_text("")
    shallow = out("diff L R", ctx)
    assert "Only in L: only.txt" in shallow and "Common subdirectories: L/sub and R/sub" in shallow
    deep = out("diff -r -q L R", ctx)
    assert "Files L/sub/deep.txt and R/sub/deep.txt differ" in deep and "same.txt" not in deep
    assert "diff L/sub/deep.txt R/sub/deep.txt\n1c1\n< x\n---\n> y" in out("diff -r L R", ctx)
    (tmp_path / "same.txt").write_text("other\n")
    assert out("diff -q same.txt L", ctx) == "Files same.txt and L/same.txt differ\n"  # file vs dir


def test_diff_binary_and_errors(ctx, tmp_path):
    (tmp_path / "x.bin").write_bytes(b"\x00\x01")
    (tmp_path / "y.bin").write_bytes(b"\x00\x02")
    (tmp_path / "t.txt").write_text("t\n")
    assert out("diff x.bin y.bin", ctx) == "Binary files x.bin and y.bin differ\n"
    assert out("diff x.bin x.bin", ctx) == ""
    with pytest.raises(PipelineError, match="missing.txt: no such file"):
        run_line("diff t.txt missing.txt", ctx)
    with pytest.raises(PipelineError, match="-U must be >= 0"):
        run_line("diff -U -1 t.txt t.txt", ctx)


def test_diff_is_colored_on_screen(ctx, pair):
    result = run_line("diff -u a.txt b.txt", ctx).output
    assert isinstance(result, Styled)
    styles = {str(span.style) for span in result.renderable.spans}
    assert {"sc.error", "sc.success", "sc.accent"} <= styles


@pytest.mark.skipif(shutil.which("diff") is None, reason="needs GNU diff to compare with")
@pytest.mark.parametrize("flags", ["", "-u", "-U 1", "-U 0"])
def test_diff_matches_gnu(ctx, tmp_path, flags):
    import random

    rng = random.Random(42)
    base = [f"line {i}" for i in range(80)]
    changed = list(base)
    for _ in range(8):
        i = rng.randrange(len(changed))
        op = rng.choice("dic")
        if op == "d":
            del changed[i]
        elif op == "i":
            changed.insert(i, f"new {i}")
        else:
            changed[i] += " changed"
    (tmp_path / "x").write_text("\n".join(base) + "\n")
    (tmp_path / "y").write_text("\n".join(changed) + "\n")
    gnu = subprocess.run(["diff", *flags.split(), "x", "y"], capture_output=True, text=True, cwd=tmp_path).stdout
    ours = out(f"diff {flags} x y", ctx)
    body = lambda text: [ln for ln in text.splitlines() if not ln.startswith(("--- ", "+++ "))]  # noqa: E731
    assert body(ours) == body(gnu)


# ── du ───────────────────────────────────────────────────────────────────────

@pytest.fixture
def tree(tmp_path):
    (tmp_path / "d" / "sub").mkdir(parents=True)
    (tmp_path / "d" / "a.txt").write_bytes(b"x" * 1000)
    (tmp_path / "d" / "sub" / "b.bin").write_bytes(b"y" * 3000)
    return tmp_path


def rows(text):
    return [tuple(line.split("\t")) for line in text.splitlines()]


def test_du_bytes_all_files(ctx, tree):
    assert rows(out("du -b -a d", ctx)) == [
        ("1000", "d/a.txt"), ("3000", os.path.join("d", "sub", "b.bin")),
        ("3000", os.path.join("d", "sub")), ("4000", "d")]


def test_du_default_kib_rounded_up_and_summary(ctx, tree):
    assert rows(out("du d", ctx)) == [("3", os.path.join("d", "sub")), ("4", "d")]
    assert rows(out("du -s d", ctx)) == [("4", "d")]
    assert rows(out("du -sh d", ctx)) == [("3.9K", "d")]


def test_du_depth_sort_and_total(ctx, tree):
    (tree / "e").mkdir()
    (tree / "e" / "c").write_bytes(b"z" * 10)
    assert rows(out("du -b -d 0 d e -c", ctx)) == [("4000", "d"), ("10", "e"), ("4010", "total")]
    assert [r[1] for r in rows(out("du -b -a -d 1 -S d", ctx))] == ["d", os.path.join("d", "sub"), "d/a.txt"]


@pytest.mark.skipif(os.name == "nt", reason="hard links")
def test_du_counts_hard_links_once(ctx, tree):
    os.link(tree / "d" / "a.txt", tree / "d" / "sub" / "again.txt")
    assert rows(out("du -b -s d", ctx)) == [("4000", "d")]


def test_du_errors(ctx, tree):
    with pytest.raises(PipelineError, match="nope: no such file"):
        run_line("du nope", ctx)
    with pytest.raises(PipelineError, match="can't be combined"):
        run_line("du -s -d 2 d", ctx)


@pytest.mark.skipif(shutil.which("du") is None or os.name == "nt", reason="needs GNU du")
def test_du_matches_gnu_apparent_size(ctx, tree):
    gnu = subprocess.run(["du", "--apparent-size", "-b", "-a", "d"], capture_output=True, text=True, cwd=tree)
    if gnu.returncode != 0:
        pytest.skip("not GNU du")
    assert sorted(rows(out("du -b -a d", ctx))) == sorted(rows(gnu.stdout))


# ── history ──────────────────────────────────────────────────────────────────

def test_history_lists_numbered_and_last_n(ctx):
    ctx.history = InMemoryHistory()
    for line in ("ls", "echo hi", "grep x f"):
        ctx.history.append_string(line)
    assert out("history", ctx) == "  1  ls\n  2  echo hi\n  3  grep x f\n"
    assert out("history 1", ctx) == "  3  grep x f\n"
    assert out("history | grep echo", ctx) == "  2  echo hi\n"


def test_history_reads_the_file_in_c_mode_and_clears(ctx, tmp_path):
    home = tmp_path / ".home"
    home.mkdir()
    (home / "history").write_text("\n# t\n+first\n\n# t\n+second\n")
    assert out("history", ctx) == "  1  first\n  2  second\n"
    ctx.history = InMemoryHistory()
    ctx.history.append_string("x")
    run_line("history -c", ctx)
    assert (home / "history").read_text() == ""
    assert out("history", ctx) == ""


def test_history_usage_and_mcp(ctx):
    with pytest.raises(PipelineError, match="usage"):
        run_line("history abc", ctx)
    mcp = ShellContext(registry=ctx.registry, allow_stateful=False, allow_sensitive=False)
    with pytest.raises(PipelineError, match="not available"):
        run_line("history", mcp)
