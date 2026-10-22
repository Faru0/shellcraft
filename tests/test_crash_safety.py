"""Crash safety: Ctrl-C (and any unexpected error) never ends the session; only Ctrl-D or `exit`.

Fault injection on the real REPL loop, modules that call sys.exit(), Ctrl-C during startup and in
funcs, the crash log, and a Ctrl-C fuzzer in a real pseudo-terminal."""

import json
import os
import select
import signal
import sys
import threading
import time
from pathlib import Path

import anyio
import pytest
from rich.console import Console

from shellcraft import crashlog, funcs, shell, startup
from shellcraft.context import ShellContext, to_text
from shellcraft.loader import ModuleRegistry
from shellcraft.pipeline import PipelineError, run_line
from shellcraft.themes import UI, all_themes

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX pty")
ROOT = Path(__file__).resolve().parents[1]

MODULES = {
    "exit0": "import sys\ndef run(args, stdin):\n    print('partial', file=sys.stderr)\n    sys.exit(0)\n",
    "exit3": "import sys\ndef run(args, stdin):\n    sys.exit(3)\n",
    "exitmsg": "import sys\ndef run(args, stdin):\n    sys.exit('bad input')\n",
    "argy": ("import argparse\ndef run(args, stdin):\n    p = argparse.ArgumentParser(prog='argy')\n"
             "    p.add_argument('target')\n    return p.parse_args(args).target\n"),
    "streamexit": "import sys\ndef run(args, stdin):\n    yield 'one'\n    sys.exit(4)\n",
    "interrupt": "def run(args, stdin):\n    raise KeyboardInterrupt\n",
    "slow": "import time\ndef run(args, stdin):\n    time.sleep(3)\n    return 'done'\n",
}


@pytest.fixture
def modules(tmp_path):
    folder = tmp_path / "modules"
    folder.mkdir()
    for name, code in MODULES.items():
        (folder / f"{name}.py").write_text(code)
        (folder / f"{name}.skill").write_text(f'summary = "{name}"\n')
    return folder


@pytest.fixture
def repl(monkeypatch, tmp_path, modules):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(shell, "create_prompt_toolkit_console", lambda cleanup: None)
    console = Console(record=True, width=120, force_terminal=False, color_system=None)
    registry = ModuleRegistry(modules)
    registry.load()
    ctx = ShellContext(registry=registry, ui=UI(all_themes({})["nord"], console), config={})
    with shell.Shell(ctx) as sh:
        yield sh, ctx, console


def scripted(sh, monkeypatch, *steps):
    """The prompt answers `steps` in turn (a line, or an exception to raise), then Ctrl-D."""
    queue = list(steps)

    def ask(message):
        item = queue.pop(0) if queue else EOFError()
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(sh, "_ask", ask)


def raise_once(exc, original=None):
    """Raises `exc` the first time, then calls `original` (or does nothing)."""
    state = {"raised": False}

    def fn(*args, **kwargs):
        if not state["raised"]:
            state["raised"] = True
            raise exc
        return original(*args, **kwargs) if original is not None else None
    return fn


