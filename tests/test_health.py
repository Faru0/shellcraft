"""health: offline checks of free, uptime, vmstat, df, top and kernel-log text. The good samples are real output
(trimmed, private names replaced); the bad ones are hand-written."""

import json
from pathlib import Path

import pytest

from modules import health
from shellcraft.modkit import ModuleError

MD = Path(__file__).resolve().parent.parent / "modules" / "health.md"

FREE_M = """               total        used        free      shared  buff/cache   available
Mem:           31380       13727         830        3378       20662       17652
Swap:           8191        6939        1252
"""
FREE_H = """               total        used        free      shared  buff/cache   available
Mem:            30Gi        13Gi       830Mi       3.3Gi        20Gi        17Gi
Swap:          8.0Gi       6.8Gi       1.2Gi
"""
FREE_OLD = """             total       used       free     shared    buffers     cached
Mem:          7986       7861        125        213        312       2105
-/+ buffers/cache:       5444       2542
Swap:         2047          0       2047
"""
MEMINFO = """MemTotal:       32133612 kB
MemFree:          791956 kB
MemAvailable:   18046124 kB
Buffers:             492 kB
Cached:         20171048 kB
SwapCached:         5992 kB
SwapTotal:       8388604 kB
SwapFree:        1283032 kB
Dirty:              2476 kB
Committed_AS:   37804984 kB
"""
UPTIME = " 16:43:58 up 31 days,  5:47,  1 user,  load average: 0.88, 0.62, 0.49\n"
LOADAVG = "0.88 0.62 0.49 2/2137 1255633\n"
VMSTAT = """procs -----------memory---------- ---swap-- -----io---- -system-- -------cpu-------
 r  b   swpd   free   buff  cache   si   so    bi    bo   in   cs us sy id wa st gu
 2  0 7105572 834644   492 21160808  5   11   217   481 2224    1  1  1 98  0  0  0
 1  0 7105572 814412   492 21173300  0    0     0     0 6336 137414 4 5 92  0  0  0
 4  0 7105572 791956   492 21184852  0    0     0     0 5705 137179 2 4 93  0  0  0
"""
DF_H = """Filesystem                           Size  Used Avail Use% Mounted on
/dev/nvme0n1p3                       930G  284G  644G  31% /
devtmpfs                              16G     0   16G   0% /dev
tmpfs                                 16G  117M   16G   1% /dev/shm
efivarfs                             148K  103K   41K  72% /sys/firmware/efi/efivars
tmpfs                                6.2G  2.4M  6.2G   1% /run
tmpfs                                 16G  4.9G   11G  32% /tmp
/dev/nvme0n1p2                       974M  463M  444M  52% /boot
nas.example.com:/mnt/Pool1/data       22T  146G   22T   1% /mnt/data
"""
DF_PT = """Filesystem                          Type      Size  Used Avail Use% Mounted on
/dev/nvme0n1p3                      btrfs     930G  284G  644G  31% /
devtmpfs                            devtmpfs   16G     0   16G   0% /dev
/dev/loop0                          squashfs  128M  128M     0 100% /snap/core/1
"""
DF_P = """Filesystem                          1024-blocks      Used Available Capacity Mounted on
/dev/nvme0n1p3                        975097856 297070788 674274828      31% /
tmpfs                                  16066804    119636  15947168       1% /dev/shm
"""
DF_I = """Filesystem                               Inodes  IUsed       IFree IUse% Mounted on
/dev/nvme0n1p3                                0      0           0     - /
devtmpfs                                3991199    767     3990432    1% /dev
tmpfs                                   1048576 141795      906781   14% /tmp
/dev/nvme0n1p2                            65536     44       65492    1% /boot
"""
TOP = """top - 16:43:59 up 31 days,  5:47,  1 user,  load average: 0.88, 0.62, 0.49
Tasks: 561 total,   2 running, 558 sleeping,   0 stopped,   1 zombie
%Cpu(s):  3.6 us,  4.8 sy,  0.0 ni, 91.3 id,  0.0 wa,  0.0 hi,  0.3 si,  0.0 st
MiB Mem :  31380.5 total,    815.1 free,  13739.6 used,  20665.3 buff/cache
MiB Swap:   8192.0 total,   1253.0 free,   6939.0 used.  17640.9 avail Mem

    PID USER      PR  NI    VIRT    RES    SHR S  %CPU  %MEM     TIME+ COMMAND
1255545 faru      20   0  276148  42020   4184 S  60.4   0.1   0:02.18 lsof
 243853 faru      20   0   77.7g 357952  74836 S  23.2   1.1      9,17 bambust+
   3046 geoclue   20   0 2503380  24556  15680 S   4.6   0.1   7:21.80 geoclue
"""
PS_AUX = """USER         PID %CPU %MEM    VSZ   RSS TTY      STAT START   TIME COMMAND
faru     1509784  3.1  2.8 9883028 927696 pts/0  Sl+  Oct09 1294:17 claude --dangerously-skip-permissions
root        5936  0.0  2.4 1491328 790900 ?      Ssl  Oct07  36:29 /usr/sbin/dnf5daemon-server
"""
KERNEL_CLEAN = """Nov 07 14:12:47 xwing kernel: wlp1s0: authenticated
Nov 07 14:12:47 xwing kernel: wlp1s0: associated
"""

