"""Alerts: the bus (priority, expiry, dismissal), the spool folder and its senders, the `alert`
builtin (local and forwarded), the subshell shim, the panel lines, the MCP tool."""

import io
import json
import os
import sys
import time

import pytest
from rich.console import Console

from core import alerts as alertmod
from core.alerts import AlertBus
from core.context import ShellContext, to_text
from core.loader import ModuleRegistry
from core.pipeline import PipelineError, run_line
from core.themes import UI, all_themes


def _wait(pred, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / "home"))
    monkeypatch.delenv(alertmod.SUB_ENV, raising=False)
    monkeypatch.delenv("SHELLCRAFT_DEPTH", raising=False)
    return tmp_path / "home"


@pytest.fixture
def bus(home):
    b = AlertBus()
    b.watch(home)
    yield b
    b.close()


def test_bus_orders_by_severity_caps_the_list_and_expires():
    changes = []
    b = AlertBus()
    b.on_change = lambda: changes.append(1)
    b.post("first", "info")
    b.post("second", "critical")
    b.post("third", "warning")
    b.post("fourth", "info")
    levels = [(a.level, a.message) for a in b.current()]
    assert levels == [("critical", "second"), ("warning", "third"), ("info", "fourth")]  # three kept, severe first
    assert b.current()[0].remaining is None and b.current()[2].timeout == 15.0 and b.current()[1].timeout == 60.0
    short = b.post("blink", "critical", timeout=1)  # a fourth alert: the lowest, oldest one ("fourth") goes
    assert short.timeout == 1.0 and [a.message for a in b.current()] == ["blink", "second", "third"]  # newest first
    assert _wait(lambda: all(a.message != "blink" for a in b.current()), 3)  # expired by the timer
    assert len(changes) >= 6
    assert b.dismiss(1) == 1 and [a.message for a in b.current()] == ["third"]
    assert b.dismiss() == 1 and b.current() == [] and b.dismiss() == 0
    assert b.post("x" * 500, "nope").message == "x" * 300 and b.current()[0].level == "info"
    b.close()


def test_spool_delivers_labels_and_drops_stale_files(bus, home, monkeypatch):
    spool = home / alertmod.SPOOL
    assert bus.spool == spool and spool.is_dir()
    if sys.platform != "win32":
        assert (spool.stat().st_mode & 0o777) == 0o700
    written = alertmod.send("disk almost full", "warning", 30, source="test")
    assert written.parent == spool and written.suffix == ".json"
    assert _wait(lambda: bus.current() and bus.current()[0].message == "disk almost full")
    first = bus.current()[0]
    assert first.level == "warning" and first.timeout == 30.0 and first.source == "test"
    assert _wait(lambda: not list(spool.glob("*.json")))  # consumed
    # the sender's label comes from the environment
    assert alertmod.source_label() == f"pid {os.getppid()}"
    monkeypatch.setenv(alertmod.SUB_ENV, "2:bash --norc extra")
    assert alertmod.source_label() == "sub 2 (bash --norc)"
    alertmod.send("from inside")
    assert _wait(lambda: any(a.source == "sub 2 (bash --norc)" for a in bus.current()))
    # junk, a bad timeout and a stale file are handled without posting junk
    (spool / "0-junk.json").write_text("not json")
    (spool / "1-list.json").write_text("[1, 2]")
    (spool / "2-stale.json").write_text(json.dumps({"time": time.time() - 3600, "message": "old", "level": "info"}))
    (spool / "3-bad.json").write_text(json.dumps({"time": time.time(), "message": "odd", "timeout": "soon"}))
    assert _wait(lambda: any(a.message == "odd" for a in bus.current()))
    assert bus.current()[-1].timeout == 15.0 and not any(a.message == "old" for a in bus.current())
    assert _wait(lambda: not list(spool.glob("*.json")))
    bus.close()
    alertmod.send("after close", home=home)  # nobody watching: the file waits in the spool
    assert list(spool.glob("*.json"))


def test_command_line_client_and_shim(bus, home, capsys):
    assert alertmod.main(["-l", "critical", "-t", "9", "build", "failed"]) == 0
    assert _wait(lambda: any(a.message == "build failed" and a.level == "critical" and a.timeout == 9.0
                             for a in bus.current()))
    assert alertmod.main([]) == 2 and "message is needed" in capsys.readouterr().err
    assert alertmod.main(["-c"]) == 2
    assert alertmod.parse_args(["-s", "hello", "there"]) == ("hello there", "info", None, False, False)
    assert alertmod.parse_args(["--list"])[4] is True
    folder = alertmod.shim_dir(home)
    script = folder / ("alert.cmd" if sys.platform == "win32" else "alert")
    assert script.exists() and "core.alerts" in script.read_text()
    env = alertmod.with_shim({"PATH": "/usr/bin"}, home)
    assert env["PATH"].startswith(str(folder) + os.pathsep)
    if sys.platform != "win32":
        import subprocess

        done = subprocess.run([str(script), "-l", "warning", "from the shim"], capture_output=True, text=True,
                              env={**os.environ, "PATH": env["PATH"], alertmod.SUB_ENV: "1:bash"})
        assert done.returncode == 0, done.stderr
        assert _wait(lambda: any(a.message == "from the shim" and a.source == "sub 1 (bash)" for a in bus.current()))


