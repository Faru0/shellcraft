"""The task board: work an orchestrating AI hands out and its workers pick up.

A task is one JSON file in ~/.shellcraft/tasks/, kept under the same rules as the review spool
(whole-file writes, an exclusive lock for every change, a signature that changes when anything
does; shellcraft.review_spool has the details). Several AI agents, each in its own process, can
share the board without stepping on each other:

- `claim` is exclusive. One agent gets a task; the others are told who has it and until when.
  A claim is a **lease** (LEASE seconds, renewed by every update). An agent that dies stops
  renewing, and after the lease anyone may claim the task again.
- A task may wait for others (`depends_on`): it cannot be claimed until they are done.
- Only the owner updates a task (progress notes, `working`, `blocked`, `done`, `failed`); the
  user can always cancel it, or finish it from the `tasks` command.

Status: open -> claimed -> working -> done | failed, with blocked as a stop on the way (the user
is told), and cancelled by the user. `release` puts a task back to open.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shellcraft import review_spool as spool

SCHEMA = spool.SCHEMA
FOLDER = "tasks"
STATUSES = ("open", "claimed", "working", "blocked", "done", "failed", "cancelled")
ACTIVE = ("open", "claimed", "working", "blocked")
DONE = ("done", "failed", "cancelled")
UPDATABLE = ("working", "blocked", "done", "failed")
LEASE = 300.0  # seconds a claim lasts without an update
MAX_LEASE = 3600.0
KEEP_DAYS = spool.KEEP_DAYS
MAX_NOTES = 50
MAX_TEXT = 20000


class TaskError(spool.SpoolError):
    """A missing task, or a change the task's state or owner does not allow."""


def folder(home: Path | None = None) -> Path:
    import os
    import stat
    import sys

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


def _path(task_id: str, home: Path | None) -> Path:
    if not task_id or "/" in task_id or "\\" in task_id or task_id.startswith("."):
        raise TaskError(f"not a task id: {task_id!r}")
    return folder(home) / f"{task_id}.json"


def new_id() -> str:
    return "t" + spool.new_id()[1:]


def get(task_id: str, home: Path | None = None) -> dict[str, Any]:
    task = spool.load_json(_path(task_id, home))
    if task is None:
        raise TaskError(f"no task {task_id} (tasks lists them)")
    return _view(task)


def find(prefix: str, home: Path | None = None) -> dict[str, Any]:
    """A task by its id or the start or end of it, when only one matches."""
    try:
        return get(prefix, home)
    except TaskError:
        pass
    matches = [t for t in list_tasks(home=home) if t["id"].startswith(prefix) or t["id"].endswith(prefix)]
    if len(matches) == 1:
        return matches[0]
    raise TaskError(f"no task {prefix} (tasks lists them)" if not matches
                    else f"{prefix} matches {len(matches)} tasks: give more of the id")


def _view(task: dict[str, Any]) -> dict[str, Any]:
    """The task with what is computed from the clock: is its lease over?"""
    lease = task.get("lease_until")
    task["stale"] = bool(task.get("status") in ("claimed", "working", "blocked") and lease and lease < time.time())
    return task


def list_tasks(status: str | None = None, owner: str | None = None, parent: str | None = None,
               claimable: bool = False, home: Path | None = None) -> list[dict[str, Any]]:
    """Tasks, highest priority first then oldest first. `status` may be a name, "active" or
    "all"/None. Finished tasks older than KEEP_DAYS are deleted on the way."""
    base = folder(home)
    cutoff = time.time() - KEEP_DAYS * 86400
    everything: dict[str, dict[str, Any]] = {}
    for path in base.glob("t-*.json"):
        task = spool.load_json(path)
        if task is None:
            continue
        if task.get("status") in DONE and spool._timestamp(task.get("updated")) < cutoff:
            spool.remove_file(path)
            continue
        everything[task["id"]] = _view(task)
    out = []
    for task in everything.values():
        task["waiting_for"] = _waiting_for(task, everything)
        task["claimable"] = _claimable(task, everything)
        if status == "active" and task["status"] not in ACTIVE:
            continue
        if status not in (None, "all", "active") and task["status"] != status:
            continue
        if owner is not None and task.get("owner") != owner:
            continue
        if parent is not None and task.get("parent") != parent:
            continue
        if claimable and not _claimable(task, everything):
            continue
        out.append(task)
    rank = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda t: (rank.get(t.get("priority"), 1), int(t.get("seq") or 0)))
    return out


def _waiting_for(task: dict[str, Any], everything: dict[str, dict[str, Any]]) -> list[str]:
    return [d for d in task.get("depends_on", []) if everything.get(d, {}).get("status") != "done"]


def _claimable(task: dict[str, Any], everything: dict[str, dict[str, Any]]) -> bool:
    free = task["status"] == "open" or task.get("stale")
    return bool(free and not _waiting_for(task, everything))


def signature(home: Path | None = None) -> tuple:
    out = []
    import os

    try:
        for entry in os.scandir(folder(home)):
            if entry.name.startswith("t-") and entry.name.endswith(".json"):
                try:
                    st = entry.stat()
                except OSError:
                    continue
                out.append((entry.name, st.st_mtime_ns, st.st_size))
    except OSError:
        return ()
    return tuple(sorted(out))


def counts(home: Path | None = None) -> dict[str, int]:
    """How many tasks are in each active state (for the prompt)."""
    out: dict[str, int] = {}
    for task in list_tasks("active", home=home):
        key = "stale" if task.get("stale") else task["status"]
        out[key] = out.get(key, 0) + 1
    return out


# ── changing ──────────────────────────────────────────────────────────────────

