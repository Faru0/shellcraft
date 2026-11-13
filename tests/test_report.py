"""report: the status record every save writes, the report file when a round found something, list and show."""

import json
import os
import stat
import sys

import pytest

from shellcraft import fleet
from shellcraft.config import config_dir
from shellcraft.modkit import ModuleError

CLOCK = 1_794_502_400.0  # 2026-11-12T16:53:20Z


@pytest.fixture
def clock(monkeypatch):
    moment = [CLOCK]
    monkeypatch.setattr(fleet, "now", lambda: moment[0])
    return moment


@pytest.fixture
def report(registry, clock):
    module = registry.get("report")
    return lambda *args, stdin="": module.run(list(args), stdin)


def _record(unit="host:web01"):
    return json.loads(fleet.record_path(unit).read_text())


def test_an_ok_round_leaves_a_status_and_no_report(report):
    said = report("save", "host:web01", "--status", "ok", "--summary", "7 checks, no change", "--changed", "0",
                  "--plan", "prod")
    assert said == "host:web01: ok recorded\n"
    assert _record() == {"version": 1, "unit": "host:web01", "time": "2026-11-12T16:53:20Z", "status": "ok",
                         "summary": "7 checks, no change", "changed": 0, "flags": None, "plan": "prod", "report": ""}
    assert not (config_dir() / "reports").exists()


def test_a_warning_writes_a_report_even_without_a_body(report):
    said = report("save", "host:web01", "-s", "warn", "-m", "new listener on port 4444", "--changed", "2",
                  "--flags", "1", "--plan", "prod")
    path = config_dir() / "reports" / "host" / "web01" / "20261112-165320-warn.md"
    assert said == f"host:web01: warn recorded\nreport: {path}\n"
    assert _record()["report"] == str(path)
    assert path.read_text() == ("# host:web01: warn\n\n- When: 2026-11-12 16:53:20 UTC\n- Watch plan: prod\n"
                                "- Changed: 2\n- Flags: 1\n\nnew listener on port 4444\n")


def test_a_body_is_kept_also_when_the_round_was_ok(report):
    said = report("save", "host:web01", "-s", "ok", "-m", "a new kernel, as planned", stdin="## What changed\n- uname\n")
    path = config_dir() / "reports" / "host" / "web01" / "20261112-165320-ok.md"
    assert f"report: {path}\n" in said
    assert path.read_text().endswith("a new kernel, as planned\n\n## What changed\n- uname\n")


def test_secrets_are_redacted_and_the_summary_is_one_line(report):
    report("save", "host:web01", "-s", "warn", "-m", "found  password=hunter2\nin a script",
           stdin="curl -H \"Authorization: Bearer abcdef0123456789abcdef\" api\npassword: s3cr3t-value\n")
    text = fleet.read_report("host:web01")[1]
    assert "hunter2" not in text and "s3cr3t-value" not in text and "abcdef0123456789" not in text
    assert "REDACTED" in text
    assert "\n" not in _record()["summary"] and "hunter2" not in _record()["summary"]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_what_is_written_is_yours_alone(report):
    report("save", "host:web01", "-s", "crit", "-m", "a miner is running")
    home = config_dir()
    for folder in (home / "fleet", home / "reports"):
        assert stat.S_IMODE(os.stat(folder).st_mode) == 0o700
    files = [path for path in home.rglob("*") if path.is_file()]
    assert len(files) == 2 and all(stat.S_IMODE(os.stat(path).st_mode) == 0o600 for path in files)
    assert not [path for path in home.rglob("*") if ".tmp-" in path.name]


def test_the_units_of_a_watch_plan_are_the_names(report):
    for unit in ("host:web01", "device:edr/web01", "device:edr/10.0.0.7", "device:edr/pc:7", "feed:kuma",
                 "func:_inventory"):
        assert report("save", unit, "-s", "ok", "-m", "fine") == f"{unit}: ok recorded\n"
    assert sorted(fleet.records()) == ["device:edr/10.0.0.7", "device:edr/pc:7", "device:edr/web01", "feed:kuma",
                                       "func:_inventory", "host:web01"]
    assert fleet.record_path("device:edr/pc:7") == config_dir() / "fleet" / "device" / "edr" / "pc%3A7.json"


@pytest.mark.parametrize("unit", ["web01", "host:", "host:../x", "host:a/b", "host:a b", "device:edr", "device:edr/",
                                  "device:edr/a/b", "user:root", "host:web01\nx", "feed:../../x"])
def test_anything_else_is_not_a_unit(report, unit):
    with pytest.raises(ModuleError, match="is not a unit"):
        report("save", unit, "-s", "ok", "-m", "fine")
    assert not (config_dir() / "fleet").exists()


def test_what_save_needs(report):
    with pytest.raises(ModuleError, match="save needs a unit and a status"):
        report("save", "host:web01")
    with pytest.raises(ModuleError, match="a summary is needed"):
        report("save", "host:web01", "-s", "ok", "-m", "   ")
    with pytest.raises(ModuleError, match="invalid choice"):
        report("save", "host:web01", "-s", "fine", "-m", "x")
    with pytest.raises(ModuleError, match="changed is a count"):
        report("save", "host:web01", "-s", "ok", "-m", "x", "--changed", "-1")
    with pytest.raises(ModuleError, match="is not a plan name"):
        report("save", "host:web01", "-s", "ok", "-m", "x", "--plan", "../x")
    with pytest.raises(ModuleError, match="usage: report save"):
        report()