def within(seconds, fn):
    """fn() in a thread; fails (instead of hanging the suite) when it takes longer than `seconds`."""
    box = {}

    def target():
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 — shown by the assertion below
            box["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(seconds)
    assert not thread.is_alive(), f"still running after {seconds} s (a hang)"
    if "error" in box:
        raise box["error"]
    return box.get("value")


# ── the loop ends only on Ctrl-D or exit ─────────────────────────────────────

@pytest.mark.parametrize("exc", [KeyboardInterrupt(), RuntimeError("boom")])
@pytest.mark.parametrize("step", ["_open_requests", "_hot_reload", "_remember"])
def test_a_failure_in_any_step_never_ends_the_loop(repl, monkeypatch, step, exc):
    sh, ctx, console = repl
    monkeypatch.setattr(sh, step, raise_once(exc))
    scripted(sh, monkeypatch, "x=1", "y=2")
    assert sh.loop() == 0  # only the Ctrl-D at the end returned
    assert ctx.variables.get("y") == "2"


@pytest.mark.parametrize("exc", [KeyboardInterrupt(), RuntimeError("boom")])
def test_history_expansion_and_showing_results_can_fail(repl, monkeypatch, exc):
    sh, ctx, console = repl
    monkeypatch.setattr(shell.histexpand, "expand", raise_once(exc, shell.histexpand.expand))
    monkeypatch.setattr(shell, "show", raise_once(exc, shell.show))
    scripted(sh, monkeypatch, "echo one", "echo two", "z=3")
    assert sh.loop() == 0 and ctx.variables.get("z") == "3"


def test_ctrl_c_inside_the_interrupt_handler_does_not_escape(repl, monkeypatch):
    sh, ctx, console = repl
    monkeypatch.setattr(shell, "run_line", raise_once(KeyboardInterrupt()))
    printed = console.print
    calls = {"n": 0}

    def flaky_print(*args, **kwargs):  # the second Ctrl-C lands while `^C interrupted` prints
        calls["n"] += 1
        if calls["n"] == 1:
            raise KeyboardInterrupt
        return printed(*args, **kwargs)

    monkeypatch.setattr(console, "print", flaky_print)
    scripted(sh, monkeypatch, "sleep 5")
    assert sh.loop() == 0 and ctx.last_status == 130


def test_ctrl_c_inside_the_error_handler_does_not_escape(repl, monkeypatch):
    sh, ctx, console = repl
    monkeypatch.setattr(shell, "show_error", raise_once(KeyboardInterrupt(), shell.show_error))
    scripted(sh, monkeypatch, "nosuchcommand", "w=1")
    assert sh.loop() == 0 and ctx.variables.get("w") == "1"


def test_sys_exit_from_a_command_is_reported_not_obeyed(repl, monkeypatch):
    sh, ctx, console = repl
    monkeypatch.setattr(shell, "run_line", raise_once(SystemExit(2)))
    scripted(sh, monkeypatch, "anything")
    assert sh.loop() == 0
    assert "tried to exit ShellCraft" in console.export_text()


def test_a_failing_prompt_falls_back_to_a_plain_one(repl, monkeypatch):
    sh, ctx, console = repl
    scripted(sh, monkeypatch, RuntimeError("Event loop stopped"), RuntimeError("again"), RuntimeError("and again"))
    monkeypatch.setattr("builtins.input", lambda prompt: "v=9")
    assert sh.loop() == 0 and ctx.variables.get("v") == "9"
    assert "the prompt keeps failing" in console.export_text()
    assert "Event loop stopped" in crashlog.path().read_text()


def test_exit_and_ctrl_d_still_end_it(repl, monkeypatch):
    sh, ctx, console = repl
    scripted(sh, monkeypatch, KeyboardInterrupt(), "exit 4")
    assert sh.loop() == 4
    scripted(sh, monkeypatch)
    assert sh.loop() == 0


def test_interrupts_are_dropped_between_commands_and_raised_during_them(repl):
    sh, ctx, console = repl
    if threading.current_thread() is not threading.main_thread():
        pytest.skip("signals need the main thread")
    with sh._interrupts_deferred():
        os.kill(os.getpid(), signal.SIGINT) if sys.platform != "win32" else signal.raise_signal(signal.SIGINT)
        time.sleep(0.05)  # dropped: nothing raised here
        with pytest.raises(KeyboardInterrupt), sh._interruptible():
            signal.raise_signal(signal.SIGINT)
            time.sleep(0.05)
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler


# ── modules that call sys.exit() ─────────────────────────────────────────────

def test_modules_that_exit_never_hang_or_end_the_shell(modules):
    registry = ModuleRegistry(modules)
    registry.load()
    ctx = ShellContext(registry=registry, config={})
    assert within(5, lambda: to_text(run_line("exit0", ctx).output)) == ""
    for line, message in (("exit3", "exited with status 3"), ("exitmsg", "bad input"),
                          ("argy", "exited with status 2"), ("argy --help", None)):
        def attempt(line=line):
            try:
                return to_text(run_line(line, ctx).output)
            except PipelineError as exc:
                return f"ERROR {exc}"
        text = within(5, attempt)
        if message:
            assert message in text, (line, text)
    with pytest.raises(PipelineError, match="status 4"):
        within(5, lambda: run_line("streamexit", ctx))


def test_a_module_that_exits_returns_a_tool_error_over_mcp(modules):
    from mcp.client import Client

    from shellcraft.mcp_server import build_server

    registry = ModuleRegistry(modules)
    registry.load()
    server = build_server(registry)

    async def main():
        async with Client(server) as client:
            return await client.call_tool("exit3", {"args": []})

    result = within(10, lambda: anyio.run(main))
    assert result.is_error and "exited with status 3" in result.content[0].text


def test_a_module_that_exits_while_imported_is_a_warning(tmp_path):
    (tmp_path / "bad.py").write_text("import sys\nsys.exit(1)\ndef run(args, stdin):\n    return ''\n")
    registry = ModuleRegistry(tmp_path, lazy=False)
    registry.load()
    assert any("exits while being imported" in w for w in registry.warnings)


# ── startup, funcs, the crash log ────────────────────────────────────────────

def ctrl_c_on(monkeypatch, word):
    """A real Ctrl-C arrives on the main thread while the line `word` runs."""
    from shellcraft import pipeline

    real = pipeline.run_line

    def run(line, ctx):
        if line.strip() == word:
            raise KeyboardInterrupt
        return real(line, ctx)

    monkeypatch.setattr(pipeline, "run_line", run)


def test_ctrl_c_in_an_rc_file_stops_the_file_not_the_shell(modules, tmp_path, monkeypatch):
    ctrl_c_on(monkeypatch, "interrupt")
    registry = ModuleRegistry(modules)
    registry.load()
    shown = []
    ctx = ShellContext(registry=registry, config={}, emit=shown.append, emit_error=lambda e, line: shown.append(str(e)))
    rc = tmp_path / "rc"
    rc.write_text("a=1\ninterrupt\nb=2\n")
    ran, failed = startup.source(rc, ctx)
    assert ctx.variables.get("a") == "1" and "b" not in ctx.variables
    assert any("interrupted" in str(x) for x in shown) and ctx.last_status == 130


def test_cli_reaches_the_prompt_after_an_interrupted_startup(monkeypatch, tmp_path, modules):
    from shellcraft import cli

    looped = []

    class FakeShell:
        def __init__(self, ctx):
            self.ctx = ctx

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

        def run_rc(self):
            raise KeyboardInterrupt

        def loop(self):
            looped.append(True)
            return 0

    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path))
    (tmp_path / "rc").write_text("sleep 5\n")
    monkeypatch.setattr(shell, "Shell", FakeShell)
    assert cli.main(["--no-banner", "--modules", str(modules)]) == 0 and looped == [True]


