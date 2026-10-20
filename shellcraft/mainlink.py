"""A private channel into the main shell, for its lite subshells and for MCP servers.

The main shell listens on a local socket (Linux, macOS) or a named pipe (Windows), locked with a
random key. Requests are tiny tuples, `(name, *args)`, answered by the handlers the shell
registers (`handle`): the clip register (`("clip-get", N)`, `("clip-list",)`, `("clip-set",
TEXT)`), and the co-pilot's questions (the screen, the jobs, the history, opening a subshell,
running a command). An unknown request answers None; a handler that fails answers
`("error", message)`. Payloads stay in memory: nothing but the address goes to disk.

Who can reach it: a `sub shellcraft` gets the address and key in its environment; an MCP server
(a separate process, started by an AI client or by `mcp start`) reads them from
`~/.shellcraft/mainlink.json`, which the main shell writes at start (owner-only) and removes at
exit. The key is the guard on both platforms: multiprocessing's connection does an HMAC
challenge, so the key never crosses the socket, and a named pipe, which any local process can
open, still needs it. A file left behind by a shell that crashed names a dead pid or an address
nobody listens on; whoever reads it then deletes it.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import threading
import time
from multiprocessing.connection import Client, Listener
from pathlib import Path
from typing import Any, Callable

ADDRESS_ENV = "SHELLCRAFT_MAIN_LINK"
KEY_ENV = "SHELLCRAFT_MAIN_KEY"
TIMEOUT = 1.0  # seconds a lite shell waits for an answer
FILE = "mainlink.json"  # under the config folder: how an MCP server finds the main shell
SCHEMA = 1


class MainLink:
    """The main shell's side: a listener thread answering its lite subshells and MCP servers."""

    def __init__(self, bus: Any, home: Path | None = None):
        self.bus = bus
        self.key = secrets.token_bytes(32)
        family = "AF_PIPE" if sys.platform == "win32" else "AF_UNIX"
        self.listener = Listener(family=family, authkey=self.key)
        self.address = self.listener.address
        self.handlers: dict[str, Callable[..., Any]] = {}
        self.handle("clip-get", lambda slot=0: bus.clip_at(slot if isinstance(slot, int) else 0))
        self.handle("clip-list", bus.clips)
        self.handle("clip-set", self._clip_set)
        self._closed = False
        self._thread = threading.Thread(target=self._serve, daemon=True, name="shellcraft:main-link")
        self._thread.start()
        self.path = publish(self.address, self.key, home)  # None when the file couldn't be written

    def handle(self, name: str, fn: Callable[..., Any]) -> None:
        """Answer `(name, *args)` with `fn(*args)` (a picklable value, or ("error", text))."""
        self.handlers[name] = fn

    @property
    def env(self) -> dict[str, str]:
        """What a `sub shellcraft` (or the `mcp start` child) needs to reach this shell."""
        return {ADDRESS_ENV: str(self.address), KEY_ENV: self.key.hex()}

    def _clip_set(self, text: Any = "") -> Any:
        if not isinstance(text, str):
            return None
        self.bus.clip = text
        return True

    def _serve(self) -> None:
        while not self._closed:
            try:
                conn = self.listener.accept()  # checks the key; a wrong one raises
            except Exception:  # noqa: BLE001 — closed, or a client without the key
                if self._closed:
                    return
                continue
            # Each request on its own thread: opening a subshell or running a command must not
            # hold up a clip request from a lite shell.
            threading.Thread(target=self._one, args=(conn,), daemon=True, name="shellcraft:main-link-request").start()

    def _one(self, conn: Any) -> None:
        try:
            if conn.poll(TIMEOUT):
                conn.send(self._answer(conn.recv()))
        except Exception:  # noqa: BLE001 — a client that went away (or a probe that sent nothing)
            pass
        finally:
            conn.close()

    def _answer(self, request: Any) -> Any:
        if not isinstance(request, tuple) or not request or not isinstance(request[0], str):
            return None
        fn = self.handlers.get(request[0])
        if fn is None:
            return None
        try:
            return fn(*request[1:])
        except Exception as exc:  # noqa: BLE001 — the asker gets the reason, the shell goes on
            return ("error", f"{type(exc).__name__}: {exc}")

    def close(self) -> None:
        self._closed = True
        unpublish(self.path)
        try:
            self.listener.close()
        except OSError:
            pass


# ── the published address ─────────────────────────────────────────────────────

def _file(home: Path | None = None) -> Path:
    if home is None:
        from shellcraft.config import config_dir

        home = config_dir()
    return Path(home) / FILE


def publish(address: Any, key: bytes, home: Path | None = None) -> Path | None:
    """Write the address and key for MCP servers, readable by the owner only; None on failure."""
    path = _file(home)
    data = {"schema": SCHEMA, "pid": os.getpid(), "address": str(address), "key": key.hex(),
            "started": time.time(), "platform": sys.platform}
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return None
    return path


def unpublish(path: Path | None = None, home: Path | None = None) -> None:
    """Remove the published file, but only this process's own (a newer main shell may have
    written its own by now)."""
    path = path or _file(home)
    try:
        with open(path, encoding="utf-8") as fh:
            if json.load(fh).get("pid") != os.getpid():
                return
        os.unlink(path)
    except (OSError, ValueError):
        pass


def read_published(home: Path | None = None) -> tuple[str, bytes] | None:
    """The (address, key) of the main shell that published them, or None: no file, a bad one, or
    a stale one (its shell is gone, or nothing listens at the address), which is then deleted."""
    path = _file(home)
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if data.get("schema") != SCHEMA:
            raise ValueError("schema")
        address, key, pid = str(data["address"]), bytes.fromhex(data["key"]), int(data["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if _alive(pid) and _listening(address, key):
        return address, key
    try:
        os.unlink(path)
    except OSError:
        pass
    return None


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = k32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return False
        k32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _listening(address: str, key: bytes) -> bool:
    """Whether something answers the key at the address (a connection that sends nothing: the
    listener closes it after TIMEOUT)."""
    try:
        conn = Client(address, family="AF_PIPE" if sys.platform == "win32" else "AF_UNIX", authkey=key)
    except Exception:  # noqa: BLE001
        return False
    conn.close()
    return True


def connect_info() -> tuple[str, bytes] | None:
    """How to reach the main shell: the environment (a lite subshell, the `mcp start` child),
    else the published file (an MCP server any client started). None: no main shell."""
    address, key = os.environ.get(ADDRESS_ENV), os.environ.get(KEY_ENV)
    if address and key:
        try:
            return address, bytes.fromhex(key)
        except ValueError:
            pass
    return read_published()


def available() -> bool:
    """Whether this shell was started by a main shell it can reach (a lite subshell)."""
    return bool(os.environ.get(ADDRESS_ENV) and os.environ.get(KEY_ENV))


def ask(request: tuple, default: Any = None, timeout: float = TIMEOUT) -> Any:
    """Send one request to the main shell; `default` when it can't be reached or doesn't answer
    (an unknown request answers None, which counts as no answer)."""
    info = connect_info()
    if info is None:
        return default
    address, key = info
    try:
        conn = Client(address, family="AF_PIPE" if sys.platform == "win32" else "AF_UNIX", authkey=key)
    except Exception:  # noqa: BLE001 — main has gone, or the key is wrong
        return default
    try:
        conn.send(request)
        if conn.poll(timeout):
            reply = conn.recv()
            return default if reply is None else reply
        return default
    except Exception:  # noqa: BLE001
        return default
    finally:
        conn.close()
