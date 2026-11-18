"""The history file, for taking chosen entries out of it (`history 5 -c`).

prompt_toolkit's `FileHistory` only appends. A record is a `# time` line and one `+` line for each
line of the command, after an empty line; this reads the file the way it does.
"""

from __future__ import annotations

import os
import tempfile
from bisect import bisect_left
from pathlib import Path


def records(path: Path) -> tuple[list[tuple[list[bytes], str]], list[bytes]]:
    """The records of the file, oldest first, as (its lines as they are, the command), and the
    lines after the last one."""
    found: list[tuple[list[bytes], str]] = []
    lines: list[bytes] = []
    command: list[str] = []

    def close() -> None:
        if command:
            found.append((lines[:], "".join(command)[:-1]))
            lines.clear()
            command.clear()

    with open(path, "rb") as handle:
        for line in handle:
            if line.startswith(b"+"):
                command.append(line[1:].decode("utf-8", errors="replace"))
            else:
                close()
            lines.append(line)
    close()
    return found, lines


def remove(path: Path, entries: list[str], numbers: list[int]) -> None:
    """Take the entries with these numbers (1 is `entries[0]`) out of the file.

    `entries` is the history as the shell holds it. The file may hold more: another shell that
    shares it adds its lines in between. So the list is matched to the file in order, and a record
    goes only when its command is the entry that was named. An entry the file no longer has is
    left to the caller's own list.
    """
    path = Path(os.path.realpath(path))  # a history kept in a dotfiles folder stays a link
    try:
        found, rest = records(path)
    except FileNotFoundError:
        return
    places: dict[str, list[int]] = {}
    for index, (_, command) in enumerate(found):
        places.setdefault(command, []).append(index)
    wanted = set(numbers)
    gone: set[int] = set()
    start = 0
    for number, entry in enumerate(entries[:max(wanted, default=0)], start=1):
        candidates = places.get(entry, [])
        at = bisect_left(candidates, start)
        if at == len(candidates):
            continue
        start = candidates[at] + 1
        if number in wanted:
            gone.add(candidates[at])
    if not gone:
        return
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".history-")
    try:
        with os.fdopen(fd, "wb") as handle:
            for index, (lines, _) in enumerate(found):
                if index not in gone:
                    handle.writelines(lines)
            handle.writelines(rest)
        os.chmod(temp, os.stat(path).st_mode & 0o777)
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise
