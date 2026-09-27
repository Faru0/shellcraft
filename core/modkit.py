"""Small helpers for module authors (optional — a module only needs `run(args, stdin)`)."""

from __future__ import annotations

import argparse
from typing import NoReturn


class ModuleError(Exception):
    """A user-facing failure: the shell shows the message without a traceback."""


class ArgParser(argparse.ArgumentParser):
    """argparse that raises ModuleError instead of printing and calling sys.exit()."""

    def __init__(self, prog: str, **kwargs):
        kwargs.setdefault("add_help", False)
        super().__init__(prog=prog, **kwargs)

    def error(self, message: str) -> NoReturn:
        raise ModuleError(f"{self.prog}: {message}  (see `man {self.prog}`)")

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        raise ModuleError(message or f"{self.prog}: exited with status {status}")
