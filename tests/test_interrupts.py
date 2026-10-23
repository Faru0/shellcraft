"""Ctrl-C at the wrong moment: no traceback at startup, no junk at the next prompt, no ^Z."""

import os
import sys
import time

import pytest
from rich.console import Console

from shellcraft import shell as shellmod
from shellcraft import typeahead
from shellcraft.context import ShellContext
from shellcraft.loader import ModuleRegistry
from shellcraft.themes import UI, all_themes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
posix_only = pytest.mark.skipif(os.name == "nt", reason="a pty is needed")
pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")  # os.forkpty in a process with threads


def test_keep_drops_cursor_replies_and_mouse_reports_and_keeps_the_typing():
    kept, dropped = typeahead.keep("\x1b[<0;10;5M\x1b[<0;10;5m\x1b[<64;10;5M\x1b[12;34Recho hi\n")
    assert dropped == 4
    assert "".join(k.data for k in kept) == "echo hi\n"
    kept, dropped = typeahead.keep("\x1b[200~pasted\n\x1b[201~")
    assert dropped == 0 and [k.key for k in kept][0] == "<bracketed-paste>"


@posix_only
def test_collect_hands_the_prompt_only_the_real_keys():
    import pty

    from prompt_toolkit.input.typeahead import get_typeahead
    from prompt_toolkit.input.vt100 import Vt100Input

    master, slave = pty.openpty()
    inp = Vt100Input(os.fdopen(slave, "r"))
    os.write(master, b"\x1b[<0;10;5M\x1b[12;34Recho hi\n")
    time.sleep(0.1)
    kept, dropped, sample = typeahead.collect(inp, slave)
    assert (kept, dropped) == (8, 2) and "<" in sample  # the sample shows a report, for diag
    assert "".join(k.data for k in get_typeahead(inp)) == "echo hi\n"
    assert typeahead.collect(inp, slave) == (0, 0, "")  # nothing left


@posix_only
def test_keep_output_on_interrupt_sets_noflsh_and_restores():
    import pty
    import termios

    from shellcraft.shell import keep_output_on_interrupt, restore_tty

    master, slave = pty.openpty()
    before = termios.tcgetattr(slave)
    saved = keep_output_on_interrupt(slave)
    assert termios.tcgetattr(slave)[3] & termios.NOFLSH
    restore_tty(saved)
    assert termios.tcgetattr(slave) == before
    assert keep_output_on_interrupt(os.open(os.devnull, os.O_RDONLY)) is None


@pytest.fixture
def sh(monkeypatch, tmp_path):
    import io

    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(shellmod, "create_prompt_toolkit_console", lambda cleanup: None)
    console = Console(file=io.StringIO(), width=120, force_terminal=False, color_system=None)
    ctx = ShellContext(registry=ModuleRegistry(tmp_path), ui=UI(all_themes({})["nord"], console), config={})
    with shellmod.Shell(ctx) as s:
        yield s


def test_ctrl_z_is_bound_to_nothing(sh):
    from prompt_toolkit.keys import Keys

    assert any(b.keys == (Keys.ControlZ,) for b in sh._key_bindings().bindings)


def test_a_dropped_interrupt_ends_its_line_before_the_prompt(sh):
    assert not sh._interrupt_dropped
    sh._dropped_interrupt(2, None)
    assert sh._interrupt_dropped
    sh._before_prompt()
    assert sh.ui.console.file.getvalue().endswith("\n") and not sh._interrupt_dropped


@posix_only
def test_ctrl_c_during_startup_exits_quietly(tmp_path):
    import pty
    import select

    tmp_home = str(tmp_path / "home")

    for delay in (0.03, 0.09):
        pid, fd = pty.fork()
        if pid == 0:
            os.chdir(ROOT)
            os.execvpe(sys.executable, [sys.executable, "main.py", "--no-startup", "--no-banner",
                                        "--modules", os.path.join(ROOT, "modules")],
                       dict(os.environ, TERM="xterm-256color", SHELLCRAFT_HOME=tmp_home))
        time.sleep(delay)
        os.write(fd, b"\x03")
        out = b""
        end = time.monotonic() + 15
        while time.monotonic() < end:
            if select.select([fd], [], [], 0.2)[0]:
                try:
                    data = os.read(fd, 65536)
                except OSError:
                    break
                if not data:
                    break
                out += data
            elif os.waitpid(pid, os.WNOHANG)[0]:
                break
        else:
            os.kill(pid, 9)
            pytest.fail("the shell did not exit after Ctrl-C during startup")
        try:
            status = os.waitpid(pid, 0)[1]
        except ChildProcessError:  # already reaped by the WNOHANG poll
            status = 130 << 8
        assert b"Traceback" not in out, out[-500:]
        assert os.waitstatus_to_exitcode(status) == 130


# ── the interrupt log in diag ────────────────────────────────────────────────

def test_the_interrupt_log_says_where_each_ctrl_c_landed_and_what_came_after():
    from shellcraft import interrupts

    interrupts.clear()
    assert "none this session" in interrupts.report_lines()[0]
    interrupts.record("a running command", "seq 1 400000 | grep 1", 1.25)
    interrupts.note_typeahead(0, 2, "'\\x1b[<64;10;5M'")
    interrupts.note_typeahead(3, 0, "")  # a second find within the window goes to a new event only if junk
    lines = "\n".join(interrupts.report_lines())
    assert "a running command after 1.25 s: seq 1 400000 | grep 1" in lines
    assert "0 key(s) kept, 2 report(s) dropped: '\\x1b[<64;10;5M'" in lines
    interrupts.note_typeahead(0, 1, "'\\x1b[12;34R'")  # the event already has its typeahead: a report on its own
    assert "a report at the prompt, no interrupt" in "\n".join(interrupts.report_lines())
    interrupts.clear()


def test_the_shell_records_interrupts_and_diag_reports_them(sh):
    from shellcraft import interrupts

    interrupts.clear()
    sh._dropped_interrupt(2, None)
    sh._interrupted("a running command", "sleep 5", 0.5)
    report = sh.report()
    assert "recent interrupts" in report and "between commands (dropped)" in report
    assert "a running command after 0.50 s: sleep 5" in report
    assert "signal handlers" in report and "SIGINT:" in report
    assert "processes " in report and "python" in report.lower()
    interrupts.clear()


def test_host_summary_and_process_chain_work_here():
    from shellcraft.diagnostics import host_summary, process_chain

    chain = process_chain()
    assert chain and "[" in chain[0]
    assert "processes" in host_summary() and "TERM=" in host_summary()


def test_the_timing_report_ends_with_the_host_line():
    from shellcraft import timing

    assert timing.report().splitlines()[-1].startswith("on: ")


def test_diag_save_writes_the_report_to_a_file(sh, tmp_path):
    from shellcraft.context import to_text
    from shellcraft.pipeline import run_line

    run_line("settings diagnostics on", sh.ctx)
    target = tmp_path / "report.txt"
    out = to_text(run_line(f"diag --save {target}", sh.ctx).output)
    assert "diagnostics written to" in out and "recent interrupts" in target.read_text(encoding="utf-8")
