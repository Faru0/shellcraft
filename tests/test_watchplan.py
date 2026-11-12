"""watchplan: read and validate the plan, list its units of work, time the rounds."""

import inspect
import json
import time

import pytest

from shellcraft.modkit import ModuleError

PLAN = """
description = "prod"
interval_minutes = 10
quiet_hours = "23:00-06:00"

[[hosts]]
name = "web01"
checks = ["ps", "ss"]
logs = [ { path = "/var/log/auth.log", grep = "Failed password" } ]

[[hosts]]
name = "db01"
checks = ["ps"]
deep = false

[[feeds]]
name = "kuma"
query = ["since=3600"]

[[funcs]]
name = "inventory"
"""


@pytest.fixture
def wp(registry, tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "watchplans").mkdir(parents=True)
    monkeypatch.setenv("SHELLCRAFT_HOME", str(home))
    fn = inspect.unwrap(registry.get("watchplan").run)
    fn.__globals__["now"] = time.time
    api = type("W", (), {})()
    api.run = lambda *a: fn(list(a), "")
    api.write = lambda name, text=PLAN: (home / "watchplans" / f"{name}.toml").write_text(text)
    api.glob = fn.__globals__
    return api


def test_list_and_check(wp):
    assert "no plans yet" in wp.run("list")
    wp.write("prod")
    assert wp.run("list") == "prod\n"
    assert "ok: plan 'prod': 4 unit(s), every 10 min" in wp.run("check", "prod")


def test_tasks_are_json_units(wp):
    wp.write("prod")
    units = json.loads(wp.run("tasks", "prod"))
    assert [u["id"] for u in units] == ["host:web01", "host:db01", "feed:kuma", "func:inventory"]
    assert units[0]["checks"] == ["ps", "ss"] and units[0]["logs"][0]["grep"] == "Failed password"
    assert units[0]["deep"] is True and units[1]["deep"] is False
    assert units[2]["query"] == ["since=3600"]


def test_show_is_readable(wp):
    wp.write("prod")
    text = wp.run("show", "prod")
    assert "host web01: ps, ss; 1 log(s); deep" in text and "feed kuma since=3600" in text and "func inventory" in text
    assert "quiet hours 23:00-06:00" in text and "due now" in text


@pytest.mark.parametrize("bad, message", [
    ('interval_minutes = 0\n[[hosts]]\nname="a"', "interval_minutes"),
    ('interval_minutes = "5"\n[[hosts]]\nname="a"', "interval_minutes"),
    ('quiet_hours = "late"\n[[hosts]]\nname="a"', "quiet_hours"),
    ('[[hosts]]\nname="-oProxy"', "host name"),
    ('[[hosts]]\nname="a"\n[[hosts]]\nname="a"', "listed twice"),
    ('[[hosts]]\nname="a"\nchecks=["ps; id"]', "not allowed"),
    ('[[hosts]]\nname="a"\nlogs=[{path="/var/log/../etc/passwd"}]', "absolute path"),
    ('[[hosts]]\nname="a"\nlogs=[{path="/var/log/x", grep="bad\\nline"}]', "plain text"),
    ('[[feeds]]\nname="x y"', "valid name"),
    ('description = "empty"', "nothing to check"),
])
def test_bad_plans_are_refused(wp, bad, message):
    wp.write("bad", bad)
    with pytest.raises(ModuleError, match=message):
        wp.run("check", "bad")


def test_too_many_units(wp):
    wp.write("big", "".join(f'[[hosts]]\nname = "h{i}"\n' for i in range(61)))
    with pytest.raises(ModuleError, match="at most 60"):
        wp.run("check", "big")


@pytest.mark.parametrize("name", ["../x", "a/b", "", "a b"])
def test_plan_names_are_checked(wp, name):
    with pytest.raises(ModuleError):
        wp.run("show", name)


