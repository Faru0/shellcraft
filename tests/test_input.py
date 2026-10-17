"""Mouse and paste input: the wheel inside an attached subshell, the scroll view at the prompt (the
wheel while the shell has the mouse), who has the mouse at the prompt, and pasting the clip."""

import io
import os
import select
import sys
import threading
import time

import pytest
from prompt_toolkit.application.current import set_app
from prompt_toolkit.key_binding.key_processor import KeyPress
from prompt_toolkit.keys import Keys
from rich.console import Console

from shellcraft import jobs as jobmod
from shellcraft.context import ShellContext, to_text
from shellcraft.footer import Footer
from shellcraft.jobs import JobTable
from shellcraft.loader import ModuleRegistry
from shellcraft.pipeline import PipelineError, run_line
from shellcraft.themes import UI, all_themes

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="pseudo-terminals are POSIX only")


@pytest.fixture
def repl_shell(monkeypatch, tmp_path):
    from shellcraft import shell

    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(shell, "create_prompt_toolkit_console", lambda cleanup: None)
    console = Console(file=io.StringIO(), width=120, force_terminal=False, color_system=None)
    ctx = ShellContext(registry=ModuleRegistry(tmp_path), ui=UI(all_themes({})["nord"], console), config={})
    with shell.Shell(ctx) as sh:
        yield sh, ctx


# ── inside an attached subshell: the wheel ────────────────────────────────────

class _Attached:
    """A subshell running `script` on a pseudo-terminal, attached to a fake terminal we type on."""

    def __init__(self, tmp_path, script, alerts=None):
        import tty

        self.table = JobTable(tmp_path / "jobs")
        self.job = self.table.start_program(["sh", "-c", script], "sh")
        self.master, self.slave = os.openpty()
        tty.setraw(self.slave)
        jobmod._set_winsize(self.slave, 24, 80)
        self.terminal = jobmod.Terminal.on_fds(self.slave, self.slave, raw=False)
        self.footer = Footer(UI(all_themes({})["nord"], Console(file=io.StringIO())), self.table, alerts=alerts)
        self.footer.size = lambda: (24, 80)
        self.seen = bytearray()
        self.result = {}
        self.thread = threading.Thread(
            target=lambda: self.result.update(how=self.table.attach(self.job, terminal=self.terminal,
                                                                    footer=self.footer, raw=False)),
            daemon=True)
        self.thread.start()

    def drain(self, seconds=0.5):
        end = time.time() + seconds
        while time.time() < end:
            if select.select([self.master], [], [], 0.05)[0]:
                self.seen.extend(os.read(self.master, 65536))

    def mouse(self, code, x, y, final="M"):
        os.write(self.master, f"\x1b[<{code};{x};{y}{final}".encode())

    def finish(self):
        os.write(self.master, jobmod.DETACH_KEY + b"x")
        self.thread.join(3)
        self.table.kill(self.job)
        os.close(self.master)
        os.close(self.slave)


@posix_only
def test_wheel_scrolls_the_page_and_a_full_screen_program_gets_arrows(tmp_path):
    a = _Attached(tmp_path, "for i in $(seq 1 60); do echo line$i; done; sleep 30")
    try:
        a.drain(0.8)
        assert a.job.scrolled == 0
        a.mouse(64, 10, 5)  # wheel up: back through the emulated page's scrollback
        a.drain(0.4)
        assert a.job.scrolled == 1
        a.mouse(65, 10, 5)  # wheel down
        a.drain(0.3)
        assert a.job.scrolled == 0
        a.mouse(68, 10, 5)  # wheel up with Shift held (68 = 64 + 4): still up
        a.drain(0.3)
        assert a.job.scrolled == 1
        a.mouse(69, 10, 5)
        a.drain(0.3)
        assert a.job.scrolled == 0
    finally:
        a.finish()
    arrows = _Attached(tmp_path, "printf '\\033[?1049h'; stty -echo -icanon; cat -v")  # an alternate-screen program
    try:
        arrows.drain(0.8)
        assert arrows.job.saved_main is not None
        arrows.seen.clear()
        arrows.mouse(64, 10, 5)  # the wheel becomes three Up arrows for it
        arrows.drain(0.5)
        assert b"^[[A^[[A^[[A" in bytes(arrows.seen)
    finally:
        arrows.finish()


