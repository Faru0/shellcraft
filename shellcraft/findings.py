"""The case file: what AI agents (and you) found, kept once, with the evidence behind it.

A **case** is a folder, ~/.shellcraft/findings/CASE/, of one JSON file per finding, kept under the
same rules as the review spool (whole-file writes, a lock for every change; see
shellcraft.review_spool). One case is current (`findings case NAME` switches; the default is
`default`). A finding is something about a thing: an IP, a domain, a hash, a user, a host. Two
agents that find the same thing do not make two findings: adding it again merges, keeping both
agents' evidence, the higher severity and the higher confidence, and counting how often it was
seen. That is what lets an orchestrator hand work to several workers and still get one picture.

`export_markdown` turns a case into a report, grouped by severity, with the evidence.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from shellcraft import review_spool as spool

SCHEMA = spool.SCHEMA
FOLDER = "findings"
CURRENT = "current"  # the file holding the current case's name
DEFAULT_CASE = "default"
KINDS = ("ip", "domain", "url", "hash", "user", "host", "file", "process", "other")
SEVERITIES = ("info", "low", "medium", "high", "critical")
CONFIDENCES = ("low", "medium", "high")
STATUSES = ("open", "confirmed", "dismissed")
MAX_EVIDENCE = 20  # pieces kept per finding (the newest)
MAX_OUTPUT = 4000  # characters of one piece's output
MAX_TEXT = 20000


class FindingError(spool.SpoolError):
    """A missing finding or a bad value."""


def root(home: Path | None = None) -> Path:
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


def case_name(name: str) -> str:
    name = str(name or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][\w.-]{0,39}", name):
        raise FindingError("a case name is letters, digits, _ . - (up to 40 characters)")
    return name


def current(home: Path | None = None) -> str:
    try:
        name = (root(home) / CURRENT).read_text(encoding="utf-8").strip()
        return case_name(name)
    except (OSError, FindingError):
        return DEFAULT_CASE


def use(name: str, home: Path | None = None) -> str:
    name = case_name(name)
    (root(home) / name).mkdir(parents=True, exist_ok=True)
    (root(home) / CURRENT).write_text(name + "\n", encoding="utf-8")
    return name


def cases(home: Path | None = None) -> dict[str, int]:
    """Every case with how many findings it holds."""
    out = {}
    for entry in sorted(root(home).iterdir()):
        if entry.is_dir():
            out[entry.name] = len(list(entry.glob("f-*.json")))
    out.setdefault(current(home), 0)
    return dict(sorted(out.items()))


def _folder(case: str | None, home: Path | None) -> Path:
    path = root(home) / case_name(case or current(home))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _norm(kind: str, value: str) -> str:
    value = " ".join(str(value).split())
    return value.lower() if kind in ("ip", "domain", "hash", "url", "host") else value


def _rank(value: str, order: tuple[str, ...]) -> int:
    return order.index(value) if value in order else 0


def new_id() -> str:
    return "f" + spool.new_id()[1:]


def _piece(raw: Any, agent: str) -> dict[str, Any] | None:
    """One piece of evidence: a command and its output, or just a note."""
    if isinstance(raw, str):
        raw = {"note": raw}
    if not isinstance(raw, dict):
        return None
    out = {"command": str(raw.get("command") or "")[:2000], "output": str(raw.get("output") or "")[-MAX_OUTPUT:],
           "note": str(raw.get("note") or "")[:2000], "by": agent, "at": spool.now()}
    return out if (out["command"] or out["output"] or out["note"]) else None


# ── reading ───────────────────────────────────────────────────────────────────

def get(finding_id: str, home: Path | None = None, case: str | None = None) -> dict[str, Any]:
    if not finding_id or "/" in finding_id or "\\" in finding_id or finding_id.startswith("."):
        raise FindingError(f"not a finding id: {finding_id!r}")
    for folder in [_folder(case, home)] if case else [root(home) / c for c in cases(home)]:
        item = spool.load_json(folder / f"{finding_id}.json")
        if item is not None:
            return item
    raise FindingError(f"no finding {finding_id} (findings lists them)")


def find(prefix: str, home: Path | None = None, case: str | None = None) -> dict[str, Any]:
    try:
        return get(prefix, home, case)
    except FindingError:
        pass
    matches = [f for f in list_findings(case, home=home) if f["id"].startswith(prefix) or f["id"].endswith(prefix)]
    if len(matches) == 1:
        return matches[0]
    raise FindingError(f"no finding {prefix} (findings lists them)" if not matches
                       else f"{prefix} matches {len(matches)} findings: give more of the id")


def list_findings(case: str | None = None, kind: str | None = None, severity: str | None = None,
                  tag: str | None = None, query: str | None = None, status: str | None = None,
                  agent: str | None = None, home: Path | None = None) -> list[dict[str, Any]]:
    """The case's findings, most severe first (then newest). `severity` is a minimum."""
    floor = _rank(severity, SEVERITIES) if severity else 0
    needle = query.lower() if query else ""
    out = []
    for path in _folder(case, home).glob("f-*.json"):
        item = spool.load_json(path)
        if item is None:
            continue
        if kind and item["kind"] != kind or status and item.get("status") != status:
            continue
        if _rank(item["severity"], SEVERITIES) < floor or tag and tag not in item.get("tags", []):
            continue
        if agent and agent not in item.get("agents", []):
            continue
        if needle and needle not in " ".join([item["value"], item["title"], item.get("summary", "")]).lower():
            continue
        out.append(item)
    out.sort(key=lambda f: (-_rank(f["severity"], SEVERITIES), -int(f.get("seq") or 0)))
    return out


