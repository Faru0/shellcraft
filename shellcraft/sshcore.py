"""The ssh machinery shared by sshrun, aggregator and the analyst setup: which hosts are usable, the ssh
command line, and running it with a timeout and an output cap. Nothing here builds a remote command; the
modules do that from fixed templates."""

from __future__ import annotations

import glob
import os
import re
import shlex
import subprocess
import threading
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from shellcraft.modkit import ModuleError, check_cancel
from shellcraft.modkit import redact_secrets as redact

__all__ = ["redact", "HOST_RE", "PATTERN_RE", "MAX_TIMEOUT", "CONNECT_TIMEOUT", "HOSTS_FILE", "Host", "Outcome",
           "ssh_config_path", "config_hosts", "read_toml", "load_hosts", "ssh_argv", "execute"]

HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")
PATTERN_RE = re.compile(r"^/[A-Za-z0-9._/*@+-]{1,150}$")  # a log_paths entry
MAX_TIMEOUT = 120
CONNECT_TIMEOUT = 10
HOSTS_FILE = "ssh-hosts.toml"


def _home() -> Path:
    from shellcraft.config import config_dir

    return config_dir()


def ssh_config_path() -> Path:
    return Path(os.environ.get("SHELLCRAFT_SSH_CONFIG") or Path.home() / ".ssh" / "config")


def config_hosts(path: Path | None = None, _seen: set[Path] | None = None) -> list[str]:
    """Explicit host names in an ssh config (wildcards and negations skipped), Include honoured."""
    path = path or ssh_config_path()
    seen = _seen if _seen is not None else set()
    try:
        real = path.resolve()
        if real in seen:
            return []
        seen.add(real)
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    names: list[str] = []
    for raw in lines:
        line = raw.split("#", 1)[0].strip()
        match = re.match(r"(?i)^(host|include)(?:\s*=\s*|\s+)(.+)$", line)
        if not match:
            continue
        if match.group(1).lower() == "host":
            names += [n for n in shlex.split(match.group(2)) if not re.search(r"[*?!]", n)]
        else:
            for pattern in shlex.split(match.group(2)):
                base = pattern if os.path.isabs(pattern) else str(path.parent / pattern)
                for found in sorted(glob.glob(os.path.expanduser(base))):
                    names += config_hosts(Path(found), seen)
    return list(dict.fromkeys(names))


@dataclass
class Host:
    name: str
    ai: bool = False
    priv: str = "none"            # none | sudo | root
    role: str = ""
    tags: list[str] = field(default_factory=list)
    log_paths: list[str] = field(default_factory=list)


def read_toml(path: Path, who: str = "sshrun") -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ModuleError(f"{who}: {path}: {exc}") from None


def load_hosts() -> dict[str, Host]:
    data = read_toml(_home() / HOSTS_FILE).get("hosts", {})
    hosts: dict[str, Host] = {}
    for name, entry in (data.items() if isinstance(data, dict) else []):
        if not isinstance(entry, dict):
            continue
        priv = "root" if entry.get("root") is True else "sudo" if entry.get("sudo") is True else "none"
        paths = [str(p) for p in entry.get("log_paths", []) if isinstance(p, str)]
        for p in paths:
            if not PATTERN_RE.match(p) or ".." in p.split("/"):
                raise ModuleError(f"sshrun: {HOSTS_FILE}: host {name}: log path '{p}' is not allowed "
                                  "(an absolute path, letters digits . _ / * @ + -, no ..)")
        hosts[str(name)] = Host(str(name), entry.get("ai") is True, priv, str(entry.get("role", "")),
                                [str(t) for t in entry.get("tags", [])], paths)
    return hosts


def ssh_argv(host: str, script: str) -> list[str]:
    ssh = os.environ.get("SHELLCRAFT_SSH") or "ssh"
    return [ssh, "-T", "-n", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={CONNECT_TIMEOUT}",
            "-o", "StrictHostKeyChecking=yes", "-o", "ClearAllForwardings=yes", "-o", "PermitLocalCommand=no",
            "-F", str(ssh_config_path()), "--", host, "sh -c " + shlex.quote(script)]


# ── running it ──────────────────────────────────────────────────────────────

@dataclass
class Outcome:
    stdout: bytes
    stderr: bytes
    status: int
    truncated: bool
    seconds: float


def _pump(stream, sink: bytearray, cap: int, flag: list, proc: subprocess.Popen, kill: bool = True) -> None:
    while chunk := stream.read(8192):
        room = cap - len(sink)
        sink += chunk[:max(room, 0)]
        if len(chunk) > room:
            flag.append(True)
            if kill:  # too much output: stop the command (stderr just stops being kept)
                proc.kill()
                return


def execute(argv: list[str], timeout: float, cap: int, who: str = "sshrun") -> Outcome:
    started = time.monotonic()
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        raise ModuleError(f"{who}: cannot run ssh: {exc}") from None
    out, err = bytearray(), bytearray()
    over: list[bool] = []
    threads = [threading.Thread(target=_pump, args=(proc.stdout, out, cap, over, proc), daemon=True),
               threading.Thread(target=_pump, args=(proc.stderr, err, 8192, [], proc, False), daemon=True)]
    for t in threads:
        t.start()
    try:
        while proc.poll() is None:
            check_cancel()
            if time.monotonic() - started > timeout:
                proc.kill()
                raise ModuleError(f"{who}: no answer within {int(timeout)} s (the host or the check is too slow)")
            time.sleep(0.05)
    finally:
        if proc.poll() is None:
            proc.kill()
        for t in threads:
            t.join(2)
        proc.wait()
    return Outcome(bytes(out), bytes(err), proc.returncode, bool(over), time.monotonic() - started)
