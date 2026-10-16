"""Subshells: `sub`, `jobs`, the job table, attach / detach on a pseudo-terminal, the bar."""

import io
import os
import sys
import threading
import time

import pytest
from prompt_toolkit.keys import Keys
from rich.console import Console

from shellcraft import jobs as jobmod
from shellcraft.context import ShellContext, to_text
from shellcraft.jobs import JobTable
from shellcraft.loader import ModuleRegistry
from shellcraft.pipeline import PipelineError, run_line
from shellcraft.themes import UI, all_themes

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

    terminal = jobmod.Terminal.on_fds(term_slave, term_slave, raw=False)

    def attach():
        result["how"] = table.attach(job, terminal=terminal, raw=False)

    thread = threading.Thread(target=attach, daemon=True)
    thread.start()
    assert _wait(lambda: job.sink is not None)
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
    assert _wait(lambda: job.sink is not None)
    os.write(term_master, jobmod.DETACH_KEY + b"2")
    thread.join(3)
    assert result["how"] == ("switch", 2) and job.running
    assert table.kill(job) and _wait(lambda: not job.running) and job.label == "killed"
    assert table.attach(job, terminal=terminal, raw=False) == ("exited", 129)  # 128 + SIGHUP
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


def test_only_the_main_shellcraft_runs_subshells(ictx, monkeypatch, tmp_path):
    """Inside a subshell, `sub` hands the request to the main shell (tested in test_alerts);
    `jobs` is for the main shell."""
    monkeypatch.setenv("SHELLCRAFT_DEPTH", "1")
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / "home"))
    out = to_text(run_line("sub seq 1", ictx).output)
    assert "asked the main shell to open sub seq 1 in the background" in out
    with pytest.raises(PipelineError, match="only the main ShellCraft"):
        run_line("jobs", ictx)


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
    assert argv[-2:] == ["--lite", "--no-banner"] and env["PYTHONUTF8"] == "1"
    assert argv[:3] == [sys.executable, "-m", "shellcraft.cli"] and argv[-1] == "--no-banner"
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

def move_event():
    from prompt_toolkit.data_structures import Point
    from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType

    return MouseEvent(Point(0, 0), MouseEventType.MOUSE_MOVE, MouseButton.NONE, frozenset())


