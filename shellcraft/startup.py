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

After the Python scripts, the shell sources `~/.shellcraft/rc`: ShellCraft command lines, run as if
typed at the prompt (`theme nord`, `export X=1`, `cd ~/work`, a module, `sub htop`), like
`~/.bashrc`. Never in `--mcp` mode. `source FILE` runs any such file by hand.

Not done yet (see TODO.md): background scripts that keep running, per-script timeouts.
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
from typing import Any

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
        from shellcraft.config import config_dir

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


# ── the rc file: ShellCraft command lines, like ~/.bashrc ────────────────────

RC_FILE = "rc"
MAX_SOURCE_DEPTH = 8  # `source` inside a sourced file, at most this deep


def rc_path(home: Path | None = None) -> Path:
    if home is None:
        from shellcraft.config import config_dir

        home = config_dir()
    return home / RC_FILE


def logical_lines(text: str) -> list[tuple[int, str]]:
    """(first line number, command) for each command in a file of ShellCraft lines: blank lines
    and `#` comments skipped, an unfinished `for` / `if` / `{` joined with the lines after it, as
    the prompt does. A block still open at the end is returned as is (running it reports why)."""
    from shellcraft import script

    out: list[tuple[int, str]] = []
    pending: tuple[int, str] | None = None
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if pending is None:
            if not line or line.startswith("#"):
                continue
            pending = (number, line)
        elif line and not line.startswith("#"):
            pending = (pending[0], script.join_continuation(pending[1], line))
        if pending is not None and script.incomplete(pending[1]) is None:
            out.append(pending)
            pending = None
    if pending is not None:
        out.append(pending)
    return out


_depth = [0]


def _report(ctx: Any, exc: Exception, where: str, line: str) -> None:
    """Say which line of which file failed, then show the error as the prompt would."""
    from shellcraft.context import ShellExit

    if isinstance(exc, ShellExit):
        raise exc
    if ctx.emit is not None:
        from rich.text import Text

        ctx.emit(Text(f"✗ {where}", style="sc.warning"))
    if ctx.emit_error is not None:
        ctx.emit_error(exc, line)


def source(path: Path, ctx: Any) -> tuple[int, int]:
    """Run every command of a file in `ctx` as if typed at the prompt: output and errors go to
    ctx.emit / ctx.emit_error as they happen; a failing line does not stop the rest. Returns
    (commands run, commands that failed). Raises OSError when the file can't be read."""
    from shellcraft.pipeline import run_line

    if _depth[0] >= MAX_SOURCE_DEPTH:
        raise RecursionError(f"source: nested more than {MAX_SOURCE_DEPTH} deep (a file sourcing itself?)")
    text = Path(path).read_text(encoding="utf-8")
    ran = failed = 0
    _depth[0] += 1
    try:
        for number, line in logical_lines(text):
            ran += 1
            where = f"{Path(path).name}:{number}: {line}"
            try:
                result = run_line(line, ctx)
            except Exception as exc:  # noqa: BLE001 — a failing line (or a bug) must not stop the file
                failed += 1
                _report(ctx, exc, where, line)
                continue
            if result is not None and ctx.emit is not None and result.output not in (None, ""):
                ctx.emit(result.output)
            if ctx.last_status:
                failed += 1
    finally:
        _depth[0] -= 1
    return ran, failed
