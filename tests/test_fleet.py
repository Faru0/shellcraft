"""fleet: every watched machine in one table, from the hosts file, the aggregators, the plans, the status
records, the baselines and the case file of a scratch home."""

import io
import json

import pytest
from rich.console import Console

from shellcraft import findings, fleet
from shellcraft.config import config_dir
from shellcraft.context import to_text
from shellcraft.pipeline import PipelineError, run_line
from shellcraft.themes import all_themes

CLOCK = 1_794_502_400.0  # 2026-11-12T16:53:20Z

HOSTS = """
[hosts.web01]
ai = true
sudo = true
role = "web server"
tags = ["prod"]

[hosts.db01]
ai = true

[hosts.laptop]
ai = false
"""

AGGREGATORS = """
[aggregators.edr]
host = "collector"
root = "/srv/edr"
devices = { pc7 = "10.0.0.7", web01 = "10.0.0.8" }

[aggregators.edr.files]
ps = "ps-*.txt"
"""

PLAN = """
interval_minutes = 10

[[hosts]]
name = "web01"
checks = ["ps", "cron"]

[[hosts]]
name = "db01"

[[hosts]]
name = "old01"

[[devices]]
aggregator = "edr"
name = "pc7"

[[feeds]]
name = "kuma"
"""


@pytest.fixture
def clock(monkeypatch):
    moment = [CLOCK]
    monkeypatch.setattr(fleet, "now", lambda: moment[0])
    return moment


@pytest.fixture
def home(ctx, clock):
    base = config_dir()
    (base / "watchplans" / ".state").mkdir(parents=True)
    (base / "ssh-hosts.toml").write_text(HOSTS)
    (base / "aggregators.toml").write_text(AGGREGATORS)
    (base / "watchplans" / "prod.toml").write_text(PLAN)
    (base / "watchplans" / ".state" / "prod.json").write_text(json.dumps({"last": CLOCK - 300}))
    return base


def out(ctx, line):
    result = run_line(line, ctx)
    return "" if result is None or result.output is None else to_text(result.output)


def rows(ctx, line="fleet"):
    return [row.split("\t") for row in out(ctx, line).splitlines()]


def screen(ctx, line="fleet"):
    console = Console(file=io.StringIO(), width=200, theme=all_themes({})["cyberpunk"].rich_theme())
    console.print(run_line(line, ctx).output.renderable)
    return console.file.getvalue()


def _baseline(base, host, check, updated, items):
    folder = base / "baselines" / host
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{check}.json").write_text(json.dumps({"version": 1, "host": host, "check": check, "learned": True,
                                                      "updated": updated, "items": items}))


def test_nothing_to_show_makes_nothing(ctx):
    assert "no machines yet" in screen(ctx)
    assert out(ctx, "fleet") == "" and out(ctx, "fleet --json") == "[]\n"
    assert not config_dir().exists() or not list(config_dir().iterdir())


def test_the_rows_are_the_hosts_the_ai_may_reach_the_devices_and_what_a_plan_lists(ctx, home):
    assert rows(ctx) == [
        ["host:db01", "-", "never", "-", "-", "no round yet (plan prod)"],
        ["host:old01", "-", "never", "-", "-", "no round yet (plan prod)"],
        ["host:web01", "-", "never", "-", "-", "no round yet (plan prod)"],
        ["device:edr/pc7", "-", "never", "-", "-", "no round yet (plan prod)"],
        ["device:edr/web01", "-", "never", "-", "-", "not in a watch plan"],
    ]
    assert not (home / "fleet").exists() and not (home / "findings").exists()  # looking makes no folder


def test_a_round_fills_the_row_and_the_ones_that_need_you_come_first(ctx, home, clock):
    fleet.save("host:db01", "ok", "3 checks, no change", changed=0, plan="prod")
    fleet.save("host:web01", "warn", "new listener on port 4444", changed=2, flags=1, plan="prod")
    fleet.save("device:edr/pc7", "crit", "a miner is running", changed=5, plan="prod")
    fleet.save("feed:kuma", "ok", "all monitors up", plan="prod")
    clock[0] += 180
    assert rows(ctx) == [
        ["device:edr/pc7", "crit", "3m", "5", "-", "a miner is running"],
        ["host:web01", "warn", "3m", "2", "-", "new listener on port 4444"],
        ["host:old01", "-", "never", "-", "-", "no round yet (plan prod)"],
        ["device:edr/web01", "-", "never", "-", "-", "not in a watch plan"],
        ["host:db01", "ok", "3m", "0", "-", "3 checks, no change"],
        ["feed:kuma", "ok", "3m", "-", "-", "all monitors up"],  # a feed shows once a round reported on it
    ]


