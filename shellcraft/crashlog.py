"""~/.shellcraft/crash.log: unexpected errors the shell survived (or didn't), for a bug report.

The REPL's guard, background threads (`threading.excepthook`) and the last-resort handler in
`cli.main` append a timestamped traceback here instead of printing it over the screen. The file is
owner-only and keeps its last MAX_BYTES.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

FILE = "crash.log"
MAX_BYTES = 200 * 1024
_lock = threading.Lock()


def path() -> Path:
    from shellcraft.config import config_dir

    return config_dir() / FILE


def record(exc: BaseException, where: str) -> Path | None:
    """Append `exc` (with its traceback) to the crash log; None when it can't be written."""
    from shellcraft import __version__

    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    entry = (f"===== {time.strftime('%Y-%m-%d %H:%M:%S')}  ShellCraft {__version__}  python "
             f"{sys.version.split()[0]}  {sys.platform}  ({where})\n{text}\n")
    target = path()
    try:
        with _lock:
            target.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as fh:
                fh.write(entry)
            if target.stat().st_size > MAX_BYTES:
                data = target.read_text(encoding="utf-8", errors="replace")[-MAX_BYTES:]
                cut = data.find("\n===== ")
                target.write_text(data[cut + 1:] if cut >= 0 else data, encoding="utf-8")
    except OSError:
        return None
    return target


def last() -> str:
    """The newest entry, for `diag`."""
    try:
        data = path().read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    cut = data.rfind("===== ")
    return data[cut:].rstrip() if cut >= 0 else ""


def install_thread_hook() -> None:
    """Background threads' uncaught errors go to the log, not over the prompt."""
    def hook(args: Any) -> None:
        if args.exc_type is SystemExit or args.exc_value is None:
            return
        name = args.thread.name if args.thread is not None else "thread"
        record(args.exc_value, f"background thread {name}")

    threading.excepthook = hook
