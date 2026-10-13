"""Alerts in the status panel, from subshells, nested ShellCrafts and AI clients over MCP.

The main shell runs an `AlertBus`: a short list of alerts (info / warning / critical), each with
a timeout, shown at the top of the panel under the prompt and of the footer inside a subshell.
It listens on a loopback TCP port with a random token; the address and the token are put in the
environment (`SHELLCRAFT_ALERT_ADDR`, `SHELLCRAFT_ALERT_TOKEN`), so every child the shell starts
(`sub bash`, `sub shellcraft`, `mcp start`) inherits them, and written to `~/.shellcraft/alert.addr`
(mode 600), so an MCP server started by an AI client on its own finds the running shell too.

Senders: the `alert` builtin (local in the main shell, forwarded by a nested one), the `alert`
command on the PATH of subshells (a shim that runs `python -m core.alerts`), the `shellcraft-alert`
console script, and the `send_tui_alert` MCP tool. The wire format is one JSON object per line:
`{"token": …, "level": …, "message": …, "timeout": …}`; the reply is `ok` or an error line.

TCP on 127.0.0.1 is used because it works the same on Linux, macOS and Windows; the token keeps
other local users and processes from pushing text into the shell.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import socketserver
import stat
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

LEVELS = ("info", "warning", "critical")
ICONS = {"info": "●", "warning": "▲", "critical": "✖"}
DEFAULT_TIMEOUT = {"info": 15.0, "warning": 60.0, "critical": None}  # None: stays until dismissed
MAX_ALERTS = 3  # shown at once; older ones of a lower level give way
MAX_MESSAGE = 300
ADDR_ENV, TOKEN_ENV = "SHELLCRAFT_ALERT_ADDR", "SHELLCRAFT_ALERT_TOKEN"
ADDR_FILE = "alert.addr"


@dataclass
class Alert:
    level: str
    message: str
    created: float = field(default_factory=time.monotonic)
    timeout: float | None = None  # seconds; None never expires
    source: str = ""  # who sent it, for `alert -l`

    @property
    def expires(self) -> float | None:
        return None if self.timeout is None else self.created + self.timeout

    @property
    def remaining(self) -> float | None:
        return None if self.expires is None else max(0.0, self.expires - time.monotonic())

    @property
    def priority(self) -> int:
        return LEVELS.index(self.level)


class AlertBus:
    """The alerts of one shell, and the listener other processes send them through."""

    def __init__(self) -> None:
        self.alerts: list[Alert] = []
        self.on_change: Callable[[], None] | None = None  # the shell redraws the panel / footer
        self.token = secrets.token_urlsafe(18)
        self.address: tuple[str, int] | None = None
        self._lock = threading.Lock()
        self._server: socketserver.ThreadingTCPServer | None = None
        self._timer: threading.Timer | None = None
        self._addr_file: Path | None = None

    # ── the list ─────────────────────────────────────────────────────────────

    def post(self, message: str, level: str = "info", timeout: float | None | str = "default",
             source: str = "") -> Alert:
        """Add an alert. `timeout` in seconds, None for sticky, "default" for the level's own."""
        level = level if level in LEVELS else "info"
        message = " ".join(str(message).split())[:MAX_MESSAGE] or "(empty alert)"
        seconds = DEFAULT_TIMEOUT[level] if timeout == "default" else timeout
        alert = Alert(level, message, timeout=None if seconds is None else max(1.0, float(seconds)), source=source)
        with self._lock:
            self.alerts.append(alert)
            # Priority stacking: the most severe first, then the newest; the overflow is dropped.
            self.alerts.sort(key=lambda a: (-a.priority, -a.created))
            del self.alerts[MAX_ALERTS:]
            self._arm_timer()
        self._changed()
        return alert

    def dismiss(self, index: int | None = None) -> int:
        """Drop one alert (1-based, as shown) or all of them; returns how many went."""
        with self._lock:
            before = len(self.alerts)
            if index is None:
                self.alerts.clear()
            elif 1 <= index <= len(self.alerts):
                del self.alerts[index - 1]
            gone = before - len(self.alerts)
        if gone:
            self._changed()
        return gone

    def current(self) -> list[Alert]:
        """The alerts still valid, most severe first."""
        self._expire()
        with self._lock:
            return list(self.alerts)

    def _expire(self) -> None:
        now = time.monotonic()
        with self._lock:
            keep = [a for a in self.alerts if a.expires is None or a.expires > now]
            changed = len(keep) != len(self.alerts)
            self.alerts = keep
            if changed:
                self._arm_timer()
        if changed:
            self._changed()

    def _arm_timer(self) -> None:
        """A timer for the next expiry, so the panel is redrawn when an alert goes (with the lock)."""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        soon = [a.expires for a in self.alerts if a.expires is not None]
        if soon:
            delay = max(0.05, min(soon) - time.monotonic() + 0.05)
            self._timer = threading.Timer(delay, self._expire)
            self._timer.daemon = True
            self._timer.start()

    def _changed(self) -> None:
        if self.on_change is not None:
            try:
                self.on_change()
            except Exception:  # noqa: BLE001 — a redraw problem must not kill the listener
                pass

    # ── the listener ─────────────────────────────────────────────────────────

    def listen(self, home: Path | None = None) -> tuple[str, int]:
        """Start the loopback listener; export the address and token to this process's
        environment (children inherit them) and to `home/alert.addr` for MCP servers."""
        bus = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                line = self.rfile.readline(4096)
                reply = bus._receive(line, self.client_address[0])
                try:
                    self.wfile.write(reply.encode() + b"\n")
                except OSError:
                    pass

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
            allow_reuse_address = True

        self._server = Server(("127.0.0.1", 0), Handler)
        host, port = self._server.server_address[:2]
        self.address = (host, port)
        threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.5}, daemon=True,
                         name="shellcraft:alerts").start()
        os.environ[ADDR_ENV] = f"{host}:{port}"
        os.environ[TOKEN_ENV] = self.token
        if home is not None:
            try:
                home.mkdir(parents=True, exist_ok=True)
                self._addr_file = home / ADDR_FILE
                self._addr_file.write_text(f"{host}:{port}\n{self.token}\n", encoding="utf-8")
                if sys.platform != "win32":
                    os.chmod(self._addr_file, stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                self._addr_file = None
        return host, port

    def _receive(self, line: bytes, client: str) -> str:
        try:
            data = json.loads(line.decode("utf-8", errors="replace"))
        except ValueError:
            return "error: not a JSON line"
        if not isinstance(data, dict) or not secrets.compare_digest(str(data.get("token", "")), self.token):
            return "error: bad token"
        timeout: Any = data.get("timeout", "default")
        if timeout not in (None, "default"):
            try:
                timeout = float(timeout)
            except (TypeError, ValueError):
                return "error: timeout must be a number of seconds"
        self.post(str(data.get("message", "")), str(data.get("level", "info")), timeout,
                  source=str(data.get("source", "")) or client)
        return "ok"

    def close(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        for key in (ADDR_ENV, TOKEN_ENV):
            os.environ.pop(key, None)
        if self._addr_file is not None:
            try:
                self._addr_file.unlink()
            except OSError:
                pass


# ── sending (subshells, nested shells, the MCP tool, the console script) ──────

def depth_forward() -> int:
    """Nonzero inside `sub shellcraft`: the alert is forwarded to the main shell."""
    from core.jobs import depth

    return depth()


def target(home: Path | None = None) -> tuple[str, int, str] | None:
    """Where the running shell listens: from the environment, else from `home/alert.addr`."""
    addr, token = os.environ.get(ADDR_ENV), os.environ.get(TOKEN_ENV)
    if not addr or not token:
        if home is None:
            from core.config import config_dir

            home = config_dir()
        try:
            addr, token = (home / ADDR_FILE).read_text(encoding="utf-8").splitlines()[:2]
        except (OSError, ValueError):
            return None
    host, _, port = addr.rpartition(":")
    if not port.isdigit():
        return None
    return host or "127.0.0.1", int(port), token


def send(message: str, level: str = "info", timeout: float | None | str = "default",
         source: str = "", home: Path | None = None, connect_timeout: float = 2.0) -> str:
    """Send one alert to the running shell; returns its reply ("ok") or raises OSError."""
    where = target(home)
    if where is None:
        raise OSError("no ShellCraft shell is listening (start the interactive shell first)")
    host, port, token = where
    payload = {"token": token, "level": level, "message": message, "timeout": timeout, "source": source}
    with socket.create_connection((host, port), timeout=connect_timeout) as sock:
        sock.sendall(json.dumps(payload).encode("utf-8") + b"\n")
        reply = sock.makefile("rb").readline().decode("utf-8", errors="replace").strip()
    if reply != "ok":
        raise OSError(reply or "no reply")
    return reply


def parse_args(argv: list[str]) -> tuple[str, str, float | None | str, bool, bool]:
    """(message, level, timeout, clear, list) for `alert [-l LEVEL] [-t SECONDS] [-c] MESSAGE…`."""
    import argparse

    parser = argparse.ArgumentParser(prog="alert", add_help=True,
                                     description="Show an alert in ShellCraft's status panel.")
    parser.add_argument("-l", "--level", choices=LEVELS, default="info")
    parser.add_argument("-t", "--timeout", type=float, metavar="SECONDS",
                        help="seconds to show it (default: 15 info, 60 warning, critical stays)")
    parser.add_argument("-s", "--sticky", action="store_true", help="stay until dismissed")
    parser.add_argument("-c", "--clear", action="store_true", help="dismiss every alert")
    parser.add_argument("--list", action="store_true", help="list the current alerts")
    parser.add_argument("message", nargs="*")
    opts = parser.parse_args(argv)
    timeout: float | None | str = "default"
    if opts.sticky:
        timeout = None
    elif opts.timeout is not None:
        timeout = opts.timeout
    return " ".join(opts.message), opts.level, timeout, opts.clear, opts.list


def main(argv: list[str] | None = None) -> int:
    """`python -m core.alerts` / `shellcraft-alert` / the `alert` shim on a subshell's PATH."""
    argv = sys.argv[1:] if argv is None else argv
    try:
        message, level, timeout, clear, listing = parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    if clear or listing:
        print("alert: -c and --list work inside ShellCraft only", file=sys.stderr)
        return 2
    if not message:
        print("alert: a message is needed", file=sys.stderr)
        return 2
    try:
        send(message, level, timeout, source=f"pid {os.getppid()}")
    except OSError as exc:
        print(f"alert: {exc}", file=sys.stderr)
        return 1
    return 0


# ── the `alert` command on a subshell's PATH ─────────────────────────────────

def shim_dir(home: Path) -> Path:
    """A folder with an `alert` command that reaches this shell, to prepend to a subshell's PATH:
    a shell script on POSIX, `alert.cmd` on Windows, both running `python -m core.alerts` with
    this checkout (or installed copy) on PYTHONPATH."""
    import core

    root = str(Path(core.__file__).resolve().parent.parent)
    folder = home / "bin"
    folder.mkdir(parents=True, exist_ok=True)
    python = sys.executable
    if sys.platform == "win32":
        script = folder / "alert.cmd"
        script.write_text(f'@echo off\r\nset "PYTHONPATH={root};%PYTHONPATH%"\r\n"{python}" -m core.alerts %*\r\n',
                          encoding="utf-8")
    else:
        script = folder / "alert"
        body = f'#!/bin/sh\nPYTHONPATH="{root}${{PYTHONPATH:+:$PYTHONPATH}}" exec "{python}" -m core.alerts "$@"\n'
        script.write_text(body, encoding="utf-8")
        os.chmod(script, 0o755)
    return folder


def with_shim(env: dict[str, str], home: Path) -> dict[str, str]:
    """`env` with the shim folder first on PATH."""
    try:
        folder = str(shim_dir(home))
    except OSError:
        return env
    env = dict(env)
    env["PATH"] = folder + os.pathsep + env.get("PATH", "")
    return env


if __name__ == "__main__":
    sys.exit(main())
