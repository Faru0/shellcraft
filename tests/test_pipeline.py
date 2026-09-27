import os
from pathlib import Path

import pytest

from core.context import ShellExit
from core.pipeline import PipelineError, run_line


def test_chaining_modules(ctx, tmp_path):
    (tmp_path / "log.txt").write_text("INFO ok\nERROR boom\nerror small\n")
    result = run_line("fetch log.txt | filter -i error | filter -c boom", ctx)
    assert result.output == "1\n"


def test_redirect_overwrite_and_append(ctx, tmp_path):
    run_line("echo first > out.txt", ctx)
    run_line("echo second > out.txt", ctx)
    assert (tmp_path / "out.txt").read_text() == "second\n"
    result = run_line("echo third >> out.txt", ctx)
    assert (tmp_path / "out.txt").read_text() == "second\nthird\n"
    assert result.output is None and result.redirected_to == (tmp_path / "out.txt").resolve()


def test_failure_stops_pipeline(ctx, tmp_path):
    with pytest.raises(PipelineError) as info:
        run_line("fetch missing.txt | filter x > never.txt", ctx)
    assert (info.value.index, info.value.total, info.value.name) == (1, 2, "fetch")
    assert not (tmp_path / "never.txt").exists()


def test_module_exception_is_wrapped(ctx):
    with pytest.raises(PipelineError) as info:
        run_line("echo x | filter '('", ctx)
    assert info.value.index == 2 and "invalid pattern" in info.value.message


def test_unknown_command_suggests(ctx):
    ctx.allow_system = False
    with pytest.raises(PipelineError) as info:
        run_line("filtr x", ctx)
    assert "did you mean 'filter'" in info.value.message


def test_cd_pwd_and_previous_dir(ctx, tmp_path):
    (tmp_path / "sub").mkdir()
    run_line("cd sub", ctx)
    assert Path(os.getcwd()) == (tmp_path / "sub").resolve()
    assert run_line("pwd", ctx).output.strip() == str((tmp_path / "sub").resolve())
    run_line("cd -", ctx)
    assert Path(os.getcwd()) == tmp_path.resolve()


def test_exit_raises(ctx):
    with pytest.raises(ShellExit) as info:
        run_line("exit 3", ctx)
    assert info.value.code == 3


def test_restricted_context(ctx):
    ctx.allow_redirect = False
    ctx.allow_stateful = False
    with pytest.raises(PipelineError, match="redirection is disabled"):
        run_line("echo x > f.txt", ctx)
    with pytest.raises(PipelineError, match="not available"):
        run_line("cd /", ctx)


def test_renderable_builtin_flattens_into_pipe(ctx):
    out = run_line("help | filter -c fetch", ctx).output
    assert int(out) >= 1


@pytest.mark.skipif(os.name == "nt", reason="uses a POSIX executable")
def test_system_fallback(ctx):
    ctx.allow_system = True
    assert run_line("echo hello | tr a-z A-Z", ctx).output == "HELLO\n"
