"""The git branch of a directory, read from `.git/HEAD` by hand (no `git` program, no subprocess).

The prompt asks for it before every command, so the answer is cached per directory for a
second: enough that typing stays instant, short enough that a checkout shows up right away.
"""

from __future__ import annotations

import time
from pathlib import Path

CACHE_SECONDS = 1.0
_cache: dict[str, tuple[float, str | None]] = {}


def branch(cwd: str | Path | None = None) -> str | None:
    """`main`, a short commit id when HEAD is detached, or None outside a repository."""
    try:
        start = Path.cwd() if cwd is None else Path(cwd)
    except OSError:
        return None  # the current directory was deleted
    key = str(start)
    now = time.monotonic()
    hit = _cache.get(key)
    if hit is not None and now - hit[0] < CACHE_SECONDS:
        return hit[1]
    value = _read_branch(start)
    _cache[key] = (now, value)
    if len(_cache) > 64:
        _cache.pop(next(iter(_cache)))
    return value


def _read_branch(start: Path) -> str | None:
    for directory in (start, *start.parents):
        dotgit = directory / ".git"
        try:
            if dotgit.is_dir():
                head = dotgit / "HEAD"
            elif dotgit.is_file():  # a worktree or submodule: "gitdir: /path/to/real/.git/worktrees/x"
                line = dotgit.read_text(encoding="utf-8", errors="replace").strip()
                if not line.startswith("gitdir:"):
                    return None
                gitdir = Path(line[7:].strip())
                head = (gitdir if gitdir.is_absolute() else directory / gitdir) / "HEAD"
            else:
                continue
            content = head.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return None
        if content.startswith("ref:"):
            ref = content[4:].strip()
            return ref[11:] if ref.startswith("refs/heads/") else ref
        return content[:7] or None
    return None
