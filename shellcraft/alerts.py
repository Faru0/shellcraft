"""Alerts in the status panel, from subshells, nested ShellCrafts and AI clients over MCP.

The main shell runs an `AlertBus`: a short list of alerts (info / warning / critical), each with
a timeout, shown at the top of the panel under the prompt and of the footer inside a subshell.
Alerts reach it through a **spool folder**, `~/.shellcraft/alerts/` (owner-only): a sender drops
one small JSON file there (written whole, then renamed into place) and the shell's watcher thread
picks it up within a quarter of a second and deletes it. No port, no token: the folder's
permissions are the trust boundary, and it works the same on Linux, macOS and Windows.

Senders: the `alert` builtin (local in the main shell, forwarded by a nested one), the `alert`
command on the PATH of subshells (a shim that runs `python -m shellcraft.alerts`), the `shellcraft-alert`
console script, and the `send_tui_alert` MCP tool. Each says where it came from (`source`): the
shell, `sub 2 (bash)`, a nested ShellCraft, an outside script with its pid, or the MCP client's
name. A file older than STALE seconds (left by a shell that was not running) is dropped unread.
"""

from __future__ import annotations

import json
import os
import re
import secrets
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
MAX_CONTENT = 64 * 1024  # characters of `content` kept with an alert (what a right-click copies)
SPOOL = "alerts"  # the folder under ~/.shellcraft
SUB_ENV = "SHELLCRAFT_SUB"  # "N:command" in a subshell's environment: the sender's label
POLL = 0.25  # seconds between looks at the spool
STALE = 300.0  # a spooled file older than this (no shell was running) is dropped


@dataclass
class Alert:
    level: str
    message: str
    created: float = field(default_factory=time.monotonic)
    timeout: float | None = None  # seconds; None never expires
    source: str = ""  # who sent it, for `alert -l`
    content: str = ""  # an optional payload (a path, a command, JSON…): a right-click copies it

    @property
    def expires(self) -> float | None:
        return None if self.timeout is None else self.created + self.timeout

    @property
    def remaining(self) -> float | None:
        return None if self.expires is None else max(0.0, self.expires - time.monotonic())

    @property
    def priority(self) -> int:
        return LEVELS.index(self.level)

    @property
    def job(self) -> int | None:
        """The subshell number the alert came from (`sub 2 (bash)`), else None."""
        m = re.match(r"sub (\d+) ", self.source)
        return int(m.group(1)) if m else None


