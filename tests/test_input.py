"""Mouse and paste input: the host clipboard, drag selection and right-click copy inside an attached
subshell, the wheel, the prompt keeping the mouse for the terminal, and the ways to paste the clip."""

import base64
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

from shellcraft import hostclip
from shellcraft import jobs as jobmod
from shellcraft.context import ShellContext, to_text
from shellcraft.footer import Footer
from shellcraft.jobs import JobTable, Selection, new_screen, selection_overlay, selection_text
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


def _osc52_texts(data: bytes) -> list[str]:
    out, text = [], data.decode("utf-8", "replace")
    while "\x1b]52;c;" in text:
        _, _, rest = text.partition("\x1b]52;c;")
        payload, _, text = rest.partition("\x07")
        out.append(base64.b64decode(payload).decode())
    return out


# ── the host clipboard ───────────────────────────────────────────────────────

def test_osc52_carries_the_text_and_copy_writes_it(monkeypatch):
    seq = hostclip.osc52("héllo\nworld")
    assert seq.startswith("\x1b]52;c;") and seq.endswith("\x07")
    assert base64.b64decode(seq[7:-1]).decode() == "héllo\nworld"
    sent = []
    assert hostclip.copy("abc", sent.append) is True and sent == [hostclip.osc52("abc")]
    assert hostclip.copy("", sent.append) is False and len(sent) == 1
    big = hostclip.osc52("x" * 200_000)
    assert len(base64.b64decode(big[7:-1])) == hostclip.MAX_BYTES  # capped for the terminal's limit

    def broken(_text):
        raise OSError("closed")

    assert hostclip.copy("abc", broken) is False
    # Windows without the Win32 libraries (here: Linux): that route fails quietly, OSC 52 stands
    monkeypatch.setattr(sys, "platform", "win32")
    sent.clear()
    assert hostclip.copy("abc", sent.append) is True and sent == [hostclip.osc52("abc")]


# ── selecting on the emulated page ───────────────────────────────────────────

def test_selection_text_and_overlay():
    screen, stream = new_screen(4, 20)
    stream.feed(b"hello world\r\nsecond line\r\n\r\nlast")
    sel = Selection(0, 6)
    sel.end = (1, 5)  # from "world" to the end of "second"
    assert selection_text(screen, sel) == "world\nsecond"
    backwards = Selection(1, 5)
    backwards.end = (0, 6)  # dragged up and to the left: the same text
    assert selection_text(screen, backwards) == "world\nsecond"
    one = Selection(0, 0)
    one.end = (0, 4)
    assert selection_text(screen, one) == "hello" and one.moved and not Selection(0, 0).moved
    whole = Selection(0, 0)
    whole.end = (3, 19)
    assert selection_text(screen, whole) == "hello world\nsecond line\n\nlast"
    overlay = selection_overlay(screen, sel).decode()
    assert "\x1b[1;7H\x1b[0;7mworld" in overlay and "\x1b[2;1H\x1b[0;7msecond" in overlay
    assert overlay.endswith("\x1b[0m\x1b[4;5H")  # the cursor goes back where the program had it


# ── inside an attached subshell: drag, right-click copy, wheel ───────────────

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
def test_drag_highlights_and_right_click_copies_to_the_host_clipboard(tmp_path):
    from shellcraft.alerts import AlertBus

    bus = AlertBus()
    a = _Attached(tmp_path, "printf 'hello world\\nsecond line\\n'; sleep 30", alerts=bus)
    try:
        a.drain(0.8)
        assert b"hello world" in a.seen
        a.seen.clear()
        a.mouse(0, 7, 1)  # left button down on the "w" of "world" ...
        a.mouse(32, 9, 1)  # ... drag (button held) ...
        a.mouse(32, 6, 2)
        a.mouse(0, 6, 2, "m")  # ... released: highlighted, nothing copied yet
        a.drain(0.6)
        assert b"\x1b[0;7mworld" in a.seen and _osc52_texts(bytes(a.seen)) == []
        a.seen.clear()
        a.mouse(2, 6, 2)  # right button down and up: copy
        a.mouse(2, 6, 2, "m")
        a.drain(0.6)
        assert _osc52_texts(bytes(a.seen)) == ["world\nsecond"]
        assert any("copied 12 characters" in alert.message for alert in bus.current())
        a.seen.clear()
        a.mouse(2, 6, 2)  # nothing selected any more: a right click does nothing
        a.mouse(2, 6, 2, "m")
        a.drain(0.3)
        assert _osc52_texts(bytes(a.seen)) == []
        a.mouse(0, 3, 1)  # a plain click selects nothing
        a.mouse(0, 3, 1, "m")
        a.mouse(0, 1, 1)  # a highlight, then a key: the page is redrawn without it
        a.mouse(32, 4, 1)
        a.mouse(0, 4, 1, "m")
        a.drain(0.4)
        assert b"\x1b[0;7m" in a.seen
        a.seen.clear()
        os.write(a.master, b"x")
        a.drain(0.5)
        assert b"\x1b[0;7m" not in a.seen
        assert a.thread.is_alive() and a.job.running
    finally:
        a.finish()
        bus.close()


