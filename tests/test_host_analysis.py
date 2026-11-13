"""sshrun, hostscan and baseline together, the way an analyst agent chains them: a stand-in ssh runs the
check on this machine, so the real `ps`, `ss` and /proc formats go through the parsers, and so does the real
output of the persistence checks (setuid files, sshd settings, units, startup files)."""

import os
import shutil
import stat

import pytest

from shellcraft.pipeline import run_line

FAKE_SSH = """#!/bin/sh
for a; do last="$a"; done
exec sh -c "$last"
"""


@pytest.fixture
def lab(ctx, tmp_path, monkeypatch):
    fake = tmp_path / "fakessh"
    fake.write_text(FAKE_SSH)
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    home = tmp_path / "home"
    home.mkdir()
    (home / "ssh-hosts.toml").write_text("[hosts.web01]\nai = true\n")
    (tmp_path / "ssh_config").write_text("Host web01\n")
    monkeypatch.setenv("SHELLCRAFT_HOME", str(home))
    monkeypatch.setenv("SHELLCRAFT_SSH", str(fake))
    monkeypatch.setenv("SHELLCRAFT_SSH_CONFIG", str(tmp_path / "ssh_config"))
    return ctx


def out(ctx, line):
    from shellcraft.context import to_text

    result = run_line(line, ctx)
    return "" if result is None else to_text(result.output)


@pytest.mark.skipif(shutil.which("ps") is None, reason="needs procps")
def test_real_ps_output_parses_in_hostscan(lab):
    text = out(lab, "sshrun web01 ps | hostscan ps")
    assert "flag" in text.lower()  # a table, then "no flags" or N flag(s)
    assert str(os.getpid()) in text or "PID" in text.upper()


@pytest.mark.skipif(shutil.which("ss") is None, reason="needs iproute2")
def test_real_ss_output_parses_in_hostscan(lab):
    text = out(lab, "sshrun web01 ss | hostscan ss")
    assert "flag" in text.lower()


def test_proc_exes_feeds_hostscan(lab):
    text = out(lab, "sshrun web01 exes | hostscan exes")
    assert "flag" in text.lower()


# The persistence checks that need no sudo and finish fast. Whatever this machine holds, the real output of the
# check must parse: a table and a last line with "no flags" or "N flag(s)", never an error.
@pytest.mark.parametrize("check", ["sshd-config", "capabilities", "ld-preload", "units", "shell-rc"])
def test_persistence_checks_feed_hostscan(lab, check):
    text = out(lab, f"sshrun web01 {check} | hostscan {check}")
    assert "no flags" in text or "flag(s)" in text


FAKE_FIND = """#!/bin/sh
# The real find with the check's own test and -printf, on /usr/bin only: a whole /home can take a minute to walk.
touch {used}
while [ $# -gt 0 ]; do case "$1" in /*) shift ;; *) break ;; esac; done
exec {find} /usr/bin "$@"
"""


@pytest.mark.skipif(shutil.which("find") is None, reason="needs findutils")
def test_real_find_output_parses_in_hostscan_suid(lab, tmp_path, monkeypatch):
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "find").write_text(FAKE_FIND.format(used=tmp_path / "used", find=shutil.which("find")))
    (tools / "find").chmod(0o755)
    monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.environ['PATH']}")
    text = out(lab, "sshrun web01 suid | hostscan suid")
    assert (tmp_path / "used").exists()
    assert "no flags" in text or "flag(s)" in text


def test_persistence_check_quiet_json_chain(lab):
    import json

    data = json.loads(out(lab, "sshrun web01 sshd-config | hostscan sshd-config -o json"))
    assert set(data) >= {"rows", "flags"}
    assert all(f["rule"].startswith("sshd_") for f in data["flags"])


def test_baseline_learns_then_reports_only_what_changed(lab):
    first = out(lab, "sshrun web01 modules | baseline diff web01 modules")
    assert "LEARNING" in first
    second = out(lab, "sshrun web01 modules | baseline diff web01 modules")
    assert "LEARNING" not in second and "0 new" in second
