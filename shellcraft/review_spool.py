"""The review spool: items the user flags for an AI (`mark`) and reviews an AI asks the user for
(`request_human_review`), shared by the interactive shell and every MCP server process.

The shell and its MCP servers are separate processes (`mcp start` runs one as a child; every AI
client launches its own `shellcraft --mcp`), so the items live on disk: one JSON file per item in
~/.shellcraft/review/, a folder only the user can read.

Race safety, with no shared memory and several writers:
- Creating writes a temp file and renames it into place (`os.replace`), so a reader sees a whole
  item or none, and readers never lock.
- Changing an item (a decision, a resolution) takes the item's lock file, made with
  O_CREAT | O_EXCL so only one process can hold it, re-reads the item, checks the change is
  allowed from its current status, bumps `rev`, writes and renames, and drops the lock. A lock
  left by a process that died is broken after STALE_LOCK seconds.
- On Windows a rename onto a file another process has open fails for a moment: it is retried.

Lifecycles: a `flag` goes pending -> resolved (the AI); a `review` goes pending -> approved or
rejected (the user), then optionally -> resolved (the AI). Items that are no longer pending are
deleted KEEP_DAYS after their last change; pending items never expire.
"""

from __future__ import annotations

import json
import os
import secrets
import stat
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = 1
FOLDER = "review"
KINDS = ("flag", "review")
STATUSES = ("pending", "approved", "rejected", "resolved")
PRIORITIES = ("low", "medium", "high")
ARTIFACT_KINDS = ("text", "diff", "file")
MAX_ARTIFACT = 256 * 1024  # characters kept per artifact: the end (where failures are)
KEEP_DAYS = 7
STALE_LOCK = 10.0  # seconds: an older lock was left by a process that died
LOCK_WAIT = 2.0  # seconds to wait for another process's lock
# Which status changes are allowed, per kind: (from, to).
TRANSITIONS = {
    "flag": {("pending", "resolved")},
    "review": {("pending", "approved"), ("pending", "rejected"), ("approved", "resolved"),
               ("rejected", "resolved"), ("pending", "resolved")},
}


class SpoolError(Exception):
    """A missing item, a change its status does not allow, or a lock that could not be had."""


def folder(home: Path | None = None) -> Path:
    """~/.shellcraft/review, created owner-only."""
    if home is None:
        from shellcraft.config import config_dir

        home = config_dir()
    path = home / FOLDER
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        try:
            os.chmod(path, stat.S_IRWXU)
        except OSError:
            pass
    return path


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_id() -> str:
    """r-YYYYMMDD-HHMMSS-xxxxxx: sortable, unique across processes, short enough to type."""
    return f"r-{datetime.now(UTC):%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"


def artifact(name: str, content: str, kind: str = "text") -> dict[str, Any]:
    """An artifact, its content cut to its last MAX_ARTIFACT characters (where a failing run's
    errors are), with the original size and whether it was cut."""
    text = str(content)
    return {"name": str(name) or "text", "kind": kind if kind in ARTIFACT_KINDS else "text",
            "content": text[-MAX_ARTIFACT:], "bytes": len(text.encode("utf-8", "replace")),
            "truncated": len(text) > MAX_ARTIFACT}


# ── reading ──────────────────────────────────────────────────────────────────

def _path(item_id: str, home: Path | None) -> Path:
    if not item_id or "/" in item_id or "\\" in item_id or item_id.startswith("."):
        raise SpoolError(f"not an item id: {item_id!r}")
    return folder(home) / f"{item_id}.json"