def test_footer_tabs_hover_click_and_ctrl_bracket_select(repl_shell, monkeypatch):
    from types import SimpleNamespace

    from shellcraft import shell as shell_module
    from shellcraft.footer import panel_lines, parse_mouse

    sh, ctx = repl_shell
    footer = sh.footer
    assert ctx.footer is footer and not footer.enabled and sh.session.bottom_toolbar is None
    footer.size = lambda: (24, 80)
    footer.enabled = True
    assert footer.height() == 1 and footer.region_rows == 23  # the main row alone: no subshell, no alert
    job = ctx.jobs.start_command("seq 2", lambda: (0, "1\n2\n"))
    assert _wait(lambda: not job.running)
    # the same stack at the prompt and in the footer
    lines = panel_lines([job], 0, None, 80, cursor=None)
    assert [r.tab for r in lines] == [0, 1] and lines[0][2].rstrip() == " ● main ▾" and lines[1][2].startswith(" ○ 1  seq 2")
    assert "done" in lines[1][2] and lines[1][1] == "done" and not lines[1][3]
    hovered = panel_lines([job], 0, 1, 80)  # hover fills the circle, the line keeps its style
    assert hovered[1][2].startswith(" ● 1 ") and hovered[1][1] == "done" and hovered[1][3]
    attached = panel_lines([job], 1, None, 80, attached=True)
    assert attached[0][2].rstrip() == " ○ main ▾" and attached[1][2].startswith(" ● 1 ") and attached[1][1] == "current"
    hinted = panel_lines([job], 1, None, 80, attached=True, hint=True)  # settings panel_hint on
    assert hinted[0][0] is None and "Ctrl-] alone returns" in hinted[0][2] and hinted[1:] == attached
    seq = footer.sequence()  # the scroll region above the stack (2 rows), the rows painted at the bottom
    assert footer.height() == 2 and "\x1b[1;22r" in seq and "\x1b[23;1H" in seq and "\x1b[24;1H" in seq
    assert "seq 2" in seq and footer.row_tabs == [0, 1]
    assert footer.hit(5, 24) == 1 and footer.hit(5, 23) == 0 and footer.hit(5, 22) is None
    footer.show_hint = lambda: True  # the header line comes back: one more row
    assert footer.height() == 3 and "\x1b[1;21r" in footer.sequence() and footer.row_tabs == [None, 0, 1]
    assert footer.hit(5, 22) is None and footer.hit(5, 23) == 0
    footer.show_hint = lambda: False
    footer.sequence()
    assert footer.set_hover(1) and not footer.set_hover(1)
    assert "seq 2" in "".join(footer.render(80))  # still there, now in the hover style
    assert footer.set_hover(None)
    # the footer's lines are clickable at the prompt only with footer_mouse on (the wheel is the price)
    assert sh.session.app.mouse_support() is False
    ctx.config.setdefault("settings", {})["footer_mouse"] = True
    assert sh.session.app.mouse_support() is True
    ctx.jobs.clear_finished()
    assert sh.session.app.mouse_support() is False
    sh.ui.mouse_always = True
    assert sh.session.app.mouse_support() is True
    sh.ui.mouse_always = False

    ran = []
    monkeypatch.setattr(shell_module, "run_in_terminal", lambda func: func())
    monkeypatch.setattr(sh, "_run_clicked", lambda line: ran.append(line))
    job = ctx.jobs.start_command("seq 3", lambda: (0, "1\n2\n3\n"))
    assert _wait(lambda: not job.running)
    footer.sequence()
    row = 24 - len(footer.row_tabs) + 1 + footer.row_tabs.index(job.number)
    click = SimpleNamespace(data=f"\x1b[<0;5;{row}m", key_sequence=[SimpleNamespace(key="<vt100-mouse-event>")])
    assert sh._footer_mouse(click) is None and ran == [f"jobs {job.number}"]
    move = SimpleNamespace(data=f"\x1b[<35;5;{row}M", key_sequence=click.key_sequence)
    assert sh._footer_mouse(move) is None and footer.hover == job.number
    assert parse_mouse("\x1b[<64;5;5M") == ("scroll", 5, 5, "left") and parse_mouse("LEFT;MOUSE_UP;3;4") == ("up", 4, 5, "left")
    assert parse_mouse("junk") is None
    # Ctrl-] N at the prompt
    binding = next(b for b in sh.session.key_bindings.bindings if [k for k in b.keys] == ["c-]", str(job.number)])
    binding.handler(None)
    assert ran[-1] == f"jobs {job.number}"
    assert not [b for b in sh.session.key_bindings.bindings if [k for k in b.keys] == ["escape", "1"]]  # no Alt-N
    # the panel under the prompt: ↓ walks it, Enter picks
    footer.enabled = False
    sh._update_toolbar()
    lines = "".join(f[1] for f in sh._toolbar()).split("\n")
    assert lines[0].rstrip() == " ● main ▾" and lines[1].startswith(f" ○ {job.number}  seq 3") and "done" in lines[1]
    ctx.config.setdefault("settings", {})["panel_hint"] = True  # the header line with the keys
    hinted = "".join(f[1] for f in sh._toolbar()).split("\n")
    assert hinted[0].startswith(" 1 subshell") and "↓ to manage" in hinted[0] and hinted[1:] == lines
    del ctx.config["settings"]["panel_hint"]
    entry = next(f for f in sh._toolbar() if "seq 3" in f[1])
    assert entry[0] == "class:toolbar.job.done" and entry[2](move_event()) is None and sh._panel_hover == job.number
    frags = list(sh._toolbar())
    circle = next(f for f in frags if f[1] == " ● ")
    assert circle[0] == "class:toolbar.job.hover"  # only the circle: the rest of the line keeps its style
    assert next(f for f in frags if "seq 3" in f[1])[0] == "class:toolbar.job.done"
    sh._panel_hover = None
    down = [b for b in sh.session.key_bindings.bindings if [k for k in b.keys] == ["down"]]
    assert len(down) == 2  # enter the panel, move in it
    sh._panel_cursor = 0
    assert any(f[1] == " ● " and f[0] == "class:toolbar.job.hover" for f in sh._toolbar())
    enter = next(b for b in sh.session.key_bindings.bindings if list(b.keys) == [Keys.Enter])
    enter.handler(None)
    assert sh._panel_cursor is None and ran[-1] == f"jobs {job.number}"
    # the prompt's jobs segment counts running jobs only
    ctx.config["prompt"] = {"left": ["jobs"]}
    assert not [f for f in sh._prompt() if " sub" in f[1]]


def test_footer_keeps_a_programs_margins_above_it_and_spots_split_escapes():
    from shellcraft.footer import Footer, incomplete_escape

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
    from shellcraft import shell

    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(shell, "create_prompt_toolkit_console", lambda cleanup: None)
    console = Console(record=True, width=120, force_terminal=False, color_system=None)
    ui = UI(all_themes({})["nord"], console)
    ctx = ShellContext(registry=ModuleRegistry(tmp_path), ui=ui, config={})
    with shell.Shell(ctx) as sh:
        yield sh, ctx


# ── the emulator: pages, colors, the alternate screen, scrollback ────────────

def test_program_page_is_rendered_from_the_emulator(tmp_path):
    from shellcraft.jobs import Pages, Terminal, new_screen, render_screen

    screen, stream = new_screen(4, 20)
    stream.feed(b"\x1b[1;31mred\x1b[0m plain\r\n\x1b[38;2;10;20;30mtc\x1b[0m\r\n")
    page = render_screen(screen).decode()
    assert page.startswith("\x1b[?25l\x1b[H\x1b[2J") and "\x1b[0;1;31mred" in page and "\x1b[0;38;2;10;20;30mtc" in page
    assert page.rstrip().endswith("\x1b[3;1H\x1b[?25h")  # the cursor on the third line, shown
    job = jobmod.Job(1, "x", "program", tmp_path / "1.log")
    job.screen, job.stream = screen, stream
    shown = []
    job.sink = shown.append
    job.append(b"\x1b[?1049h\x1b[Hvim!\x1b[?1049l")  # the alternate screen: saved and restored
    assert "vim!" not in render_screen(job.screen).decode() and "red" in render_screen(job.screen).decode()
    assert b"\x1b[?1049" not in shown[0] and b"\x1b[H\x1b[2J" in shown[0]  # never reaches the terminal
    assert b"red" in shown[0]  # the restored main page is drawn when the program leaves its alt screen
    for i in range(10):
        job.append(f"line {i}\r\n".encode())
    job.scroll(-1)
    assert job.scrolled == 1 and "line 9" not in render_screen(job.screen).decode()
    job.scroll_bottom()
    assert job.scrolled == 0 and "line 9" in render_screen(job.screen).decode()
    job.page(6, 30)
    assert (job.screen.lines, job.screen.columns) == (6, 30)
    written = []
    pages = Pages(Terminal(lambda t: None, written.append, lambda: (24, 80)))
    pages.begin()
    pages.begin()
    pages.end()
    assert written == [b"\x1b[?1049h\x1b[H\x1b[2J", b"\x1b[r\x1b[?25h\x1b[0m\x1b[?1049l"]