@pytest.fixture
def ictx(home, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx = ShellContext(registry=ModuleRegistry(tmp_path / "modules"))
    ctx.interactive = True
    ctx.ui = UI(all_themes({})["nord"], Console(file=io.StringIO()))
    ctx.alerts = AlertBus()
    return ctx


def test_alert_builtin_posts_lists_and_dismisses(ictx):
    assert run_line("alert deploy finished", ictx).status == 0
    run_line("alert -l warning -t 40 disk at 90%", ictx)
    run_line("alert -l critical -s pager is down", ictx)
    listed = to_text(run_line("alert --list", ictx).output)
    assert listed.startswith("1. [critical] pager is down  (sticky, from main shell)")
    assert "2. [warning] disk at 90%" in listed and "3. [info] deploy finished" in listed
    assert "1 alert dismissed" in to_text(run_line("alert -c 2", ictx).output)
    assert "2 alerts dismissed" in to_text(run_line("alert -c", ictx).output)
    assert "no alerts" in to_text(run_line("alert --list", ictx).output)
    with pytest.raises(PipelineError, match="message is needed"):
        run_line("alert", ictx)
    ictx.interactive = False
    ictx.allow_stateful = False
    with pytest.raises(PipelineError):
        run_line("alert over mcp", ictx)  # stateful: never through shellcraft_pipeline


def test_nested_shell_forwards_to_the_main_shell(ictx, bus, monkeypatch):
    monkeypatch.setenv("SHELLCRAFT_DEPTH", "1")
    monkeypatch.setenv(alertmod.SUB_ENV, "3:shellcraft --no-banner")
    out = to_text(run_line("alert -l warning from inside", ictx).output)
    assert "sent to the main shell" in out
    assert _wait(lambda: any(a.message == "from inside" and a.level == "warning"
                             and a.source == "sub 3 (shellcraft --no-banner) shellcraft ⤷1" for a in bus.current()))
    with pytest.raises(PipelineError, match="main ShellCraft"):
        run_line("alert --list", ictx)


def test_panel_lines_show_alerts_first_with_their_source(bus):
    from core.footer import panel_lines

    bus.post("all good", "info", source="main shell")
    bus.post("look here", "critical", source="sub 1 (bash)")
    lines = panel_lines([], 0, None, 80, alerts=bus.current())
    assert [style for _t, style, _x, _h in lines] == ["alert.critical", "alert.info"]  # no stack without jobs
    assert lines[0][2].startswith(" ✖ look here · from sub 1 (bash)") and lines[0][2].rstrip().endswith("critical · sticky")
    assert lines[1][2].startswith(" ● all good · from main shell") and "info · " in lines[1][2]
    long = AlertBus()
    long.post("x" * 200, "warning")
    text = panel_lines([], 0, None, 60, alerts=long.current())[0][2]
    assert "…" in text and len(text) <= 60


def test_mcp_tool_send_tui_alert(bus, registry):
    import anyio
    from mcp.client import Client

    from core.mcp_server import ALERT_TOOL, build_server

    def call(fn):
        async def main():
            async with Client(build_server(registry)) as client:
                return await fn(client)
        return anyio.run(main)

    tools = {t.name: t for t in call(lambda c: c.list_tools()).tools}
    assert ALERT_TOOL in tools and tools[ALERT_TOOL].input_schema["required"] == ["message"]
    assert tools[ALERT_TOOL].input_schema["properties"]["severity"]["enum"] == ["info", "warning", "critical"]
    ok = call(lambda c: c.call_tool(ALERT_TOOL, {"message": "model says hi", "severity": "warning",
                                                 "timeout_seconds": 20}))
    assert not ok.is_error and "alert shown (warning)" in ok.content[0].text
    assert _wait(lambda: any(a.message == "model says hi" and a.timeout == 20.0 and a.source.startswith("mcp · ")
                             for a in bus.current()))
    bad = call(lambda c: c.call_tool(ALERT_TOOL, {"message": "x", "severity": "loud"}))
    assert bad.is_error and "severity" in bad.content[0].text
    empty = call(lambda c: c.call_tool(ALERT_TOOL, {"message": "   "}))
    assert empty.is_error and "required" in empty.content[0].text