@posix_only
def test_a_mouse_report_split_across_reads_is_not_sent_to_the_program(tmp_path):
    a = _Attached(tmp_path, "stty -echo; cat -v")
    try:
        a.drain(0.5)
        a.seen.clear()
        os.write(a.master, b"\x1b[<0;5")  # the first half of a click report ...
        a.drain(0.3)
        os.write(a.master, b";7M")  # ... and the rest
        a.drain(0.4)
        os.write(a.master, b"ok\n")
        a.drain(0.4)
        assert b"ok" in a.seen and b"[<0" not in bytes(a.seen) and b";7M" not in bytes(a.seen)
    finally:
        a.finish()


# ── the scroll view at the prompt ────────────────────────────────────────────

def test_scrollback_keeps_output_without_cursor_moves():
    from shellcraft.scrollback import Scrollback

    sb = Scrollback(max_lines=4)
    sb.feed("\x1b[1;32mgreen\x1b[0m line\n")
    sb.feed("spinner 1\rspinner 2\r\x1b[2Kdone\n")  # a line that rewrote itself
    sb.feed("half")
    sb.command("ls -l")
    sb.feed("a\nb\nc\n")
    text = sb.text()
    assert "\x1b[1;32mgreen\x1b[0m line" not in text  # only the last 4 lines are kept
    assert text.splitlines() == ["half", "\x1b[1m❯\x1b[0m ls -l", "a", "b", "c"][-4:]
    sb.clear()
    assert sb.text() == ""
    sb.feed("x\r\nkeep\n")
    assert sb.text().splitlines() == ["x", "keep"]


def test_the_shell_captures_output_and_the_footer_bypasses_it(repl_shell):
    from shellcraft.output import clear_screen
    from shellcraft.scrollback import CaptureFile, terminal_file

    sh, ctx = repl_shell
    assert isinstance(sh.ui.console.file, CaptureFile)
    sh.ui.console.print("hello from a command")
    assert "hello from a command" in sh.scrollback.text()
    sh.footer.size = lambda: (24, 80)
    sh.footer.enabled = True
    sh.footer.draw()  # drawing, not output: it reaches the terminal, not the scroll view
    assert "main" not in sh.scrollback.text() and "main" in terminal_file(sh.ui.console.file).getvalue()
    clear_screen(sh.ui)
    assert sh.scrollback.text() == ""  # clear empties it, like the terminal's history


def test_wheel_at_the_prompt_opens_the_scroll_view(repl_shell, monkeypatch):
    from types import SimpleNamespace

    from shellcraft import output
    from shellcraft import shell as shell_module
    from shellcraft.footer import wheel_up

    assert wheel_up("\x1b[<64;5;5M") and not wheel_up("\x1b[<65;5;5M")
    assert wheel_up("NONE;SCROLL_UP;3;4") and not wheel_up("NONE;SCROLL_DOWN;3;4")
    sh, ctx = repl_shell
    opened = []
    monkeypatch.setattr(output, "page", lambda ui, text, title=None, **kw: opened.append((text, kw)))
    monkeypatch.setattr(shell_module, "run_in_terminal", lambda func: func())
    sh.ui.console.print("output to scroll back to")
    keys = [SimpleNamespace(key=Keys.Vt100MouseEvent)]
    assert sh._footer_mouse(SimpleNamespace(data="\x1b[<65;5;5M", key_sequence=keys)) is None
    assert opened == []  # down: nothing to do
    assert sh._footer_mouse(SimpleNamespace(data="\x1b[<64;5;5M", key_sequence=keys)) is None
    assert "output to scroll back to" in opened[0][0]
    assert opened[0][1] == {"at_end": 3, "close_past_end": True}


def test_the_pager_opens_at_the_end_and_closes_past_it():
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.data_structures import Size
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    from shellcraft.output import page

    text = "\n".join(f"line {i}" for i in range(60))

    def run(keys, **kw):
        with create_pipe_input() as pipe:
            pipe.send_text(keys)
            out = DummyOutput()
            out.get_size = lambda: Size(rows=11, columns=40)
            with create_app_session(input=pipe, output=out):
                return page(UI(all_themes({})["nord"], Console(file=io.StringIO())), text, **kw)

    assert run("q", at_end=3)["top"] == 60 - 10 - 3  # three lines above the end
    assert run("jjjjq", at_end=3, close_past_end=True)["top"] == 50  # down to the end, then one more closes
    assert run("jjjjjjq")["top"] == 6  # a normal pager: j just stops at the end


