"""health — offline checks of system-health text (free, uptime, vmstat, df, top, dmesg) for stable, safe systems."""

from __future__ import annotations

import json
import math
import re
import tomllib
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from posixpath import basename

from shellcraft.config import config_dir
from shellcraft.modkit import ArgParser, ModuleError

SUMMARY = "Check free/uptime/vmstat/df/top/dmesg text for memory, load, disk and kernel trouble (offline)"

SUBCOMMANDS = ("mem", "load", "disk", "top", "oom", "all")
OUTPUTS = ("table", "json")
SEVERITIES = ("info", "low", "medium", "high", "critical")
RANK = {s: i for i, s in enumerate(SEVERITIES)}

# Every rule health can raise. The man page lists them all (a test keeps the two in sync).
RULES = [
    {"id": "mem_low", "severity": "medium", "sub": "mem, top, all",
     "description": "Available memory is under 10% of the total (high under half of that, 5%)."},
    {"id": "swap_high", "severity": "low", "sub": "mem, top, all",
     "description": "More than 50% of swap is in use (medium above 80%)."},
    {"id": "swap_thrashing", "severity": "medium", "sub": "load, all",
     "description": "vmstat shows heavy swap traffic: si+so of 1000 KB/s or more in half the samples (high when the "
                    "average is 10000 KB/s or more)."},
    {"id": "load_high", "severity": "low", "sub": "load, all",
     "description": "The 1 or 15 minute load average is over 1.5 per CPU (medium over 3 per CPU)."},
    {"id": "iowait_high", "severity": "medium", "sub": "load, top, all",
     "description": "CPUs wait on disk more than 30% of the time (high above 60%); from vmstat wa or top's %Cpu line."},
    {"id": "steal_high", "severity": "medium", "sub": "load, top, all",
     "description": "A virtual machine loses more than 20% of CPU time to its host (high above 40%)."},
    {"id": "disk_full", "severity": "low", "sub": "disk, all",
     "description": "A filesystem is 90% full or more (high at 95%, critical at 99%). Read-only squashfs, loop, "
                    "iso9660 and snap mounts at 100% are ignored; tmpfs and other pseudo filesystems count only when "
                    "full."},
    {"id": "inode_full", "severity": "medium", "sub": "disk, all",
     "description": "A filesystem has used 90% or more of its inodes (high at 99%)."},
    {"id": "oom_kill", "severity": "high", "sub": "oom, all",
     "description": "The kernel (or systemd-oomd) killed a process for lack of memory (critical when it was sshd, "
                    "cloudflared, nginx, dockerd, postgres, mysqld or another important service)."},
    {"id": "kernel_errors", "severity": "medium", "sub": "oom, all",
     "description": "Kernel log trouble: I/O errors and filesystem errors (high); segfaults, BUG/Oops, soft lockups, "
                    "hung tasks and call traces (medium; a call trace next to other findings is low)."},
    {"id": "proc_mem_hog", "severity": "medium", "sub": "top, all",
     "description": "One process uses more than 40% of memory (high above 70%)."},
    {"id": "proc_cpu_hog", "severity": "info", "sub": "top, all",
     "description": "One process uses more than 90% of a CPU (low at 200% or more, two full cores)."},
    {"id": "zombies", "severity": "low", "sub": "top, all",
     "description": "10 or more zombie processes (medium at 100 or more)."},
    {"id": "recent_reboot", "severity": "info", "sub": "load, all",
     "description": "The machine has been up less than an hour."},
]

IMPORTANT = ("sshd", "cloudflared", "nginx", "dockerd", "containerd", "postgres", "mysqld", "mariadbd", "apache2",
             "httpd", "haproxy", "redis-server", "kubelet", "systemd", "unbound", "etcd")
DEFAULT_LIMIT = 200
RULES_FILE = "health-rules.toml"
MAX_NAMES = 8  # distinct killed processes shown before the rest are rolled up

# key -> (default, minimum, maximum). Numbers only; the list keys are handled separately.
NUMBER_KEYS = {
    "mem_low_pct": (10, 1, 50), "swap_high_pct": (50, 1, 99), "swap_very_high_pct": (80, 1, 100),
    "swap_io_kb": (1000, 1, 10**9), "load_factor": (1.5, 0.1, 100), "load_very_high_factor": (3.0, 0.1, 1000),
    "iowait_pct": (30, 1, 100), "steal_pct": (20, 1, 100),
    "disk_full_pct": (90, 1, 100), "disk_high_pct": (95, 1, 100), "disk_critical_pct": (99, 1, 100),
    "inode_full_pct": (90, 1, 100), "proc_mem_pct": (40, 1, 100), "proc_mem_high_pct": (70, 1, 100),
    "proc_cpu_pct": (90, 1, 10000), "zombies": (10, 1, 10**6), "zombies_many": (100, 1, 10**6),
    "reboot_minutes": (60, 1, 10**6),
}
ORDERED = (("swap_high_pct", "swap_very_high_pct"), ("load_factor", "load_very_high_factor"),
           ("disk_full_pct", "disk_high_pct"), ("disk_high_pct", "disk_critical_pct"),
           ("proc_mem_pct", "proc_mem_high_pct"), ("zombies", "zombies_many"))
LIST_KEYS = ("ignore_mounts", "ignore_processes", "important_processes", "ignore_rules")

