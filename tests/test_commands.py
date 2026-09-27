import os
import re
from pathlib import Path

import pytest

from core.context import Styled, to_text
from core.pipeline import PipelineError, run_line


def out(line, ctx):
    return to_text(run_line(line, ctx).output)


@pytest.fixture
def files(tmp_path):
    (tmp_path / "a.txt").write_text("apple\nbanana\ncherry\n")
    (tmp_path / "b.txt").write_text("Banana split\nkiwi\n")
    (tmp_path / "nums.txt").write_text("10\n9\n100\nx\n")
    (tmp_path / ".hidden").write_text("secret\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "inner.txt").write_text("deep\n")
    return tmp_path


def test_ls_plain_when_piped(ctx, files):
    assert out("ls", ctx) == "a.txt\nb.txt\nnums.txt\nsub/\n"
    assert ".hidden" in out("ls -a", ctx)
    assert out("ls | grep txt | wc -l", ctx) == "3\n"


def test_ls_screen_output_is_styled(ctx, files):
    assert isinstance(run_line("ls", ctx).output, Styled)


def test_ls_long_and_missing(ctx, files):
    assert re.search(r"^-rw.* a\.txt$", out("ls -l a.txt", ctx), re.M)
    with pytest.raises(PipelineError, match="no such file"):
        run_line("ls nope", ctx)


def test_cat(ctx, files):
    assert out("cat a.txt b.txt", ctx) == "apple\nbanana\ncherry\nBanana split\nkiwi\n"
    assert out("cat -n b.txt", ctx) == "     1\tBanana split\n     2\tkiwi\n"
    assert out("echo piped | cat", ctx) == "piped\n"


def test_grep(ctx, files):
    assert out("grep -i banana a.txt b.txt", ctx) == "a.txt:banana\nb.txt:Banana split\n"
    assert out("cat a.txt | grep -v an", ctx) == "apple\ncherry\n"
    assert out("grep -c an a.txt", ctx) == "1\n"
    assert out("grep -l kiwi a.txt b.txt", ctx) == "b.txt\n"
    assert out("grep -n -F . a.txt", ctx) == ""


def test_echo_flags(ctx):
    assert out("echo -n hi", ctx) == "hi"
    assert out(r"echo -e a\tb\nc", ctx) == "a\tb\nc\n"
    assert out("echo -e café", ctx) == "café\n"


def test_head_tail(ctx, files):
    assert out("head -2 a.txt", ctx) == "apple\nbanana\n"
    assert out("tail -n 1 a.txt", ctx) == "cherry\n"
    assert out("tail -n 0 a.txt", ctx) == ""
    assert "==> b.txt <==" in out("head -1 a.txt b.txt", ctx)


def test_wc(ctx, files):
    assert out("wc -l a.txt", ctx).split() == ["3", "a.txt"]
    assert out("cat a.txt | wc -w", ctx) == "3\n"
    assert out("wc -l a.txt b.txt", ctx).splitlines()[-1].split() == ["5", "total"]


def test_sort_and_uniq(ctx, files):
    assert out("sort -n -r nums.txt", ctx) == "100\n10\n9\nx\n"
    assert out("echo -e b\\na\\nb | sort -u", ctx) == "a\nb\n"
    assert out("echo -e a\\na\\nb | uniq -c", ctx) == "      2 a\n      1 b\n"
    assert out("echo -e a\\nA\\nb | uniq -i -d", ctx) == "a\n"
    assert out("echo -e 'x 3\\ny 1' | sort -k 2", ctx) == "y 1\nx 3\n"


def test_date(ctx):
    assert re.fullmatch(r"\d{4}\n", out("date +%Y", ctx))
    assert "T" in out("date -u --iso", ctx)


def test_tee(ctx, files):
    assert out("echo one | tee t.txt", ctx) == "one\n"
    run_line("echo two | tee -a t.txt", ctx)
    assert (files / "t.txt").read_text() == "one\ntwo\n"


def test_mkdir_cp_mv(ctx, files):
    run_line("mkdir -p x/y/z", ctx)
    assert (files / "x/y/z").is_dir()
    with pytest.raises(PipelineError, match="already exists"):
        run_line("mkdir x", ctx)
    run_line("cp a.txt x", ctx)
    assert (files / "x/a.txt").read_text().startswith("apple")
    with pytest.raises(PipelineError, match="use -r"):
        run_line("cp sub copy", ctx)
    run_line("cp -r sub copy", ctx)
    assert (files / "copy/inner.txt").exists()
    run_line("mv b.txt renamed.txt", ctx)
    assert (files / "renamed.txt").exists() and not (files / "b.txt").exists()
    with pytest.raises(PipelineError, match="not a directory"):
        run_line("mv a.txt nums.txt renamed.txt", ctx)


def test_rm(ctx, files):
    run_line("rm a.txt", ctx)
    assert not (files / "a.txt").exists()
    with pytest.raises(PipelineError, match="is a directory"):
        run_line("rm sub", ctx)
    run_line("rm -rf sub missing.txt", ctx)
    assert not (files / "sub").exists()
    with pytest.raises(PipelineError, match="no such file"):
        run_line("rm missing.txt", ctx)


@pytest.mark.parametrize("target", [".", "..", "~", os.path.abspath(os.sep)])
def test_rm_refuses_dangerous_targets(ctx, files, target):
    with pytest.raises(PipelineError, match="refusing"):
        run_line(f"rm -rf {target}", ctx)
    assert Path(files).exists()


def test_rm_validates_all_before_deleting(ctx, files):
    with pytest.raises(PipelineError):
        run_line("rm a.txt ~", ctx)
    assert (files / "a.txt").exists()


def test_write_commands_blocked_when_writes_disabled(ctx, files):
    ctx.allow_writes = False
    for line in ("echo x | tee t.txt", "mkdir d", "cp a.txt c.txt", "mv a.txt m.txt", "rm a.txt", "touch t"):
        with pytest.raises(PipelineError, match="not available"):
            run_line(line, ctx)
    assert out("cat a.txt | sort -r | head -1", ctx) == "cherry\n"


def test_man_uses_builtin_docs(ctx):
    assert out("man grep", ctx).startswith("# grep")


# ── find / tree / touch ──────────────────────────────────────────────────────

def test_find(ctx, files):
    j = os.path.join
    assert out('find . -name "*.txt" -maxdepth 1', ctx) == f"{j('.', 'a.txt')}\n{j('.', 'b.txt')}\n{j('.', 'nums.txt')}\n"
    assert out("find sub", ctx) == f"sub\n{j('sub', 'inner.txt')}\n"
    assert out("find . -type d", ctx) == f".\n{j('.', 'sub')}\n"
    assert out('find . -iname "A.TXT"', ctx) == f"{j('.', 'a.txt')}\n"
    assert out("find . -mindepth 2", ctx) == f"{j('.', 'sub', 'inner.txt')}\n"
    assert j(".", ".hidden") in out("find .", ctx)
    with pytest.raises(PipelineError, match="no such file"):
        run_line("find nope", ctx)


def test_tree(ctx, files):
    text = out("tree", ctx)
    assert text.splitlines()[0] == "."
    assert "├── a.txt" in text and "└── sub/" in text and "    └── inner.txt" in text
    assert text.rstrip().endswith("1 directory, 4 files")
    assert ".hidden" not in text and ".hidden" in out("tree -a", ctx)
    assert out("tree -d", ctx).rstrip().endswith("1 directory")
    assert "inner.txt" not in out("tree -L 1", ctx)
    assert isinstance(run_line("tree", ctx).output, Styled)


def test_touch(ctx, files):
    run_line("touch new.txt", ctx)
    assert (files / "new.txt").read_text() == ""
    os.utime(files / "a.txt", (0, 0))
    run_line("touch a.txt", ctx)
    assert (files / "a.txt").stat().st_mtime > 0
    run_line("touch -c ghost.txt", ctx)
    assert not (files / "ghost.txt").exists()


# ── cut / tr ─────────────────────────────────────────────────────────────────

def test_cut(ctx, files):
    (files / "d.csv").write_text("a,b,c,d\n1,2,3,4\nnodelim\n")
    assert out("cut -d , -f 1,3 d.csv", ctx) == "a,c\n1,3\nnodelim\n"
    assert out("cut -d , -f 3- -s d.csv", ctx) == "c,d\n3,4\n"
    assert out("cut -d , -f -2 --output-delimiter : d.csv", ctx) == "a:b\n1:2\nnodelim\n"
    assert out("cut -c 2-3 a.txt", ctx) == "pp\nan\nhe\n"
    with pytest.raises(PipelineError, match="invalid"):
        run_line("cut -f 0 a.txt", ctx)
    with pytest.raises(PipelineError):
        run_line("cut a.txt", ctx)


def test_tr(ctx):
    assert out("echo hello | tr a-z A-Z", ctx) == "HELLO\n"
    assert out("echo hello | tr [:lower:] [:upper:]", ctx) == "HELLO\n"
    assert out("echo 'a  b   c' | tr -s ' '", ctx) == "a b c\n"
    assert out("echo 'phone: 555-0100' | tr -cd 0-9", ctx) == "5550100"
    assert out("echo banana | tr -d a", ctx) == "bnn\n"
    assert out("echo abc | tr abc x", ctx) == "xxx\n"
    assert out(r"echo -e 'a\tb' | tr '\t' ,", ctx) == "a,b\n"
    with pytest.raises(PipelineError, match="missing SET2"):
        run_line("echo x | tr a", ctx)
    with pytest.raises(PipelineError, match="reverse order"):
        run_line("echo x | tr z-a x", ctx)


# ── which / env ──────────────────────────────────────────────────────────────

def test_which(ctx, monkeypatch, tmp_path):
    assert out("which ls fetch", ctx).splitlines() == [
        "ls: ShellCraft builtin (files)",
        f"fetch: ShellCraft module ({ctx.registry.get('fetch').path})",
    ]
    assert out("which ls nope-xyz", ctx).splitlines()[-1] == "nope-xyz: not found"
    with pytest.raises(PipelineError, match="not found"):
        run_line("which nope-xyz", ctx)

    fake = tmp_path / ("ls.bat" if os.name == "nt" else "ls")
    fake.write_text("")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    everything = out("which -a ls", ctx).splitlines()
    assert everything[0] == "ls: ShellCraft builtin (files)"
    assert "shadowed" in everything[1] and "OS commands are off" in everything[1]


def test_env(ctx, monkeypatch):
    monkeypatch.setenv("SHELLCRAFT_TEST_VAR", "hello")
    assert out("env SHELLCRAFT_TEST_VAR MISSING_VAR_XYZ", ctx) == "SHELLCRAFT_TEST_VAR=hello\n"
    assert "SHELLCRAFT_TEST_VAR=hello" in out("env | grep SHELLCRAFT_TEST", ctx)


def test_env_blocked_when_sensitive_disabled(ctx):
    ctx.allow_sensitive = False
    with pytest.raises(PipelineError, match="not available"):
        run_line("env", ctx)
