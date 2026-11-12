"""watchplan — the list of things to keep checking: hosts and their checks, feeds, funcs, how often.

A plan is a file in ~/.shellcraft/watchplans/NAME.toml. This module reads and validates it and keeps the
time of the last round, so an orchestrating AI can turn a plan into tasks and know when the next round is
due. It runs nothing itself. See modules/watchplan.md.
"""

from __future__ import annotations

import json
import os
import re
import time
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any

from shellcraft.modkit import ArgParser, ModuleError

SUMMARY = "Read a watch plan: the hosts, checks, feeds and funcs to keep checking, and when the next round is due"

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,40}$")
HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")
CHECK_RE = re.compile(r"^[a-z][a-z0-9-]{0,30}$")
PATH_RE = re.compile(r"^/[A-Za-z0-9._/@+:~-]{0,199}$")
AGG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,40}$")
DEVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
KIND_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,40}$")
QUIET_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-3]):[0-5]\d$")
MAX_UNITS = 60

now = time.time  # seam for tests


def _dir() -> Path:
    from shellcraft.config import config_dir

    return config_dir() / "watchplans"


def plan_path(name: str) -> Path:
    if not NAME_RE.match(name):
        raise ModuleError(f"watchplan: '{name}' is not a plan name (letters, digits, - and _)")
    return _dir() / f"{name}.toml"


def load(name: str) -> dict[str, Any]:
    path = plan_path(name)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ModuleError(f"watchplan: no plan named '{name}' (watchplan list shows them; plans live in {_dir()})") \
            from None
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ModuleError(f"watchplan: {path}: {exc}") from None
    validate(name, data)
    return data


def _strings(where: str, value: Any, pattern: re.Pattern[str]) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise ModuleError(f"watchplan: {where} must be a list of strings")
    for item in value:
        if not pattern.match(item):
            raise ModuleError(f"watchplan: {where}: '{item}' is not allowed here")
    return value


