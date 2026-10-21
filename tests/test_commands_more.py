"""The bash switches of the text and file builtins (grep -r/-A/-o, ls -t/-R, find -size, sort -h…) and the small
builtins (printf, seq, xargs, basename, stat, uname, sleep, time, type)."""

import os
import socket
import time

import pytest

from shellcraft.context import to_text
from shellcraft.pipeline import PipelineError, run_line


def out(line, ctx):
    result = run_line(line, ctx)
    return "" if result is None else to_text(result.output)


@pytest.fixture
def tree_dir(tmp_path):
    (tmp_path / "a.txt").write_text("apple\nbanana\ncherry\n")
    (tmp_path / "b.log").write_text("Banana split\nkiwi\n")
    (tmp_path / "big.bin").write_bytes(b"x" * 3000)
    (tmp_path / "empty").write_text("")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "inner.py").write_text("print('hi')\n# banana\n")
    (tmp_path / "sub" / "__pycache__").mkdir()
    (tmp_path / "sub" / "__pycache__" / "x.pyc").write_text("banana")
    return tmp_path


# ── grep ─────────────────────────────────────────────────────────────────────

def test_grep_recursive_context_and_words(ctx, tree_dir):
    text = out("grep -rn banana .", ctx)
    assert "a.txt:2:banana" in text and "sub/inner.py:2:# banana" in text and "pycache" not in text
    assert out("grep -i -C 1 banana a.txt", ctx) == "apple\nbanana\ncherry\n"
    assert out("grep -A 1 apple a.txt", ctx) == "apple\nbanana\n"
    assert out("grep -B 1 cherry a.txt", ctx) == "banana\ncherry\n"
    assert out("grep -n -e apple -e cherry a.txt", ctx) == "1:apple\n3:cherry\n"
    assert out("grep -nA 0 -e apple -e cherry a.txt", ctx) == "1:apple\n--\n3:cherry\n"
    assert out("grep -w an a.txt", ctx) == "" and ctx.last_status == 1
    assert out("grep -x banana a.txt", ctx) == "banana\n"
    assert out("grep -o an a.txt", ctx) == "an\nan\n"
    assert out("grep -L kiwi a.txt b.log", ctx) == "a.txt\n"
    assert out("grep -H apple a.txt", ctx) == "a.txt:apple\n"
    assert out("grep -h apple a.txt b.log", ctx) == "apple\n"
    assert out("grep -E 'ap+le' a.txt", ctx) == "apple\n"


# ── cat / head / tail ────────────────────────────────────────────────────────

def test_cat_flags(ctx):
    assert out("echo -e 'a\\n\\n\\nb' | cat -s", ctx) == "a\n\nb\n"
    assert out("echo -e 'a\\tb' | cat -A", ctx) == "a^Ib$\n"
    assert out("echo x | cat -E", ctx) == "x$\n"
    assert out("echo -e 'a\\tb' | cat -T", ctx) == "a^Ib\n"
    assert out("echo -e 'a\\n\\nb' | cat -ns", ctx) == "     1\ta\n     2\t\n     3\tb\n"


def test_head_and_tail_bytes(ctx):
    assert out("echo abcdef | head -c 3", ctx) == "abc"
    assert out("echo abcdef | tail -c 3", ctx) == "ef\n"
    assert out("echo é | head -c 1", ctx) == ""  # half a character is dropped, not garbled


def test_tail_follow_prints_what_is_appended(ctx, tmp_path):
    """`tail -f` streams: the last lines, then each line appended, handed to the live output as
    it is written, until Ctrl-C (here: interrupt_main from a timer)."""
    import _thread
    import threading
    import time

    path = tmp_path / "grow.log"
    path.write_text("one\n")
    seen = []
    ctx.interactive = True
    ctx.live = lambda item: seen.append(item)

    def writer():
        time.sleep(0.6)
        with open(path, "a") as fh:
            fh.write("two\n")
        time.sleep(0.8)
        _thread.interrupt_main()

    threading.Thread(target=writer, daemon=True).start()
    with pytest.raises(KeyboardInterrupt):
        run_line("tail -f grow.log", ctx)
    assert "".join(seen) == "one\ntwo\n" and ctx.last_status == 130
    ctx.interactive, ctx.live = False, None
    with pytest.raises(PipelineError, match="interactive"):
        run_line("tail -f grow.log", ctx)