def test_a_later_save_replaces_the_status_and_keeps_the_reports(report, clock):
    report("save", "host:web01", "-s", "warn", "-m", "new listener")
    clock[0] += 900
    report("save", "host:web01", "-s", "ok", "-m", "the listener is gone")
    assert (_record()["status"], _record()["time"], _record()["report"]) == ("ok", "2026-11-12T17:08:20Z", "")
    assert [item["status"] for item in fleet.reports("host:web01")] == ["warn"]


def test_two_reports_in_one_second_are_two_files(report):
    for text in ("first", "second"):
        report("save", "host:web01", "-s", "warn", "-m", text)
    names = sorted(path.name for path in fleet.report_dir("host:web01").iterdir())
    assert names == ["20261112-165320-warn-2.md", "20261112-165320-warn.md"]
    assert len(fleet.reports("host:web01")) == 2


def test_list_and_show(report, clock):
    assert report("list") == "no status yet: a round ends each host with `report save`\n"
    assert report("list", "host:web01") == "host:web01: no status yet\n"
    report("save", "host:web01", "-s", "warn", "-m", "new listener", "--changed", "2", "--flags", "1",
           stdin="first body")
    clock[0] += 900
    report("save", "host:web01", "-s", "crit", "-m", "it talks to a known bad address", stdin="second body")
    report("save", "host:db01", "-s", "ok", "-m", "no change", "--changed", "0")
    assert report("list") == (
        "host:db01  ok  2026-11-12T17:08:20Z  no change  (0 changed, 0 report(s))\n"
        "host:web01  crit  2026-11-12T17:08:20Z  it talks to a known bad address  (2 report(s))\n")
    folder = fleet.report_dir("host:web01")
    assert report("list", "host:web01") == (
        "host:web01  crit  2026-11-12T17:08:20Z  it talks to a known bad address\n"
        f"1  2026-11-12T17:08:20Z  crit  {folder / '20261112-170820-crit.md'}\n"
        f"2  2026-11-12T16:53:20Z  warn  {folder / '20261112-165320-warn.md'}\n")
    assert report("show", "host:web01").endswith("second body\n")
    assert report("show", "host:web01", "2").endswith("first body\n")
    with pytest.raises(ModuleError, match=r"host:web01 has 2 report\(s\): 1 is the newest"):
        report("show", "host:web01", "3")
    with pytest.raises(ModuleError, match="no report for host:db01"):
        report("show", "host:db01")
    with pytest.raises(ModuleError, match="show needs a unit"):
        report("show")


def test_old_reports_are_removed_by_a_later_save(report, clock):
    report("save", "host:web01", "-s", "warn", "-m", "old")
    report("save", "host:db01", "-s", "warn", "-m", "old too")
    clock[0] += 29 * 86400
    report("save", "host:web01", "-s", "warn", "-m", "newer")
    assert len(fleet.reports()) == 3
    clock[0] += 2 * 86400  # the first two are 31 days old now
    report("save", "feed:kuma", "-s", "ok", "-m", "fine")
    assert [(item["unit"], item["time"]) for item in fleet.reports()] == [("host:web01", "2026-12-11T16:53:20Z")]
    assert fleet.records()["host:db01"]["status"] == "warn"  # the status stays: it is what `fleet` shows


def test_a_unit_keeps_its_newest_reports(report, clock, monkeypatch):
    monkeypatch.setattr(fleet, "MAX_REPORTS", 3)
    for number in range(5):
        clock[0] += 60
        report("save", "host:web01", "-s", "warn", "-m", f"round {number}")
    kept = fleet.reports("host:web01")
    assert len(kept) == 3 and "round 4" in fleet.read_report("host:web01")[1]
    assert "round 2" in fleet.read_report("host:web01", 3)[1]


def test_a_long_body_is_cut_and_says_so(report, monkeypatch):
    monkeypatch.setattr(fleet, "MAX_BODY", 100)
    report("save", "host:web01", "-s", "warn", "-m", "long", stdin="x" * 500)
    text = fleet.read_report("host:web01")[1]
    assert text.count("x") == 100 and "(cut here: a report holds at most 100 characters)" in text


def test_files_that_are_not_records_or_reports_are_left_out(report):
    report("save", "host:web01", "-s", "warn", "-m", "real")
    home = config_dir()
    (home / "fleet" / "host" / "junk.json").write_text("{not json")
    (home / "fleet" / "host" / "other.json").write_text(json.dumps({"unit": "host:web01", "status": "ok",
                                                                    "time": "2026-11-12T16:53:20Z"}))
    (home / "fleet" / "host" / "bad.json").write_text(json.dumps({"unit": "host:bad", "status": "great",
                                                                  "time": "2026-11-12T16:53:20Z"}))
    (home / "reports" / "host" / "web01" / "notes.md").write_text("mine")
    (home / "reports" / "stray.md").write_text("mine")
    assert list(fleet.records()) == ["host:web01"]
    assert [item["status"] for item in fleet.reports()] == ["warn"]
    assert (home / "reports" / "host" / "web01" / "notes.md").exists()  # and never removed