@posix_only
def test_wheel_scrolls_the_scrollback_and_the_page_is_redrawn(tmp_path):
    import tty

    table = JobTable(tmp_path / "jobs")
    job = table.start_program(["sh", "-c", "for i in $(seq 1 60); do echo line$i; done; sleep 30"], "sh", size=(5, 40))
    assert _wait(lambda: b"line60" in job.tail())
    term_master, term_slave = os.openpty()
    tty.setraw(term_slave)
    jobmod._set_winsize(term_slave, 5, 40)  # the fake terminal's size (a fresh pty says 0 × 0)
    terminal = jobmod.Terminal.on_fds(term_slave, term_slave, raw=False)
    result = {}
    thread = threading.Thread(target=lambda: result.update(how=table.attach(job, terminal=terminal, raw=False)), daemon=True)
    thread.start()
    assert _wait(lambda: job.sink is not None)
    seen = b""
    end = time.time() + 3
    while b"line60" not in seen and time.time() < end:
        seen += os.read(term_master, 65536)
    assert b"line60" in seen and b"\x1b[?1000h" in seen  # the page, and the mouse taken for the wheel
    os.write(term_master, b"\x1b[<64;5;3M")  # wheel up: a page back in the history
    seen = b""
    end = time.time() + 3
    while b"\x1b[2J" not in seen and time.time() < end:
        seen += os.read(term_master, 65536)
    assert job.scrolled == 1 and b"line60" not in seen.split(b"\x1b[2J")[-1]
    os.write(term_master, b"x")  # any key: back to the live page
    assert _wait(lambda: job.scrolled == 0)
    os.write(term_master, jobmod.DETACH_KEY)
    thread.join(3)
    assert result["how"] == ("detached", 0)
    table.kill(job)
    os.close(term_master)
    os.close(term_slave)


def test_windows_backend_through_a_fake_winpty(tmp_path, monkeypatch):
    """The ConPTY path (pywinpty) with a fake PtyProcess: spawn, read, write, resize, exit."""
    import types

    class FakePty:
        spawned = []

        def __init__(self, argv, cwd, env, dimensions):
            self.argv, self.cwd, self.env, self.dimensions = argv, cwd, env, dimensions
            self.pid = 4242
            self.chunks = ["hello from conpty\r\n", ""]
            self.written = []
            self.alive = True
            self.exitstatus = None

        @classmethod
        def spawn(cls, argv, cwd=None, env=None, dimensions=(24, 80)):
            inst = cls(argv, cwd, env, dimensions)
            cls.spawned.append(inst)
            return inst

        def read(self, size=1024):
            if self.chunks:
                return self.chunks.pop(0)
            if not self.alive:
                raise EOFError
            time.sleep(0.02)
            return ""

        def isalive(self):
            return self.alive

        def write(self, s):
            self.written.append(s)
            if s == "quit":
                self.alive, self.exitstatus = False, 7
            return len(s)

        def setwinsize(self, rows, cols):
            self.dimensions = (rows, cols)

        def terminate(self, force=False):
            self.alive, self.exitstatus = False, 1

    monkeypatch.setitem(sys.modules, "winpty", types.SimpleNamespace(PtyProcess=FakePty))
    monkeypatch.setattr(sys, "platform", "win32")
    table = JobTable(tmp_path / "jobs")
    job = table.start_program([sys.executable, "-c", "pass"], "python", size=(10, 50))
    assert job.winpty is FakePty.spawned[-1] and job.pid == 4242 and job.winpty.dimensions == (10, 50)
    assert _wait(lambda: b"hello from conpty" in job.tail()) and "hello from conpty" in job.page().decode()
    job.resize(12, 60)
    assert job.winpty.dimensions == (12, 60) and (job.screen.lines, job.screen.columns) == (12, 60)
    job.write(b"quit")
    assert job.winpty.written == ["quit"] and _wait(lambda: not job.running) and job.status == 7
    # without pywinpty the program is run in the foreground instead
    monkeypatch.setitem(sys.modules, "winpty", None)
    with pytest.raises(jobmod.JobsUnsupported):
        table.start_program([sys.executable], "python")


@posix_only
def test_without_pyte_programs_still_work_and_the_user_is_told(tmp_path, monkeypatch):
    import builtins as py_builtins
    import tty

    real_import = py_builtins.__import__

    def no_pyte(name, *args, **kwargs):
        if name == "pyte":
            raise ImportError("no pyte")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(py_builtins, "__import__", no_pyte)
    table = JobTable(tmp_path / "jobs")
    job = table.start_program(["sh", "-c", "echo plain; sleep 30"], "sh")
    assert job.screen is None and _wait(lambda: b"plain" in job.tail())
    term_master, term_slave = os.openpty()
    tty.setraw(term_slave)
    jobmod._set_winsize(term_slave, 5, 40)
    terminal = jobmod.Terminal.on_fds(term_slave, term_slave, raw=False)
    result = {}
    thread = threading.Thread(target=lambda: result.update(how=table.attach(job, terminal=terminal, raw=False)), daemon=True)
    thread.start()
    assert _wait(lambda: job.sink is not None)
    os.write(term_master, jobmod.DETACH_KEY)
    thread.join(3)
    assert result["how"] == ("detached", 0)
    assert any("pip install pyte" in note for note in table.pop_notifications())
    table.kill(job)
    os.close(term_master)
    os.close(term_slave)


