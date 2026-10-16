"""A private channel from the main shell to its lite subshells (`sub shellcraft`).

The main shell listens on a local socket (Linux, macOS) or a named pipe (Windows), locked with
a random key that only the `sub shellcraft` it starts receive, in their environment. A lite
shell asks it for the clip register, or hands it a new one; the register stays in the main
shell's memory, never on disk. Requests are tiny tuples: ("clip-get",) → the text,
("clip-set", TEXT) → True.
"""

from __future__ import annotations

import os
import secrets
import sys
import threading
from multiprocessing.connection import Client, Listener
from typing import Any

ADDRESS_ENV = "SHELLCRAFT_MAIN_LINK"
KEY_ENV = "SHELLCRAFT_MAIN_KEY"
TIMEOUT = 1.0  # seconds a lite shell waits for an answer


class MainLink:
    """The main shell's side: a listener thread answering its lite subshells."""

    def __init__(self, bus: Any):
        self.bus = bus
        self.key = secrets.token_bytes(32)
        family = "AF_PIPE" if sys.platform == "win32" else "AF_UNIX"
        self.listener = Listener(family=family, authkey=self.key)
        self.address = self.listener.address
        self._closed = False
        self._thread = threading.Thread(target=self._serve, daemon=True, name="shellcraft:main-link")
        self._thread.start()

    @property
    def env(self) -> dict[str, str]:
        """What a `sub shellcraft` needs to reach this shell."""
        return {ADDRESS_ENV: str(self.address), KEY_ENV: self.key.hex()}

    def _serve(self) -> None:
        while not self._closed:
            try:
                conn = self.listener.accept()  # checks the key; a wrong one raises
            except Exception:  # noqa: BLE001 — closed, or a client without the key
                if self._closed:
                    return
                continue
            try:
                if conn.poll(TIMEOUT):
                    conn.send(self._answer(conn.recv()))
            except Exception:  # noqa: BLE001 — a client that went away
                pass
            finally:
                conn.close()

    def _answer(self, request: Any) -> Any:
        if not isinstance(request, tuple) or not request:
            return None
        if request[0] == "clip-get":
            return self.bus.clip
        if request[0] == "clip-set" and len(request) == 2 and isinstance(request[1], str):
            self.bus.clip = request[1]
            return True
        return None

    def close(self) -> None:
        self._closed = True
        try:
            self.listener.close()
        except OSError:
            pass


def available() -> bool:
    """Whether this shell was started by a main shell it can reach (a lite subshell)."""
    return bool(os.environ.get(ADDRESS_ENV) and os.environ.get(KEY_ENV))


def ask(request: tuple, default: Any = None) -> Any:
    """Send one request to the main shell; `default` when it can't be reached or doesn't answer."""
    address, key = os.environ.get(ADDRESS_ENV), os.environ.get(KEY_ENV)
    if not address or not key:
        return default
    try:
        conn = Client(address, family="AF_PIPE" if sys.platform == "win32" else "AF_UNIX",
                      authkey=bytes.fromhex(key))
    except Exception:  # noqa: BLE001 — main has gone, or the key is wrong
        return default
    try:
        conn.send(request)
        if conn.poll(TIMEOUT):
            reply = conn.recv()
            return default if reply is None else reply
        return default
    except Exception:  # noqa: BLE001
        return default
    finally:
        conn.close()
