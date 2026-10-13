"""Subshells: the jobs started with `sub`, listed by `jobs` and shown in the bar under the prompt.

Two kinds of job:

- a **program** (`sub bash`, `sub htop`, `sub shellcraft`): an OS process on its own
  pseudo-terminal, so it can be interactive. `attach()` hands it the real keyboard and screen until
  it exits or the user presses the detach key (Ctrl-]); it keeps running in between. POSIX only:
  Windows has no `pty` module, so there a program runs in the foreground instead (the `sub`
  builtin does that) and is not a job.
- a **command** (`sub grep -r TODO .`, `sub queryDns example.com`): a ShellCraft command line run
  in a worker thread, with its output collected. Selecting it shows the output so far.

Every job writes what it produced to `~/.shellcraft/jobs/N.log` and keeps the last TAIL_BYTES in
memory for a quick look. `JobTable.on_change` is called from worker threads whenever a job's
state changes, so the shell can redraw the bar.
"""

from __future__ import annotations

import os
import re
import select
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import IO, Any, Callable

DETACH_KEY = b"\x1d"  # Ctrl-]: alone it detaches; followed by a digit it switches to that tab
PREFIX_WAIT = 0.6  # seconds to wait for the digit after Ctrl-]
MOUSE_MODE_RE = re.compile(rb"\x1b\[\?(?:1000|1002|1003|1006|1015)[hl]")
MOUSE_SEQ_RE = re.compile(rb"\x1b\[<\d+;\d+;\d+[Mm]")
TAIL_BYTES = 64 * 1024  # kept in memory per job
REPLAY_BYTES = 4096  # shown again when attaching, so a shell's last lines are visible
MAX_FINISHED = 20  # finished jobs kept in the table (the oldest are dropped)


class JobsUnsupported(Exception):
    """Pseudo-terminals are not available here (Windows): run the program in the foreground."""


class Job:
    def __init__(self, number: int, command: str, kind: str, log: Path):
        self.number = number
        self.command = command  # what the user typed after `sub`, for display
        self.kind = kind  # "program" | "command"
        self.log = log
        self.started = time.monotonic()
        self.finished: float | None = None
        self.status: int | None = None  # exit status once finished
        self.killed = False
        self._tail = bytearray()
        self._lock = threading.Lock()
        self._log_file: IO[bytes] | None = None
        # program jobs
        self.pid: int | None = None
        self.master_fd: int | None = None
        self.sink: int | None = None  # the terminal's fd while attached: output is copied there too
        self.on_output: Callable[[bytes], bytes] | None = None  # while attached: filters output to the sink
        self.mouse_on = False  # the program turned on mouse reporting (seen in its output)
        # command jobs
        self.thread: threading.Thread | None = None

    # ── state ────────────────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self.finished is None

    @property
    def label(self) -> str:
        """`running`, `done`, `exit 2` or `killed`: for the bar and `jobs`."""
        if self.running:
            return "running"
        if self.killed:
            return "killed"
        return "done" if self.status == 0 else f"exit {self.status}"

    @property
    def elapsed(self) -> float:
        return (self.finished or time.monotonic()) - self.started

    def short(self, width: int = 18) -> str:
        text = " ".join(self.command.split())
        return text if len(text) <= width else text[: width - 1] + "…"

    # ── output ───────────────────────────────────────────────────────────────

    def append(self, data: bytes) -> None:
        with self._lock:
            if self._log_file is None:
                self.log.parent.mkdir(parents=True, exist_ok=True)
                self._log_file = open(self.log, "ab")  # noqa: SIM115 — closed in _finish
            self._log_file.write(data)
            self._log_file.flush()
            self._tail += data
            if len(self._tail) > TAIL_BYTES:
                del self._tail[: len(self._tail) - TAIL_BYTES]
            sink = self.sink
        if sink is not None:
            if MOUSE_MODE_RE.search(data):
                self.mouse_on = MOUSE_MODE_RE.findall(data)[-1].endswith(b"h")
            if self.on_output is not None:
                data = self.on_output(data)
            try:
                os.write(sink, data)
            except OSError:
                pass

    def tail(self, limit: int = TAIL_BYTES) -> bytes:
        with self._lock:
            return bytes(self._tail[-limit:])

    @property
    def output(self) -> str:
        """Everything the job produced so far, as text (the log file, or the tail if it is gone)."""
        try:
            return self.log.read_bytes().decode("utf-8", errors="replace")
        except OSError:
            return self.tail().decode("utf-8", errors="replace")

    def _finish(self, status: int) -> None:
        with self._lock:
            if self.finished is not None:
                return
            self.finished = time.monotonic()
            self.status = status
            if self._log_file is not None:
                self._log_file.close()
                self._log_file = None


