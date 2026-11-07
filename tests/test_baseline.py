import inspect
import json
import os
import stat
import sys

import pytest

from shellcraft.modkit import ModuleError


@pytest.fixture
def mod(registry, tmp_path, monkeypatch):
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / "home"))
    run = registry.get("baseline").run
    g = inspect.unwrap(run).__globals__
    clock = {"t": "2026-11-01T10:00:00Z"}
    monkeypatch.setitem(g, "now", lambda: clock["t"])

    class M:
        pass

    m = M()
    m.run = run
    m.g = g
    m.clock = clock
    m.home = tmp_path / "home"
    m.normalize = g["normalize"]
    return m


def diff(mod, text, check="ps", *extra, host="web1"):
    return mod.run(["diff", host, check, *extra], text)


def test_first_run_learns_and_flags_nothing(mod):
    out = diff(mod, "a\nb\nc\n", "cron")
    assert out == "LEARNING web1 cron: recorded 3 item(s); nothing is flagged on a first run\n"


def test_new_and_gone_then_unchanged(mod):
    diff(mod, "a\nb\n", "cron")
    out = diff(mod, "b\nc\n", "cron")
    assert out == "NEW (1)\n+ c\nGONE (1)\n- a\nweb1 cron: 1 new, 1 gone, 1 unchanged, 0 accepted\n"
    assert diff(mod, "b\nc\n", "cron") == "web1 cron: 0 new, 0 gone, 2 unchanged, 0 accepted\n"
    assert "SAME (2)\n= b\n= c" in diff(mod, "b\nc\n", "cron", "--show-same")


def test_returned_item(mod):
    diff(mod, "a\nb\n", "cron")
    mod.clock["t"] = "2026-11-02T10:00:00Z"
    diff(mod, "b\n", "cron")
    mod.clock["t"] = "2026-11-03T10:00:00Z"
    out = diff(mod, "a\nb\n", "cron")
    assert "+ a  (returned; first seen 2026-11-01)" in out
    assert "1 new" in out


def test_gone_item_expires_after_30_days(mod):
    diff(mod, "a\nb\n", "cron")
    mod.clock["t"] = "2026-11-02T10:00:00Z"
    diff(mod, "b\n", "cron")
    mod.clock["t"] = "2026-12-20T10:00:00Z"
    out = diff(mod, "a\nb\n", "cron")
    assert "+ a\n" in out and "returned" not in out


def test_accept_hides_new(mod):
    diff(mod, "a\n", "cron")
    assert mod.run(["accept", "web1", "cron", "b"], "") == "web1 cron: accepted 1 item(s)\n"
    out = diff(mod, "a\nb\n", "cron")
    assert out == "web1 cron: 0 new, 0 gone, 1 unchanged, 1 accepted\n"
    assert mod.run(["accept", "web1", "cron"], "c\nd\n").endswith("accepted 2 item(s)\n")
    with pytest.raises(ModuleError, match="accept needs items"):
        mod.run(["accept", "web1", "cron"], "")


def test_accept_before_first_run_still_learns(mod):
    mod.run(["accept", "web1", "cron", "a"], "")
    assert diff(mod, "a\nb\n", "cron").startswith("LEARNING web1 cron: recorded 2")
    assert diff(mod, "a\nb\nc\n", "cron").startswith("NEW (1)\n+ c")


def test_no_store_leaves_store_untouched(mod):
    diff(mod, "a\n", "cron")
    path = mod.home / "baselines" / "web1" / "cron.json"
    before = path.read_bytes()
    mod.clock["t"] = "2026-11-05T00:00:00Z"
    assert "+ b" in diff(mod, "a\nb\n", "cron", "--no-store")
    assert path.read_bytes() == before
    assert "+ b" in diff(mod, "a\nb\n", "cron")  # still new, was not stored


def test_first_run_no_store_stores_nothing(mod):
    diff(mod, "a\n", "cron", "--no-store")
    assert not (mod.home / "baselines").exists()


