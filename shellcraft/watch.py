"""Notice when files in the modules directory change, for hot reload.

Polling file stats is cheap for a handful of modules, needs no extra dependency, and works the
same on Linux, macOS and Windows. The shell checks before each command; the MCP server checks
on a timer.
"""

from __future__ import annotations

from pathlib import Path

WATCHED = (".py", ".md", ".skill")


class ModuleWatcher:
    """Watches one or more module folders (the main one and ~/.shellcraft/modules); a folder that
    does not exist yet is fine, and is picked up once it is created."""

    def __init__(self, *directories: Path):
        self.directories = [Path(d) for d in directories]
        self.directory = self.directories[0]
        self._snapshot = self._scan()

    def _scan(self) -> dict[str, tuple[int, int]]:
        files: dict[str, tuple[int, int]] = {}
        for directory in self.directories:
            try:
                entries = list(directory.iterdir())
            except OSError:
                continue
            for path in entries:
                if path.suffix in WATCHED:
                    try:
                        stat = path.stat()
                    except OSError:
                        continue  # deleted between listing and stat
                    files[str(path)] = (stat.st_mtime_ns, stat.st_size)
        return files

    def changes(self) -> list[str]:
        """Names of files added, removed or modified since the last call (sorted, no folder)."""
        current = self._scan()
        changed = {Path(key).name for key in current.keys() | self._snapshot.keys()
                   if current.get(key) != self._snapshot.get(key)}
        self._snapshot = current
        return sorted(changed)