@posix_only
def test_a_drag_released_over_the_footer_does_not_switch_shells(tmp_path):
    a = _Attached(tmp_path, "printf 'one two\\n'; sleep 30")
    try:
        a.drain(0.6)
        a.footer.enabled = True
        a.footer.sequence()
        a.mouse(0, 1, 1)
        a.mouse(32, 4, 1)
        a.mouse(0, 5, 24, "m")  # released on the footer's last row (the subshell's own line)
        a.drain(0.4)
        assert a.thread.is_alive() and not a.result  # still attached: that was not a click on the footer
        a.footer.enabled = False
    finally:
        a.finish()


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


# ── the prompt keeps the mouse for the terminal ─────────────────────────────

def test_the_prompt_leaves_the_mouse_to_the_terminal_unless_asked(repl_shell):
    sh, ctx = repl_shell
    ctx.alerts.post("disk", "warning")
    assert not sh._mouse_wanted() and sh.session.app.mouse_support() is False  # wheel and selection: no Shift
    ctx.config.setdefault("settings", {})["footer_mouse"] = True
    assert sh._mouse_wanted()
    del ctx.config["settings"]["footer_mouse"]
    sh.ui.mouse_always = True
    assert sh._mouse_wanted()
    sh.ui.mouse_always = False


# ── pasting the clip ─────────────────────────────────────────────────────────

def _feed(sh, *keys):
    import asyncio

    app = sh.session.app

    async def go():
        with set_app(app):
            app.key_processor.feed_multiple([KeyPress(*k) for k in keys])
            app.key_processor.process_keys()

    asyncio.run(go())


def test_alt_v_ctrl_bracket_v_and_clip_i_paste_the_clip(repl_shell):
    sh, ctx = repl_shell
    ctx.alerts.clip = "/var/log/app.log\n"
    buffer = sh.session.default_buffer
    # exactly what a Windows console's classic key reader sends for Alt+v: Escape, then v
    _feed(sh, (Keys.Escape, ""), ("v", "v"))
    assert buffer.text == "/var/log/app.log"
    buffer.reset()
    _feed(sh, (Keys.ControlSquareClose, "\x1d"), ("v", "v"))  # Ctrl-] v: no Alt involved
    assert buffer.text == "/var/log/app.log"
    buffer.reset()
    _feed(sh, ("v", "v"))  # a plain v is only a v
    assert buffer.text == "v"
    buffer.reset()
    ctx.alerts.clip = ""
    _feed(sh, (Keys.Escape, ""), ("v", "v"))
    assert buffer.text == ""  # nothing to paste
    # clip -i: the next prompt starts with it, on any terminal
    ctx.interactive = True
    with pytest.raises(PipelineError, match="register is empty"):
        run_line("clip -i", ctx)
    ctx.alerts.clip = "echo pasted"
    assert "next prompt starts" in to_text(run_line("clip -i", ctx).output) and ctx.prefill == "echo pasted"
    seen = []
    sh.session.prompt = lambda message, default="": seen.append(default) or "line"
    assert sh._ask(sh._prompt) == "line" and seen == ["echo pasted"] and ctx.prefill == ""
    assert sh._ask(sh._prompt) == "line" and seen[-1] == ""  # only once


def test_clip_host_and_alert_copies_reach_the_host_clipboard(repl_shell):
    sh, ctx = repl_shell
    ctx.interactive = True
    raw = io.StringIO()
    ctx.ui.console.file = raw
    ctx.alerts.post("errors in the log", "warning", content="/var/log/app.log")
    assert "copied" in to_text(run_line("clip -a 1", ctx).output)
    assert _osc52_texts(raw.getvalue().encode()) == ["/var/log/app.log"] and ctx.alerts.clip == "/var/log/app.log"
    raw.truncate(0)
    raw.seek(0)
    assert "6 characters" in to_text(run_line("clip -H hello!", ctx).output) and ctx.alerts.clip == "hello!"
    assert _osc52_texts(raw.getvalue().encode()) == ["hello!"]
    raw.truncate(0)
    raw.seek(0)
    run_line("clip plain", ctx)  # setting the register alone does not touch the host's clipboard
    assert _osc52_texts(raw.getvalue().encode()) == []
    sh._host_copy("via the handler")
    assert _osc52_texts(raw.getvalue().encode()) == ["via the handler"]
    ctx.alerts.clip = ""
    with pytest.raises(PipelineError, match="nothing to copy"):
        run_line("clip -H", ctx)
