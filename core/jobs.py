"""Subshells: the jobs started with `sub`, listed by `jobs`, shown in the panel and the footer.

Two kinds of job:

- a **program** (`sub bash`, `sub htop`, `sub shellcraft`): an OS process on its own
  pseudo-terminal (`pty` on POSIX, ConPTY through `pywinpty` on Windows), so it can be
  interactive. Its output is fed to a terminal emulator (`pyte`) that keeps the program's screen
  and a scrollback, so switching to it redraws its page exactly, like tmux, instead of replaying
  raw output. `attach()` hands it the real keyboard and screen until it exits or the user leaves
  (Ctrl-] alone detaches, Ctrl-] N switches to tab N, a click on the footer too); it keeps
  running in between. Subshells are shown on the terminal's alternate screen (`Pages`), so coming
  back to the prompt restores it untouched.
- a **command** (`sub grep -r TODO .`, `sub queryDns example.com`): a ShellCraft command line run
  in a worker thread, with its output collected. Selecting it shows the output so far.

Every job writes what it produced to `~/.shellcraft/jobs/N.log` and keeps the last TAIL_BYTES in
memory. `JobTable.on_change` is called from worker threads whenever a job's state changes, so the
shell can redraw the panel.
"""

from __future__ import annotations

import copy
import os
import queue
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
TAIL_BYTES = 64 * 1024  # kept in memory per job
HISTORY_LINES = 5000  # scrollback lines the emulator keeps per program
MAX_FINISHED = 20  # finished jobs kept in the table (the oldest are dropped)
MOUSE_MODE_RE = re.compile(rb"\x1b\[\?(?:1000|1002|1003|1006|1015)[hl]")
MOUSE_SEQ_RE = re.compile(rb"\x1b\[<\d+;\d+;\d+[Mm]")
# The alternate-screen switches a program may send (vim, htop, less): the real terminal is
# already on its alternate screen while a subshell is shown, so these are handled by the
# emulator instead (the main buffer is saved and restored) and never reach the terminal.
ALT_SCREEN_RE = re.compile(rb"\x1b\[\?(?:1049|1047|1048|47)[hl]")
_COLOR_NAMES = {"black": 0, "red": 1, "green": 2, "yellow": 3, "blue": 4, "magenta": 5, "cyan": 6, "white": 7}


class JobsUnsupported(Exception):
    """No pseudo-terminal here (Windows without pywinpty): run the program in the foreground."""