def create(title: str, description: str = "", parent: str = "", depends_on: list[str] | None = None,
           source: str = "", priority: str = "medium", created_by: str = "shell",
           home: Path | None = None) -> dict[str, Any]:
    title = " ".join(str(title).split())
    if not title:
        raise TaskError("a task needs a title")
    if priority not in spool.PRIORITIES:
        raise TaskError(f"priority must be one of {', '.join(spool.PRIORITIES)}")
    for ref in [parent, *(depends_on or [])]:
        if ref:
            get(ref, home)  # it must exist
    stamp = spool.now()
    task = {"schema": SCHEMA, "id": new_id(), "rev": 1, "seq": time.time_ns(), "title": title[:200],
            "description": str(description)[:MAX_TEXT], "status": "open", "priority": priority,
            "parent": parent, "depends_on": list(depends_on or []), "source": source, "owner": "",
            "lease_until": None, "notes": [], "result": None, "created": stamp, "updated": stamp,
            "created_by": created_by}
    spool.write_json(_path(task["id"], home), task)
    return _view(task)


def _change(task_id: str, change: Any, home: Path | None) -> dict[str, Any]:
    path = _path(task_id, home)
    with spool.Lock(path.with_suffix(".lock")):
        task = spool.load_json(path)
        if task is None:
            raise TaskError(f"no task {task_id} (tasks lists them)")
        change(_view(task))
        task.pop("stale", None)
        task["rev"] = int(task.get("rev", 0)) + 1
        task["updated"] = spool.now()
        spool.write_json(path, task)
    return _view(task)


def _lease(seconds: Any) -> float:
    try:
        return max(10.0, min(float(seconds), MAX_LEASE))
    except (TypeError, ValueError):
        return LEASE


def claim(task_id: str | None, owner: str, lease: Any = LEASE, home: Path | None = None) -> dict[str, Any]:
    """Take a task for `owner`. Without an id, takes the most urgent one that can be claimed.
    TaskError names the owner when someone else holds it, and what it waits for."""
    if not owner:
        raise TaskError("claim needs an owner name (the `agent` label)")
    if not task_id:
        for candidate in list_tasks(claimable=True, home=home):
            try:
                return claim(candidate["id"], owner, lease, home)
            except TaskError:
                continue  # taken a moment ago: the next one
        raise TaskError("no task is free to claim")
    everything = {t["id"]: t for t in list_tasks(home=home)}

    def change(task: dict[str, Any]) -> None:
        if task["status"] in DONE:
            raise TaskError(f"{task['id']} is {task['status']}")
        holder = task.get("owner")
        if holder and holder != owner and task["status"] != "open" and not task.get("stale"):
            until = time.strftime("%H:%M:%S", time.localtime(task.get("lease_until") or 0))
            raise TaskError(f"{task['id']} is held by {holder} until {until}")
        waiting = _waiting_for(task, everything)
        if waiting:
            raise TaskError(f"{task['id']} waits for {', '.join(waiting)}")
        task["status"], task["owner"] = "claimed", owner
        task["lease_until"] = time.time() + _lease(lease)
        _note(task, owner, "claimed")

    return _change(task_id, change, home)


def _note(task: dict[str, Any], by: str, text: str) -> None:
    task["notes"] = [*task.get("notes", []), {"at": spool.now(), "by": by, "text": str(text)[:MAX_TEXT]}][-MAX_NOTES:]


def update(task_id: str, owner: str, status: str | None = None, note: str = "", result: str | None = None,
           lease: Any = LEASE, home: Path | None = None) -> dict[str, Any]:
    """The owner's progress: a note, a new status (working, blocked, done, failed), a result.
    Any update renews the lease. Someone who is not the owner is refused."""
    if status is not None and status not in UPDATABLE:
        raise TaskError(f"status must be one of {', '.join(UPDATABLE)}")

    def change(task: dict[str, Any]) -> None:
        if task["status"] in DONE:
            raise TaskError(f"{task['id']} is already {task['status']}")
        if task.get("owner") != owner:
            raise TaskError(f"{task['id']} is {'held by ' + task['owner'] if task.get('owner') else 'not claimed'}:"
                            " claim it before updating it")
        if note:
            _note(task, owner, note)
        if result is not None:
            task["result"] = str(result)[:MAX_TEXT]
        if status:
            task["status"] = status
        if task["status"] in DONE:
            task["lease_until"] = None
        else:
            task["lease_until"] = time.time() + _lease(lease)

    return _change(task_id, change, home)


def release(task_id: str, owner: str, note: str = "", home: Path | None = None) -> dict[str, Any]:
    """Give a task back: it is open for anyone again."""
    def change(task: dict[str, Any]) -> None:
        if task["status"] in DONE:
            raise TaskError(f"{task['id']} is already {task['status']}")
        if task.get("owner") != owner:
            raise TaskError(f"{task['id']} is not yours to release")
        _note(task, owner, note or "released")
        task["status"], task["owner"], task["lease_until"] = "open", "", None

    return _change(task_id, change, home)


def finish(task_id: str, status: str, by: str = "user", note: str = "", home: Path | None = None) -> dict[str, Any]:
    """The user's override: cancel a task, or mark it done or failed, whoever holds it."""
    if status not in ("cancelled", "done", "failed"):
        raise TaskError("a task is cancelled, done or failed")

    def change(task: dict[str, Any]) -> None:
        if task["status"] in DONE:
            raise TaskError(f"{task['id']} is already {task['status']}")
        _note(task, by, note or status)
        task["status"], task["lease_until"] = status, None

    return _change(task_id, change, home)


def remove(task_id: str, home: Path | None = None) -> None:
    spool.remove_file(_path(task_id, home))


def when(task: dict[str, Any]) -> str:
    """When the task was last changed, for a listing."""
    return datetime.strptime(task["updated"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).astimezone().strftime("%H:%M")
