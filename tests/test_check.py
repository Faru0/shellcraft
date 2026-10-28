"""Checking a script without running it: `source -n FILE` and `func check NAME` (shellcraft.check)."""

import pytest

from shellcraft import check
from shellcraft.context import to_text
from shellcraft.pipeline import run_line


def run(ctx, line):
    result = run_line(line, ctx)
    return ("" if result is None else to_text(result.output)), ctx.last_status


def report(ctx, text):
    return check.check_text(text, "f.sc", ctx)


def test_a_clean_file(ctx, tmp_path):
    (tmp_path / "ok.sc").write_text("# setup\ngreet() {\n    echo hi $1\n}\ngreet you\nx=1\n$x arg\n")
    assert run(ctx, f"source -n {tmp_path / 'ok.sc'}") == ("ok: 7 lines, no problems\n", 0)


def test_nothing_runs(ctx, tmp_path):
    (tmp_path / "s.sc").write_text("x=5\nf() { echo f; }\necho $(touch made) > out.txt\nexit\n")
    text, status = run(ctx, f"source -n {tmp_path / 's.sc'}")
    assert status == 0 and text.startswith("ok:")
    assert "x" not in ctx.variables and "f" not in ctx.functions
    assert not (tmp_path / "made").exists() and not (tmp_path / "out.txt").exists()


def test_every_problem_with_its_line(ctx, tmp_path):
    (tmp_path / "bad.sc").write_text(
        "ecoh start\n"                       # 1: typo
        "for -py n in 1..3:\n"
        "        echo $n\n"
        "    echo bad\n"                     # 4: unindent to no level
        "def f(a b):\n"                      # 5: bad parameters
        "    echo\n"
        "if -py (x ==) { echo }\n"           # 7: bad Python condition
        "for f in a b; do\n"                 # 8: never closes
        "    echo $(grpe x)\n"               # 9: typo inside $( )
    )
    text, status = run(ctx, f"source -n {tmp_path / 'bad.sc'}")
    assert status == 2
    assert text.splitlines() == [
        "bad.sc:1: warning: unknown command 'ecoh' (did you mean 'echo'?)",
        "bad.sc:4: unindent does not match any outer indentation level",
        "bad.sc:5: def f: invalid parameters (invalid syntax): (a b)",
        "bad.sc:7: if: invalid condition (invalid syntax): x ==",
        "bad.sc:8: missing 'done': the block that starts here never ends",
        "bad.sc:9: warning: unknown command 'grpe' (did you mean 'grep'?)",
        "6 problems (4 errors, 2 warnings)",
    ]


def test_warnings_only_is_status_1(ctx):
    r = report(ctx, "if true; then\n  echo a\n  qqqzzz b\nfi\n")
    assert r.status == 1 and r.text().splitlines() == ["f.sc:3: warning: unknown command 'qqqzzz'",
                                                       "1 problem (1 warning)"]


@pytest.mark.parametrize("text", [
    "f() { g; }\ng() { echo g; }\nf\n",                  # a body may call a function defined later
    "def hello(name):\n    echo $name\nhello you\n",
    "alias ll='ls -l'\nll\n",
    "\\echo bypass\n",
    "name = input(\"name? \")\n",
    "case $x in a) echo a;; *) true;; esac\n",
    "while read line; do echo $line; done < f | sort\n",
])
def test_known_commands(ctx, text):
    assert report(ctx, text).problems == []


def test_resolved_like_the_shell(ctx, monkeypatch):
    ctx.functions["mine"] = object()
    assert report(ctx, "mine\nqueryDns x\n").problems == []  # a function in the shell, a module
    r = report(ctx, "later\nlater() { echo; }\n")
    assert [p.message for p in r.problems] == ["later is called before the file defines it"]
    monkeypatch.setattr("shellcraft.pipeline.shutil.which", lambda name: f"/usr/bin/{name}" if name == "zzprog" else None)
    ctx.allow_system = True
    assert report(ctx, "zzprog\n").problems == []
    ctx.allow_system = False
    assert "OS commands are off" in report(ctx, "zzprog\n").problems[0].message


def test_func_check_uses_the_file_lines(ctx, tmp_path):
    folder = tmp_path / ".home" / "funcs"
    folder.mkdir(parents=True)
    (folder / "recon.func").write_text("# func: recon\n# description: d\n# system: no\n# output: show\n"
                                     "# command: no\nqueryDns $1\n@ ecoh done\nfor x in a; do\n  echo\n")
    text, status = run(ctx, "func check recon")
    assert status == 2
    assert text.splitlines() == [
        "recon:7: warning: unknown command 'ecoh' (did you mean 'echo'?)",
        "recon:8: missing 'done': the block that starts here never ends",
        "2 problems (1 error, 1 warning)",
    ]
    run(ctx, "func new clean 'echo hi'")
    assert run(ctx, "func check clean")[1] == 0


def test_source_n_errors(ctx, tmp_path):
    from shellcraft.pipeline import PipelineError

    with pytest.raises(PipelineError, match="no such file"):
        run_line(f"source -n {tmp_path / 'missing.sc'}", ctx)
    with pytest.raises(PipelineError, match="usage"):
        run_line("source -n", ctx)