def test_a_func_that_runs_itself_stops_at_the_depth_limit(modules):
    registry = ModuleRegistry(modules)
    registry.load()
    ctx = ShellContext(registry=registry, config={})
    run_line("func new loopy --command 'loopy'", ctx)
    result = within(10, lambda: run_line("func run loopy", ctx))
    assert result.status != 0 and f"nested more than {funcs.MAX_DEPTH} deep" in to_text(result.output)


def test_ctrl_c_in_a_func_stops_it_at_that_step(modules, monkeypatch):
    ctrl_c_on(monkeypatch, "interrupt")
    registry = ModuleRegistry(modules)
    registry.load()
    ctx = ShellContext(registry=registry, config={})
    run_line("func new stopper 'a=1' 'interrupt' 'b=2'", ctx)
    result = run_line("func run stopper", ctx)
    assert result.status == 130 and "interrupted" in to_text(result.output) and "b" not in ctx.variables


def test_the_crash_log_is_private_trimmed_and_fed_by_threads(monkeypatch):
    try:
        raise ValueError("first problem")
    except ValueError as exc:
        path = crashlog.record(exc, "a test")
    assert path is not None and "ValueError: first problem" in crashlog.last()
    if sys.platform != "win32":
        assert path.stat().st_mode & 0o777 == 0o600
    monkeypatch.setattr(crashlog, "MAX_BYTES", 2000)
    for i in range(20):
        crashlog.record(RuntimeError(f"error {i} " + "x" * 200), "loop")
    assert path.stat().st_size <= 2100 and "error 19" in crashlog.last()
    previous = threading.excepthook
    crashlog.install_thread_hook()
    try:
        worker = threading.Thread(target=lambda: 1 / 0, name="worker-x")
        worker.start()
        worker.join()
    finally:
        threading.excepthook = previous
    assert "background thread worker-x" in path.read_text() and "ZeroDivisionError" in path.read_text()


# ── a Ctrl-C fuzzer in a real terminal ───────────────────────────────────────

@posix_only
@pytest.mark.filterwarnings("ignore:This process .* is multi-threaded, use of forkpty")  # the child execs at once
@pytest.mark.parametrize("command, gap", [("slow", 0.0), ("seq 1 2000000 | grep 7", 0.03), ("exit3", 0.0)])
def test_ctrl_c_bursts_never_close_the_shell(tmp_path, modules, command, gap):
    import fcntl
    import pty
    import struct
    import termios

    home = tmp_path / "home"
    home.mkdir()
    (home / "config.json").write_text(json.dumps({"settings": {"clear_on_start": False}}))
    env = dict(os.environ, SHELLCRAFT_HOME=str(home), TERM="xterm-256color")
    pid, fd = pty.fork()
    if pid == 0:
        os.execve(sys.executable, [sys.executable, str(ROOT / "main.py"), "--no-banner", "--modules",
                                   str(modules)], env)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 100, 0, 0))
    seen = bytearray()

    def pump(seconds):
        end = time.time() + seconds
        while time.time() < end:
            ready, _, _ = select.select([fd], [], [], 0.02)
            if ready:
                try:
                    data = os.read(fd, 65536)
                except OSError:
                    return False
                if not data:
                    return False
                if b"\x1b[6n" in data:
                    os.write(fd, b"\x1b[1;1R")
                seen.extend(data)
        return True

    try:
        assert pump(2.5)
        os.write(fd, command.encode() + b"\r")
        pump(0.4)
        for _ in range(6):
            os.write(fd, b"\x03")
            pump(gap)
        pump(0.8)
        os.write(fd, b"echo ALIVE-$((40+2))\r")
        alive = pump(1.5) and b"ALIVE-42" in seen
        assert alive, f"the shell did not answer after Ctrl-C bursts (gap {gap})"
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        except OSError:
            pass
        os.close(fd)