def test_windows_arrow_keys_reach_the_subshell():
    """The classic Windows console reports arrows as key codes with no character: they become
    the VT sequences the program expects; characters (and VT input) pass through as they are."""
    from shellcraft.jobs import win_key

    assert [win_key("\x00", vk) for vk in (0x26, 0x28, 0x27, 0x25)] == ["\x1b[A", "\x1b[B", "\x1b[C", "\x1b[D"]
    assert win_key("", 0x24) == "\x1b[H" and win_key("", 0x2E) == "\x1b[3~"
    assert win_key("a", 0x41) == "a" and win_key("\x1b", 0x1B) == "\x1b"  # VT input: ESC [ A arrive as chars
    assert win_key("\x00", 0x10) == ""  # Shift alone: nothing


@posix_only
def test_ctrl_bracket_x_ends_the_attached_subshell(tmp_path, ictx, monkeypatch):
    """Ctrl-] then x in an attached program: attach returns ("kill", N) and `sub` stops it."""
    import tty

    from rich.console import Console

    from shellcraft.alerts import AlertBus
    from shellcraft.footer import Footer
    from shellcraft.themes import UI, all_themes

    table = JobTable(tmp_path / "jobs")
    job = table.start_program(["cat"], "cat")
    term_master, term_slave = os.openpty()
    tty.setraw(term_slave)
    terminal = jobmod.Terminal.on_fds(term_slave, term_slave, raw=False)
    bus = AlertBus()
    bus.clip = "pasted text\n"
    footer = Footer(UI(all_themes({})["nord"], Console(file=io.StringIO())), table, alerts=bus)  # not enabled
    result = {}

    def run() -> None:
        result["how"] = table.attach(job, terminal=terminal, footer=footer, raw=False)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert _wait(lambda: job.sink is not None)
    os.write(term_master, jobmod.DETACH_KEY + b"v")  # Ctrl-] v: the clip goes to the program (cat echoes it)
    assert _wait(lambda: "pasted text" in job.output) and not job.output.endswith("\n\n")
    os.write(term_master, jobmod.DETACH_KEY + b"c")  # Ctrl-] c: fold the stack
    assert _wait(lambda: footer.collapsed)
    opened: list = []
    table.on_request = lambda: opened.append(table.requests.pop(0))  # the shell's handler: open it, stay here
    table.requests.append({"args": ["pwsh"], "cwd": "/"})  # `sub pwsh` typed inside a subshell
    assert _wait(lambda: opened) and job.sink is not None and thread.is_alive()  # still attached
    os.write(term_master, jobmod.DETACH_KEY + b"x")
    thread.join(3)
    assert result["how"] == ("kill", job.number)
    bus.close()
    table.kill(job)
    os.close(term_master)
    os.close(term_slave)
    # in the builtin: the kill result stops the job and reports it
    monkeypatch.setattr(JobTable, "attach", lambda self, job, **kw: ("kill", job.number))
    note = to_text(run_line("sub sh -c 'sleep 30'", ictx).output)
    killed = ictx.jobs.get(1)
    assert "killed: sh -c sleep 30" in note and _wait(lambda: not killed.running) and killed.killed


def test_panel_x_stops_the_chosen_subshell(repl_shell, monkeypatch):
    from shellcraft import shell as shell_module

    sh, ctx = repl_shell
    ran = []
    monkeypatch.setattr(shell_module, "run_in_terminal", lambda func: func())
    monkeypatch.setattr(sh, "_run_clicked", lambda line: ran.append(line))
    job = ctx.jobs.start_command("seq 9", lambda: (0, "1\n"))
    assert _wait(lambda: not job.running)
    sh._panel_cursor = 0
    stop = next(b for b in sh.session.key_bindings.bindings if [k for k in b.keys] == ["x"])
    stop.handler(None)
    assert ran == [f"jobs -k {job.number}"]
    assert "x stops" not in "".join(f[1] for f in sh._toolbar())  # the key hints only with panel_hint
    ctx.config.setdefault("settings", {})["panel_hint"] = True
    assert "x stops" in "".join(f[1] for f in sh._toolbar())
    del ctx.config["settings"]["panel_hint"]


def _job_threads(job):
    """The threads still working for a job: its worker and the pipeline segments it started."""
    return [t for t in threading.enumerate()
            if t.is_alive() and (t is job.thread or t.name.startswith("shellcraft:") and not t.name.startswith("shellcraft:job"))]