class AlertBus:
    """The alerts of one shell, and the listener other processes send them through."""

    def __init__(self) -> None:
        self.alerts: list[Alert] = []
        self.expanded = False  # the panel shows every alert as a row (else the badge folds them)
        self.clip = ""  # the clip register: what a right-click on an alert copied (Alt-v pastes it)
        self.copied: Alert | None = None  # the alert just copied, for the "copied" hint on its row
        self.on_change: Callable[[], None] | None = None  # the shell redraws the panel / footer
        self.on_request: Callable[[dict[str, Any]], None] | None = None  # `sub` asked for by a nested shell
        self.spool: Path | None = None
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._stop = threading.Event()
        self._watcher: threading.Thread | None = None

    # ── the list ─────────────────────────────────────────────────────────────

    def post(self, message: str, level: str = "info", timeout: float | None | str = "default",
             source: str = "", content: str = "") -> Alert:
        """Add an alert. `timeout` in seconds, None for sticky, "default" for the level's own."""
        level = level if level in LEVELS else "info"
        message = " ".join(str(message).split())[:MAX_MESSAGE] or "(empty alert)"
        seconds = DEFAULT_TIMEOUT[level] if timeout == "default" else timeout
        alert = Alert(level, message, timeout=None if seconds is None else max(1.0, float(seconds)), source=source,
                      content=str(content or "")[:MAX_CONTENT])
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

    def remove(self, alert: Alert) -> bool:
        """Drop this alert (a click on its ×, or a right-click): whether it was still there."""
        with self._lock:
            found = any(a is alert for a in self.alerts)
            self.alerts = [a for a in self.alerts if a is not alert]
        if found:
            self._changed()
        return found

    def copy(self, alert: Alert) -> str:
        """A right-click on an alert: its content (or its message) goes into the clip register,
        for Alt-v at the prompt, Ctrl-] v in a subshell, `clip` and `$CLIP`. Returns the text."""
        self.clip = alert.content or alert.message
        self.copied = alert
        self._changed()
        return self.clip

    def toggle(self) -> bool:
        """Fold or unfold the alert rows of the panel (a click on the badge); the new state."""
        self.expanded = not self.expanded
        self._changed()
        return self.expanded

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

    # ── the spool ────────────────────────────────────────────────────────────

    def watch(self, home: Path) -> Path:
        """Create the spool folder (owner-only) and start the thread that reads it."""
        self.spool = spool_dir(home)
        self._stop.clear()
        self._watcher = threading.Thread(target=self._poll, daemon=True, name="shellcraft:alerts")
        self._watcher.start()
        return self.spool

    def _poll(self) -> None:
        while not self._stop.wait(POLL):
            self.drain()

    def drain(self) -> int:
        """Read and delete every spooled alert now; returns how many were posted."""
        if self.spool is None:
            return 0
        try:
            files = sorted(p for p in self.spool.iterdir() if p.suffix == ".json")
        except OSError:
            return 0
        posted = 0
        for path in files:
            try:
                raw = path.read_text(encoding="utf-8")
            except OSError:
                continue
            try:
                path.unlink()
            except OSError:
                pass
            try:
                data = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(data, dict):
                continue
            if time.time() - float(data.get("time", 0) or 0) > STALE:
                continue  # left behind while no shell was running
            if data.get("kind") == "sub":  # a nested shell asks the main one to open a subshell
                if self.on_request is not None and isinstance(data.get("args"), list):
                    try:
                        self.on_request(data)
                    except Exception:  # noqa: BLE001 — a bad request must not kill the listener
                        pass
                continue
            timeout: Any = data.get("timeout", "default")
            if timeout not in (None, "default"):
                try:
                    timeout = float(timeout)
                except (TypeError, ValueError):
                    timeout = "default"
            content = data.get("content", "")
            self.post(str(data.get("message", "")), str(data.get("level", "info")), timeout,
                      source=str(data.get("source", "")), content=content if isinstance(content, str) else "")
            posted += 1
        return posted

    def close(self) -> None:
        self._stop.set()
        if self._timer is not None:
            self._timer.cancel()
        if self._watcher is not None:
            self._watcher.join(POLL * 2)
            self._watcher = None


# ── sending (subshells, nested shells, the MCP tool, the console script) ──────

def depth_forward() -> int:
    """Nonzero inside `sub shellcraft`: the alert is forwarded to the main shell."""
    from shellcraft.jobs import depth

    return depth()


def spool_dir(home: Path | None = None) -> Path:
    """`home/alerts`, created owner-only."""
    if home is None:
        from shellcraft.config import config_dir

        home = config_dir()
    folder = home / SPOOL
    folder.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        try:
            os.chmod(folder, stat.S_IRWXU)
        except OSError:
            pass
    return folder


def source_label() -> str:
    """Who is sending, from the environment: `sub 2 (bash)` inside a subshell, else the pid."""
    sub = os.environ.get(SUB_ENV, "")
    number, _, command = sub.partition(":")
    if number.isdigit():
        return f"sub {number} ({' '.join(command.split()[:2]) or 'program'})"
    return f"pid {os.getppid()}"


def send(message: str, level: str = "info", timeout: float | None | str = "default",
         source: str = "", home: Path | None = None, content: str = "") -> Path:
    """Drop one alert into the spool for the running shell; returns the file written. Raises
    OSError when the folder cannot be written. `content` is the optional payload a right-click
    on the alert copies (a path, a command, JSON…)."""
    payload: dict[str, Any] = {"time": time.time(), "level": level, "message": message, "timeout": timeout,
                               "source": source or source_label()}
    if content:
        payload["content"] = content[:MAX_CONTENT]
    return _spool(payload, home)