NOISE_TYPES = {"tmpfs", "devtmpfs", "overlay", "squashfs", "efivarfs", "ramfs", "devpts", "proc", "sysfs", "cgroup",
               "cgroup2", "shm", "none", "udev", "erofs", "iso9660", "fuse.portal", "fuse.gvfsd-fuse"}
READONLY_TYPES = {"squashfs", "iso9660", "erofs"}
NOISE_MOUNTS = ("/dev", "/run/", "/sys/", "/proc/", "/snap/", "/var/lib/snapd/")
MARKERS = {"inodes", "loadavg", "cpus", "nproc", "meminfo", "uptime", "vmstat", "free", "df", "top", "ps", "dmesg",
           "journal", "oom", "mem", "load", "disk", "kernel", "lscpu", "cpuinfo"}

_UPTIME = re.compile(r"\bup\s+(?P<up>.+?),\s+(?:(?P<users>\d+)\s+users?,\s+)?load averages?:\s*(?P<rest>.*)$")
_LOADAVG = re.compile(r"^(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+\.\d+)\s+(\d+)/(\d+)\s+\d+\s*$")
_MEMINFO = re.compile(r"^(MemTotal|MemFree|MemAvailable|Buffers|Cached|SwapTotal|SwapFree|Committed_AS|Dirty):\s+(\d+)")
_SIZE = re.compile(r"^(\d+(?:[.,]\d+)?)\s*([KMGTP]?)(?:i?B?)$", re.I)
_NUM = r"(\d+(?:[.,]\d+)?)"
_TOP_MEM = re.compile(_NUM + r"\s*([kKmMgG])?\s*(total|free|used|buff/cache|buffers|cached|avail Mem)")
_TOP_CPU = re.compile(_NUM + r"\s*%?\s*(us|sy|ni|id|wa|hi|si|st)\b")
_DF_ROW = re.compile(r"^(?P<head>.*?)\s(?P<pct>\d+%|-)\s+(?P<mount>/.*)$")
_DMESG_TS = re.compile(r"^\[\s*\d+\.\d+\]")
_AGG_HEAD = re.compile(r"^# aggregator \S+ (\S+) \S+ \(")
_SSHRUN_HEAD = re.compile(r"^# sshrun (\S+) \S+ \(")
_KILLED = re.compile(r"\b[Kk]ill(?:ed)? process (\d+) \((.{1,64}?)\)")
_OOM_TASK = re.compile(r"\boom-kill:.*?\btask=([^,\s]+),pid=(\d+)")
_REAPED = re.compile(r"\boom_reaper: reaped process (\d+) \((.{1,64}?)\)")
_OOMD = re.compile(r"systemd-oomd\[\d+\]: Killed (\S+)")
_INVOKED = re.compile(r"(\S+) invoked oom-killer")
_OOM_ANY = re.compile(r"out of memory|oom-kill|oom-killer|oom_reaper|systemd-oomd", re.I)

# (flag subject, severity, regex): the first match wins for a line. Matched against untrusted text, never executed.
KERNEL_CATEGORIES = [
    ("disk I/O errors", "high", re.compile(r"I/O error|blk_update_request|critical medium error|Buffer I/O", re.I)),
    ("filesystem errors", "high", re.compile(
        r"\b(?:ext[234]-fs|xfs|btrfs|f2fs|ntfs3?|jbd2)\b.*\b(?:error|corrupt\w*)\b|\bxfs_\w+.*\berror\b"
        r"|Remounting filesystem read-only|filesystem corruption", re.I)),
    ("segfaults", "medium", re.compile(r"\bsegfault at\b|\btraps: .* general protection", re.I)),
    ("soft lockups", "medium", re.compile(r"soft lockup|hard LOCKUP|rcu[_\w]* .*stall", re.I)),
    ("kernel BUG or Oops", "medium", re.compile(r"\bBUG:|kernel BUG at|\bOops\b|general protection fault")),
    ("hung tasks", "medium", re.compile(r"hung_task|blocked for more than \d+ seconds", re.I)),
    ("call traces", "medium", re.compile(r"\bCall Trace\b")),
]


# ── entry point ──────────────────────────────────────────────────────────────

