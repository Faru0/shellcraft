"""hostscan — offline triage of Linux host data (ps, ss, exes, maps, bpf, probes) collected over ssh."""

from __future__ import annotations

import json
import re
import tomllib
from collections import Counter, defaultdict
from fnmatch import fnmatchcase
from posixpath import basename

from shellcraft.config import config_dir
from shellcraft.modkit import ArgParser, ModuleError

SUMMARY = "Triage ps/ss/exe/maps/bpf/probe text from a host: suspicious processes, sockets, hooks (offline)"

SUBCOMMANDS = ("ps", "ss", "maps", "exes", "bpf", "probes", "join")
OUTPUTS = ("table", "json")
SEVERITIES = ("info", "low", "medium", "high", "critical")
RANK = {s: i for i, s in enumerate(SEVERITIES)}

# Every rule hostscan can raise. The man page lists them all (a test keeps the two in sync).
RULES = [
    {"id": "deleted_exe", "severity": "high", "sub": "exes, join",
     "description": "The process runs a binary that was deleted from disk (medium when the path is under a system "
                    "directory, because a package upgrade does the same)."},
    {"id": "memfd_exe", "severity": "high", "sub": "exes, join",
     "description": "The process runs from an anonymous memory file (/memfd:...), a fileless-malware habit."},
    {"id": "writable_exe", "severity": "high", "sub": "ps, exes, join",
     "description": "The executable (or the command path, or the script an interpreter runs) is under /tmp, "
                    "/var/tmp, /dev/shm or /run/user."},
    {"id": "web_child_shell", "severity": "high", "sub": "ps, join",
     "description": "A shell, nc, ncat or socat (medium for python or perl) whose parent is a web server or "
                    "database (nginx, apache2, httpd, php-fpm, tomcat, java, node, mysqld, postgres)."},
    {"id": "sshd_child_nc", "severity": "high", "sub": "ps, join",
     "description": "nc, ncat or socat started directly by sshd."},
    {"id": "masquerade", "severity": "medium", "sub": "join",
     "description": "The process name (first word of the command) differs from the executable name. Interpreters, "
                    "busybox, shell aliases and kernel threads are ignored. A name like [kworker/0:1] that has a "
                    "readable executable is high."},
    {"id": "listen_unknown", "severity": "low", "sub": "ss, join",
     "description": "Listening on all interfaces (0.0.0.0, ::, *) on a port outside the usual set "
                    "(22, 25, 53, 80, 123, 443, 587, 993, 995, 3000, 3306, 5432, 6379, 8080, 8443, 9090, 9100). "
                    "UDP ports of 32768 and up are skipped."},
    {"id": "listen_writable", "severity": "high", "sub": "join",
     "description": "Like listen_unknown, on a port above 1024, and the listener's executable is in a writable "
                    "directory."},
    {"id": "conn_flood", "severity": "medium", "sub": "ss, join",
     "description": "One remote address (not loopback) holds 20 or more established connections."},
    {"id": "conn_rare_port", "severity": "medium", "sub": "ss, join",
     "description": "Outbound established connection to 4444, 1337, 31337, 6667, 6697, 9001, 9050, 8333 or 3333 "
                    "(high when a shell, nc, ncat or socat owns it)."},
    {"id": "conn_shell_highport", "severity": "medium", "sub": "ss, join",
     "description": "A shell holds an outbound connection to a remote port of 30000 or more."},
    {"id": "maps_anon_rwx", "severity": "high", "sub": "maps, join",
     "description": "An anonymous (or heap/stack) mapping is readable, writable and executable. JIT runtimes "
                    "(java, node) do this legitimately."},
    {"id": "maps_file_rwx", "severity": "medium", "sub": "maps, join",
     "description": "A file-backed mapping is readable, writable and executable."},
    {"id": "maps_writable_file", "severity": "high", "sub": "maps, join",
     "description": "A file under /tmp, /var/tmp, /dev/shm or /run/user is mapped (high when executable, "
                    "medium for a data mapping)."},
    {"id": "maps_deleted_file", "severity": "high", "sub": "maps, join",
     "description": "A deleted file is mapped (high when executable outside system directories, medium "
                    "otherwise; a library replaced by a package upgrade looks the same)."},
    {"id": "maps_memfd", "severity": "high", "sub": "maps, join",
     "description": "A /memfd: file is mapped (high when executable, medium for a data mapping)."},
    {"id": "maps_nonsystem_lib", "severity": "medium", "sub": "maps, join",
     "description": "A shared library (.so) is loaded from outside /usr/lib*, /lib*, /usr/local/lib* and /opt."},
    {"id": "bpf_prog", "severity": "medium", "sub": "bpf",
     "description": "A tracing, networking or cgroup eBPF program is loaded (info when its owner is a known tool "
                    "such as systemd, cilium, bpftrace, falco, tetragon, datadog, pixie, otelcol)."},
    {"id": "bpf_prog_anon", "severity": "high", "sub": "bpf",
     "description": "A kprobe, kretprobe or raw_tracepoint program with no owner process or no name."},
    {"id": "bpf_no_pids", "severity": "info", "sub": "bpf",
     "description": "A program of another type has no owner pid information (run bpftool as root for pids)."},
    {"id": "bpf_link", "severity": "info", "sub": "bpf",
     "description": "An eBPF link is attached."},
    {"id": "bpf_map_name", "severity": "medium", "sub": "bpf",
     "description": "A prog_array, sockmap, sockhash, hash, lru_hash or array map is named like hidden, ports or pids "
                    "(info when its owner is a known tool)."},
    {"id": "kprobe_hook", "severity": "medium", "sub": "probes",
     "description": "A kprobe on a syscall, kill, tcp_*, udp_*, read, write, open, execve or bpf function."},
    {"id": "kprobe_rootkit", "severity": "high", "sub": "probes",
     "description": "A kprobe on getdents, readdir, iterate_dir or a *_seq_show function, the hooks rootkits "
                    "use to hide files, processes and sockets."},
    {"id": "uprobe_lib", "severity": "medium", "sub": "probes",
     "description": "A uprobe in libc, libssl or sudo."},
    {"id": "uprobe_auth", "severity": "high", "sub": "probes",
     "description": "A uprobe in libpam, a pam module, sshd or ssh (credential theft)."},
    {"id": "uprobe_writable", "severity": "high", "sub": "probes",
     "description": "A uprobe on a file under /tmp, /var/tmp, /dev/shm or /run/user."},
    {"id": "probe_other", "severity": "info", "sub": "probes",
     "description": "Any other kprobe or uprobe is registered."},
]
RULE_SEVERITY = {r["id"]: r["severity"] for r in RULES}