def request_sub(args: list[str], cwd: str, home: Path | None = None) -> Path:
    """Inside `sub shellcraft`: ask the main shell to open `sub ARGS` (it lists the subshell under
    main and switches to it). The request travels through the spool like an alert."""
    return _spool({"time": time.time(), "kind": "sub", "args": [str(a) for a in args], "cwd": cwd,
                   "source": source_label()}, home)


def _spool(payload: dict[str, Any], home: Path | None) -> Path:
    folder = spool_dir(home)
    name = f"{time.time():.6f}-{os.getpid()}-{secrets.token_hex(3)}"
    tmp = folder / f"{name}.tmp"
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    final = folder / f"{name}.json"
    os.replace(tmp, final)  # whole or not there: the watcher never sees a half-written file
    return final


def parse_args(argv: list[str]) -> tuple[str, str, float | None | str, bool, bool, str]:
    """(message, level, timeout, clear, list, content) for
    `alert [-l LEVEL] [-t SECONDS] [-C CONTENT] [-c] MESSAGE…`."""
    import argparse

    parser = argparse.ArgumentParser(prog="alert", add_help=True,
                                     description="Show an alert in ShellCraft's status panel.")
    parser.add_argument("-l", "--level", choices=LEVELS, default="info")
    parser.add_argument("-t", "--timeout", type=float, metavar="SECONDS",
                        help="seconds to show it (default: 15 info, 60 warning, critical stays)")
    parser.add_argument("-s", "--sticky", action="store_true", help="stay until dismissed")
    parser.add_argument("-c", "--clear", action="store_true", help="dismiss every alert")
    parser.add_argument("--list", action="store_true", help="list the current alerts")
    parser.add_argument("-C", "--content", default="", metavar="TEXT",
                        help="a payload a right-click on the alert copies (- reads it from stdin)")
    parser.add_argument("message", nargs="*")
    opts = parser.parse_args(argv)
    timeout: float | None | str = "default"
    if opts.sticky:
        timeout = None
    elif opts.timeout is not None:
        timeout = opts.timeout
    return " ".join(opts.message), opts.level, timeout, opts.clear, opts.list, opts.content


def main(argv: list[str] | None = None) -> int:
    """`python -m shellcraft.alerts` / `shellcraft-alert` / the `alert` shim on a subshell's PATH."""
    argv = sys.argv[1:] if argv is None else argv
    try:
        message, level, timeout, clear, listing, content = parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    if clear or listing:
        print("alert: -c and --list work inside ShellCraft only", file=sys.stderr)
        return 2
    if not message:
        print("alert: a message is needed", file=sys.stderr)
        return 2
    if content == "-":  # `make 2>&1 | alert -l warning build failed -C -`
        content = sys.stdin.read()
    try:
        send(message, level, timeout, content=content)
    except OSError as exc:
        print(f"alert: cannot write the alert: {exc}", file=sys.stderr)
        return 1
    return 0


# ── the `alert` command on a subshell's PATH ─────────────────────────────────

def shim_dir(home: Path) -> Path:
    """A folder with an `alert` command that reaches this shell, to prepend to a subshell's PATH:
    a shell script on POSIX, `alert.cmd` on Windows, both running `python -m shellcraft.alerts` with
    this checkout (or installed copy) on PYTHONPATH."""
    import shellcraft

    root = str(Path(shellcraft.__file__).resolve().parent.parent)
    folder = home / "bin"
    folder.mkdir(parents=True, exist_ok=True)
    python = sys.executable
    if sys.platform == "win32":
        script = folder / "alert.cmd"
        lines = ["@echo off", f'set "PYTHONPATH={root};%PYTHONPATH%"', f'"{python}" -m shellcraft.alerts %*']
        script.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    else:
        script = folder / "alert"
        body = (f'#!/bin/sh\nPYTHONPATH="{root}${{PYTHONPATH:+:$PYTHONPATH}}" '
                f'exec "{python}" -m shellcraft.alerts "$@"\n')
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
