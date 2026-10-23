"""Starter files for ~/.shellcraft, the way Linux copies /etc/skel into a new home folder.

The first time the interactive shell starts without one of them, ShellCraft writes it: an `rc`
and an `aliases` file that are all comments (they do nothing until you remove a `#`), an example
func and two example startup scripts named `.sample` (never loaded until you copy them to a
`.func` / `.py` name), and a README in the startup folder. Each file is written once: the names
are recorded under "seeded" in config.json, so a file you delete stays deleted.

`shellcraft --init` writes any that are missing again (never over your files); `--init --force`
also refreshes the `.sample` and README files to this version's text.

The texts live in shellcraft/starters/, laid out as they land in ~/.shellcraft.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

SOURCE = Path(__file__).resolve().parent / "starters"
CONFIG_KEY = "seeded"
REFRESHABLE = (".sample", ".txt")  # examples and notes: never something the user edits in place


def files() -> list[str]:
    """Every starter file, as a path relative to ~/.shellcraft (forward slashes)."""
    return sorted(p.relative_to(SOURCE).as_posix() for p in SOURCE.rglob("*")
                  if p.is_file() and "__pycache__" not in p.parts)


def _write(rel: str, home: Path) -> None:
    target = home / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes((SOURCE / rel).read_bytes())


def seed(config: Any, home: Path | None = None) -> list[str]:
    """Write each starter file that was never written before and doesn't exist; record them all.
    Returns what was written. Never raises: a start must not fail over a starter file."""
    from shellcraft.config import config_dir, save_config

    home = home or config_dir()
    recorded = config.get(CONFIG_KEY)
    done = set(recorded) if isinstance(recorded, list) else set()
    written: list[str] = []
    for rel in files():
        if rel in done:
            continue
        if not (home / rel).exists():
            try:
                _write(rel, home)
            except OSError:
                continue  # tried again at the next start
            written.append(rel)
        done.add(rel)
    if sorted(done) != recorded:
        config[CONFIG_KEY] = sorted(done)
        try:
            save_config(config)
        except OSError:
            pass
    return written


def init(force: bool = False, home: Path | None = None) -> list[tuple[str, str]]:
    """`shellcraft --init`: (file, what happened) for every starter file: created, kept, refreshed."""
    from shellcraft.config import config_dir, load_config, save_config

    home = home or config_dir()
    report = []
    for rel in files():
        target = home / rel
        if not target.exists():
            _write(rel, home)
            report.append((rel, "created"))
        elif force and rel.endswith(REFRESHABLE):
            _write(rel, home)
            report.append((rel, "refreshed"))
        else:
            report.append((rel, "kept"))
    config = load_config()
    config[CONFIG_KEY] = sorted(set(config.get(CONFIG_KEY) or []) | {rel for rel, _ in report})
    save_config(config)
    return report
