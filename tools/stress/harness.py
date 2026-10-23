"""Stress ShellCraft in a pty: a pyte terminal that answers CPR like a real one (with delay),
commands fired with Ctrl-C at random moments, and a scan for crashes, tracebacks, leaked escape
fragments, hung prompts and leftover input."""
import os, pty, sys, time, select, re, random, signal, struct, fcntl, termios, threading, json, shutil, tempfile
import pyte

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PY = sys.executable
COLS, ROWS = 120, 40
CPR = re.compile(rb"\x1b\[6n")
LEAK = re.compile(r"(\d+;\d+R|\[\?\d+[hl]|\x1b|\[\d+(;\d+)*[A-Za-z]\b|<?\d+;\d+;\d+[Mm]|200~|201~)")
MODES = re.compile(rb"\x1b\[\?(1000|1002|1003|1006|2004)([hl])")

class Term:
    def __init__(self, home, extra=()):
        self.home = home
        self.screen = pyte.Screen(COLS, ROWS); self.stream = pyte.ByteStream(self.screen)
        self.raw = bytearray(); self.lock = threading.Lock(); self.last_rx = time.monotonic()
        self.cpr_delay = (0.0, 0.4)
        self.modes = {}  # mouse / bracketed paste modes the shell has switched on, as a real terminal would track
        env = dict(os.environ, TERM="xterm-256color", COLUMNS=str(COLS), LINES=str(ROWS), SHELLCRAFT_HOME=home,
                   PYTHONUNBUFFERED="1", LANG="C.UTF-8")
        env.pop("SHELLCRAFT_TIMING", None)
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.chdir(ROOT)
            os.execvpe(PY, [PY, "main.py", "--no-startup", "--modules", ROOT + "/modules", *extra], env)
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
        self.alive = True
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        while True:
            try:
                r, _, _ = select.select([self.fd], [], [], 0.1)
                if not r: continue
                data = os.read(self.fd, 65536)
            except OSError:
                self.alive = False; return
            if not data: self.alive = False; return
            with self.lock:
                self.raw += data; self.last_rx = time.monotonic()
                try:
                    self.stream.feed(data)
                except Exception as exc:  # pyte choked on a broken sequence: note it, keep going
                    self.bad_sequences = getattr(self, "bad_sequences", 0) + 1
                for mode, onoff in MODES.findall(data):
                    self.modes[int(mode)] = onoff == b"h"
            for _ in CPR.findall(data):
                threading.Thread(target=self._answer_cpr, daemon=True).start()

    def _answer_cpr(self):
        time.sleep(random.uniform(*self.cpr_delay))
        with self.lock:
            y, x = self.screen.cursor.y + 1, self.screen.cursor.x + 1
        try: os.write(self.fd, f"\x1b[{y};{x}R".encode())
        except OSError: pass

    def send(self, data: bytes):
        os.write(self.fd, data)

    def lines(self):
        with self.lock:
            return [self.screen.display[i].rstrip() for i in range(ROWS)], (self.screen.cursor.y, self.screen.cursor.x)

    def resize(self, rows, cols):
        with self.lock:
            self.screen.resize(rows, cols)
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def at_prompt(self):
        lines, (y, x) = self.lines()
        if y >= len(lines): return False
        line = lines[y]
        return line.startswith("╰─❯") and x == 4 and time.monotonic() - self.last_rx > 0.4

    def prompt_text(self):
        lines, (y, x) = self.lines()
        return lines[y] if y < len(lines) else ""

    def wait_prompt(self, timeout=15.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if not self.alive: return "dead"
            if self.at_prompt(): return "ok"
            time.sleep(0.05)
        return "hang"

    def mouse_on(self):
        return any(self.modes.get(m) for m in (1000, 1002, 1003))

    def exited(self):
        try:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            return status if pid else None
        except ChildProcessError:
            return -1

    def kill(self):
        try: os.kill(self.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        try: os.waitpid(self.pid, 0)
        except ChildProcessError: pass


def scan(term, since):
    """Anomalies in the raw output since `since` and on the screen."""
    with term.lock:
        raw = bytes(term.raw[since:])
    text = raw.decode("utf-8", errors="replace")
    plain = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[()][A-Z0-9]|\x1b[=>]|\x1b\[\?[0-9;]*[hl]|\x1b[78]|\x1bP.*?\x1b\\", "", text)
    found = []
    if "Traceback (most recent call last)" in plain: found.append("TRACEBACK")
    if "session closed" in plain: found.append("EXITED")
    if re.search(r"\b\w+Error: |\b\w+Exception: ", plain): found.append("EXCEPTION-TEXT:" + re.search(r"\b\w+(Error|Exception): .{0,80}", plain).group(0))
    lines, (y, x) = term.lines()
    prompt_line = lines[y] if y < len(lines) else ""
    m = LEAK.search(prompt_line)  # on the prompt line: what the shell would run. (The tty's own
    if m: found.append("LEAK-ON-PROMPT:" + repr(m.group(0)) + " in " + repr(prompt_line[:80]))  # echo of raw bytes during a command is not a leak.)
    stray = re.search(r"(?<![\x1b])\d+;\d+R", plain)
    if stray: found.append("CPR-IN-OUTPUT:" + repr(plain[max(0, stray.start()-20):stray.end()+5]))
    if y < len(lines) and lines[y].startswith("╰─❯") and lines[y][4:].strip():
        found.append("LEFTOVER-INPUT:" + repr(lines[y][4:].strip()))
    return found, plain
