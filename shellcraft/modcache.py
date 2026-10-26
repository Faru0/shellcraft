"""A short memory for module answers, shared by the AI agents that call modules.

Two workers asking `queryDns example.com` a minute apart should not both spend an API call. The
MCP server keeps each answer in ~/.shellcraft/cache/ (owner-only, one small JSON file per
question) for the policy's `[cache] seconds` (300 by default). The question is the module, its
arguments, its input and the module file's modification time, so editing a module forgets what it
said. An agent passes `fresh: true` to skip the memory; errors are never remembered.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import time
from pathlib import Path

FOLDER = "cache"
MAX_ENTRY = 512 * 1024  # characters of one answer worth remembering
MAX_FILES = 400


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


def key(name: str, args: list[str], stdin: str, version: int = 0) -> str:
    raw = json.dumps([name, args, stdin, version], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:40]


def version_of(path: Path | None) -> int:
    try:
        return path.stat().st_mtime_ns if path is not None else 0
    except OSError:
        return 0


def get(k: str, ttl: float, home: Path | None = None) -> tuple[str, float] | None:
    """(the remembered answer, its age in seconds), or None."""
    try:
        data = json.loads((folder(home) / f"{k}.json").read_text(encoding="utf-8"))
        age = time.time() - float(data["at"])
        return (str(data["text"]), age) if 0 <= age <= ttl else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def put(k: str, name: str, text: str, ttl: float, home: Path | None = None) -> None:
    if len(text) > MAX_ENTRY:
        return
    base = folder(home)
    try:
        tmp = base / f".{k}.{os.getpid()}.tmp"
        tmp.write_text(json.dumps({"module": name, "at": time.time(), "text": text}, ensure_ascii=False),
                       encoding="utf-8")
        os.replace(tmp, base / f"{k}.json")
        _sweep(base, ttl)
    except OSError:
        pass


def _sweep(base: Path, ttl: float) -> None:
    files = sorted(base.glob("*.json"), key=lambda p: p.stat().st_mtime)
    cutoff = time.time() - max(ttl, 60.0)
    for path in files[: max(0, len(files) - MAX_FILES)] + [p for p in files if p.stat().st_mtime < cutoff]:
        try:
            path.unlink()
        except OSError:
            pass


def clear(home: Path | None = None) -> int:
    n = 0
    for path in folder(home).glob("*.json"):
        try:
            path.unlink()
            n += 1
        except OSError:
            pass
    return n