def test_put_show_forget_list(mod):
    assert mod.run(["list"], "") == "no baselines stored\n"
    mod.run(["put", "web1", "cron"], "a\nb\n")
    mod.run(["put", "web1", "ps"], "1 0 root 5 /sbin/init\n")
    mod.run(["put", "db1", "cron"], "x\n")
    assert mod.run(["list"], "") == "db1: cron (1)\nweb1: cron (2), ps (1)\n"
    assert json.loads(mod.run(["list", "-o", "json"], ""))["hosts"]["web1"] == {"cron": 2, "ps": 1}
    shown = mod.run(["show", "web1"], "")
    assert "cron  2 item(s)  updated 2026-11-01T10:00:00Z" in shown
    detail = mod.run(["show", "web1", "cron"], "")
    assert detail.splitlines()[1:] == ["a", "b"]
    assert json.loads(mod.run(["show", "web1", "cron", "-o", "json"], ""))["items"][0]["item"] == "a"
    assert "web1 cron: forgotten" in mod.run(["forget", "web1", "cron"], "")
    assert "forgot 1 check" in mod.run(["forget", "web1"], "")
    assert mod.run(["list"], "") == "db1: cron (1)\n"
    with pytest.raises(ModuleError, match="nothing stored"):
        mod.run(["forget", "web1"], "")
    with pytest.raises(ModuleError, match="nothing stored"):
        mod.run(["show", "web1"], "")


def test_put_replaces_silently_and_second_diff_is_clean(mod):
    diff(mod, "a\n", "cron")
    mod.run(["put", "web1", "cron"], "b\n")
    assert diff(mod, "b\n", "cron") == "web1 cron: 0 new, 0 gone, 1 unchanged, 0 accepted\n"


def test_json_diff(mod):
    diff(mod, "a\n", "cron")
    obj = json.loads(diff(mod, "b\n", "cron", "-o", "json"))
    assert obj["new"] == ["b"] and obj["gone"] == ["a"] and obj["learning"] is False


@pytest.mark.parametrize("bad", ["../x", "a/b", "", "x" * 65, ".hidden", "a b", "a\n"])
def test_name_validation(mod, bad):
    with pytest.raises(ModuleError, match="invalid host"):
        mod.run(["diff", bad, "ps"], "x")
    with pytest.raises(ModuleError, match="invalid check"):
        mod.run(["diff", "web1", bad], "x")


def test_name_ok(mod):
    diff(mod, "x\n", "proc-net", host="a.b_c-1")
    assert (mod.home / "baselines" / "a.b_c-1" / "proc-net.json").is_file()


def test_unknown_subcommand_and_usage(mod):
    with pytest.raises(ModuleError, match="invalid choice"):
        mod.run(["frobnicate"], "")
    with pytest.raises(ModuleError, match="usage: baseline diff"):
        mod.run(["diff", "web1"], "")


def test_item_cap_and_truncated_note(mod, monkeypatch):
    monkeypatch.setitem(mod.g, "MAX_ITEMS", 3)
    out = diff(mod, "a\nb\nc\nd\ne\n", "cron")
    assert out.splitlines() == ["NOTE truncated: kept 3 of 5 items",
                                "LEARNING web1 cron: recorded 3 item(s); nothing is flagged on a first run"]
    assert json.loads(diff(mod, "a\nb\nc\nd\n", "cron", "-o", "json"))["truncated"] is True


def test_item_text_capped(mod):
    assert len(mod.normalize("cron", "x" * 900)[0]) == 500


def test_host_and_check_caps(mod, monkeypatch):
    monkeypatch.setitem(mod.g, "MAX_HOSTS", 2)
    monkeypatch.setitem(mod.g, "MAX_CHECKS", 2)
    diff(mod, "a", "c1", host="h1")
    diff(mod, "a", "c2", host="h1")
    diff(mod, "a", "c1", host="h2")
    with pytest.raises(ModuleError, match="2 hosts"):
        diff(mod, "a", "c1", host="h3")
    with pytest.raises(ModuleError, match="2 checks"):
        diff(mod, "a", "c3", host="h1")
    diff(mod, "b", "c1", host="h1")  # an existing check is still fine


def test_corrupt_store_recovers(mod):
    diff(mod, "a\n", "cron")
    path = mod.home / "baselines" / "web1" / "cron.json"
    path.write_text("{not json", encoding="utf-8")
    out = diff(mod, "a\nb\n", "cron")
    assert "corrupt" in out and "cron.json.bad" in out and "LEARNING web1 cron: recorded 2" in out
    assert path.with_suffix(".json.bad").is_file()
    assert diff(mod, "a\nb\n", "cron").startswith("web1 cron: 0 new")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_permissions_owner_only(mod):
    diff(mod, "a\n", "cron")
    path = mod.home / "baselines" / "web1" / "cron.json"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700


# ---- normalisers -----------------------------------------------------------------------------