def run(args: list[str], stdin: str) -> str:
    parser = ArgParser("health")
    parser.add_argument("subcommand", choices=SUBCOMMANDS)
    parser.add_argument("-o", "--output", default="table", choices=OUTPUTS)
    parser.add_argument("--cpus", type=int, metavar="N")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, metavar="N")
    opts = parser.parse_args(args)
    if opts.limit < 1:
        raise ModuleError("health: --limit must be 1 or more")
    if opts.cpus is not None and opts.cpus < 1:
        raise ModuleError("health: --cpus must be 1 or more")
    if not stdin.strip():
        raise ModuleError(f"health {opts.subcommand}: no input on stdin; pipe in {_EXPECTED[opts.subcommand]}")

    cfg = _load_rules()
    groups = _group(stdin)
    if not groups and opts.subcommand == "oom":
        groups = {"": Facts()}  # sshrun's oom check prints only a header when the kernel log was clean
    hosts = groups
    wanted = {"mem": ("mem",), "load": ("load",), "disk": ("disk",), "top": ("top",), "oom": ("oom",),
              "all": ("mem", "load", "disk", "top", "oom")}[opts.subcommand]
    if opts.subcommand != "oom" and not any(_has(f, w) for f in hosts.values() for w in wanted):
        raise ModuleError(f"health {opts.subcommand}: nothing recognised; expected {_EXPECTED[opts.subcommand]}")
    if opts.subcommand == "oom" and any(f.text_lines and not f.kernel for f in hosts.values()):
        raise ModuleError(f"health oom: none of the input lines look like kernel log text; expected "
                          f"{_EXPECTED['oom']}")

    rows: list[dict] = []
    flags: list[dict] = []
    notes: list[str] = []
    for label, f in hosts.items():
        sub_rows, sub_flags, sub_notes = _analyse(label, f, wanted, opts.subcommand, cfg, opts.cpus)
        rows += sub_rows
        flags += sub_flags
        notes += sub_notes
    flags = [x for x in _dedupe(flags) if x["rule"] not in cfg["ignore_rules"]]
    flags.sort(key=lambda x: (-RANK[x["severity"]], x["rule"], x["subject"], x["reason"]))

    shown = rows[: opts.limit]
    hidden = len(rows) - len(shown)
    if opts.output == "json":
        out: dict = {"rows": shown, "flags": flags}
        if hidden:
            out["truncated"] = hidden
        if notes:
            out["notes"] = notes
        return json.dumps(out, indent=2, ensure_ascii=False) + "\n"

    lines: list[str] = []
    last = None
    for r in shown:
        if r["section"] and r["section"] != last:
            lines.append(f"== {r['section']} ==")
        last = r["section"]
        lines.append(r["summary"])
    if hidden:
        lines.append(f"... {hidden} more row(s), raise --limit")
    lines.append("")
    lines += [f"note: {n}" for n in notes]
    lines += [f"FLAG {x['severity']} {x['rule']} {x['subject']}: {x['reason']}" for x in flags]
    lines.append(f"{len(flags)} flag(s)" if flags else "no flags")
    return "\n".join(lines) + "\n"


_EXPECTED = {
    "mem": "free, free -m/-h or /proc/meminfo output (or the Mem and Swap lines of top)",
    "load": "uptime, /proc/loadavg or vmstat output",
    "disk": "df -h, df -P or df -i output",
    "top": "top -b -n1 output or a ps aux table (PID, %CPU, %MEM, COMMAND)",
    "oom": "dmesg or journalctl -k text",
    "all": "the output of free, /proc/meminfo, uptime, /proc/loadavg, vmstat, df, top, ps aux or dmesg/journalctl -k",
}


# ── small helpers ────────────────────────────────────────────────────────────

def _clean(text: str, width: int = 60) -> str:
    """Untrusted text made safe to print: control characters become '?', long text is cut."""
    text = "".join(ch if ch.isprintable() else "?" for ch in text)
    return text if len(text) <= width else text[: width - 1] + "…"


def _dedupe(flags: list[dict]) -> list[dict]:
    seen, out = set(), []
    for f in flags:
        key = (f["rule"], f["subject"])
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def _num(text: str) -> float:
    return float(text.replace(",", "."))


def _gib(kib: float) -> str:
    return f"{kib / 1048576:.1f}"


def _pair(used_kib: float, total_kib: float) -> str:
    """'3.1 of 7.8 GiB' (MiB when the total is under 1 GiB)."""
    unit, div = ("GiB", 1048576) if total_kib >= 1048576 else ("MiB", 1024)
    first = "0" if used_kib <= 0 else f"{used_kib / div:.1f}"
    return f"{first} of {total_kib / div:.1f} {unit}"


def _flag(rule: str, severity: str, subject: str, reason: str) -> dict:
    return {"severity": severity, "rule": rule, "subject": subject, "reason": reason}


def _proc_name(cmd: str) -> str:
    cmd = cmd.strip()
    if cmd.startswith("["):
        m = re.match(r"\[[^\]]*\]", cmd)
        return m.group(0) if m else cmd.split()[0]
    first = cmd.split()[0] if cmd else ""
    return basename(first).rstrip(":").lstrip("-")


# ── user rules file ──────────────────────────────────────────────────────────

