"""baseline — the "have I seen this before?" memory for a host-monitoring loop.

Pipe a snapshot of some check (ps, ss, cron, authorized-keys, ...) into `baseline diff HOST CHECK`
and it prints only what is new or gone since the last time. Pure Python, no network.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import re
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from shellcraft.config import config_dir
from shellcraft.modkit import ArgParser, ModuleError

SUMMARY = "Remember host snapshots and report only what is new or gone since last time"

COMMANDS = ("diff", "put", "show", "accept", "forget", "list")
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
MAX_ITEMS = 5000
MAX_HOSTS = 200
MAX_CHECKS = 50
MAX_ITEM_CHARS = 500
GONE_DAYS = 30
EPHEMERAL = 32768
PROTOS = {"tcp", "udp", "tcp6", "udp6", "raw", "raw6", "sctp", "mptcp"}


def now() -> str:
    """Current UTC time, ISO-8601. A seam for tests."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------------------------

_MONTHS = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
_DAYS = "Mon|Tue|Wed|Thu|Fri|Sat|Sun"
_TIMESTAMPS = [
    re.compile(r"\b\d{4}-\d\d-\d\d[T ]\d\d:\d\d(?::\d\d)?(?:[.,]\d+)?(?:Z|[+-]\d\d:?\d\d)?"),
    re.compile(rf"\b(?:(?:{_DAYS})\s+)?(?:{_MONTHS})\s+\d{{1,2}}\s+\d\d:\d\d:\d\d(?:\s+\d{{4}})?\b"),
    re.compile(rf"\b(?:{_DAYS})\s+\d{{1,2}}\s+(?:{_MONTHS})\s+\d{{4}}\s+\d\d:\d\d:\d\d\b"),
]
_BIG_NUMBER = re.compile(r"\b\d{5,}\b")
_KEY_TYPE = re.compile(r"^(?:ssh-|ecdsa-|sk-ssh-|sk-ecdsa-)\S*$")


def _generic(line: str) -> str | None:
    for pattern in _TIMESTAMPS:
        line = pattern.sub(" ", line)
    return line


def _ps(line: str) -> str | None:
    parts = line.split(None, 4)
    if len(parts) < 4 or not parts[0].isdigit() or not parts[1].isdigit():
        return None  # the header line, or not a ps line
    user = parts[2]
    args = _BIG_NUMBER.sub("N", parts[4]) if len(parts) > 4 else ""
    return f"{user} {args}".strip()


def _split_addr(addr: str) -> tuple[str, str]:
    host, _, port = addr.rpartition(":")
    return host.split("%")[0], port


def _bucket(port: str) -> str:
    return "*" if port.isdigit() and int(port) >= EPHEMERAL else port


def _ss(line: str) -> str | None:
    parts = line.split()
    proto = "tcp"
    if parts and parts[0].lower() in PROTOS:
        proto = parts.pop(0).lower()
    if len(parts) < 5 or not parts[0].isalpha() or not (parts[1].isdigit() and parts[2].isdigit()):
        return None  # the header, or not an ss line
    state = parts[0].upper().replace("-", "_")
    local, peer = parts[3], parts[4]
    names = sorted(set(re.findall(r'\("([^"]*)"', line)))
    proc = ",".join(names) or "-"
    lhost, lport = _split_addr(local)
    if state in ("LISTEN", "UNCONN"):
        return f"{proto} {state} {lhost}:{lport} {proc}"
    phost, pport = _split_addr(peer)
    return f"{proto} {state} {_bucket(lport)}->{phost}:{_bucket(pport)} {proc}"


def _fingerprint(blob: str) -> str:
    try:
        raw = base64.b64decode(blob + "=" * (-len(blob) % 4), validate=True)
    except ValueError:
        raw = blob.encode()
    return "SHA256:" + base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")


def _authorized_key(line: str) -> str | None:
    parts = line.split()
    for i, token in enumerate(parts):
        if _KEY_TYPE.match(token) and i + 1 < len(parts):
            options = " ".join(parts[:i])
            comment = " ".join(parts[i + 2:])
            item = f"{token} {_fingerprint(parts[i + 1])}"
            return " ".join(x for x in (options, item, comment) if x)
    return _generic(line)  # not a key line: keep the text, but never a blob-looking one


def _lines(per_line: Callable[[str], str | None]) -> Callable[[str], list[str]]:
    def run(text: str) -> list[str]:
        items: set[str] = set()
        section = ""
        for raw in text.splitlines():
            line = " ".join(raw.split())
            if not line:
                continue
            if line.startswith("##"):
                section = line.lstrip("#").strip()
                continue
            if line.startswith("#"):
                continue
            item = per_line(line)
            item = " ".join(item.split()) if item else ""
            if item:
                items.add(f"{section}: {item}" if section else item)
        return sorted(items)
    return run