# ── one job ──────────────────────────────────────────────────────────────────

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
        self._lock = threading.RLock()
        self._log_file: IO[bytes] | None = None
        # program jobs
        self.pid: int | None = None
        self.master_fd: int | None = None  # POSIX
        self.winpty: Any = None  # Windows: winpty.PtyProcess
        self.screen: Any = None  # pyte.HistoryScreen: the program's page and scrollback
        self.stream: Any = None  # pyte.ByteStream feeding it
        self.saved_main: Any = None  # the main buffer while the program is on its alternate screen
        self.sink: Callable[[bytes], None] | None = None  # the terminal while attached
        self.on_output: Callable[[bytes], bytes] | None = None  # while attached: filters the live output
        self.mouse_on = False  # the program turned on mouse reporting (seen in its output)
        self.scrolled = 0  # pages scrolled back in the emulator's history while attached
        # command jobs
        self.thread: threading.Thread | None = None

    # ── state ────────────────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self.finished is None

    @property
    def label(self) -> str:
        """`running`, `done`, `exit 2` or `killed`: for the panel and `jobs`."""
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
            if MOUSE_MODE_RE.search(data):
                self.mouse_on = MOUSE_MODE_RE.findall(data)[-1].endswith(b"h")
            if self.screen is not None:
                data = self._emulate(data)
            sink, on_output = self.sink, self.on_output
        if sink is not None:
            if on_output is not None:
                data = on_output(data)
            try:
                sink(data)
            except OSError:
                pass

    def _emulate(self, data: bytes) -> bytes:
        """Feed the emulator. A switch to or from the program's alternate screen is honoured in
        the emulator (the main buffer is saved and restored) and becomes a plain clear for the
        real terminal, which is already on its own alternate screen."""
        out = bytearray()
        pos = 0
        for m in ALT_SCREEN_RE.finditer(data):
            self.stream.feed(data[pos:m.start()])
            out += data[pos:m.start()]
            pos = m.end()
            if m.group().endswith(b"h"):
                if self.saved_main is None:
                    self.saved_main = (copy.deepcopy(self.screen.buffer), self.screen.cursor.x, self.screen.cursor.y)
                self.screen.reset()
                out += b"\x1b[H\x1b[2J"  # the program draws its alternate screen itself
            elif self.saved_main is not None:
                buffer, x, y = self.saved_main
                self.saved_main = None
                self.screen.reset()
                self.screen.buffer.update(buffer)
                self.screen.cursor.x, self.screen.cursor.y = x, y
                out += render_screen(self.screen)  # nobody else redraws the main page: do it here
            else:
                out += b"\x1b[H\x1b[2J"
            self.screen.dirty.update(range(self.screen.lines))
        self.stream.feed(data[pos:])
        if pos == 0:
            return data
        out += data[pos:]
        return bytes(out)

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

    def page(self, rows: int | None = None, cols: int | None = None) -> bytes:
        """The program's screen as ANSI: clear, every line with its colors, the cursor placed.
        After leaving its alternate screen the emulator is restored, so the page is right too."""
        with self._lock:
            if self.screen is None:
                return b"\x1b[H\x1b[2J" + self.tail(4096)
            if rows and cols and (self.screen.lines, self.screen.columns) != (rows, cols):
                self.screen.resize(rows, cols)
            return render_screen(self.screen)

    def resize(self, rows: int, cols: int) -> None:
        with self._lock:
            if self.screen is not None and (self.screen.lines, self.screen.columns) != (rows, cols):
                self.screen.resize(rows, cols)
            if self.master_fd is not None:
                try:
                    _set_winsize(self.master_fd, rows, cols)
                except OSError:
                    pass
            elif self.winpty is not None:
                try:
                    self.winpty.setwinsize(rows, cols)
                except Exception:  # noqa: BLE001 — a closed pty
                    pass

    def scroll(self, pages: int) -> None:
        """Move the emulator's view through its scrollback: pages < 0 is back, > 0 forward."""
        with self._lock:
            if self.screen is None:
                return
            for _ in range(abs(pages)):
                if pages < 0:
                    self.screen.prev_page()
                    self.scrolled += 1
                elif self.scrolled:
                    self.screen.next_page()
                    self.scrolled -= 1
            self.screen.dirty.update(range(self.screen.lines))

    def scroll_bottom(self) -> None:
        with self._lock:
            while self.scrolled and self.screen is not None:
                self.screen.next_page()
                self.scrolled -= 1

    def write(self, data: bytes) -> None:
        """Keys for the program."""
        if self.master_fd is not None:
            os.write(self.master_fd, data)
        elif self.winpty is not None:
            self.winpty.write(data.decode("utf-8", errors="replace"))

    def _finish(self, status: int) -> None:
        with self._lock:
            if self.finished is not None:
                return
            self.finished = time.monotonic()
            self.status = status
            if self._log_file is not None:
                self._log_file.close()
                self._log_file = None


# ── the table ────────────────────────────────────────────────────────────────