KNOWN_PORTS = {22, 53, 80, 443, 123, 25, 587, 993, 995, 3306, 5432, 6379, 8080, 8443, 9090, 9100, 3000}
RARE_PORTS = {4444, 1337, 31337, 6667, 6697, 9001, 9050, 8333, 3333}
KNOWN_OWNERS = ("systemd", "cilium", "bpftrace", "falco", "tetragon", "datadog", "pixie", "otelcol")
WRITABLE_DIRS = ("/tmp/", "/var/tmp/", "/dev/shm/", "/run/user/")
SYSTEM_LIB_DIRS = ("/usr/lib", "/lib", "/usr/local/lib", "/opt/")
SYSTEM_DIRS = ("/usr/", "/bin/", "/sbin/", "/lib", "/opt/")
SHELLS = {"sh", "bash", "dash", "zsh", "ash"}
NETTOOLS = {"nc", "ncat", "socat", "netcat"}
WEB_DB = {"nginx", "apache2", "httpd", "tomcat", "java", "node", "mysqld", "mariadbd", "postgres", "lighttpd", "caddy"}
INTERPRETER = re.compile(r"(python|perl|ruby|java|node|nodejs|php|lua|bash|sh|dash|zsh|ash|busybox|dotnet|mono"
                         r"|env|pwsh)([\d.\-].*)?$")
ALIASES = {"init": "systemd"}
BPF_TYPES = {"kprobe", "kretprobe", "tracepoint", "raw_tracepoint", "perf_event", "sk_msg", "sk_skb", "sk_filter",
             "socket_filter", "xdp", "sched_cls"}
BPF_HIGH_TYPES = {"kprobe", "kretprobe", "raw_tracepoint"}
BPF_MAP_TYPES = {"prog_array", "sockmap", "sockhash", "hash", "lru_hash", "array"}
BPF_MAP_NAME = re.compile(r"(^|[^a-z])(hidden|hide|ports?|pids?)([^a-z]|$)")
DEFAULT_LIMIT = 200
RULES_FILE = "hostscan-rules.toml"

_SS_USERS = re.compile(r'\("([^"]*)",pid=(\d+)')
_MAPS_LINE = re.compile(r"^([0-9a-f]+)-([0-9a-f]+)\s+(\S{4})\s+([0-9a-f]+)\s+(\S+)\s+(\d+)\s*(.*)$")
_PROBE_LINE = re.compile(r"^([pr])(?:\[\w*\])?:(?:([^/\s]+)/)?(\S+)\s+(\S+)(?:\s+(.*))?$")


# ── entry point ──────────────────────────────────────────────────────────────

def run(args: list[str], stdin: str) -> str:
    parser = ArgParser("hostscan")
    parser.add_argument("subcommand", choices=SUBCOMMANDS)
    parser.add_argument("--pid", type=int, metavar="PID")
    parser.add_argument("-o", "--output", default="table", choices=OUTPUTS)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, metavar="N")
    opts = parser.parse_args(args)
    if opts.limit < 1:
        raise ModuleError("hostscan: --limit must be 1 or more")
    if opts.pid is not None and opts.pid < 1:
        raise ModuleError("hostscan: --pid must be a positive process id")

    rules, note = _load_rules()
    notes = [note] if note else []
    handler = {"ps": _do_ps, "ss": _do_ss, "exes": _do_exes, "maps": _do_maps, "bpf": _do_bpf,
               "probes": _do_probes, "join": _do_join}[opts.subcommand]
    rows, flags, render, more_notes = handler(stdin, opts)
    notes += more_notes

    flags = _dedupe(flags)
    kept = [f for f in flags if not _suppressed(f, rules)]
    if len(kept) < len(flags):
        notes.append(f"{len(flags) - len(kept)} flag(s) suppressed by {RULES_FILE}")
    kept.sort(key=lambda f: (-RANK[f["severity"]], f["rule"], f["subject"], f["reason"]))

    shown = rows[: opts.limit]
    hidden = len(rows) - len(shown)
    if opts.output == "json":
        out = {"rows": shown, "flags": [_public(f) for f in kept]}
        if hidden:
            out["truncated"] = hidden
        if notes:
            out["notes"] = notes
        return json.dumps(out, indent=2, ensure_ascii=False) + "\n"

    lines = [render(shown) if shown else "(no rows)", ""]
    if hidden:
        lines.append(f"... {hidden} more row(s), raise --limit")
    lines += [f"note: {n}" for n in notes]
    lines += [f"FLAG {f['severity']} {f['rule']} {f['subject']}: {f['reason']}" for f in kept]
    lines.append(f"{len(kept)} flag(s)" if kept else "no flags")
    return "\n".join(lines) + "\n"