def test_late_is_more_than_twice_the_interval_of_the_plan(ctx, home, clock):
    fleet.save("host:web01", "ok", "no change", plan="prod")
    fleet.save("device:edr/web01", "ok", "no change")  # in no plan: nothing says how often
    clock[0] += 20 * 60
    assert rows(ctx, "fleet | grep web01") == [["host:web01", "ok", "20m", "-", "-", "no change"],
                                               ["device:edr/web01", "ok", "20m", "-", "-", "no change"]]
    clock[0] += 1
    assert rows(ctx, "fleet | grep LATE") == [["host:web01", "ok", "20m LATE", "-", "-", "no change"]]
    clock[0] += 3 * 86400
    assert rows(ctx)[0][:3] == ["host:web01", "ok", "3d LATE"]
    assert "3d LATE" in screen(ctx)


def test_a_unit_in_two_plans_is_timed_by_the_shorter_one(ctx, home, clock):
    (home / "watchplans" / "slow.toml").write_text('interval_minutes = 60\n[[hosts]]\nname = "web01"\n')
    fleet.save("host:web01", "ok", "no change", plan="slow")
    clock[0] += 25 * 60
    one = json.loads(out(ctx, "fleet web01 --json"))
    assert (one["plan"], one["interval"], one["late"]) == ("prod", 10, True)


def test_findings_count_by_tag_or_as_a_host_finding(ctx, home):
    findings.add("ip", "203.0.113.9", "talks to web01", severity="high", tags=["host:web01"])
    findings.add("process", "/tmp/.x/agent", severity="medium", tags=["web01", "round-3"])
    findings.add("host", "DB01", "db01: a new sudoers file", severity="low")
    findings.add("file", "/etc/cron.d/x", severity="critical", tags=["device:edr/pc7"], case="incident-7")
    gone, _ = findings.add("user", "deploy", severity="critical", tags=["host:web01"])
    findings.update(gone["id"], status="dismissed")
    findings.add("ip", "198.51.100.4", severity="critical", tags=["host:web010", "watch-prod"])  # another machine
    found = {row[0]: row[4] for row in rows(ctx)}
    assert found == {"host:web01": "2 high", "host:db01": "1 low", "device:edr/pc7": "1 critical", "host:old01": "-",
                     "device:edr/web01": "1 medium"}  # the bare name is the same machine seen two ways


def test_one_unit_in_full(ctx, home, clock):
    old = "2026-11-12T16:40:00Z"
    _baseline(home, "web01", "ps", "2026-11-12T16:50:00Z", {
        "root /sbin/init": {"first_seen": old, "last_seen": "2026-11-12T16:50:00Z", "seen": 2, "present": True},
        "www nginx: worker": {"first_seen": "2026-11-12T16:50:00Z", "last_seen": "2026-11-12T16:50:00Z", "seen": 1,
                              "present": True},
        "root /tmp/.x/agent": {"first_seen": "2026-11-12T16:50:00Z", "last_seen": "2026-11-12T16:50:00Z", "seen": 1,
                               "present": True},
        "root cron": {"first_seen": old, "last_seen": old, "seen": 1, "present": False,
                      "gone_since": "2026-11-12T16:50:00Z"},
        "root atd": {"first_seen": old, "last_seen": old, "seen": 1, "present": False, "gone_since": old}})
    _baseline(home, "web01", "cron", "2026-11-12T16:50:00Z", {
        "root backup": {"first_seen": "2026-11-12T16:50:00Z", "last_seen": "2026-11-12T16:50:00Z", "seen": 1,
                        "present": True}})
    _baseline(home, "web01", "ss", "2026-11-12T16:50:00Z", {
        "LISTEN 0.0.0.0:22": {"first_seen": old, "last_seen": "2026-11-12T16:50:00Z", "seen": 2, "present": True}})
    (home / "baselines" / "web01" / "junk.json").write_text("{")
    item, _ = findings.add("ip", "203.0.113.9", "talks to web01", severity="high", tags=["host:web01"])
    fleet.save("host:web01", "warn", "new listener on port 4444", "## What changed\n- ss", 2, 1, "prod")
    clock[0] += 120
    text = out(ctx, "fleet host:web01")
    path = home / "reports" / "host" / "web01" / "20261112-165320-warn.md"
    assert text.splitlines() == [
        "host:web01\twarn\t2m",
        "new listener on port 4444",
        "web server, sudo, prod",
        "watch plan prod, every 10 min, its last round began 7m ago",
        "2 changed, 1 flag(s)",
        "check\tcron\t2026-11-12T16:50:00Z\tfirst run: 1 item(s) learned",
        "check\tps\t2026-11-12T16:50:00Z\t2 new, 1 gone, 3 now",
        "check\tss\t2026-11-12T16:50:00Z\tno change (1 item(s))",
        f"finding\t{item['id']}\thigh\tip\t203.0.113.9\ttalks to web01",
        f"report\t2026-11-12T16:53:20Z\twarn\t{path}",
    ]
    assert out(ctx, "fleet web01 | grep ^report | cut -f 4 | xargs cat | head -n 1") == "# host:web01: warn\n"
    shown = screen(ctx, "fleet host:web01")
    assert "checks" in shown and "findings (1)" in shown and "report show host:web01 N prints one" in shown