def test_unknown_plan_and_usage(wp):
    with pytest.raises(ModuleError, match="no plan named 'zzz'"):
        wp.run("show", "zzz")
    with pytest.raises(ModuleError, match="usage"):
        wp.run()
    with pytest.raises(ModuleError, match="needs a plan name"):
        wp.run("due")


def test_rounds_are_timed_from_done(wp):
    wp.write("prod")
    clock = [1_000_000.0]
    wp.glob["now"] = lambda: clock[0]
    assert "due now: no round has run yet" in wp.run("due", "prod")
    assert "recorded" in wp.run("done", "prod")
    clock[0] += 120
    assert "next round in 480 s" in wp.run("due", "prod")
    clock[0] += 500
    assert "due now: the last round was 620 s ago" in wp.run("due", "prod")
    # a round that ran with nobody there records when it began, so a timer keeps its rhythm
    assert "recorded" in wp.run("done", "prod", str(clock[0] - 60))
    assert "next round in 540 s" in wp.run("due", "prod")
    with pytest.raises(ModuleError, match="invalid float value"):
        wp.run("done", "prod", "soon")


def test_quiet_hours_wrap_midnight(wp):
    from datetime import datetime

    data = {"quiet_hours": "23:00-06:00"}
    at = lambda h, m: datetime(2026, 11, 7, h, m).timestamp()  # noqa: E731
    f = wp.glob["in_quiet_hours"]
    assert f(data, at(23, 0)) and f(data, at(2, 30)) and f(data, at(5, 59))
    assert not f(data, at(6, 0)) and not f(data, at(12, 0)) and not f(data, at(22, 59))
    assert f({"quiet_hours": "01:00-02:00"}, at(1, 30)) and not f({"quiet_hours": "01:00-02:00"}, at(3, 0))
    assert not f({}, at(3, 0))


DEVICE_PLAN = """
interval_minutes = 5

[[hosts]]
name = "web01"
checks = ["ps"]

[[devices]]
aggregator = "edr"
name = "web01"
kinds = ["ps", "netstat"]

[[devices]]
aggregator = "edr"
name = "192.168.1.9"
"""


def test_devices_become_units_after_the_hosts(wp):
    wp.write("both", DEVICE_PLAN)
    units = json.loads(wp.run("tasks", "both"))
    assert [u["id"] for u in units] == ["host:web01", "device:edr/web01", "device:edr/192.168.1.9"]
    assert units[1] == {"id": "device:edr/web01", "kind": "device", "aggregator": "edr", "device": "web01",
                        "kinds": ["ps", "netstat"], "deep": True}
    assert units[2]["kinds"] == []
    assert "ok: plan 'both': 3 unit(s)" in wp.run("check", "both")
    shown = wp.run("show", "both")
    assert "device edr/web01: ps, netstat; deep" in shown and "device edr/192.168.1.9: (all kinds)" in shown


@pytest.mark.parametrize("entry, message", [
    ('[[devices]]\nname = "web01"', "needs an aggregator"),
    ('[[devices]]\naggregator = "edr"', "needs an aggregator"),
    ('[[devices]]\naggregator = "../x"\nname = "a"', "needs an aggregator"),
    ('[[devices]]\naggregator = "edr"\nname = "a b"', "needs an aggregator"),
    ('[[devices]]\naggregator = "edr"\nname = "a"\nkinds = ["ps; rm"]', "not allowed"),
    ('[[devices]]\naggregator = "edr"\nname = "a"\n[[devices]]\naggregator = "edr"\nname = "a"', "listed twice"),
])
def test_a_bad_device_entry_is_explained(wp, entry, message):
    wp.write("bad", "interval_minutes = 5\n" + entry + "\n")
    with pytest.raises(ModuleError, match=message):
        wp.run("check", "bad")


def test_a_plan_of_only_devices_is_enough(wp):
    wp.write("onlydev", '[[devices]]\naggregator = "edr"\nname = "web01"\n')
    assert "1 unit(s)" in wp.run("check", "onlydev")