def _load(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("schema") == SCHEMA else None


def get(item_id: str, home: Path | None = None) -> dict[str, Any]:
    item = _load(_path(item_id, home))
    if item is None:
        raise SpoolError(f"no review item {item_id} (review lists them)")
    return item


def find(prefix: str, home: Path | None = None) -> dict[str, Any]:
    """An item by its id or by the start of it (`review show r-2026…a1b2`), when only one matches."""
    try:
        return get(prefix, home)
    except SpoolError:
        pass
    matches = [i for i in list_items(home=home) if i["id"].startswith(prefix) or i["id"].endswith(prefix)]
    if len(matches) == 1:
        return matches[0]
    raise SpoolError(f"no review item {prefix} (review lists them)" if not matches
                     else f"{prefix} matches {len(matches)} items: give more of the id")


def list_items(status: str | None = None, kind: str | None = None,
               home: Path | None = None) -> list[dict[str, Any]]:
    """Every item, newest first, narrowed by status and kind ("all" or None for any). Items that
    are no longer pending and older than KEEP_DAYS are deleted on the way."""
    base = folder(home)
    cutoff = time.time() - KEEP_DAYS * 86400
    items = []
    for path in base.glob("r-*.json"):
        item = _load(path)
        if item is None:
            continue
        if item.get("status") != "pending" and _timestamp(item.get("updated")) < cutoff:
            _remove(path)
            continue
        if status not in (None, "all") and item.get("status") != status:
            continue
        if kind not in (None, "all") and item.get("kind") != kind:
            continue
        items.append(item)
    items.sort(key=lambda i: (int(i.get("seq") or 0), i["id"]), reverse=True)  # newest first, to the nanosecond
    return items


def _timestamp(value: Any) -> float:
    try:
        return datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp()
    except ValueError:
        return time.time()


def signature(home: Path | None = None) -> tuple:
    """A cheap fingerprint of the folder (each item's name, mtime and size): it changes whenever
    an item is created, changed or deleted."""
    out = []
    try:
        for entry in os.scandir(folder(home)):
            if entry.name.startswith("r-") and entry.name.endswith(".json"):
                try:
                    st = entry.stat()
                except OSError:
                    continue
                out.append((entry.name, st.st_mtime_ns, st.st_size))
    except OSError:
        return ()
    return tuple(sorted(out))


def summary(item: dict[str, Any]) -> dict[str, Any]:
    """What the queue resource shows of an item: no artifact contents."""
    keys = ("id", "kind", "status", "type", "title", "priority", "created", "updated", "created_by")
    out = {k: item.get(k) for k in keys}
    out["artifacts"] = [{"name": a.get("name"), "kind": a.get("kind"), "bytes": a.get("bytes"),
                         "truncated": a.get("truncated")} for a in item.get("artifacts", [])]
    if item.get("decision"):
        out["decision"] = item["decision"]
    if item.get("result"):
        out["result"] = {"status": item["result"].get("status"), "bytes": item["result"].get("bytes")}
    return out


# ── writing ──────────────────────────────────────────────────────────────────

def create(kind: str, title: str, description: str = "", artifacts: list[dict[str, Any]] | None = None,
           priority: str = "medium", type_: str = "", created_by: str = "shell", cwd: str | None = None,
           home: Path | None = None) -> dict[str, Any]:
    if kind not in KINDS:
        raise SpoolError(f"kind must be one of {', '.join(KINDS)}")
    if priority not in PRIORITIES:
        raise SpoolError(f"priority must be one of {', '.join(PRIORITIES)}")
    stamp = now()
    item = {"schema": SCHEMA, "id": new_id(), "rev": 1, "seq": time.time_ns(), "kind": kind, "status": "pending",
            "type": str(type_ or ""), "title": str(title)[:200], "description": str(description),
            "priority": priority, "created": stamp, "updated": stamp, "created_by": created_by,
            "cwd": cwd if cwd is not None else os.getcwd(), "artifacts": list(artifacts or []),
            "decision": None, "resolution": None}
    _write(_path(item["id"], home), item)
    return item


MAX_RESULT = 256 * 1024  # characters of an approved command's output kept (the end)


def result_of(output: str, status: int, error: str | None, seconds: float, cwd: str) -> dict[str, Any]:
    """What an approved `run_in_shell` command produced, stored with the decision."""
    truncated = len(output) > MAX_RESULT
    return {"output": output[-MAX_RESULT:] if truncated else output, "bytes": len(output), "truncated": truncated,
            "status": status, "error": error, "seconds": round(seconds, 2), "cwd": cwd, "at": now()}


def decide(item_id: str, status: str, notes: str = "", by: str = "user",
           home: Path | None = None, result: dict[str, Any] | None = None) -> dict[str, Any]:
    """The user's answer to a review: approved or rejected, with notes; `result` (result_of) when
    approving ran a command, written in the same step so a waiting AI sees both at once."""
    if status not in ("approved", "rejected"):
        raise SpoolError("a decision is approved or rejected")

    def change(item: dict[str, Any]) -> None:
        if item.get("kind") != "review":
            raise SpoolError(f"{item['id']} is a flag for the AI, not a review request: nothing to approve")
        item["decision"] = {"status": status, "notes": notes, "by": by, "at": now()}
        if result is not None:
            item["result"] = result

    return _update(item_id, status, change, home)


def resolve(item_id: str, notes: str = "", by: str = "mcp", home: Path | None = None) -> dict[str, Any]:
    """Close an item: a flag the AI has dealt with, or a review the AI has acted on."""
    def change(item: dict[str, Any]) -> None:
        item["resolution"] = {"notes": notes, "by": by, "at": now()}

    return _update(item_id, "resolved", change, home)


def _update(item_id: str, new_status: str, change: Any, home: Path | None) -> dict[str, Any]:
    path = _path(item_id, home)
    with _Lock(path.with_suffix(".lock")):
        item = _load(path)
        if item is None:
            raise SpoolError(f"no review item {item_id} (review lists them)")
        old = item.get("status")
        change(item)  # first: it says when the change makes no sense for this kind of item
        if (old, new_status) not in TRANSITIONS.get(item.get("kind", ""), set()):
            raise SpoolError(f"{item_id} is {old}: it can't become {new_status}")
        item["status"] = new_status
        item["rev"] = int(item.get("rev", 0)) + 1
        item["updated"] = now()
        _write(path, item)
    return item


def _write(path: Path, item: dict[str, Any]) -> None:
    tmp = path.with_name(f".{path.stem}.tmp-{os.getpid()}-{secrets.token_hex(3)}")
    tmp.write_text(json.dumps(item, ensure_ascii=False, indent=1), encoding="utf-8")
    end = time.monotonic() + 1.0
    while True:
        try:
            os.replace(tmp, path)  # whole or not there: readers never see half an item
            return
        except PermissionError:  # Windows: another process has the old file open for a moment
            if time.monotonic() > end:
                _remove(tmp)
                raise
            time.sleep(0.02)


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


class _Lock:
    """An exclusive lock file for one item, across processes."""

    def __init__(self, path: Path):
        self.path = path

    def __enter__(self) -> _Lock:
        end = time.monotonic() + LOCK_WAIT
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > STALE_LOCK:
                        _remove(self.path)  # its writer died: break it
                        continue
                except OSError:
                    continue  # gone in the meantime: try again
                if time.monotonic() > end:
                    raise SpoolError(f"{self.path.stem} is being changed by another process; try again") from None
                time.sleep(0.02)

    def __exit__(self, *exc: object) -> None:
        _remove(self.path)