# hand-written trouble
FREE_BAD = """               total        used        free      shared  buff/cache   available
Mem:            7982        7700          90          10         190         210
Swap:           2047        1900         147
"""
DF_BAD = """Filesystem      Size  Used Avail Use% Mounted on
/dev/sda1        50G   46G  4.0G  92% /
/dev/sdb1       100G   96G  4.0G  96% /var
/dev/sdc1        10G   10G     0 100% /data
/dev/loop3      100M  100M     0 100% /snap/core/9
tmpfs           2.0G  1.9G  100M  95% /run/lock
/dev/sdd1        20G   14G  6.0G  70% /srv
"""
DF_I_BAD = """Filesystem      Inodes   IUsed   IFree IUse% Mounted on
/dev/sda1      3276800 3200000   76800   98% /
/dev/sdb1      3276800    1000 3275800    1% /var
"""
KERNEL_BAD = """[ 1234.5] java invoked oom-killer: gfp_mask=0x140cca(GFP_HIGHUSER_MOVABLE|__GFP_COMP), order=0
[ 1234.6] oom-kill:constraint=CONSTRAINT_NONE,nodemask=(null),cpuset=/,task=postgres,pid=2201,uid=26
[ 1234.7] Out of memory: Killed process 2201 (postgres) total-vm:8123456kB, anon-rss:7000000kB
[ 1234.8] Out of memory: Killed process 3302 (php-fpm) total-vm:123456kB, anon-rss:100000kB
[ 1300.1] blk_update_request: I/O error, dev sda, sector 123456 op 0x0:(READ)
[ 1300.2] EXT4-fs error (device sda1): ext4_find_entry:1455: inode #2: comm ls: reading directory lblock 0
[ 1300.3] sshd[999]: segfault at 0 ip 00007f sp 00007ffe error 4 in libc.so.6
[ 1300.4] INFO: task jbd2/sda1-8:300 blocked for more than 120 seconds.
[ 1300.5] watchdog: BUG: soft lockup - CPU#3 stuck for 22s!
[ 1300.55] BUG: unable to handle page fault for address: 00000000deadbeef
[ 1300.6] Call Trace:
"""
VMSTAT_BAD = """procs -----------memory---------- ---swap-- -----io---- -system-- ------cpu-----
 r  b   swpd   free   buff  cache   si   so    bi    bo   in   cs us sy id wa st
 3  1 900000  10000   100  20000   10   10   100   100 1000 2000 10 10 60 20  0
 4  2 910000   9000   100  19000 5000 8000  4000  6000 1000 2000  5  5 20 70 25
 5  3 920000   8000   100  18000 6000 9000  4000  6000 1000 2000  5  5 20 65 25
"""
TOP_BAD = """top - 10:00:00 up 2 min,  1 user,  load average: 9.00, 5.00, 2.00
Tasks: 300 total,   2 running, 188 sleeping,   0 stopped, 110 zombie
%Cpu(s): 10.0 us, 10.0 sy,  0.0 ni, 45.0 id, 35.0 wa,  0.0 hi,  0.0 si,  0.0 st
MiB Mem :   8000.0 total,    100.0 free,   7000.0 used,    900.0 buff/cache
MiB Swap:   2000.0 total,    100.0 free,   1900.0 used.    300.0 avail Mem

    PID USER      PR  NI    VIRT    RES    SHR S  %CPU  %MEM     TIME+ COMMAND
   4242 bob       20   0 9000000   6.0g   1000 R  99.0  75.0   9:00.00 java
   4343 bob       20   0 9000000   3.0g   1000 S   1.0  45.0   9:00.00 python3
"""


def hl(*args, stdin=""):
    return health.run(list(args), stdin)


