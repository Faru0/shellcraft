"""Record the second demo: what is ShellCraft's own, written as an asciinema v2 `.cast`.

    python tools/demo3.py                    writes docs/demo3.cast
    python tools/demo3.py -o /tmp/x.cast     somewhere else
    python tools/demo3.py --screens DIR      also keeps the screen after every step, as text
    python tools/cast2gif.py docs/demo3.cast the GIF, docs/demo3.gif (needs Pillow; `agg` does it too)

It is recorded the way tools/demo.py records the first one (same pty, same size, same theme), in a
throwaway home folder, so your config, history, keys and files never appear. This one starts with the
MCP server and `ask`: the AI reads the screen, runs a command in the shell, raises an alert and leaves
the next command for Alt-v. Then it shows the `~/.shellcraft` folder (the rc file, a func, a module of
your own), a request pasted from a browser's "Copy as cURL", pipes and redirection, environment
variables and settings.

Nothing leaves the machine. The API is a small server this script starts on 127.0.0.1, and the AI
is tools/demo3_ai.py, a stand-in for the Claude Code client whose sentences are scripted and whose
tool calls are real (see its first lines).
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import pty
import shutil
import struct
import sys
import tempfile
import termios
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import demo
from demo import ENTER, ESC, HEIGHT, ROOT, WIDTH, Recorder

PORT = 8000
API = f"http://localhost:{PORT}"
TYPE_DELAY = 0.035  # seconds a character: there is more to show than in the first demo
PASTE = "paste"  # ("paste", TEXT): put on the line at once, as a paste from the clipboard is

USERS = [
    {"id": 1, "name": "ada", "team": "core", "admin": True},
    {"id": 2, "name": "lin", "team": "ops", "admin": False},
    {"id": 3, "name": "kim", "team": "sec", "admin": True},
]
ANSWERS = {
    "/api/users": {"data": USERS, "total": len(USERS)},
    "/api/health": {"status": "degraded", "checks": {"api": "ok", "cache": "ok", "db": "timeout"}},
}

# The scratch folder is the home folder of the recording: NAME -> text.
FILES = {
    "app.log": "".join(f"2026-11-13 09:00:{i:02d} {lvl} {msg}\n" for i, (lvl, msg) in enumerate([
        ("INFO", "service started"), ("ERROR", "db timeout"), ("WARN", "slow request /api/users"),
        ("ERROR", "db timeout"), ("INFO", "cache warmed"), ("ERROR", "disk full on /var"),
        ("ERROR", "db timeout"), ("INFO", "health check ok")])),
    ".shellcraft/rc": f"""\
# ~/.shellcraft/rc runs at every start, like ~/.bashrc
export API={API}

errors() {{          # a function, written as in bash
  grep ERROR "$1" | cut -d ' ' -f 4- | sort | uniq -c
}}
""",
    ".shellcraft/funcs/apicheck.func": """\
