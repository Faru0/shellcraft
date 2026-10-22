"""The text editor ShellCraft opens files in (`func edit`, `func new -e`, `profile edit`).

`settings editor NAME` chooses it (saved in the base config.json, shared by every profile); without
it, `$VISUAL`, then `$EDITOR`, then nano (notepad on Windows). Terminal editors (vim, nano…) run in
a subshell so they get the whole terminal; window editors (VS Code, Sublime…) open their own
window, started with their "wait" switch so ShellCraft reads the file back when you close it.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

CONFIG_KEY = "editor"

# Window editors and the switch that makes them wait until the file is closed.
WINDOW: dict[str, list[str]] = {
    "code": ["--wait"], "code-insiders": ["--wait"], "codium": ["--wait"], "cursor": ["--wait"],
    "zed": ["--wait"], "subl": ["-w"], "sublime_text": ["-w"], "kate": ["--block"], "gedit": ["--wait"],
    "gnome-text-editor": ["--standalone"], "mousepad": [], "xed": [], "notepad": [],
    "notepad++": ["-multiInst", "-nosession"],
}
NAMES = {"code": "VS Code", "code-insiders": "VS Code Insiders", "codium": "VSCodium", "cursor": "Cursor",
         "zed": "Zed", "subl": "Sublime Text", "sublime_text": "Sublime Text", "kate": "Kate", "gedit": "gedit",
         "notepad": "Notepad", "notepad++": "Notepad++"}
TERMINAL = ("nvim", "vim", "vi", "nano", "micro", "hx", "helix", "kak", "emacs", "joe", "ne", "mg", "pico")


def configured(config: dict[str, Any]) -> str | None:
    value = config.get(CONFIG_KEY)
    return value.strip() if isinstance(value, str) and value.strip() else None


def command(config: dict[str, Any]) -> list[str]:
    """The editor's command line: the setting, $VISUAL, $EDITOR, else nano / notepad."""
    value = configured(config) or os.environ.get("VISUAL") or os.environ.get("EDITOR") \
        or ("notepad" if sys.platform == "win32" else "nano")
    try:
        argv = shlex.split(value, posix=sys.platform != "win32")
    except ValueError:
        argv = value.split()
    return argv or ["nano"]


def source(config: dict[str, Any]) -> str:
    """Where the editor comes from, for `settings editor`."""
    if configured(config):
        return "settings editor"
    for var in ("VISUAL", "EDITOR"):
        if os.environ.get(var):
            return f"${var}"
    return "the default"


def _base(argv: list[str]) -> str:
    return Path(argv[0]).stem.lower() if argv else ""


def is_window(argv: list[str]) -> bool:
    """A window editor (its own window) rather than a terminal one. Emacs is a window unless -nw."""
    base = _base(argv)
    if base == "emacs":
        return not any(a in ("-nw", "-t", "--no-window-system") for a in argv[1:])
    return base in WINDOW


def with_wait(argv: list[str]) -> list[str]:
    """argv with the editor's wait switch added when it isn't there already."""
    switches = WINDOW.get(_base(argv), [])
    return argv + [s for s in switches if s not in argv]


def label(argv: list[str]) -> str:
    return NAMES.get(_base(argv), argv[0] if argv else "the editor")


def found() -> dict[str, str]:
    """The known editors on this machine's PATH: {command: what it is}."""
    out = {}
    for name in [*WINDOW, *TERMINAL]:
        if shutil.which(name):
            out[name] = f"{NAMES.get(name, name)} ({'window' if name in WINDOW else 'terminal'})"
    return out


def open_file(ctx: Any, path: Path) -> None:
    """Open `path` in the editor and return when it is closed. Raises OSError when it can't start."""
    argv = command(ctx.config)
    if is_window(argv):
        argv = with_wait(argv)
        if ctx.ui is not None:
            from rich.text import Text

            ctx.ui.console.print(Text(f"waiting for {label(argv)}: close the file's tab or window to continue",
                                      style="sc.muted"), markup=False, highlight=False)
        exe = shutil.which(argv[0]) or argv[0]
        try:
            subprocess.run([exe, *argv[1:], str(path)], check=False)
        except FileNotFoundError:
            raise OSError(f"editor not found: {argv[0]} (settings editor NAME chooses another)") from None
        return
    if ctx.interactive and getattr(ctx, "jobs", None) is not None:
        from shellcraft.pipeline import run_line

        if not shutil.which(argv[0]):
            raise OSError(f"editor not found: {argv[0]} (settings editor NAME chooses another)")
        quote = shlex.quote if sys.platform != "win32" else (lambda s: f'"{s}"')
        run_line("sub " + " ".join(quote(a) for a in [*argv, str(path)]), ctx)  # the whole terminal
        return
    try:
        subprocess.run([shutil.which(argv[0]) or argv[0], *argv[1:], str(path)], check=False)
    except FileNotFoundError:
        raise OSError(f"editor not found: {argv[0]} (settings editor NAME chooses another)") from None