class JobTable:
    """The jobs of one shell session."""

    def __init__(self, log_dir: Path):
        self.log_dir = Path(log_dir)
        self.jobs: dict[int, Job] = {}
        self.notifications: list[str] = []  # "[1] done: grep -r …", printed at the next prompt
        self.on_change: Callable[[], None] | None = None  # the shell redraws its bar
        self._next = 1
        self._lock = threading.Lock()

    # ── lookup ───────────────────────────────────────────────────────────────

    def get(self, number: int) -> Job | None:
        return self.jobs.get(number)

    def __iter__(self):
        return iter(sorted(self.jobs.values(), key=lambda j: j.number))

    def __len__(self) -> int:
        return len(self.jobs)

    @property
    def running(self) -> list[Job]:
        return [j for j in self if j.running]

    def _add(self, command: str, kind: str) -> Job:
        with self._lock:
            number = self._next
            self._next += 1
            job = Job(number, command, kind, self.log_dir / f"{number}.log")
            self.jobs[number] = job
            # Keep the table short: the oldest finished jobs go (their logs stay on disk).
            finished = [j for j in self if not j.running]
            for old in finished[: max(0, len(finished) - MAX_FINISHED)]:
                self.jobs.pop(old.number, None)
        return job

    def _changed(self) -> None:
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:  # noqa: BLE001 — a redraw problem must not kill a worker thread
                pass

    def _done(self, job: Job, status: int) -> None:
        job._finish(status)
        self.notifications.append(f"[{job.number}] {job.label}: {job.command}")
        self._changed()

    def pop_notifications(self) -> list[str]:
        notes, self.notifications = self.notifications, []
        return notes

    def clear_finished(self) -> int:
        gone = [n for n, j in self.jobs.items() if not j.running]
        for n in gone:
            del self.jobs[n]
        return len(gone)

    # ── programs on a pseudo-terminal ────────────────────────────────────────

    def start_program(self, argv: list[str], command: str, env: dict[str, str] | None = None,
                      cwd: str | None = None, size: tuple[int, int] | None = None) -> Job:
        """Spawn `argv` on a new pseudo-terminal (POSIX). The job's reader thread copies its
        output to the log and to the terminal while attached. Raises JobsUnsupported on Windows
        and FileNotFoundError when the program is not on PATH."""
        if sys.platform == "win32":
            raise JobsUnsupported("pseudo-terminals are not available on Windows")
        import pty

        exe = shutil.which(argv[0]) if not os.path.dirname(argv[0]) else argv[0]
        if not exe or not os.path.exists(exe):
            raise FileNotFoundError(argv[0])
        job = self._add(command, "program")
        rows, cols = size or _terminal_size()
        pid, master = pty.fork()
        if pid == 0:  # the child: a new session with the pty as its controlling terminal
            try:
                if cwd:
                    os.chdir(cwd)
                os.execve(exe, argv, env if env is not None else dict(os.environ))
            except OSError as exc:
                os.write(2, f"sub: {argv[0]}: {exc}\n".encode())
            finally:
                os._exit(127)
        job.pid, job.master_fd = pid, master
        _set_winsize(master, rows, cols)
        threading.Thread(target=self._read_program, args=(job,), daemon=True,
                         name=f"shellcraft:job{job.number}").start()
        self._changed()
        return job

    def _read_program(self, job: Job) -> None:
        assert job.master_fd is not None and job.pid is not None
        try:
            while True:
                try:
                    data = os.read(job.master_fd, 65536)
                except OSError:  # EIO: the child closed its side
                    break
                if not data:
                    break
                job.append(data)
        finally:
            status = _wait(job.pid)
            try:
                os.close(job.master_fd)
            except OSError:
                pass
            self._done(job, status)

    def attach(self, job: Job, stdin_fd: int = 0, stdout_fd: int = 1, raw: bool = True,
               replay: int = REPLAY_BYTES, footer: Any = None) -> tuple[str, int]:
        """Hand the terminal to a program job until it exits, or the user leaves it.

        Returns ("exited", status), ("detached", 0) after Ctrl-] alone, or ("switch", tab) after
        Ctrl-] followed by a digit, or a click on a footer tab (0 is the shell itself). The last
        `replay` bytes of its output are shown first, and the pseudo-terminal is resized to the
        terminal (minus the footer row, when there is a footer), which makes full-screen
        programs redraw. With a `footer` (core.footer.Footer), the program's scroll-region
        commands are kept above it and it is redrawn after the program's output.
        """
        if job.kind != "program" or job.master_fd is None:
            raise ValueError("not a program job")
        if not job.running:
            return "exited", job.status or 0
        master = job.master_fd
        old_attrs: Any = None
        if raw:
            import termios
            import tty

            old_attrs = termios.tcgetattr(stdin_fd)
            tty.setraw(stdin_fd)
        previous_winch: Any = None
        pending_redraw = False

        def size() -> tuple[int, int]:
            rows, cols = _terminal_size(stdout_fd)
            return (max(1, rows - 1) if footer is not None else rows), cols

        def filter_output(data: bytes) -> bytes:
            """Keep the program's margins above the footer and redraw it after its output."""
            nonlocal pending_redraw
            if footer is None:
                return data
            from core.footer import incomplete_escape

            data = footer.clamp_margins(data, size()[0])
            pending_redraw = incomplete_escape(data)
            if not pending_redraw:
                data += footer.render_bytes()
            return data

        def mouse_wanted() -> bool:
            return footer is not None and not job.mouse_on

        try:
            if footer is not None:
                footer.current = job.number
                footer.draw()
            if replay:
                last = job.tail(replay)
                try:
                    os.write(stdout_fd, footer.clamp_margins(last, size()[0]) if footer is not None else last)
                except OSError:
                    pass
            job.on_output = filter_output
            job.sink = stdout_fd

            def resize(*_: Any) -> None:
                try:
                    rows, cols = size()
                    _set_winsize(master, rows, cols)
                    if footer is not None:
                        footer.draw()
                except OSError:
                    pass

            # A size change: the program gets SIGWINCH through its pty. Setting the size twice
            # (one row off, then right) makes it redraw right away, the way tmux's attach does.
            try:
                rows, cols = size()
                _set_winsize(master, max(1, rows - 1), cols)
                _set_winsize(master, rows, cols)
            except OSError:
                pass
            if threading.current_thread() is threading.main_thread():
                try:
                    previous_winch = signal.signal(signal.SIGWINCH, resize)
                except (ValueError, OSError, AttributeError):
                    previous_winch = None
            # The footer's tabs are clickable even when the program has no use for the mouse:
            # reporting is turned on for it, and clicks elsewhere are dropped, not forwarded.
            our_mouse = False
            if mouse_wanted():
                _mouse(stdout_fd, True)
                our_mouse = True
            prefix_at: float | None = None  # when Ctrl-] was pressed and no digit has followed
            while job.running:
                if our_mouse and job.mouse_on:  # the program took the mouse itself
                    our_mouse = False
                elif not our_mouse and mouse_wanted():
                    _mouse(stdout_fd, True)
                    our_mouse = True
                try:
                    ready, _, _ = select.select([stdin_fd], [], [], 0.1)
                except (OSError, ValueError):
                    break
                if not ready:
                    if prefix_at is not None and time.monotonic() - prefix_at > PREFIX_WAIT:
                        return "detached", 0
                    if footer is not None and pending_redraw:
                        footer.draw()
                        pending_redraw = False
                    continue
                try:
                    data = os.read(stdin_fd, 4096)
                except OSError:
                    break
                if not data:  # the terminal went away
                    break
                if prefix_at is not None:
                    prefix_at = None
                    if data[:1].isdigit():
                        return "switch", int(data[:1])
                    if data[:1] == DETACH_KEY:  # Ctrl-] twice: the program gets one
                        data = data[1:]
                        try:
                            os.write(master, DETACH_KEY)
                        except OSError:
                            break
                    else:
                        return "detached", 0
                # Mouse: a click on a footer tab switches; the rest goes to the program only if
                # it asked for the mouse, else it is dropped (it would be garbage to it).
                if footer is not None and b"\x1b[<" in data:
                    from core.footer import parse_mouse

                    forward = bytearray()
                    pos = 0
                    for m in MOUSE_SEQ_RE.finditer(data):
                        forward += data[pos:m.start()]
                        pos = m.end()
                        parsed = parse_mouse(m.group().decode())
                        if parsed is not None:
                            kind, x, y = parsed
                            tab = footer.hit(x, y)
                            if kind == "move" and footer.set_hover(tab):
                                footer.draw()
                            if kind == "up" and tab is not None and tab != job.number:
                                footer.set_hover(None)
                                return "switch", tab
                            if tab is not None:
                                continue
                        if job.mouse_on:
                            forward += m.group()
                    forward += data[pos:]
                    data = bytes(forward)
                cut = data.find(DETACH_KEY)
                if cut >= 0:
                    rest = data[cut + 1:]
                    data = data[:cut]
                    if rest[:1].isdigit():
                        if data:
                            try:
                                os.write(master, data)
                            except OSError:
                                break
                        return "switch", int(rest[:1])
                    prefix_at = time.monotonic()
                if data:
                    try:
                        os.write(master, data)
                    except OSError:
                        break
            return "exited", job.status or 0
        finally:
            job.sink = None
            job.on_output = None
            if footer is not None and our_mouse:
                _mouse(stdout_fd, False)
            if footer is not None:
                footer.current = 0
                footer.set_hover(None)
            if previous_winch is not None:
                try:
                    signal.signal(signal.SIGWINCH, previous_winch)
                except (ValueError, OSError):
                    pass
            if raw and old_attrs is not None:
                import termios

                termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_attrs)

    # ── ShellCraft commands in a thread ──────────────────────────────────────

    def start_command(self, command: str, run: Callable[[], tuple[int, str]]) -> Job:
        """Run `run()` (which returns (status, output)) in a worker thread as a job."""
        job = self._add(command, "command")

        def target() -> None:
            try:
                status, text = run()
            except Exception as exc:  # noqa: BLE001 — a bug in the command is the job's failure
                status, text = 1, f"✗ {type(exc).__name__}: {exc}\n"
            if text:
                job.append(text.encode("utf-8", errors="replace"))
            self._done(job, status)

        job.thread = threading.Thread(target=target, daemon=True, name=f"shellcraft:job{job.number}")
        job.thread.start()
        self._changed()
        return job

    # ── stopping ─────────────────────────────────────────────────────────────

    def kill(self, job: Job) -> bool:
        """Stop a running program job (SIGHUP, then SIGKILL). A command job's thread cannot be
        stopped; it is marked killed and its result is dropped. Returns whether it was running."""
        if not job.running:
            return False
        job.killed = True
        if job.kind == "program" and job.pid is not None:
            for sig in (signal.SIGHUP, signal.SIGKILL):
                try:
                    os.kill(job.pid, sig)
                except OSError:
                    break
                for _ in range(20):
                    if not job.running:
                        return True
                    time.sleep(0.05)
            return True
        self._done(job, 130)
        return True

    def cleanup(self) -> list[Job]:
        """At exit: stop every running program job; returns the jobs that were still running."""
        running = self.running
        for job in running:
            self.kill(job)
        return running