def validate(name: str, data: dict[str, Any]) -> None:
    interval = data.get("interval_minutes", 15)
    if not isinstance(interval, int) or isinstance(interval, bool) or not 1 <= interval <= 1440:
        raise ModuleError("watchplan: interval_minutes is a whole number from 1 to 1440")
    quiet = data.get("quiet_hours")
    if quiet is not None and not (isinstance(quiet, str) and QUIET_RE.match(quiet)):
        raise ModuleError('watchplan: quiet_hours looks like "23:00-06:00"')
    units = 0
    seen: set[str] = set()
    for entry in data.get("hosts", []):
        if not isinstance(entry, dict) or not HOST_RE.match(str(entry.get("name", ""))):
            raise ModuleError("watchplan: every [[hosts]] entry needs a name that is a host name")
        if entry["name"] in seen:
            raise ModuleError(f"watchplan: host {entry['name']} is listed twice")
        seen.add(entry["name"])
        _strings(f"host {entry['name']}: checks", entry.get("checks", []), CHECK_RE)
        for log in entry.get("logs", []):
            if not isinstance(log, dict) or not PATH_RE.match(str(log.get("path", ""))) \
                    or ".." in str(log.get("path")).split("/"):
                raise ModuleError(f"watchplan: host {entry['name']}: each log needs an absolute path (no ..)")
            if "grep" in log and not (isinstance(log["grep"], str) and re.match(r"^[ -~]{1,100}$", log["grep"])):
                raise ModuleError(f"watchplan: host {entry['name']}: grep is plain text, up to 100 characters")
        units += 1
    seen_devices: set[str] = set()
    for entry in data.get("devices", []):
        if not isinstance(entry, dict) or not AGG_RE.match(str(entry.get("aggregator", ""))) \
                or not DEVICE_RE.match(str(entry.get("name", ""))):
            raise ModuleError("watchplan: every [[devices]] entry needs an aggregator (its name in aggregators.toml) "
                              "and a device name")
        key = f"{entry['aggregator']}/{entry['name']}"
        if key in seen_devices:
            raise ModuleError(f"watchplan: device {key} is listed twice")
        seen_devices.add(key)
        _strings(f"device {key}: kinds", entry.get("kinds", []), KIND_RE)
        units += 1
    kinds = (("feeds", re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,40}$")),
             ("funcs", re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,47}$")))
    for key, pattern in kinds:
        for entry in data.get(key, []):
            if not isinstance(entry, dict) or not pattern.match(str(entry.get("name", ""))):
                raise ModuleError(f"watchplan: every [[{key}]] entry needs a valid name")
            for field in ("query" if key == "feeds" else "args",):
                _strings(f"{key[:-1]} {entry['name']}: {field}", entry.get(field, []), re.compile(r"^[ -~]{1,200}$"))
            units += 1
    if units == 0:
        raise ModuleError(f"watchplan: plan '{name}' has nothing to check: add a [[hosts]], [[devices]], "
                          "[[feeds]] or [[funcs]] entry")
    if units > MAX_UNITS:
        raise ModuleError(f"watchplan: at most {MAX_UNITS} hosts, devices, feeds and funcs in one plan")


def units(data: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for h in data.get("hosts", []):
        out.append({"id": f"host:{h['name']}", "kind": "host", "host": h["name"], "checks": h.get("checks", []),
                    "logs": h.get("logs", []), "deep": h.get("deep", True) is not False})
    for d in data.get("devices", []):
        out.append({"id": f"device:{d['aggregator']}/{d['name']}", "kind": "device", "aggregator": d["aggregator"],
                    "device": d["name"], "kinds": d.get("kinds", []), "deep": d.get("deep", True) is not False})
    for f in data.get("feeds", []):
        out.append({"id": f"feed:{f['name']}", "kind": "feed", "feed": f["name"], "query": f.get("query", [])})
    for f in data.get("funcs", []):
        out.append({"id": f"func:{f['name']}", "kind": "func", "func": f["name"], "args": f.get("args", [])})
    return out


def in_quiet_hours(data: dict[str, Any], at: float | None = None) -> bool:
    quiet = data.get("quiet_hours")
    if not quiet:
        return False
    start, end = (int(p[:2]) * 60 + int(p[3:]) for p in quiet.split("-"))
    moment = datetime.fromtimestamp(now() if at is None else at)
    minute = moment.hour * 60 + moment.minute
    return start <= minute < end if start <= end else minute >= start or minute < end


def _state_path(name: str) -> Path:
    return _dir() / ".state" / f"{name}.json"


def last_round(name: str) -> float | None:
    try:
        return float(json.loads(_state_path(name).read_text(encoding="utf-8"))["last"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def due_text(name: str, data: dict[str, Any]) -> str:
    interval = int(data.get("interval_minutes", 15)) * 60
    last = last_round(name)
    quiet = "; quiet hours now: only crit alerts" if in_quiet_hours(data) else ""
    if last is None:
        return f"due now: no round has run yet{quiet}\n"
    wait = last + interval - now()
    return (f"due now: the last round was {int(now() - last)} s ago{quiet}\n" if wait <= 0
            else f"next round in {int(wait)} s{quiet}\n")


def run(args: list[str], stdin: str) -> str:
    parser = ArgParser("watchplan")
    parser.add_argument("action", nargs="?", choices=("list", "show", "check", "tasks", "due", "done"))
    parser.add_argument("name", nargs="?")
    parser.add_argument("at", nargs="?", type=float)  # done NAME SECONDS: the round began then (--watch-rounds)
    opts = parser.parse_args(args)
    if opts.action is None:
        raise ModuleError("watchplan: usage: watchplan list | show|check|tasks|due|done NAME")
    if opts.action == "list":
        names = sorted(p.stem for p in _dir().glob("*.toml")) if _dir().is_dir() else []
        return "".join(f"{n}\n" for n in names) or f"no plans yet: put one in {_dir()} (man watchplan)\n"
    if not opts.name:
        raise ModuleError(f"watchplan: {opts.action} needs a plan name")
    data = load(opts.name)
    if opts.action == "check":
        return f"ok: plan '{opts.name}': {len(units(data))} unit(s), every {data.get('interval_minutes', 15)} min\n"
    if opts.action == "tasks":
        return json.dumps(units(data), indent=1) + "\n"
    if opts.action == "due":
        return due_text(opts.name, data)
    if opts.action == "done":
        path = _state_path(opts.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".tmp")
        temp.write_text(json.dumps({"last": now() if opts.at is None else opts.at}), encoding="utf-8")
        os.replace(temp, path)
        return f"round recorded for '{opts.name}'\n"
    lines = [f"plan {opts.name}: {data.get('description', '')}".rstrip(": "),
             f"every {data.get('interval_minutes', 15)} min"
             + (f"; quiet hours {data['quiet_hours']}" if data.get("quiet_hours") else "")]
    for u in units(data):
        if u["kind"] == "host":
            lines.append(f"host {u['host']}: {', '.join(u['checks']) or '(no fixed checks)'}"
                         + (f"; {len(u['logs'])} log(s)" if u["logs"] else "") + ("; deep" if u["deep"] else ""))
        elif u["kind"] == "device":
            lines.append(f"device {u['aggregator']}/{u['device']}: {', '.join(u['kinds']) or '(all kinds)'}"
                         + ("; deep" if u["deep"] else ""))
        elif u["kind"] == "feed":
            lines.append(f"feed {u['feed']} {' '.join(u['query'])}".rstrip())
        else:
            lines.append(f"func {u['func']} {' '.join(u['args'])}".rstrip())
    return "\n".join(lines) + "\n" + due_text(opts.name, data)
