"""The MCP boundary: per-call timeout, size caps, `expose = false`, --expose, and the bearer
token; plus lazy module loading, which the server relies on for a fast start."""

import time

import anyio
import pytest
from mcp.client import Client
from mcp.shared.exceptions import MCPError

from core import mcp_server
from core.loader import ModuleRegistry
from core.mcp_server import ALERT_TOOL, PIPELINE_TOOL, build_server, require_bearer, resolve_token


def _call(server, fn):
    async def main():
        async with Client(server) as client:
            return await fn(client)
    return anyio.run(main)


@pytest.fixture
def local(tmp_path):
    (tmp_path / "pub.py").write_text("def run(args, stdin):\n    return 'public ' + ' '.join(args)\n")
    (tmp_path / "pub.skill").write_text('summary = "a public tool"\n')
    (tmp_path / "secret.py").write_text("def run(args, stdin):\n    return 'secret'\n")
    (tmp_path / "secret.skill").write_text('summary = "local only"\nexpose = false\n')
    (tmp_path / "secret.md").write_text("# secret\n\nLocal only.\n")
    (tmp_path / "slow.py").write_text("import time\ndef run(args, stdin):\n    time.sleep(2)\n    return 'done'\n")
    (tmp_path / "slow.skill").write_text('summary = "slow"\n')  # a .skill makes it an MCP tool
    (tmp_path / "big.py").write_text("def run(args, stdin):\n    return 'x' * 3_000_000\n")
    (tmp_path / "big.skill").write_text('summary = "big"\n')
    registry = ModuleRegistry(tmp_path)
    registry.load()
    return registry


def test_unexposed_modules_are_not_tools_anywhere(local):
    server = build_server(local)
    tools = {t.name for t in _call(server, lambda c: c.list_tools()).tools}
    assert "pub" in tools and "secret" not in tools
    async def unknown(c):
        with pytest.raises(MCPError, match="Unknown tool"):
            await c.call_tool("secret", {"args": []})

    _call(server, unknown)
    result = _call(server, lambda c: c.call_tool(PIPELINE_TOOL, {"command": "secret"}))
    assert result.is_error and "not available to MCP clients" in result.content[0].text
    names = {str(r.uri) for r in _call(server, lambda c: c.list_resources()).resources}
    assert mcp_server.MAN_URI + "secret" not in names
    assert "secret" not in _call(server, lambda c: c.list_tools()).tools[-1].description


def test_expose_narrows_the_tool_list(local):
    server = build_server(local, expose=["slow"])
    tools = {t.name for t in _call(server, lambda c: c.list_tools()).tools}
    assert tools == {"slow", PIPELINE_TOOL, ALERT_TOOL}  # the alert tool is always offered
    result = _call(server, lambda c: c.call_tool(PIPELINE_TOOL, {"command": "pub"}))
    assert result.is_error


def test_the_shell_still_runs_unexposed_modules(local):
    from core.context import ShellContext, to_text
    from core.pipeline import run_line

    assert to_text(run_line("secret", ShellContext(registry=local)).output) == "secret"


def test_a_slow_call_times_out(local):
    server = build_server(local, timeout=0.3)
    started = time.monotonic()
    result = _call(server, lambda c: c.call_tool("slow", {"args": []}))
    assert result.is_error and "did not finish within 0 s" in result.content[0].text
    assert time.monotonic() - started < 1.5


def test_output_and_stdin_are_capped(local, monkeypatch):
    server = build_server(local)
    result = _call(server, lambda c: c.call_tool("big", {"args": []}))
    assert not result.is_error
    text = result.content[0].text
    assert len(text) < 2_100_000 and "output cut after" in text
    monkeypatch.setattr(mcp_server, "MAX_STDIN", 10)
    result = _call(server, lambda c: c.call_tool("pub", {"args": [], "stdin": "x" * 11}))
    assert result.is_error and "too large" in result.content[0].text


def test_resolve_token(monkeypatch):
    monkeypatch.delenv("SHELLCRAFT_MCP_TOKEN", raising=False)
    assert resolve_token("abc") == "abc"
    assert resolve_token(None, no_token=True) is None
    generated = resolve_token(None)
    assert generated and len(generated) >= 24 and generated != resolve_token(None)
    monkeypatch.setenv("SHELLCRAFT_MCP_TOKEN", "from-env")
    assert resolve_token(None) == "from-env"


def test_bearer_middleware_rejects_without_the_token():
    seen = []

    async def inner(scope, receive, send):
        seen.append(scope["type"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    app = require_bearer(inner, "s3cret")

    async def run(headers):
        sent = []

        async def send(message):
            sent.append(message)

        await app({"type": "http", "headers": headers}, None, send)
        return sent[0]["status"]

    assert anyio.run(run, []) == 401
    assert anyio.run(run, [(b"authorization", b"Bearer wrong")]) == 401
    assert anyio.run(run, [(b"authorization", b"Bearer s3cret")]) == 200
    async def nothing(message):
        pass

    assert anyio.run(lambda: app({"type": "lifespan"}, None, nothing)) is None  # passes through
    assert seen == ["http", "lifespan"]


# ── lazy loading ─────────────────────────────────────────────────────────────

def test_lazy_registry_imports_on_first_use(tmp_path):
    (tmp_path / "late.py").write_text(
        "import sys\nsys.modules['_lazy_marker'] = True\nSUMMARY = 'from the code'\n"
        "def run(args, stdin):\n    return 'ran'\n")
    (tmp_path / "late.skill").write_text('summary = "from the skill"\nusage = "late"\n')
    (tmp_path / "broken.py").write_text("import nosuchmodule_xyz\ndef run(args, stdin):\n    return ''\n")
    import sys

    sys.modules.pop("_lazy_marker", None)
    registry = ModuleRegistry(tmp_path, lazy=True)
    registry.load()
    assert set(registry.names()) == {"broken", "late"}  # nothing imported yet, so nothing failed
    assert registry.warnings == []
    assert "_lazy_marker" not in sys.modules
    spec = registry.get("late")
    assert not spec.loaded and spec.summary == "from the skill" and spec.description.startswith("from the skill")
    assert spec.run([], "") == "ran"
    assert spec.loaded and "_lazy_marker" in sys.modules

    from core.context import ShellContext
    from core.pipeline import PipelineError, run_line

    with pytest.raises(PipelineError, match="nosuchmodule_xyz"):
        run_line("broken", ShellContext(registry=registry))
    assert any("broken.py" in w for w in registry.warnings)


def test_lazy_registry_lists_api_keys_before_first_use(tmp_path):
    (tmp_path / "keyed.py").write_text(
        "from core.modkit import EnvSetting\n"
        "ENV_SETTINGS = [EnvSetting('LAZY_TEST_KEY', 'Lazy key', 'for the test')]\n"
        "def run(args, stdin):\n    return ''\n")
    from core import settings

    registry = ModuleRegistry(tmp_path, lazy=True)
    registry.load()
    assert "LAZY_TEST_KEY" in settings.env_settings(registry)


def test_cli_passes_the_lazy_setting(tmp_path, monkeypatch, started):
    import json

    from core import cli

    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path))
    (tmp_path / "m.py").write_text("def run(args, stdin):\n    return ''\n")
    cli.main(["--no-banner", "--modules", str(tmp_path)])
    assert started[-1].registry.lazy and not started[-1].registry.get("m").loaded
    (tmp_path / "config.json").write_text(json.dumps({"settings": {"lazy_modules": False}}))
    cli.main(["--no-banner", "--modules", str(tmp_path)])
    assert not started[-1].registry.lazy and started[-1].registry.get("m").loaded