# ── sort ─────────────────────────────────────────────────────────────────────

def test_sort_flags(ctx):
    assert out("echo -e '1K\\n2M\\n3\\n500' | sort -h", ctx) == "3\n500\n1K\n2M\n"
    assert out("echo -e 'v1.10\\nv1.2\\nv1.9' | sort -V", ctx) == "v1.2\nv1.9\nv1.10\n"
    assert out("echo -e 'b 1\\na 1\\nc 0' | sort -s -k 2", ctx) == "c 0\nb 1\na 1\n"
    assert out("echo -e 'x 2 b\\nx 1 a\\ny 1 a' | sort -k 2,3", ctx) == "x 1 a\ny 1 a\nx 2 b\n"
    with pytest.raises(PipelineError, match="-k must be"):
        run_line("echo x | sort -k 3,1", ctx)


# ── ls / find / tree ─────────────────────────────────────────────────────────

def test_ls_sorting_recursion_and_directories(ctx, tree_dir):
    os.utime(tree_dir / "a.txt", (time.time() + 100, time.time() + 100))
    assert out("ls -t", ctx).splitlines()[0] == "a.txt"
    assert out("ls -S", ctx).splitlines()[0] == "big.bin"
    assert out("ls -Sr", ctx).splitlines()[-1] == "big.bin"
    assert out("ls -d sub", ctx) == "sub/\n"
    text = out("ls -R", ctx)
    assert ".:" in text and "sub:" in text and "inner.py" in text
    with pytest.raises(PipelineError, match="usage: ls"):
        run_line("ls -z", ctx)


def test_find_tests(ctx, tree_dir):
    assert out("find . -size +1k", ctx) == "./big.bin\n"
    assert out("find . -size -3c -type f", ctx) == "./empty\n"
    assert out("find . -empty -type f", ctx) == "./empty\n"
    assert out("find . -name '*.txt' -not -name 'a*'", ctx) == ""
    assert out("find . -type f ! -name '*.py*' -path '*sub*'", ctx) == ""
    assert out("find . -mtime -1 -name 'b.*'", ctx) == "./b.log\n"
    os.utime(tree_dir / "b.log", (time.time() + 50, time.time() + 50))
    os.utime(tree_dir / "a.txt", (time.time() + 100, time.time() + 100))
    assert out("find . -newer b.log -type f", ctx) == "./a.txt\n"
    assert out("find . -ipath '*SUB/INNER*'", ctx) == "./sub/inner.py\n"
    with pytest.raises(PipelineError, match="unknown test"):
        run_line("find . -bogus", ctx)
    with pytest.raises(PipelineError, match="needs a value"):
        run_line("find . -name", ctx)


def test_tree_ignore_and_sizes(ctx, tree_dir):
    text = out("tree -I '__pycache__|*.bin'", ctx)
    assert "pycache" not in text and "big.bin" not in text and "inner.py" in text
    text = out("tree --du -L 1", ctx)
    assert "[  2.9K]  big.bin" in text and "used in" in text


# ── new builtins ─────────────────────────────────────────────────────────────

def test_printf(ctx):
    assert out("printf '%-5s|%03d\\n' ab 7 cd 42", ctx) == "ab   |007\ncd   |042\n"
    assert out("printf '%s\\n' a b", ctx) == "a\nb\n"
    assert out("printf '%5.2f %x %c %%\\n' 3.14159 255 hello", ctx) == " 3.14 ff h %\n"
    assert out("printf 'no newline'", ctx) == "no newline"
    assert out("printf '%q\\n' 'a b'", ctx) == "'a b'\n"
    with pytest.raises(PipelineError, match="expected a number"):
        run_line("printf '%d' x", ctx)