@pytest.mark.parametrize("line", ["yes", "yes | cat", "sleep 60", "for i in 1 2 3; do sleep 60; done"])
def test_jobs_kill_stops_a_background_commands_threads(ictx, line):
    """`jobs -k N` stops the command for real: its worker thread ends, and so do the pipeline
    segments it started (an endless producer, sleep, every statement of a loop)."""
    before = {t for t in threading.enumerate()}
    run_line(f"{line} &", ictx)  # a pipeline or a loop goes to the background with a trailing &
    job = ictx.jobs.get(1)
    time.sleep(0.3)
    assert job.running and job.thread.is_alive()
    assert "stopped" in to_text(run_line("jobs -k 1", ictx).output)
    assert _wait(lambda: not job.thread.is_alive(), 3)
    assert _wait(lambda: not [t for t in threading.enumerate() if t not in before and t.is_alive()
                              and t.name.startswith("shellcraft:")], 3)
    assert job.label == "killed" and ictx.jobs.pop_notifications().count(f"[1] killed: {line}") == 1


@posix_only
def test_jobs_kill_stops_a_quiet_os_program_in_a_background_pipeline(ictx):
    ictx.allow_system = True
    run_line("/bin/sleep 60 | cat &", ictx)  # the OS program, quiet: only the watcher can stop it
    job = ictx.jobs.get(1)
    time.sleep(0.3)
    assert job.kind == "command" and job.running
    run_line("jobs -k 1", ictx)
    assert _wait(lambda: not job.thread.is_alive(), 3)


def _mouse(kind, button):
    from prompt_toolkit.data_structures import Point
    from prompt_toolkit.mouse_events import MouseEvent

    return MouseEvent(Point(0, 0), kind, button, frozenset())


def test_alert_mouse_at_the_prompt_with_windows_releases(repl_shell):
    """Windows reports a press as RIGHT;MOUSE_DOWN and its release as NONE;MOUSE_UP: the copy,
    the icon and the badge go by the button that went down. The mouse is on for alerts alone."""
    from types import SimpleNamespace

    from prompt_toolkit.mouse_events import MouseButton, MouseEventType

    from shellcraft.alerts import AlertBus

    sh, ctx = repl_shell
    ctx.alerts = AlertBus()
    assert not sh._mouse_wanted()  # no subshell, no alert: the terminal keeps the mouse
    warn = ctx.alerts.post("disk", "warning", content="/var/log/app.log")
    assert sh._mouse_wanted()  # an alert shows: its ✖, right-click and badge need the mouse
    # the keyboard ways: Alt-c copies the top alert, clip -a N alert N
    binding = next(b for b in sh.session.key_bindings.bindings if [str(k) for k in b.keys] == ["Keys.Escape", "c"])
    binding.handler(None)
    assert ctx.alerts.clip == "/var/log/app.log" and ctx.alerts.copied is warn
    ctx.alerts.clip = ""
    ctx.interactive = True
    assert "copied: /var/log/app.log" in to_text(run_line("clip -a 1", ctx).output) and ctx.alerts.clip
    with pytest.raises(PipelineError, match="no such alert"):
        run_line("clip -a 5", ctx)
    ctx.alerts.clip = ""
    keys = [SimpleNamespace(key="<windows-mouse-event>")]

    def press(button, kind):  # what the Windows input sends through the prompt's mouse binding
        sh._footer_mouse(SimpleNamespace(data=f"{button};{kind};3;4", key_sequence=keys))

    row = sh._alert_handler(warn)
    press("RIGHT", "MOUSE_DOWN")
    assert row(_mouse(MouseEventType.MOUSE_DOWN, MouseButton.RIGHT)) is None
    press("NONE", "MOUSE_UP")
    assert row(_mouse(MouseEventType.MOUSE_UP, MouseButton.NONE)) is None
    assert ctx.alerts.clip == "/var/log/app.log" and warn in ctx.alerts.current()  # copied, not dismissed
    ctx.alerts.clip = ""
    close = sh._close_handler(warn)
    press("RIGHT", "MOUSE_DOWN")
    close(_mouse(MouseEventType.MOUSE_UP, MouseButton.NONE))  # a right-click on the icon: still a copy
    assert ctx.alerts.clip == "/var/log/app.log" and warn in ctx.alerts.current()
    press("LEFT", "MOUSE_DOWN")
    row(_mouse(MouseEventType.MOUSE_UP, MouseButton.NONE))  # a left click on the row: nothing
    assert warn in ctx.alerts.current()
    badge = sh._badge_handler()
    assert badge(_mouse(MouseEventType.MOUSE_UP, MouseButton.NONE)) is None and ctx.alerts.expanded
    close(_mouse(MouseEventType.MOUSE_UP, MouseButton.NONE))  # a left click on the icon: dismissed
    assert warn not in ctx.alerts.current()
    # Linux reports the real button on release: the same results
    other = ctx.alerts.post("other", "info")
    sh._alert_handler(other)(_mouse(MouseEventType.MOUSE_UP, MouseButton.RIGHT))
    assert ctx.alerts.clip == "other"
    ctx.alerts.close()


def test_footer_mouse_with_windows_releases(tmp_path):
    from shellcraft.alerts import AlertBus
    from shellcraft.footer import Footer, parse_mouse

    bus = AlertBus()
    warn = bus.post("disk", "warning", source="sub 1 (bash)", content="df -h")
    footer = Footer(UI(all_themes({})["nord"], Console(file=io.StringIO())), [], alerts=bus)
    footer.size = lambda: (24, 80)
    footer.enabled = True
    footer.sequence()
    assert footer.row_tabs == [None, 0]  # no subshells: the alert row, then main on the last line
    assert parse_mouse("RIGHT;MOUSE_DOWN;9;22") == ("down", 10, 23, "right")
    assert footer.mouse(*parse_mouse("RIGHT;MOUSE_DOWN;9;22")) == ("handled", None)
    assert footer.mouse(*parse_mouse("NONE;MOUSE_UP;9;22")) == ("handled", None)
    assert bus.clip == "df -h" and warn in bus.current()  # copied on the release that said NONE
    footer.mouse("move", 10, 23, "none")
    assert footer.mouse(*parse_mouse("LEFT;MOUSE_DOWN;1;22")) == ("handled", None)
    assert footer.mouse(*parse_mouse("NONE;MOUSE_UP;1;22")) == ("handled", None)
    assert not bus.current()  # the icon took the left click
    bus.close()