def signature(home: Path | None = None) -> tuple:
    import os

    out = []
    try:
        for folder in root(home).iterdir():
            if folder.is_dir():
                for entry in os.scandir(folder):
                    if entry.name.startswith("f-") and entry.name.endswith(".json"):
                        try:
                            st = entry.stat()
                        except OSError:
                            continue
                        out.append((folder.name, entry.name, st.st_mtime_ns, st.st_size))
    except OSError:
        return ()
    return tuple(sorted(out))


# ── writing ───────────────────────────────────────────────────────────────────

def add(kind: str, value: str, title: str = "", summary: str = "", evidence: list[Any] | None = None,
        confidence: str = "medium", severity: str = "info", agent: str = "", task_id: str = "",
        tags: list[str] | None = None, case: str | None = None,
        home: Path | None = None) -> tuple[dict[str, Any], bool]:
    """Record a finding, or merge it into the one already there for the same thing. Returns the
    finding and whether it is new."""
    if kind not in KINDS:
        raise FindingError(f"kind must be one of {', '.join(KINDS)}")
    if severity not in SEVERITIES:
        raise FindingError(f"severity must be one of {', '.join(SEVERITIES)}")
    if confidence not in CONFIDENCES:
        raise FindingError(f"confidence must be one of {', '.join(CONFIDENCES)}")
    value = _norm(kind, value)
    if not value:
        raise FindingError("a finding needs a value (the IP, domain, hash… it is about)")
    folder = _folder(case, home)
    pieces = [p for p in (_piece(e, agent) for e in (evidence or [])) if p]
    with spool.Lock(folder / ".add.lock"):
        for path in folder.glob("f-*.json"):
            item = spool.load_json(path)
            if item is not None and item["kind"] == kind and item["value"] == value:
                return _merge(path, item, title, summary, pieces, confidence, severity, agent, task_id, tags), False
        stamp = spool.now()
        item = {"schema": SCHEMA, "id": new_id(), "rev": 1, "seq": time.time_ns(), "case": folder.name, "kind": kind,
                "value": value, "title": (title or f"{kind} {value}")[:200], "summary": str(summary)[:MAX_TEXT],
                "evidence": pieces[-MAX_EVIDENCE:], "confidence": confidence, "severity": severity,
                "status": "open", "agents": [agent] if agent else [], "task_id": task_id,
                "tags": sorted(set(map(str, tags or []))), "seen": 1, "created": stamp, "updated": stamp}
        spool.write_json(folder / f"{item['id']}.json", item)
    return item, True


def _merge(path: Path, item: dict[str, Any], title: str, summary: str, pieces: list[dict[str, Any]], confidence: str,
           severity: str, agent: str, task_id: str, tags: list[str] | None) -> dict[str, Any]:
    item["seen"] = int(item.get("seen", 1)) + 1
    item["evidence"] = [*item.get("evidence", []), *pieces][-MAX_EVIDENCE:]
    if _rank(confidence, CONFIDENCES) > _rank(item["confidence"], CONFIDENCES):
        item["confidence"] = confidence
    if _rank(severity, SEVERITIES) > _rank(item["severity"], SEVERITIES):
        item["severity"] = severity
    if summary and summary.strip() not in item.get("summary", ""):
        item["summary"] = (item.get("summary", "") + ("\n" if item.get("summary") else "") + summary.strip())[:MAX_TEXT]
    if title and item["title"] == f"{item['kind']} {item['value']}":
        item["title"] = title[:200]
    if agent and agent not in item.get("agents", []):
        item["agents"] = [*item.get("agents", []), agent]
    item["task_id"] = item.get("task_id") or task_id
    item["tags"] = sorted(set(item.get("tags", [])) | set(map(str, tags or [])))
    item["rev"] = int(item.get("rev", 0)) + 1
    item["updated"] = spool.now()
    spool.write_json(path, item)
    return item