# ── helpers ──────────────────────────────────────────────────────────────────

def _terminal_size(fd: int = 1) -> tuple[int, int]:
    try:
        size = os.get_terminal_size(fd)
        return max(1, size.lines), max(1, size.columns)
    except OSError:
        size = shutil.get_terminal_size()
        return max(1, size.lines), max(1, size.columns)


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    import fcntl
    import struct
    import termios

    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _mouse(fd: int, on: bool) -> None:
    """Turn the terminal's mouse reporting (clicks, SGR coordinates) on or off."""
    try:
        os.write(fd, b"\x1b[?1000h\x1b[?1006h" if on else b"\x1b[?1000l\x1b[?1006l")
    except OSError:
        pass


def _wait(pid: int) -> int:
    try:
        _, raw = os.waitpid(pid, 0)
    except ChildProcessError:
        return 0
    if os.WIFSIGNALED(raw):
        return 128 + os.WTERMSIG(raw)
    return os.WEXITSTATUS(raw) if os.WIFEXITED(raw) else 1


def login_shell() -> list[str]:
    """The user's own shell, for `sub` with no arguments."""
    if sys.platform == "win32":
        pwsh = shutil.which("pwsh") or shutil.which("powershell")
        return [pwsh] if pwsh else [os.environ.get("COMSPEC", "cmd.exe")]
    shell = os.environ.get("SHELL") or shutil.which("bash") or shutil.which("sh") or "/bin/sh"
    return [shell]


def nested_shellcraft(args: list[str]) -> tuple[list[str], dict[str, str]]:
    """argv and environment for `sub shellcraft`: this interpreter running this checkout (or the
    installed copy), one level deeper (SHELLCRAFT_DEPTH, shown in the prompt)."""
    import core

    root = str(Path(core.__file__).resolve().parent.parent)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (root, env.get("PYTHONPATH")) if p)
    env["SHELLCRAFT_DEPTH"] = str(depth() + 1)
    return [sys.executable, "-m", "core.cli", *args], env


def depth() -> int:
    try:
        return max(0, int(os.environ.get("SHELLCRAFT_DEPTH", "0") or 0))
    except ValueError:
        return 0


def run_foreground(argv: list[str], env: dict[str, str] | None = None) -> int:
    """Run a program with the terminal (keyboard and screen) handed to it, and wait: what `sub`
    does on Windows, where there is no pseudo-terminal to put it on in the background."""
    try:
        return subprocess.run(argv, cwd=os.getcwd(), env=env).returncode
    except KeyboardInterrupt:
        return 130
