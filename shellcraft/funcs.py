"""Funcs: named, on-demand scripts of ShellCraft lines, like the rc file but run when called.

A func file (`NAME.func`) is a small header of `# key: value` lines, then the steps:

    # func: recon
    # description: Quick domain recon: certificates, DNS, owners
    # system: no        (yes: it runs OS programs; refused while system_commands is off)
    # output: show      (show: each step's output prints | quiet: only echo / printf and @ steps)
    # command: no       (yes: typing `recon` runs it, besides `func run recon`)
    # usage: recon DOMAIN
    queryCert -s $1 > subs-$1.txt
    queryDns $1
    @ cat subs-$1.txt | wc -l

A step is a logical line (a `for … done` over several lines is one step); other `#` lines are
comments. `func run NAME ARGS` runs the steps one after another in the shell, as if typed (a `cd`
or `env set` stays), with `$1`, `$@` and `$#` set to ARGS, and stops at the first failing step.
`-i` runs it isolated (the directory, variables, environment, aliases, theme and settings are put
back afterwards, and nothing is saved meanwhile).

They live in `~/.shellcraft/funcs/` (the default setup, available in every profile) and
`~/.shellcraft/profiles/NAME/funcs/` (the active profile's, which win over a default one of the
same name); a func made without `--save` lives in the session only (`ctx.funcs`).
"""

from __future__ import annotations

import copy
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SUFFIX = ".func"
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,47}$")
HEADER = re.compile(r"^#\s*(func|description|system|output|command|usage)\s*:\s*(.*?)\s*$", re.I)
OUTPUTS = ("show", "quiet")
MAX_DEPTH = 16  # funcs running inside funcs (a func that runs itself by name stops here)
_local = threading.local()  # .running: the funcs running in this thread, innermost last


def _running() -> list[str]:
    if not hasattr(_local, "running"):
        _local.running = []
    return _local.running
SPEAKERS = ("echo", "printf")  # in a quiet func their output still shows: that's how a script talks to you


class FuncError(Exception):
    """A func that can't be found, read, saved or run."""


@dataclass
class Func:
    name: str
    description: str = ""
    system: bool = False
    output: str = "show"
    command: bool = False
    usage: str = ""
    body: str = ""  # the lines after the header, comments included
    notes: str = ""  # `#` comment lines above the header (how to use it, who wrote it), kept as they are
    where: str = "session"  # "session", "default", or the profile's name
    path: Path | None = None

    @property
    def steps(self) -> list[str]:
        from shellcraft.indent import IndentError
        from shellcraft.startup import logical_lines

        try:
            return [line for _, line in logical_lines(self.body)]
        except IndentError:  # badly indented: the steps as written, so running one names the problem
            return [line for _, line in logical_lines(self.body, colon_blocks=False)]

    def set_steps(self, steps: list[str]) -> None:
        self.body = "".join(step.rstrip("\n") + "\n" for step in steps)

    def copy(self) -> Func:
        return copy.copy(self)


def check_name(name: str) -> str:
    if not NAME.match(name):
        raise FuncError(f"invalid func name '{name}' (letters, digits, _ and -, starting with a letter or _)")
    return name


def _yes(value: str) -> bool:
    return value.strip().lower() in ("yes", "y", "true", "on", "1")


def parse(text: str, name: str | None = None) -> Func:
    """A func from its file's text; `name` (the file name) when the header has none."""
    func = Func(name=name or "")
    lines = text.splitlines()
    i = 0
    # Comment lines above the header (`# How to use this func: …`) are notes, not the end of it.
    while i < len(lines) and not HEADER.match(lines[i].strip()) \
            and (not lines[i].strip() or lines[i].strip().startswith("#")):
        i += 1
    func.notes = "\n".join(lines[:i]).strip("\n")
    while i < len(lines):
        m = HEADER.match(lines[i].strip())
        if not m:
            if lines[i].strip() == "" and i == 0:
                i += 1
                continue
            break
        key, value = m.group(1).lower(), m.group(2)
        if key == "func":
            func.name = func.name or value
        elif key == "description":
            func.description = value
        elif key in ("system", "command"):
            setattr(func, key, _yes(value))
        elif key == "output":
            func.output = value.lower() if value.lower() in OUTPUTS else "show"
        elif key == "usage":
            func.usage = value
        i += 1
    func.body = "\n".join(lines[i:]).strip("\n") + ("\n" if lines[i:] else "")
    return func


def render(func: Func) -> str:
    """The file's text: its notes, the header, then the body."""
    header = [f"# func: {func.name}", f"# description: {func.description}",
              f"# system: {'yes' if func.system else 'no'}", f"# output: {func.output}",
              f"# command: {'yes' if func.command else 'no'}"]
    if func.usage:
        header.append(f"# usage: {func.usage}")
    notes = func.notes + "\n\n" if func.notes else ""
    return notes + "\n".join(header) + "\n" + func.body