def update(finding_id: str, title: str | None = None, summary: str | None = None, severity: str | None = None,
           confidence: str | None = None, status: str | None = None, add_tags: list[str] | None = None,
           remove_tags: list[str] | None = None, evidence: list[Any] | None = None, agent: str = "",
           case: str | None = None, home: Path | None = None) -> dict[str, Any]:
    """Change a finding: its words, its rating, its status, its tags, more evidence."""
    for name, value, allowed in (("severity", severity, SEVERITIES), ("confidence", confidence, CONFIDENCES),
                                 ("status", status, STATUSES)):
        if value is not None and value not in allowed:
            raise FindingError(f"{name} must be one of {', '.join(allowed)}")
    item = get(finding_id, home, case)
    path = root(home) / item["case"] / f"{item['id']}.json"
    with spool.Lock(path.with_suffix(".lock")):
        item = spool.load_json(path)
        if item is None:
            raise FindingError(f"no finding {finding_id}")
        if title is not None:
            item["title"] = str(title)[:200]
        if summary is not None:
            item["summary"] = str(summary)[:MAX_TEXT]
        for key, value in (("severity", severity), ("confidence", confidence), ("status", status)):
            if value is not None:
                item[key] = value
        tags = (set(item.get("tags", [])) | set(map(str, add_tags or []))) - set(map(str, remove_tags or []))
        item["tags"] = sorted(tags)
        pieces = [p for p in (_piece(e, agent) for e in (evidence or [])) if p]
        item["evidence"] = [*item.get("evidence", []), *pieces][-MAX_EVIDENCE:]
        if agent and agent not in item.get("agents", []):
            item["agents"] = [*item.get("agents", []), agent]
        item["rev"] = int(item.get("rev", 0)) + 1
        item["updated"] = spool.now()
        spool.write_json(path, item)
    return item


def remove(finding_id: str, home: Path | None = None, case: str | None = None) -> None:
    item = get(finding_id, home, case)
    spool.remove_file(root(home) / item["case"] / f"{item['id']}.json")


# ── the report ────────────────────────────────────────────────────────────────

def export_markdown(case: str | None = None, home: Path | None = None) -> str:
    name = case_name(case or current(home))
    found = list_findings(name, home=home)
    counts = {s: sum(1 for f in found if f["severity"] == s) for s in reversed(SEVERITIES)}
    lines = [f"# Findings: {name}", "", f"{len(found)} finding{'s' if len(found) != 1 else ''} · "
             + ", ".join(f"{n} {s}" for s, n in counts.items() if n) if found else "No findings.", ""]
    agents = sorted({a for f in found for a in f.get("agents", [])})
    if agents:
        lines += [f"Found by: {', '.join(agents)}", ""]
    for severity in reversed(SEVERITIES):
        group = [f for f in found if f["severity"] == severity]
        if not group:
            continue
        lines += [f"## {severity.capitalize()}", ""]
        for f in group:
            lines += [f"### {f['title']}", "",
                      f"`{f['value']}` ({f['kind']}) · confidence {f['confidence']} · {f['status']}"
                      + (f" · seen {f['seen']} times" if f.get("seen", 1) > 1 else "")
                      + (f" · {', '.join(f['agents'])}" if f.get("agents") else ""), ""]
            if f.get("summary"):
                lines += [f["summary"], ""]
            for piece in f.get("evidence", []):
                if piece.get("note"):
                    lines += [f"- {piece['note']}"]
                if piece.get("command") or piece.get("output"):
                    lines += ["", "```", *([f"$ {piece['command']}"] if piece.get("command") else []),
                              *([piece["output"].rstrip("\n")] if piece.get("output") else []), "```"]
            lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def export_json(case: str | None = None, home: Path | None = None) -> str:
    return json.dumps({"case": case_name(case or current(home)), "findings": list_findings(case, home=home)},
                      ensure_ascii=False, indent=1) + "\n"