class JobTable:
    """The jobs of one shell session."""

    def __init__(self, log_dir: Path):
        self.log_dir = Path(log_dir)
        self.jobs: dict[int, Job] = {}
        self.notifications: list[str] = []  # "[1] done: grep -r …", printed at the next prompt
        self.on_change: Callable[[], None] | None = None  # the shell redraws its panel
        self._next = 1
        self._lock = threading.Lock()

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
            finished = [j for j in self if not j.running]  # keep the table short; the logs stay
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
        """Spawn `argv` on a new pseudo-terminal, with an emulator keeping its screen. The reader
        thread copies its output to the log, the emulator and (while attached) the terminal.
        Raises JobsUnsupported when there is no pseudo-terminal (Windows without pywinpty) and
        FileNotFoundError when the program is not on PATH."""
        exe = shutil.which(argv[0]) if not os.path.dirname(argv[0]) else argv[0]
        if not exe or not os.path.exists(exe):
            raise FileNotFoundError(argv[0])
        rows, cols = size or _terminal_size()
        if sys.platform == "win32":
            try:
                from winpty import PtyProcess
            except ImportError:
                raise JobsUnsupported("pywinpty is not installed: pip install pywinpty") from None
            job = self._add(command, "program")
            job.winpty = PtyProcess.spawn([exe, *argv[1:]], cwd=cwd or os.getcwd(),
                                          env=env if env is not None else dict(os.environ), dimensions=(rows, cols))
            job.pid = job.winpty.pid
        else:
            import pty

            job = self._add(command, "program")
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
        job.screen, job.stream = new_screen(rows, cols)
        threading.Thread(target=self._read_program, args=(job,), daemon=True,
                         name=f"shellcraft:job{job.number}").start()
        self._changed()
        return job

    def _read_program(self, job: Job) -> None:
        try:
            if job.winpty is not None:
                while True:
                    try:
                        text = job.winpty.read(4096)
                    except EOFError:
                        break
                    if not text:
                        if not job.winpty.isalive():
                            break
                        continue
                    job.append(text.encode("utf-8", errors="surrogatepass"))
            else:
                assert job.master_fd is not None
                while True:
                    try:
                        data = os.read(job.master_fd, 65536)
                    except OSError:  # EIO: the child closed its side
                        break
                    if not data:
                        break
                    job.append(data)
        finally:
            if job.winpty is not None:
                while job.winpty.isalive():
                    time.sleep(0.05)
                status = job.winpty.exitstatus or 0
            else:
                assert job.pid is not None and job.master_fd is not None
                status = _wait(job.pid)
                try:
                    os.close(job.master_fd)
                except OSError:
                    pass
            self._done(job, status)

    def attach(self, job: Job, terminal: Terminal | None = None, footer: Any = None,
               raw: bool = True) -> tuple[str, int]:
        """Hand the terminal to a program job until it exits, or the user leaves it.

        Returns ("exited", status), ("detached", 0) after Ctrl-] alone, or ("switch", tab) after
        Ctrl-] followed by a digit, or a click on a footer tab (0 is the shell itself). The
        program's page is drawn from its emulator, the pseudo-terminal is resized to the terminal
        (minus the footer row, with a `footer`), the mouse wheel scrolls through its scrollback
        (unless the program uses the mouse itself), and the footer is redrawn after its output.
        """
        if job.kind != "program":
            raise ValueError("not a program job")
        if not job.running:
            return "exited", job.status or 0
        term = terminal or Terminal.real()
        if job.screen is None and not getattr(self, "_warned_no_pyte", False):
            self._warned_no_pyte = True
            self.notifications.append("the 'pyte' package is not installed, so switching between subshells "
                                      "replays their last output instead of redrawing their screen: "
                                      f"{sys.executable} -m pip install pyte")
        pending_redraw = False

        def size() -> tuple[int, int]:
            rows, cols = term.size()
            return (max(1, rows - 1) if footer is not None else rows), cols

        def show_page() -> None:
            rows, cols = size()
            term.write(job.page(rows, cols))
            if footer is not None:
                footer.draw()

        def filter_output(data: bytes) -> bytes:
            """Keep the program's margins above the footer; redraw the footer after its output."""
            nonlocal pending_redraw
            if job.scrolled:
                return b""  # the user is reading the scrollback: the page is redrawn on return
            if footer is None:
                return data
            from core.footer import incomplete_escape

            data = footer.clamp_margins(data, size()[0])
            pending_redraw = incomplete_escape(data)
            if not pending_redraw:
                data += footer.render_bytes()
            return data

        def mouse_wanted() -> bool:
            return not job.mouse_on

        our_mouse = False
        if raw:
            term.raw(True)
        try:
            if footer is not None:
                footer.current = job.number
            job.resize(*size())
            job.scroll_bottom()
            show_page()
            job.on_output = filter_output
            job.sink = term.write
            term.on_resize = lambda: (job.resize(*size()), show_page())
            if mouse_wanted():
                term.mouse(True)
                our_mouse = True
            prefix_at: float | None = None  # when Ctrl-] was pressed and no digit has followed
            while job.running:
                if our_mouse and job.mouse_on:  # the program took the mouse itself
                    our_mouse = False
                elif not our_mouse and mouse_wanted():
                    term.mouse(True)
                    our_mouse = True
                data = term.read(0.1)
                if data is None:
                    if prefix_at is not None and time.monotonic() - prefix_at > PREFIX_WAIT:
                        return "detached", 0
                    if footer is not None and pending_redraw:
                        footer.draw()
                        pending_redraw = False
                    continue
                if not data:  # the terminal went away
                    break
                if prefix_at is not None:
                    prefix_at = None
                    if data[:1].isdigit():
                        return "switch", int(data[:1])
                    if data[:1] == DETACH_KEY:  # Ctrl-] twice: the program gets one
                        data = data[1:]
                        job.write(DETACH_KEY)
                    else:
                        return "detached", 0
                # Mouse: the wheel scrolls the page's history, a click on a footer tab switches,
                # the rest goes to the program only if it asked for the mouse (else dropped).
                if b"\x1b[<" in data:
                    from core.footer import parse_mouse

                    forward = bytearray()
                    pos = 0
                    for m in MOUSE_SEQ_RE.finditer(data):
                        forward += data[pos:m.start()]
                        pos = m.end()
                        parsed = parse_mouse(m.group().decode())
                        if parsed is not None:
                            kind, x, y = parsed
                            tab = footer.hit(x, y) if footer is not None else None
                            if kind == "move" and footer is not None and footer.set_hover(tab):
                                footer.draw()
                            if kind == "up" and tab is not None and tab != job.number:
                                footer.set_hover(None)
                                return "switch", tab
                            if tab is not None:
                                continue
                            if kind == "scroll" and not job.mouse_on:
                                job.scroll(-1 if m.group().startswith(b"\x1b[<64") else 1)
                                show_page()
                                continue
                        if job.mouse_on:
                            forward += m.group()
                    forward += data[pos:]
                    data = bytes(forward)
                if job.scrolled and data:  # any key: back to the live page
                    job.scroll_bottom()
                    show_page()
                cut = data.find(DETACH_KEY)
                if cut >= 0:
                    rest = data[cut + 1:]
                    data = data[:cut]
                    if rest[:1].isdigit():
                        if data:
                            job.write(data)
                        return "switch", int(rest[:1])
                    prefix_at = time.monotonic()
                if data:
                    try:
                        job.write(data)
                    except OSError:
                        break
            return "exited", job.status or 0
        finally:
            job.sink = None
            job.on_output = None
            job.scroll_bottom()
            term.on_resize = None
            if our_mouse:
                term.mouse(False)
            if footer is not None:
                footer.current = 0
                footer.set_hover(None)
            if raw:
                term.raw(False)

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
        """Stop a running program job (SIGHUP, then SIGKILL; terminate on Windows). A command
        job's thread cannot be stopped; it is marked killed. Returns whether it was running."""
        if not job.running:
            return False
        job.killed = True
        if job.kind == "program":
            if job.winpty is not None:
                for force in (False, True):
                    try:
                        job.winpty.terminate(force=force)
                    except Exception:  # noqa: BLE001 — already gone
                        break
                    for _ in range(20):
                        if not job.running:
                            return True
                        time.sleep(0.05)
                return True
            if job.pid is not None:
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