def rules_of(out: str) -> set[str]:
    return {ln.split()[2] for ln in out.splitlines() if ln.startswith("FLAG ")}


def flags_of(out: str) -> list[str]:
    return [ln for ln in out.splitlines() if ln.startswith("FLAG ")]


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path))
    return tmp_path


# ── real, healthy samples ────────────────────────────────────────────────────

def test_free_m_and_h_give_the_same_numbers():
    m = hl("mem", stdin=FREE_M)
    assert "memory: 17.2 of 30.6 GiB available (56%), swap 6.8 of 8.0 GiB used (85%)" in m
    assert "swap_high" in rules_of(m) and "mem_low" not in rules_of(m)
    h = hl("mem", stdin=FREE_H)
    assert "memory: 17.0 of 30.0 GiB available (57%)" in h and "swap 6.8 of 8.0 GiB" in h


def test_old_free_uses_the_buffers_cache_line():
    out = hl("mem", stdin=FREE_OLD)
    assert "memory: 2.5 of 7.8 GiB available (32%), swap 0 of 2.0 GiB used (0%)" in out and out.endswith("no flags\n")


def test_old_free_without_the_buffers_line_adds_buffers_and_cache():
    text = FREE_OLD.replace("-/+ buffers/cache:       5444       2542\n", "")
    assert "memory: 2.5 of 7.8 GiB available (32%)" in hl("mem", stdin=text)


def test_meminfo():
    out = hl("mem", stdin=MEMINFO)
    assert "memory: 17.2 of 30.6 GiB available (56%), swap 6.8 of 8.0 GiB used (85%), committed 36.1 GiB" in out
    assert "swap_high" in rules_of(out)


def test_uptime_and_loadavg():
    out = hl("load", "--cpus", "16", stdin=UPTIME)
    assert "load: 0.88 0.62 0.49 on 16 cpus, up 31 days, 1 user" in out and out.endswith("no flags\n")
    assert "load: 0.88 0.62 0.49 on 2 cpus" in hl("load", "--cpus", "2", stdin=LOADAVG)


def test_vmstat_skips_the_since_boot_row():
    out = hl("load", stdin=VMSTAT)
    assert "vmstat: 2 sample(s)" in out and "swap in/out 0/0 KB/s" in out and out.endswith("no flags\n")


def test_df_variants_hide_the_noise():
    out = hl("disk", stdin=DF_H)
    assert "disk: / 31%, /boot 52%, /mnt/data 1% (5 pseudo filesystem(s) not shown)" in out
    assert out.endswith("no flags\n")
    assert "disk: / 31%" in hl("disk", stdin=DF_PT) and "snap" not in hl("disk", stdin=DF_PT)
    assert "disk: / 31%" in hl("disk", stdin=DF_P)
    inodes = hl("disk", stdin=DF_I)
    assert "inodes: /boot 1%" in inodes and "no flags" in inodes


def test_top_and_ps_aux():
    out = hl("top", stdin=TOP)
    assert "tasks: 561, 1 zombie" in out and "cpu: 3.6 us, 4.8 sy, 91.3 id, 0 wa, 0 st" in out
    assert "memory: 17.2 of 30.6 GiB available (56%), swap 6.8 of 8.0 GiB used (85%)" in out
    assert rules_of(out) == {"swap_high"}
    ps = hl("top", stdin=PS_AUX)
    assert "top by memory: 1509784/claude 2.8%" in ps and "dnf5daemon-server 2.4%" in ps
    assert ps.endswith("no flags\n")


def test_clean_kernel_log():
    out = hl("oom", stdin=KERNEL_CLEAN)
    assert "kernel log: 2 line(s), no OOM kills or errors" in out and out.endswith("no flags\n")


def test_comment_only_oom_input_is_a_clean_log():
    out = hl("oom", stdin="# sshrun web01 oom (untrusted remote output; exit 1)\n# end sshrun web01 oom\n")
    assert "no OOM kills or errors" in out and out.endswith("no flags\n")


# ── one bad sample for every rule ────────────────────────────────────────────