_BPF_EXTRA = ("attach_type", "tp_name", "target_name", "func_name", "func", "cgroup")


def _bpf(text: str) -> list[str]:
    try:
        data = json.loads(text)
    except ValueError:
        return _lines(_generic)(text)
    if isinstance(data, dict):
        data = [data]
    items: set[str] = set()
    for obj in data if isinstance(data, list) else []:
        if not isinstance(obj, dict):
            continue
        kind = obj.get("type") or obj.get("prog_type") or "-"
        extra = [str(obj[k]) for k in _BPF_EXTRA if isinstance(obj.get(k), (str, int)) and k in obj]
        name = str(obj.get("name") or "-")
        items.add(" ".join([str(kind), name, *extra]))
    return sorted(items)


NORMALIZERS: dict[str, Callable[[str], list[str]]] = {
    "generic": _lines(_generic),
    "ps": _lines(_ps),
    "ss": _lines(_ss),
    "authorized-keys": _lines(_authorized_key),
    "bpf-progs": _bpf,
    "bpf-links": _bpf,
    "bpf-maps": _bpf,
}
# These are plain line-based checks; they use the generic normaliser and are listed for clarity.
for _name in ("cron", "timers", "modules", "kprobes", "uprobes", "listening", "logins", "exes"):
    NORMALIZERS[_name] = NORMALIZERS["generic"]


def normalize(check: str, text: str) -> list[str]:
    """Turn a snapshot into a sorted list of unique items. The check name picks the rules."""
    fn = NORMALIZERS.get(check)
    if fn is None:
        fn = NORMALIZERS["generic"]
    items = fn(text)
    return sorted({item[:MAX_ITEM_CHARS] for item in items if item})


# ---------------------------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------------------------

def _check_name(kind: str, value: str) -> str:
    if not NAME_RE.fullmatch(value):
        raise ModuleError(f"baseline: invalid {kind} '{value[:70]}' (use 1 to 64 letters, digits, '.', '_' or '-', "
                          "starting with a letter or digit)")
    return value


def _root() -> Path:
    return config_dir() / "baselines"


def _path(host: str, check: str) -> Path:
    return _root() / host / f"{check}.json"


def _checks_of(host: str) -> list[str]:
    folder = _root() / host
    return sorted(p.stem for p in folder.glob("*.json")) if folder.is_dir() else []


def _hosts() -> list[str]:
    root = _root()
    return sorted(p.name for p in root.iterdir() if p.is_dir()) if root.is_dir() else []


def _empty(host: str, check: str) -> dict[str, Any]:
    return {"version": 1, "host": host, "check": check, "learned": False, "updated": "", "items": {}}


def _load(host: str, check: str, notes: list[str]) -> dict[str, Any] | None:
    """The stored baseline, or None. A corrupt file is moved to .bad (with a note)."""
    path = _path(host, check)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("items"), dict):
            raise ValueError("not a baseline")
        for rec in data["items"].values():
            if not isinstance(rec, dict):
                raise ValueError("bad item")
    except (ValueError, OSError) as exc:
        bad = path.with_suffix(".json.bad")
        try:
            os.replace(path, bad)
        except OSError as err:
            raise ModuleError(f"baseline: {path} is unreadable ({exc}) and could not be moved aside: {err}") from None
        notes.append(f"NOTE {host} {check}: the stored baseline was corrupt ({exc}); moved to {bad.name} and "
                     "treated as a first run")
        return None
    _purge(data)
    return data