# ── the emulator ─────────────────────────────────────────────────────────────

def new_screen(rows: int, cols: int) -> tuple[Any, Any]:
    """A pyte screen and the stream feeding it, or (None, None) when pyte is not installed: the
    job then works without an emulator (switching replays its last output instead of its page)."""
    try:
        import pyte
    except ImportError:
        return None, None
    screen = pyte.HistoryScreen(cols, rows, history=HISTORY_LINES, ratio=0.5)
    screen.write_process_input = lambda data: None
    return screen, pyte.ByteStream(screen)


def _sgr(char: Any) -> str:
    """The SGR sequence for one emulator cell."""
    codes = ["0"]
    if char.bold:
        codes.append("1")
    if char.italics:
        codes.append("3")
    if char.underscore:
        codes.append("4")
    if char.blink:
        codes.append("5")
    if char.reverse:
        codes.append("7")
    if char.strikethrough:
        codes.append("9")
    for color, base in ((char.fg, 30), (char.bg, 40)):
        if color == "default":
            continue
        name = color[6:] if color.startswith("bright") else color
        if name in _COLOR_NAMES:
            codes.append(str((base + 60 if color.startswith("bright") else base) + _COLOR_NAMES[name]))
        elif len(color) == 6:
            try:
                r, g, b = int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)
            except ValueError:
                continue
            codes.append(f"{base + 8};2;{r};{g};{b}")
    return "\x1b[" + ";".join(codes) + "m"