def test_seq(ctx):
    assert out("seq 3", ctx) == "1\n2\n3\n"
    assert out("seq 0 5 20", ctx) == "0\n5\n10\n15\n20\n"
    assert out("seq -w 8 10", ctx) == "08\n09\n10\n"
    assert out("seq -s , 1 3", ctx) == "1,2,3\n"
    assert out("seq 3 1", ctx) == ""
    assert out("seq 1 0.5 2", ctx) == "1.0\n1.5\n2.0\n"
    assert out("for n in $(seq 1 3); do echo n$n; done", ctx) == "n1\nn2\nn3\n"


def test_xargs(ctx, tmp_path):
    assert out("seq 3 | xargs echo n:", ctx) == "n: 1 2 3\n"
    assert out("seq 3 | xargs -n 1 echo n:", ctx) == "n: 1\nn: 2\nn: 3\n"
    assert out("echo -e 'a b\\nc' | xargs -I {} echo '<{}>'", ctx) == "<a b>\n<c>\n"
    assert out("echo -e 'a b\\nc d' | xargs -L 1 echo", ctx) == "a b\nc d\n"
    assert out("echo | xargs -r echo never", ctx) == ""
    assert out("echo x | xargs -t echo", ctx) == "+ echo x\nx\n"
    (tmp_path / "f1").write_text("")
    out("echo f1 | xargs rm", ctx)
    assert not (tmp_path / "f1").exists()
    assert "no such file" in out("echo nosuch | xargs cat", ctx) and ctx.last_status == 123


def test_path_helpers(ctx, tmp_path):
    assert out("basename /a/b/c.txt", ctx) == "c.txt\n"
    assert out("basename /a/b/c.txt .txt", ctx) == "c\n"
    assert out("basename -s .py shellcraft/cli.py shellcraft/shell.py", ctx) == "cli\nshell\n"
    assert out("dirname /a/b/c.txt a.txt /x", ctx) == "/a/b\n.\n/\n"
    assert out("realpath .", ctx).strip() == str(tmp_path.resolve())
    (tmp_path / "d").mkdir()
    assert out("realpath --relative-to . d", ctx) == "d\n"
    with pytest.raises(PipelineError, match="no such file"):
        run_line("realpath -e nope", ctx)


def test_stat(ctx, tmp_path):
    (tmp_path / "f").write_text("abc")
    assert out("stat -c '%n %s %F' f", ctx) == "f 3 regular file\n"
    text = out("stat f", ctx)
    assert "Size: 3" in text and "regular file" in text and "Modify:" in text
    with pytest.raises(PipelineError, match="(?i)no such"):
        run_line("stat nope", ctx)


def test_identity_commands(ctx):
    assert out("whoami", ctx).strip()
    assert out("hostname", ctx).strip() == socket.gethostname()
    assert out("uname", ctx).strip() == out("uname -s", ctx).strip()
    assert len(out("uname -a", ctx).split()) >= 4


def test_sleep_and_time(ctx):
    started = time.monotonic()
    assert out("sleep 0.05", ctx) == ""
    assert time.monotonic() - started >= 0.04
    with pytest.raises(PipelineError, match="invalid time"):
        run_line("sleep abc", ctx)
    text = out("time echo hi", ctx)
    assert text.startswith("hi\nreal ") and text.endswith("s\n")
    text = out("time cat nosuch", ctx)
    assert "✗ cat: nosuch: no such file" in text and ctx.last_status == 1


def test_type(ctx):
    assert out("type ls", ctx) == "ls is a shell builtin\n"
    assert out("type -t for", ctx) == "keyword\n"
    assert out("type fetch", ctx).startswith("fetch is a ShellCraft module")
    assert out("type nosuchcmd", ctx) == "nosuchcmd: not found\n" and ctx.last_status == 1
    ctx.config["aliases"] = {"ll": "ls -l"}
    assert out("type ll", ctx) == "ll is aliased to `ls -l'\n"
