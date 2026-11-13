"""hostscan — offline triage of Linux host data (ps, ss, netstat, lsof, exes, maps, bpf, probes and the
persistence checks: packages, setuid files, capabilities, preloads, units, sudoers, sshd, startup files)."""

from __future__ import annotations

import json
import re
import shlex
import tomllib
from collections import Counter, defaultdict
from fnmatch import fnmatchcase
from posixpath import basename

from shellcraft.config import config_dir
from shellcraft.modkit import ArgParser, ModuleError

SUMMARY = "Triage ps/ss/lsof/maps/bpf text and persistence checks from a host: processes, sockets, hooks (offline)"

SUBCOMMANDS = ("ps", "ss", "netstat", "lsof", "maps", "exes", "bpf", "probes", "join", "pkg-verify", "suid",
               "capabilities", "ld-preload", "units", "sudoers-files", "sshd-config", "shell-rc")
OUTPUTS = ("table", "json")
SEVERITIES = ("info", "low", "medium", "high", "critical")
RANK = {s: i for i, s in enumerate(SEVERITIES)}

# Every rule hostscan can raise. The man page lists them all (a test keeps the two in sync).
RULES = [
    {"id": "deleted_exe", "severity": "high", "sub": "exes, lsof, join",
     "description": "The process runs a binary that was deleted from disk (medium when the path is under a system "
                    "directory, because a package upgrade does the same)."},
    {"id": "memfd_exe", "severity": "high", "sub": "exes, lsof, join",
     "description": "The process runs from an anonymous memory file (/memfd:...), a fileless-malware habit."},
    {"id": "writable_exe", "severity": "high", "sub": "ps, exes, lsof, join",
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
    {"id": "listen_unknown", "severity": "low", "sub": "ss, netstat, lsof, join",
     "description": "Listening on all interfaces (0.0.0.0, ::, *) on a port outside the usual set "
                    "(22, 25, 53, 80, 123, 443, 587, 993, 995, 3000, 3306, 5432, 6379, 8080, 8443, 9090, 9100). "
                    "UDP ports of 32768 and up are skipped."},
    {"id": "listen_writable", "severity": "high", "sub": "join",
     "description": "Like listen_unknown, on a port above 1024, and the listener's executable is in a writable "
                    "directory."},
    {"id": "conn_flood", "severity": "medium", "sub": "ss, netstat, lsof, join",
     "description": "One remote address (not loopback) holds 20 or more established connections."},
    {"id": "conn_rare_port", "severity": "medium", "sub": "ss, netstat, lsof, join",
     "description": "Outbound established connection to 4444, 1337, 31337, 6667, 6697, 9001, 9050, 8333 or 3333 "
                    "(high when a shell, nc, ncat or socat owns it)."},
    {"id": "conn_shell_highport", "severity": "medium", "sub": "ss, netstat, lsof, join",
     "description": "A shell holds an outbound connection to a remote port of 30000 or more."},
    {"id": "maps_anon_rwx", "severity": "high", "sub": "maps, join",
     "description": "An anonymous (or heap/stack) mapping is readable, writable and executable. JIT runtimes "
                    "(java, node) do this legitimately."},
    {"id": "maps_file_rwx", "severity": "medium", "sub": "maps, join",
     "description": "A file-backed mapping is readable, writable and executable."},
    {"id": "maps_writable_file", "severity": "high", "sub": "maps, lsof, join",
     "description": "A file under /tmp, /var/tmp, /dev/shm or /run/user is mapped (high when executable, "
                    "medium for a data mapping)."},
    {"id": "maps_deleted_file", "severity": "high", "sub": "maps, lsof, join",
     "description": "A deleted file is mapped (high when executable outside system directories, medium "
                    "otherwise; a library replaced by a package upgrade looks the same)."},
    {"id": "maps_memfd", "severity": "high", "sub": "maps, lsof, join",
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
    {"id": "lsof_deleted_file", "severity": "low", "sub": "lsof",
     "description": "A process holds a deleted file open (not its executable or a mapping, which have their own "
                    "rules). Logs removed by logrotate look the same; /memfd: files are ignored here."},
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
    {"id": "pkg_binary_changed", "severity": "high", "sub": "pkg-verify",
     "description": "A packaged program or library (under /bin, /sbin, /usr/bin, /usr/sbin, /lib*, /usr/lib* or "
                    "/usr/libexec) no longer matches its package digest (critical for sshd, ssh, login, sudo, su, "
                    "passwd, a shell, a PAM module, libc or the loader). Changed config files are only rows."},
    {"id": "pkg_missing", "severity": "medium", "sub": "pkg-verify",
     "description": "A packaged program or library is missing from disk (high for the same critical files)."},
    {"id": "suid_writable_dir", "severity": "high", "sub": "suid",
     "description": "A setuid or setgid file is under /tmp, /var/tmp, /dev/shm, /run/user or /home (critical when "
                    "it is setuid and owned by root)."},
    {"id": "suid_interpreter", "severity": "high", "sub": "suid",
     "description": "A shell, an interpreter or a tool that can start one (find, vim, less, awk, env, cp, tar, "
                    "docker, systemctl, ...) is setuid (critical when owned by root)."},
    {"id": "cap_dangerous", "severity": "high", "sub": "capabilities",
     "description": "A file carries a capability that leads to root: cap_setuid, cap_setgid, cap_sys_admin, "
                    "cap_sys_ptrace, cap_sys_module, cap_dac_override, cap_dac_read_search, cap_chown, cap_fowner, "
                    "cap_setfcap, cap_sys_rawio, cap_bpf, cap_perfmon, or all of them (critical when the file is "
                    "an interpreter or in a writable directory)."},
    {"id": "ld_preload_set", "severity": "high", "sub": "ld-preload",
     "description": "/etc/ld.so.preload names a library, so it is loaded into every program (critical when the "
                    "library is in a writable or hidden directory)."},
    {"id": "ld_path_writable", "severity": "high", "sub": "ld-preload",
     "description": "The library search path in /etc/ld.so.conf.d has a relative directory or one under /tmp, "
                    "/var/tmp, /dev/shm, /run/user or /home."},
    {"id": "unit_writable_exec", "severity": "high", "sub": "units",
     "description": "A systemd unit starts a program from /tmp, /var/tmp, /dev/shm, /run/user or a hidden "
                    "directory (medium when a system unit starts one from /home). A user unit that runs a file "
                    "from its own home is normal."},
    {"id": "unit_download_exec", "severity": "high", "sub": "units",
     "description": "A unit's command downloads and runs code or opens a reverse shell (curl or wget into a shell, "
                    "base64 -d into a shell, /dev/tcp, nc -e, inline Python with sockets, mkfifo with nc)."},
    {"id": "sudoers_nopasswd_all", "severity": "high", "sub": "sudoers-files",
     "description": "A user or group may run every command as root without a password (critical when the rule is "
                    "for ALL; medium for `Defaults !authenticate`, which turns the password prompt off)."},
    {"id": "sudoers_writable_cmd", "severity": "high", "sub": "sudoers-files",
     "description": "A sudo rule allows a command under /tmp, /var/tmp, /dev/shm, /run/user or /home, or allows a "
                    "shell, an interpreter or a tool that can start one (vim, less, find, awk, env, tee, cp, tar, "
                    "...) without a password."},
    {"id": "sshd_root_login", "severity": "medium", "sub": "sshd-config",
     "description": "PermitRootLogin is yes outside a Match block, so root can log in with a password."},
    {"id": "sshd_empty_passwords", "severity": "high", "sub": "sshd-config",
     "description": "PermitEmptyPasswords is yes (medium inside a Match block)."},
    {"id": "sshd_keys_command", "severity": "medium", "sub": "sshd-config",
     "description": "AuthorizedKeysCommand runs a program to supply login keys (low when it is under /usr or /opt, "
                    "like sss_ssh_authorizedkeys; high under a temp, home or hidden directory), or "
                    "AuthorizedKeysFile reads keys from a temp or hidden directory (high)."},
    {"id": "rc_download_exec", "severity": "high", "sub": "shell-rc",
     "description": "A shell startup file downloads and runs code, opens a reverse shell, runs a file from /tmp, "
                    "/var/tmp, /dev/shm or /run/user, or starts one from a hidden directory in the background "
                    "(also inside an alias, PROMPT_COMMAND or a trap)."},
    {"id": "rc_ld_preload", "severity": "high", "sub": "shell-rc",
     "description": "A startup file sets LD_PRELOAD (medium when the library is in a system library directory) or "
                    "LD_AUDIT, or adds a temp directory to LD_LIBRARY_PATH."},
    {"id": "rc_alias_hijack", "severity": "medium", "sub": "shell-rc",
     "description": "A startup file aliases or redefines sudo, su, ssh, scp, passwd, gpg, doas or login (high when "
                    "the alias points into a temp or hidden directory). An alias to the command itself with plain "
                    "switches, like `alias sudo='sudo '`, is normal."},
]
RULE_SEVERITY = {r["id"]: r["severity"] for r in RULES}

KNOWN_PORTS = {22, 53, 80, 443, 123, 25, 587, 993, 995, 3306, 5432, 6379, 8080, 8443, 9090, 9100, 3000}
RARE_PORTS = {4444, 1337, 31337, 6667, 6697, 9001, 9050, 8333, 3333}
KNOWN_OWNERS = ("systemd", "cilium", "bpftrace", "falco", "tetragon", "datadog", "pixie", "otelcol")
WRITABLE_DIRS = ("/tmp/", "/var/tmp/", "/dev/shm/", "/run/user/")
USER_DIRS = WRITABLE_DIRS + ("/home/",)  # where a user can put a file: the persistence checks count /home too
SYSTEM_LIB_DIRS = ("/usr/lib", "/lib", "/usr/local/lib", "/opt/")
SYSTEM_DIRS = ("/usr/", "/bin/", "/sbin/", "/lib", "/opt/")
SHELLS = {"sh", "bash", "dash", "zsh", "ash"}
NETTOOLS = {"nc", "ncat", "socat", "netcat"}
WEB_DB = {"nginx", "apache2", "httpd", "tomcat", "java", "node", "mysqld", "mariadbd", "postgres", "lighttpd", "caddy"}
INTERPRETER = re.compile(r"(python|perl|ruby|java|node|nodejs|php|lua|bash|sh|dash|zsh|ash|busybox|dotnet|mono"
                         r"|env|pwsh)([\d.\-].*)?$")
ALIASES = {"init": "systemd"}
# Tools that hand out a shell or any file once they run with more rights (setuid, a capability, sudo).
ESCAPES = {"find", "vim", "vi", "view", "nano", "ed", "emacs", "less", "more", "man", "awk", "gawk", "mawk", "nawk",
           "sed", "tee", "cp", "tar", "dd", "nmap", "gdb", "strace", "xargs", "nc", "ncat", "netcat", "socat"}
SUID_ESCAPES = ESCAPES | {"docker", "systemctl", "mv", "chmod", "chown", "cat", "curl", "wget", "rsync", "make", "git",
                          "base64", "xxd", "timeout", "nice", "watch", "ip", "tcpdump"}
# Hidden directories that tools create and run from: not a hiding place on their own.
HIDDEN_OK = frozenset({".venv", ".virtualenvs", ".local", ".config", ".cargo", ".rustup", ".nvm", ".pyenv", ".rbenv",
                       ".bun", ".deno", ".dotnet", ".asdf", ".volta", ".yarn", ".npm-global", ".nix-profile",
                       ".sdkman", ".conda", ".linuxbrew", ".acme.sh", ".bundle", ".gem", ".go"})
DANGEROUS_CAPS = {"cap_setuid", "cap_setgid", "cap_sys_admin", "cap_sys_ptrace", "cap_sys_module", "cap_dac_override",
                  "cap_dac_read_search", "cap_chown", "cap_fowner", "cap_setfcap", "cap_sys_rawio", "cap_bpf",
                  "cap_perfmon"}
# Capabilities distributions ship on these programs (under /usr, /bin or /sbin): a row, not a flag.
CAP_EXPECTED = {
    "newuidmap": {"cap_setuid"}, "newgidmap": {"cap_setgid"}, "suexec": {"cap_setuid", "cap_setgid"},
    "systemd-detect-virt": {"cap_dac_override", "cap_sys_ptrace"},
    "unix_chkpwd": {"cap_dac_override", "cap_dac_read_search"}, "intel_gpu_top": {"cap_perfmon"},
    "krb5_child": {"cap_dac_read_search", "cap_setgid", "cap_setuid"}, "ldap_child": {"cap_dac_read_search"},
    "selinux_child": {"cap_setgid", "cap_setuid"}, "sssd_pam": {"cap_dac_read_search"},
    "snap-confine": {"cap_chown", "cap_dac_override", "cap_dac_read_search", "cap_fowner", "cap_sys_ptrace",
                     "cap_sys_admin", "cap_setuid", "cap_setgid"},
}
SECURITY_CMDS = {"sudo", "su", "ssh", "scp", "passwd", "gpg", "doas", "login"}
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
    parser.add_argument("-q", "--quiet", action="store_true")
    opts = parser.parse_args(args)
    if opts.limit < 1:
        raise ModuleError("hostscan: --limit must be 1 or more")
    if opts.pid is not None and opts.pid < 1:
        raise ModuleError("hostscan: --pid must be a positive process id")

    rules, note = _load_rules()
    notes = [note] if note else []
    handler = {"ps": _do_ps, "ss": _do_ss, "netstat": _do_netstat, "lsof": _do_lsof, "exes": _do_exes,
               "maps": _do_maps, "bpf": _do_bpf, "probes": _do_probes, "join": _do_join, "pkg-verify": _do_pkg,
               "suid": _do_suid, "capabilities": _do_caps, "ld-preload": _do_ldso, "units": _do_units,
               "sudoers-files": _do_sudoers, "sshd-config": _do_sshd, "shell-rc": _do_rc}[opts.subcommand]
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
        out = {"rows": [] if opts.quiet else shown, "flags": [_public(f) for f in kept]}
        if opts.quiet:
            out["row_count"] = len(rows)
        if hidden:
            out["truncated"] = hidden
        if notes:
            out["notes"] = notes
        return json.dumps(out, indent=2, ensure_ascii=False) + "\n"

    lines = [] if opts.quiet else [render(shown) if shown else "(no rows)", ""]
    if hidden and not opts.quiet:
        lines.append(f"... {hidden} more row(s), raise --limit")
    lines += [f"note: {n}" for n in notes]
    lines += [f"FLAG {f['severity']} {f['rule']} {f['subject']}: {f['reason']}" for f in kept]
    count = f" in {len(rows)} row(s)" if opts.quiet else ""
    lines.append(f"{len(kept)} flag(s){count}" if kept else f"no flags{count}")
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

_PS_TIME = re.compile(r"(\d+-)?\d+:\d\d(:\d\d)?$")
_PS_NUM = re.compile(r"\d+(\.\d+)?$")


def _ps_tuned(ln: str) -> dict | None:
    parts = ln.split(None, 4)
    if len(parts) < 4 or not (parts[0].isdigit() and parts[1].isdigit() and parts[3].isdigit()):
        return None
    args = parts[4].strip() if len(parts) == 5 else ""
    return {"pid": int(parts[0]), "ppid": int(parts[1]), "user": parts[2], "age_s": int(parts[3]), "args": args}


def _ps_ef(ln: str) -> dict | None:
    """`ps -ef`: UID PID PPID C STIME TTY TIME CMD. There is no elapsed time, so age_s is None."""
    parts = ln.split(None, 7)
    if len(parts) < 7 or not (parts[1].isdigit() and parts[2].isdigit() and parts[3].isdigit()) \
            or not _PS_TIME.match(parts[6]):
        return None
    return {"pid": int(parts[1]), "ppid": int(parts[2]), "user": parts[0], "age_s": None,
            "args": parts[7].strip() if len(parts) == 8 else ""}


def _ps_aux(ln: str) -> dict | None:
    """`ps aux`: USER PID %CPU %MEM VSZ RSS TTY STAT START TIME COMMAND. It has no parent pid (ppid is None)."""
    parts = ln.split(None, 10)
    if len(parts) < 10 or not (parts[1].isdigit() and _PS_NUM.match(parts[2]) and _PS_NUM.match(parts[3])
                               and parts[4].isdigit() and parts[5].isdigit()) or not _PS_TIME.match(parts[9]):
        return None
    return {"pid": int(parts[1]), "ppid": None, "user": parts[0], "age_s": None,
            "args": parts[10].strip() if len(parts) == 11 else ""}


_PS_FORMATS = {"ef": _ps_ef, "aux": _ps_aux, "tuned": _ps_tuned}


def _ps_header(ln: str) -> str | None:
    toks = ln.split()
    if toks[:4] == ["UID", "PID", "PPID", "C"]:
        return "ef"
    if toks[:3] == ["USER", "PID", "%CPU"]:
        return "aux"
    return None


def _parse_ps(lines: list[str]) -> tuple[dict[int, dict], int]:
    """The tuned form, `ps -ef` or `ps aux`. The header line (if any) names the format, which is tried first;
    every other line falls back to the other forms."""
    hint = next((h for h in map(_ps_header, lines) if h), None)
    order = ([hint] if hint else []) + [f for f in ("ef", "aux", "tuned") if f != hint]
    procs, skipped = {}, 0
    for ln in lines:
        if _ps_header(ln):
            continue
        row = next((r for r in (_PS_FORMATS[f](ln) for f in order) if r), None)
        if not row:
            skipped += 1
            continue
        row["name"] = _proc_name(row["args"])
        procs[row["pid"]] = row
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


def _ps_notes(procs: dict[int, dict]) -> list[str]:
    if procs and all(p["ppid"] is None for p in procs.values()):
        return ["ps aux has no parent pid column: web_child_shell and sshd_child_nc cannot be checked "
                "(use ps -ef or sshrun's ps check)"]
    return []


def _dash(value) -> str:
    return "-" if value is None else str(value)


PS_COLS = [("PID", lambda r: r["pid"]), ("PPID", lambda r: _dash(r["ppid"])), ("USER", lambda r: r["user"]),
           ("AGE", lambda r: _age(r["age_s"]) if r["age_s"] is not None else "-"),
           ("CMD", lambda r: _cut(r["args"]))]


def _do_ps(text: str, opts):
    lines = _flat(text)
    _need(lines, "ps", "the output of sshrun's ps check, `ps -ef` or `ps aux`")
    procs, skipped = _parse_ps(lines)
    if not procs:
        raise _unrecognised("ps", "`ps -ef`, `ps aux` or `ps -eo pid=,ppid=,user=,etimes=,args=` output", skipped)
    rows = [procs[p] for p in sorted(procs)]
    return rows, _proc_flags(procs, {}), lambda rs: _grid(PS_COLS, rs), _skipped_note(skipped) + _ps_notes(procs)


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


def _conn_view(conns: list[dict], notes: list[str]):
    """The rows, flags and table every connection source (ss, netstat, lsof) shares."""
    conns.sort(key=lambda c: (c["proto"], c["state"], c["local"], c["peer"]))
    rows = [{k: c[k] for k in ("proto", "state", "local", "peer")} | {"procs": _proc_label(c)} for c in conns]
    cols = [("PROTO", lambda r: r["proto"]), ("STATE", lambda r: r["state"]), ("LOCAL", lambda r: r["local"]),
            ("PEER", lambda r: r["peer"]), ("PROCESS", lambda r: r["procs"])]
    return rows, _ss_flags(conns, {}), lambda rs: _grid(cols, rs), notes


def _do_ss(text: str, opts):
    lines = _flat(text)
    _need(lines, "ss", "the output of sshrun's ss check")
    conns, skipped = _parse_ss(lines)
    if not conns:
        raise _unrecognised("ss", "`ss -H -tunap` output", skipped)
    return _conn_view(conns, _skipped_note(skipped))


# ── netstat ──────────────────────────────────────────────────────────────────

_NETSTAT_STATE = re.compile(r"[A-Z][A-Z_]+$")
_NETSTAT_NOISE = ("Active Internet", "(Not all processes", "will not be shown", "Proto ")


def _ss_state(state: str) -> str:
    """ss spelling of a TCP state: ESTAB, TIME-WAIT, CLOSE-WAIT."""
    return "ESTAB" if state == "ESTABLISHED" else state.replace("_", "-")


def _conn(proto: str, state: str, local: str, peer: str, procs: list[dict]) -> dict:
    laddr, lport = _split_addr(local)
    raddr, rport = _split_addr(peer)
    return {"proto": proto, "state": state, "local": local, "peer": peer, "laddr": laddr, "lport": lport,
            "raddr": raddr, "rport": rport, "procs": procs}


def _parse_netstat(lines: list[str]) -> tuple[list[dict], int, bool | None]:
    """Rows of `netstat -tunap` (or -antp, -tulnp). The last value says whether a PID/Program column exists
    (None when the input does not show it either way)."""
    conns, skipped, has_pid = [], 0, None
    for ln in lines:
        s = ln.strip()
        if s.startswith("Active UNIX"):
            break  # the unix socket table has a different layout; nothing in it is a connection
        if s.startswith(_NETSTAT_NOISE):
            if s.startswith("Proto "):
                has_pid = "PID/Program" in s
            continue
        parts = s.split(None, 5)
        if len(parts) < 5 or parts[0] not in ("tcp", "udp", "tcp6", "udp6") or not (
                parts[1].isdigit() and parts[2].isdigit()):
            skipped += 1
            continue
        rest = parts[5].split(None, 1) if len(parts) == 6 else []
        state = None
        if rest and _NETSTAT_STATE.match(rest[0]):
            state, rest = rest[0], rest[1:]  # UDP has no state column: the pid/program follows the peer
        cell = rest[0] if rest else None
        if cell is not None and has_pid is None:
            has_pid = True
        procs = []
        m = re.match(r"(\d+)/(.*)$", cell or "")
        if m:
            procs = [{"pid": int(m.group(1)), "name": _proc_name(m.group(2))}]
        if state is None:
            state = "UNCONN" if _split_addr(parts[4])[1] is None else "ESTAB"
        conns.append(_conn(parts[0], _ss_state(state), parts[3], parts[4], procs))
    return conns, skipped, has_pid


def _do_netstat(text: str, opts):
    lines = _flat(text)
    _need(lines, "netstat", "the output of `netstat -tunap`")
    conns, skipped, has_pid = _parse_netstat(lines)
    if not conns:
        raise _unrecognised("netstat", "`netstat -tunap` output (Proto Recv-Q Send-Q Local Address Foreign Address "
                            "State PID/Program name)", skipped)
    notes = _skipped_note(skipped)
    if not has_pid:
        notes.append("no PID/Program name column (netstat run without -p): sockets show no owning process")
    elif not any(c["procs"] for c in conns):
        notes.append("every PID/Program name is empty (netstat not run as root): sockets show no owning process")
    return _conn_view(conns, notes)


# ── lsof ─────────────────────────────────────────────────────────────────────

LSOF_COLS = ["COMMAND", "PID", "USER", "FD", "TYPE", "DEVICE", "SIZE/OFF", "NODE", "NAME"]
_LSOF_NAME = re.compile(r"(\S+?)(?:->(\S+))?(?:\s+\((\w+)\))?$")


def _lsof_net(node: str, typ: str, name: str, pid: int, command: str) -> dict | None:
    proto = node.lower()
    m = _LSOF_NAME.match(name.strip())
    if proto not in ("tcp", "udp") or not m:
        return None
    local, peer, state = m.group(1), m.group(2) or "*:*", m.group(3)
    if typ == "IPv6":
        proto += "6"
    if state:
        state = _ss_state(state)
    elif peer != "*:*":
        state = "ESTAB"
    else:
        state = "UNCONN" if proto.startswith("udp") else "LISTEN"
    return _conn(proto, state, local, peer, [{"pid": pid, "name": command}])


def _parse_lsof(lines: list[str]) -> tuple[list[dict], list[dict], int, int]:
    """Socket rows, other open-file rows, malformed lines and socket rows that name hosts or services."""
    conns, files, skipped, unnumbered = {}, [], 0, 0
    cols = {name: i for i, name in enumerate(LSOF_COLS)}
    for ln in lines:
        toks = ln.split()
        if toks[:3] == ["COMMAND", "PID", "USER"] and "NAME" in toks:
            cols = {name: i for i, name in enumerate(toks)}
            continue
        if toks[0] == "lsof:":
            continue  # lsof's own warnings (can't stat a fuse mount, ...)
        pid_i, type_i, name_i, fd_i = cols["PID"], cols["TYPE"], cols["NAME"], cols["FD"]
        node_i = cols.get("NODE", name_i - 1)
        parts = ln.split(None, name_i)
        if len(parts) <= type_i or not parts[pid_i].isdigit():
            skipped += 1
            continue
        pid, command, typ = int(parts[pid_i]), parts[0], parts[type_i]
        if typ in ("IPv4", "IPv6"):
            c = _lsof_net(parts[node_i], typ, parts[name_i] if len(parts) > name_i else "", pid, command)
            if c:
                if c["lport"] is None:
                    unnumbered += 1
                conns[(c["proto"], c["state"], c["local"], c["peer"], pid, command)] = c
        else:
            m = re.search(r"\s(/\S.*)$", ln)  # the name of a file starts with /; the columns before it never do
            if m:
                files.append({"pid": pid, "command": command, "fd": re.match(r"[A-Za-z]*", parts[fd_i]).group(0),
                              "name": m.group(1).strip()})
    return list(conns.values()), files, skipped, unnumbered


def _lsof_file_flags(files: list[dict]) -> list[dict]:
    """Open files of general `lsof` output: the txt line is the executable, mem and DEL lines are mappings."""
    out = []
    for f in files:
        pid, name, label = f["pid"], f["name"], f"{f['pid']}/{f['command']}"
        deleted = name.endswith(" (deleted)")
        path = name.removesuffix(" (deleted)")
        if f["fd"] == "txt":
            out += _exe_flags({pid: name}, {pid: {"name": f["command"]}})
        elif f["fd"] in ("mem", "DEL"):
            execm = ".so" in basename(path)
            subject = f"{label} {name}"
            if path.startswith("/memfd:"):
                out.append(flag("maps_memfd", subject, f"memfd mapping {name}", "high" if execm else "medium",
                                pid=pid, path=path))
            elif deleted:
                if path == "/dev/zero" or path.startswith(("/SYSV", "/anon_hugepage")):
                    continue
                sev = "high" if execm and not path.startswith(SYSTEM_LIB_DIRS) else "medium"
                out.append(flag("maps_deleted_file", subject, f"deleted file is mapped: {path}", sev, pid=pid,
                                path=path))
            elif _writable(path):
                out.append(flag("maps_writable_file", subject, f"file in a writable directory is mapped: {path}",
                                "high" if execm else "medium", pid=pid, path=path))
        elif deleted and not path.startswith(("/memfd:", "/dev/zero", "/SYSV")):
            out.append(flag("lsof_deleted_file", f"{label} {name}", f"holds a deleted file open: {path}", pid=pid,
                            path=path))
    return out


def _do_lsof(text: str, opts):
    lines = _flat(text)
    _need(lines, "lsof", "the output of `lsof -nP -i`")
    conns, files, skipped, unnumbered = _parse_lsof(lines)
    if not conns and not files:
        raise _unrecognised("lsof", "`lsof -nP -i` output (COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME)",
                            skipped)
    notes = _skipped_note(skipped)
    if unnumbered:
        notes.append(f"{unnumbered} socket row(s) name hosts or services instead of numbers; run lsof with -nP "
                     "so addresses and ports can be checked")
    if files:
        notes.append(f"{len(files)} open-file row(s) from a general lsof were checked for deleted and temp-directory "
                     "files")
    rows, flags, render, notes = _conn_view(conns, notes)
    return rows, flags + _lsof_file_flags(files), render, notes


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
        if body.strip() and body.lstrip()[0] not in "[{":
            if want in ("link", "map"):
                raise ModuleError(f"hostscan bpf: the {want} section is not JSON; expected `bpftool -j {want} show`")
            objs += [("prog", d) for d in _bpf_text_progs(lines)]
            continue
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


_BPF_TEXT_HEAD = re.compile(r"(\d+):\s+(\S+)(?:\s+name\s+(\S+))?(?:\s+tag\s+([0-9a-f]+))?(\s+gpl)?\s*$")
_BPF_TEXT_PIDS = re.compile(r"([^,\s][^,]*?)\((\d+)\)")


def _bpf_text_progs(lines: list[str]) -> list[dict]:
    """Programs from plain `bpftool prog show`, mapped to the fields `bpftool -j prog show` has."""
    progs: list[dict] = []
    for ln in lines:
        m = None if ln[:1].isspace() else _BPF_TEXT_HEAD.match(ln.strip())
        if m:
            d = {"id": int(m.group(1)), "type": m.group(2)}
            if m.group(3):
                d["name"] = m.group(3)
            if m.group(4):
                d["tag"] = m.group(4)
            if m.group(5):
                d["gpl_compatible"] = True
            progs.append(d)
        elif progs and ln[:1].isspace():
            d, text = progs[-1], ln.strip()
            if text.startswith("pids "):
                d["pids"] = [{"pid": int(pid), "comm": comm.strip()} for comm, pid in _BPF_TEXT_PIDS.findall(text[5:])]
                continue
            for key, field in (("loaded_at", "loaded_at"), ("uid", "uid"), ("btf_id", "btf_id")):
                v = re.search(rf"\b{key}\s+(\S+)", text)
                if v:
                    d[field] = int(v.group(1)) if v.group(1).isdigit() else v.group(1)
            for key, field in (("xlated", "bytes_xlated"), ("jited", "bytes_jited"), ("memlock", "bytes_memlock")):
                v = re.search(rf"\b{key}\s+(\d+)B", text)
                if v:
                    d[field] = int(v.group(1))
            v = re.search(r"\bmap_ids\s+([\d,]+)", text)
            if v:
                d["map_ids"] = [int(x) for x in v.group(1).split(",") if x]
    if not progs:
        raise ModuleError("hostscan bpf: input is neither bpftool JSON nor `bpftool prog show` text; expected "
                          "`bpftool -j prog show` or `bpftool prog show` output")
    return progs


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


# ── persistence checks: shared helpers ───────────────────────────────────────

_WRAPPERS = {"env", "nohup", "exec", "setsid", "sudo", "command", "builtin", "nice", "ionice", "timeout", "stdbuf",
             "time", "then", "do", "else", "elif", "if", "while", "until", "!"}
_ASSIGN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\+?=")
_DASH_C = re.compile(r"-[a-zA-Z]*c$")
_SEGMENTS = re.compile(r"\|\||&&|;|\||(?<![<>&])&(?![>&])|[(){}]")
_DOWNLOAD = r"(?:curl|wget|fetch)\b"
_TO_SHELL = r"\|\s*(?:sudo\s+(?:-\S+\s+)*)?(?:\S*/)?(?:ba|da|z|k|a)?sh\b"
_FETCH_EXEC = [
    (re.compile(r"\b" + _DOWNLOAD + r"[^|;&]*" + _TO_SHELL), "pipes a download into a shell"),
    (re.compile(r"\b" + _DOWNLOAD + r"[^|;&]*\|\s*(?:sudo\s+)?(?:\S*/)?(?:python[\d.]*|perl|ruby|php|node)"
                r"\s*(?:-\s*)?(?:$|[;&|)'\"])"), "pipes a download into an interpreter"),
    (re.compile(r"(?:\beval|\bsource|(?:^|[\s;&|])\.|\b(?:(?:ba|da|z|k|a)?sh|python[\d.]*|perl)(?:\s+-\w+)*)"
                r"\s+[\"']?(?:\$\(|`|<\()\s*(?:sudo\s+)?" + _DOWNLOAD), "runs what a download returns"),
    (re.compile(r"\b" + _DOWNLOAD + r".*(?:;|&&)\s*(?:sudo\s+)?chmod\s+(?:[ugoa]*\+[rw]*x|[0-7]?[1357][0-7]{2})\b"),
     "downloads a file and makes it executable"),
    (re.compile(r"\bbase64\s+(?:-d|-D|--decode)\b[^|;&]*" + _TO_SHELL), "decodes base64 text into a shell"),
    (re.compile(r"(?:sh\s+-i\b.*|\d<>\s*|[<>]&\s*)/dev/(?:tcp|udp)/|/dev/(?:tcp|udp)/\S+.*(?:\s0?[<>]&\s*\d|<&\d)"),
     "ties a shell to a /dev/tcp connection"),
    (re.compile(r"(?:^|[\s;&|(])(?:\S*/)?(?:nc|ncat|netcat)(?:\.\w+)?\s(?:[^|;&]*\s)?-(?:[a-zA-Z]*e|-exec|-sh-exec)"
                r"[\s=]"), "starts nc with a program attached to the connection"),
    (re.compile(r"\bpython[\d.]*\s+-\w*c\b.*(?:socket.*(?:connect|subprocess|pty|dup2)|pty\.spawn"
                r"|(?:urllib|urlopen|requests).*\bexec\s*\()"), "runs inline Python that opens a socket or runs "
     "a download"),
    (re.compile(r"\bperl\s+-\w*e\b.*[Ss]ocket.*(?:exec|STDIN|/bin/)|\bphp\s+-r\b.*fsockopen|\bruby\s+-rsocket\b"),
     "runs an inline script that opens a socket"),
    (re.compile(r"\bmkfifo\b.*(?:\b(?:nc|ncat|netcat|telnet)\b|sh\s+-i\b)"), "builds a shell over a named pipe"),
]
_SOCAT = re.compile(r"\bsocat\b(?=.*\b(?:exec|system):)(?=.*\b(?:tcp|udp|openssl|ssl)[\w-]*:)", re.I)


def _under(path: str, dirs: tuple[str, ...]) -> bool:
    return (path.rstrip("/") + "/").startswith(dirs)


def _hidden(path: str) -> str | None:
    """The first hidden directory on a path, the ones tools create (HIDDEN_OK) and .ssh aside."""
    return next((p for p in path.split("/")[:-1] if p.startswith(".") and p not in (".", "..", ".ssh")
                 and p not in HIDDEN_OK), None)


def _risky(base: str) -> bool:
    return bool(INTERPRETER.match(base)) or base in ESCAPES


def _words(cmd: str) -> list[str]:
    try:
        return shlex.split(cmd)
    except ValueError:
        return [w.strip("\"'") for w in cmd.split()]


def _segments(cmd: str) -> list[tuple[str, bool]]:
    """Split a shell line into simple commands, quotes respected: (command, runs in the background)."""
    masked, quote = [], ""
    for ch in cmd:
        if quote:
            masked.append("_")
            quote = "" if ch == quote else quote
        else:
            quote = ch if ch in "\"'" else ""
            masked.append("_" if quote else ch)
    out, start = [], 0
    for m in list(_SEGMENTS.finditer("".join(masked))) + [None]:
        seg = cmd[start: m.start() if m else len(cmd)].strip()
        if seg:
            out.append((seg, bool(m) and m.group(0) == "&"))
        start = m.end() if m else start
    return out


def _run_paths(cmd: str, depth: int = 0) -> list[str]:
    """What one simple command runs: the program, the script handed to an interpreter or sourced, and the programs
    inside a `sh -c '...'` string."""
    toks = _words(cmd)
    while toks and (basename(toks[0]) in _WRAPPERS or toks[0].startswith("-") or _ASSIGN.match(toks[0])
                    or re.fullmatch(r"\d+[smhd]?", toks[0])):
        toks = toks[1:]
    if not toks:
        return []
    out, base, rest = [toks[0]], basename(toks[0]), toks[1:]
    if base in (".", "source"):
        return rest[:1]
    inner = next((rest[i + 1] for i, t in enumerate(rest[:-1]) if _DASH_C.match(t)), None)
    if base in SHELLS and inner is not None:
        if depth < 2:
            out += [p for seg, _ in _segments(inner) for p in _run_paths(seg, depth + 1)]
    elif INTERPRETER.match(base) and inner is None:
        out += [t for t in rest if not t.startswith("-")][:1]
    return out


def _fetch_exec(cmd: str) -> str | None:
    """Say how a command line downloads and runs code or opens a reverse shell, or None. Shared by units and
    shell-rc. A plain download (`curl -o file URL`, `x=$(curl URL)`) is not a hit."""
    for pattern, what in _FETCH_EXEC:
        if pattern.search(cmd):
            return what
    return "attaches a program to a socat connection" if _SOCAT.search(cmd) else None


def _input(text: str, sub: str, what: str, marker=None) -> list[tuple[str, list[str]]]:
    """The input of a persistence check as (section, lines): `## NAME` starts a section when marker(NAME) says so,
    and '' holds the lines before the first one. Blank lines and sshrun's frame are dropped; comments stay for the
    caller. A flat check (no marker) may be empty inside sshrun's frame: the command ran and printed nothing."""
    parts: list[tuple[str, list[str]]] = [("", [])]
    framed = False
    for raw in text.splitlines():
        s = raw.strip()
        if s.startswith(("# sshrun ", "# end sshrun ")):
            framed = True
        elif marker and s.startswith("## ") and marker(s[3:].strip()):
            parts.append((s[3:].strip(), []))
        elif s:
            parts[-1][1].append(s)
    if len(parts) == 1 and not (framed and marker is None):
        _need([ln for ln in parts[0][1] if ln != "#" and not ln.startswith("# ")], sub, what)
    return parts


def _code(lines: list[str]) -> list[str]:
    return [ln for ln in lines if not ln.startswith("#")]


def _worst_first(flags: list[dict]) -> list[dict]:
    return sorted(flags, key=lambda f: -RANK[f["severity"]])


# ── pkg-verify ───────────────────────────────────────────────────────────────

_PKG_LINE = re.compile(r"^(missing|[.?SM5DLUGTP]{8,9})\s+(?:([a-z])\s+)?(/.*)$")
_APK_LINE = re.compile(r"^(\S.*?): -?\d+ - (OK|UNTRUSTED|FAILED)\b")
_PKG_PROGRAM = re.compile(r"^/(usr/)?(s?bin|lib[^/]*)/")
_PKG_CORE = re.compile(r"^(sshd|ssh|login|sudo|su|passwd|bash|sh|dash|pam_.+\.so.*|libpam.*\.so.*|libc\.so.*"
                       r"|libc-[\d.]+\.so|ld-linux.*|ld-[\d.]+\.so)$")
_PKG_TYPES = {"c": "config", "d": "doc", "g": "ghost", "l": "license", "r": "readme"}


def _parse_pkg(lines: list[str]) -> tuple[list[dict], int, list[str]]:
    rows, skipped, notes = [], 0, []
    for ln in lines:
        if ln.startswith("no package manager found"):
            notes.append("the host has no dpkg, rpm or apk, so no file was verified")
            continue
        m = _APK_LINE.match(ln)
        if m:
            state = "ok" if m.group(2) == "OK" else "changed"
            rows.append({"state": state, "marks": m.group(2), "type": "package", "path": m.group(1), "note": ""})
            continue
        m = _PKG_LINE.match(ln)
        if not m:
            skipped += 1
            continue
        marks, attr, path = m.groups()
        state = "missing" if marks == "missing" else "changed" if marks[2] == "5" else "attrs"
        unreadable = re.search(r" \([^()]+\)$", path) if state == "missing" else None
        if unreadable:
            path, state = path[: unreadable.start()], "unreadable"
        kind = _PKG_TYPES.get(attr or "", "config" if path.startswith("/etc/") else "file")
        rows.append({"state": state, "marks": "" if marks == "missing" else marks, "type": kind, "path": path,
                     "note": ""})
    return rows, skipped, notes


def _pkg_flags(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        path, state = r["path"], r["state"]
        if r["type"] == "package":
            if state == "changed":
                r["note"] = "the package failed verification"
                out.append(flag("pkg_binary_changed", path, f"apk reports {path} as {r['marks']}", path=path))
            continue
        if state == "unreadable":
            r["note"] = "could not be read: run the check with sudo"
        elif state == "attrs":
            r["note"] = ("content could not be read: run the check with sudo" if r["marks"][2] == "?"
                         else "only attributes differ (owner, mode, size or link)")
        elif r["type"] != "file":
            r["note"] = f"{r['type']} file, {'edited' if state == 'changed' else 'removed'}: not a flag"
        elif not _PKG_PROGRAM.match(path):
            r["note"] = "not a program or library folder"
        elif path.endswith(".pyc") or "/__pycache__/" in path:
            r["note"] = "compiled Python cache, rebuilt on the host"
        elif state == "changed":
            core = bool(_PKG_CORE.match(basename(path)))
            r["note"] = "content differs from the package"
            out.append(flag("pkg_binary_changed", path, f"{path} no longer matches the digest in its package "
                            f"(verify marks {r['marks']})", "critical" if core else None, path=path))
        elif state == "missing":
            core = bool(_PKG_CORE.match(basename(path)))
            r["note"] = "in a package, not on disk"
            out.append(flag("pkg_missing", path, f"{path} belongs to a package but is missing from disk",
                            "high" if core else None, path=path))
    return out


def _do_pkg(text: str, opts):
    lines = _code(_input(text, "pkg-verify", "the output of sshrun's pkg-verify check (dpkg --verify or rpm -Va)")
                  [0][1])
    rows, skipped, notes = _parse_pkg(lines)
    if not rows and not notes and skipped:
        raise _unrecognised("pkg-verify", "dpkg --verify or rpm -Va lines", skipped)
    flags = _pkg_flags(rows)
    unread = sum(1 for r in rows if "could not be read" in r["note"])
    if unread:
        notes.append(f"{unread} file(s) could not be read without root, so they were not compared: run the check "
                     "with sudo for a full answer")
    rows.sort(key=lambda r: (r["path"], r["state"]))
    cols = [("STATE", lambda r: r["state"]), ("MARKS", lambda r: r["marks"] or "-"), ("TYPE", lambda r: r["type"]),
            ("PATH", lambda r: _cut(r["path"], 70)), ("NOTE", lambda r: r["note"])]
    return rows, flags, lambda rs: _grid(cols, rs), notes + _skipped_note(skipped)


# ── suid ─────────────────────────────────────────────────────────────────────

_SUID_LINE = re.compile(r"^([0-7]{3,5})\s+(\S+)\s+(/.*)$")
_SUID_IMAGE = re.compile(r"/(?:\.local/share|var/lib)/(?:containers|docker)/")  # image layers and volumes


def _parse_suid(lines: list[str]) -> tuple[list[dict], int]:
    rows, skipped = [], 0
    for ln in lines:
        m = _SUID_LINE.match(ln)
        if not m:
            skipped += 1
            continue
        mode = int(m.group(1), 8)
        bits = "+".join(n for n, b in (("setuid", 0o4000), ("setgid", 0o2000)) if mode & b)
        rows.append({"mode": m.group(1), "owner": m.group(2), "path": m.group(3), "bits": bits or "-", "note": ""})
    return rows, skipped


def _suid_flags(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        path, owner, base = r["path"], r["owner"], basename(r["path"])
        setuid = "setuid" in r["bits"]
        notes = []
        if owner != "root" and _SUID_IMAGE.search(path):
            r["note"] = "in container storage, not owned by root"  # rootless podman and docker unpack images here
            continue
        if r["bits"] != "-" and _under(path, USER_DIRS):
            notes.append("in a directory users can write to")
            out.append(flag("suid_writable_dir", path, f"{r['bits']} file owned by {owner} (mode {r['mode']}) in a "
                            "directory users can write to", "critical" if setuid and owner == "root" else None,
                            path=path))
        if setuid and (_risky(base) or base in SUID_ESCAPES):
            notes.append("can start a shell")
            out.append(flag("suid_interpreter", path, f"{base} is setuid {owner} (mode {r['mode']}): whoever runs "
                            f"it can get a shell as {owner}", "critical" if owner == "root" else None, path=path))
        r["note"] = ", ".join(notes)
    return out


def _do_suid(text: str, opts):
    lines = _code(_input(text, "suid", "the output of sshrun's suid check (find -printf '%m %u %p')")[0][1])
    rows, skipped = _parse_suid(lines)
    if not rows and skipped:
        raise _unrecognised("suid", "'MODE OWNER PATH' lines", skipped)
    flags = _suid_flags(rows)
    rows.sort(key=lambda r: r["path"])
    cols = [("MODE", lambda r: r["mode"]), ("OWNER", lambda r: r["owner"]), ("BITS", lambda r: r["bits"]),
            ("PATH", lambda r: _cut(r["path"], 80)), ("NOTE", lambda r: r["note"])]
    return rows, flags, lambda rs: _grid(cols, rs), _skipped_note(skipped)


# ── capabilities ─────────────────────────────────────────────────────────────

_CAP_LINE = re.compile(r"^(/.*?)\s+(?:=\s+)?((?:[a-z0-9_,]*[=+][eip]*(?:\s+|$))+)$")


def _parse_caps(lines: list[str]) -> tuple[list[dict], int, list[str]]:
    rows, skipped, notes = [], 0, []
    for ln in lines:
        if "getcap is not installed" in ln:
            notes.append("getcap is not installed on the host, so file capabilities were not listed")
            continue
        m = _CAP_LINE.match(ln)
        if not m:
            skipped += 1
            continue
        held: set[str] = set()
        for clause in m.group(2).split():
            names, flags_ = re.split(r"[=+]", clause, maxsplit=1)
            if "e" in flags_ or "p" in flags_:
                held |= {n for n in names.split(",") if n} if names not in ("", "all") else {"all"}
        rows.append({"path": m.group(1), "caps": " ".join(m.group(2).split()), "held": sorted(held), "note": ""})
    return rows, skipped, notes


def _cap_flags(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        path, base = r["path"], basename(r["path"])
        held = set(r.pop("held"))
        bad = sorted(held & DANGEROUS_CAPS) or (["all capabilities"] if "all" in held else [])
        if not bad:
            continue
        if path.startswith(("/usr/", "/bin/", "/sbin/")) and set(bad) <= CAP_EXPECTED.get(base, set()):
            r["note"] = "shipped this way by the distribution"
            continue
        worse = _risky(base) or _under(path, USER_DIRS)
        r["note"] = "leads to root"
        out.append(flag("cap_dangerous", path, f"{path} holds {', '.join(bad)} ({r['caps']})"
                        + (": an interpreter with it is a root shell" if _risky(base) else ""),
                        "critical" if worse else None, path=path))
    return out


def _do_caps(text: str, opts):
    lines = _code(_input(text, "capabilities", "the output of sshrun's capabilities check (getcap -r)")[0][1])
    rows, skipped, notes = _parse_caps(lines)
    if not rows and not notes and skipped:
        raise _unrecognised("capabilities", "getcap lines such as '/usr/bin/ping cap_net_raw=ep'", skipped)
    flags = _cap_flags(rows)
    rows.sort(key=lambda r: r["path"])
    cols = [("PATH", lambda r: _cut(r["path"], 70)), ("CAPABILITIES", lambda r: _cut(r["caps"], 70)),
            ("NOTE", lambda r: r["note"])]
    return rows, flags, lambda rs: _grid(cols, rs), notes + _skipped_note(skipped)


# ── ld-preload ───────────────────────────────────────────────────────────────

_LDSO_FILES = ("/etc/ld.so.preload", "/etc/ld.so.conf.d", "/etc/ld.so.conf")


def _do_ldso(text: str, opts):
    parts = _input(text, "ld-preload", "the output of sshrun's ld-preload check", lambda n: n in _LDSO_FILES)
    stray = _code(parts[0][1])
    if len(parts) == 1:
        raise _unrecognised("ld-preload", "the '## /etc/ld.so.preload' and '## /etc/ld.so.conf.d' sections of "
                            "sshrun's ld-preload check", len(stray))
    rows, flags, skipped = [], [], len(stray)
    for source, lines in parts[1:]:
        for ln in lines:
            ln = ln.split("#", 1)[0].strip()
            if ln.startswith("cat: "):
                skipped += 1
                continue
            if source == "/etc/ld.so.preload":
                for lib in re.split(r"[\s:]+", ln):
                    if not lib:
                        continue
                    worse = _under(lib, USER_DIRS) or _hidden(lib)
                    rows.append({"source": source, "entry": lib, "note": "loaded into every program"})
                    flags.append(flag("ld_preload_set", lib, f"/etc/ld.so.preload loads {lib} into every program"
                                      + (", from a directory that is writable or hidden" if worse else ""),
                                      "critical" if worse else None, path=lib))
                continue
            if not ln or ln.split()[0] in ("include", "hwcap"):
                continue
            for entry in re.split(r"[\s:,]+", ln):
                folder = entry.split("=", 1)[0]
                if not folder:
                    continue
                row = {"source": source, "entry": folder, "note": ""}
                rows.append(row)
                if not folder.startswith("/"):
                    row["note"] = "relative"
                    flags.append(flag("ld_path_writable", folder, f"the library search path has the relative "
                                      f"directory {folder}", path=folder))
                elif _under(folder, USER_DIRS):
                    row["note"] = "users can write here"
                    flags.append(flag("ld_path_writable", folder, f"the library search path has {folder}, a "
                                      "directory users can write to", path=folder))
    cols = [("SOURCE", lambda r: r["source"]), ("ENTRY", lambda r: _cut(r["entry"], 80)),
            ("NOTE", lambda r: r["note"])]
    return rows, _worst_first(flags), lambda rs: _grid(cols, rs), _skipped_note(skipped)


# ── units ────────────────────────────────────────────────────────────────────

_UNIT_LINE = re.compile(r"^(\S+\.(?:service|socket|timer|path|target|mount|automount|swap|slice|scope))\s+(\S+)"
                        r"(?:\s+\S+)?$")
_UNIT_EXEC = re.compile(r"^(/.+?\.service):(Exec\w+)=(.*)$")
_USER_UNIT = re.compile(r"^(/home/[^/]+|/root)/\.config/systemd/user/")


def _parse_units(parts: list[tuple[str, list[str]]]) -> tuple[list[dict], int]:
    states, execs, skipped = {}, {}, 0
    for section, lines in parts:
        for ln in _code(lines):
            m = _UNIT_EXEC.match(ln)
            if m:
                file, key, command = m.group(1), m.group(2), m.group(3).strip().lstrip("@-:+!").strip()
                home = _USER_UNIT.match(file)
                ident = ("user" if home else "system", basename(file), key, command)
                if ident not in execs or len(file) < len(execs[ident]["file"]):
                    execs[ident] = {"unit": basename(file), "state": "", "scope": ident[0], "exec": key,
                                    "command": command, "file": file, "home": home.group(1) if home else ""}
                continue
            m = _UNIT_LINE.match(ln) if not section else None
            if m:
                states[m.group(1)] = m.group(2)
            else:
                skipped += 1
    rows = list(execs.values())
    for r in rows:
        r["state"] = states.get(r["unit"], "-") if r["scope"] == "system" else "user"
    listed = {r["unit"] for r in rows if r["scope"] == "system"}
    rows += [{"unit": u, "state": s, "scope": "system", "exec": "", "command": "", "file": "", "home": ""}
             for u, s in states.items() if u not in listed]
    rows.sort(key=lambda r: (r["scope"], r["unit"], r["exec"], r["command"]))
    return rows, skipped


def _unit_flags(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        command, subject = r["command"], f"{r['file']} {r['exec']}"
        home = r.pop("home")
        if not command:
            continue
        what = _fetch_exec(command)
        if what:
            out.append(flag("unit_download_exec", subject, f"{r['exec']} {what}: {_cut(command, 100)}",
                            path=r["file"]))
        for seg, _ in _segments(command):
            for p in _run_paths(seg):
                if not p.startswith("/"):
                    continue
                if _under(p, WRITABLE_DIRS):
                    out.append(flag("unit_writable_exec", subject, f"{r['exec']} runs {p}, a file in a directory "
                                    "any user can write to", path=p))
                elif home and p.startswith(home + "/"):
                    continue
                elif _hidden(p):
                    out.append(flag("unit_writable_exec", subject, f"{r['exec']} runs {p}, a file in the hidden "
                                    f"directory {_hidden(p)}", path=p))
                elif _under(p, ("/home/",)) and r["scope"] == "system":
                    out.append(flag("unit_writable_exec", subject, f"a system unit runs {p} from a home directory, "
                                    "where its owner can replace it", "medium", path=p))
    return _worst_first(out)


def _do_units(text: str, opts):
    parts = _input(text, "units", "the output of sshrun's units check", lambda n: n.lower() == "execstart")
    rows, skipped = _parse_units(parts)
    if not rows and skipped:
        raise _unrecognised("units", "systemctl list-unit-files rows or 'FILE:ExecStart=COMMAND' lines", skipped)
    flags = _unit_flags(rows)
    cols = [("UNIT", lambda r: _cut(r["unit"], 44)), ("STATE", lambda r: r["state"]), ("EXEC", lambda r: r["exec"]
            or "-"), ("COMMAND", lambda r: _cut(r["command"], 90) or "-")]
    return rows, flags, lambda rs: _grid(cols, rs), _skipped_note(skipped)


# ── sudoers-files ────────────────────────────────────────────────────────────

_SUDOERS_FILES = ("/etc/sudoers", "/etc/sudoers.d")
_SUDO_TAG = re.compile(r"^(NOPASSWD|PASSWD|NOEXEC|EXEC|SETENV|NOSETENV|LOG_INPUT|NOLOG_INPUT|LOG_OUTPUT|NOLOG_OUTPUT"
                       r"|MAIL|NOMAIL|FOLLOW|NOFOLLOW|INTERCEPT|NOINTERCEPT)\s*:\s*")
_SUDO_OPTION = re.compile(r"^(ROLE|TYPE|CWD|CHROOT|TIMEOUT|NOTBEFORE|NOTAFTER|APPARMOR_PROFILE|PRIVS|LIMITPRIVS)"
                          r"=\S+\s+")
_SUDO_ALIAS = re.compile(r"([A-Z][A-Z0-9_]*)\s*=\s*(.*?)(?=\s*:\s*[A-Z][A-Z0-9_]*\s*=|$)")
_SUDO_ALWAYS = {"vim", "vi", "view", "nano", "ed", "emacs", "less", "more", "man"}  # a shell whatever the arguments


def _split_commas(text: str) -> list[str]:
    out, depth, cur = [], 0, ""
    for i, ch in enumerate(text):
        depth += (ch == "(") - (ch == ")")
        if ch == "," and depth == 0 and text[i - 1: i] != "\\":
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    return [c for c in out + [cur.strip()] if c]


def _sudoers_lines(lines: list[str]) -> list[str]:
    out, pending = [], ""
    for ln in lines:
        ln = pending + ln
        if ln.endswith("\\"):
            pending = ln[:-1].rstrip() + " "
            continue
        pending = ""
        if ln.startswith("#") and not re.match(r"#(include(dir)?\s|\d)", ln):
            continue
        ln = re.sub(r"\s#(?!\d).*$", "", ln).strip()
        if ln:
            out.append(ln)
    return out + ([pending.strip()] if pending.strip() else [])


def _sudo_grants(spec: str, aliases: dict[str, list[str]]) -> list[tuple[bool, bool, str]]:
    """The command list of one rule as (runs as root, without a password, command), aliases expanded."""
    out, runas, nopasswd = [], None, False
    for item in _split_commas(spec):
        if item.startswith("("):
            runas, _, item = item[1:].partition(")")
            item = item.strip()
        while True:
            m = _SUDO_TAG.match(item)
            if m:
                nopasswd = {"NOPASSWD": True, "PASSWD": False}.get(m.group(1), nopasswd)
            else:
                m = _SUDO_OPTION.match(item)
            if not m:
                break
            item = item[m.end():]
        users = None if runas is None else {u.strip() for u in runas.split(":", 1)[0].split(",")}
        as_root = users is None or bool(users & {"ALL", "root", "#0"})
        for cmd in aliases.get(item, [item]) if item != "ALL" else [item]:
            cmd = _SUDO_TAG.sub("", cmd)
            out += [(as_root, nopasswd, c) for c in aliases.get(cmd, [cmd])]
    return out


def _parse_sudoers(parts: list[tuple[str, list[str]]]) -> tuple[list[dict], list[dict], int]:
    entries, skipped = [], 0
    aliases: dict[str, list[str]] = {}
    for section, raw in parts:
        file = section or "/etc/sudoers"
        for ln in _sudoers_lines(raw):
            head = ln.split(None, 1)[0]
            if head.startswith("Defaults"):
                entries.append((file, "defaults", head[8:].lstrip(":@>!") or "-", ln))
            elif head in ("User_Alias", "Runas_Alias", "Host_Alias", "Cmnd_Alias", "Cmd_Alias"):
                if head.startswith("C"):
                    aliases.update({n: _split_commas(v) for n, v in _SUDO_ALIAS.findall(ln.split(None, 1)[1])})
                entries.append((file, "alias", "-", ln))
            elif head.lstrip("@#") in ("include", "includedir"):
                entries.append((file, "include", "-", ln))
            elif "=" in ln and len(ln.split("=", 1)[0].split()) >= 2:
                entries.append((file, "rule", " ".join(ln.split("=", 1)[0].split()[:-1]), ln))
            else:
                skipped += 1
    rows, flags = [], []
    for file, kind, who, ln in entries:
        row = {"file": file, "kind": kind, "who": who, "text": " ".join(ln.split()), "note": ""}
        rows.append(row)
        if kind == "defaults" and re.search(r"(^|[\s,])!authenticate\b", ln):
            row["note"] = "password prompt off"
            flags.append(flag("sudoers_nopasswd_all", f"{file} Defaults", f"`{row['text']}` turns the sudo password "
                              "prompt off", "medium", path=file))
        if kind != "rule" or {w.strip() for w in who.split(",")} <= {"root"}:
            continue
        for as_root, nopasswd, cmd in _sudo_grants(ln.split("=", 1)[1].strip(), aliases):
            words = cmd.split()
            if words and re.fullmatch(r"sha(224|256|384|512):\S+", words[0]):
                words = words[1:]
            if not as_root or not words or words[0].startswith("!"):
                continue
            prog, base, args = words[0], basename(words[0]), words[1:]
            if prog == "ALL":
                if nopasswd:
                    row["note"] = "every command, no password"
                    flags.append(flag("sudoers_nopasswd_all", f"{file} {who}", f"{who} may run every command as "
                                      f"root without a password: `{row['text']}`",
                                      "critical" if "ALL" in re.split(r"[\s,]+", who) else None, path=file))
            elif _under(prog, USER_DIRS):
                row["note"] = "command in a writable directory"
                flags.append(flag("sudoers_writable_cmd", f"{who} {prog}", f"{who} may run {prog} as root, a file in "
                                  "a directory users can write to", path=prog))
            elif nopasswd and _risky(base) and (base in _SUDO_ALWAYS or not args or any("*" in a for a in args)):
                row["note"] = "can start a root shell"
                flags.append(flag("sudoers_writable_cmd", f"{who} {prog}", f"{who} may run {_cut(cmd, 60)} as root "
                                  f"without a password, and {base} can start a shell or write any file", path=prog))
    return rows, _worst_first(flags), skipped


def _do_sudoers(text: str, opts):
    parts = _input(text, "sudoers-files", "the output of sshrun's sudoers-files check",
                   lambda n: n in _SUDOERS_FILES)
    rows, flags, skipped = _parse_sudoers(parts)
    if not rows and len(parts) == 1 and not skipped:
        _need([], "sudoers-files", "the output of sshrun's sudoers-files check")
    if not rows and skipped:
        raise _unrecognised("sudoers-files", "sudoers rules, Defaults or aliases", skipped)
    cols = [("FILE", lambda r: r["file"]), ("KIND", lambda r: r["kind"]), ("WHO", lambda r: _cut(r["who"], 24)),
            ("LINE", lambda r: _cut(r["text"], 80)), ("NOTE", lambda r: r["note"])]
    return rows, flags, lambda rs: _grid(cols, rs), _skipped_note(skipped)


# ── sshd-config ──────────────────────────────────────────────────────────────

_SSHD_LINE = re.compile(r"^([A-Za-z][A-Za-z0-9]*)(?:\s*=\s*|\s+)(.*)$")
_SSHD_NAMED = re.compile(r"^(/[^\s:]+):(.*)$")
_SSHD_FILES = {"sshd_config": "/etc/ssh/sshd_config", "sshd_config.d": "/etc/ssh/sshd_config.d"}
_SSHD_KEYWORDS = {
    "acceptenv", "addressfamily", "allowagentforwarding", "allowgroups", "allowtcpforwarding", "allowusers",
    "authenticationmethods", "authorizedkeyscommand", "authorizedkeyscommanduser", "authorizedkeysfile", "banner",
    "challengeresponseauthentication", "chrootdirectory", "ciphers", "clientalivecountmax", "clientaliveinterval",
    "compression", "denygroups", "denyusers", "forcecommand", "gatewayports", "gssapiauthentication",
    "gssapicleanupcredentials", "hostbasedauthentication", "hostkey", "ignorerhosts", "include",
    "kbdinteractiveauthentication", "kerberosauthentication", "kexalgorithms", "listenaddress", "logingracetime",
    "loglevel", "macs", "match", "maxauthtries", "maxsessions", "maxstartups", "passwordauthentication",
    "permitemptypasswords", "permitrootlogin", "permittunnel", "permittty", "permituserenvironment", "pidfile",
    "port", "printlastlog", "printmotd", "protocol", "pubkeyauthentication", "strictmodes", "subsystem",
    "syslogfacility", "tcpkeepalive", "usedns", "usepam", "x11forwarding", "x11uselocalhost",
}
_SSHD_KEYS_OK = {"sss_ssh_authorizedkeys", "eic_run_authorized_keys", "google_authorized_keys"}


def _parse_sshd(parts: list[tuple[str, list[str]]]) -> tuple[list[dict], int]:
    rows, skipped = [], 0
    for section, lines in parts:
        file, match = _SSHD_FILES.get(section.lower(), "/etc/ssh/sshd_config"), ""
        for ln in _code(lines):
            named = _SSHD_NAMED.match(ln)  # grep over several files puts FILE: in front of each line
            if named:
                match = match if named.group(1) == file else ""
                file, ln = named.group(1), named.group(2).strip()
                if not ln or ln.startswith("#"):
                    continue
            m = _SSHD_LINE.match(ln)
            if not m:
                skipped += 1
                continue
            value = " ".join(m.group(2).split())
            if len(value) > 1 and value[0] == value[-1] == '"':
                value = value[1:-1]
            if m.group(1).lower() == "match":
                match = "" if value.lower() == "all" else value
            rows.append({"file": file, "keyword": m.group(1), "value": value, "match": match, "note": ""})
    return rows, skipped


def _sshd_flags(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        key, value, file = r["keyword"].lower(), r["value"], r["file"]
        where = f" (inside Match {r['match']})" if r["match"] else ""
        if key == "permitrootlogin" and value.lower() == "yes":
            if r["match"]:
                r["note"] = "only inside the Match block"
                continue
            r["note"] = "root can log in with a password"
            out.append(flag("sshd_root_login", file, f"`{r['keyword']} {value}` in {file} lets root log in over ssh "
                            "with a password", path=file))
        elif key == "permitemptypasswords" and value.lower() == "yes":
            r["note"] = "accounts without a password can log in"
            out.append(flag("sshd_empty_passwords", file, f"`{r['keyword']} {value}` in {file}{where} lets accounts "
                            "with an empty password log in", "medium" if r["match"] else None, path=file))
        elif key == "authorizedkeyscommand" and value.lower() != "none" and value:
            prog = value.split()[0]
            if prog.startswith("/usr/") and basename(prog) in _SSHD_KEYS_OK:
                r["note"] = "a known directory-service helper"
                continue
            bad = not prog.startswith("/") or _under(prog, USER_DIRS) or _hidden(prog)
            severity = "high" if bad else "low" if prog.startswith(("/usr/", "/opt/")) else None
            r["note"] = "a program supplies the login keys"
            out.append(flag("sshd_keys_command", prog, f"`{r['keyword']} {_cut(value, 70)}` in {file}{where}: this "
                            "program decides which keys may log in", severity, path=prog))
        elif key == "authorizedkeysfile":
            for pattern in value.split():
                if pattern.startswith("/") and (_under(pattern, WRITABLE_DIRS) or _hidden(pattern)):
                    r["note"] = "keys read from a temp or hidden directory"
                    out.append(flag("sshd_keys_command", pattern, f"`{r['keyword']} {_cut(value, 70)}` in {file}"
                                    f"{where}: login keys are read from {pattern}", "high", path=pattern))
    return _worst_first(out)


def _do_sshd(text: str, opts):
    parts = _input(text, "sshd-config", "the output of sshrun's sshd-config check or of sshd -T",
                   lambda n: n.lower() in _SSHD_FILES)
    rows, skipped = _parse_sshd(parts)
    if rows and not any(r["keyword"].lower() in _SSHD_KEYWORDS for r in rows):
        raise _unrecognised("sshd-config", "sshd_config settings such as 'PermitRootLogin no'", len(rows) + skipped)
    if not rows and skipped:
        raise _unrecognised("sshd-config", "sshd_config settings such as 'PermitRootLogin no'", skipped)
    flags = _sshd_flags(rows)
    cols = [("FILE", lambda r: r["file"]), ("KEYWORD", lambda r: r["keyword"]), ("VALUE", lambda r: _cut(r["value"],
            70)), ("MATCH", lambda r: _cut(r["match"], 30) or "-"), ("NOTE", lambda r: r["note"])]
    return rows, flags, lambda rs: _grid(cols, rs), _skipped_note(skipped)


# ── shell-rc ─────────────────────────────────────────────────────────────────

_RC_ALIAS = re.compile(r"^alias\s+(?:-g\s+)?(?=\S+=)")
_RC_PAIR = re.compile(r"([\w.:+@-]+)=('[^']*'|\"[^\"]*\"|\S*)")
_RC_FUNC = re.compile(r"^(?:function\s+([\w.:-]+)\s*(?:\(\s*\))?|([\w.:-]+)\s*\(\s*\))\s*\{?")
_RC_LD = re.compile(r"\b(LD_PRELOAD|LD_AUDIT|LD_LIBRARY_PATH)=('[^']*'|\"[^\"]*\"|\S*)")
_RC_INNER = re.compile(r"\bPROMPT_COMMAND\+?=('[^']*'|\"[^\"]*\"|\S+)|\btrap\s+('[^']*'|\"[^\"]*\")\s+\w+"
                       r"|\beval\s+('[^']*'|\"[^\"]*\")")
_RC_SWITCH = re.compile(r"-[A-Za-z0-9]*$|--[a-z][a-z-]*$")
_RC_HELPERS = {"nocorrect", "noglob", "command", "kitty", "kitten", "+kitten", "autossh", "gpg2"}
_RC_HOME = ("/", "~/", "$HOME/", "${HOME}/")


def _rc_exec(line: str, depth: int = 0) -> str | None:
    """Why a startup line counts as running fetched or hidden code, or None."""
    what = _fetch_exec(line)
    if what:
        return what
    segments = _segments(line)
    later = any(re.match(r"disown\b", seg) for seg, _ in segments)
    for seg, background in segments:
        background = background or later or bool(re.match(r"(nohup|setsid)\b", seg))
        for p in _run_paths(seg):
            if not p.startswith(_RC_HOME) or re.search(r"[*?\[]", p):
                continue
            if _under(p, WRITABLE_DIRS):
                return f"runs {p} from a temp directory"
            if background and _hidden(p):
                return f"starts {p} from the hidden directory {_hidden(p)} in the background"
    if depth < 2:
        for m in _RC_INNER.finditer(line):
            inner = _rc_exec(next(g for g in m.groups() if g).strip("\"'"), depth + 1)
            if inner:
                return inner
    return None


def _rc_same_command(value: str) -> bool:
    """True when an alias of a security command still runs that command: itself, sudo or doas in front, plain
    switches. `alias sudo='sudo '` and `alias su='sudo su -'` pass; a new program or an option value does not."""
    words = value.split()
    while words and _ASSIGN.match(words[0]):
        words = words[1:]
    seen = False
    for w in words:
        name = w.lstrip("\\")
        if name.startswith(("/usr/bin/", "/bin/", "/usr/sbin/", "/sbin/", "/usr/local/bin/")):
            name = basename(name)
        if name in SECURITY_CMDS or name in _RC_HELPERS:
            seen = True
        elif not _RC_SWITCH.match(w):
            return False
    return seen


def _rc_file(file: str, lines: list[str]) -> tuple[dict, list[dict]]:
    flags, hits, aliases, functions = [], [], 0, 0
    for ln in _code(lines):
        body = ln
        if _RC_ALIAS.match(ln):
            body = ""
            for name, value in _RC_PAIR.findall(_RC_ALIAS.sub("", ln)):
                aliases += 1
                value = value[1:-1] if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'" else value
                if name not in SECURITY_CMDS:
                    body += value + " ; "
                elif not _rc_same_command(value):
                    odd = any(_under(w, WRITABLE_DIRS) or (w.startswith(_RC_HOME) and _hidden(w))
                              for w in _words(value))
                    flags.append(flag("rc_alias_hijack", f"{file} {name}", f"{file} aliases {name} to "
                                      f"`{_cut(value, 70)}`", "high" if odd else None, path=file))
        m = _RC_FUNC.match(ln)
        if m:
            functions += 1
            name = m.group(1) or m.group(2)
            body = ln[m.end():]
            if name in SECURITY_CMDS:
                flags.append(flag("rc_alias_hijack", f"{file} {name}", f"{file} defines a shell function named "
                                  f"{name}, which runs in place of the real command", path=file))
        for var, value in _RC_LD.findall(ln):
            items = [v for v in re.split(r"[:\s]+", value.strip("\"'")) if v and not v.startswith("$" + var[:2])
                     and v != "${" + var + "}"]
            if var == "LD_LIBRARY_PATH":
                items = [v for v in items if _under(v, WRITABLE_DIRS)]
                if items:
                    flags.append(flag("rc_ld_preload", f"{file} {var}", f"{file} adds the temp directory {items[0]} "
                                      "to LD_LIBRARY_PATH", path=file))
            elif items:
                system = var == "LD_PRELOAD" and all(v.startswith(SYSTEM_LIB_DIRS) for v in items)
                flags.append(flag("rc_ld_preload", f"{file} {var}", f"{file} sets {var}={_cut(' '.join(items), 70)}"
                                  ", loaded into every program the shell starts", "medium" if system else None,
                                  path=file))
        what = _rc_exec(body) if body else None
        if what:
            hits.append(f"{what}: `{_cut(ln, 90)}`")
    if hits:
        more = f" (and {len(hits) - 1} more line(s))" if len(hits) > 1 else ""
        flags.append(flag("rc_download_exec", file, f"{file} {hits[0]}{more}", path=file))
    row = {"file": file, "lines": len(_code(lines)), "aliases": aliases, "functions": functions,
           "note": ", ".join(sorted({f["rule"] for f in flags}))}
    return row, flags


def _do_rc(text: str, opts):
    parts = _input(text, "shell-rc", "the output of sshrun's shell-rc check or a shell startup file",
                   lambda n: n.startswith(("/", "~")))
    rows, flags = [], []
    for section, lines in parts:
        if section or _code(lines):
            row, found = _rc_file(section or "stdin", lines)
            rows.append(row)
            flags += found
    cols = [("FILE", lambda r: _cut(r["file"], 60)), ("LINES", lambda r: r["lines"]), ("ALIASES", lambda r:
            r["aliases"]), ("FUNCTIONS", lambda r: r["functions"]), ("NOTE", lambda r: r["note"] or "-")]
    return rows, _worst_first(flags), lambda rs: _grid(cols, rs), []


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
    notes = _skipped_note(s1 + s2 + s3) + _ps_notes(procs)
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