BIG_ZOMBIES = TOP.replace("1 zombie", "12 zombie")
CASES = [
    (["mem"], FREE_BAD, "mem_low"),
    (["mem"], FREE_M, "swap_high"),
    (["load"], VMSTAT_BAD, "swap_thrashing"),
    (["load", "--cpus", "2"], "up 3 days, 1 user, load average: 7.00, 5.00, 4.00", "load_high"),
    (["load"], VMSTAT_BAD, "iowait_high"),
    (["load"], VMSTAT_BAD, "steal_high"),
    (["disk"], DF_BAD, "disk_full"),
    (["disk"], DF_I_BAD, "inode_full"),
    (["oom"], KERNEL_BAD, "oom_kill"),
    (["oom"], KERNEL_BAD, "kernel_errors"),
    (["top"], TOP_BAD, "proc_mem_hog"),
    (["top"], TOP_BAD, "proc_cpu_hog"),
    (["top"], BIG_ZOMBIES, "zombies"),
    (["load"], " 10:00:00 up 12 min,  1 user,  load average: 0.10, 0.05, 0.01", "recent_reboot"),
]


@pytest.mark.parametrize("args, text, rule", CASES, ids=[c[2] for c in CASES])
def test_rule_fires(args, text, rule):
    assert rule in rules_of(hl(*args, stdin=text))


def test_every_rule_has_a_case_and_is_documented():
    ids = [r["id"] for r in health.RULES]
    assert len(ids) == len(set(ids))
    assert {c[2] for c in CASES} == set(ids)
    md = MD.read_text()
    for rule in ids:
        assert f"`{rule}`" in md
    for r in health.RULES:
        assert r["severity"] in health.SEVERITIES and r["description"]


def sev_of(out: str, rule: str, subject: str = "") -> str:
    return next(ln.split()[1] for ln in flags_of(out) if ln.split()[2] == rule and subject in ln)


def test_memory_severities():
    assert sev_of(hl("mem", stdin=FREE_BAD), "mem_low") == "high"  # 2.6% available
    medium = FREE_BAD.replace("210", "600")
    assert sev_of(hl("mem", stdin=medium), "mem_low") == "medium"
    assert sev_of(hl("mem", stdin=FREE_BAD), "swap_high") == "medium"  # 93% of swap
    assert sev_of(hl("mem", stdin=FREE_M), "swap_high") == "medium"
    low = FREE_M.replace("6939", "4500").replace("1252", "3691")
    assert sev_of(hl("mem", stdin=low), "swap_high") == "low"


def test_disk_severities_and_ignored_mounts():
    out = hl("disk", stdin=DF_BAD)
    assert sev_of(out, "disk_full", "/:") == "low"
    assert sev_of(out, "disk_full", "/var") == "high"
    assert sev_of(out, "disk_full", "/data") == "critical"
    assert "/snap" not in out and "/srv" not in "".join(flags_of(out))
    assert sev_of(out, "disk_full", "/run/lock") == "high"  # tmpfs counts once it is full
    inodes = hl("disk", stdin=DF_I_BAD)
    assert sev_of(inodes, "inode_full", "/:") == "medium"
    assert sev_of(hl("disk", stdin=DF_I_BAD.replace("98%", "99%")), "inode_full", "/:") == "high"


def test_load_severities_use_the_cpu_count():
    text = "up 3 days, 1 user, load average: 7.00, 5.00, 1.00"
    assert sev_of(hl("load", "--cpus", "2", stdin=text), "load_high") == "medium"  # over 3 per cpu
    assert sev_of(hl("load", "--cpus", "4", stdin=text), "load_high") == "low"
    assert "load_high" not in rules_of(hl("load", "--cpus", "8", stdin=text))
    sustained = "up 3 days, 1 user, load average: 1.00, 3.00, 7.00"
    assert "15 minute load 7.00" in hl("load", "--cpus", "2", stdin=sustained)


def test_cpu_count_comes_from_the_input_and_the_option_wins():
    text = "up 3 days, 1 user, load average: 7.00, 5.00, 4.00\n## cpus\n2\n"
    assert "on 2 cpus" in hl("load", stdin=text)
    assert "on 16 cpus" in hl("load", "--cpus", "16", stdin=text)
    assert "on 8 cpus" in hl("load", stdin="CPU(s):                  8\n" + text.split("\n")[0])
    out = hl("load", stdin=text.split("\n")[0])
    assert "cpu count unknown, assumed 1" in out and "on 1 cpu," in out or "on 1 cpu\n" in out


def test_swap_thrashing_vmstat():
    out = hl("load", stdin=VMSTAT_BAD)
    assert sev_of(out, "swap_thrashing") == "high"
    assert "iowait_high" in rules_of(out) and "steal_high" in rules_of(out)