# ── shared helpers ───────────────────────────────────────────────────────────

def flag(rule: str, subject: str, reason: str, severity: str | None = None, *, pid: int | None = None,
         path: str | None = None, port: int | None = None, owners: list[str] | None = None) -> dict:
    return {"severity": severity or RULE_SEVERITY[rule], "rule": rule, "subject": subject, "reason": reason,
            "pid": pid, "path": path, "port": port, "owners": owners or []}


def _public(f: dict) -> dict:
    return {k: f[k] for k in ("severity", "rule", "subject", "reason")}


def _dedupe(flags: list[dict]) -> list[dict]:
    seen, out = set(), []
    for f in flags:
        key = (f["rule"], f["subject"])
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def _sections(text: str) -> list[tuple[str, str, list[str]]]:
    """Split on `## NAME ARG` markers, dropping blank lines and `# ` comments. Lines before the first
    marker belong to the section named ''."""
    out: list[list] = [["", "", []]]
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s == "#" or s.startswith("# "):
            continue
        if s == "##" or s.startswith("## "):
            parts = s[2:].split(None, 1)
            out.append([parts[0].lower() if parts else "", parts[1].strip() if len(parts) > 1 else "", []])
            continue
        out[-1][2].append(raw)
    return [(n, a, ls) for n, a, ls in out]


def _flat(text: str) -> list[str]:
    return [ln for _, _, lines in _sections(text) for ln in lines]


def _need(lines: list, sub: str, what: str) -> None:
    if not lines:
        raise ModuleError(f"hostscan {sub}: no input on stdin; pipe in {what}")


def _unrecognised(sub: str, what: str, skipped: int) -> ModuleError:
    return ModuleError(f"hostscan {sub}: none of the {skipped} input line(s) look like {what}")


def _skipped_note(skipped: int) -> list[str]:
    return [f"{skipped} malformed line(s) skipped"] if skipped else []


def _writable(path: str | None) -> bool:
    return bool(path) and path.startswith(WRITABLE_DIRS)