# func: apicheck
# description: API status and the errors in a log
# usage: apicheck LOGFILE
curl -s $API/api/health --jq status
grep -c ERROR $1
""",
    ".shellcraft/modules/shout.py": '''\
"""shout: a command of your own is one Python file."""

SUMMARY = "Upper-case what is piped in"


def run(args, stdin):
    return stdin.upper()
''',
}

# What a browser's developer tools put on the clipboard for "Copy as cURL (bash)".
COPIED = (f"curl '{API}/api/users' \\\n"
          "  -H 'accept: application/json' \\\n"
          "  -H 'authorization: Bearer demo-token' \\\n"
          "  --compressed")

# (what to type, seconds to wait after it). A str is typed one character at a time; ("keys", TEXT)
# is sent at once (control keys); ("paste", TEXT) arrives as a paste.
SCENES: list[tuple[object, float]] = [
    (None, 2.2),  # the banner
    # MCP and the AI come first.
    ("mcp start", 0.25), (("keys", ENTER), 3.2),
    ("ask what is filling app.log with errors?", 0.4), (("keys", ENTER), 12.5),
    (("keys", ESC + "v"), 1.8),  # Alt-v: what the AI left in the clip register
    (("keys", ENTER), 2.6),
    ("ai log", 0.25), (("keys", ENTER), 3.6),
    ("alert -c", 0.25), (("keys", ENTER), 1.4),  # the alert has been read
    # Your folder, and the rc file with a function in it.
    ("clear", 0.2), (("keys", ENTER), 0.5),
    ("ls ~/.shellcraft", 0.25), (("keys", ENTER), 1.8),
    ("cat ~/.shellcraft/rc", 0.25), (("keys", ENTER), 2.6),
    ("errors app.log", 0.25), (("keys", ENTER), 2.0),  # the function the rc file defined
    # A func: a few steps kept in a file, run by its name.
    ("clear", 0.2), (("keys", ENTER), 0.5),
    ("cat ~/.shellcraft/funcs/apicheck.func", 0.25), (("keys", ENTER), 2.2),
    ("func list", 0.25), (("keys", ENTER), 2.0),
    ("apicheck app.log", 0.25), (("keys", ENTER), 2.0),
    # A module of your own, and what each name is.
    ("clear", 0.2), (("keys", ENTER), 0.5),
    ("cat ~/.shellcraft/modules/shout.py", 0.25), (("keys", ENTER), 2.2),
    ("echo a command of my own | shout", 0.25), (("keys", ENTER), 1.6),
    ("type errors apicheck shout curl", 0.25), (("keys", ENTER), 3.0),
    # A request from the browser, pipes and redirection.
    ("clear", 0.2), (("keys", ENTER), 0.5),
    ((PASTE, COPIED), 2.0), (("keys", ENTER), 2.4),
    ("curl -s $API/api/users --jq data | jq -f '[*].name' > names.txt", 0.25), (("keys", ENTER), 1.0),
    ("sort -r < names.txt | tee sorted.txt", 0.25), (("keys", ENTER), 1.6),
    ("ls /nope 2> err.txt; cat err.txt", 0.25), (("keys", ENTER), 1.8),
    # Variables and settings.
    ("clear", 0.2), (("keys", ENTER), 0.5),
    ('teams = ["core", "ops", "sec"]', 0.25), (("keys", ENTER), 0.8),  # a list, the Python way
    ('for -py t in teams: print(f"team {t}")', 0.25), (("keys", ENTER), 1.8),
    ("env set REGION=eu-west-1", 0.25), (("keys", ENTER), 1.0),
    ("env", 0.25), (("keys", ENTER), 2.2),
    ("settings system_commands on", 0.25), (("keys", ENTER), 1.4),  # now programs on PATH run too
    ("uname -s", 0.25), (("keys", ENTER), 2.0),
]


class Api(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        answer = ANSWERS.get(self.path.split("?")[0])
        body = json.dumps(answer if answer is not None else {"error": "not found"}).encode()
        self.send_response(200 if answer is not None else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


def stand_in(folder: Path) -> Path:
    """The program `ask` starts as its client: tools/demo3_ai.py with this interpreter."""
    path = folder / "claude"
    path.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{ROOT / "tools" / "demo3_ai.py"}" "$@"\n')
    path.chmod(0o755)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("-o", "--output", type=Path, default=ROOT / "docs" / "demo3.cast")
    parser.add_argument("--screens", type=Path, metavar="DIR", help="keep the screen after every step, as text")
    parser.add_argument("--fast", action="store_true", help="no typing pace and short waits: a quick check")
    args = parser.parse_args(argv)
    if sys.platform == "win32":
        print("tools/demo3.py needs a pty: run it on Linux or macOS", file=sys.stderr)
        return 1
    try:
        api = ThreadingHTTPServer(("127.0.0.1", PORT), Api)
    except OSError as exc:
        print(f"tools/demo3.py: port {PORT} is taken ({exc.strerror}); free it and run again", file=sys.stderr)
        return 1
    threading.Thread(target=api.serve_forever, daemon=True).start()
    work = Path(tempfile.mkdtemp(prefix="home-"))  # shown where a command names a file in it
    aside = Path(tempfile.mkdtemp(prefix="shellcraft-demo-ai-"))
    home = work / ".shellcraft"
    for name, text in FILES.items():
        (work / name).parent.mkdir(parents=True, exist_ok=True)
        (work / name).write_text(text, encoding="utf-8")
    # No user@host in the prompt.
    config = {"theme": "cyberpunk", "settings": {"clear_on_start": True},
              "prompt": {"left": ["name", "path", "git", "status", "duration"]}}
    (home / "config.json").write_text(json.dumps(config))
    env = dict(os.environ, SHELLCRAFT_HOME=str(home), TERM="xterm-256color", COLORTERM="truecolor",
               PS1="$ ", HOME=str(work), USER="demo", LOGNAME="demo", SHELLCRAFT_CLAUDE=str(stand_in(aside)))
    env.pop("SHELLCRAFT_MODULES", None)
    demo.TYPE_DELAY = 0.002 if args.fast else TYPE_DELAY
    if args.screens:
        args.screens.mkdir(parents=True, exist_ok=True)
    pid, fd = pty.fork()
    if pid == 0:  # the child: the shell, in the scratch folder
        os.chdir(work)
        os.execve(sys.executable, [sys.executable, str(ROOT / "main.py")], env)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", HEIGHT, WIDTH, 0, 0))
    rec = Recorder(fd, args.output)
    try:
        for number, (step, wait) in enumerate(SCENES):
            if isinstance(step, tuple) and step[0] == PASTE:
                os.write(fd, f"\x1b[200~{step[1]}\x1b[201~".encode())
            elif isinstance(step, tuple):
                os.write(fd, step[1].encode())
            elif isinstance(step, str):
                rec.type(step)
            rec.pump(wait)
            if args.screens:
                shown = "\n".join(line.rstrip() for line in rec.screen.display)
                (args.screens / f"{number:02d}.txt").write_text(f"{step!r}\n{shown}\n", encoding="utf-8")
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
        api.shutdown()
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(aside, ignore_errors=True)
    print(f"wrote {args.output} ({time.monotonic() - rec.start:.0f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
