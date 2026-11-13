"""fleet: one status per watched machine, and the reports a round leaves behind (docs/fleet.md).

Two small stores under ~/.shellcraft, both written by the `report` module at the end of a watch round:

    fleet/host/web01.json                         the newest status of one unit: ok, warn or crit, and a summary
    reports/host/web01/20261112-162233-warn.md    a Markdown report, written only when the round found something

A unit is what a watch plan checks (modules/watchplan.py): host:NAME, device:AGGREGATOR/NAME, feed:NAME or
func:NAME. The `fleet` builtin (shellcraft.commands.fleet) joins the records with what else is known about a
machine: ssh-hosts.toml, aggregators.toml, the watch plans, the baselines and the case file. Looking is
tolerant and changes nothing: a file that cannot be read is left out, and no folder is made.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import stat
import sys
import time
import tomllib
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

VERSION = 1
RECORDS = "fleet"
REPORTS = "reports"
STATUSES = ("ok", "warn", "crit")
KINDS = ("host", "device", "feed", "func")
KEEP_DAYS = 30  # a report older than this is removed by the next save
MAX_REPORTS = 200  # reports kept for one unit (the newest)
MAX_BODY = 256 * 1024  # characters of one report's body (the start: that is where a report says what it found)
MAX_SUMMARY = 300
LATE_AFTER = 2  # a unit is late when its last round is older than this many intervals of its plan
DEFAULT_INTERVAL = 15  # minutes, as in modules/watchplan.py
SEVERITIES = ("info", "low", "medium", "high", "critical")

_HOST = r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}"
_NAME = r"[A-Za-z][A-Za-z0-9_-]{0,40}"
_DEVICE = r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}"
_FUNC = r"[A-Za-z_][A-Za-z0-9_-]{0,47}"
UNIT_RE = re.compile(rf"^(?:host:{_HOST}|device:{_NAME}/{_DEVICE}|feed:{_NAME}|func:{_FUNC})$")
REPORT_RE = re.compile(r"^(\d{8})-(\d{6})-(ok|warn|crit)(?:-\d+)?\.md$")

now = time.time  # seam for tests


class FleetError(Exception):
    """A unit id, a status or a report that is not one."""


def _home(home: Path | None) -> Path:
    if home is not None:
        return Path(home)
    from shellcraft.config import config_dir

    return config_dir()


def unit_id(text: str) -> str:
    text = text.strip()
    if not UNIT_RE.match(text):
        raise FleetError(f"'{text}' is not a unit: use host:NAME, device:AGGREGATOR/NAME, feed:NAME or func:NAME "
                         "(the ids `watchplan tasks` gives)")
    return text


def _parts(unit: str) -> list[str]:
    """host:web01 -> [host, web01]; device:edr/pc:7 -> [device, edr, pc%3A7]: one safe path part each."""
    kind, _, rest = unit.partition(":")
    return [kind] + [quote(part, safe="") for part in rest.split("/")]


def record_path(unit: str, home: Path | None = None) -> Path:
    parts = _parts(unit)
    return _home(home).joinpath(RECORDS, *parts[:-1], parts[-1] + ".json")


def report_dir(unit: str, home: Path | None = None) -> Path:
    return _home(home).joinpath(REPORTS, *_parts(unit))


def _stamp(at: float) -> str:
    return datetime.fromtimestamp(at, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def epoch(stamp: Any) -> float | None:
    try:
        return datetime.strptime(str(stamp), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp()
    except ValueError:
        return None


# ── writing: one status record, and a report when there is something to read ────

def _private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform != "win32":
        try:
            os.chmod(path, stat.S_IRWXU)
        except OSError:
            pass


def _write_private(path: Path, text: str) -> None:
    """Whole or not there, and readable by you alone."""
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{secrets.token_hex(3)}")
    fd = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(tmp, path)


def _count(name: str, value: int | None) -> int | None:
    if value is not None and not 0 <= value <= 1_000_000:
        raise FleetError(f"{name} is a count: 0 or more")
    return value


def save(unit: str, status: str, summary: str, body: str = "", changed: int | None = None,
         flags: int | None = None, plan: str = "", home: Path | None = None) -> dict[str, Any]:
    """Record what a round saw on one unit. A report file is written when the status is not ok or a body is given."""
    from shellcraft.modkit import redact_secrets

    unit = unit_id(unit)
    if status not in STATUSES:
        raise FleetError(f"the status is one of {', '.join(STATUSES)}")
    summary = " ".join(redact_secrets(summary).split())[:MAX_SUMMARY]
    if not summary:
        raise FleetError("a summary is needed: one line that says what the round saw")
    if plan and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,40}", plan):
        raise FleetError(f"'{plan}' is not a plan name")
    at = now()
    record: dict[str, Any] = {"version": VERSION, "unit": unit, "time": _stamp(at), "status": status,
                              "summary": summary, "changed": _count("changed", changed),
                              "flags": _count("flags", flags), "plan": plan, "report": ""}
    body = redact_secrets(body).strip()
    base = _home(home)
    if status != "ok" or body:
        record["report"] = str(_write_report(record, body, at, base))
    path = record_path(unit, base)
    _private_dir(base / RECORDS)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_private(path, json.dumps(record, ensure_ascii=False, indent=1) + "\n")
    clean(base)
    return record


def _write_report(record: dict[str, Any], body: str, at: float, base: Path) -> Path:
    folder = report_dir(record["unit"], base)
    _private_dir(base / REPORTS)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{datetime.fromtimestamp(at, UTC):%Y%m%d-%H%M%S}-{record['status']}"
    path, extra = folder / f"{name}.md", 1
    while path.exists():  # two reports in one second
        extra += 1
        path = folder / f"{name}-{extra}.md"
    facts = [f"- When: {record['time'].replace('T', ' ').replace('Z', ' UTC')}"]
    if record["plan"]:
        facts.append(f"- Watch plan: {record['plan']}")
    for label, key in (("Changed", "changed"), ("Flags", "flags")):
        if record[key] is not None:
            facts.append(f"- {label}: {record[key]}")
    if len(body) > MAX_BODY:
        body = body[:MAX_BODY].rstrip() + f"\n\n(cut here: a report holds at most {MAX_BODY} characters)"
    text = f"# {record['unit']}: {record['status']}\n\n" + "\n".join(facts) + f"\n\n{record['summary']}\n"
    _write_private(path, text + (f"\n{body}\n" if body else ""))
    return path


def clean(home: Path | None = None) -> int:
    """Remove reports older than KEEP_DAYS and all but the newest MAX_REPORTS of a unit. How many went."""
    root, removed = _home(home) / REPORTS, 0
    if not root.is_dir():
        return 0
    oldest = f"{datetime.fromtimestamp(now() - KEEP_DAYS * 86400, UTC):%Y%m%d-%H%M%S}"
    folders: dict[Path, list[Path]] = {}
    for path in root.rglob("*.md"):
        if REPORT_RE.match(path.name):
            folders.setdefault(path.parent, []).append(path)
    for paths in folders.values():
        paths.sort(key=lambda p: p.name, reverse=True)
        for index, path in enumerate(paths):
            if index >= MAX_REPORTS or path.name[:15] < oldest:
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
    return removed


# ── reading ──────────────────────────────────────────────────────────────────

def _json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _number(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _record(path: Path) -> dict[str, Any] | None:
    data = _json(path)
    if data is None or not isinstance(data.get("unit"), str) or not UNIT_RE.match(data["unit"]) \
            or data.get("status") not in STATUSES or epoch(data.get("time")) is None:
        return None
    return {"version": VERSION, "unit": data["unit"], "time": data["time"], "status": data["status"],
            "summary": str(data.get("summary", "")), "changed": _number(data.get("changed")),
            "flags": _number(data.get("flags")), "plan": str(data.get("plan", "")),
            "report": str(data.get("report", ""))}


def record(unit: str, home: Path | None = None) -> dict[str, Any] | None:
    found = _record(record_path(unit_id(unit), home))
    return found if found is not None and found["unit"] == unit.strip() else None


def records(home: Path | None = None) -> dict[str, dict[str, Any]]:
    """Every status record, by unit id."""
    root = _home(home) / RECORDS
    out: dict[str, dict[str, Any]] = {}
    for path in (sorted(root.rglob("*.json")) if root.is_dir() else []):
        found = _record(path)
        if found is not None and record_path(found["unit"], home) == path:
            out[found["unit"]] = found
    return out


def _report(path: Path, unit: str) -> dict[str, Any] | None:
    match = REPORT_RE.match(path.name)
    if match is None:
        return None
    day, clock, status = match.groups()
    return {"unit": unit, "status": status, "path": str(path),
            "time": f"{day[:4]}-{day[4:6]}-{day[6:]}T{clock[:2]}:{clock[2:4]}:{clock[4:]}Z"}


def reports(unit: str | None = None, home: Path | None = None) -> list[dict[str, Any]]:
    """The reports of one unit, or of all of them, newest first."""
    if unit is not None:
        unit = unit_id(unit)
        folder = report_dir(unit, home)
        found = [_report(path, unit) for path in (folder.iterdir() if folder.is_dir() else [])]
    else:
        root, found = _home(home) / REPORTS, []
        for path in (root.rglob("*.md") if root.is_dir() else []):
            parts = [unquote(part) for part in path.parent.relative_to(root).parts]
            name = f"{parts[0]}:{'/'.join(parts[1:])}" if len(parts) > 1 else ""
            if UNIT_RE.match(name) and report_dir(name, home) == path.parent:
                found.append(_report(path, name))
    return sorted((item for item in found if item is not None), key=lambda r: (r["time"], r["path"]), reverse=True)


def read_report(unit: str, number: int = 1, home: Path | None = None) -> tuple[dict[str, Any], str]:
    """Report NUMBER of a unit (1 is the newest) and its text."""
    listed = reports(unit, home)
    if not listed:
        raise FleetError(f"no report for {unit.strip()} (a report is written when a round found something)")
    if not 1 <= number <= len(listed):
        raise FleetError(f"{unit.strip()} has {len(listed)} report(s): 1 is the newest")
    try:
        return listed[number - 1], Path(listed[number - 1]["path"]).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise FleetError(f"{listed[number - 1]['path']}: {exc.strerror or exc}") from None


# ── the view: every unit with all that is known about it ─────────────────────────

@dataclass
class Unit:
    id: str
    kind: str  # host | device | feed | func
    name: str  # the host, the device, the feed or the func
    status: str = ""  # ok | warn | crit, or "" when no round has reported yet
    time: str = ""  # of the last status record
    age: int | None = None  # seconds since then
    late: bool = False
    summary: str = ""
    changed: int | None = None
    flags: int | None = None
    plan: str = ""  # the watch plan that checks it
    interval: int | None = None  # minutes
    report: str = ""  # the report of the last round, when it wrote one
    findings: int = 0  # in the case file and not dismissed
    worst: str = ""  # the highest severity among them
    about: str = ""  # role, privilege and tags from ssh-hosts.toml, or the aggregator's host


def _toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _table(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _entries(value: Any) -> list[dict[str, Any]]:
    return [entry for entry in value if isinstance(entry, dict)] if isinstance(value, list) else []


def plans(home: Path | None = None) -> dict[str, dict[str, Any]]:
    """Every watch plan that can be read: {name: {interval, units, last}} (last: when its last round began)."""
    folder = _home(home) / "watchplans"
    out: dict[str, dict[str, Any]] = {}
    for path in (sorted(folder.glob("*.toml")) if folder.is_dir() else []):
        data = _toml(path)
        interval = data.get("interval_minutes", DEFAULT_INTERVAL)
        units = [f"{kind}:{entry['name']}" for kind in ("host", "feed", "func")
                 for entry in _entries(data.get(f"{kind}s")) if isinstance(entry.get("name"), str)]
        units += [f"device:{entry['aggregator']}/{entry['name']}" for entry in _entries(data.get("devices"))
                  if isinstance(entry.get("aggregator"), str) and isinstance(entry.get("name"), str)]
        last = (_json(folder / ".state" / f"{path.stem}.json") or {}).get("last")
        began = float(last) if isinstance(last, (int, float)) and not isinstance(last, bool) else None
        out[path.stem] = {"interval": interval if _number(interval) else DEFAULT_INTERVAL,
                          "units": [unit for unit in units if UNIT_RE.match(unit)], "last": began}
    return out


def _about_host(entry: dict[str, Any]) -> str:
    priv = "root" if entry.get("root") is True else "sudo" if entry.get("sudo") is True else ""
    tags = entry.get("tags")
    words = [str(entry.get("role") or ""), priv, *(str(tag) for tag in (tags if isinstance(tags, list) else []))]
    return ", ".join(word for word in words if word)


def _known(base: Path) -> dict[str, Unit]:
    """The units the configuration names: hosts the AI may reach, aggregator devices, what the plans list."""
    out: dict[str, Unit] = {}

    def add(unit: str, about: str = "") -> None:
        if UNIT_RE.match(unit) and unit not in out:
            kind, _, rest = unit.partition(":")
            out[unit] = Unit(unit, kind, rest.rpartition("/")[2], about=about)

    for name, entry in _table(_toml(base / "ssh-hosts.toml").get("hosts")).items():
        if isinstance(entry, dict) and entry.get("ai") is True:
            add(f"host:{name}", _about_host(entry))
    for name, entry in _table(_toml(base / "aggregators.toml").get("aggregators")).items():
        where = _table(entry).get("host")
        for device in _table(_table(entry).get("devices")):
            add(f"device:{name}/{device}", f"files on {where}" if isinstance(where, str) else "")
    return out


def _findings(base: Path) -> list[dict[str, Any]]:
    """Every finding of every case that has not been dismissed."""
    if not (base / "findings").is_dir():
        return []
    from shellcraft import findings as case

    out: list[dict[str, Any]] = []
    for name in sorted(entry.name for entry in (base / "findings").iterdir() if entry.is_dir()):
        try:
            out += [item for item in case.list_findings(name, home=base) if item.get("status") != "dismissed"]
        except (OSError, KeyError, TypeError, case.FindingError):
            continue
    return out


def about_unit(item: dict[str, Any], unit: Unit) -> bool:
    """A finding belongs to a unit by a tag (the unit id, or just the name), or as a `host` finding of that name."""
    tags = [str(tag).lower() for tag in item.get("tags", []) if isinstance(tag, str)]
    name = unit.name.lower()
    if unit.id.lower() in tags or name in tags:
        return True
    return unit.kind in ("host", "device") and item.get("kind") == "host" and str(item.get("value", "")).lower() == name


def _rank(severity: Any) -> int:
    return SEVERITIES.index(severity) if severity in SEVERITIES else 0


def gather(home: Path | None = None) -> list[Unit]:
    """Every unit, the ones that need you first: crit, warn, late, never checked, ok."""
    base = _home(home)
    units = _known(base)
    watched = plans(base)
    for plan in watched.values():
        for unit in plan["units"]:
            if unit not in units and unit.startswith(("host:", "device:")):
                kind, _, rest = unit.partition(":")
                units[unit] = Unit(unit, kind, rest.rpartition("/")[2])
    kept = records(base)
    for unit in kept:
        if unit not in units:
            kind, _, rest = unit.partition(":")
            units[unit] = Unit(unit, kind, rest.rpartition("/")[2])
    open_findings = _findings(base)
    at = now()
    for unit in units.values():
        mine = [(name, plan) for name, plan in watched.items() if unit.id in plan["units"]]
        if mine:
            unit.plan, plan = min(mine, key=lambda pair: pair[1]["interval"])
            unit.interval = plan["interval"]
        found = kept.get(unit.id)
        if found is not None:
            unit.status, unit.time, unit.summary = found["status"], found["time"], found["summary"]
            unit.changed, unit.flags, unit.report = found["changed"], found["flags"], found["report"]
            unit.age = max(0, int(at - (epoch(found["time"]) or at)))
            if not unit.plan and found["plan"] in watched:
                unit.plan, unit.interval = found["plan"], watched[found["plan"]]["interval"]
            unit.late = unit.interval is not None and unit.age > LATE_AFTER * unit.interval * 60
        about = [item for item in open_findings if about_unit(item, unit)]
        unit.findings = len(about)
        unit.worst = max((str(item.get("severity", "")) for item in about), key=_rank, default="")
    return sorted(units.values(), key=lambda u: (_order(u), u.kind != "host", u.id))


def _order(unit: Unit) -> int:
    if unit.status in ("crit", "warn"):
        return 0 if unit.status == "crit" else 1
    if unit.late:
        return 2
    return 3 if not unit.status else 4


def resolve(text: str, units: list[Unit]) -> Unit:
    """The unit a user means: its id, or just its name (a host before a device of the same name)."""
    text = text.strip()
    for unit in units:
        if unit.id == text:
            return unit
    named = [unit for unit in units if unit.name == text]
    hosts = [unit for unit in named if unit.kind == "host"]
    if len(named) == 1 or len(hosts) == 1:
        return (hosts or named)[0]
    if named:
        raise FleetError(f"'{text}' is more than one unit: {', '.join(unit.id for unit in named)}")
    known = ", ".join(unit.id for unit in units) or "none yet"
    raise FleetError(f"no unit '{text}' (known: {known})")


@dataclass
class Check:
    """One remembered check of a machine (a baseline file), with what its last update changed."""
    name: str
    updated: str
    new: int
    gone: int
    present: int
    first: bool  # the last update was the first: everything was learned, nothing compared


def checks(name: str, home: Path | None = None) -> list[Check]:
    folder = _home(home) / "baselines" / name
    out: list[Check] = []
    for path in (sorted(folder.glob("*.json")) if re.fullmatch(_HOST, name) and folder.is_dir() else []):
        data = _json(path) or {}
        updated = data.get("updated")
        items = [item for item in _table(data.get("items")).values() if isinstance(item, dict)]
        if not isinstance(updated, str) or not updated:
            continue
        present = [item for item in items if item.get("present")]
        new = sum(1 for item in present if item.get("first_seen") == updated)
        gone = sum(1 for item in items if not item.get("present") and item.get("gone_since") == updated)
        first = bool(items) and new == len(items)
        out.append(Check(path.stem, updated, 0 if first else new, gone, len(present), first))
    return out


@dataclass
class Detail:
    unit: Unit
    checks: list[Check] = field(default_factory=list)
    findings: list[dict[str, Any]] = field(default_factory=list)
    reports: list[dict[str, Any]] = field(default_factory=list)
    plan_last: str = ""  # when the last round of its plan began

    def as_dict(self) -> dict[str, Any]:
        keep = ("id", "case", "kind", "value", "title", "severity", "status", "seen", "updated")
        return {**asdict(self.unit), "checks": [asdict(check) for check in self.checks],
                "open_findings": [{key: item.get(key) for key in keep} for item in self.findings],
                "reports": self.reports, "plan_last_round": self.plan_last}


REPORTS_SHOWN = 5


def detail(text: str, home: Path | None = None) -> Detail:
    base = _home(home)
    unit = resolve(text, gather(base))
    out = Detail(unit)
    if unit.kind in ("host", "device"):
        out.checks = checks(unit.name, base)
        out.findings = sorted((item for item in _findings(base) if about_unit(item, unit)),
                              key=lambda item: -_rank(item.get("severity")))
    out.reports = reports(unit.id, base)[:REPORTS_SHOWN]
    last = plans(base).get(unit.plan, {}).get("last")
    out.plan_last = _stamp(last) if last else ""
    return out


def age_text(seconds: int | None) -> str:
    if seconds is None:
        return "never"
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"