def test_oom_kill_names_the_process_and_importance():
    out = hl("oom", stdin=KERNEL_BAD)
    assert sev_of(out, "oom_kill", "postgres") == "critical"
    assert sev_of(out, "oom_kill", "php-fpm") == "high"
    assert "pid 2201" in out
    assert len([ln for ln in flags_of(out) if " oom_kill " in ln]) == 2  # the oom-kill: line is not a third kill
    assert sev_of(out, "kernel_errors", "disk I/O errors") == "high"
    assert sev_of(out, "kernel_errors", "filesystem errors") == "high"
    for name in ("segfaults", "hung tasks", "soft lockups", "kernel BUG"):
        assert sev_of(out, "kernel_errors", name) == "medium"
    assert sev_of(out, "kernel_errors", "call traces") == "low"  # next to other findings


def test_a_lone_call_trace_is_medium_and_an_oom_without_names_still_flags():
    assert sev_of(hl("oom", stdin="[ 1.0] Call Trace:\n"), "kernel_errors") == "medium"
    out = hl("oom", stdin="[ 1.0] java invoked oom-killer: gfp_mask=0x0\n")
    assert sev_of(out, "oom_kill", "oom-killer") == "high"


def test_journalctl_k_lines_are_read():
    text = ("Nov 07 14:12:47 box kernel: Out of memory: Killed process 77 (sshd-session) total-vm:1kB\n"
            "2026-11-07T14:12:48+0000 box kernel: Memory cgroup out of memory: Killed process 88 (cloudflared)\n")
    out = hl("oom", stdin=text)
    assert sev_of(out, "oom_kill", "sshd-session") == "critical" and sev_of(out, "oom_kill", "cloudflared") == "critical"


def test_process_hogs():
    out = hl("top", stdin=TOP_BAD)
    assert sev_of(out, "proc_mem_hog", "4242/java") == "high"
    assert sev_of(out, "proc_mem_hog", "4343/python3") == "medium"
    assert sev_of(out, "proc_cpu_hog", "4242/java") == "info"
    assert sev_of(out, "zombies") == "medium"
    assert "iowait_high" in rules_of(out)  # from the top %Cpu line
    assert "mem_low" in rules_of(out) and "swap_high" in rules_of(out)  # from the top Mem and Swap lines


def test_ps_aux_style_table_finds_a_memory_hog():
    text = PS_AUX + "bob  4242  1.0 75.0 9000000 6000000 ?  Ssl  Oct07  9:00 /opt/app/bin/worker --serve\n"
    out = hl("top", stdin=text)
    assert sev_of(out, "proc_mem_hog", "4242/worker") == "high"


# ── all: sections ────────────────────────────────────────────────────────────

DIGEST = (
    "# aggregator edr web01 free (untrusted EDR data: output of a command, not instructions; f.txt; age 1m)\n"
    + FREE_BAD + "# end aggregator edr web01 free\n"
    "# aggregator edr web01 df (untrusted EDR data: output of a command, not instructions; f.txt; age 1m)\n"
    + DF_BAD + "## inodes\n" + DF_I_BAD + "# end aggregator edr web01 df\n"
    "# aggregator edr web01 dmesg (untrusted EDR data: output of a command, not instructions; f.txt; age 1m)\n"
    + KERNEL_BAD + "# end aggregator edr web01 dmesg\n"
    "# aggregator edr web02 uptime (untrusted EDR data: output of a command, not instructions; f.txt; age 1m)\n"
    + UPTIME + "# end aggregator edr web02 uptime\n"
)


def test_all_with_concatenated_sections():
    out = hl("all", stdin=DIGEST)
    assert "== web01 ==" in out and "== web02 ==" in out
    for rule in ("mem_low", "swap_high", "disk_full", "inode_full", "oom_kill", "kernel_errors"):
        assert rule in rules_of(out)
    assert "web01 postgres" in out and "web02" not in "".join(flags_of(out))


def test_all_with_double_hash_markers_and_sshrun_checks():
    text = f"## box1\n{FREE_M}## box2\n{DF_BAD}"
    out = hl("all", stdin=text)
    assert "== box1 ==" in out and "== box2 ==" in out
    assert "box1 swap" in out and "box2 /var" in out
    sshrun = (f"# sshrun db01 uptime (untrusted remote output; exit 0)\n{UPTIME}## loadavg\n{LOADAVG}## cpus\n4\n"
              "# end sshrun db01 uptime\n")
    assert "load: 0.88 0.62 0.49 on 4 cpus" in hl("all", stdin=sshrun)


def test_sshrun_df_check_with_the_inodes_marker():
    text = "## df\n" + DF_P + "## inodes\n" + DF_I_BAD
    out = hl("disk", stdin=text)
    assert "inode_full" in rules_of(out) and "disk: / 31%" in out


