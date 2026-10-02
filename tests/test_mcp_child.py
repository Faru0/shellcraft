"""`mcp start / stop / status / log`: the MCP HTTP server as a child process of the shell."""

import io
import json
import os
import socket
import sys
import urllib.request

import pytest

from core import mcp_child
from core.context import to_text
from core.pipeline import PipelineError, run_line

pytestmark = pytest.mark.skipif(bool(mcp_child.mcp_available()), reason="needs the mcp package")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server_ctx(ctx):
    yield ctx
    if ctx.mcp_http is not None:  # never leave a server behind, even when a test fails
        ctx.mcp_http.stop()


def out(line, ctx):
    return to_text(run_line(line, ctx).output)


def _initialize(url: str) -> dict:
    body = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "test", "version": "1"}}}
    request = urllib.request.Request(url, data=json.dumps(body).encode(), headers={
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    with urllib.request.urlopen(request, timeout=10) as response:
        text = response.read().decode()
    data = next(line[5:] for line in text.splitlines() if line.startswith("data:")) if "data:" in text else text
    return json.loads(data)


def test_start_status_log_stop(server_ctx):
    ctx = server_ctx
    assert "not running" in out("mcp", ctx)
    port = _free_port()
    started = out(f"mcp start 127.0.0.1:{port}", ctx)
    url = f"http://127.0.0.1:{port}/mcp"
    assert url in started and f"pid {ctx.mcp_http.pid}" in started

    # A real MCP server answers at that URL, serving the shell's modules.
    reply = _initialize(url)
    assert reply["result"]["serverInfo"]["name"].startswith("shellcraft")
    status = out("mcp", ctx)
    assert "running at " + url in status and "up " in status
    assert "streamable HTTP on " + url in out("mcp log", ctx)

    with pytest.raises(PipelineError, match="already running"):
        run_line("mcp start", ctx)
    process = ctx.mcp_http.process
    assert "stopped" in out("mcp stop", ctx)
    assert process.poll() is not None and ctx.mcp_http is None
    assert "not running" in out("mcp", ctx)
    with pytest.raises(PipelineError, match="isn't running"):
        run_line("mcp stop", ctx)


def test_restart_keeps_the_address(server_ctx):
    ctx = server_ctx
    port = _free_port()
    run_line(f"mcp start 127.0.0.1:{port}", ctx)
    first = ctx.mcp_http.pid
    assert f"restarted at http://127.0.0.1:{port}/mcp" in out("mcp restart", ctx)
    assert ctx.mcp_http.pid != first


@pytest.mark.skipif(sys.platform == "win32", reason="process groups are POSIX sessions here")
def test_ctrl_c_at_the_prompt_does_not_reach_the_server(server_ctx):
    run_line(f"mcp start 127.0.0.1:{_free_port()}", server_ctx)
    assert os.getpgid(server_ctx.mcp_http.pid) != os.getpgid(0)


def test_a_port_in_use_is_refused(server_ctx):
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        with pytest.raises(PipelineError, match="already in use"):
            run_line(f"mcp start 127.0.0.1:{port}", server_ctx)
    assert server_ctx.mcp_http is None


@pytest.mark.parametrize("line, error", [
    ("mcp start 0.0.0.0:8765", "only binds to this machine"),
    ("mcp start nonsense", "invalid address"),
    ("mcp start a:1 b:2", "usage"),
    ("mcp restart", "isn't running"),
    ("mcp frobnicate", "usage"),
    ("mcp log x", "usage"),
])
def test_bad_usage(server_ctx, line, error):
    with pytest.raises(PipelineError, match=error):
        run_line(line, server_ctx)
    assert server_ctx.mcp_http is None


def test_a_server_that_exits_reports_its_log(server_ctx, monkeypatch):
    class Dying(mcp_child.McpHttpChild):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.process.kill()
            self.process.wait()
            mcp_child.log_path().write_text("boom: something broke\n")

    monkeypatch.setattr(mcp_child, "McpHttpChild", Dying)
    with pytest.raises(PipelineError, match=r"exited with code .*\n.*boom: something broke"):
        run_line(f"mcp start 127.0.0.1:{_free_port()}", server_ctx)
    assert server_ctx.mcp_http is None


def test_a_server_that_died_is_forgotten(server_ctx):
    run_line(f"mcp start 127.0.0.1:{_free_port()}", server_ctx)
    server_ctx.mcp_http.process.kill()
    server_ctx.mcp_http.process.wait()
    assert "not running" in out("mcp", server_ctx)
    assert server_ctx.mcp_http is None


def test_without_the_mcp_package(ctx, monkeypatch):
    monkeypatch.setattr(mcp_child, "mcp_available", lambda: ["mcp", "mcp_types"])
    with pytest.raises(PipelineError, match="needs the 'mcp' package"):
        run_line("mcp start", ctx)


def test_mcp_clients_cannot_start_servers(ctx):
    ctx.allow_stateful = False  # as for MCP clients
    with pytest.raises(PipelineError):
        run_line("mcp start", ctx)


def test_the_shell_stops_the_server_when_it_exits(monkeypatch, tmp_path, ctx):
    from core import shell

    monkeypatch.setattr(shell, "open_windows_console", lambda: None)
    from core.themes import UI, all_themes
    from rich.console import Console

    ctx.ui = UI(all_themes({})["cyberpunk"], Console(file=io.StringIO()))
    with shell.Shell(ctx):
        run_line(f"mcp start 127.0.0.1:{_free_port()}", ctx)
        process = ctx.mcp_http.process
        assert process.poll() is None
    assert process.poll() is not None and ctx.mcp_http is None


def test_prompt_marks_a_running_server(monkeypatch, ctx):
    from rich.console import Console

    from core import shell
    from core.themes import UI, all_themes

    def marker(sh):
        return "".join(text for style, text in sh._prompt() if style.startswith("class:mcp"))

    monkeypatch.setattr(shell, "open_windows_console", lambda: None)
    ctx.ui = UI(all_themes({})["cyberpunk"], Console(file=io.StringIO()))
    with shell.Shell(ctx) as sh:
        assert marker(sh) == ""
        port = _free_port()
        run_line(f"mcp start 127.0.0.1:{port}", ctx)
        assert marker(sh) == f"● mcp :{port}"
        ctx.mcp_http.process.kill()
        ctx.mcp_http.process.wait()
        assert marker(sh) == f"✗ mcp :{port}"  # died on its own: shown until noticed
        run_line("mcp", ctx)
        assert marker(sh) == ""


def test_prompt_marker_shows_a_non_default_host(ctx):
    from types import SimpleNamespace

    from core import shell

    fake = SimpleNamespace(ctx=ctx)
    ctx.mcp_http = SimpleNamespace(host="::1", port=9000, url="http://[::1]:9000/mcp", running=lambda: True)
    assert shell.Shell._mcp_marker(fake) == [("class:sep", " ─ "), ("class:mcp", "● mcp [::1]:9000")]
    ctx.mcp_http = None
