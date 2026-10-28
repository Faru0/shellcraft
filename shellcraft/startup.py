"""Startup: what runs when the interactive shell starts, before the first prompt.

Like bash's `~/.bashrc` followed by `~/.bashrc.d/*`:

1. `~/.shellcraft/rc`: ShellCraft command lines, run as if typed at the prompt (`theme nord`,
   `env set X=1`, `cd ~/work`, a module, `sub htop`).
2. `~/.shellcraft/startup/*.sc`: more files of the same kind, each for one job, run in name order
   (`10-vpn.sc`, `20-project.sc`). They run in the shell itself, so what one sets (`env set`, `cd`,
   a variable from `input()`) the next one, and the prompt, see. A file whose name starts with `_`
   is a helper: never run on its own, `source`d by the others.
3. In a profile, its own `rc` and then its `startup/*.sc` (shellcraft.profiles.run_rc), at start
   and on `profile use`.

A failing line is reported with its file and line number and the rest still run (after `set -e`,
the rest of that file is skipped); Ctrl-C skips the rest of that file. Never for `--mcp` or a lite
subshell; `--no-startup` or `settings startup off` skip it all. `startup` lists the files and their
last result, `startup run [NAME]` runs them again.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FOLDER = "startup"
SUFFIX = ".sc"


@dataclass
class Result:
    """How one startup file went the last time it ran."""

    name: str
    ran: int = 0  # commands run
    failed: int = 0  # of those, how many failed
    seconds: float = 0.0
    error: str = ""  # the file couldn't be read

    @property
    def ok(self) -> bool:
        return not self.failed and not self.error

    @property
    def label(self) -> str:
        if self.error:
            return f"not run: {self.error}"
        lines = f"{self.ran} command{'s' if self.ran != 1 else ''}"
        return f"{lines}, ok" if not self.failed else f"{lines}, {self.failed} failed"


def folder(home: Path | None = None) -> Path:
    if home is None:
        from shellcraft.config import config_dir

        home = config_dir()
    return home / FOLDER


def scripts(where: Path) -> list[Path]:
    """The `.sc` files of a startup folder, in the order they run (helpers `_x.sc` left out)."""
    if not where.is_dir():
        return []
    return sorted((p for p in where.glob(f"*{SUFFIX}") if not p.name.startswith("_")),
                  key=lambda p: p.name.lower())


def leftover_python(where: Path) -> list[Path]:
    """`.py` files in a startup folder: Python startup scripts from older versions, no longer run."""
    if not where.is_dir():
        return []
    return sorted(p for p in where.glob("*.py") if not p.name.startswith("_"))


def run_folder(ctx: Any, where: Path, label: str | None = None, only: str | None = None) -> list[Result]:
    """Source every `.sc` file of `where` in name order (or just the one named `only`, with or
    without `.sc`); each result is also kept in ctx.startup_results under its path."""
    results = []
    for path in scripts(where):
        if only is not None and only not in (path.name, path.stem):
            continue
        result = Result(path.name)
        started = time.monotonic()
        try:
            result.ran, result.failed = source(path, ctx, label=f"{label}/{path.name}" if label else path.name)
        except (OSError, UnicodeDecodeError, RecursionError) as exc:
            result.error = str(exc)
        result.seconds = time.monotonic() - started
        results.append(result)
        ctx.startup_results[str(path)] = result
    return results


# ── the rc file: ShellCraft command lines, like ~/.bashrc ────────────────────

RC_FILE = "rc"
MAX_SOURCE_DEPTH = 8  # `source` inside a sourced file, at most this deep


def rc_path(home: Path | None = None) -> Path:
    if home is None:
        from shellcraft.config import config_dir

        home = config_dir()
    return home / RC_FILE


def logical_lines(text: str, colon_blocks: bool = True) -> list[tuple[int, str]]:
    """(first line number, command) for each command in a file of ShellCraft lines: blank lines
    and `#` comments skipped, an unfinished `for` / `if` / `{` joined with the lines after it, as
    the prompt does. A block still open at the end is returned as is (running it reports why).
    Python-style colon blocks are turned into the brace form first (shellcraft.indent); one that is
    indented wrongly raises indent.IndentError (with colon_blocks False, the lines are left as
    they are)."""
    from shellcraft import indent, script
    from shellcraft.parser import strip_comments

    out: list[tuple[int, str]] = []
    pending: tuple[int, str] | None = None
    colon = colon_blocks and indent.has_colon_block(text)
    lines = indent.to_braces(text) if colon else enumerate(text.splitlines(), 1)
    for number, raw in lines:
        line = strip_comments(raw).strip()  # `# …` lines and `cmd  # note` comments
        if pending is None:
            if not line:
                continue
            pending = (number, line)
        elif line:
            pending = (pending[0], script.join_continuation(pending[1], line))
        if pending is not None and script.incomplete(pending[1]) is None:
            out.append(pending)
            pending = None
    if pending is not None:
        out.append(pending)
    return out


SKIPPED = " (set -e: the rest of the file was skipped)"
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


def source(path: Path, ctx: Any, label: str | None = None) -> tuple[int, int]:
    """Run every command of a file in `ctx` as if typed at the prompt: output and errors go to
    ctx.emit / ctx.emit_error as they happen; a failing line does not stop the rest, unless `set -e`
    is on (or `set -u` met an unset variable): then the rest of the file is skipped. Returns
    (commands run, commands that failed). Raises OSError when the file can't be read. `label` names
    the file for `env` (a profile's rc: `profile:osint`); by default its file name."""
    from shellcraft.indent import IndentError
    from shellcraft.pipeline import UnboundError, errexit, run_line

    if _depth[0] >= MAX_SOURCE_DEPTH:
        raise RecursionError(f"source: nested more than {MAX_SOURCE_DEPTH} deep (a file sourcing itself?)")
    text = Path(path).read_text(encoding="utf-8")
    ran = failed = 0
    _depth[0] += 1
    sourcing.append(label or Path(path).name)
    try:
        try:
            commands = logical_lines(text)
        except IndentError as exc:  # like Python: a badly indented file doesn't run at all
            raw = text.splitlines()[exc.line - 1] if 0 < exc.line <= len(text.splitlines()) else ""
            _report(ctx, exc, f"{Path(path).name}:{exc.line}: {raw.strip()}", raw)
            ctx.last_status = 2
            return 0, 1
        for number, line in commands:
            ran += 1
            where = f"{Path(path).name}:{number}: {line}"
            try:
                result = run_line(line, ctx)
            except KeyboardInterrupt:  # Ctrl-C: this file stops here; the shell goes on
                failed += 1
                from shellcraft.context import CommandError

                _report(ctx, CommandError("interrupted (Ctrl-C); the rest of the file was skipped", 130), where, line)
                ctx.last_status = 130
                break
            except Exception as exc:  # noqa: BLE001 — a failing line (or a bug) must not stop the file
                failed += 1
                stop = errexit(ctx) or isinstance(exc, UnboundError)
                _report(ctx, exc, f"{where}{SKIPPED if stop else ''}", line)
                if stop:
                    break
                continue
            if result is not None and ctx.emit is not None and result.output not in (None, ""):
                ctx.emit(result.output)
            if ctx.last_status:
                failed += 1
            if result is not None and result.stopped:
                if ctx.emit is not None:  # the failing command has said why: just say where
                    from rich.text import Text

                    ctx.emit(Text(f"✗ {where}{SKIPPED}", style="sc.warning"))
                break
    finally:
        _depth[0] -= 1
        sourcing.pop()
    return ran, failed