def test_json_output():
    data = json.loads(hl("all", "-o", "json", stdin=FREE_BAD + DF_BAD))
    assert {r["kind"] for r in data["rows"]} >= {"memory", "disk"}
    assert {"severity", "rule", "subject", "reason"} <= set(data["flags"][0])
    assert any(f["rule"] == "mem_low" for f in data["flags"])


def test_limit_caps_rows_not_flags():
    out = hl("all", "--limit", "1", stdin=FREE_BAD + DF_BAD)
    assert "more row(s)" in out and "disk_full" in out


# ── clean errors ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("args, text, match", [
    (["frobnicate"], "x", "invalid choice"),
    ([], "x", "required"),
    (["mem"], "", "no input on stdin"),
    (["mem"], "hello world\nnothing here\n", "expected free"),
    (["load"], "hello", "expected uptime"),
    (["disk"], FREE_M, "expected df"),
    (["top"], "hello", "expected top"),
    (["all"], "hello", "expected the output of free"),
    (["oom"], FREE_M, "look like kernel log text"),
    (["mem", "--limit", "0"], FREE_M, "--limit must be 1 or more"),
    (["load", "--cpus", "0"], UPTIME, "--cpus must be 1 or more"),
])
def test_clean_errors(args, text, match):
    with pytest.raises(ModuleError, match=match):
        hl(*args, stdin=text)


def test_hostile_text_is_data_and_is_cut():
    name = "x" * 500 + "\x1b[31m"
    text = f"[ 1.0] Out of memory: Killed process 9 ({name[:60]}\x1b[2J) total-vm:1kB\n"
    out = hl("oom", stdin=text)
    assert "\x1b" not in out and all(len(ln) < 300 for ln in out.splitlines())
    top = ("USER PID %CPU %MEM VSZ RSS TTY STAT START TIME COMMAND\n"
           "bob 5 1.0 80.0 1 1 ? S 10:00 0:01 " + "A" * 2000 + "\n")
    out = hl("top", stdin=top)
    assert len(max(out.splitlines(), key=len)) < 200 and "proc_mem_hog" in out


# ── user rules file ──────────────────────────────────────────────────────────

def rules(home, text):
    (home / "health-rules.toml").write_text(text)


def test_rules_file_overrides_thresholds(home):
    rules(home, "mem_low_pct = 50\ndisk_full_pct = 93\nignore_mounts = ['/boot*']\nignore_processes = ['java']\n")
    assert "mem_low" in rules_of(hl("mem", stdin=FREE_M.replace("17652", "12000")))  # 38% is under 50%
    out = hl("disk", stdin=DF_BAD)  # 92% is now fine; 95% and over still flag
    assert {ln.split()[3] for ln in flags_of(out)} == {"/data:", "/var:", "/run/lock:"}
    assert "/boot" not in hl("disk", stdin=DF_H)
    top = hl("top", stdin=TOP_BAD)
    assert "4343/python3" in top and "4242/java" not in "".join(flags_of(top))


def test_rules_file_lists_extend_important_processes_and_ignore_rules(home):
    rules(home, "important_processes = ['php-fpm']\nignore_rules = ['kernel_errors']\n")
    out = hl("oom", stdin=KERNEL_BAD)
    assert sev_of(out, "oom_kill", "php-fpm") == "critical" and "kernel_errors" not in rules_of(out)


@pytest.mark.parametrize("body, match", [
    ("this is = = not toml", "health-rules.toml"),
    ("bogus = 1", "unknown key 'bogus'"),
    ("mem_low_pct = 'ten'", "mem_low_pct must be a number from 1 to 50"),
    ("mem_low_pct = 90", "mem_low_pct must be a number"),
    ("disk_full_pct = true", "disk_full_pct must be a number"),
    ("ignore_mounts = '/boot'", "ignore_mounts must be a list of strings"),
    ("ignore_rules = ['nope']", "names no rule: nope"),
    ("disk_full_pct = 99\ndisk_high_pct = 95", r"disk_full_pct \(99\) must not be above disk_high_pct \(95\)"),
])
def test_bad_rules_file_is_a_clear_error(home, body, match):
    rules(home, body)
    with pytest.raises(ModuleError, match=match):
        hl("mem", stdin=FREE_M)


def test_missing_rules_file_is_fine(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / "nothing-here"))
    assert hl("mem", stdin=FREE_OLD).endswith("no flags\n")
