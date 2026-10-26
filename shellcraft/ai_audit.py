"""A log of what AI clients did here: one JSON line per tool call, in ~/.shellcraft/ai-audit.jsonl.

The MCP server writes a line for every tool call, and the shell writes one when you approve or
reject something an AI asked for. `ai log` reads it. The file is owner-only and, past MAX_BYTES,
moved to `ai-audit.jsonl.1` (one older generation is kept). Writing never raises: an audit that
cannot be written must not stop the tool call.
"""

from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path
from typing import Any

FILE = "ai-audit.jsonl"
MAX_BYTES = 1024 * 1024
MAX_SUMMARY = 200


def path(home: Path | None = None) -> Path:
    if home is None:
        from shellcraft.config import config_dir

        home = config_dir()
    return Path(home) / FILE


def record(tool: str, summary: str = "", agent: str = "", role: str = "", client: str = "", tier: str = "",
           decision: str = "", ok: bool | None = None, seconds: float | None = None,
           home: Path | None = None) -> None:
    entry: dict[str, Any] = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "tool": tool,
                             "summary": " ".join(str(summary).split())[:MAX_SUMMARY]}
    for key, value in (("agent", agent), ("role", role), ("client", client), ("tier", tier), ("decision", decision)):
        if value:
            entry[key] = value
    if ok is not None:
        entry["ok"] = ok
    if seconds is not None:
        entry["seconds"] = round(seconds, 2)
    target = path(home)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            if target.stat().st_size > MAX_BYTES:
                os.replace(target, target.with_name(target.name + ".1"))
        except OSError:
            pass
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def tail(n: int = 20, agent: str = "", home: Path | None = None) -> list[dict[str, Any]]:
    """The last `n` entries (oldest first), only `agent`'s when one is given."""
    entries: list[dict[str, Any]] = []
    target = path(home)
    for file in (target.with_name(target.name + ".1"), target):
        try:
            lines = file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict) and (not agent or entry.get("agent") == agent):
                entries.append(entry)
    return entries[-n:] if n > 0 else entries