def test_lite_shell_skips_startup_watchers_and_mcp(tmp_path, monkeypatch, started):
    """`shellcraft --lite` (what `sub shellcraft` runs): no banner, no startup scripts or rc, no
    alert spool watcher, no module watcher, and `mcp` refused."""
    from shellcraft import cli, shell

    home = tmp_path / "home"
    (home / "startup").mkdir(parents=True)
    (home / "startup" / "10-x.py").write_text("print('hi')\n")
    monkeypatch.setenv("SHELLCRAFT_HOME", str(home))
    assert cli.main(["--lite", "--modules", str(tmp_path)]) == 0
    assert started[-1].lite and started.startup_runs == 0
    assert cli.main(["--no-banner", "--modules", str(tmp_path)]) == 0
    assert not started[-1].lite and started.startup_runs == 1
    # the real shell, lite
    monkeypatch.undo()
    monkeypatch.setenv("SHELLCRAFT_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(shell, "create_prompt_toolkit_console", lambda cleanup: None)
    console = Console(record=True, width=120, force_terminal=False, color_system=None)
    ctx = ShellContext(registry=ModuleRegistry(tmp_path), ui=UI(all_themes({})["nord"], console), config={}, lite=True)
    with shell.Shell(ctx) as sh:
        assert ctx.alerts is not None and ctx.alerts.spool is None  # the main shell owns the spool
        (tmp_path / "newmod.py").write_text("def run(args, stdin):\n    return 'x'\n")
        sh._hot_reload()  # a lite shell never reloads
        assert ctx.registry.get("newmod") is None
        with pytest.raises(PipelineError, match="not in a lite shell"):
            run_line("mcp start", ctx)
        assert to_text(run_line("echo still a shell", ctx).output) == "still a shell\n"


class _FakeJob:
    def __init__(self, number, command="bash", running=True, status=0):
        self.number, self.command, self.running, self.status = number, command, running, status
        self.label = "running" if running else ("done" if status == 0 else f"exit {status}")

    def short(self, width=18):
        return self.command[:width]


def test_stack_rows_fold_cap_and_always_show_main():
    from shellcraft.footer import panel_lines

    jobs = [_FakeJob(n, f"job {n}") for n in range(1, 6)]
    rows = panel_lines([], 0, width=60, stack=True)
    assert [r.text.rstrip() for r in rows] == [" ● main"]  # the footer: main even with nothing under it
    assert panel_lines([], 0, width=60) == []  # the panel under the prompt (footer off): nothing
    rows = panel_lines(jobs, 2, width=60, stack=True, max_rows=3)
    assert rows[0].text.startswith(" ○ main ▾") and rows[0].spans[0][3] == "fold"
    assert [r.tab for r in rows] == [0, 1, 2, 3, None] and rows[-1].text.strip() == "… and 2 more (jobs)"
    assert rows[2].text.startswith(" ● 2  job 2")  # the shell you are in: the filled bubble
    folded = panel_lines(jobs, 2, width=60, stack=True, collapsed=True)
    assert [r.tab for r in folded] == [0] and folded[0].text.rstrip() == " ○ main  5 subshells ▸"
    assert folded[0].text[folded[0].spans[0][0]:folded[0].spans[0][1]] == "  5 subshells ▸"
    one = panel_lines(jobs[:1], 0, width=60, stack=True, collapsed=True)
    assert one[0].text.rstrip() == " ● main  1 subshell ▸"


def test_footer_height_fold_click_and_shortened_screen(repl_shell, monkeypatch):
    from prompt_toolkit.data_structures import Size

    from shellcraft.footer import Footer

    sh, ctx = repl_shell
    jobs = [_FakeJob(n, f"job {n}") for n in range(1, 10)]
    footer = Footer(sh.ui, jobs, alerts=ctx.alerts)
    footer.size = lambda: (12, 80)
    footer.enabled = True
    assert footer.max_rows() == 6 and footer.height() == 1 + 6 + 1  # main, six rows, "… and 3 more"
    footer.size = lambda: (40, 80)
    assert footer.max_rows() == 20 and footer.height() == 10
    footer.sequence()
    fold = footer.rows[0]
    col = fold.text.index("▾") + 1
    assert footer.mouse("up", col, 40 - footer.height() + 1, "left") == ("handled", None)
    assert footer.collapsed and footer.height() == 1 and footer.rows[0].text.rstrip().endswith("main  9 subshells ▸")
    assert footer.mouse("up", 5, 40, "left") == ("switch", 0)  # the main row itself still switches
    assert footer.toggle_collapsed() is False and footer.height() == 10
    footer.sequence()  # 10 rows painted (31-40); folding to 1 row clears rows 31-39
    footer.collapsed = True
    folded = footer.sequence()
    assert all(f"\x1b[{row};1H\x1b[2K" in folded for row in range(31, 40)) and "\x1b[1;39r" in folded
    assert "\x1b[30;1H" not in folded and footer.sequence().count("\x1b[2K") == 1  # nothing to clear now
    footer.collapsed = False
    # the prompt's screen is shorter by the footer while it is drawn at the prompt
    sh.footer.size = lambda: (24, 80)
    monkeypatch.setattr(sh.session.app.output, "get_size", lambda: Size(rows=24, columns=80), raising=False)
    sh._shorten_output_for_footer()
    assert sh.session.app.output.get_size() == Size(rows=24, columns=80)  # not enabled: untouched
    sh.footer.enabled = True
    assert sh.session.app.output.get_size() == Size(rows=23, columns=80)  # the main row
    sh.footer.enabled = False
    # at the prompt: the `footer` setting and a terminal turn it on; `settings footer off` turns it off
    assert not sh.footer.at_prompt
    sh._footer_at_prompt()
    assert not sh.footer.at_prompt  # the test console is not a terminal
    monkeypatch.setattr(type(sh.ui.console), "is_terminal", property(lambda self: True))
    sh._footer_at_prompt()
    assert sh.footer.at_prompt and sh.footer.enabled
    ctx.config["settings"] = {"footer": False}
    sh._footer_at_prompt()
    assert not sh.footer.at_prompt and not sh.footer.enabled
    del ctx.config["settings"]


def test_finished_jobs_report_their_output_and_clear_themselves(repl_shell):
    sh, ctx = repl_shell
    job = ctx.jobs.start_command("seq 3", lambda: (0, "1\n2\n3\n"))
    assert _wait(lambda: not job.running) and len(ctx.jobs) == 1 and not job.reported
    sh._report_finished()
    out = sh.ui.console.export_text()
    assert "[1] done: seq 3" in out and "1\n2\n3" in out and len(ctx.jobs) == 0  # shown once, then gone
    failing = ctx.jobs.start_command("false", lambda: (1, ""))
    assert _wait(lambda: not failing.running)
    ctx.jobs.pop_notifications()  # reported elsewhere (jobs N, the attach loop): no output again
    assert failing.reported
    ctx.config["settings"] = {"jobs_autoclear": False}
    sh._report_finished()
    assert len(ctx.jobs) == 1  # off: it stays until jobs -c
    del ctx.config["settings"]
    sh._report_finished()
    assert len(ctx.jobs) == 0 and "false" not in sh.ui.console.export_text().split("[1] done: seq 3")[-1].replace("exit 1: false", "")


def test_sc_is_shellcraft_and_ampersand_inside_a_subshell_goes_to_main(ictx, monkeypatch, tmp_path):
    from shellcraft import alerts as alertmod
    from shellcraft import builtins as bmod
    from shellcraft.jobs import JobTable

    started: list = []

    def fake_start(self, argv, command, env=None, cwd=None, size=None):
        started.append((argv, command))
        return self.start_command(command, lambda: (0, ""))

    monkeypatch.setattr(JobTable, "start_program", fake_start)
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / "home"))
    ictx.jobs = JobTable(tmp_path / "jobs")
    assert "background" in to_text(run_line("sc &", ictx).output)
    assert started[-1][0][-3:] == ["shellcraft.cli", "--lite"] or "--lite" in started[-1][0]
    assert started[-1][1] == "sc"
    monkeypatch.setattr(bmod, "_attach", lambda ctx, job: "attached")
    assert run_line("sub sc --theme nord", ictx).output == "attached" and "--lite" in started[-1][0]
    # inside a subshell: `cmd &` is handed to main, like `sub`
    monkeypatch.setenv("SHELLCRAFT_DEPTH", "1")
    monkeypatch.setenv(alertmod.SUB_ENV, "2:shellcraft")
    out = to_text(run_line("sleep 9 &", ictx).output)
    assert "asked the main shell to run sleep 9 &" in out
    request = sorted((tmp_path / "home" / "alerts").glob("*.json"))[-1]
    import json

    assert json.loads(request.read_text())["line"] == "sleep 9"
    monkeypatch.delenv("SHELLCRAFT_DEPTH")
    # main runs it in the background: a command job here (sleep is a builtin), never attached
    ictx.jobs.requests.append({"line": "sleep 9", "cwd": str(tmp_path), "source": "sub 2 (shellcraft)"})
    notes = bmod.open_requested(ictx)
    assert len(notes) == 1 and "sleep 9" in notes[0] and "asked by sub 2 (shellcraft)" in notes[0]
    assert any(j.command == "sleep 9" for j in ictx.jobs)
    for job in list(ictx.jobs):
        ictx.jobs.kill(job)


