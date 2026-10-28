"""`shellcraft func run NAME ARGS` from outside ShellCraft (shellcraft.func_cli, cli.main)."""

import io
import os
import subprocess
import sys

import pytest

from shellcraft import cli, profiles
from shellcraft.config import config_dir

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def _restore(tmp_path, monkeypatch):
    saved, cwd = dict(os.environ), os.getcwd()
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / "home"))
    os.environ.pop(profiles.ENV_VAR, None)
    os.chdir(tmp_path)
    yield
    os.chdir(cwd)
    os.environ.clear()
    os.environ.update(saved)


def func(name, *steps, folder=None):
    where = folder or config_dir() / "funcs"
    where.mkdir(parents=True, exist_ok=True)
    (where / f"{name}.func").write_text(f"# func: {name}\n" + "".join(s + "\n" for s in steps), encoding="utf-8")


def rc(text):
    config_dir().mkdir(parents=True, exist_ok=True)
    (config_dir() / "rc").write_text(text, encoding="utf-8")


def run(capsys, *argv):
    code = cli.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_output_args_and_status(capsys):
    func("greet", 'echo "hi $1 and $2 ($#)"', "echo done")
    code, out, err = run(capsys, "func", "run", "greet", "a", "b c")
    assert (code, out, err) == (0, "hi a and b c (2)\ndone\n", "")


def test_a_failing_step_stops_it_with_its_status(capsys):
    func("fail", "echo one", "nosuchcmd", "echo never")
    code, out, err = run(capsys, "func", "run", "fail")
    assert code == 127
    assert out == "one\n"
    assert "command not found: nosuchcmd" in err and "\x1b" not in err


def test_exit_inside_the_func_and_the_exit_traps(capsys):
    rc('trap "echo session bye" EXIT\n')
    func("quit", 'trap "echo func bye" EXIT', "echo one", "exit 7", "echo never")
    code, out, err = run(capsys, "func", "run", "quit")
    assert code == 7
    assert out == "one\nfunc bye\nsession bye\n"


def test_the_summary_only_with_v_on_stderr(capsys):
    func("ok", "echo one")
    assert run(capsys, "func", "run", "ok")[2] == ""
    code, out, err = run(capsys, "func", "run", "-v", "ok")
    assert (code, out) == (0, "one\n")
    assert err.startswith("✓ ok: 1 step,")


def test_rc_and_startup_load_first_unless_no_startup(capsys):
    rc("NAME=from-rc\n")
    (config_dir() / "startup").mkdir(parents=True, exist_ok=True)
    (config_dir() / "startup" / "10-more.sc").write_text("MORE=from-startup\n", encoding="utf-8")
    func("show", 'echo "[$NAME] [$MORE]"')
    assert run(capsys, "func", "run", "show")[1] == "[from-rc] [from-startup]\n"
    assert run(capsys, "--no-startup", "func", "run", "show")[1] == "[$NAME] [$MORE]\n"  # unset: kept as typed


def test_profile_before_func(capsys):
    profiles.create("work", rc="WHERE=work-rc\n")
    func("where", 'echo "in $WHERE"', folder=profiles.folder("work") / "funcs")
    code, out, err = run(capsys, "--profile", "work", "func", "run", "where")
    assert (code, out) == (0, "in work-rc\n"), err
    code, out, err = run(capsys, "--profile=work", "--no-startup", "func", "run", "where")
    assert (code, out) == (0, "in $WHERE\n"), err
    os.environ.pop(profiles.ENV_VAR, None)  # main() sets it for the programs a func starts
    assert run(capsys, "func", "run", "where")[0] == 127  # not in the default setup


def test_unknown_func_missing_name_and_b(capsys):
    func("ok", "echo one")
    code, out, err = run(capsys, "func", "run", "nope")
    assert (code, out) == (127, "") and "no func 'nope'" in err
    code, out, err = run(capsys, "func", "run")
    assert (code, out) == (2, "") and "usage: shellcraft" in err
    code, out, err = run(capsys, "func")
    assert code == 2 and "usage" in err
    code, out, err = run(capsys, "func", "run", "-b", "ok")
    assert (code, out) == (2, "") and "-b" in err
    code, out, err = run(capsys, "func", "edit", "ok")
    assert code == 2 and "only inside ShellCraft" in err


def test_switches_after_name_go_to_the_func(capsys):
    func("args", 'echo "$@"')
    assert run(capsys, "func", "run", "args", "-v", "x")[1] == "-v x\n"


def test_read_without_a_terminal_fails(capsys, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    func("ask", "read who", "echo never")
    code, out, err = run(capsys, "--no-startup", "func", "run", "ask")
    assert code != 0 and out == "" and "no terminal" in err


def test_func_list_and_show(capsys):
    func("ok", "echo one")
    code, out, _ = run(capsys, "func", "list")
    assert code == 0 and out.startswith("ok")
    code, out, _ = run(capsys, "func", "show", "ok")
    assert code == 0 and "echo one" in out


def test_func_as_an_option_value_is_not_the_command(capsys):
    assert cli._split_func(["--theme", "func"]) is None
    assert cli._split_func(["--prof", "w", "func", "run", "x"]) == (["--prof", "w"], ["run", "x"])
    assert cli._split_func(["func", "run", "--profile", "x"]) == ([], ["run", "--profile", "x"])


def test_from_bash(tmp_path):
    func("greet", 'echo "hi $1 $2"', "exit 3")
    result = subprocess.run([sys.executable, os.path.join(ROOT, "main.py"), "--no-startup", "func", "run",
                             "greet", "a", "b"], capture_output=True, text=True, timeout=60,
                            stdin=subprocess.DEVNULL, env={**os.environ, "NO_COLOR": "1"})
    assert (result.returncode, result.stdout) == (3, "hi a b\n"), result.stderr
