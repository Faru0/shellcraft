"""sshrun, hostscan and baseline together, the way an analyst agent chains them: a stand-in ssh runs the
check on this machine, so the real `ps`, `ss` and /proc formats go through the parsers."""

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


def test_baseline_learns_then_reports_only_what_changed(lab):
    first = out(lab, "sshrun web01 modules | baseline diff web01 modules")
    assert "LEARNING" in first
    second = out(lab, "sshrun web01 modules | baseline diff web01 modules")
    assert "LEARNING" not in second and "0 new" in second
