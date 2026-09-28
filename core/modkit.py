"""Small helpers for module authors (optional — a module only needs `run(args, stdin)`)."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
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


@dataclass(frozen=True)
class EnvSetting:
    """An environment variable (usually an API key) a module needs, set with `settings NAME`.

    Declare them in the module as ENV_SETTINGS = [EnvSetting("MY_API_KEY", "My API key", "…")].
    The shell lists them in `settings` as "<module> · <label>", stores the value in
    config.json and exports it to os.environ, so the module just reads os.environ["MY_API_KEY"].
    """

    name: str  # the environment variable, e.g. "DNSDUMPSTER_API_KEY"
    label: str  # short human name, e.g. "DnsDumpster API key"
    description: str  # where to get it, what it unlocks