def _purge(data: dict[str, Any]) -> None:
    current = datetime.strptime(now(), "%Y-%m-%dT%H:%M:%SZ")
    cutoff = (current - timedelta(days=GONE_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    items = data["items"]
    for item in [i for i, r in items.items()
                 if not r.get("present") and not r.get("accepted") and (r.get("gone_since") or "") < cutoff]:
        del items[item]


def _save(host: str, check: str, data: dict[str, Any]) -> None:
    folder = _root() / host
    new_host = not folder.is_dir()
    if new_host and len(_hosts()) >= MAX_HOSTS:
        raise ModuleError(f"baseline: already tracking {MAX_HOSTS} hosts; forget one first")
    if not _path(host, check).exists() and len(_checks_of(host)) >= MAX_CHECKS:
        raise ModuleError(f"baseline: {host} already has {MAX_CHECKS} checks; forget one first")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(folder, 0o700)
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=f".{check}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=1, sort_keys=True, ensure_ascii=False)
                fh.write("\n")
            with contextlib.suppress(OSError):
                os.chmod(tmp, 0o600)
            os.replace(tmp, _path(host, check))
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
    except OSError as exc:
        raise ModuleError(f"baseline: cannot write {_path(host, check)}: {exc}") from None


def _snapshot(check: str, stdin: str) -> tuple[list[str], str]:
    items = normalize(check, stdin)
    note = ""
    if len(items) > MAX_ITEMS:
        note = f"NOTE truncated: kept {MAX_ITEMS} of {len(items)} items"
        items = items[:MAX_ITEMS]
    return items, note


# ---------------------------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------------------------

def _out(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def _need(names: list[str], count: int, usage: str) -> None:
    if len(names) < count:
        raise ModuleError(f"baseline: usage: baseline {usage}")


def _diff(names: list[str], stdin: str, opts) -> str:
    _need(names, 2, "diff HOST CHECK [--no-store] [--show-same]")
    host, check = _check_name("host", names[0]), _check_name("check", names[1])
    if len(names) > 2:
        raise ModuleError("baseline: diff takes just HOST and CHECK; the snapshot comes from stdin")
    current, note = _snapshot(check, stdin)
    notes: list[str] = [note] if note else []
    data = _load(host, check, notes)
    learning = data is None or not data.get("learned")
    data = data or _empty(host, check)
    stored = data["items"]
    stamp = now()
    cur = set(current)
    active = {i for i, r in stored.items() if r.get("present")}

    new, returned, gone, same, accepted = [], {}, [], [], []
    if not learning:
        for item in current:
            rec = stored.get(item)
            if rec and rec.get("present"):
                (accepted if rec.get("accepted") else same).append(item)
            elif rec and rec.get("accepted"):
                accepted.append(item)
            elif rec and rec.get("gone_since"):
                new.append(item)
                returned[item] = rec.get("first_seen", stamp)[:10]
            else:
                new.append(item)
        gone = sorted(active - cur)

    if not opts.no_store:
        for item in current:
            rec = stored.get(item)
            if rec is None:
                rec = stored[item] = {"first_seen": stamp, "seen": 0}
            rec.setdefault("first_seen", stamp)
            rec.update(last_seen=stamp, present=True, seen=int(rec.get("seen", 0)) + 1)
            rec.pop("gone_since", None)
        for item in active - cur:
            stored[item].update(present=False, gone_since=stamp)
        data.update(learned=True, updated=stamp)
        _purge(data)
        _save(host, check, data)

    if opts.output == "json":
        return _out({"host": host, "check": check, "learning": learning, "items": len(current),
                     "new": new, "returned": sorted(returned), "gone": gone,
                     "unchanged": len(same), "accepted": len(accepted),
                     "truncated": bool(note), "stored": not opts.no_store, "notes": notes})
    lines = list(notes)
    if learning:
        lines.append(f"LEARNING {host} {check}: recorded {len(current)} item(s); nothing is flagged on a first run")
        return "\n".join(lines) + "\n"
    if new:
        lines.append(f"NEW ({len(new)})")
        lines += [f"+ {i}" + (f"  (returned; first seen {returned[i]})" if i in returned else "") for i in new]
    if gone:
        lines.append(f"GONE ({len(gone)})")
        lines += [f"- {i}" for i in gone]
    if opts.show_same and same:
        lines.append(f"SAME ({len(same)})")
        lines += [f"= {i}" for i in same]
    lines.append(f"{host} {check}: {len(new)} new, {len(gone)} gone, {len(same)} unchanged, {len(accepted)} accepted")
    return "\n".join(lines) + "\n"


def _put(names: list[str], stdin: str, opts) -> str:
    _need(names, 2, "put HOST CHECK")
    host, check = _check_name("host", names[0]), _check_name("check", names[1])
    items, note = _snapshot(check, stdin)
    stamp = now()
    data = _empty(host, check)
    data.update(learned=True, updated=stamp)
    data["items"] = {i: {"first_seen": stamp, "last_seen": stamp, "seen": 1, "present": True} for i in items}
    _save(host, check, data)
    text = (note + "\n" if note else "") + f"{host} {check}: baseline replaced with {len(items)} item(s)\n"
    return _out({"host": host, "check": check, "items": len(items), "truncated": bool(note)}) \
        if opts.output == "json" else text


def _show(names: list[str], stdin: str, opts) -> str:
    _need(names, 1, "show HOST [CHECK]")
    host = _check_name("host", names[0])
    if len(names) == 1:
        checks = _checks_of(host)
        if not checks:
            raise ModuleError(f"baseline: nothing stored for host {host}")
        rows = []
        notes: list[str] = []
        for check in checks:
            data = _load(host, check, notes)
            if data:
                n = sum(1 for r in data["items"].values() if r.get("present"))
                rows.append({"check": check, "items": n, "updated": data.get("updated", "")})
        if opts.output == "json":
            return _out({"host": host, "checks": rows, "notes": notes})
        return "\n".join(notes + [f"{r['check']}  {r['items']} item(s)  updated {r['updated'] or 'never'}"
                                  for r in rows]) + "\n"
    check = _check_name("check", names[1])
    notes = []
    data = _load(host, check, notes)
    if data is None:
        raise ModuleError(f"baseline: nothing stored for {host} {check}")
    recs = data["items"]
    present = sorted(i for i, r in recs.items() if r.get("present"))
    gone = sorted(i for i, r in recs.items() if not r.get("present") and r.get("gone_since"))
    if opts.output == "json":
        return _out({"host": host, "check": check, "updated": data.get("updated", ""),
                     "items": [{"item": i, **recs[i]} for i in present],
                     "gone": [{"item": i, **recs[i]} for i in gone]})
    lines = [f"{host} {check}: {len(present)} item(s), updated {data.get('updated') or 'never'}"]
    lines += [i + ("  (accepted)" if recs[i].get("accepted") else "") for i in present]
    if gone:
        lines.append(f"GONE ({len(gone)})")
        lines += [f"{i}  (gone since {recs[i]['gone_since'][:10]})" for i in gone]
    return "\n".join(lines) + "\n"


def _accept(names: list[str], stdin: str, opts) -> str:
    _need(names, 2, "accept HOST CHECK ITEM...")
    host, check = _check_name("host", names[0]), _check_name("check", names[1])
    given = names[2:] or [ln for ln in stdin.splitlines() if ln.strip()]
    items = sorted({" ".join(g.split())[:MAX_ITEM_CHARS] for g in given if g.strip()})
    if not items:
        raise ModuleError("baseline: accept needs items, as arguments or one per line on stdin")
    notes: list[str] = []
    data = _load(host, check, notes) or _empty(host, check)
    stamp = now()
    for item in items:
        rec = data["items"].setdefault(item, {"first_seen": stamp, "seen": 0, "present": False})
        rec["accepted"] = True
    if len(data["items"]) > MAX_ITEMS:
        raise ModuleError(f"baseline: a check can hold at most {MAX_ITEMS} items")
    data["updated"] = data.get("updated") or stamp
    _save(host, check, data)
    if opts.output == "json":
        return _out({"host": host, "check": check, "accepted": items, "notes": notes})
    return "\n".join(notes + [f"{host} {check}: accepted {len(items)} item(s)"]) + "\n"


def _forget(names: list[str], stdin: str, opts) -> str:
    _need(names, 1, "forget HOST [CHECK]")
    host = _check_name("host", names[0])
    if len(names) > 2:
        raise ModuleError("baseline: usage: baseline forget HOST [CHECK]")
    if len(names) == 2:
        check = _check_name("check", names[1])
        path = _path(host, check)
        if not path.is_file():
            raise ModuleError(f"baseline: nothing stored for {host} {check}")
        path.unlink()
        path.with_suffix(".json.bad").unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            path.parent.rmdir()  # only succeeds when it was the last check
        return f"{host} {check}: forgotten\n"
    folder = _root() / host
    checks = _checks_of(host)
    if not checks:
        raise ModuleError(f"baseline: nothing stored for host {host}")
    for p in folder.iterdir():
        if p.is_file():
            p.unlink()
    with contextlib.suppress(OSError):
        folder.rmdir()
    return f"{host}: forgot {len(checks)} check(s)\n"


def _list(names: list[str], stdin: str, opts) -> str:
    notes: list[str] = []
    result: dict[str, dict[str, int]] = {}
    for host in _hosts():
        for check in _checks_of(host):
            data = _load(host, check, notes)
            if data:
                result.setdefault(host, {})[check] = sum(1 for r in data["items"].values() if r.get("present"))
    if opts.output == "json":
        return _out({"hosts": result, "notes": notes})
    if not result:
        return "\n".join(notes + ["no baselines stored"]) + "\n"
    return "\n".join(notes + [f"{h}: " + ", ".join(f"{c} ({n})" for c, n in sorted(checks.items()))
                              for h, checks in sorted(result.items())]) + "\n"


_HANDLERS = {"diff": _diff, "put": _put, "show": _show, "accept": _accept, "forget": _forget, "list": _list}


def run(args: list[str], stdin: str) -> str:
    parser = ArgParser("baseline")
    parser.add_argument("subcommand", choices=COMMANDS, metavar="SUBCOMMAND")
    parser.add_argument("names", nargs="*", metavar="NAME")
    parser.add_argument("--no-store", action="store_true")
    parser.add_argument("--show-same", action="store_true")
    parser.add_argument("-o", "--output", default="text", choices=("text", "json"))
    opts = parser.parse_args(args)
    return _HANDLERS[opts.subcommand](opts.names, stdin, opts)