def test_generic_drops_blank_comments_timestamps_and_collapses(mod):
    text = ("# sshrun web1 cron (untrusted remote output)\n\n"
            "root   /bin/a   2026-11-06T10:00:00Z  \n"
            "# note\n"
            "Thu Nov  6 10:00:00 2026 root /bin/b\n"
            "uptime 12345 s\n")
    items = mod.normalize("whatever", text)
    assert "root /bin/a" in items and "uptime 12345 s" in items
    assert not any("2026" in i or "#" in i for i in items)


def test_sections_become_prefixes(mod):
    text = "## alice\nssh-rsa AAAA x\n## bob\nfoo\n"
    assert mod.normalize("cron", text) == ["alice: ssh-rsa AAAA x", "bob: foo"]


def test_ps_pids_vary_but_items_equal(mod):
    one = "  PID  PPID USER ETIMES ARGS\n  812     1 www    9000 nginx: worker process\n 4021   812 root      12 /tmp/job --port 41234\n"
    two = " 99123     1 www      55 nginx: worker process\n 77001 99123 root       3 /tmp/job --port 55999\n"
    assert mod.normalize("ps", one) == mod.normalize("ps", two)
    assert mod.normalize("ps", one) == ["root /tmp/job --port N", "www nginx: worker process"]


def test_ss_listen_and_ephemeral(mod):
    text = (
        "Netid State Recv-Q Send-Q Local Address:Port Peer Address:Port Process\n"
        'tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=801,fd=3))\n'
        'udp UNCONN 0 0 127.0.0.53%lo:53 0.0.0.0:* users:(("systemd-resolve",pid=500,fd=13))\n'
        'tcp ESTAB 0 0 10.0.0.5:22 203.0.113.9:51022 users:(("sshd",pid=1234,fd=4))\n'
        'tcp ESTAB 0 0 10.0.0.5:44444 198.51.100.7:443 users:(("curl",pid=99,fd=5))\n')
    other = text.replace("51022", "40000").replace("44444", "33333").replace("pid=1234", "pid=2")
    assert mod.normalize("ss", text) == mod.normalize("ss", other)
    assert mod.normalize("ss", text) == [
        "tcp ESTAB *->198.51.100.7:443 curl",
        "tcp ESTAB 22->203.0.113.9:* sshd",
        "tcp LISTEN 0.0.0.0:22 sshd",
        "udp UNCONN 127.0.0.53:53 systemd-resolve",
    ]


def test_ss_without_netid_column(mod):
    assert mod.normalize("ss", "LISTEN 0 4096 [::]:80 [::]:*\n") == ["tcp LISTEN [::]:80 -"]


def test_authorized_keys_never_stores_blob(mod):
    blob = "AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"
    text = f'# header\n## root\nssh-ed25519 {blob} alice@laptop\ncommand="/bin/true" ssh-rsa {blob} bob\n'
    items = mod.normalize("authorized-keys", text)
    assert len(items) == 2
    assert all(blob not in i for i in items)
    assert items[0].startswith('root: command="/bin/true" ssh-rsa SHA256:')
    assert items[1].startswith("root: ssh-ed25519 SHA256:") and items[1].endswith("alice@laptop")
    diff(mod, text, "authorized-keys")
    stored = (mod.home / "baselines" / "web1" / "authorized-keys.json").read_text()
    assert blob not in stored


def test_bpf_json(mod):
    text = json.dumps([
        {"id": 12, "type": "kprobe", "name": "trace_exec", "tag": "abcd", "loaded_at": 1730000000},
        {"id": 13, "type": "cgroup_skb", "name": "egress", "tag": "ef01"},
    ])
    renumbered = text.replace('"id": 12', '"id": 99').replace("abcd", "zzzz")
    assert mod.normalize("bpf-progs", text) == ["cgroup_skb egress", "kprobe trace_exec"]
    assert mod.normalize("bpf-progs", text) == mod.normalize("bpf-progs", renumbered)
    one = json.dumps({"id": 1, "type": "array", "name": "m"})
    assert mod.normalize("bpf-maps", one) == ["array m"]
    links = json.dumps([{"id": 4, "type": "tracing", "prog_id": 9, "attach_type": "trace_fentry"}])
    assert mod.normalize("bpf-links", links) == ["tracing - trace_fentry"]
    assert mod.normalize("bpf-progs", "plain line\n") == ["plain line"]


def test_normalizer_table_has_expected_checks(mod):
    for name in ("generic", "ps", "ss", "cron", "timers", "authorized-keys", "modules", "kprobes", "uprobes",
                 "bpf-progs", "bpf-links", "bpf-maps", "listening", "logins", "exes"):
        assert name in mod.g["NORMALIZERS"]