# ── who has the mouse at the prompt ──────────────────────────────────────────

def test_the_prompt_takes_the_mouse_only_while_something_is_clickable(repl_shell):
    sh, ctx = repl_shell
    assert not sh._mouse_wanted()  # nothing to click: the terminal keeps it (its wheel, its selection)
    ctx.alerts.post("disk", "warning")
    assert sh._mouse_wanted() and sh.session.app.mouse_support() is True  # clicking the ✖, the badge
    ctx.config.setdefault("settings", {})["footer_mouse"] = False
    assert not sh._mouse_wanted()  # off: the terminal keeps it, the keys still work
    del ctx.config["settings"]["footer_mouse"]


# ── pasting the clip ─────────────────────────────────────────────────────────

def _feed(sh, *keys):
    import asyncio

    app = sh.session.app

    async def go():
        with set_app(app):
            app.key_processor.feed_multiple([KeyPress(*k) for k in keys])
            app.key_processor.process_keys()

    asyncio.run(go())


def test_alt_v_and_clip_i_paste_the_clip(repl_shell):
    sh, ctx = repl_shell
    ctx.alerts.clip = "/var/log/app.log\n"
    buffer = sh.session.default_buffer
    # exactly what a Windows console's classic key reader sends for Alt+v: Escape, then v
    _feed(sh, (Keys.Escape, ""), ("v", "v"))
    assert buffer.text == "/var/log/app.log"
    buffer.reset()
    _feed(sh, ("v", "v"))  # a plain v is only a v
    assert buffer.text == "v"
    buffer.reset()
    ctx.alerts.clip = ""
    _feed(sh, (Keys.Escape, ""), ("v", "v"))
    assert buffer.text == ""  # nothing to paste
    ctx.interactive = True
    with pytest.raises(PipelineError, match="register is empty"):
        run_line("clip -i", ctx)
    ctx.alerts.clip = "echo pasted"
    assert "next prompt starts" in to_text(run_line("clip -i", ctx).output) and ctx.prefill == "echo pasted"
    seen = []
    sh.session.prompt = lambda message, default="": seen.append(default) or "line"
    assert sh._ask(sh._prompt) == "line" and seen == ["echo pasted"] and ctx.prefill == ""
    assert sh._ask(sh._prompt) == "line" and seen[-1] == ""  # only once


def test_alt_v_again_pastes_the_older_copies_in_turn(repl_shell):
    sh, ctx = repl_shell
    for text in ("oldest", "middle", "newest"):
        ctx.alerts.clip = text
    buffer = sh.session.default_buffer
    _feed(sh, *[(ch, ch) for ch in "cat "])
    alt_v = [(Keys.Escape, ""), ("v", "v")]
    _feed(sh, *alt_v)
    assert buffer.text == "cat newest"
    _feed(sh, *alt_v)
    assert buffer.text == "cat middle"  # again right away: the next older copy, in its place
    _feed(sh, *alt_v)
    assert buffer.text == "cat oldest"
    _feed(sh, *alt_v)
    assert buffer.text == "cat newest"  # round again
    _feed(sh, (" ", " "))
    _feed(sh, *alt_v)
    assert buffer.text == "cat newest newest"  # typed in between: a fresh paste of the newest


def test_right_alt_and_altgr_count_as_alt_on_windows():
    from shellcraft.console import LEFT_ALT_PRESSED, LEFT_CTRL_PRESSED, RIGHT_ALT_PRESSED, SHIFT_PRESSED, right_alt_key

    V = 0x56
    assert right_alt_key(RIGHT_ALT_PRESSED, V, "v") == "v"  # right Alt + v: Alt-v
    assert right_alt_key(RIGHT_ALT_PRESSED | LEFT_CTRL_PRESSED, V, "\x16") == "v"  # AltGr + v: Ctrl+Alt+v
    assert right_alt_key(RIGHT_ALT_PRESSED | LEFT_CTRL_PRESSED, V, "\x00") == "v"  # AltGr + v typing nothing
    assert right_alt_key(RIGHT_ALT_PRESSED | SHIFT_PRESSED, V, "V") == "V"
    assert right_alt_key(RIGHT_ALT_PRESSED | LEFT_CTRL_PRESSED, 0x51, "@") is None  # AltGr+q types @ (German)
    assert right_alt_key(RIGHT_ALT_PRESSED | LEFT_CTRL_PRESSED, 0x45, "€") is None  # AltGr+e types €
    assert right_alt_key(LEFT_ALT_PRESSED, V, "v") is None  # the left Alt: prompt_toolkit handles it
    assert right_alt_key(0, V, "v") is None  # no Alt
    assert right_alt_key(RIGHT_ALT_PRESSED, 0x0D, "\r") is None  # not a letter or a digit
    assert right_alt_key(RIGHT_ALT_PRESSED, 0x32, "2") == "2"
    assert right_alt_key(RIGHT_ALT_PRESSED | SHIFT_PRESSED, 0x32, "@") is None