def _load_rules() -> dict:
    cfg: dict = {k: v[0] for k, v in NUMBER_KEYS.items()}
    cfg.update({k: [] for k in LIST_KEYS})
    path = config_dir() / RULES_FILE
    if not path.is_file():
        return cfg
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ModuleError(f"health: {path}: {' '.join(str(exc).split())}") from None
    known = set(NUMBER_KEYS) | set(LIST_KEYS)
    for key, value in data.items():
        if key not in known:
            raise ModuleError(f"health: {path}: unknown key '{_clean(str(key), 40)}' "
                              f"(valid keys: {', '.join(sorted(known))})")
        if key in NUMBER_KEYS:
            _, low, high = NUMBER_KEYS[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
                raise ModuleError(f"health: {path}: {key} must be a number from {low} to {high}")
            cfg[key] = value
        else:
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ModuleError(f"health: {path}: {key} must be a list of strings")
            if key == "ignore_rules":
                bad = [v for v in value if v not in {r["id"] for r in RULES}]
                if bad:
                    raise ModuleError(f"health: {path}: ignore_rules names no rule: {', '.join(bad)} "
                                      "(`man health` lists the rule ids)")
            cfg[key] = list(value)
    for low_key, high_key in ORDERED:
        if cfg[low_key] > cfg[high_key]:
            raise ModuleError(f"health: {path}: {low_key} ({cfg[low_key]}) must not be above {high_key} "
                              f"({cfg[high_key]})")
    return cfg


# ── parsing ──────────────────────────────────────────────────────────────────

@dataclass
class Facts:
    """Everything found in one host's text. Missing parts stay None or empty."""
    mem: dict | None = None            # total, avail, swap_total, swap_free, committed, dirty (KiB)
    topmem: dict | None = None
    load: tuple[float, float, float] | None = None
    up_seconds: int | None = None
    users: int | None = None
    cpus: int | None = None
    vm_runs: list[list[dict]] = field(default_factory=list)
    disks: list[dict] = field(default_factory=list)
    inodes: list[dict] = field(default_factory=list)
    tasks: int | None = None
    zombies: int | None = None
    cpu_line: dict | None = None       # us sy id wa st (percent)
    procs: list[dict] = field(default_factory=list)
    kernel: list[str] = field(default_factory=list)
    text_lines: int = 0
    _scale: int = 1                    # KiB per plain number in free output (1 or 1024)


def _group(text: str) -> dict[str, Facts]:
    """Split the text into hosts (by `# aggregator ... DEVICE KIND` and `# sshrun HOST CHECK` headers, or `## name`
    markers) and parse each. Lines of a comment-only input leave no group."""
    chunks: dict[str, list[tuple[str, str]]] = {}
    label, marker, header_seen = "", "", False
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s == "#":
            continue
        if s.startswith("# "):
            m = _AGG_HEAD.match(s) or _SSHRUN_HEAD.match(s)
            if m:
                label, header_seen, marker = _clean(m.group(1), 40), True, ""
            continue
        if s == "##" or s.startswith("## "):
            parts = s[2:].split()
            marker = parts[0].lower() if parts else ""
            if marker and marker not in MARKERS and not header_seen:
                label = _clean(parts[0], 40)
            continue
        chunks.setdefault(label, []).append((marker, raw[:400]))
    return {lab: _parse(lines) for lab, lines in chunks.items()}


def _kib(token: str) -> tuple[float, bool] | None:
    """A free-style size: ('30Gi' -> KiB, True) or a plain number (n, False)."""
    m = _SIZE.match(token)
    if not m:
        return None
    value = _num(m.group(1))
    unit = m.group(2).upper()
    if not unit:
        return value, False
    return value * 1024 ** "KMGTP".index(unit), True


def _parse(lines: list[tuple[str, str]]) -> Facts:
    f = Facts()
    df_mode: str | None = None
    df_type = False
    pending = ""
    proc_cols: list[str] | None = None
    vm_cols: list[str] | None = None
    free_cols = ["total", "used", "free", "shared", "buff/cache", "available"]
    last_marker = None
    for marker, raw in lines:
        if marker != last_marker:  # a new `## section`: table state does not carry over
            df_mode, proc_cols, vm_cols, pending = None, None, None, ""
            last_marker = marker
        f.text_lines += 1
        line = raw.strip()
        if re.match(r"^Filesystem\b.*\bMounted\b", line):
            df_mode = "inode" if re.search(r"\bInodes\b|\bIUse%", line) else "block"
            df_type = bool(re.search(r"\bType\b", line))
            pending = ""
            continue
        if df_mode:
            row = _df_row(pending + " " + line if pending else line, df_type)
            if row:
                row["mode"] = df_mode
                (f.inodes if df_mode == "inode" else f.disks).append(row)
                pending = ""
                continue
            if len(line.split()) == 1 and not pending:
                pending = line  # a long device name wrapped onto its own line
                continue
            df_mode, pending = None, ""
        tokens = line.split()
        upper = {t.upper() for t in tokens}
        if "PID" in upper and "%MEM" in upper and ("COMMAND" in upper or "CMD" in upper):
            proc_cols = [t.upper() for t in tokens]
            continue
        if proc_cols:
            row = _proc_row(line, proc_cols)
            if row:
                f.procs.append(row)
                continue
            proc_cols = None
        if "swpd" in tokens and "si" in tokens and "so" in tokens and tokens[0] == "r":
            vm_cols = tokens
            f.vm_runs.append([])
            continue
        if vm_cols and len(tokens) == len(vm_cols) and all(re.fullmatch(r"-?\d+", t) for t in tokens):
            f.vm_runs[-1].append(dict(zip(vm_cols, map(int, tokens))))
            continue
        if _top_line(f, line):
            continue
        if tokens[:1] == ["total"] and "used" in tokens:
            free_cols = [t.lower() for t in tokens]
            continue
        if _free_line(f, line, tokens, free_cols):
            continue
        m = _MEMINFO.match(line)
        if m:
            _meminfo(f, m.group(1), int(m.group(2)))
            continue
        m = _LOADAVG.match(line)
        if m and f.load is None:
            f.load = (float(m.group(1)), float(m.group(2)), float(m.group(3)))
            continue
        m = _UPTIME.search(line)
        if m:
            _uptime(f, m)
            continue
        if _cpus_line(f, line, marker):
            continue
        if _kernel_like(line):
            f.kernel.append(line)
    if f.mem is not None:
        f.mem.setdefault("swap_total", 0)
    return f


def _df_row(line: str, has_type: bool) -> dict | None:
    m = _DF_ROW.match(line.strip())
    if not m:
        return None
    head = m.group("head").split()
    if not head or (has_type and len(head) < 2):
        return None
    pct = None if m.group("pct") == "-" else int(m.group("pct")[:-1])
    return {"fs": _clean(head[0], 60), "type": head[1] if has_type else "", "mount": _clean(m.group("mount"), 80),
            "pct": pct}


def _proc_row(line: str, cols: list[str]) -> dict | None:
    parts = line.split(None, len(cols) - 1)
    if len(parts) != len(cols) or not parts[cols.index("PID")].isdigit():
        return None
    cmd_col = cols.index("COMMAND") if "COMMAND" in cols else cols.index("CMD")
    try:
        mem = _num(parts[cols.index("%MEM")])
        cpu = _num(parts[cols.index("%CPU")]) if "%CPU" in cols else 0.0
    except ValueError:
        return None
    state = parts[cols.index("STAT")] if "STAT" in cols else parts[cols.index("S")] if "S" in cols else ""
    cmd = parts[cmd_col]
    return {"pid": int(parts[cols.index("PID")]), "name": _clean(_proc_name(cmd), 40), "mem": mem, "cpu": cpu,
            "zombie": state.startswith("Z") or "<defunct>" in cmd, "cmd": _clean(cmd, 60)}


def _top_line(f: Facts, line: str) -> bool:
    m = re.match(r"^Tasks:\s*(\d+)\s+total", line)
    if m:
        f.tasks = int(m.group(1))
        z = re.search(r"(\d+)\s+zombie", line)
        f.zombies = int(z.group(1)) if z else 0
        return True
    if re.match(r"^%?Cpu\(s\):", line):
        f.cpu_line = {unit: _num(v) for v, unit in _TOP_CPU.findall(line)}
        return True
    m = re.match(r"^(?:(Ki|Mi|Gi|Ti)B\s+)?(Mem|Swap)\s*:\s*(.*)$", line)
    if m and re.search(r"\btotal\b", m.group(3)):
        prefix = {"Ki": 1, "Mi": 1024, "Gi": 1048576, "Ti": 1073741824}.get(m.group(1) or "Ki", 1)
        vals: dict[str, float] = {}
        for v, suffix, name in _TOP_MEM.findall(m.group(3)):
            mult = {"k": 1, "m": 1024, "g": 1048576}.get(suffix.lower(), prefix) if suffix else prefix
            vals[name] = _num(v) * mult
        tm = f.topmem = f.topmem or {}
        if m.group(2) == "Mem":
            tm["total"] = vals.get("total", 0.0)
            tm["avail"] = vals.get("avail Mem", vals.get("free", 0.0) + vals.get("buff/cache", 0.0)
                                   + vals.get("buffers", 0.0) + vals.get("cached", 0.0))
        else:
            tm["swap_total"] = vals.get("total", 0.0)
            tm["swap_free"] = vals.get("free", 0.0)
            if "avail Mem" in vals:
                tm["avail"] = vals["avail Mem"]
        return True
    m = re.match(r"^top - ", line)
    if m:
        u = _UPTIME.search(line)
        if u:
            _uptime(f, u)
        return True
    return False


def _free_line(f: Facts, line: str, tokens: list[str], cols: list[str]) -> bool:
    if tokens[:1] == ["-/+"] and f.mem is not None and len(tokens) >= 4:
        v = _kib(tokens[-1])
        if v:
            f.mem["avail"] = v[0] * (1 if v[1] else f._scale)
        return True
    if tokens[:1] not in (["Mem:"], ["Swap:"]) or re.search(r"\btotal\b", line):
        return False
    vals = [_kib(t) for t in tokens[1:]]
    if not vals or any(v is None for v in vals):
        return False
    if tokens[0] == "Mem:":
        total = vals[0]
        plain = not total[1]
        f._scale = 1 if (plain and total[0] >= 4_000_000) else 1024
        by = {c: v[0] * (1 if v[1] else f._scale) for c, v in zip(cols, vals)}
        avail = by.get("available")
        if avail is None:
            avail = by.get("free", 0.0) + by.get("buff/cache", 0.0) + by.get("buffers", 0.0) + by.get("cache", 0.0)
            avail += by.get("cached", 0.0)
        f.mem = {**(f.mem or {}), "total": by.get("total", 0.0), "avail": avail}
        if "free" in by:
            f.mem["free"] = by["free"]
    else:
        f.mem = f.mem if f.mem is not None else {}
        f.mem["swap_total"] = vals[0][0] * (1 if vals[0][1] else f._scale)
        if len(vals) > 2:
            f.mem["swap_free"] = vals[2][0] * (1 if vals[2][1] else f._scale)
        elif len(vals) > 1:
            f.mem["swap_free"] = f.mem["swap_total"] - vals[1][0] * (1 if vals[1][1] else f._scale)
    return True


def _meminfo(f: Facts, key: str, kib: int) -> None:
    mi = f.mem if f.mem is not None else {}
    f.mem = mi
    mi["_" + key] = kib
    if key == "MemTotal":
        mi["total"] = kib
    elif key == "MemAvailable":
        mi["avail"] = kib
    elif key == "SwapTotal":
        mi["swap_total"] = kib
    elif key == "SwapFree":
        mi["swap_free"] = kib
    elif key == "Committed_AS":
        mi["committed"] = kib
    elif key == "Dirty":
        mi["dirty"] = kib
    if "avail" not in mi and {"_MemFree", "_Buffers", "_Cached"} <= mi.keys():
        mi["avail_est"] = mi["_MemFree"] + mi["_Buffers"] + mi["_Cached"]


def _uptime(f: Facts, m: re.Match) -> None:
    rest = m.group("rest")
    nums = re.findall(r"\d+\.\d+", rest)
    if len(nums) < 3:
        nums = [n.replace(",", ".") for n in re.findall(r"\d+,\d+", rest)]
    if len(nums) >= 3:
        f.load = (float(nums[0]), float(nums[1]), float(nums[2]))
    up = m.group("up")
    secs = 0
    for pat, mult in ((r"(\d+)\s+days?", 86400), (r"(\d+)\s+(?:hrs?|hours?)", 3600), (r"(\d+)\s+min", 60)):
        d = re.search(pat, up)
        if d:
            secs += int(d.group(1)) * mult
    hm = re.search(r"\b(\d+):(\d\d)\b", up)
    if hm:
        secs += int(hm.group(1)) * 3600 + int(hm.group(2)) * 60
    f.up_seconds = secs
    if m.group("users"):
        f.users = int(m.group("users"))


def _cpus_line(f: Facts, line: str, marker: str) -> bool:
    if marker in ("cpus", "nproc") and re.fullmatch(r"\d{1,5}", line):
        f.cpus = int(line)
        return True
    m = re.match(r"^CPU\(s\):\s+(\d+)$", line)
    if m:
        f.cpus = int(m.group(1))
        return True
    if re.match(r"^processor\s*:\s*\d+$", line):
        f.cpus = (f.cpus or 0) + 1  # /proc/cpuinfo: one block per logical CPU
        return True
    return False


def _kernel_like(line: str) -> bool:
    if _DMESG_TS.match(line) or " kernel: " in line or _OOM_ANY.search(line) or _KILLED.search(line):
        return True
    return any(rx.search(line) for _, _, rx in KERNEL_CATEGORIES)


# ── analysis ─────────────────────────────────────────────────────────────────

def _has(f: Facts, group: str) -> bool:
    return {"mem": bool(f.mem or f.topmem), "load": bool(f.load or f.vm_runs or f.up_seconds is not None),
            "disk": bool(f.disks or f.inodes), "top": bool(f.procs or f.tasks is not None or f.cpu_line or f.topmem),
            "oom": bool(f.kernel)}[group]


def _analyse(label: str, f: Facts, wanted: tuple[str, ...], sub: str, cfg: dict, cpus_opt: int | None):
    rows: list[dict] = []
    flags: list[dict] = []
    notes: list[str] = []

    def row(kind: str, summary: str, **values) -> None:
        rows.append({"section": label, "kind": kind, "summary": summary, "values": values})

    def add(rule: str, severity: str, what: str, reason: str) -> None:
        flags.append(_flag(rule, severity, f"{label} {what}" if label else what, reason))

    if "mem" in wanted or "top" in wanted:
        mem = (f.mem or f.topmem) if "mem" in wanted else f.topmem
        if mem and mem.get("total"):
            _mem_checks(mem, row, add, cfg)
    if "load" in wanted:
        cpus = cpus_opt or f.cpus
        if cpus is None and (f.load is not None):
            cpus = 1
            notes.append(f"{label + ': ' if label else ''}cpu count unknown, assumed 1 (pass --cpus N or add nproc "
                         "output)")
        _load_checks(f, cpus, row, add, cfg)
    if "load" in wanted or "top" in wanted:
        _cpu_time_checks(f, wanted, row, add, cfg)
    if "disk" in wanted:
        _disk_checks(f, row, add, cfg)
    if "top" in wanted:
        _top_checks(f, row, add, cfg)
    if "oom" in wanted and (f.kernel or sub == "oom"):
        _kernel_checks(f, row, add, cfg)
    return rows, flags, notes


def _mem_checks(mem: dict, row, add, cfg: dict) -> None:
    total = mem["total"]
    avail = mem.get("avail", mem.get("avail_est"))
    if avail is None:
        row("memory", f"memory: {_pair(0, total)} total, no available figure to judge by", total_kib=int(total))
        return
    pct = 100 * avail / total
    text = f"memory: {_pair(avail, total)} available ({pct:.0f}%)"
    values = {"total_kib": int(total), "available_kib": int(avail), "available_pct": round(pct, 1)}
    swap = mem.get("swap_total", 0)
    if swap:
        used = swap - mem.get("swap_free", swap)
        spct = 100 * used / swap
        text += f", swap {_pair(used, swap)} used ({spct:.0f}%)"
        values.update(swap_total_kib=int(swap), swap_used_kib=int(used), swap_used_pct=round(spct, 1))
    else:
        text += ", no swap"
    if mem.get("committed"):
        text += f", committed {_gib(mem['committed'])} GiB"
    row("memory", text, **values)
    low = cfg["mem_low_pct"]
    if pct < low:
        sev = "high" if pct < low / 2 else "medium"
        add("mem_low", sev, "memory", f"only {pct:.1f}% available ({_pair(avail, total)})")
    if swap:
        if spct > cfg["swap_very_high_pct"]:
            add("swap_high", "medium", "swap", f"{spct:.0f}% of swap in use ({_pair(used, swap)})")
        elif spct > cfg["swap_high_pct"]:
            add("swap_high", "low", "swap", f"{spct:.0f}% of swap in use ({_pair(used, swap)})")


def _vm_samples(f: Facts) -> list[dict]:
    out: list[dict] = []
    for run_ in f.vm_runs:
        out += run_[1:] if len(run_) > 1 else run_  # the first vmstat row is the average since boot
    return out


def _load_checks(f: Facts, cpus: int | None, row, add, cfg: dict) -> None:
    if f.load is not None:
        l1, l5, l15 = f.load
        text = f"load: {l1:.2f} {l5:.2f} {l15:.2f}" + (f" on {cpus} cpu{'s' if cpus != 1 else ''}" if cpus else "")
        if f.up_seconds is not None:
            text += f", up {_span(f.up_seconds)}"
        if f.users is not None:
            text += f", {f.users} user{'s' if f.users != 1 else ''}"
        row("load", text, load1=l1, load5=l5, load15=l15, cpus=cpus, up_seconds=f.up_seconds)
        if cpus:
            reasons, sev = [], None
            for name, val in (("1 minute", l1), ("15 minute", l15)):
                if val > cpus * cfg["load_very_high_factor"]:
                    reasons.append(f"{name} load {val:.2f} is over {cfg['load_very_high_factor']:g} per cpu")
                    sev = "medium"
                elif val > cpus * cfg["load_factor"]:
                    reasons.append(f"{name} load {val:.2f} is over {cfg['load_factor']:g} per cpu")
                    sev = sev or "low"
            if reasons:
                add("load_high", sev, "load average", f"{'; '.join(reasons)} on {cpus} cpu{'s' if cpus != 1 else ''}")
    elif f.up_seconds is not None:
        row("uptime", f"uptime: up {_span(f.up_seconds)}", up_seconds=f.up_seconds)
    if f.up_seconds is not None and f.up_seconds < cfg["reboot_minutes"] * 60:
        add("recent_reboot", "info", "uptime", f"up only {_span(f.up_seconds)}: the machine restarted recently")
    samples = _vm_samples(f)
    if samples:
        n = len(samples)
        si = sum(s.get("si", 0) for s in samples) / n
        so = sum(s.get("so", 0) for s in samples) / n
        wa = sum(s.get("wa", 0) for s in samples) / n
        st = sum(s.get("st", 0) for s in samples) / n
        r = sum(s.get("r", 0) for s in samples) / n
        row("vmstat", f"vmstat: {n} sample(s), run queue {r:.1f}, swap in/out {si:.0f}/{so:.0f} KB/s, "
                      f"iowait {wa:.0f}%, steal {st:.0f}%", samples=n, si=si, so=so, wa=wa, st=st, run_queue=r)
        big = sum(1 for s in samples if s.get("si", 0) + s.get("so", 0) >= cfg["swap_io_kb"])
        if big >= max(1, math.ceil(n / 2)):
            sev = "high" if si + so >= cfg["swap_io_kb"] * 10 else "medium"
            add("swap_thrashing", sev, "swap", f"swap traffic averages {si:.0f} KB/s in and {so:.0f} KB/s out over "
                                              f"{n} vmstat sample(s)")


def _span(seconds: int) -> str:
    if seconds >= 86400:
        d = seconds // 86400
        return f"{d} day{'s' if d != 1 else ''}"
    if seconds >= 3600:
        return f"{seconds // 3600} hour(s)"
    return f"{seconds // 60} minute(s)"


def _cpu_time_checks(f: Facts, wanted: tuple[str, ...], row, add, cfg: dict) -> None:
    wa: list[tuple[float, str]] = []
    st: list[tuple[float, str]] = []
    samples = _vm_samples(f) if "load" in wanted else []
    if samples:
        wa.append((sum(s.get("wa", 0) for s in samples) / len(samples), "vmstat average"))
        st.append((sum(s.get("st", 0) for s in samples) / len(samples), "vmstat average"))
    if f.cpu_line and "top" in wanted:
        wa.append((f.cpu_line.get("wa", 0.0), "top"))
        st.append((f.cpu_line.get("st", 0.0), "top"))
    if f.cpu_line and "top" in wanted:
        c = f.cpu_line
        row("cpu", "cpu: " + ", ".join(f"{c[k]:g} {k}" for k in ("us", "sy", "id", "wa", "st") if k in c), **c)
    if wa:
        val, src = max(wa)
        if val > cfg["iowait_pct"]:
            add("iowait_high", "high" if val > 60 else "medium", "cpu iowait", f"{val:.0f}% of CPU time waits on disk "
                                                                              f"({src})")
    if st:
        val, src = max(st)
        if val > cfg["steal_pct"]:
            add("steal_high", "high" if val > 40 else "medium", "cpu steal", f"{val:.0f}% of CPU time stolen by the "
                                                                           f"host ({src})")


def _noise(r: dict) -> bool:
    if r["mount"] == "/":
        return False
    return (r["type"] in NOISE_TYPES or r["fs"] in NOISE_TYPES or r["fs"].startswith("/dev/loop")
            or r["mount"].startswith(NOISE_MOUNTS) or r["mount"] == "/dev")


def _readonly(r: dict) -> bool:
    return (r["type"] in READONLY_TYPES or r["fs"].startswith("/dev/loop") or r["mount"].startswith("/snap/")
            or r["mount"].startswith("/var/lib/snapd/"))


def _disk_checks(f: Facts, row, add, cfg: dict) -> None:
    ignore = cfg["ignore_mounts"]
    for kind, rows_, key, limit in (("disk", f.disks, "disk_full_pct", cfg["disk_full_pct"]),
                                    ("inodes", f.inodes, "inode_full_pct", cfg["inode_full_pct"])):
        seen, shown, hidden = set(), [], 0
        for r in rows_:
            if r["pct"] is None or r["mount"] in seen or any(fnmatchcase(r["mount"], g) for g in ignore):
                continue
            seen.add(r["mount"])
            if _readonly(r) and r["pct"] >= 100:
                continue
            if _noise(r) and r["pct"] < limit:
                hidden += 1
                continue
            shown.append(r)
        if not rows_:
            continue
        parts = [f"{r['mount']} {r['pct']}%" for r in shown[:10]]
        if len(shown) > 10:
            parts.append(f"+{len(shown) - 10} more")
        text = f"{kind}: " + (", ".join(parts) if parts else "no real filesystems")
        if hidden:
            text += f" ({hidden} pseudo filesystem(s) not shown)"
        row(kind, text, mounts={r["mount"]: r["pct"] for r in shown})
        for r in shown:
            pct = r["pct"]
            if kind == "disk" and pct >= limit:
                sev = ("critical" if pct >= cfg["disk_critical_pct"] else "high" if pct >= cfg["disk_high_pct"]
                       else "low")
                add("disk_full", sev, r["mount"], f"{pct}% full ({r['fs']})")
            elif kind == "inodes" and pct >= limit:
                add("inode_full", "high" if pct >= 99 else "medium", r["mount"],
                    f"{pct}% of inodes used ({r['fs']}): new files may fail with 'No space left'")


def _top_checks(f: Facts, row, add, cfg: dict) -> None:
    if f.tasks is not None:
        row("tasks", f"tasks: {f.tasks}, {f.zombies or 0} zombie{'s' if (f.zombies or 0) != 1 else ''}",
            tasks=f.tasks, zombies=f.zombies or 0)
    zombies = max(f.zombies or 0, sum(1 for p in f.procs if p["zombie"]))
    if zombies >= cfg["zombies"]:
        add("zombies", "medium" if zombies >= cfg["zombies_many"] else "low", "processes",
            f"{zombies} zombie processes (a parent is not reaping its children)")
    if f.procs:
        top = sorted(f.procs, key=lambda p: -p["mem"])[:3]
        row("processes", "top by memory: " + ", ".join(f"{p['pid']}/{p['name']} {p['mem']:g}%" for p in top),
            processes=[{"pid": p["pid"], "name": p["name"], "mem_pct": p["mem"], "cpu_pct": p["cpu"]} for p in top])
    ignored = {n.lower() for n in cfg["ignore_processes"]}
    for p in f.procs:
        if p["name"].lower() in ignored:
            continue
        who = f"{p['pid']}/{p['name']}"
        if p["mem"] > cfg["proc_mem_pct"]:
            add("proc_mem_hog", "high" if p["mem"] > cfg["proc_mem_high_pct"] else "medium", who,
                f"uses {p['mem']:g}% of memory")
        if p["cpu"] > cfg["proc_cpu_pct"]:
            add("proc_cpu_hog", "low" if p["cpu"] >= 200 else "info", who, f"uses {p['cpu']:g}% CPU")


def _kernel_checks(f: Facts, row, add, cfg: dict) -> None:
    kills: dict[str, set[str]] = {}
    seen_pairs: set[tuple[str, str]] = set()
    invoked = anon = 0
    cats: dict[str, list[str]] = {}
    for line in f.kernel:
        found = None
        for rx in (_KILLED, _REAPED):
            m = rx.search(line)
            if m:
                found = (m.group(2), m.group(1))
                break
        else:
            m = _OOM_TASK.search(line)
            if m:
                found = (m.group(1), m.group(2))
            else:
                m = _OOMD.search(line)
                if m:
                    found = (basename(m.group(1).rstrip("/")) or m.group(1), "")
        if found:
            if found not in seen_pairs:
                seen_pairs.add(found)
                kills.setdefault(_clean(found[0], 40), set()).add(found[1])
            continue
        if _INVOKED.search(line):
            invoked += 1
        elif re.search(r"out of memory", line, re.I):
            anon += 1
        else:
            for name, _, rx in KERNEL_CATEGORIES:
                if rx.search(line):
                    cats.setdefault(name, []).append(_clean(line.strip(), 100))
                    break
    names = sorted(kills, key=lambda n: -len(kills[n]))
    bits = []
    if kills:
        bits.append(f"{len(seen_pairs)} OOM kill(s)")
    if invoked:
        bits.append(f"oom-killer invoked {invoked}x")
    bits += [f"{len(v)} {k} line(s)" for k, v in cats.items()]
    row("kernel", f"kernel log: {len(f.kernel)} line(s)" + (": " + ", ".join(bits) if bits
                                                           else ", no OOM kills or errors"),
        lines=len(f.kernel), oom_kills=len(seen_pairs), oom_invoked=invoked,
        errors={k: len(v) for k, v in cats.items()})
    important = tuple(n.lower() for n in IMPORTANT + tuple(cfg["important_processes"]))
    for name in names[:MAX_NAMES]:
        pids = sorted(p for p in kills[name] if p)
        crit = name.lower().startswith(important)
        extra = f"; pid {', '.join(pids[:4])}" if pids else ""
        add("oom_kill", "critical" if crit else "high", name,
            f"killed by the out-of-memory killer ({len(kills[name])}x{extra})"
            + ("; an important service" if crit else ""))
    if len(names) > MAX_NAMES:
        add("oom_kill", "high", "other processes", f"{len(names) - MAX_NAMES} more distinct processes were killed")
    if (invoked or anon) and not kills:
        add("oom_kill", "high", "oom-killer", f"ran or reported out of memory {invoked + anon}x, no 'Killed process' "
                                              "line in this text")
    for name, sev, _ in KERNEL_CATEGORIES:
        if name not in cats:
            continue
        if name == "call traces" and (len(cats) > 1 or kills or invoked):
            sev = "low"
        add("kernel_errors", sev, name, f"{len(cats[name])} line(s), first: {cats[name][0]}")
