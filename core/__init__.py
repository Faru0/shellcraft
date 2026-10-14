"""Compatibility shim: ShellCraft's package is `shellcraft` since 0.3.0; `core` is its old name.

`import core.modkit` (and any `core.X`) gives the very same module object as `shellcraft.X`, so
modules written for 0.2 keep working unchanged. `python -m core.cli` and `python -m core.alerts`
still run (real files here, for runpy). This shim is kept for the 0.3 releases and removed in
0.4: change `from core.modkit import …` to `from shellcraft.modkit import …`.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import sys
import warnings
from pathlib import Path

from shellcraft import *  # noqa: F401,F403 — __version__ and friends
from shellcraft import __version__  # noqa: F401

_HERE = Path(__file__).resolve().parent


class _Alias(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """`core.X` → the module object of `shellcraft.X` (except the runnable files kept here)."""

    def find_spec(self, name, path=None, target=None):  # type: ignore[no-untyped-def]
        if not name.startswith("core."):
            return None
        first = name.split(".")[1]
        if (_HERE / f"{first}.py").is_file():  # core/cli.py, core/alerts.py: real files, for -m
            return None
        return importlib.util.spec_from_loader(name, self)

    def create_module(self, spec):  # type: ignore[no-untyped-def]
        return importlib.import_module("shellcraft." + spec.name[len("core."):])

    def exec_module(self, module):  # type: ignore[no-untyped-def]
        pass  # already executed as shellcraft.X


if not any(isinstance(f, _Alias) for f in sys.meta_path):
    sys.meta_path.insert(0, _Alias())

warnings.warn("the 'core' package is now 'shellcraft': import shellcraft.modkit instead of core.modkit "
              "(the 'core' name goes away in 0.4)", DeprecationWarning, stacklevel=2)