def test_esc_ctrl_v_pastes_too(repl_shell):
    sh, ctx = repl_shell
    ctx.alerts.clip = "/var/log/app.log"
    _feed(sh, (Keys.Escape, ""), (Keys.ControlV, "\x16"))  # Ctrl+Alt+v, as AltGr+v arrives
    assert sh.session.default_buffer.text == "/var/log/app.log"


def test_diag_keys_shows_what_each_key_arrives_as():
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    from shellcraft.output import key_test, windows_flags

    assert windows_flags(0x0001 | 0x0008) == "right-alt+left-ctrl" and windows_flags(0) == "none"
    with create_pipe_input() as pipe:
        pipe.send_text("\x1bv" + "a" + "\x1b\x16" + "\r")
        with create_app_session(input=pipe, output=DummyOutput()):
            lines = key_test(UI(all_themes({})["nord"], Console(file=io.StringIO())))
    assert lines == ["escape v", "a", "escape c-v"]


# ── the hover highlight goes away ────────────────────────────────────────────

def test_the_footer_highlight_clears_when_the_mouse_leaves_and_after_a_second(tmp_path, monkeypatch):
    from shellcraft import footer as footer_mod
    from shellcraft.jobs import JobTable

    table = JobTable(tmp_path / "jobs")
    job = table.start_command("sleep", lambda: (time.sleep(5), (0, ""))[1])
    footer = Footer(UI(all_themes({})["nord"], Console(file=io.StringIO())), table)
    footer.size = lambda: (24, 80)
    footer.enabled = True
    footer.sequence()
    changes = []
    footer.on_change = lambda: changes.append(1)
    job_row = 24  # main on 23, the subshell on the last row
    assert footer.mouse("move", 5, job_row, "none") == ("handled", None) and footer.hover == job.number
    assert footer.mouse("move", 5, 10, "none") is None  # over the output: still not ours …
    assert footer.hover is None  # … but the highlight is gone
    # without moving away: it goes by itself after HOVER_TIMEOUT
    monkeypatch.setattr(footer_mod, "HOVER_TIMEOUT", 0.2)
    footer.mouse("move", 5, job_row, "none")
    assert footer.hover == job.number
    footer.dirty = False
    assert _wait_for(lambda: footer.hover is None, 2) and footer.dirty and changes
    # still moving over it: the second starts again
    footer.mouse("move", 5, job_row, "none")
    time.sleep(0.12)
    footer.mouse("move", 6, job_row, "none")
    time.sleep(0.12)
    assert footer.hover == job.number  # 0.24 s after the first move, 0.12 after the last
    # a click on the fold control leaves nothing lit
    col = footer.rows[0].text.index("▾") + 1
    footer.mouse("move", col, job_row - 1, "none")
    assert footer.mouse("up", col, job_row - 1, "left") == ("handled", None)
    assert footer.hover is None and footer.collapsed
    footer.disable()
    table.kill(job)


def _wait_for(predicate, seconds):
    end = time.time() + seconds
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_the_prompt_panel_highlight_clears_after_a_second(repl_shell, monkeypatch):
    from shellcraft import footer as footer_mod

    sh, ctx = repl_shell
    monkeypatch.setattr(footer_mod, "HOVER_TIMEOUT", 0.2)
    assert sh._set_panel_hover(1) is None and sh._panel_hover == 1
    assert _wait_for(lambda: sh._panel_hover is None, 2)
    assert sh.footer.dirty  # a redraw was asked for