def render_screen(screen: Any) -> bytes:
    """A pyte screen as one ANSI picture: clear, each line with its colors, the cursor placed."""
    out = ["\x1b[?25l\x1b[H\x1b[2J"]
    for y in range(screen.lines):
        line = screen.buffer[y]
        out.append(f"\x1b[{y + 1};1H")
        last_sgr = None
        for x in range(screen.columns):
            char = line[x]
            if char.data == "":  # the second cell of a wide character
                continue
            sgr = _sgr(char)
            if sgr != last_sgr:
                out.append(sgr)
                last_sgr = sgr
            out.append(char.data or " ")
    out.append(f"\x1b[0m\x1b[{screen.cursor.y + 1};{screen.cursor.x + 1}H")
    if not screen.cursor.hidden:
        out.append("\x1b[?25h")
    screen.dirty.clear()
    return "".join(out).encode("utf-8", errors="replace")


# ── the real terminal while attached ─────────────────────────────────────────

class Terminal:
    """The keyboard and screen handed to a program: bytes in, bytes out, raw mode, size.
    `Terminal.real()` is the user's terminal; tests build one on a pseudo-terminal pair."""

    def __init__(self, read: Callable[[float], bytes | None], write: Callable[[bytes], None],
                 size: Callable[[], tuple[int, int]], raw: Callable[[bool], None] | None = None,
                 close: Callable[[], None] | None = None):
        self._read, self._write, self._size, self._raw, self._close = read, write, size, raw, close
        self.on_resize: Callable[[], None] | None = None

    def read(self, timeout: float) -> bytes | None:
        """Bytes typed, b"" when the terminal is gone, None when nothing arrived in `timeout`."""
        return self._read(timeout)

    def write(self, data: bytes) -> None:
        self._write(data)

    def size(self) -> tuple[int, int]:
        return self._size()

    def raw(self, on: bool) -> None:
        if self._raw is not None:
            self._raw(on)

    def mouse(self, on: bool) -> None:
        """Turn the terminal's mouse reporting (clicks, SGR coordinates) on or off."""
        self.write(b"\x1b[?1000h\x1b[?1006h" if on else b"\x1b[?1000l\x1b[?1006l")

    def close(self) -> None:
        if self._close is not None:
            self._close()

    @classmethod
    def real(cls) -> Terminal:
        if sys.platform == "win32":
            return _windows_terminal()
        return cls.on_fds(0, 1)

    @classmethod
    def on_fds(cls, stdin_fd: int, stdout_fd: int, raw: bool = True) -> Terminal:
        """A POSIX terminal on two file descriptors (the user's, or a pty pair in tests)."""
        state: dict[str, Any] = {"attrs": None, "winch": None}

        def read(timeout: float) -> bytes | None:
            try:
                ready, _, _ = select.select([stdin_fd], [], [], timeout)
            except (OSError, ValueError):
                return b""
            if not ready:
                return None
            try:
                return os.read(stdin_fd, 4096)
            except OSError:
                return b""

        def write(data: bytes) -> None:
            os.write(stdout_fd, data)

        def size() -> tuple[int, int]:
            return _terminal_size(stdout_fd)

        def set_raw(on: bool) -> None:
            import termios
            import tty

            if on:
                state["attrs"] = termios.tcgetattr(stdin_fd)
                tty.setraw(stdin_fd)
                if threading.current_thread() is threading.main_thread():
                    def winch(*_: Any) -> None:
                        if terminal.on_resize is not None:
                            terminal.on_resize()
                    try:
                        state["winch"] = signal.signal(signal.SIGWINCH, winch)
                    except (ValueError, OSError, AttributeError):
                        state["winch"] = None
            else:
                if state["attrs"] is not None:
                    termios.tcsetattr(stdin_fd, termios.TCSADRAIN, state["attrs"])
                    state["attrs"] = None
                if state["winch"] is not None:
                    try:
                        signal.signal(signal.SIGWINCH, state["winch"])
                    except (ValueError, OSError):
                        pass
                    state["winch"] = None

        terminal = cls(read, write, size, set_raw if raw else None)
        return terminal


