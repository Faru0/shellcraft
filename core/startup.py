"""Startup scripts: Python files that run when the interactive shell starts, before the prompt.

SKELETON (2026-10-13). The shape is settled, the open questions are listed in TODO.md under
"Startup scripts". What works today:

- Every `*.py` in `~/.shellcraft/startup/` runs once, in name order (`10-env.py`, `20-mounts.py`,
  …), as `python FILE` with this interpreter and the shell's working directory. Files whose name
  starts with `_` are helpers and never run on their own (as in `modules/`).
- **Handing settings back.** A script is a separate process, so its own `os.environ` changes die
  with it. To set variables for the shell and everything it starts later (subshells, modules, OS
  programs, `mcp start`), a script appends `NAME=value` lines to the file named in
  `$SHELLCRAFT_ENV_FILE`, the way GitHub Actions uses `$GITHUB_ENV`:

      import os
      with open(os.environ["SHELLCRAFT_ENV_FILE"], "a") as f:
          f.write("PROJECT_ROOT=/srv/app\n")

  `NAME=` with nothing after it unsets NAME. The shell applies the lines after the script exits;
  a later script sees the variables of the earlier ones.
- A script that fails (nonzero exit, a traceback) or takes longer than TIMEOUT seconds is reported
  in one line and the shell starts anyway. Its output (stdout and stderr) is shown under the banner.
- `settings startup off` or `shellcraft --no-startup` skips them; `startup` lists them and their
  last result, `startup run [NAME]` runs them again.

Not done yet (see TODO.md): background scripts that keep running, running in `--mcp` mode,
ShellCraft commands in a startup file (`alias`, `theme`… lines), per-script timeouts.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

TIMEOUT = 30.0  # seconds a startup script may take before it is stopped
FOLDER = "startup"
ENV_FILE_VAR = "SHELLCRAFT_ENV_FILE"
_ENV_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


@dataclass
class Result:
    name: str
    status: int | None = None  # exit status; None while not run, -1 timed out, -2 could not start
    seconds: float = 0.0
    output: str = ""
    env: dict[str, str | None] = field(default_factory=dict)  # what it set (None: unset)

    @property
    def ok(self) -> bool:
        return self.status == 0

    @property
    def label(self) -> str:
        if self.status is None:
            return "not run"
        if self.status == -1:
            return f"timed out after {TIMEOUT:.0f}s"
        if self.status == -2:
            return "could not start"
        return "ok" if self.status == 0 else f"exit {self.status}"


def folder(home: Path | None = None) -> Path:
    if home is None:
        from core.config import config_dir

        home = config_dir()
    return home / FOLDER


def scripts(home: Path | None = None) -> list[Path]:
    """The startup scripts, in the order they run (helpers `_x.py` left out)."""
    where = folder(home)
    if not where.is_dir():
        return []
    return sorted((p for p in where.glob("*.py") if not p.name.startswith("_")), key=lambda p: p.name.lower())


def parse_env(text: str) -> dict[str, str | None]:
    """`NAME=value` lines from a script's env file; `NAME=` unsets. Other lines are ignored."""
    found: dict[str, str | None] = {}
    for line in text.splitlines():
        m = _ENV_LINE.match(line.rstrip("\r"))
        if m:
            found[m.group(1)] = m.group(2) or None
    return found


def apply_env(changes: dict[str, str | None]) -> None:
    for name, value in changes.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def run_one(script: Path, timeout: float = TIMEOUT) -> Result:
    """Run one startup script and apply the variables it handed back."""
    result = Result(script.stem)
    fd, env_path = tempfile.mkstemp(prefix="shellcraft-env-", suffix=".txt")
    os.close(fd)
    env = dict(os.environ)
    env[ENV_FILE_VAR] = env_path
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(script.parent), env.get("PYTHONPATH")) if p)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    started = time.monotonic()
    try:
        done = subprocess.run([sys.executable, str(script)], capture_output=True, cwd=os.getcwd(), env=env,
                              timeout=timeout, stdin=subprocess.DEVNULL)
        result.status = done.returncode
        result.output = (done.stdout + done.stderr).decode("utf-8", errors="replace").replace("\r\n", "\n")
    except subprocess.TimeoutExpired as exc:
        result.status = -1
        result.output = ((exc.stdout or b"") + (exc.stderr or b"")).decode("utf-8", errors="replace")
    except OSError as exc:
        result.status, result.output = -2, str(exc)
    finally:
        result.seconds = time.monotonic() - started
        try:
            result.env = parse_env(Path(env_path).read_text(encoding="utf-8", errors="replace"))
        except OSError:
            result.env = {}
        try:
            os.unlink(env_path)
        except OSError:
            pass
    if result.status == 0:
        apply_env(result.env)  # only a script that finished cleanly changes the environment
    return result


def run_all(home: Path | None = None, only: str | None = None) -> list[Result]:
    """Run every startup script (or the one named `only`), in order."""
    results = []
    for script in scripts(home):
        if only is not None and script.stem != only:
            continue
        results.append(run_one(script))
    return results
