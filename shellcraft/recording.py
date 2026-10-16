"""`record` / `script`: a transcript of the session, like Linux `script` without the escape codes.

What you type and what the shell prints go into a plain-text file as they happen, each command
with the time. The transcript is written through a tee on the shell's console file, so command
output, errors and notes are captured; the prompt itself (drawn by prompt_toolkit) and what
happens inside an attached subshell are not, and the footer's redraws are skipped. The prompt
shows a blinking 🔴 REC while it runs.
"""

from __future__ import annotations

import re
import socket
import time
from datetime import datetime
from pathlib import Path
from typing import IO, Any

from shellcraft.config import config_dir

RECORDS = "records"  # under ~/.shellcraft: the default folder for transcripts
# CSI, OSC and other escape sequences, and carriage returns: a transcript holds text only.
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]|\r")


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def default_path() -> Path:
    folder = config_dir() / RECORDS
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"session-{datetime.now():%Y-%m-%d-%H%M%S}.txt"


class Recorder:
    """One transcript file. `command()` writes a typed line with its time, `output()` the text
    the shell printed (escape codes removed), `note()` a line of its own."""

    def __init__(self, path: Path):
        self.path = path
        self.started = time.monotonic()
        self.commands = 0
        self._file: IO[str] = open(path, "a", encoding="utf-8")  # noqa: SIM115 — closed by close()
        self._at_line_start = True
        home = str(Path.home())
        cwd = str(Path.cwd())
        cwd = "~" + cwd[len(home):] if cwd.startswith(home) else cwd
        self._write(f"# ShellCraft session · started {datetime.now():%Y-%m-%d %H:%M:%S} · "
                    f"{socket.gethostname()} · {cwd}\n")

    def command(self, line: str) -> None:
        if not self._at_line_start:
            self._write("\n")
        self._write(f"[{datetime.now():%H:%M:%S}] ❯ {line}\n")
        self.commands += 1

    def output(self, text: str) -> None:
        clean = strip_ansi(text)
        if clean:
            self._write(clean)

    def note(self, text: str) -> None:
        if not self._at_line_start:
            self._write("\n")
        self._write(f"({text})\n")

    def _write(self, text: str) -> None:
        try:
            self._file.write(text)
            self._file.flush()
        except (OSError, ValueError):
            return
        self._at_line_start = text.endswith("\n")

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def close(self) -> None:
        if self._file.closed:
            return
        if not self._at_line_start:
            self._write("\n")
        minutes, seconds = divmod(int(self.elapsed), 60)
        self._write(f"# ended {datetime.now():%H:%M:%S} · {minutes} min {seconds} s · "
                    f"{self.commands} command{'s' if self.commands != 1 else ''}\n")
        self._file.close()


class Tee:
    """The console's file while recording: everything written goes to the terminal as before and,
    without its escape codes, to the transcript. `raw` is the terminal itself, for the footer."""

    def __init__(self, raw: Any, recorder: Recorder):
        self.raw = raw
        self.recorder = recorder

    def write(self, text: str) -> int:
        n = self.raw.write(text)
        self.recorder.output(text)
        return n if isinstance(n, int) else len(text)

    def flush(self) -> None:
        self.raw.flush()

    def __getattr__(self, name: str) -> Any:  # fileno, isatty, encoding, closed… from the terminal
        return getattr(self.raw, name)


def start(ctx: Any, path: Path | None = None) -> Recorder:
    """Begin a transcript at `path` (default: ~/.shellcraft/records/session-DATE.txt); the console
    writes through a Tee from now on. Raises OSError when the file cannot be opened."""
    if ctx.recording is not None:
        raise OSError(f"already recording to {ctx.recording.path}")
    recorder = Recorder(path or default_path())
    if ctx.ui is not None:
        ctx.ui.console.file = Tee(ctx.ui.console.file, recorder)
    ctx.recording = recorder
    return recorder


def stop(ctx: Any) -> Recorder | None:
    """End the transcript and put the console's file back; the Recorder that was closed."""
    recorder = ctx.recording
    if recorder is None:
        return None
    if ctx.ui is not None and isinstance(ctx.ui.console.file, Tee):
        ctx.ui.console.file = ctx.ui.console.file.raw
    recorder.close()
    ctx.recording = None
    return recorder
