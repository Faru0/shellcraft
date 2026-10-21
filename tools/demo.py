"""Record the README demo: a scripted ShellCraft session, written as an asciinema v2 `.cast`.

    python tools/demo.py                     writes docs/demo.cast
    python tools/demo.py -o /tmp/x.cast      somewhere else
    agg --font-size 16 docs/demo.cast docs/demo.gif      the GIF (https://github.com/asciinema/agg)

Linux and macOS only (a pty). It runs the real shell (`main.py`) in a 100x32 pseudo-terminal with
a throwaway SHELLCRAFT_HOME and a scratch folder of sample files, so your config, history, keys and
files never appear, and types the scenes in SCENES with a human pace. A pyte screen follows the
output so the shell's cursor-position queries get real answers, as from a terminal. Re-run it
whenever the UI changes; the scenes need no network.
"""

from __future__ import annotations

import argparse
import codecs
import fcntl
import json
import os
import pty
import select
import shutil
import struct
import sys
import tempfile
import termios
import time
from pathlib import Path

import pyte

ROOT = Path(__file__).resolve().parents[1]
WIDTH, HEIGHT = 100, 32
TYPE_DELAY = 0.045  # seconds between typed characters

ENTER, TAB, ESC, PREFIX = "\r", "\t", "\x1b", "\x1d"  # PREFIX: Ctrl-], the subshell key

FILES = {
    "app.log": "".join(f"2026-10-21 09:00:{i:02d} {lvl} {msg}\n" for i, (lvl, msg) in enumerate([
        ("INFO", "service started"), ("ERROR", "db timeout"), ("WARN", "slow request /api/users"),
        ("ERROR", "db timeout"), ("INFO", "cache warmed"), ("ERROR", "disk full on /var"),
        ("ERROR", "db timeout"), ("INFO", "health check ok")])),
    "users.json": json.dumps([
        {"name": "ada", "team": "core", "langs": ["py", "go"], "admin": True},
        {"name": "lin", "team": "ops", "langs": ["sh", "py"], "admin": False},
        {"name": "kim", "team": "sec", "langs": ["rust"], "admin": True}], indent=2) + "\n",
    "hosts.csv": "web-1,10.0.0.11,prod\nweb-2,10.0.0.12,prod\ndb-1,10.0.0.21,prod\nci-1,10.0.1.5,dev\n",
}

# (what to type, seconds to wait after it). A str is typed one character at a time; a tuple
# ("keys", TEXT) is sent at once (control keys, Tab).
SCENES: list[tuple[object, float]] = [
    (None, 2.5),  # the banner
    ("cat app.log | grep ERROR | cut -d ' ' -f 4- | sort | uniq -c", 0.4), (("keys", ENTER), 1.8),
    ("jq -f '[*].name' users.json", 0.4), (("keys", ENTER), 1.6),
    ("cut ", 0.3), (("keys", TAB), 1.4),  # completion that follows the usage line: -c / -f
    (("keys", TAB), 0.8),  # Tab again: -f
    (" 1,2 -d , hosts.csv", 0.4), (("keys", ENTER), 1.8),
    ("for h in $(cut -d , -f 1 hosts.csv); do echo \"checking $h\"; done", 0.4), (("keys", ENTER), 1.8),
    ("sub bash --norc", 0.3), (("keys", ENTER), 1.5),  # a subshell, with the footer below
    ("ls; echo hello from bash", 0.3), (("keys", ENTER), 1.6),
    (("keys", PREFIX), 1.8),  # Ctrl-]: back to the prompt; bash keeps running in the footer
    ("alert -l crit -d 'grep -c ERROR app.log' errors in app.log", 0.3), (("keys", ENTER), 2.4),
    (("keys", ESC + "c"), 0.6),  # Alt-c: copy the alert's data
    (("keys", ESC + "v"), 1.2),  # Alt-v: paste it at the prompt
    (("keys", ENTER), 1.6),  # and run it
    ("alert -c", 0.3), (("keys", ENTER), 1.2),
    ("man jq", 0.3), (("keys", ENTER), 2.0), (("keys", "/"), 0.3), ("depth", 0.3),
    (("keys", ENTER), 1.6), (("keys", "q"), 1.0),
    ("theme matrix", 0.3), (("keys", ENTER), 1.6),
    ("jobs", 0.3), (("keys", ENTER), 2.5),
]


class Recorder:
    def __init__(self, fd: int, out: Path):
        self.fd = fd
        self.screen = pyte.Screen(WIDTH, HEIGHT)
        self.stream = pyte.ByteStream(self.screen)
        self.screen.write_process_input = self.reply  # device-attribute queries
        self.start = time.monotonic()
        self.fh = out.open("w", encoding="utf-8")
        header = {"version": 2, "width": WIDTH, "height": HEIGHT, "timestamp": int(time.time()),
                  "env": {"TERM": "xterm-256color", "SHELL": "/bin/bash"}, "title": "ShellCraft"}
        self.fh.write(json.dumps(header) + "\n")
        self.decode = codecs.getincrementaldecoder("utf-8")(errors="replace").decode

    def reply(self, data: str) -> None:
        os.write(self.fd, data.encode())

    def pump(self, seconds: float) -> None:
        """Read the shell's output for `seconds`, record it and answer cursor-position queries."""
        end = time.monotonic() + seconds
        while True:
            left = end - time.monotonic()
            if left <= 0:
                return
            ready, _, _ = select.select([self.fd], [], [], min(left, 0.05))
            if not ready:
                continue
            try:
                data = os.read(self.fd, 65536)
            except OSError:
                return
            if not data:
                return
            if b"\x1b[6n" in data:  # where is the cursor? (prompt_toolkit asks)
                self.stream.feed(data.replace(b"\x1b[6n", b""))
                y, x = self.screen.cursor.y + 1, self.screen.cursor.x + 1
                os.write(self.fd, f"\x1b[{y};{x}R".encode())
            else:
                self.stream.feed(data)
            text = self.decode(data)  # an incomplete UTF-8 sequence waits for the next read
            if text:
                self.fh.write(json.dumps([round(time.monotonic() - self.start, 6), "o", text]) + "\n")

    def type(self, text: str) -> None:
        for ch in text:
            os.write(self.fd, ch.encode())
            self.pump(TYPE_DELAY)

    def close(self) -> None:
        self.fh.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("-o", "--output", type=Path, default=ROOT / "docs" / "demo.cast")
    args = parser.parse_args(argv)
    if sys.platform == "win32":
        print("tools/demo.py needs a pty: run it on Linux or macOS", file=sys.stderr)
        return 1
    home = Path(tempfile.mkdtemp(prefix="shellcraft-demo-home-"))
    work = Path(tempfile.mkdtemp(prefix="shellcraft-demo-"))
    for name, text in FILES.items():
        (work / name).write_text(text, encoding="utf-8")
    # No user@host in the prompt; the OS keyring off (a stub package keeps the banner's "keyring is
    # not installed" hint, which names this machine's Python, out of the picture).
    config = {"theme": "cyberpunk", "settings": {"clear_on_start": True, "keyring": False},
              "prompt": {"left": ["name", "path", "git", "status", "duration"]}}
    (home / "config.json").write_text(json.dumps(config))
    stub = home / "stub" / "keyring"
    stub.mkdir(parents=True)
    (stub / "__init__.py").write_text("")
    env = dict(os.environ, SHELLCRAFT_HOME=str(home), TERM="xterm-256color", COLORTERM="truecolor",
               PS1="$ ", HOME=str(work), USER="demo", LOGNAME="demo",
               PYTHONPATH=str(home / "stub"))
    env.pop("SHELLCRAFT_MODULES", None)
    pid, fd = pty.fork()
    if pid == 0:  # the child: the shell, in the scratch folder
        os.chdir(work)
        os.execve(sys.executable, [sys.executable, str(ROOT / "main.py")], env)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", HEIGHT, WIDTH, 0, 0))
    rec = Recorder(fd, args.output)
    try:
        for step, wait in SCENES:
            if isinstance(step, tuple):
                os.write(fd, step[1].encode())
            elif isinstance(step, str):
                rec.type(step)
            rec.pump(wait)
        rec.type("exit")
        os.write(fd, ENTER.encode())
        rec.pump(1.5)
    finally:
        rec.close()
        try:
            os.kill(pid, 9)
        except OSError:
            pass
        os.waitpid(pid, 0)
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)
    print(f"wrote {args.output} ({time.monotonic() - rec.start:.0f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