def test_finished_line_has_a_close_mark_and_rows_fill_the_width(tmp_path, repl_shell):
    from prompt_toolkit.utils import get_cwidth

    from shellcraft.footer import Footer, panel_lines
    from shellcraft.jobs import JobTable

    sh, ctx = repl_shell
    table = JobTable(tmp_path / "jobs")
    done = table.start_command("seq 1", lambda: (0, "1\n"))
    running = table.start_command("sleep", lambda: (_wait(lambda: False, 0.5), "")[1] and (0, "") or (0, ""))
    assert _wait(lambda: not done.running)
    rows = panel_lines(list(table), 0, width=60, stack=True)
    assert all(get_cwidth(r.text) == 60 for r in rows)  # every row's color runs to the right edge
    assert rows[1].text.rstrip().endswith("done ×") and rows[1].spans[-1][3] == "close-job"
    assert rows[2].text.rstrip().endswith("sleep") and not any(role == "close-job" for *_, role in rows[2].spans)
    footer = Footer(sh.ui, table, alerts=None)
    footer.size = lambda: (24, 60)
    footer.enabled = True
    footer.sequence()
    row = footer.rows[1]
    col = row.text.rindex("×") + 1  # 1-based terminal column of the ×
    assert footer.mouse("up", col, 24 - len(footer.rows) + 2, "left") == ("handled", None)
    assert table.get(done.number) is None and len(table) == 1  # the × forgot the finished job
    footer.remove_job(running.number)  # a running one is not removed
    assert len(table) == 1
    table.kill(running)


