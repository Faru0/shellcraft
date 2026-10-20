"""The brief an AI-opened subshell prints at start (the `open_subshell` MCP tool).

The main shell writes the AI's text to `~/.shellcraft/briefs/<token>.json` (owner-only), starts
`shellcraft --lite --brief FILE`, and that lite shell prints the brief and deletes the file. The
folder only ever holds briefs on their way to a subshell; one left behind (the subshell never
started) is swept after a day.
"""

from __future__ import annotations

import json
import os
import secrets
import stat
import sys
import time
from pathlib import Path
from typing import Any

FOLDER = "briefs"
SCHEMA = 1
MAX_BRIEF = 64 * 1024  # characters of text kept (the start: the AI's summary comes first)
SWEEP_AFTER = 86400.0  # seconds a brief nobody read may stay


def folder(home: Path | None = None) -> Path:
    if home is None:
        from shellcraft.config import config_dir

        home = config_dir()
    path = Path(home) / FOLDER
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        try:
            os.chmod(path, stat.S_IRWXU)
        except OSError:
            pass
    return path


def write(title: str, name: str, text: str, source: str, cwd: str, home: Path | None = None) -> Path:
    """Store a brief; returns its path (for `--brief`)."""
    if len(text) > MAX_BRIEF:
        text = text[:MAX_BRIEF] + f"\n… [cut: the brief went on for {len(text) - MAX_BRIEF} more characters]\n"
    data = {"schema": SCHEMA, "title": title, "name": name, "text": text, "source": source,
            "created": time.time(), "cwd": cwd}
    path = folder(home) / f"{int(time.time())}-{secrets.token_hex(4)}.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    return path


def read(path: Path | str) -> dict[str, Any] | None:
    """The brief at `path`, which is then deleted; None when it is missing or not a brief."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    try:
        os.unlink(path)
    except OSError:
        pass
    if not isinstance(data, dict) or data.get("schema") != SCHEMA or not isinstance(data.get("text"), str):
        return None
    return data


def remove(path: Path | str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def sweep(home: Path | None = None, older_than: float = SWEEP_AFTER) -> int:
    """Delete briefs older than `older_than` seconds; returns how many."""
    gone = 0
    try:
        entries = list(folder(home).glob("*.json"))
    except OSError:
        return 0
    for path in entries:
        try:
            if time.time() - path.stat().st_mtime > older_than:
                path.unlink()
                gone += 1
        except OSError:
            pass
    return gone
