"""shellcraft --setup-analyst and --check-analyst: enable hosts, write the MCP entry, install the agents, and a
doctor that says what is wrong. A stand-in ssh runs the probes on this machine; sudo is faked by an env var."""

import json
import os
import stat
import tomllib

import pytest

from shellcraft import analyst_setup as setup_mod
from shellcraft import cli

FAKE_SSH = """#!/bin/sh
for a; do last="$a"; done
[ -n "$FAKESSH_DOWN" ] && { echo "ssh: connect to host port 22: Connection refused" >&2; exit 255; }
case "$last" in
  *"sudo -n true"*)
    echo shellcraft-ok
    if [ "$FAKESSH_SUDO" = ok ]; then echo sudo-ok; exit 0; fi
    echo "sudo: a password is required" >&2; exit 1;;
esac
exec sh -c "$last"
"""


@pytest.fixture
def lab(tmp_path, monkeypatch):
    fake = tmp_path / "fakessh"
    fake.write_text(FAKE_SSH)
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "proj"
    project.mkdir()
    (tmp_path / "cfg").write_text("Host web01\n  HostName 10.0.0.5\nHost db01\nHost edrbox\n")
    monkeypatch.setenv("SHELLCRAFT_HOME", str(home))
    monkeypatch.setenv("SHELLCRAFT_SSH", str(fake))
    monkeypatch.setenv("SHELLCRAFT_SSH_CONFIG", str(tmp_path / "cfg"))
    monkeypatch.delenv("FAKESSH_SUDO", raising=False)
    monkeypatch.delenv("FAKESSH_DOWN", raising=False)
    monkeypatch.chdir(project)
    lab = type("Lab", (), {})()
    lab.home, lab.project, lab.target, lab.tmp = home, project, project / ".claude", tmp_path
    lab.hosts_file = home / "ssh-hosts.toml"

    def setup(*hosts, **kw):
        opts = dict(sudo=False, aggregator=None, root=None, target=lab.target, project=lab.project, force=False,
                    dry_run=False)
        opts.update(kw)
        return setup_mod.setup(list(hosts), **opts)

    lab.setup = setup
    lab.doctor = lambda: setup_mod.doctor(lab.project, lab.target)
    return lab


def statuses(lines):
    return [line.status for line in lines]


def text(lines):
    return "\n".join(line.render() for line in lines)


def test_setup_enables_the_host_and_writes_everything(lab):
    lines = lab.setup("web01")
    data = tomllib.loads(lab.hosts_file.read_text())
    assert data == {"hosts": {"web01": {"ai": True}}}                       # no sudo unless asked
    mcp = json.loads((lab.project / ".mcp.json").read_text())["mcpServers"]
    assert list(mcp) == ["shellcraft"] and mcp["shellcraft"]["args"][-2:] == ["--role", "analyst"]
    for name in ("host-analyst", "edr-analyst", "orchestrator"):
        assert (lab.target / "agents" / f"{name}.md").is_file()
    plan = tomllib.loads((lab.home / "watchplans" / "web01.toml").read_text())
    assert plan["hosts"][0]["name"] == "web01" and "pkg-verify" in plan["hosts"][0]["checks"]
    assert "probes" not in plan["hosts"][0]["checks"]                       # root checks only with sudo
    assert "fix" not in statuses(lines) and "ssh works" in text(lines)


def test_sudo_is_set_only_when_it_is_asked_for_and_works(lab, monkeypatch):
    monkeypatch.setenv("FAKESSH_SUDO", "ok")
    lines = lab.setup("web01", sudo=True)
    assert tomllib.loads(lab.hosts_file.read_text())["hosts"]["web01"] == {"ai": True, "sudo": True}
    assert "sudo -n works" in text(lines)
    plan = tomllib.loads((lab.home / "watchplans" / "web01.toml").read_text())
    assert "probes" in plan["hosts"][0]["checks"] and "sudoers-files" in plan["hosts"][0]["checks"]


def test_sudo_that_needs_a_password_is_not_set(lab):
    lines = lab.setup("web01", sudo=True)
    assert tomllib.loads(lab.hosts_file.read_text())["hosts"]["web01"] == {"ai": True}
    assert "fix" in statuses(lines) and "sshrun --sudoers web01" in text(lines)


def test_what_you_wrote_is_never_rewritten(lab):
    original = "# my hosts\n[hosts.web01]\nai = false   # not yet\nrole = \"web\"\n"
    lab.hosts_file.write_text(original)
    lines = lab.setup("web01", "db01")
    after = lab.hosts_file.read_text()
    assert after.startswith(original)                                        # appended to, not rewritten
    assert "ai = false" in text(lines) and "[hosts.db01]" in after
    assert after.count("[hosts.web01]") == 1