def test_a_name_is_enough_and_means_the_host_first(ctx, home):
    assert out(ctx, "fleet db01").startswith("host:db01\t-\tnever\n")
    assert out(ctx, "fleet pc7").startswith("device:edr/pc7\t-\tnever\n")
    assert out(ctx, "fleet web01").startswith("host:web01\t-\tnever\n")
    assert out(ctx, "fleet device:edr/web01").splitlines()[:2] == ["device:edr/web01\t-\tnever", "files on collector"]
    (home / "aggregators.toml").write_text(AGGREGATORS + '\n[aggregators.siem]\nhost = "c"\ndevices = { pc7 = "x" }\n')
    with pytest.raises(PipelineError, match="fleet: 'pc7' is more than one unit: device:edr/pc7, device:siem/pc7"):
        run_line("fleet pc7", ctx)
    with pytest.raises(PipelineError, match=r"fleet: no unit 'nope' \(known: host:db01, "):
        run_line("fleet nope", ctx)


def test_json_for_a_script(ctx, home, clock):
    fleet.save("host:web01", "warn", "new listener", changed=2, flags=1, plan="prod")
    clock[0] += 60
    listed = json.loads(out(ctx, "fleet --json"))
    assert [unit["id"] for unit in listed][0] == "host:web01"
    assert listed[0] == {"id": "host:web01", "kind": "host", "name": "web01", "status": "warn",
                         "time": "2026-11-12T16:53:20Z", "age": 60, "late": False, "summary": "new listener",
                         "changed": 2, "flags": 1, "plan": "prod", "interval": 10,
                         "report": str(home / "reports" / "host" / "web01" / "20261112-165320-warn.md"),
                         "findings": 0, "worst": "", "about": "web server, sudo, prod"}
    one = json.loads(out(ctx, "fleet host:web01 --json"))
    assert one["id"] == "host:web01" and one["checks"] == [] and one["open_findings"] == []
    assert [item["status"] for item in one["reports"]] == ["warn"] and one["plan_last_round"] == "2026-11-12T16:48:20Z"
    assert out(ctx, "fleet --json | jq -w 'status=warn' -f id") == "host:web01\n"


def test_files_that_cannot_be_read_are_left_out_not_an_error(ctx, home):
    (home / "ssh-hosts.toml").write_text("[hosts.web01\nai = true")
    (home / "aggregators.toml").write_text('[aggregators.edr]\ndevices = "none"\n[aggregators.x]\ndevices = {a = 1}')
    (home / "watchplans" / "broken.toml").write_text("interval_minutes = ")
    (home / "watchplans" / "odd.toml").write_text('interval_minutes = "soon"\nhosts = "all"\n[[devices]]\nname = 7\n')
    (home / "watchplans" / ".state" / "prod.json").write_text("[]")
    (home / "fleet").mkdir()
    (home / "fleet" / "junk.json").write_text("{")
    (home / "findings" / "default").mkdir(parents=True)
    (home / "findings" / "default" / "f-1.json").write_text("{")
    assert [row[0] for row in rows(ctx)] == ["host:db01", "host:old01", "host:web01", "device:edr/pc7", "device:x/a"]
    assert "no round yet" in out(ctx, "fleet host:web01")


def test_the_manual_and_the_help_know_it(ctx):
    assert "last round" in out(ctx, "man fleet") and "report save" in out(ctx, "man fleet")
    assert "fleet" in out(ctx, "help")


def test_an_ai_pipeline_may_read_it(ctx, home):
    from shellcraft.builtins import BUILTINS

    entry = BUILTINS["fleet"]
    assert not (entry.stateful or entry.writes or entry.sensitive)  # so shellcraft_pipeline offers it to an analyst
