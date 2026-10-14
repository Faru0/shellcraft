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


# ── following a background command ───────────────────────────────────────────

SLOW = ("import time\n"
        "def run(args, stdin):\n"
        "    for i in range(int(args[0]) if args else 6):\n"
        "        yield f'tick{i}'\n"
        "        time.sleep(0.25)\n")


def _with_slow_module(ictx, tmp_path):
    (tmp_path / "modules").mkdir(exist_ok=True)
    (tmp_path / "modules" / "slow.py").write_text(SLOW)
    ictx.registry = ModuleRegistry(tmp_path / "modules")
    ictx.registry.load()


def test_a_background_command_records_its_output_as_it_streams(ictx, tmp_path):
    _with_slow_module(ictx, tmp_path)
    run_line("sub slow 8", ictx)
    job = ictx.jobs.get(1)
    assert _wait(lambda: "tick2" in job.output, 4)
    assert job.running and "tick7" not in job.output  # part of it, while it still runs
    assert _wait(lambda: not job.running, 5) and job.output.count("tick") == 8


def test_jobs_follow_prints_until_the_job_ends(ictx, tmp_path):
    _with_slow_module(ictx, tmp_path)
    run_line("slow 5 &", ictx)
    result = run_line("jobs -f 1", ictx)
    printed = ictx.ui.console.file.getvalue()
    assert "following" in printed and all(f"tick{i}" in printed for i in range(5))
    assert "done" in to_text(result.output) and not ictx.jobs.get(1).running
    finished = to_text(run_line("jobs 1", ictx).output)  # a finished job: a snapshot
    assert "tick4" in finished and "following" not in finished


def test_ctrl_c_stops_following_and_the_job_keeps_running(ictx, tmp_path):
    import _thread
    import threading

    _with_slow_module(ictx, tmp_path)
    run_line("sub slow 40", ictx)
    threading.Timer(0.8, _thread.interrupt_main).start()
    result = run_line("jobs 1", ictx)  # running: selecting it follows it
    assert "still running" in to_text(result.output)
    job = ictx.jobs.get(1)
    assert job.running and "tick1" in ictx.ui.console.file.getvalue()
    ictx.jobs.kill(job)


def test_sub_tail_f_into_grep_records_lines_as_written(ictx, tmp_path):
    log = tmp_path / "app.log"
    log.write_text("start\n")
    run_line("tail -f app.log | grep ERROR &", ictx)
    job = ictx.jobs.get(1)
    time.sleep(0.6)
    with open(log, "a") as fh:
        fh.write("ok\nERROR first\n")
    assert _wait(lambda: "ERROR first" in job.output, 4) and job.running
    ictx.jobs.kill(job)