def test_a_second_run_changes_nothing(lab):
    lab.setup("web01")
    snapshot = {p: p.read_text() for p in (lab.hosts_file, lab.project / ".mcp.json",
                                           lab.home / "watchplans" / "web01.toml")}
    lines = lab.setup("web01")
    assert {p: p.read_text() for p in snapshot} == snapshot
    assert not [line for line in lines if line.status == "done"]


def test_a_host_that_is_not_in_the_ssh_config_or_is_down_is_not_enabled(lab, monkeypatch):
    assert "not a Host in" in text(lab.setup("nope"))
    assert "x;y" not in lab.hosts_file.read_text() if lab.hosts_file.exists() else True
    assert "not a host name" in text(lab.setup("x;y"))
    monkeypatch.setenv("FAKESSH_DOWN", "1")
    lines = lab.setup("web01")
    assert "cannot log in" in text(lines)
    assert not lab.hosts_file.exists() or "web01" not in lab.hosts_file.read_text()
    assert not (lab.home / "watchplans" / "web01.toml").exists()


def test_dry_run_writes_nothing(lab):
    lab.setup("web01", dry_run=True)
    assert not lab.hosts_file.exists() and not (lab.project / ".mcp.json").exists()
    assert not lab.target.exists() and not (lab.home / "watchplans").exists()


def test_the_mcp_file_keeps_other_servers_and_guards_a_different_shellcraft(lab):
    mcp = lab.project / ".mcp.json"
    mcp.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}, "shellcraft": {"command": "py",
                                                                                         "args": ["--mcp"]}}}))
    lines = lab.setup("web01")
    assert "different 'shellcraft' server" in text(lines)
    assert json.loads(mcp.read_text())["mcpServers"]["shellcraft"] == {"command": "py", "args": ["--mcp"]}
    lab.setup("web01", force=True)
    servers = json.loads(mcp.read_text())["mcpServers"]
    assert servers["other"] == {"command": "x"} and servers["shellcraft"]["args"][-2:] == ["--role", "analyst"]
    assert (lab.project / ".mcp.json.bak").is_file()


def test_a_broken_mcp_file_is_reported_not_overwritten(lab):
    (lab.project / ".mcp.json").write_text("{not json")
    assert "not valid JSON" in text(lab.setup("web01"))
    assert (lab.project / ".mcp.json").read_text() == "{not json"


def test_an_edited_agent_file_is_left_alone(lab):
    lab.setup("web01")
    agent = lab.target / "agents" / "host-analyst.md"
    agent.write_text(agent.read_text() + "\nmy note\n")
    lines = lab.setup("web01")
    assert "my note" in agent.read_text() and "you edited it" in text(lines)
    assert "host-analyst.md" in text(lines)


def test_kinds_are_inferred_from_real_file_names():
    names = ["ps-2026-11-07T1900.txt", "ps-2026-11-07T1930.txt", "netstat_20261107_1900.log", "lsof-1.txt",
             "readme.txt", "free-2026-11-07.out"]
    assert setup_mod.infer_kinds(names) == {"ps": "ps-*.txt", "netstat": "netstat_*.log", "lsof": "lsof-*.txt",
                                            "free": "free-*.out"}


def test_an_aggregator_is_described_from_what_is_on_it(lab):
    root = lab.tmp / "edr"
    for device in ("192.168.1.2", "192.168.1.3"):
        (root / device).mkdir(parents=True)
        for name in ("ps-2026-11-07T1900.txt", "ss-2026-11-07T1900.txt"):
            (root / device / name).write_text("x\n")
    lines = lab.setup(aggregator="edrbox", root=str(root))
    agg = tomllib.loads((lab.home / "aggregators.toml").read_text())["aggregators"]["edrbox"]
    assert agg["root"] == str(root) and agg["files"] == {"ps": "ps-*.txt", "ss": "ss-*.txt"}
    assert tomllib.loads(lab.hosts_file.read_text())["hosts"]["edrbox"] == {"ai": True}
    plan = tomllib.loads((lab.home / "watchplans" / "edrbox-devices.toml").read_text())
    assert [d["name"] for d in plan["devices"]] == ["192.168.1.2", "192.168.1.3"]
    assert "2 device folder(s)" in text(lines) and "fix" not in statuses(lines)


