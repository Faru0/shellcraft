"""Set up host analysis in one command, and check that it is set up right.

    shellcraft --setup-analyst [HOST ...] [--sudo] [--aggregator ALIAS --root PATH] [--user | --project]
    shellcraft --check-analyst

Setup enables the hosts you name for the AI (`ai = true` in ~/.shellcraft/ssh-hosts.toml), tests that ssh works,
describes an aggregator if you give one, writes ./.mcp.json with the analyst server under the name the agents
expect (`shellcraft`), installs the analyst agents and writes a starter watch plan per host. It only adds:
nothing you wrote is rewritten. The check is read-only and says what to fix.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shellcraft import ai_install, sshcore

MCP_NAME = "shellcraft"  # the agents list their tools as mcp__shellcraft__*, so the server must have this name
AGGREGATORS_FILE = "aggregators.toml"
BASE_CHECKS = ["ps", "ss", "exes", "cron", "authorized-keys", "modules", "logins", "ld-preload", "units",
               "sshd-config", "shell-rc", "suid", "pkg-verify"]
ROOT_CHECKS = ["probes", "sudoers-files"]
DEFAULT_KINDS = {"ps": "ps-*.txt", "netstat": "netstat-*.txt", "ss": "ss-*.txt", "lsof": "lsof-*.txt",
                 "ebpf": "ebpf-*.txt", "memory": "free-*.txt"}
ROOT_RE = re.compile(r"^/[A-Za-z0-9._/@+:~-]{1,199}$")
ALIAS_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,40}$")


class SetupError(Exception):
    """A bad request, shown as one line."""


@dataclass
class Line:
    status: str   # ok, done, skip, info, fix
    text: str

    def render(self) -> str:
        return f"{self.status:<5} {self.text}"


def _home() -> Path:
    from shellcraft.config import config_dir

    return config_dir()


def _hosts_file() -> Path:
    return _home() / sshcore.HOSTS_FILE


def _toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise SetupError(f"{path}: {exc}") from None


def _append(path: Path, text: str, dry_run: bool) -> None:
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    sep = "" if not old or old.endswith("\n\n") else "\n" if old.endswith("\n") else "\n\n"
    path.write_text(old + sep + text, encoding="utf-8")


def _ssh(host: str, script: str, timeout: int = 20) -> sshcore.Outcome:
    return sshcore.execute(sshcore.ssh_argv(host, script), timeout + sshcore.CONNECT_TIMEOUT, 200_000, "setup")


# ── hosts ──────────────────────────────────────────────────────────────────────────────────────

def choose_hosts(names: list[str]) -> list[str]:
    available = sshcore.config_hosts()
    if names:
        return names
    if not available:
        raise SetupError(f"{sshcore.ssh_config_path()} has no explicit Host entries: name the hosts "
                         "(--setup-analyst web01 db01)")
    if not sys.stdin.isatty():
        raise SetupError("name the hosts (--setup-analyst web01 db01); your ssh config has: " + ", ".join(available))
    print("Hosts in your ssh config:")
    for number, name in enumerate(available, 1):
        print(f"  {number:>2}  {name}")
    try:
        answer = input("Which may the AI analyze? Numbers or names, separated by commas (empty: none): ").strip()
    except (EOFError, KeyboardInterrupt):
        print(file=sys.stderr)
        return []
    chosen = []
    for word in (w.strip() for w in answer.split(",") if w.strip()):
        chosen.append(available[int(word) - 1] if word.isdigit() and 0 < int(word) <= len(available) else word)
    return chosen


def enable_host(name: str, sudo_wanted: bool, dry_run: bool) -> tuple[list[Line], bool]:
    """Add `[hosts.NAME]` when it is missing, after testing ssh (and sudo -n when asked). Returns (lines, usable)."""
    lines: list[Line] = []
    if not sshcore.HOST_RE.match(name):
        return [Line("fix", f"{name}: not a host name")], False
    if name not in sshcore.config_hosts():
        return [Line("fix", f"{name}: not a Host in {sshcore.ssh_config_path()}; add it there first")], False
    existing = sshcore.load_hosts().get(name)
    reach = _ssh(name, "echo shellcraft-ok; id -un; uname -sr")
    if reach.status != 0 or b"shellcraft-ok" not in reach.stdout:
        why = reach.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or ["ssh failed"]
        lines.append(Line("fix", f"{name}: cannot log in with ssh (BatchMode, host key already known): {why[0][:160]}"))
        return lines, False
    who = reach.stdout.decode("utf-8", "replace").splitlines()[1:3]
    lines.append(Line("ok", f"{name}: ssh works ({', '.join(who)})"))
    sudo_ok = False
    if sudo_wanted or (existing is not None and existing.priv == "sudo"):
        probe = _ssh(name, "sudo -n true")
        sudo_ok = probe.status == 0
        lines.append(Line("ok", f"{name}: sudo -n works") if sudo_ok else Line(
            "fix", f"{name}: sudo -n needs a password: install the lines from `sshrun --sudoers {name}`"))
    if existing is not None:
        if existing.ai:
            lines.append(Line("skip", f"{name}: already enabled in {sshcore.HOSTS_FILE}"))
        else:
            lines.append(Line("fix", f"{name}: is in {sshcore.HOSTS_FILE} with ai = false; "
                                     "set ai = true yourself if you mean it"))
            return lines, False
        return lines, True
    block = f"[hosts.{name}]\nai = true\n" + ("sudo = true\n" if sudo_wanted and sudo_ok else "")
    _append(_hosts_file(), block, dry_run)
    lines.append(Line("done", f"{name}: enabled for the AI in {sshcore.HOSTS_FILE}"
                              + (" with sudo = true" if sudo_wanted and sudo_ok else "")))
    return lines, True


def starter_plan(name: str, dry_run: bool) -> Line:
    path = _home() / "watchplans" / f"{name}.toml"
    if path.exists():
        return Line("skip", f"watch plan {name}: already there")
    host = sshcore.load_hosts().get(name)
    checks = BASE_CHECKS + (ROOT_CHECKS if host is not None and host.priv != "none" else [])
    text = (f'description = "{name}, written by --setup-analyst"\ninterval_minutes = 15\n\n[[hosts]]\nname = "{name}"\n'
            f"checks = {json.dumps(checks)}\n")
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return Line("done", f"watch plan {name}: written ({len(checks)} checks, every 15 minutes)")


# ── the aggregator ─────────────────────────────────────────────────────────────────────────────

def infer_kinds(names: list[str]) -> dict[str, str]:
    """Kinds and globs from a device folder's file names: `ps-2026-11-07T1930.txt` is kind `ps`, glob `ps-*.txt`."""
    kinds: dict[str, str] = {}
    for name in names:
        match = re.match(r"^([A-Za-z][A-Za-z0-9]*?)([-_.])\d.*?(\.[A-Za-z0-9]{1,6})?$", name)
        if match:
            kinds.setdefault(match.group(1).lower(), f"{match.group(1)}{match.group(2)}*{match.group(3) or ''}")
    return kinds