def _windows_terminal() -> Terminal:
    """Windows: keys from CONIN$ as VT sequences (a reader thread that waits for input before
    reading, so nothing is swallowed after detaching), output to CONOUT$, raw mode through the
    console input modes."""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    from prompt_toolkit.win32_types import INPUT_RECORD

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    conin_fd = os.open("CONIN$", os.O_RDWR | os.O_BINARY)  # type: ignore[attr-defined]
    conout = open("CONOUT$", "w", encoding="utf-8", errors="replace")  # noqa: SIM115 — closed in close()
    conin = wintypes.HANDLE(msvcrt.get_osfhandle(conin_fd))  # type: ignore[attr-defined]
    incoming: queue.Queue[bytes] = queue.Queue()
    stop = threading.Event()
    state: dict[str, Any] = {"mode": None, "thread": None}
    enable_vt_input, enable_window_input = 0x0200, 0x0008
    enable_processed, enable_line, enable_echo = 0x0001, 0x0002, 0x0004

    def reader() -> None:
        records = (INPUT_RECORD * 256)()
        count = wintypes.DWORD()
        while not stop.is_set():
            if k32.WaitForSingleObject(conin, 100) != 0:  # WAIT_OBJECT_0: input is waiting
                continue
            if not k32.ReadConsoleInputW(conin, records, 256, ctypes.byref(count)):
                break
            text = []
            for i in range(count.value):
                rec = records[i]
                if rec.EventType == 1 and rec.Event.KeyEvent.KeyDown and rec.Event.KeyEvent.uChar.UnicodeChar:
                    text.append(rec.Event.KeyEvent.uChar.UnicodeChar)
                elif rec.EventType == 4 and terminal.on_resize is not None:  # WINDOW_BUFFER_SIZE_EVENT
                    try:
                        terminal.on_resize()
                    except Exception:  # noqa: BLE001
                        pass
            if text:
                incoming.put("".join(text).encode("utf-8", errors="surrogatepass"))

    def read(timeout: float) -> bytes | None:
        try:
            return incoming.get(timeout=timeout)
        except queue.Empty:
            return None

    def write(data: bytes) -> None:
        conout.write(data.decode("utf-8", errors="replace"))
        conout.flush()

    def size() -> tuple[int, int]:
        return _terminal_size(conout.fileno())

    def set_raw(on: bool) -> None:
        mode = wintypes.DWORD()
        if on:
            k32.GetConsoleMode(conin, ctypes.byref(mode))
            state["mode"] = mode.value
            raw_mode = mode.value | enable_vt_input | enable_window_input
            raw_mode &= ~(enable_processed | enable_line | enable_echo)
            k32.SetConsoleMode(conin, raw_mode)
            stop.clear()
            state["thread"] = threading.Thread(target=reader, daemon=True, name="shellcraft:console-keys")
            state["thread"].start()
        else:
            stop.set()
            if state["thread"] is not None:
                state["thread"].join(0.5)
            if state["mode"] is not None:
                k32.SetConsoleMode(conin, state["mode"])

    def close() -> None:
        stop.set()
        try:
            os.close(conin_fd)
            conout.close()
        except OSError:
            pass

    terminal = Terminal(read, write, size, set_raw, close)
    return terminal


class Pages:
    """The alternate screen while subshells are shown: entered once before attaching, left when
    the user is back at the prompt, which the terminal then restores untouched."""

    def __init__(self, terminal: Terminal):
        self.terminal = terminal
        self.active = False

    def begin(self) -> None:
        if not self.active:
            self.terminal.write(b"\x1b[?1049h\x1b[H\x1b[2J")
            self.active = True

    def end(self) -> None:
        if self.active:
            self.terminal.write(b"\x1b[r\x1b[?25h\x1b[0m\x1b[?1049l")
            self.active = False


# ── helpers ──────────────────────────────────────────────────────────────────

def _terminal_size(fd: int = 1) -> tuple[int, int]:
    try:
        size = os.get_terminal_size(fd)
        if size.lines < 1 or size.columns < 1:
            raise OSError("no size yet")  # a fresh pseudo-terminal reports 0 × 0
        return size.lines, size.columns
    except (OSError, ValueError):
        size = shutil.get_terminal_size()
        return max(1, size.lines), max(1, size.columns)


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    import fcntl
    import struct
    import termios

    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


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
    """Run a program with the terminal handed to it, and wait: what `sub` does on Windows when
    pywinpty is not installed (no pseudo-terminal to put it on in the background)."""
    try:
        return subprocess.run(argv, cwd=os.getcwd(), env=env).returncode
    except KeyboardInterrupt:
        return 130
