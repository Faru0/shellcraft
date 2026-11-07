"""Install ShellCraft's AI agents and skills (the ai/ folder) into Claude Code's folders.

    shellcraft --install-ai [NAME ...] [--user | --project] [--team] [--skills-only | --agents-only]
    shellcraft --check-ai [NAME ...]
    shellcraft --uninstall-ai [NAME ...]

Agents land in `<root>/agents/NAME.md` and skills in `<root>/skills/NAME/SKILL.md`, where `<root>` is
`~/.claude` (--user) or `./.claude` (--project). An agent brings the skills it names. Nothing is ever
overwritten silently: ~/.shellcraft/ai-installed.json remembers the text this installer wrote, so a
file you edited (or one of the same name that is not ours) is skipped with a diff, and `--force` writes
it after saving a `.bak`. Uninstall removes only files that are still exactly what was written.
"""

from __future__ import annotations

import contextlib
import difflib
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

# An installed copy has the files inside the package; a source checkout has ai/ next to shellcraft/.
_INSTALLED = Path(__file__).resolve().parent / "bundled_ai"
SOURCE = _INSTALLED if _INSTALLED.is_dir() else Path(__file__).resolve().parent.parent / "ai"
MANIFEST_NAME = "ai-installed.json"
TEAM = ("orchestrator", "worker", "monitor", "host-analyst", "log-reader", "feed-analyst", "ebpf-auditor",
        "edr-analyst")


class InstallError(Exception):
    """A bad request (an unknown name, no target), shown as one line."""


@dataclass(frozen=True)
class Item:
    name: str
    kind: str       # "agent" or "skill"
    source: Path
    rel: str        # path under the target root, forward slashes


@dataclass
class Result:
    rel: str
    status: str     # new, update, unchanged, edited, forced, removed, missing, kept
    diff: str = ""


def catalog(source: Path | None = None) -> dict[str, Item]:
    """Every agent and skill, by name. An agent and a skill never share a name."""
    root = source or SOURCE
    items: dict[str, Item] = {}
    for path in sorted((root / "agents").glob("*.md")):
        items[path.stem] = Item(path.stem, "agent", path, f"agents/{path.name}")
    for path in sorted((root / "skills").glob("*/SKILL.md")):
        name = path.parent.name
        items[name] = Item(name, "skill", path, f"skills/{name}/SKILL.md")
    return items


def needs(item: Item, items: dict[str, Item]) -> list[str]:
    """The skills an agent names in backticks (`task-board`), so installing it brings them."""
    if item.kind != "agent":
        return []
    text = item.source.read_text(encoding="utf-8")
    return sorted({m for m in re.findall(r"`([\w-]+)`", text) if m in items and items[m].kind == "skill"})


def select(names: list[str], team: bool = False, skills_only: bool = False, agents_only: bool = False,
           source: Path | None = None) -> list[Item]:
    """The items to act on: the named ones (with the skills agents need), --team's, or everything."""
    items = catalog(source)
    wanted = list(names) + (list(TEAM) if team else [])
    unknown = [n for n in wanted if n not in items]
    if unknown:
        raise InstallError(f"no agent or skill named {', '.join(unknown)} (known: {', '.join(items)})")
    if skills_only and agents_only:
        raise InstallError("--skills-only and --agents-only exclude each other")
    chosen: dict[str, Item] = {}
    if wanted:
        for name in wanted:
            chosen[name] = items[name]
            for skill in needs(items[name], items):
                chosen[skill] = items[skill]
    else:
        chosen = dict(items)
    return [i for i in chosen.values()
            if not (skills_only and i.kind == "agent") and not (agents_only and i.kind == "skill")]


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _text(path: Path) -> bytes:
    return path.read_bytes().replace(b"\r\n", b"\n")  # LF on disk whatever the checkout's line endings


def manifest_path(home: Path | None = None) -> Path:
    from shellcraft.config import config_dir

    return (home or config_dir()) / MANIFEST_NAME


def _load(home: Path | None) -> dict[str, dict[str, str]]:
    try:
        data = json.loads(manifest_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict[str, dict[str, str]], home: Path | None) -> None:
    path = manifest_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write(path, json.dumps(data, indent=2, sort_keys=True).encode("utf-8") + b"\n")


def _write(path: Path, data: bytes) -> None:
    """Write beside the file, then replace it: a crash never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_bytes(data)
    os.replace(temp, path)


def _classify(item: Item, target: Path, written: dict[str, str]) -> tuple[str, bytes]:
    want = _text(item.source)
    disk = target / item.rel
    if not disk.is_file():
        return "new", want
    have = _digest(disk.read_bytes().replace(b"\r\n", b"\n"))
    if have == _digest(want):
        return "unchanged", want
    if written.get(item.rel) == have:
        return "update", want  # still what we wrote, only the source moved on
    return "edited", want  # yours, or another file of the same name


def _diff(item: Item, target: Path, want: bytes) -> str:
    have = (target / item.rel).read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    new = want.decode("utf-8", errors="replace").splitlines(keepends=True)
    return "".join(difflib.unified_diff(have, new, f"{item.rel} (installed)", f"{item.rel} (this version)"))


def install(items: list[Item], target: Path, force: bool = False, dry_run: bool = False,
            home: Path | None = None) -> list[Result]:
    manifest = _load(home)
    key = str(target.resolve())
    written = manifest.setdefault(key, {})
    results: list[Result] = []
    for item in items:
        status, want = _classify(item, target, written)
        diff = ""
        if status == "edited":
            diff = _diff(item, target, want)
            if force:
                status = "forced"
        if status in ("new", "update", "forced") and not dry_run:
            disk = target / item.rel
            if status == "forced":
                disk.with_name(disk.name + ".bak").write_bytes(disk.read_bytes())
            _write(disk, want)
            written[item.rel] = _digest(want)
        elif status == "unchanged" and not dry_run:
            written.setdefault(item.rel, _digest(want))  # adopt a file that already matches
        results.append(Result(item.rel, status, diff))
    if not dry_run:
        with contextlib.suppress(OSError):
            _save(manifest, home)
    return results


def check(items: list[Item], target: Path, home: Path | None = None) -> list[Result]:
    """What `install` would do, without doing it: new = missing, update = out of date."""
    written = _load(home).get(str(target.resolve()), {})
    out: list[Result] = []
    for item in items:
        status, want = _classify(item, target, written)
        out.append(Result(item.rel, "missing" if status == "new" else status,
                          _diff(item, target, want) if status == "edited" else ""))
    return out


def uninstall(items: list[Item], target: Path, dry_run: bool = False, home: Path | None = None) -> list[Result]:
    manifest = _load(home)
    key = str(target.resolve())
    written = manifest.get(key, {})
    out: list[Result] = []
    for item in items:
        disk = target / item.rel
        if not disk.is_file():
            out.append(Result(item.rel, "missing"))
        elif written.get(item.rel) == _digest(disk.read_bytes().replace(b"\r\n", b"\n")):
            if not dry_run:
                disk.unlink()
                written.pop(item.rel, None)
                with contextlib.suppress(OSError):
                    if item.kind == "skill":
                        disk.parent.rmdir()  # only when empty
            out.append(Result(item.rel, "removed"))
        else:
            out.append(Result(item.rel, "kept"))  # edited, or not installed by us
    if not dry_run:
        if not written:
            manifest.pop(key, None)
        with contextlib.suppress(OSError):
            _save(manifest, home)
    return out
