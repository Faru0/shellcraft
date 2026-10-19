"""The footer's state colors (running / done / failed / recording): what makes a line change color.

Windows reported that they never change while Linux does. Three things decide it: the state of the
job (a ConPTY program that exits quietly must still end its job), the styles each theme gives the
states, and the redraw when a job ends. Linux can't run ConPTY, so the Windows reader is driven
here with a fake pywinpty process that behaves the way pywinpty's source says it does: a blocking
read() that gets no EOF when the program exits.
"""

import io
import threading
import time

import pytest
from rich.color import Color
from rich.console import Console

from shellcraft import jobs as jobmod
from shellcraft.context import ShellContext, to_text
from shellcraft.diagnostics import FOOTER_STATES, console_report
from shellcraft.footer import Footer, panel_lines
from shellcraft.jobs import JobTable
from shellcraft.loader import ModuleRegistry
from shellcraft.pipeline import PipelineError, run_line
from shellcraft.themes import UI, all_themes


def _wait(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


class FakePty:
    """pywinpty's PtyProcess as its source behaves: read() is a blocking recv that returns the
    output and then blocks until the socket is closed (a ConPTY pipe stays open after the program
    exits); isalive() asks the process; exitstatus is None for a moment after it ends."""

    def __init__(self, output="", status=0, late=0, eof=False):
        self.output, self.status, self.late, self.eof = output, status, late, eof
        self.dead = threading.Event()
        self.closed = threading.Event()
        self.sent = False
        self.asked = 0
        self.fileobj = self._Socket(self.closed)
        self._server = self._Socket(self.closed)

    class _Socket:
        def __init__(self, closed):
            self.closed = closed

        def close(self):
            self.closed.set()

    def read(self, size=1024):
        if self.output and not self.sent:
            self.sent = True
            return self.output
        if self.eof and self.dead.is_set():
            raise EOFError("Pty is closed")
        while not self.closed.is_set():  # no EOF: blocks, as a ConPTY pipe does
            if self.eof and self.dead.is_set():
                raise EOFError("Pty is closed")
            time.sleep(0.005)
        raise OSError("the socket was closed")

    def isalive(self):
        return not self.dead.is_set()

    @property
    def exitstatus(self):
        if not self.dead.is_set():
            return None
        self.asked += 1
        return None if self.asked <= self.late else self.status

    def write(self, text):
        if self.dead.is_set():
            raise EOFError("Pty is closed")


@pytest.fixture
def fast_winpty(monkeypatch):
    monkeypatch.setattr(jobmod, "WINPTY_POLL", 0.01)
    monkeypatch.setattr(jobmod, "WINPTY_DRAIN", 0.05)


def _program(tmp_path, fake, command="pwsh"):
    table = JobTable(tmp_path / "jobs")
    job = table._add(command, "program")
    job.winpty = fake
    job.screen, job.stream = jobmod.new_screen(24, 80)
    seen = {"changes": 0, "done": []}
    table.on_change = lambda: seen.__setitem__("changes", seen["changes"] + 1)
    table.on_done.append(lambda j: seen["done"].append(j.number))
    reader = threading.Thread(target=table._read_program, args=(job,), daemon=True)
    reader.start()
    return table, job, reader, seen


def test_a_conpty_program_that_exits_quietly_still_ends_its_job(tmp_path, fast_winpty):
    """`exit` in pwsh prints nothing more: the blocked read never sees an EOF, so the job used to
    stay `running` for good and its footer line never turned green or red."""
    fake = FakePty(output="PS> exit\r\n", status=3)
    table, job, reader, seen = _program(tmp_path, fake)
    assert _wait(lambda: "PS> exit" in job.output) and job.running  # alive: still running
    fake.dead.set()
    assert _wait(lambda: not job.running, 3)
    assert job.status == 3 and job.label == "exit 3" and "PS> exit" in job.output
    assert table.pop_notifications() == ["[1] exit 3: pwsh"]  # one end, one note
    assert seen["done"] == [1] and seen["changes"] >= 1  # the hook ran once, the footer was told
    assert fake.closed.is_set()  # the blocked reader was released
    reader.join(2)
    assert not reader.is_alive()


def test_a_conpty_program_that_ends_with_an_eof_ends_once(tmp_path, fast_winpty):
    fake = FakePty(output="bye\r\n", status=0, eof=True)
    table, job, reader, seen = _program(tmp_path, fake)
    assert _wait(lambda: "bye" in job.output)
    fake.dead.set()
    assert _wait(lambda: not job.running, 3)
    assert job.status == 0 and job.label == "done" and seen["done"] == [1]
    assert table.pop_notifications() == ["[1] done: pwsh"]
    reader.join(2)
    assert not reader.is_alive()


def test_a_late_exit_status_is_waited_for_not_turned_into_success(tmp_path, fast_winpty, monkeypatch):
    """pywinpty answers None for a moment after isalive() is False; `None or 0` made a failing
    program look like a success (green `done` instead of red `exit 7`)."""
    fake = FakePty(status=7, late=3)
    table, job, reader, _ = _program(tmp_path, fake)
    fake.dead.set()
    assert _wait(lambda: not job.running, 3)
    assert job.status == 7 and job.label == "exit 7"
    monkeypatch.setattr(jobmod, "WINPTY_STATUS_TRIES", 3)
    assert jobmod._winpty_status(FakePty(status=5, late=99)) == 0  # never answered: 0, after a bound
    quick = FakePty(status=5)
    quick.dead.set()
    assert jobmod._winpty_status(quick) == 5
    reader.join(2)


def test_a_job_end_is_reported_once_whoever_notices_first(tmp_path):
    table = JobTable(tmp_path / "jobs")
    job = table._add("seq", "program")
    ended = []
    table.on_done.append(lambda j: ended.append(j.status))
    table._done(job, 1)
    table._done(job, 0)  # a second signal that it ended: ignored
    assert job.status == 1 and ended == [1] and table.pop_notifications() == ["[1] exit 1: seq"]
    assert job._finish(2) is False


def test_writing_to_a_program_that_just_ended_is_an_oserror(tmp_path):
    fake = FakePty()
    table = JobTable(tmp_path / "jobs")
    job = table._add("pwsh", "program")
    job.winpty = fake
    assert jobmod._send(job, b"x") is True
    fake.dead.set()
    with pytest.raises(OSError, match="pseudo-terminal is closed"):
        job.write(b"x")
    assert jobmod._send(job, b"x") is False  # the attach loop notices the end on its next turn


# ── the footer's colors, in every theme ──────────────────────────────────────

class StateJob:
    """What panel_lines reads from a job."""

    def __init__(self, number, command, running=True, status=0, killed=False, recording=False):
        self.number, self.command, self.running, self.status = number, command, running, status
        self.killed, self.recording, self.name, self.kind = killed, recording, None, "program"
        self.label = "running" if running else "killed" if killed else "done" if status == 0 else f"exit {status}"
        self.pid, self.reported = number, False

    def short(self, width=18):
        return self.command[:width]


def _rgb(color: str) -> str:
    r, g, b = Color.parse(color).get_truecolor()
    return f"38;2;{r};{g};{b}"


@pytest.mark.parametrize("theme_name", list(all_themes({})))
def test_every_theme_colors_running_done_failed_and_recording_differently(theme_name):
    theme = all_themes({})[theme_name]
    console = Console(file=io.StringIO(), width=100, force_terminal=True, color_system="truecolor",
                      legacy_windows=False)
    ui = UI(theme, console)
    jobs = [StateJob(1, "running one"), StateJob(2, "done one", running=False),
            StateJob(3, "failed one", running=False, status=2),
            StateJob(4, "killed one", running=False, status=0, killed=True)]
    footer = Footer(ui, jobs)
    footer.size = lambda: (30, 100)
    rows = footer.render(100)
    main, running, done, failed, killed = rows
    assert [r.style for r in footer.rows] == ["current", "running", "done", "failed", "failed"]
    assert _rgb(theme.success) in done and _rgb(theme.success) not in running
    assert _rgb(theme.error) in failed and _rgb(theme.error) in killed and _rgb(theme.error) not in done
    assert running != done != failed and len({running[:60], done[:60], failed[:60]}) == 3
    # the recording bubble (a span over the first cells) is the theme's error color on main and on a subshell
    footer.is_recording = lambda: True
    jobs[0].recording = True
    rec_main, rec_running = footer.render(100)[0], footer.render(100)[1]
    assert _rgb(theme.error) in rec_main.split("main")[0] and _rgb(theme.error) in rec_running.split("running")[0]
    assert _rgb(theme.error) not in main.split("main")[0]


def test_the_prompt_panel_uses_the_same_states():
    jobs = [StateJob(1, "a"), StateJob(2, "b", running=False), StateJob(3, "c", running=False, status=1)]
    assert [r.style for r in panel_lines(jobs, 0, width=60, stack=True)] == ["current", "running", "done", "failed"]


# ── the redraw when a job ends, in a real shell ──────────────────────────────

@pytest.fixture
def shell_ctx(monkeypatch, tmp_path):
    from shellcraft import shell

    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(shell, "create_prompt_toolkit_console", lambda cleanup: None)
    console = Console(file=io.StringIO(), width=100, force_terminal=True, color_system="truecolor",
                      legacy_windows=False)
    ctx = ShellContext(registry=ModuleRegistry(tmp_path), ui=UI(all_themes({})["nord"], console), config={})
    with shell.Shell(ctx) as sh:
        yield sh, ctx


def test_the_footer_changes_color_when_a_job_ends(shell_ctx):
    sh, ctx = shell_ctx
    theme = sh.ui.theme
    sh.footer.size = lambda: (30, 100)
    sh.footer.enabled = True
    gate = threading.Event()
    job = ctx.jobs.start_command("waits", lambda: (gate.wait(5), (0, "ok\n"))[1])
    sh.footer.dirty = False
    before = sh.footer.sequence()
    assert _rgb(theme.success) not in before and _rgb(theme.error) not in before  # running: plain
    gate.set()
    assert _wait(lambda: not job.running)
    assert sh.footer.dirty  # the job's end told the footer (Shell._jobs_changed)
    after = sh.footer.sequence()
    assert _rgb(theme.success) in after and "done" in after
    failing = ctx.jobs.start_command("fails", lambda: (2, ""))
    assert _wait(lambda: not failing.running)
    assert _rgb(theme.error) in sh.footer.sequence()


# ── the diagnostic ───────────────────────────────────────────────────────────

def test_the_report_shows_the_footer_colors_and_the_jobs(tmp_path):
    ui = UI(all_themes({})["nord"], Console(file=io.StringIO(), width=100, force_terminal=True,
                                            color_system="truecolor", legacy_windows=False))
    table = JobTable(tmp_path / "jobs")
    fake = FakePty(status=3)
    job = table._add("pwsh", "program")
    job.winpty = fake
    footer = Footer(ui, table)
    footer.size = lambda: (30, 100)
    footer.sequence()
    report = console_report(ui, jobs=table, footer=footer)
    assert "[footer colors]" in report and "running / done / failed look different: yes" in report
    for name in FOOTER_STATES:
        assert f"sc.footer.{name}" in report
    assert "ESC[38;2;" in report and "MISSING" not in report
    assert "[1] program running=True status=None label='running'" in report
    assert "winpty alive=True exitstatus=None" in report and "rows as last drawn" in report
    fake.dead.set()
    table._done(job, 3)
    assert "label='exit 3'" in console_report(ui, jobs=table, footer=footer)
    bare = UI(all_themes({})["nord"], Console(file=io.StringIO(), force_terminal=True, color_system="truecolor"))
    bare.console.pop_theme()  # a console without the theme: every footer line would lose its color
    assert "MISSING" in console_report(bare) and "NO (same colors" in console_report(bare)
    plain = UI(all_themes({})["nord"], Console(file=io.StringIO(), force_terminal=True, color_system=None))
    assert "no escape sequence" in console_report(plain)


def test_diag_prints_the_report_in_the_shell(shell_ctx):
    sh, ctx = shell_ctx
    ctx.interactive = True
    out = to_text(run_line("diag", ctx).output)
    assert "[footer colors]" in out and "sc.footer.done" in out and "0 jobs" in out
    ctx.jobs.start_command("seq 1", lambda: (0, "1\n"))
    assert "[1] command" in to_text(run_line("diag", ctx).output)
    with pytest.raises(PipelineError, match="usage: diag"):
        run_line("diag x", ctx)
    ctx.console_report = None
    with pytest.raises(PipelineError, match="only in the interactive shell"):
        run_line("diag", ctx)


def test_the_recording_bubble_is_bright_red_on_windows():
    from shellcraft.themes import WINDOWS_REC_RED, rec_color

    assert rec_color("#bf616a", "win32") == WINDOWS_REC_RED
    assert rec_color("#bf616a", "linux") == "#bf616a"  # elsewhere the theme's own red, as before