def test_jobs_rename_shows_in_the_footer_and_the_table(ictx, tmp_path):
    from shellcraft.footer import panel_lines
    from shellcraft.jobs import JobTable

    ictx.jobs = JobTable(tmp_path / "jobs")
    job = ictx.jobs.start_command("sleep 30", lambda: (_wait(lambda: False, 0.3), (0, ""))[1])
    out = to_text(run_line("jobs -r 1 api logs", ictx).output)
    assert "is now api logs" in out and job.name == "api logs" and job.title == "api logs"
    assert panel_lines([job], 0, width=60, stack=True)[1].text.startswith(" ○ 1  api logs")
    listed = to_text(run_line("jobs", ictx).output)
    assert "api logs" in listed and "sleep 30" in listed
    assert "is sleep 30 again" in to_text(run_line("jobs -r 1", ictx).output) and job.name is None
    with pytest.raises(PipelineError, match="-r needs the number"):
        run_line("jobs -r 9 x", ictx)
    ictx.jobs.rename(job, "   ")
    assert job.name is None
    ictx.jobs.rename(job, "x")
    assert _wait(lambda: not job.running)
    assert ictx.jobs.pop_notifications() == ["[1] done: x"]


def test_mouse_mode_helpers():
    modes = jobmod.mouse_modes_after(set(), b"x\x1b[?1000h\x1b[?1006hy")
    assert modes == {1000, 1006}
    assert jobmod.mouse_modes_after(modes, b"\x1b[?1000l\x1b[?1006l") == set()
    assert jobmod.footer_mouse_bytes(set()).endswith(b"\x1b[?1000h\x1b[?1006h")
    assert b"\x1b[?1003h" in jobmod.footer_mouse_bytes({1000, 1003, 1006})
    click = b"\x1b[<0;5;7M"
    assert jobmod.sgr_for_program(click, {1000, 1006}) == click  # it asked for SGR: as is
    assert jobmod.sgr_for_program(click, {1000}) == b"\x1b[M" + bytes((32, 37, 39))  # X10 for an old program
    assert jobmod.sgr_for_program(b"\x1b[<0;5;7m", {1000}) == b"\x1b[M" + bytes((35, 37, 39))  # release
    assert jobmod.sgr_for_program(b"\x1b[<35;5;7M", {1000, 1006}) == b""  # motion it did not ask for
    assert jobmod.sgr_for_program(b"\x1b[<35;5;7M", {1003, 1006}) == b"\x1b[<35;5;7M"
    assert jobmod.sgr_for_program(b"\x1b[<32;5;7M", {1002, 1006}) == b"\x1b[<32;5;7M"  # a drag
    assert jobmod.sgr_for_program(b"\x1b[<0;300;7M", {1000}) == b""  # too far for X10


@posix_only
def test_a_program_turning_its_mouse_off_keeps_the_footers(tmp_path):
    """A program inside a subshell (a lite ShellCraft, less, vim) switching mouse reporting on and
    off must not switch the footer's off: its mode sequences never reach the terminal, ours do."""
    import select
    import tty

    from rich.console import Console

    from shellcraft.footer import Footer
    from shellcraft.themes import UI, all_themes

    table = JobTable(tmp_path / "jobs")
    script = "printf 'A\\033[?1000h\\033[?1006hB'; sleep 0.4; printf 'C\\033[?1000l\\033[?1006lD'; cat"
    job = table.start_program(["sh", "-c", script], "sh")
    term_master, term_slave = os.openpty()
    tty.setraw(term_slave)
    jobmod._set_winsize(term_slave, 24, 80)
    terminal = jobmod.Terminal.on_fds(term_slave, term_slave, raw=False)
    footer = Footer(UI(all_themes({})["nord"], Console(file=io.StringIO())), table)
    footer.size = lambda: (24, 80)
    seen = bytearray()

    def drain(seconds):
        end = time.time() + seconds
        while time.time() < end:
            if select.select([term_master], [], [], 0.05)[0]:
                seen.extend(os.read(term_master, 65536))

    thread = threading.Thread(target=lambda: table.attach(job, terminal=terminal, footer=footer, raw=False),
                              daemon=True)
    thread.start()
    drain(1.5)
    assert b"C" in seen and b"D" in seen
    after = bytes(seen[seen.rindex(b"D"):])
    assert b"\x1b[?1000l" not in after and b"\x1b[?1000h" in bytes(seen[seen.index(b"C"):])  # re-asserted
    assert job.mouse_modes == set() and not job.mouse_on
    tail = bytes(seen[seen.index(b"A"):])
    assert tail.count(b"\x1b[?1000l") == 0  # the program's switch-offs never reached the terminal
    os.write(term_master, jobmod.DETACH_KEY + b"x")
    thread.join(3)
    table.kill(job)
    os.close(term_master)
    os.close(term_slave)
