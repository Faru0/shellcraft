"""What happened in this shell, as events an AI can ask about, and the triggers that wake it.

The interactive shell records an event for every command it runs (`command`: the line, the exit
status, how long, where, and the numbers of the output lines it printed), every background job
or subshell that ends (`job`) and every Ctrl-C (`interrupt`). They sit in a ring of the last
MAX_EVENTS in memory, numbered by `seq`. An AI reads them with `get_events(since)`, which can
wait for the next one, so it does not poll with a model.

A **trigger** says which events are worth a nudge: a command that failed, one whose output
matches a pattern, a job that died. Triggers come from `ai-policy.toml` (`[[trigger]]`) and from
`watch` at the prompt (this session only). When one fires, the event becomes a *flag* in the
review spool (type `event`), so the AI's monitor sees it through the queue it already watches,
and the footer shows a one-line alert.
"""

from __future__ import annotations

import fnmatch
import json
import re
import threading
import time
from collections import deque
from typing import Any, Callable

from shellcraft import ai_policy

MAX_EVENTS = 2000
MAX_WAIT = 120.0
REFIRE = 30.0  # seconds before the same trigger flags the same command line again
OUTPUT_FOR_TRIGGER = 400  # lines of a command's output a match trigger reads
KINDS = ("command", "job", "interrupt")

_lock = threading.Condition()
_events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)
_seq = 0
_session: list[ai_policy.Trigger] = []
_session_n = 0
_fired: dict[tuple[str, str], float] = {}


def emit(kind: str, **data: Any) -> dict[str, Any]:
    """Record an event and wake anyone waiting for one."""
    global _seq
    with _lock:
        _seq += 1
        event = {"seq": _seq, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind, **data}
        _events.append(event)
        _lock.notify_all()
    return event


def latest() -> int:
    with _lock:
        return _seq


def since(cursor: int = 0, kinds: list[str] | None = None, limit: int = 100, wait: float = 0.0) -> dict[str, Any]:
    """The events after `cursor` (oldest first, at most `limit`), and the cursor to ask with next.
    With `wait`, holds until there is one (or the time is up)."""
    deadline = time.monotonic() + max(0.0, min(wait, MAX_WAIT))
    with _lock:
        while True:
            found = [e for e in _events if e["seq"] > cursor and (not kinds or e["kind"] in kinds)]
            remaining = deadline - time.monotonic()
            if found or remaining <= 0:
                break
            _lock.wait(remaining)
        more = len(found) > limit
        found = found[:limit]
        cursor_next = found[-1]["seq"] if more else _seq  # all seen, the other kinds too
        first = _events[0]["seq"] if _events else 0
    return {"events": found, "cursor": cursor_next, "dropped": max(0, first - cursor - 1) if cursor else 0}


def clear() -> None:
    global _seq, _session_n
    with _lock:
        _events.clear()
        _seq = 0
        _session.clear()
        _session_n = 0
        _fired.clear()


# ── triggers ──────────────────────────────────────────────────────────────────

def add_session(on: str, match: str = "", command: str = "", priority: str = "medium") -> ai_policy.Trigger:
    global _session_n
    _session_n += 1
    trigger = ai_policy.Trigger(on, match, command, priority, f"w{_session_n}")
    _session.append(trigger)
    return trigger


def remove_session(ident: str) -> int:
    """Forget a session trigger by id (`all` for every one); how many went."""
    before = len(_session)
    _session[:] = [t for t in _session if ident not in ("all", t.id)]
    return before - len(_session)


def triggers() -> list[ai_policy.Trigger]:
    """The policy file's triggers, then this session's."""
    return [*ai_policy.load().triggers, *_session]


def describe(trigger: ai_policy.Trigger) -> str:
    what = {"fail": "a command fails", "job-died": "a job or subshell ends with an error",
            "match": f"a command's output matches /{trigger.match}/"}[trigger.on]
    return what + (f" (commands like `{trigger.command}`)" if trigger.command else "")


def _hit(trigger: ai_policy.Trigger, event: dict[str, Any], output: Callable[[], str]) -> bool:
    if event.get("origin") == "ai":
        return False  # the AI knows what its own commands did
    if trigger.command and not fnmatch.fnmatchcase(str(event.get("line") or event.get("command") or ""),
                                                   trigger.command):
        return False
    if trigger.on == "fail":
        return event["kind"] == "command" and event.get("status") not in (0, 130, None)
    if trigger.on == "job-died":
        return event["kind"] == "job" and event.get("status") not in (0, None) and not event.get("killed")
    if trigger.on == "match":
        return event["kind"] == "command" and re.search(trigger.match, output(), re.I | re.M) is not None
    return False


def fire(event: dict[str, Any], output: Callable[[], str] = lambda: "", alert: Callable[..., Any] | None = None,
         home: Any = None) -> list[dict[str, Any]]:
    """Run the triggers over an event. Each one that fires puts a flag in the review spool (type
    `event`) and calls `alert(message, level)`. Returns the flags made."""
    from shellcraft import review_spool as spool

    made: list[dict[str, Any]] = []
    cache: list[str] = []

    def text() -> str:
        if not cache:
            cache.append(output())
        return cache[0]

    now = time.monotonic()
    for trigger in triggers():
        try:
            if not _hit(trigger, event, text):
                continue
        except re.error:
            continue
        what = str(event.get("line") or event.get("command") or event["kind"])
        key = (trigger.id, what)
        if now - _fired.get(key, -REFIRE) < REFIRE:
            continue
        _fired[key] = now
        if len(_fired) > 500:
            _fired.clear()
        title = f"{_headline(trigger, event)}: {what}"[:200]
        artifacts = [spool.artifact("event", json.dumps(event, ensure_ascii=False, indent=1))]
        out = text()
        if out:
            artifacts.append(spool.artifact("output", out))
        try:
            item = spool.create("flag", title, f"Fired by the trigger {trigger.id}: {describe(trigger)}.",
                                artifacts, trigger.priority, "event", created_by=f"shell trigger {trigger.id}",
                                home=home)
        except (OSError, spool.SpoolError):
            continue
        made.append(item)
        if alert is not None:
            alert(f"watch: {_headline(trigger, event)}: {what[:80]} (flagged for the AI)",
                  {"high": "warn", "medium": "info", "low": "info"}[trigger.priority])
    return made


def _headline(trigger: ai_policy.Trigger, event: dict[str, Any]) -> str:
    if trigger.on == "fail":
        return f"exit {event.get('status')}"
    if trigger.on == "job-died":
        return f"job {event.get('number')} died (exit {event.get('status')})"
    return "output matched"