def setup_aggregator(alias: str, root: str, dry_run: bool) -> tuple[list[Line], list[str]]:
    lines: list[Line] = []
    if not ROOT_RE.match(root) or ".." in root.split("/"):
        return [Line("fix", f"{root}: --root is an absolute path (letters, digits and . _ / @ + : ~ -, no ..)")], []
    host_lines, usable = enable_host(alias, False, dry_run)
    lines += host_lines
    if not usable:
        return lines, []
    probe = _ssh(alias, f"cd {shlex.quote(root)} || exit 4; "
                        "for d in *; do [ -d \"$d\" ] && [ ! -L \"$d\" ] && echo \"$d\"; done | head -n 200")
    if probe.status == 4:
        return lines + [Line("fix", f"{alias}: {root} does not exist on the aggregator")], []
    devices = [d for d in probe.stdout.decode("utf-8", "replace").splitlines() if d]
    lines.append(Line("ok" if devices else "fix", f"{alias}: {len(devices)} device folder(s) under {root}"
                      + (f" ({', '.join(devices[:5])}{' ...' if len(devices) > 5 else ''})" if devices else "")))
    kinds: dict[str, str] = {}
    sample = [d for d in devices if re.match(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$", d)][:5]
    for device in sample:
        listing = _ssh(alias, f"cd {shlex.quote(root)}/{shlex.quote(device)} && ls -1 | head -n 300")
        for kind, glob in infer_kinds(listing.stdout.decode("utf-8", "replace").splitlines()).items():
            kinds.setdefault(kind, glob)
    if devices:
        seen = ", ".join(f"{k} = {g}" for k, g in kinds.items()) or "none recognised, using the defaults"
        lines.append(Line("info", f"kinds seen in {len(sample)} device folder(s): {seen}"))
    path = _home() / AGGREGATORS_FILE
    if alias in (_toml(path).get("aggregators") or {}):
        lines.append(Line("skip", f"aggregator {alias}: already in {AGGREGATORS_FILE}"))
    else:
        files = kinds or DEFAULT_KINDS
        text = (f"[aggregators.{alias}]\nhost = \"{alias}\"\nroot = \"{root}\"\nstale_minutes = 30\n\n"
                f"[aggregators.{alias}.files]\n" + "".join(f'{k} = "{g}"\n' for k, g in files.items()))
        _append(path, text, dry_run)
        lines.append(Line("done", f"aggregator {alias}: written to {AGGREGATORS_FILE} (check the kinds and globs; "
                                  "stale_minutes is 30)"))
    return lines, devices


def aggregator_plan(alias: str, devices: list[str], dry_run: bool) -> Line:
    path = _home() / "watchplans" / f"{alias}-devices.toml"
    if path.exists():
        return Line("skip", f"watch plan {alias}-devices: already there")
    text = f'description = "devices on {alias}, written by --setup-analyst"\ninterval_minutes = 15\n\n' + "".join(
        f'[[devices]]\naggregator = "{alias}"\nname = "{d}"\n\n' for d in devices[:40])
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return Line("done", f"watch plan {alias}-devices: written for {min(len(devices), 40)} device(s)")


# ── the MCP server entry ───────────────────────────────────────────────────────────────────────

def server_entry() -> dict[str, Any]:
    """How Claude Code starts the analyst server: this checkout's main.py, or the `shellcraft` command."""
    main = Path(__file__).resolve().parent.parent / "main.py"
    if main.is_file():
        return {"command": sys.executable, "args": [str(main), "--mcp", "--role", "analyst"]}
    return {"command": shutil.which("shellcraft") or "shellcraft", "args": ["--mcp", "--role", "analyst"]}


def mcp_json(project: Path, force: bool, dry_run: bool) -> Line:
    path = project / ".mcp.json"
    wanted = server_entry()
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError) as exc:
        return Line("fix", f"{path}: not valid JSON ({exc}); fix or remove it, then run this again")
    if not isinstance(data, dict):
        return Line("fix", f"{path}: expected a JSON object")
    servers = data.setdefault("mcpServers", {})
    have = servers.get(MCP_NAME)
    if have == wanted:
        return Line("skip", f"{path.name}: the '{MCP_NAME}' analyst server is already there")
    if have is not None and not force:
        shown = " ".join(str(x) for x in [have.get("command", ""), *have.get("args", [])])[:100]
        return Line("fix", f"{path.name}: has a different '{MCP_NAME}' server ({shown}); "
                           "--force replaces it (the old file is kept as .mcp.json.bak)")
    if have is not None and not dry_run:
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    servers[MCP_NAME] = wanted
    if not dry_run:
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return Line("done", f"{path.name}: '{MCP_NAME}' analyst server written in {project} "
                        "(Claude Code asks you to approve a project server once)")


# ── the two entry points ──────────────────────────────────────────────────────────────────────

def setup(hosts: list[str], *, sudo: bool, aggregator: str | None, root: str | None, target: Path, project: Path,
          force: bool, dry_run: bool) -> list[Line]:
    if aggregator and not ALIAS_RE.match(aggregator.replace(".", "_")):
        raise SetupError(f"{aggregator}: not an aggregator name")
    if bool(aggregator) != bool(root):
        raise SetupError("--aggregator ALIAS and --root PATH go together")
    lines: list[Line] = []
    usable: list[str] = []
    if hosts or not aggregator:
        for name in choose_hosts(hosts):
            host_lines, ok = enable_host(name, sudo, dry_run)
            lines += host_lines
            if ok:
                usable.append(name)
    devices: list[str] = []
    if aggregator and root:
        agg_lines, devices = setup_aggregator(aggregator, root, dry_run)
        lines += agg_lines
    lines.append(mcp_json(project, force, dry_run))
    items = ai_install.select([], team=True)
    results = ai_install.install(items, target, force=force, dry_run=dry_run)
    written = [r for r in results if r.status in ("new", "update", "forced")]
    edited = [r for r in results if r.status == "edited"]
    if written:
        lines.append(Line("done", f"{len(written)} of {len(items)} analyst agent and skill files "
                                  f"{'would be ' if dry_run else ''}written to {target}"))
    elif not edited:
        lines.append(Line("skip", f"the {len(items)} analyst agents and skills in {target} are up to date"))
    for r in edited:
        lines.append(Line("fix", f"{r.rel}: you edited it, so it was left alone (--force replaces it, keeping a .bak)"))
    for name in usable:
        lines.append(starter_plan(name, dry_run))
    if aggregator and devices:
        lines.append(aggregator_plan(aggregator, devices, dry_run))
    if usable:
        ask = f"Use the host-analyst agent on {usable[0]}"
    elif devices:
        ask = f"Check out device {devices[0]}"
    else:
        ask = "Use the host-analyst agent on HOST"
    lines.append(Line("info", f'next: in Claude Code (started in {project}), say "{ask}"; '
                              "run `shellcraft --check-analyst` any time to check the setup"))
    return lines


def doctor(project: Path, target: Path) -> list[Line]:
    """Read-only: everything the analysis needs, one line each, `fix` where something is wrong."""
    lines: list[Line] = []
    try:
        hosts = sshcore.load_hosts()
    except Exception as exc:  # noqa: BLE001 — a bad file is a finding, not a crash
        return [Line("fix", f"{sshcore.HOSTS_FILE}: {exc}")]
    enabled = [h for h in hosts.values() if h.ai]
    if not enabled:
        lines.append(Line("fix", f"no host is enabled: run `shellcraft --setup-analyst HOST` (or add ai = true in "
                                 f"{sshcore.HOSTS_FILE})"))
    in_config = set(sshcore.config_hosts())
    for h in enabled:
        if h.name not in in_config:
            lines.append(Line("fix", f"{h.name}: enabled, but not a Host in {sshcore.ssh_config_path()}"))
            continue
        reach = _ssh(h.name, "echo shellcraft-ok; " + ("sudo -n true && echo sudo-ok" if h.priv == "sudo" else "true"))
        if b"shellcraft-ok" not in reach.stdout:
            lines.append(Line("fix", f"{h.name}: ssh does not work (BatchMode, host key already known)"))
        elif h.priv == "sudo" and b"sudo-ok" not in reach.stdout:
            lines.append(Line("fix", f"{h.name}: sudo = true, but `sudo -n` asks for a password "
                                     f"(see `sshrun --sudoers {h.name}`)"))
        else:
            lines.append(Line("ok", f"{h.name}: ssh works" + (", sudo -n works" if h.priv == "sudo" else "")))
    aggs = (_toml(_home() / AGGREGATORS_FILE).get("aggregators") or {})
    for alias, entry in aggs.items():
        host = str(entry.get("host", ""))
        if not hosts.get(host, sshcore.Host(host)).ai:
            lines.append(Line("fix", f"aggregator {alias}: its host {host} is not enabled "
                                     f"(ai = true in {sshcore.HOSTS_FILE})"))
            continue
        root = str(entry.get("root", ""))
        probe = _ssh(host, f"cd {shlex.quote(root)} && ls -1 | head -n 1000 | wc -l") if ROOT_RE.match(root) else None
        count = probe.stdout.decode().strip() if probe is not None and probe.status == 0 else None
        lines.append(Line("ok", f"aggregator {alias}: {count} entries under {root}") if count is not None
                     else Line("fix", f"aggregator {alias}: cannot read {root} on {host}"))
    mcp = project / ".mcp.json"
    try:
        servers = json.loads(mcp.read_text(encoding="utf-8")).get("mcpServers", {}) if mcp.exists() else {}
    except (OSError, ValueError, AttributeError):
        servers = None
    if servers is None:
        lines.append(Line("fix", f"{mcp}: not valid JSON"))
    elif MCP_NAME in servers:
        args = [str(a) for a in servers[MCP_NAME].get("args", [])]
        ok = "--role" in args and args[args.index("--role") + 1:][:1] == ["analyst"]
        lines.append(Line("ok", f".mcp.json: '{MCP_NAME}' server runs the analyst role") if ok else Line(
            "fix", f".mcp.json: '{MCP_NAME}' server does not use --role analyst "
                   "(the analyst agents would then have a shell they should not have)"))
    else:
        others = [n for n in servers if "shellcraft" in n.lower()]
        lines.append(Line("fix", ".mcp.json: no server named 'shellcraft'" + (
            f" (found {', '.join(others)}: the agents' tools are mcp__shellcraft__*, so the name must be exactly "
            "'shellcraft')" if others else "") + "; run `shellcraft --setup-analyst`"))
    from shellcraft import ai_policy

    lines.append(Line("ok" if "analyst" in ai_policy.load().roles else "fix", "role analyst is known"))
    import importlib.util

    lines.append(Line("ok" if importlib.util.find_spec("mcp_types") else "fix",
                      "the mcp package is installed" if importlib.util.find_spec("mcp_types") else
                      "the mcp package is missing: pip install 'mcp'"))
    stale = [r for r in ai_install.check(ai_install.select([], team=True), target) if r.status != "unchanged"]
    lines.append(Line("ok" if not stale else "fix",
                      f"analyst agents and skills are installed in {target}" if not stale else
                      f"{len(stale)} agent/skill file(s) in {target} are missing or old: "
                      "`shellcraft --install-ai --team`"))
    lines.append(Line("ok" if shutil.which("claude") else "info", "claude is on PATH" if shutil.which("claude") else
                      "claude is not on PATH (only matters if you start Claude Code from here)"))
    return lines


def exit_code(lines: list[Line]) -> int:
    return 1 if any(line.status == "fix" for line in lines) else 0


def project_dir() -> Path:
    return Path(os.getcwd())