# ── where they live ──────────────────────────────────────────────────────────

def default_dir() -> Path:
    from shellcraft.config import config_dir

    return config_dir() / "funcs"


def profile_dir(profile: str) -> Path:
    from shellcraft import profiles

    return profiles.folder(profile) / "funcs"


def target_dir(config: Any, global_: bool = False) -> tuple[Path, str]:
    """Where `--save` writes: the active profile's folder, or the default one (`-g`, no profile)."""
    from shellcraft import profiles

    current = profiles.active(config)
    if current and not global_:
        return profile_dir(current), current
    return default_dir(), "default"


def load_dir(folder: Path, where: str) -> dict[str, Func]:
    out: dict[str, Func] = {}
    try:
        files = sorted(folder.glob(f"*{SUFFIX}"))
    except OSError:
        return out
    for path in files:
        try:
            func = parse(path.read_text(encoding="utf-8"), path.stem)
        except (OSError, UnicodeDecodeError):
            continue
        func.name, func.where, func.path = path.stem, where, path
        out[path.stem] = func
    return out


def available(ctx: Any) -> dict[str, Func]:
    """Every func this shell can run: the default ones, the active profile's over them, then the
    session's over both."""
    from shellcraft import profiles

    found = load_dir(default_dir(), "default")
    current = profiles.active(ctx.config)
    if current:
        found.update(load_dir(profile_dir(current), current))
    found.update({n: f for n, f in getattr(ctx, "funcs", {}).items()})
    return found


def shadowed(ctx: Any, name: str) -> str | None:
    """Where a func of the same name hides behind the one in use ("default" or the profile)."""
    from shellcraft import profiles

    layers = [("default", load_dir(default_dir(), "default"))]
    current = profiles.active(ctx.config)
    if current:
        layers.append((current, load_dir(profile_dir(current), current)))
    holders = [where for where, funcs in layers if name in funcs]
    in_use = available(ctx).get(name)
    hidden = [w for w in holders if in_use is not None and w != in_use.where]
    return hidden[-1] if hidden else None


def find(ctx: Any, name: str) -> Func:
    func = available(ctx).get(name)
    if func is None:
        known = ", ".join(sorted(available(ctx))) or "none yet (func new NAME)"
        raise FuncError(f"no func '{name}' (funcs: {known})")
    return func


def save(func: Func, folder: Path, where: str) -> Func:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{check_name(func.name)}{SUFFIX}"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(render(func), encoding="utf-8")
    os.replace(tmp, path)
    saved = func.copy()
    saved.where, saved.path = where, path
    return saved


def store(ctx: Any, func: Func) -> None:
    """Keep a changed func where it lives: its file, or the session."""
    if func.where == "session" or func.path is None:
        ctx.funcs[func.name] = func
    else:
        save(func, func.path.parent, func.where)


def remove(ctx: Any, func: Func) -> None:
    if func.where == "session" or func.path is None:
        ctx.funcs.pop(func.name, None)
    else:
        func.path.unlink(missing_ok=True)


def as_command(ctx: Any, name: str) -> Func | None:
    """The func `name` runs when typed, if its header says `command: yes`."""
    if not getattr(ctx, "allow_aliases", False) or not NAME.match(name or ""):
        return None
    func = available(ctx).get(name)
    return func if func is not None and func.command else None


# ── running ──────────────────────────────────────────────────────────────────

@dataclass
class Outcome:
    status: int = 0
    steps: int = 0
    failed: int | None = None  # the failing step's number
    failed_line: str = ""
    error: str = ""
    seconds: float = 0.0
    collected: list[Any] = field(default_factory=list)  # outputs, when nothing prints them as they come


