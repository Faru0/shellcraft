"""Startup scripts: Python files that run when the interactive shell starts, before the prompt.

What runs, in order:

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

Header lines at the top of a script (`# shellcraft: background, restart, timeout=N`) start it as
a subshell instead (the Supervisor, below), restart it after a crash, or give it more time.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import threading
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


_HEADER = re.compile(r"^#\s*shellcraft:\s*(.+)$", re.I)
RESTART_DELAYS = (2.0, 4.0, 8.0, 16.0, 32.0, 60.0)  # seconds before each restart, then 60 s
CRASH_LIMIT, CRASH_WINDOW = 5, 600.0  # this many crashes within this many seconds: give up


def header(script: Path) -> dict[str, Any]:
    """The `# shellcraft: …` lines at the top of a script: background, restart, timeout=N.
    Several can share a line (`# shellcraft: background, restart`)."""
    found: dict[str, Any] = {"background": False, "restart": False, "timeout": None}
    try:
        lines = script.read_text(encoding="utf-8", errors="replace").splitlines()[:30]
    except OSError:
        return found
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#"):
            break  # the header ends at the first line of code
        m = _HEADER.match(line)
        if not m:
            continue
        for word in re.split(r"[,\s]+", m.group(1).strip()):
            key, _, value = word.partition("=")
            key = key.lower()
            if key in ("background", "restart"):
                found[key] = True
            elif key == "timeout":
                try:
                    found["timeout"] = max(1.0, float(value))
                except ValueError:
                    pass
    return found


def background_scripts(home: Path | None = None) -> list[Path]:
    return [p for p in scripts(home) if header(p)["background"]]


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
    """Run every normal startup script (or the one named `only`), in order, each with its own
    `timeout=` if it has one. Background scripts are left to the Supervisor."""
    results = []
    for script in scripts(home):
        if only is not None and script.stem != only:
            continue
        info = header(script)
        if info["background"]:
            continue
        results.append(run_one(script, timeout=info["timeout"] or TIMEOUT))
    return results


# ── background scripts: subshells started with the prompt, restarted on request ──

@dataclass
class Managed:
    """One background script and its subshell."""

    name: str
    path: Path
    restart: bool
    job: Any = None
    crashes: list[float] = field(default_factory=list)  # when it exited with an error lately
    stopped: bool = False  # stopped by the user: never restarted
    gave_up: bool = False
    timer: Any = None


class Supervisor:
    """Starts the background startup scripts as program subshells (a hidden process where there is
    no pseudo-terminal), and restarts those with `# shellcraft: restart` when they exit with an
    error: after 2, 4, 8… up to 60 s, giving up (with a red alert) after CRASH_LIMIT crashes in
    CRASH_WINDOW seconds. A clean exit (status 0), or a stop by the user, is never restarted."""

    def __init__(self, table: Any, alerts: Any = None, home: Path | None = None):
        self.table = table
        self.alerts = alerts
        self.home = home
        self.scripts: dict[str, Managed] = {}
        self.closed = False
        table.on_done.append(self._job_done)

    # ── control ──────────────────────────────────────────────────────────────

    def start_all(self) -> list[Managed]:
        for script in background_scripts(self.home):
            self.start(script.stem)
        return list(self.scripts.values())

    def start(self, name: str) -> Managed:
        """Start (or start again) background script NAME. Raises KeyError for an unknown name."""
        managed = self.scripts.get(name)
        if managed is None:
            path = next((p for p in background_scripts(self.home) if p.stem == name), None)
            if path is None:
                raise KeyError(name)
            managed = self.scripts[name] = Managed(name, path, header(path)["restart"])
        if managed.job is not None and managed.job.running:
            return managed
        managed.stopped = managed.gave_up = False
        managed.restart = header(managed.path)["restart"]
        managed.job = self._spawn(managed)
        return managed

    def stop(self, name: str | None = None) -> list[Managed]:
        """Stop one background script (or all): no restart until `start`."""
        targets = [self.scripts[name]] if name is not None else list(self.scripts.values())
        for managed in targets:
            managed.stopped = True
            if managed.timer is not None:
                managed.timer.cancel()
                managed.timer = None
            if managed.job is not None and managed.job.running:
                self.table.kill(managed.job)
        return targets

    def restart(self, name: str) -> Managed:
        if name not in self.scripts:
            self.start(name)  # raises KeyError for an unknown name
        self.stop(name)
        return self.start(name)

    def close(self) -> None:
        self.closed = True
        for managed in self.scripts.values():
            managed.stopped = True
            if managed.timer is not None:
                managed.timer.cancel()

    def find(self, job: Any) -> Managed | None:
        return next((m for m in self.scripts.values() if m.job is job), None)

    # ── internals ────────────────────────────────────────────────────────────

    def _spawn(self, managed: Managed) -> Any:
        """Start the script as a subshell; its previous, finished subshell leaves the table, so a
        restarted script keeps one line in the panel."""
        old = managed.job
        if old is not None and not old.running:
            self.table.jobs.pop(old.number, None)
        from shellcraft.alerts import SUB_ENV, with_shim
        from shellcraft.config import config_dir
        from shellcraft.jobs import JobsUnsupported

        command = f"startup: {managed.path.name}"
        env = with_shim(dict(os.environ), self.home or config_dir())
        env[SUB_ENV] = f"{self.table.next_number}:{command}"  # alerts name the script
        env["PYTHONPATH"] = os.pathsep.join(p for p in (str(managed.path.parent), env.get("PYTHONPATH")) if p)
        env.setdefault("PYTHONIOENCODING", "utf-8")
        argv = [sys.executable, str(managed.path)]
        try:
            return self.table.start_program(argv, command, env=env, cwd=os.getcwd())
        except JobsUnsupported:  # Windows without pywinpty: a hidden process, its output in the job log
            return self.table.start_hidden(argv, command, env=env, cwd=os.getcwd())

    def _job_done(self, job: Any) -> None:
        managed = self.find(job)
        if managed is None or self.closed or managed.stopped or job.killed or not managed.restart:
            return
        if job.status == 0:
            return  # finished its work: not a crash
        now = time.monotonic()
        managed.crashes = [t for t in managed.crashes if now - t < CRASH_WINDOW] + [now]
        if len(managed.crashes) >= CRASH_LIMIT:
            managed.gave_up = True
            if self.alerts is not None:
                self.alerts.post(f"startup {managed.path.name} crashed {CRASH_LIMIT} times in "
                                 f"{int(CRASH_WINDOW // 60)} min: not restarted (startup start {managed.name})",
                                 "crit", source="startup")
            return
        delay = RESTART_DELAYS[min(len(managed.crashes) - 1, len(RESTART_DELAYS) - 1)]
        managed.timer = threading.Timer(delay, self._restart_now, args=(managed,))
        managed.timer.daemon = True
        managed.timer.start()

    def _restart_now(self, managed: Managed) -> None:
        managed.timer = None
        if self.closed or managed.stopped:
            return
        try:
            managed.job = self._spawn(managed)
        except OSError as exc:
            if self.alerts is not None:
                self.alerts.post(f"startup {managed.path.name} could not restart: {exc}", "crit",
                                 source="startup")


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
sourcing: list[str] = []  # the files being sourced, innermost last (`env set` records where it ran)


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
    sourcing.append(Path(path).name)
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
        sourcing.pop()
    return ran, failed