def _age(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _cut(text: str, width: int = 110) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def _grid(cols: list[tuple[str, callable]], rows: list[dict]) -> str:
    cells = [[str(fn(r)) for _, fn in cols] for r in rows]
    widths = [max(len(title), *(len(c[i]) for c in cells)) for i, (title, _) in enumerate(cols)]
    lines = ["  ".join(t.ljust(w) for (t, _), w in zip(cols, widths)).rstrip()]
    lines += ["  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip() for row in cells]
    return "\n".join(lines)


def _proc_name(args: str) -> str:
    args = args.strip()
    if args.startswith("["):
        m = re.match(r"\[[^\]]*\]", args)
        return m.group(0) if m else args.split()[0]
    first = args.split()[0] if args else ""
    return basename(first).rstrip(":").lstrip("-")


def _label(pid: int, procs: dict) -> str:
    p = procs.get(pid)
    return f"{pid}/{p['name']}" if p and p["name"] else str(pid)


# ── user rules file ──────────────────────────────────────────────────────────

class _UserRules:
    def __init__(self) -> None:
        self.ports: set[int] = set()
        self.owners: list[str] = []
        self.paths: list[str] = []


def _load_rules() -> tuple[_UserRules, str | None]:
    rules = _UserRules()
    path = config_dir() / RULES_FILE
    if not path.is_file():
        return rules, None
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return _UserRules(), f"{path} ignored: {' '.join(str(exc).split())}"
    problems = []
    for key, want in (("known_ports", int), ("known_owners", str), ("known_paths", str)):
        value = data.get(key)
        if value is None:
            continue
        if not isinstance(value, list) or not all(isinstance(v, want) and not isinstance(v, bool) for v in value):
            problems.append(f"{key} must be a list of {'integers' if want is int else 'strings'}, ignored")
        elif key == "known_ports":
            rules.ports = set(value)
        elif key == "known_owners":
            rules.owners = [v.lower() for v in value]
        else:
            rules.paths = list(value)
    extra = sorted(set(data) - {"known_ports", "known_owners", "known_paths"})
    if extra:
        problems.append(f"unknown key(s) {', '.join(extra)} ignored")
    return rules, (f"{path}: " + "; ".join(problems)) if problems else None


def _suppressed(f: dict, rules: _UserRules) -> bool:
    if f["port"] is not None and f["port"] in rules.ports:
        return True
    if f["path"] and any(fnmatchcase(f["path"], g) for g in rules.paths):
        return True
    return any(_owner_in(o, rules.owners) for o in f["owners"])


def _owner_in(comm: str, names) -> bool:
    comm = comm.lower()
    return any(comm.startswith(n) for n in names)


# ── ps ───────────────────────────────────────────────────────────────────────

def _parse_ps(lines: list[str]) -> tuple[dict[int, dict], int]:
    procs, skipped = {}, 0
    for ln in lines:
        parts = ln.split(None, 4)
        if len(parts) < 4 or not (parts[0].isdigit() and parts[1].isdigit() and parts[3].isdigit()):
            skipped += 1
            continue
        args = parts[4].strip() if len(parts) == 5 else ""
        pid = int(parts[0])
        procs[pid] = {"pid": pid, "ppid": int(parts[1]), "user": parts[2], "age_s": int(parts[3]),
                      "name": _proc_name(args), "args": args}
    return procs, skipped


def _cmd_path(args: str) -> str | None:
    toks = args.split()
    if not toks:
        return None
    cand = toks[0]
    if INTERPRETER.match(basename(cand)):
        cand = next((t for t in toks[1:] if not t.startswith("-")), "")
    return cand if cand.startswith("/") else None


def _masquerade(p: dict, exe: str) -> tuple[str, str] | None:
    path = exe.removesuffix(" (deleted)")
    base, name = basename(path), p["name"]
    if not name or name.startswith("("):
        return None
    if name.startswith("["):
        return "high", f"named {name} like a kernel thread, but it has an executable ({path})"
    if INTERPRETER.match(base) or INTERPRETER.match(name):
        return None
    if name == base or base.startswith(name) or name.startswith(base) or ALIASES.get(name) == base:
        return None
    return "medium", f"process name {name} differs from its executable {path}"


def _proc_flags(procs: dict[int, dict], exes: dict[int, str | None]) -> list[dict]:
    out = []
    for pid in sorted(procs):
        p = procs[pid]
        name, label = p["name"], _label(pid, procs)
        path = _cmd_path(p["args"])
        if _writable(path):
            out.append(flag("writable_exe", label, f"command runs from {path}", pid=pid, path=path))
        parent = procs.get(p["ppid"])
        if parent:
            pname = "php-fpm" if parent["name"].startswith("php-fpm") else parent["name"]
            if pname in WEB_DB:
                if name in SHELLS or name in NETTOOLS:
                    out.append(flag("web_child_shell", label, f"{name} started by {pname} ({p['ppid']})", pid=pid))
                elif re.match(r"(python|perl)[\d.]*$", name):
                    out.append(flag("web_child_shell", label, f"{name} started by {pname} ({p['ppid']})",
                                    "medium", pid=pid))
            if parent["name"] == "sshd" and name in NETTOOLS:
                out.append(flag("sshd_child_nc", label, f"{name} started directly by sshd ({p['ppid']})", pid=pid))
        exe = exes.get(pid)
        if exe:
            hit = _masquerade(p, exe)
            if hit:
                out.append(flag("masquerade", label, hit[1], hit[0], pid=pid, path=exe.removesuffix(" (deleted)")))
    return out


PS_COLS = [("PID", lambda r: r["pid"]), ("PPID", lambda r: r["ppid"]), ("USER", lambda r: r["user"]),
           ("AGE", lambda r: _age(r["age_s"])), ("CMD", lambda r: _cut(r["args"]))]


def _do_ps(text: str, opts):
    lines = _flat(text)
    _need(lines, "ps", "the output of sshrun's ps check")
    procs, skipped = _parse_ps(lines)
    if not procs:
        raise _unrecognised("ps", "`ps -eo pid=,ppid=,user=,etimes=,args=` output", skipped)
    rows = [procs[p] for p in sorted(procs)]
    return rows, _proc_flags(procs, {}), lambda rs: _grid(PS_COLS, rs), _skipped_note(skipped)


# ── exes ─────────────────────────────────────────────────────────────────────

def _parse_exes(lines: list[str]) -> tuple[dict[int, str | None], int]:
    exes, skipped = {}, 0
    for ln in lines:
        parts = ln.split(None, 1)
        if not parts or not parts[0].isdigit():
            skipped += 1
            continue
        exes[int(parts[0])] = parts[1].strip() if len(parts) == 2 else None
    return exes, skipped


def _exe_flags(exes: dict[int, str | None], procs: dict[int, dict]) -> list[dict]:
    out = []
    for pid in sorted(exes):
        exe = exes[pid]
        if not exe:
            continue
        label = _label(pid, procs)
        deleted = exe.endswith(" (deleted)")
        path = exe.removesuffix(" (deleted)")
        if path.startswith("/memfd:"):
            out.append(flag("memfd_exe", label, f"runs from an anonymous memory file ({path})", pid=pid, path=path))
        elif deleted:
            sev = "medium" if path.startswith(SYSTEM_DIRS) else "high"
            why = "binary deleted from disk" + (" (a package upgrade does this too)" if sev == "medium" else "")
            out.append(flag("deleted_exe", label, f"{why}: {path}", sev, pid=pid, path=path))
        if _writable(path):
            out.append(flag("writable_exe", label, f"executable is in a writable directory: {path}", pid=pid,
                            path=path))
    return out


def _do_exes(text: str, opts):
    lines = _flat(text)
    _need(lines, "exes", "the output of sshrun's exes check")
    exes, skipped = _parse_exes(lines)
    if not exes:
        raise _unrecognised("exes", "`PID /path/to/exe`", skipped)
    rows = [{"pid": p, "exe": exes[p]} for p in sorted(exes)]
    cols = [("PID", lambda r: r["pid"]), ("EXE", lambda r: r["exe"] or "(unreadable)")]
    return rows, _exe_flags(exes, {}), lambda rs: _grid(cols, rs), _skipped_note(skipped)


# ── ss ───────────────────────────────────────────────────────────────────────

def _split_addr(s: str) -> tuple[str, int | None]:
    host, sep, port = s.rpartition(":")
    if not sep:
        return s, None
    return host.strip("[]"), int(port) if port.isdigit() else None


def _wildcard(addr: str) -> bool:
    return addr.split("%")[0] in ("0.0.0.0", "::", "*", "")


def _loopback(addr: str) -> bool:
    return addr.startswith("127.") or addr in ("::1", "localhost")


def _parse_ss(lines: list[str]) -> tuple[list[dict], int]:
    conns, skipped = [], 0
    for ln in lines:
        parts = ln.split(None, 6)
        if len(parts) < 6 or parts[0] not in ("tcp", "udp", "tcp6", "udp6") or not (
                parts[2].isdigit() and parts[3].isdigit()):
            skipped += 1
            continue
        laddr, lport = _split_addr(parts[4])
        raddr, rport = _split_addr(parts[5])
        procs = [{"pid": int(pid), "name": name}
                 for name, pid in _SS_USERS.findall(parts[6] if len(parts) == 7 else "")]
        conns.append({"proto": parts[0], "state": parts[1], "local": parts[4], "peer": parts[5], "laddr": laddr,
                      "lport": lport, "raddr": raddr, "rport": rport, "procs": procs})
    return conns, skipped


def _proc_label(c: dict) -> str:
    return ",".join(f"{p['pid']}/{p['name']}" for p in c["procs"])


def _ss_flags(conns: list[dict], exes: dict[int, str | None]) -> list[dict]:
    out = []
    listening = {c["lport"] for c in conns if c["state"] == "LISTEN" and c["lport"] is not None}
    for c in conns:
        label = _proc_label(c)
        subject = f"{c['proto']} {c['local']}" + (f" ({label})" if label else "")
        port, names = c["lport"], {p["name"] for p in c["procs"]}
        if c["state"] in ("LISTEN", "UNCONN") and _wildcard(c["laddr"]) and port is not None:
            if port in KNOWN_PORTS or (c["proto"].startswith("udp") and port >= 32768):
                continue
            wexe = next((exes[p["pid"]] for p in c["procs"] if _writable((exes.get(p["pid"]) or "").removesuffix(
                " (deleted)"))), None)
            if wexe and port > 1024:
                out.append(flag("listen_writable", subject, f"listens on all interfaces, port {port}, and runs "
                                f"from {wexe}", pid=c["procs"][0]["pid"], path=wexe.removesuffix(" (deleted)"),
                                port=port))
            else:
                pid = c["procs"][0]["pid"] if c["procs"] else None
                out.append(flag("listen_unknown", subject, f"listens on all interfaces, port {port} is not in the "
                                "usual set", pid=pid, port=port))
        elif c["state"] in ("ESTAB", "ESTABLISHED") and port not in listening and c["rport"] is not None:
            shellish = bool(names & (SHELLS | NETTOOLS))
            pid = c["procs"][0]["pid"] if c["procs"] else None
            if c["rport"] in RARE_PORTS:
                out.append(flag("conn_rare_port", subject, f"outbound to {c['peer']}, a port often used by "
                                "backdoors, IRC or Tor", "high" if shellish else "medium", pid=pid, port=c["rport"]))
            elif c["rport"] >= 30000 and names & SHELLS:
                out.append(flag("conn_shell_highport", subject, f"a shell is connected out to {c['peer']}",
                                pid=pid, port=c["rport"]))
    peers = Counter(c["raddr"] for c in conns if c["state"] in ("ESTAB", "ESTABLISHED") and not _loopback(c["raddr"]))
    for addr, n in sorted(peers.items()):
        if n >= 20:
            out.append(flag("conn_flood", addr, f"{n} established connections from or to one address"))
    return out


def _do_ss(text: str, opts):
    lines = _flat(text)
    _need(lines, "ss", "the output of sshrun's ss check")
    conns, skipped = _parse_ss(lines)
    if not conns:
        raise _unrecognised("ss", "`ss -H -tunap` output", skipped)
    conns.sort(key=lambda c: (c["proto"], c["state"], c["local"], c["peer"]))
    rows = [{k: c[k] for k in ("proto", "state", "local", "peer")} | {"procs": _proc_label(c)} for c in conns]
    cols = [("PROTO", lambda r: r["proto"]), ("STATE", lambda r: r["state"]), ("LOCAL", lambda r: r["local"]),
            ("PEER", lambda r: r["peer"]), ("PROCESS", lambda r: r["procs"])]
    return rows, _ss_flags(conns, {}), lambda rs: _grid(cols, rs), _skipped_note(skipped)


# ── maps ─────────────────────────────────────────────────────────────────────

def _parse_maps(lines: list[str]) -> tuple[list[dict], int]:
    maps, skipped = [], 0
    for ln in lines:
        m = _MAPS_LINE.match(ln.strip())
        if not m:
            skipped += 1
            continue
        maps.append({"start": m.group(1), "perms": m.group(3), "path": m.group(7).strip()})
    return maps, skipped


def _maps_flags(pid: int | None, maps: list[dict]) -> list[dict]:
    who = f"pid {pid}" if pid else "pid ?"
    hits: dict[tuple, dict] = {}

    def add(rule: str, path: str, sev: str, why: str, m: dict) -> None:
        h = hits.setdefault((rule, path), {"n": 0, "first": m["start"], "sev": sev, "why": why, "path": path})
        h["n"] += 1

    for m in maps:
        p, perms = m["path"], m["perms"]
        execm = perms[2] == "x"
        anon = p in ("", "[heap]", "[stack]")
        shown = p or "[anon]"
        if anon and perms[:3] == "rwx":
            add("maps_anon_rwx", shown, "high", f"{shown} mapping is rwx (JIT runtimes do this too)", m)
        if p.startswith("[v") or anon:
            continue
        deleted = p.endswith(" (deleted)")
        path = p.removesuffix(" (deleted)")
        if path.startswith("/memfd:"):
            add("maps_memfd", p, "high" if execm else "medium", f"memfd mapping {p}", m)
        elif deleted:
            if path == "/dev/zero" or path.startswith(("/SYSV", "/anon_hugepage")):
                continue
            sev = "high" if execm and not path.startswith(SYSTEM_LIB_DIRS) else "medium"
            add("maps_deleted_file", p, sev, f"deleted file is mapped ({perms}): {path}", m)
        elif _writable(path):
            add("maps_writable_file", p, "high" if execm else "medium", f"file in a writable directory is mapped "
                f"({perms}): {path}", m)
        elif ".so" in basename(path) and not path.startswith(SYSTEM_LIB_DIRS):
            add("maps_nonsystem_lib", p, "medium", f"library outside the system directories: {path}", m)
        if p.startswith("/") and perms[:3] == "rwx":
            add("maps_file_rwx", p, "medium", f"file-backed mapping is rwx: {p}", m)
    out = []
    for (rule, key), h in sorted(hits.items()):
        count = f" ({h['n']} mappings, first at {h['first']})" if h["n"] > 1 else f" (at {h['first']})"
        out.append(flag(rule, f"{who} {key}", h["why"] + count, h["sev"], pid=pid,
                        path=h["path"].removesuffix(" (deleted)") if h["path"].startswith("/") else None))
    return out


def _maps_rows(maps: list[dict]) -> list[dict]:
    groups: dict[str, dict] = defaultdict(lambda: {"perms": set(), "count": 0})
    for m in maps:
        g = groups[m["path"] or "[anon]"]
        g["perms"].add(m["perms"])
        g["count"] += 1
    return [{"path": k, "perms": sorted(g["perms"]), "count": g["count"]} for k, g in sorted(groups.items())]


def _do_maps(text: str, opts):
    lines = _flat(text)
    _need(lines, "maps", "the contents of /proc/PID/maps")
    maps, skipped = _parse_maps(lines)
    if not maps:
        raise _unrecognised("maps", "/proc/PID/maps lines", skipped)
    rows = _maps_rows(maps)
    cols = [("COUNT", lambda r: r["count"]), ("PERMS", lambda r: ",".join(r["perms"])), ("PATH", lambda r: r["path"])]
    return rows, _maps_flags(opts.pid, maps), lambda rs: _grid(cols, rs), _skipped_note(skipped)


# ── bpf ──────────────────────────────────────────────────────────────────────

def _bpf_objects(text: str) -> list[tuple[str, dict]]:
    objs = []
    decoder = json.JSONDecoder()
    for name, _, lines in _sections(text):
        body = "\n".join(lines)
        want = name.rstrip("s") if name.rstrip("s") in ("prog", "link", "map") else None
        pos = 0
        try:
            while True:
                while pos < len(body) and body[pos].isspace():
                    pos += 1
                if pos >= len(body):
                    break
                value, pos = decoder.raw_decode(body, pos)
                for d in value if isinstance(value, list) else [value]:
                    if isinstance(d, dict):
                        objs.append((want or _bpf_kind(d), d))
        except json.JSONDecodeError as exc:
            raise ModuleError(f"hostscan bpf: input is not valid JSON ({exc.msg}, line {exc.lineno}); expected "
                              "`bpftool -j prog show` output") from None
    return objs


def _bpf_kind(d: dict) -> str:
    if "prog_id" in d:
        return "link"
    if any(k in d for k in ("bytes_key", "bytes_value", "max_entries")):
        return "map"
    return "prog"


def _owners(d: dict) -> list[tuple[str, str]]:
    pids = d.get("pids")
    if not isinstance(pids, list):
        return []
    return [(str(p.get("comm", "?")), str(p.get("pid", "?"))) for p in pids if isinstance(p, dict)]


def _do_bpf(text: str, opts):
    if not _flat(text):
        raise ModuleError("hostscan bpf: no input on stdin; pipe in the output of sshrun's bpf check")
    objs = _bpf_objects(text)
    rows, flags = [], []
    for kind, d in objs:
        owners = _owners(d)
        comms = [c for c, _ in owners]
        known = [c for c in comms if _owner_in(c, KNOWN_OWNERS)]
        owner_txt = ",".join(f"{c}({p})" for c, p in owners)
        typ, name, ident = str(d.get("type", "?")), str(d.get("name", "")), d.get("id", "?")
        extra = ""
        if kind == "link":
            extra = f"prog {d.get('prog_id', '?')}"
        elif kind == "map":
            extra = (f"{d.get('bytes_key', '?')}B key, {d.get('bytes_value', '?')}B value, "
                     f"max {d.get('max_entries', '?')}")
        else:
            extra = f"tag {d.get('tag', '?')} loaded {d.get('loaded_at', '?')} uid {d.get('uid', '?')}"
        rows.append({"kind": kind, "id": ident, "type": typ, "name": name, "owners": owner_txt, "extra": extra})
        subject = f"{kind} {ident} {typ} {name or '(unnamed)'}"
        if kind == "prog":
            if typ in BPF_TYPES or typ.startswith("cgroup_"):
                why = f"{typ} program, owner {owner_txt or 'unknown'}"
                if known:
                    flags.append(flag("bpf_prog", subject, why, "info", owners=comms))
                elif typ in BPF_HIGH_TYPES and (not owners or not name):
                    flags.append(flag("bpf_prog_anon", subject, why + (" and no name" if not name else ""),
                                      owners=comms))
                else:
                    flags.append(flag("bpf_prog", subject, why, owners=comms))
            elif not owners:
                flags.append(flag("bpf_no_pids", subject, f"{typ} program without owner pid information"))
        elif kind == "link":
            flags.append(flag("bpf_link", f"link {ident} {typ}", f"{typ} link to {extra}"
                              + (f", owner {owner_txt}" if owner_txt else ""), owners=comms))
        elif typ in BPF_MAP_TYPES and BPF_MAP_NAME.search(name.lower()):
            flags.append(flag("bpf_map_name", subject, f"{typ} map named {name}"
                              + (f", owner {owner_txt}" if owner_txt else ""), "info" if known else None,
                              owners=comms))
    rows.sort(key=lambda r: (r["kind"], str(r["id"]).zfill(10), r["name"]))
    cols = [("KIND", lambda r: r["kind"]), ("ID", lambda r: r["id"]), ("TYPE", lambda r: r["type"]),
            ("NAME", lambda r: r["name"] or "-"), ("OWNERS", lambda r: r["owners"] or "-"),
            ("DETAIL", lambda r: r["extra"])]
    return rows, flags, lambda rs: _grid(cols, rs), []


# ── probes ───────────────────────────────────────────────────────────────────

def _parse_probes(text: str) -> tuple[list[dict], int]:
    probes, skipped = [], 0
    for section, _, lines in _sections(text):
        for ln in lines:
            m = _PROBE_LINE.match(ln.strip())
            if not m:
                skipped += 1
                continue
            kind_, group, name, target, rest = m.groups()
            if section.startswith("uprobe") or (group or "").startswith("uprobes"):
                kind = "uprobe"
            elif section.startswith("kprobe") or (group or "").startswith("kprobes"):
                kind = "kprobe"
            else:
                kind = "uprobe" if target.startswith("/") else "kprobe"
            probes.append({"kind": kind, "type": kind_, "group": group or "", "name": name, "target": target,
                           "args": (rest or "").strip()})
    return probes, skipped


def _probe_flags(probes: list[dict]) -> list[dict]:
    out = []
    for p in probes:
        subject = f"{p['kind']} {p['group'] + '/' if p['group'] else ''}{p['name']}"
        target = p["target"]
        if p["kind"] == "kprobe":
            low = target.lower()
            toks = set(re.split(r"[^a-z]+", low))
            if toks & {"getdents", "readdir", "filldir", "iterate", "kallsyms"} or "seq_show" in low:
                out.append(flag("kprobe_rootkit", subject, f"hooks {target}, a function rootkits use to hide "
                                "files, processes or sockets"))
            elif (toks & {"read", "write", "open", "openat", "execve", "execveat", "kill", "bpf", "tcp", "udp"}
                  or "sys_call" in low or "syscall" in low):
                out.append(flag("kprobe_hook", subject, f"hooks {target}"))
            else:
                out.append(flag("probe_other", subject, f"hooks {target}"))
            continue
        path = target.rsplit(":", 1)[0] if ":" in target else target
        base = basename(path).lower()
        hit = False
        if _writable(path):
            out.append(flag("uprobe_writable", subject, f"uprobe on a file in a writable directory: {path}",
                            path=path))
            hit = True
        if base.startswith(("libpam", "pam_", "libssh")) or base in ("sshd", "ssh", "ssh-agent"):
            out.append(flag("uprobe_auth", subject, f"uprobe in {path}, where credentials pass", path=path))
            hit = True
        elif base.startswith(("libc.", "libc-", "libssl", "sudo")):
            out.append(flag("uprobe_lib", subject, f"uprobe in {path}", path=path))
            hit = True
        if not hit:
            out.append(flag("probe_other", subject, f"uprobe on {target}", path=path))
    return out


def _do_probes(text: str, opts):
    if not _flat(text) and not any(n for n, _, _ in _sections(text)):
        raise ModuleError("hostscan probes: no input on stdin; pipe in the output of sshrun's probes check")
    probes, skipped = _parse_probes(text)
    if not probes and skipped:
        raise _unrecognised("probes", "kprobe_events or uprobe_events lines", skipped)
    probes.sort(key=lambda p: (p["kind"], p["group"], p["name"], p["target"]))
    cols = [("KIND", lambda r: r["kind"]), ("TYPE", lambda r: r["type"]), ("NAME", lambda r: f"{r['group']}/{r['name']}"
            if r["group"] else r["name"]), ("TARGET", lambda r: r["target"])]
    return probes, _probe_flags(probes), lambda rs: _grid(cols, rs), _skipped_note(skipped)


# ── join ─────────────────────────────────────────────────────────────────────

def _chain(pid: int, procs: dict[int, dict]) -> list[str]:
    out, seen = [], set()
    while pid in procs and pid not in seen and len(out) < 12:
        seen.add(pid)
        out.append(f"{procs[pid]['name'] or '?'}({pid})")
        pid = procs[pid]["ppid"]
    return out[::-1]


def _more(items: list[str], keep: int = 8) -> list[str]:
    return items if len(items) <= keep else items[:keep] + [f"(+{len(items) - keep} more)"]


def _do_join(text: str, opts):
    ps_lines, ss_lines, exe_lines, maps_blocks, ignored = [], [], [], [], []
    for name, arg, lines in _sections(text):
        if name == "ps":
            ps_lines += lines
        elif name == "ss":
            ss_lines += lines
        elif name == "exes":
            exe_lines += lines
        elif name == "maps":
            maps_blocks.append((arg, lines))
        elif name:
            ignored.append(name)
        elif lines:
            ignored.append("(text before the first ## marker)")
    if not (ps_lines or ss_lines or exe_lines):
        raise ModuleError("hostscan join: expected `## ps`, `## ss` and `## exes` sections (and optionally "
                          "`## maps PID`) on stdin")
    procs, s1 = _parse_ps(ps_lines)
    conns, s2 = _parse_ss(ss_lines)
    exes, s3 = _parse_exes(exe_lines)
    notes = _skipped_note(s1 + s2 + s3)
    if ignored:
        notes.append("ignored section(s): " + ", ".join(sorted(set(ignored))))

    flags = _exe_flags(exes, procs) + _proc_flags(procs, exes) + _ss_flags_joined(conns, exes, procs)
    for arg, lines in maps_blocks:
        pid = int(arg.split()[0]) if arg.split() and arg.split()[0].isdigit() else opts.pid
        maps, skipped = _parse_maps(lines)
        if skipped:
            notes.append(f"maps {pid or '?'}: {skipped} malformed line(s) skipped")
        for f in _maps_flags(pid, maps):
            if pid in procs:
                f["subject"] = f["subject"].replace(f"pid {pid}", f"pid {_label(pid, procs)}", 1)
            flags.append(f)

    by_pid: dict[int, list[dict]] = defaultdict(list)
    for c in conns:
        for p in c["procs"]:
            by_pid[p["pid"]].append(c)
    flagged: dict[int, set[str]] = defaultdict(set)
    for f in flags:
        if f["pid"] is not None:
            flagged[f["pid"]].add(f["rule"])
    interesting = sorted(set(flagged) | set(by_pid))

    rows = []
    for pid in interesting:
        p = procs.get(pid)
        cs = by_pid.get(pid, [])
        name = p["name"] if p else next((x["name"] for c in cs for x in c["procs"] if x["pid"] == pid), "?")
        rows.append({
            "pid": pid, "ppid": p["ppid"] if p else None, "user": p["user"] if p else None,
            "age_s": p["age_s"] if p else None, "name": name, "exe": exes.get(pid),
            "cmd": p["args"] if p else None, "chain": _chain(pid, procs),
            "listen": sorted({f"{c['proto']} {c['local']}" for c in cs if c["state"] in ("LISTEN", "UNCONN")}),
            "established": sorted({f"{c['local']} -> {c['peer']}" for c in cs
                                   if c["state"] in ("ESTAB", "ESTABLISHED")}),
            "flags": sorted(flagged.get(pid, ())),
        })

    def render(rs: list[dict]) -> str:
        blocks = []
        for r in rs:
            age = _age(r["age_s"]) if r["age_s"] is not None else "?"
            exe = r["exe"] if r["exe"] else ("(unreadable)" if r["pid"] in exes else "?")
            head = (f"PID {r['pid']}  PPID {r['ppid'] if r['ppid'] is not None else '?'}  "
                    f"USER {r['user'] or '?'}  AGE {age}  EXE {exe}")
            lines = [head, f"  CMD    {_cut(r['cmd']) if r['cmd'] else '?'}"]
            chain = " > ".join(r["chain"]) or f"{r['name']}({r['pid']})"
            lines.append(f"  CHAIN  {chain}")
            if r["listen"]:
                lines.append("  LISTEN " + "; ".join(_more(r["listen"])))
            if r["established"]:
                lines.append("  PEERS  " + "; ".join(_more(r["established"])))
            if r["flags"]:
                lines.append("  FLAGS  " + ", ".join(r["flags"]))
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    return rows, flags, render, notes


def _ss_flags_joined(conns: list[dict], exes: dict[int, str | None], procs: dict[int, dict]) -> list[dict]:
    """ss flags, with the executables known (writable listeners) and process names from ps (shells)."""
    for c in conns:
        for p in c["procs"]:
            if p["pid"] in procs:
                p["name"] = procs[p["pid"]]["name"] or p["name"]
    return _ss_flags(conns, exes)
