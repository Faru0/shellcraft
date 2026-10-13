"""Alerts: the bus (priority, expiry, dismissal), the loopback listener and its clients, the
`alert` builtin (local and forwarded), the subshell shim, the panel lines, the MCP tool."""

import io
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
def bus(tmp_path, monkeypatch):
    monkeypatch.delenv(alertmod.ADDR_ENV, raising=False)
    monkeypatch.delenv(alertmod.TOKEN_ENV, raising=False)
    b = AlertBus()
    b.listen(tmp_path / "home")
    yield b
    b.close()


def test_bus_orders_by_severity_caps_the_list_and_expires(monkeypatch):
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


def test_listener_accepts_the_token_and_refuses_the_rest(bus, tmp_path):
    host, port = bus.address
    assert os.environ[alertmod.ADDR_ENV] == f"{host}:{port}" and os.environ[alertmod.TOKEN_ENV] == bus.token
    assert (tmp_path / "home" / alertmod.ADDR_FILE).read_text().splitlines() == [f"{host}:{port}", bus.token]
    assert alertmod.send("disk almost full", "warning", 30, source="test") == "ok"
    assert _wait(lambda: bus.current() and bus.current()[0].message == "disk almost full")
    assert bus.current()[0].level == "warning" and bus.current()[0].timeout == 30.0 and bus.current()[0].source == "test"
    # a wrong token, bad JSON and a bad timeout are refused
    assert bus._receive(b'{"token": "nope", "message": "x"}', "127.0.0.1") == "error: bad token"
    assert bus._receive(b"not json", "127.0.0.1").startswith("error")
    bad_timeout = ('{"token": "%s", "message": "x", "timeout": "soon"}' % bus.token).encode()
    assert bus._receive(bad_timeout, "127.0.0.1").startswith("error: timeout")
    sticky = ('{"token": "%s", "message": "sticky", "timeout": null}' % bus.token).encode()
    assert bus._receive(sticky, "127.0.0.1") == "ok"
    assert any(a.message == "sticky" and a.timeout is None for a in bus.current())
    # the address file is found when the environment has nothing
    os.environ.pop(alertmod.ADDR_ENV), os.environ.pop(alertmod.TOKEN_ENV)
    assert alertmod.target(tmp_path / "home") == (host, port, bus.token)
    assert alertmod.send("via file", home=tmp_path / "home") == "ok"
    bus.close()
    assert not (tmp_path / "home" / alertmod.ADDR_FILE).exists()
    with pytest.raises(OSError, match="no ShellCraft"):
        alertmod.send("gone", home=tmp_path / "home")


def test_command_line_client_and_shim(bus, tmp_path, capsys, monkeypatch):
    assert alertmod.main(["-l", "critical", "-t", "9", "build", "failed"]) == 0
    assert _wait(lambda: any(a.message == "build failed" and a.level == "critical" and a.timeout == 9.0
                             for a in bus.current()))
    assert alertmod.main([]) == 2 and "message is needed" in capsys.readouterr().err
    assert alertmod.main(["-c"]) == 2
    assert alertmod.parse_args(["-s", "hello", "there"]) == ("hello there", "info", None, False, False)
    assert alertmod.parse_args(["--list"])[4] is True
    folder = alertmod.shim_dir(tmp_path / "home")
    script = folder / ("alert.cmd" if sys.platform == "win32" else "alert")
    assert script.exists() and "core.alerts" in script.read_text()
    env = alertmod.with_shim({"PATH": "/usr/bin"}, tmp_path / "home")
    assert env["PATH"].startswith(str(folder) + os.pathsep)
    if sys.platform != "win32":
        import subprocess

        done = subprocess.run([str(script), "-l", "warning", "from the shim"], capture_output=True, text=True,
                              env={**os.environ, "PATH": env["PATH"]})
        assert done.returncode == 0, done.stderr
        assert _wait(lambda: any(a.message == "from the shim" for a in bus.current()))


@pytest.fixture
def ictx(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("SHELLCRAFT_DEPTH", raising=False)
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
    assert listed.startswith("1. [critical] pager is down  (sticky") and "2. [warning] disk at 90%" in listed
    assert "3. [info] deploy finished" in listed
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
    out = to_text(run_line("alert -l warning from inside", ictx).output)
    assert "sent to the main shell" in out
    assert _wait(lambda: any(a.message == "from inside" and a.level == "warning" and a.source == "shellcraft ⤷1"
                             for a in bus.current()))
    with pytest.raises(PipelineError, match="main ShellCraft"):
        run_line("alert --list", ictx)


def test_panel_lines_show_alerts_first(bus):
    from core.footer import panel_lines

    bus.post("all good", "info")
    bus.post("look here", "critical")
    lines = panel_lines([], 0, None, 80, alerts=bus.current())
    assert [style for _t, style, _x, _h in lines] == ["alert.critical", "alert.info"]  # no stack without jobs
    assert lines[0][2].startswith(" ✖ look here") and lines[0][2].rstrip().endswith("critical · sticky")
    assert lines[1][2].startswith(" ● all good") and "info · " in lines[1][2]
    long = AlertBus()
    long.post("x" * 200, "warning")
    text = panel_lines([], 0, None, 60, alerts=long.current())[0][2]
    assert "…" in text and len(text) <= 60


def test_mcp_tool_send_tui_alert(bus, registry, tmp_path, monkeypatch):
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
    assert _wait(lambda: any(a.message == "model says hi" and a.timeout == 20.0 and a.source == "mcp"
                             for a in bus.current()))
    bad = call(lambda c: c.call_tool(ALERT_TOOL, {"message": "x", "severity": "loud"}))
    assert bad.is_error and "severity" in bad.content[0].text
    empty = call(lambda c: c.call_tool(ALERT_TOOL, {"message": "   "}))
    assert empty.is_error and "required" in empty.content[0].text
    bus.close()
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / "nowhere"))
    gone = call(lambda c: c.call_tool(ALERT_TOOL, {"message": "nobody home"}))
    assert gone.is_error and "no ShellCraft" in gone.content[0].text
