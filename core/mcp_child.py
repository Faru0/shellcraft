"""The MCP HTTP server as a child process of the interactive shell (`mcp start` / `mcp stop`).

It is the same server as `python main.py --mcp-http HOST:PORT`, started with this interpreter and
this modules directory. It runs in its own process group, so Ctrl-C at the prompt doesn't reach
it, and its log goes to ~/.shellcraft/mcp-http.log instead of over the prompt. The shell stops it
when it exits. API keys set with `settings NAME` before `mcp start` are in its environment.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import IO, Any, Callable

import core
from core.config import config_dir

DEFAULT_ADDRESS = "127.0.0.1:8765"
STARTUP_TIMEOUT = 15.0  # seconds: importing mcp and uvicorn can take a while on a cold start


class StartError(Exception):
    """The server didn't start; the message says why (with the end of its log)."""


def log_path() -> Path:
    return config_dir() / "mcp-http.log"


def log_tail(lines: int = 20) -> str:
    try:
        text = log_path().read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:]) if lines else ""


def _url_host(host: str) -> str:
    return f"[{host}]" if ":" in host else host


def _url(host: str, port: int) -> str:
    return f"http://{_url_host(host)}:{port}/mcp"


class McpHttpChild:
    """One running server: the process, its address and its log file."""

    def __init__(self, host: str, port: int, modules_dir: Path, allow_system: bool = False):
        self.host, self.port = host, port
        self.url = _url(host, port)
        self.allow_system = allow_system
        self.started = time.time()
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._log: IO[bytes] = open(path, "wb")  # noqa: SIM115 — closed in stop()
        root = str(Path(core.__file__).resolve().parent.parent)
        env = dict(os.environ)
        # `-m core.cli` must import this checkout (or installed copy) from any cwd.
        env["PYTHONPATH"] = os.pathsep.join(p for p in (root, env.get("PYTHONPATH")) if p)
        env["PYTHONUNBUFFERED"] = "1"  # the log is complete even if the server is killed
        argv = [sys.executable, "-m", "core.cli", "--mcp-http", f"{_url_host(host)}:{port}",
                "--modules", str(modules_dir)]
        if allow_system:
            argv.append("--allow-system")
        # Its own process group: Ctrl-C at the prompt (sent to the whole foreground group on POSIX,
        # to every process on the console on Windows) must not stop the server.
        if sys.platform == "win32":
            group: dict[str, Any] = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}  # type: ignore[attr-defined]
        else:
            group = {"start_new_session": True}
        try:
            self.process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=self._log,
                                            stderr=subprocess.STDOUT, env=env, **group)
        except OSError:
            self._log.close()
            raise

    @property
    def pid(self) -> int:
        return self.process.pid

    def running(self) -> bool:
        return self.process.poll() is None

    def wait_ready(self, timeout: float = STARTUP_TIMEOUT) -> None:
        """Wait until the port accepts connections; raise StartError if the server exits first."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.running():
                code = self.process.returncode
                self.stop()
                raise StartError(f"the MCP server exited with code {code}:\n{log_tail(12)}")
            try:
                with socket.create_connection((self.host, self.port), timeout=0.25):
                    return
            except OSError:
                time.sleep(0.1)
        self.stop()
        raise StartError(f"the MCP server didn't start listening on {self.url} within {timeout:.0f} s:\n"
                         f"{log_tail(12)}")

    def stop(self, timeout: float = 5.0) -> int | None:
        """Stop the server (terminate, then kill); returns its exit code."""
        if self.running():
            self.process.terminate()
            try:
                self.process.wait(timeout)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if not self._log.closed:
            self._log.close()
        return self.process.returncode


def mcp_available() -> list[str]:
    """The packages the server needs that this Python doesn't have (empty when it can run)."""
    import importlib.util

    return [name for name in ("mcp", "mcp_types") if importlib.util.find_spec(name) is None]


def start(address: str, modules_dir: Path, allow_system: bool = False,
          on_spawn: Callable[[McpHttpChild], None] | None = None) -> McpHttpChild:
    """Start the server and wait until it listens; raises StartError (or AddressError).

    `on_spawn` gets the process as soon as it exists, before it is ready, so the shell tracks
    (and stops) it even if Ctrl-C abandons this call while it waits.
    """
    missing = mcp_available()
    if missing:
        raise StartError(f"the MCP server needs the 'mcp' package, which {sys.executable} doesn't have "
                         f"(missing: {', '.join(missing)}); install it with: "
                         f"{sys.executable} -m pip install -r requirements.txt")
    from core.mcp_server import parse_http_address

    host, port = parse_http_address(address)  # AddressError: loopback only, like --mcp-http
    try:
        with socket.create_connection((host, port), timeout=0.25):
            raise StartError(f"{_url(host, port)} is already in use (another server?); "
                             "pick another port: mcp start HOST:PORT")
    except OSError:
        pass  # nothing listening: free
    child = McpHttpChild(host, port, modules_dir, allow_system)
    if on_spawn is not None:
        on_spawn(child)
    child.wait_ready()
    return child