def run(ctx: Any, func: Func, args: list[str], output: str | None = None, isolated: bool = False) -> Outcome:
    """Run the steps in the shell, one after another; stop at the first that fails."""
    from shellcraft.context import ShellExit
    from shellcraft.pipeline import run_line

    if func.system and not ctx.allow_system:
        raise FuncError(f"{func.name} runs OS commands (system: yes): settings system_commands on "
                        "(or start with --allow-system) first")
    steps = func.steps
    if not steps:
        raise FuncError(f"{func.name} has no steps yet (func add {func.name} 'COMMAND')")
    mode = output or func.output
    if len(_running()) >= MAX_DEPTH:
        raise FuncError(f"{func.name}: funcs nested more than {MAX_DEPTH} deep (does it call itself?)")
    saved = _snapshot(ctx) if isolated else None
    outcome = Outcome()
    start = time.monotonic()
    ctx.positional.append(list(args))
    _running().append(func.name)
    try:
        for number, step in enumerate(steps, 1):
            line, shown = step, mode == "show"
            if step.startswith("@"):
                line, shown = step[1:].lstrip(), True
            elif mode == "quiet" and line.split(maxsplit=1)[0] in SPEAKERS:
                shown = True
            live = ctx.live
            if not shown:
                ctx.live = None  # a quiet step's output must not stream to the screen either
            try:
                result = run_line(line, ctx)
            except ShellExit:
                raise
            except KeyboardInterrupt:  # Ctrl-C: the func stops at this step
                outcome.status, outcome.failed, outcome.failed_line = 130, number, line
                outcome.error = "interrupted (Ctrl-C)"
                break
            except Exception as exc:  # noqa: BLE001 — a failing step: report it and stop
                outcome.status = getattr(exc, "status", 1) or 1
                outcome.failed, outcome.failed_line = number, line
                outcome.error = getattr(exc, "message", None) or str(exc)
                if ctx.emit_error is not None:
                    ctx.emit_error(exc, line)
                break
            finally:
                ctx.live = live
            outcome.steps = number
            value = result.output if result is not None else None
            if shown and value not in (None, ""):
                if ctx.emit is not None:
                    ctx.emit(value)
                else:
                    outcome.collected.append(value)
            if ctx.last_status:
                outcome.status = ctx.last_status
                outcome.failed, outcome.failed_line = number, line
                outcome.error = f"exit status {ctx.last_status}"
                break
    finally:
        ctx.positional.pop()
        _running().pop()
        if saved is not None:
            _restore(ctx, saved)
        outcome.seconds = time.monotonic() - start
    ctx.last_status = outcome.status
    return outcome


def _alias_files(ctx: Any) -> list[Path]:
    from shellcraft import aliases

    files = [aliases.path()]
    own = aliases.profile_path(ctx.config)
    if own is not None:
        files.append(own)
    return files


def _snapshot(ctx: Any) -> dict[str, Any]:
    texts = {}
    for path in _alias_files(ctx):
        try:
            texts[path] = path.read_text(encoding="utf-8")
        except OSError:
            texts[path] = None
    snap = {
        "cwd": os.getcwd(), "variables": dict(ctx.variables), "exported": dict(ctx.exported),
        "env_vars": copy.deepcopy(ctx.env_vars), "environ": dict(os.environ),
        "config": copy.deepcopy(dict(ctx.config)), "aliases": texts, "funcs": dict(ctx.funcs),
        "functions": dict(ctx.functions), "options": set(ctx.options),
    }
    if hasattr(ctx.config, "frozen"):
        snap["frozen"] = ctx.config.frozen
        ctx.config.frozen = True  # nothing is saved while it runs
    return snap


def _restore(ctx: Any, snap: dict[str, Any]) -> None:
    from shellcraft import profiles

    try:
        os.chdir(snap["cwd"])
    except OSError:
        pass
    for name in ("variables", "exported", "funcs", "functions", "options"):
        current = getattr(ctx, name)
        current.clear()
        current.update(snap[name])
    ctx.env_vars.clear()
    ctx.env_vars.update(snap["env_vars"])
    os.environ.clear()
    os.environ.update(snap["environ"])
    for path, text in snap["aliases"].items():
        try:
            if text is None:
                path.unlink(missing_ok=True)
            elif path.read_text(encoding="utf-8") != text:
                path.write_text(text, encoding="utf-8")
        except OSError:
            pass
    disabled_now = set(ctx.config.get("disabled_modules") or [])
    ctx.config.clear()
    ctx.config.update(snap["config"])
    if "frozen" in snap:
        ctx.config.frozen = snap["frozen"]
    profiles.apply_config(ctx, disabled_now)


def preview(func: Func, width: int = 60) -> str:
    """The first steps on one line, cut with … (for `func list`)."""
    text = " ; ".join(" ".join(step.split()) for step in func.steps)
    return text if len(text) <= width else text[: width - 1].rstrip() + "…"


# ── export / import ──────────────────────────────────────────────────────────

BUNDLE = "shellcraft-funcs"


def export_bundle(funcs: list[Func]) -> dict[str, Any]:
    return {"format": BUNDLE, "version": 1, "funcs": {f.name: render(f) for f in funcs}}


def import_bundle(bundle: Any, folder: Path, where: str, overwrite: bool = False) -> list[str]:
    if not isinstance(bundle, dict) or bundle.get("format") != BUNDLE or not isinstance(bundle.get("funcs"), dict):
        raise FuncError("not a ShellCraft funcs export (func export NAME FILE makes one)")
    names = []
    for name, text in bundle["funcs"].items():
        func = parse(str(text), check_name(str(name)))
        func.name = str(name)
        if (folder / f"{func.name}{SUFFIX}").exists() and not overwrite:
            raise FuncError(f"func '{func.name}' exists already (-f replaces it)")
        save(func, folder, where)
        names.append(func.name)
    return names