def test_file_names_and_devices_you_pass_beat_the_guess(lab):
    root = lab.tmp / "edr"
    (root / "192.168.1.2").mkdir(parents=True)
    (root / "192.168.1.2" / "ps-2026-11-07T1900.txt").write_text("x\n")
    lab.setup(aggregator="edrbox", root=str(root), file_args=["ps=proc_*.log", "mem=free_*.out"],
              device_args=["web01=192.168.1.2"], stale_minutes=90)
    agg = tomllib.loads((lab.home / "aggregators.toml").read_text())["aggregators"]["edrbox"]
    assert agg["files"] == {"ps": "proc_*.log", "mem": "free_*.out"} and agg["stale_minutes"] == 90
    assert agg["devices"] == {"web01": "192.168.1.2"}


@pytest.mark.parametrize("kw, message", [
    ({"file_args": ["ps"]}, "expected KIND=GLOB"), ({"file_args": ["ps=a;b"]}, "expected KIND=GLOB"),
    ({"device_args": ["w=../x"]}, "expected NAME=FOLDER"), ({"stale_minutes": 0}, "stale-minutes"),
])
def test_bad_file_device_and_stale_options_are_refused(lab, kw, message):
    with pytest.raises(setup_mod.SetupError, match=message):
        lab.setup(aggregator="edrbox", root="/srv/edr", **kw)
    assert not (lab.home / "aggregators.toml").exists()


def test_file_needs_an_aggregator(lab):
    with pytest.raises(setup_mod.SetupError, match="go with --aggregator"):
        lab.setup("web01", file_args=["ps=x*"])


def test_an_aggregator_needs_a_real_root_and_both_options(lab):
    with pytest.raises(setup_mod.SetupError, match="go together"):
        lab.setup(aggregator="edrbox")
    assert "fix" in statuses(lab.setup(aggregator="edrbox", root="/nonexistent/dir"))
    assert "absolute path" in text(lab.setup(aggregator="edrbox", root="/srv/../etc"))
    assert not (lab.home / "aggregators.toml").exists()


def test_the_doctor_is_happy_after_setup_and_names_what_is_wrong_before(lab):
    before = lab.doctor()
    assert setup_mod.exit_code(before) == 1 and "no host is enabled" in text(before)
    lab.setup("web01")
    after = lab.doctor()
    assert setup_mod.exit_code(after) == 0, text(after)
    assert "web01: ssh works" in text(after)


def test_the_doctor_catches_the_wrong_server_name_and_a_missing_role(lab):
    lab.setup("web01")
    mcp = lab.project / ".mcp.json"
    servers = json.loads(mcp.read_text())["mcpServers"]
    mcp.write_text(json.dumps({"mcpServers": {"shellcraft-analyst": servers["shellcraft"]}}))
    report = text(lab.doctor())
    assert "no server named 'shellcraft'" in report and "shellcraft-analyst" in report
    mcp.write_text(json.dumps({"mcpServers": {"shellcraft": {"command": "py", "args": ["--mcp"]}}}))
    assert "does not use --role analyst" in text(lab.doctor())


def test_the_doctor_reports_an_unreachable_host_and_a_sudo_that_asks_for_a_password(lab, monkeypatch):
    lab.setup("web01")
    lab.hosts_file.write_text("[hosts.web01]\nai = true\nsudo = true\n")
    assert "asks for a password" in text(lab.doctor())
    monkeypatch.setenv("FAKESSH_DOWN", "1")
    assert "ssh does not work" in text(lab.doctor())


def test_the_doctor_notices_stale_agents(lab):
    lab.setup("web01")
    (lab.target / "agents" / "edr-analyst.md").unlink()
    assert "missing or old" in text(lab.doctor())


# ── the command line ────────────────────────────────────────────────────────────────────────────

def test_the_flags_work_from_the_command_line(lab, capsys):
    assert cli.main(["--setup-analyst", "web01", "--project"]) == 0
    out = capsys.readouterr().out
    assert "web01: enabled for the AI" in out and (lab.project / ".mcp.json").is_file()
    assert cli.main(["--check-analyst"]) == 0
    assert "ssh works" in capsys.readouterr().out
    (lab.project / ".mcp.json").unlink()
    assert cli.main(["--check-analyst"]) == 1


def test_the_flags_refuse_company(lab, capsys):
    assert cli.main(["--setup-analyst", "web01", "--mcp"]) == 2
    assert cli.main(["--setup-analyst", "web01", "--check-analyst"]) == 2
    assert cli.main(["--check-analyst", "--install-ai"]) == 2
    capsys.readouterr()


def test_without_a_terminal_it_asks_for_host_names(lab, capsys, monkeypatch):
    with open(os.devnull) as devnull:
        monkeypatch.setattr("sys.stdin", devnull)
        assert cli.main(["--setup-analyst", "--project"]) == 1
    assert "name the hosts" in capsys.readouterr().err
