"""`cmd &`: a trailing & runs the line in the background, like `sub cmd`; elsewhere & is an error.
And `jobs -f N`: follow a background command's output as it grows."""

import io
import sys
import time

import pytest
from rich.console import Console

from shellcraft.context import ShellContext, to_text
from shellcraft.jobs import JobTable
from shellcraft.loader import ModuleRegistry
from shellcraft.parser import ParseError, split_background
from shellcraft.pipeline import PipelineError, run_line
from shellcraft.themes import UI, all_themes


def _wait(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def ictx(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("SHELLCRAFT_DEPTH", raising=False)
    monkeypatch.chdir(tmp_path)
    ctx = ShellContext(registry=ModuleRegistry(tmp_path / "modules"))
    ctx.interactive = True
    ctx.ui = UI(all_themes({})["nord"], Console(file=io.StringIO()))
    ctx.jobs = JobTable(tmp_path / "home" / "jobs")
    return ctx


def test_split_background():
    assert split_background("seq 3 &") == "seq 3"
    assert split_background("grep 'a b' f.txt   &  ") == "grep 'a b' f.txt"
    assert split_background("echo 'x &'") is None  # quoted
    assert split_background("a && b") is None and split_background("ls &> out") is None
    assert split_background("fetch https://x/?a=1&b=2") is None  # inside a word
    assert split_background("&") is None and split_background("seq 3&") is None  # needs a space before


def test_trailing_ampersand_runs_a_command_job(ictx):
    result = run_line("seq 3 &", ictx)
    assert "[1]" in to_text(result.output) and "background" in to_text(result.output)
    assert ictx.last_status == 0
    job = ictx.jobs.get(1)
    assert job.kind == "command" and job.command == "seq 3"
    assert _wait(lambda: not job.running) and job.output == "1\n2\n3\n"
    run_line("echo 'a  b' | cat &", ictx)  # quotes and pipes kept: the line as typed
    job2 = ictx.jobs.get(2)
    assert _wait(lambda: not job2.running) and job2.output == "a  b\n"


@pytest.mark.skipif(sys.platform == "win32", reason="pseudo-terminals are POSIX only")
def test_trailing_ampersand_starts_a_program_without_attaching(ictx, monkeypatch):
    attached = []
    monkeypatch.setattr(JobTable, "attach", lambda self, job, **kw: attached.append(job) or ("detached", 0))
    out = to_text(run_line("sh -c 'sleep 5' &", ictx).output)
    job = ictx.jobs.get(1)
    assert job.kind == "program" and job.running and attached == [] and "Ctrl-] 1" in out
    ictx.jobs.kill(job)
    with pytest.raises(PipelineError, match="not a command"):
        run_line("no-such-program-xyz &", ictx)


def test_ampersand_elsewhere_and_outside_the_shell(ictx):
    with pytest.raises(ParseError, match="only a trailing &"):
        run_line("echo hi & echo there", ictx)
    ictx.interactive = False
    with pytest.raises(PipelineError, match="interactive shell"):
        run_line("seq 3 &", ictx)
    ictx.interactive, ictx.jobs = True, None  # a background job's own context has no job table
    with pytest.raises(PipelineError, match="interactive shell"):
        run_line("seq 3 &", ictx)
