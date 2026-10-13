"""Subshells: `sub`, `jobs`, the job table, attach / detach on a pseudo-terminal, the bar."""

import io
import os
import sys
import threading
import time

import pytest
from rich.console import Console

from core import jobs as jobmod
from core.context import ShellContext, to_text
from core.jobs import JobTable
from core.loader import ModuleRegistry
from core.pipeline import PipelineError, run_line
from core.themes import UI, all_themes

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="pseudo-terminals are POSIX only")


def _wait(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def ictx(tmp_path, monkeypatch):
    """An interactive context with its own job table and modules folder."""
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    ctx = ShellContext(registry=ModuleRegistry(tmp_path / "modules"))
    ctx.interactive = True
    ctx.ui = UI(all_themes({})["nord"], Console(file=io.StringIO()))
    ctx.jobs = JobTable(tmp_path / "home" / "jobs")
    return ctx


# ── the table ────────────────────────────────────────────────────────────────

@posix_only
def test_program_job_runs_on_a_pty_and_logs_its_output(tmp_path):
    table = JobTable(tmp_path / "jobs")
    changes = []
    table.on_change = lambda: changes.append(1)
    job = table.start_program(["sh", "-c", "printf hello; exit 3"], "sh -c …")
    assert job.kind == "program" and job.pid and job.running
    assert _wait(lambda: not job.running)
    assert job.status == 3 and job.label == "exit 3" and "hello" in job.output
    assert (tmp_path / "jobs" / "1.log").read_bytes() == b"hello"
    assert table.pop_notifications() == ["[1] exit 3: sh -c …"] and table.pop_notifications() == []
    assert len(changes) >= 2  # started, finished
    with pytest.raises(FileNotFoundError):
        table.start_program(["no-such-program-xyz"], "nope")


@posix_only
def test_attach_forwards_keys_and_detaches_on_ctrl_bracket(tmp_path):
    table = JobTable(tmp_path / "jobs")
    job = table.start_program(["cat"], "cat")  # echoes what it is sent
    import tty

    term_master, term_slave = os.openpty()  # a fake terminal for the test
    tty.setraw(term_slave)  # as the real one is while attached: keys arrive at once, unbuffered
    result = {}

    def attach():
        result["how"] = table.attach(job, stdin_fd=term_slave, stdout_fd=term_slave, raw=False, replay=0)

    thread = threading.Thread(target=attach, daemon=True)
    thread.start()
    assert _wait(lambda: job.sink == term_slave)
    os.write(term_master, b"ping\n")
    assert _wait(lambda: b"ping" in job.tail())  # reached cat, came back through the pty
    seen = b""
    end = time.time() + 3
    while b"ping" not in seen and time.time() < end:
        seen += os.read(term_master, 4096)
    assert b"ping" in seen  # and was copied to the terminal while attached
    os.write(term_master, b"x" + jobmod.DETACH_KEY)
    thread.join(3)
    assert result["how"] == ("detached", 0) and job.running and job.sink is None
    # Ctrl-] then a digit: switch to that tab
    thread = threading.Thread(target=attach, daemon=True)
    thread.start()
    assert _wait(lambda: job.sink == term_slave)
    os.write(term_master, jobmod.DETACH_KEY + b"2")
    thread.join(3)
    assert result["how"] == ("switch", 2) and job.running
    assert table.kill(job) and _wait(lambda: not job.running) and job.label == "killed"
    assert table.attach(job, stdin_fd=term_slave, stdout_fd=term_slave, raw=False) == ("exited", 129)  # 128 + SIGHUP
    os.close(term_master)
    os.close(term_slave)


def test_command_job_collects_output_and_status(tmp_path):
    table = JobTable(tmp_path / "jobs")
    job = table.start_command("fake", lambda: (0, "a\nb\n"))
    assert job.kind == "command" and _wait(lambda: not job.running)
    assert job.output == "a\nb\n" and job.label == "done"
    failing = table.start_command("boom", lambda: (_ for _ in ()).throw(RuntimeError("bad")))
    assert _wait(lambda: not failing.running)
    assert failing.status == 1 and "RuntimeError: bad" in failing.output
    assert [j.number for j in table] == [1, 2] and table.clear_finished() == 2 and len(table) == 0


@posix_only
def test_cleanup_stops_running_programs(tmp_path):
    table = JobTable(tmp_path / "jobs")
    job = table.start_program(["sleep", "30"], "sleep 30")
    stopped = table.cleanup()
    assert stopped == [job] and _wait(lambda: not job.running) and job.killed


# ── the builtins ─────────────────────────────────────────────────────────────

def test_sub_runs_a_shell_command_in_the_background(ictx):
    result = run_line("sub seq 3", ictx)
    assert "[1]" in to_text(result.output) and "background" in to_text(result.output)
    job = ictx.jobs.get(1)
    assert job.kind == "command" and _wait(lambda: not job.running)
    assert job.output == "1\n2\n3\n" and job.status == 0
    shown = to_text(run_line("jobs 1", ictx).output)
    assert "seq 3" in shown and "1\n2\n3" in shown and "done" in shown
    listed = to_text(run_line("jobs", ictx).output)
    assert listed.startswith("1\tdone\t") and "command\tseq 3" in listed
    # a background command cannot change the shell's state
    run_line("sub cd ..", ictx)
    job2 = ictx.jobs.get(2)
    assert _wait(lambda: not job2.running) and job2.status != 0 and "cd" in job2.output
    assert to_text(run_line("jobs -c", ictx).output).startswith("✓ 2 finished")
    assert "no subshells" in to_text(run_line("jobs", ictx).output)


def test_sub_and_jobs_refuse_outside_the_interactive_shell(ictx):
    ictx.interactive = False
    with pytest.raises(PipelineError, match="interactive"):
        run_line("sub seq 3", ictx)
    with pytest.raises(PipelineError, match="interactive"):
        run_line("jobs", ictx)
    ictx.interactive = True
    with pytest.raises(PipelineError, match="not a command, a module or a program"):
        run_line("sub no-such-program-xyz", ictx)
    with pytest.raises(PipelineError, match="no job 9"):
        run_line("jobs 9", ictx)
    with pytest.raises(PipelineError, match="job number"):
        run_line("jobs -k", ictx)


@posix_only
def test_sub_program_attaches_and_reports_its_exit(ictx, monkeypatch):
    """`sub PROGRAM`: started on a pty and attached; here the attach is stubbed to return at
    once, so the exit status reaches `$?` through WithStatus."""
    monkeypatch.setattr(JobTable, "attach", lambda self, job, **kw: ("exited", job.status) if _wait(lambda: not job.running) else ("exited", 1))
    result = run_line("sub sh -c 'exit 4'", ictx)
    assert result.status == 4 and "exit 4" in to_text(result.output)
    job = ictx.jobs.get(1)
    assert job.kind == "program" and job.command == "sh -c exit 4"  # shown as parsed, quotes gone
    monkeypatch.setattr(JobTable, "attach", lambda self, job, **kw: ("detached", 0))
    result = run_line("sub sh -c 'sleep 5'", ictx)  # sleep itself is a builtin: that would be a command job
    assert "detached" in to_text(result.output) and ictx.jobs.get(2).running
    assert "stopped" in to_text(run_line("jobs -k 2", ictx).output)


def test_nested_shellcraft_argv_and_depth(monkeypatch):
    monkeypatch.delenv("SHELLCRAFT_DEPTH", raising=False)
    argv, env = jobmod.nested_shellcraft(["--no-banner"])
    assert argv[:3] == [sys.executable, "-m", "core.cli"] and argv[-1] == "--no-banner"
    assert env["SHELLCRAFT_DEPTH"] == "1" and "PYTHONPATH" in env
    monkeypatch.setenv("SHELLCRAFT_DEPTH", "2")
    assert jobmod.depth() == 2 and jobmod.nested_shellcraft([])[1]["SHELLCRAFT_DEPTH"] == "3"
    monkeypatch.setenv("SHELLCRAFT_DEPTH", "junk")
    assert jobmod.depth() == 0


def test_login_shell_and_foreground_fallback(monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/zsh")
    if sys.platform != "win32":
        assert jobmod.login_shell() == ["/bin/zsh"]
    assert jobmod.run_foreground([sys.executable, "-c", "import sys; sys.exit(5)"]) == 5


# ── the shell: bar, hotkeys, hover ───────────────────────────────────────────

def test_footer_tabs_hover_click_and_ctrl_bracket_select(repl_shell, monkeypatch):
    from types import SimpleNamespace

    from core import shell as shell_module
    from core.footer import parse_mouse

    sh, ctx = repl_shell
    footer = sh.footer
    assert ctx.footer is footer and not footer.enabled and sh.session.bottom_toolbar is None
    footer.size = lambda: (24, 80)
    footer.enabled = True
    line = footer.render(80)
    assert "0:⚡shellcraft" in line and "Ctrl-] N" in line and footer.spans[0][2] == 0
    job = ctx.jobs.start_command("seq 2", lambda: (0, "1\n2\n"))
    assert _wait(lambda: not job.running)
    seq = footer.sequence()  # sets the scroll region above the last row, paints the footer there
    assert "\x1b[1;23r" in seq and "\x1b[24;1H" in seq and "1:seq 2 ✓" in seq
    x0, x1, tab = next(sp for sp in footer.spans if sp[2] == 1)
    assert footer.hit(x0 + 1, 24) == 1 and footer.hit(x0 + 1, 23) is None and footer.hit(0, 24) is None
    assert footer.set_hover(1) and not footer.set_hover(1)
    assert "1:seq 2" in footer.render(80)  # still there, now in the hover style
    assert footer.set_hover(None)
    # the mouse is on at the prompt while there are subshells, so the tabs can be clicked
    assert sh.session.app.mouse_support() is True
    ctx.jobs.clear_finished()
    assert sh.session.app.mouse_support() is False

    ran = []
    monkeypatch.setattr(shell_module, "run_in_terminal", lambda func: func())
    monkeypatch.setattr(sh, "_run_clicked", lambda line: ran.append(line))
    job = ctx.jobs.start_command("seq 3", lambda: (0, "1\n2\n3\n"))
    assert _wait(lambda: not job.running)
    footer.render(80)
    x0, x1, tab = next(sp for sp in footer.spans if sp[2] == job.number)
    click = SimpleNamespace(data=f"\x1b[<0;{x0 + 1};24m", key_sequence=[SimpleNamespace(key="<vt100-mouse-event>")])
    assert sh._footer_mouse(click) is None and ran == [f"jobs {job.number}"]
    move = SimpleNamespace(data=f"\x1b[<35;{x0 + 1};24M", key_sequence=click.key_sequence)
    assert sh._footer_mouse(move) is None and footer.hover == job.number
    assert parse_mouse("\x1b[<64;5;5M") == ("scroll", 5, 5) and parse_mouse("LEFT;MOUSE_UP;3;4") == ("up", 4, 5)
    assert parse_mouse("junk") is None
    # Ctrl-] N at the prompt
    binding = next(b for b in sh.session.key_bindings.bindings if [k for k in b.keys] == ["c-]", str(job.number)])
    binding.handler(None)
    assert ran[-1] == f"jobs {job.number}"
    assert not [b for b in sh.session.key_bindings.bindings if [k for k in b.keys][:1] == ["escape"]]  # no Alt-N
    # the prompt's jobs segment counts running jobs only
    ctx.config["prompt"] = {"left": ["jobs"]}
    assert not [f for f in sh._prompt() if "⤷" in f[1]]


def test_footer_keeps_a_programs_margins_above_it_and_spots_split_escapes():
    from core.footer import Footer, incomplete_escape

    assert Footer.clamp_margins(b"a\x1b[rb", 23) == b"a\x1b[1;23rb"
    assert Footer.clamp_margins(b"\x1b[5;24r", 23) == b"\x1b[5;23r"
    assert Footer.clamp_margins(b"\x1b[2;10r", 23) == b"\x1b[2;10r"
    assert incomplete_escape(b"abc\x1b[3") and incomplete_escape(b"x\x1b") and incomplete_escape(b"\x1b]0;title")
    assert not incomplete_escape(b"abc\x1b[3m") and not incomplete_escape(b"\x1b]0;t\x07") and not incomplete_escape(b"plain")


def test_completion_menu_follows_the_mouse(repl_shell):
    from types import SimpleNamespace

    from prompt_toolkit.buffer import CompletionState
    from prompt_toolkit.completion import Completion
    from prompt_toolkit.data_structures import Point
    from prompt_toolkit.document import Document
    from prompt_toolkit.layout.menus import CompletionsMenuControl
    from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType

    sh, ctx = repl_shell
    menu = next(c for c in sh.session.layout.find_all_controls() if isinstance(c, CompletionsMenuControl))
    state = CompletionState(Document("h"), [Completion("head"), Completion("help"), Completion("history")])
    picked = []

    def go_to_completion(index):  # the real one also rewrites the buffer text; no event loop here
        picked.append(index)
        state.complete_index = index

    buffer = sh.session.default_buffer = SimpleNamespace(complete_state=state, go_to_completion=go_to_completion,
                                                         text="h")
    move = lambda y: MouseEvent(Point(0, y), MouseEventType.MOUSE_MOVE, MouseButton.NONE, frozenset())  # noqa: E731
    assert menu.mouse_handler(move(2)) is None and picked == [2]
    assert menu.mouse_handler(move(2)) is NotImplemented  # already there: no repaint
    assert menu.mouse_handler(move(9)) is NotImplemented and picked == [2]  # clamped to the last entry
    assert menu.mouse_handler(move(0)) is None and picked == [2, 0]
    buffer.complete_state = None
    assert menu.mouse_handler(move(0)) is NotImplemented


@pytest.fixture
def repl_shell(monkeypatch, tmp_path):
    from core import shell

    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(shell, "create_prompt_toolkit_console", lambda cleanup: None)
    console = Console(record=True, width=120, force_terminal=False, color_system=None)
    ui = UI(all_themes({})["nord"], console)
    ctx = ShellContext(registry=ModuleRegistry(tmp_path), ui=ui, config={})
    with shell.Shell(ctx) as sh:
        yield sh, ctx
