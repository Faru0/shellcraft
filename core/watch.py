"""Notice when files in the modules directory change, for hot reload.

Polling file stats is cheap for a handful of modules, needs no extra dependency, and works the
same on Linux, macOS and Windows. The shell checks before each command; the MCP server checks
on a timer.
"""

from __future__ import annotations

from pathlib import Path

WATCHED = (".py", ".md", ".skill")


class ModuleWatcher:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self._snapshot = self._scan()

    def _scan(self) -> dict[str, tuple[int, int]]:
        files: dict[str, tuple[int, int]] = {}
        try:
            entries = list(self.directory.iterdir())
        except OSError:
            return files
        for path in entries:
            if path.suffix in WATCHED:
                try:
                    stat = path.stat()
                except OSError:
                    continue  # deleted between listing and stat
                files[path.name] = (stat.st_mtime_ns, stat.st_size)
        return files

    def changes(self) -> list[str]:
        """Names of files added, removed or modified since the last call (sorted)."""
        current = self._scan()
        changed = sorted(name for name in current.keys() | self._snapshot.keys()
                         if current.get(name) != self._snapshot.get(name))
        self._snapshot = current
        return changed
