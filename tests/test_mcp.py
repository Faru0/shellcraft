import anyio
import pytest
from mcp.client import Client
from mcp.shared.exceptions import MCPError

from core import params
from core.loader import ModuleRegistry
from core.mcp_server import (MAN_URI, MODULE_INPUT_SCHEMA, PIPELINE_TOOL, AddressError, ShellcraftMCP,
                             build_server, parse_http_address)
from core.watch import ModuleWatcher
from tests.conftest import OS_UPPER


def _call(server, fn):
    async def main():
        async with Client(server) as client:
            return await fn(client)
    return anyio.run(main)


def test_tools_carry_skill_descriptions(registry):
    result = _call(build_server(registry), lambda c: c.list_tools())
    tools = {t.name: t for t in result.tools}
    assert {"fetch", "filter", PIPELINE_TOOL} <= set(tools)
    assert tools["filter"].description == registry.get("filter").skill.to_description()
    assert "When to use:" in tools["filter"].description
    assert tools["filter"].input_schema["properties"]["stdin"]["type"] == "string"


def test_call_module_tool(registry):
    result = _call(build_server(registry),
                   lambda c: c.call_tool("filter", {"pattern": "b", "ignore_case": True,
                                                    "stdin": "apple\nBanana\ncherry\n"}))
    assert not result.is_error
    assert result.content[0].text == "Banana\n"


def test_module_error_is_tool_error(registry):
    result = _call(build_server(registry), lambda c: c.call_tool("filter", {"pattern": "(", "stdin": "x"}))
    assert result.is_error and "invalid pattern" in result.content[0].text


def test_pipeline_tool_blocks_redirect_and_system(registry):
    server = build_server(registry)
    ok = _call(server, lambda c: c.call_tool(PIPELINE_TOOL, {"command": "echo a b c | filter -c b"}))
    assert ok.content[0].text == "1\n"
    redirect = _call(server, lambda c: c.call_tool(PIPELINE_TOOL, {"command": "echo x > pwned.txt"}))
    assert redirect.is_error and "redirection is disabled" in redirect.content[0].text
    system = _call(server, lambda c: c.call_tool(PIPELINE_TOOL, {"command": f"echo x | {OS_UPPER}"}))
    assert system.is_error and "command not found" in system.content[0].text


def test_pipeline_tool_allows_readonly_ports_but_not_writes(registry, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "f.txt").write_text("b\na\n")
    server = build_server(registry)
    ok = _call(server, lambda c: c.call_tool(PIPELINE_TOOL, {"command": "cat f.txt | sort"}))
    assert not ok.is_error and ok.content[0].text == "a\nb\n"
    for command in ("rm f.txt", "echo x | tee g.txt", "mkdir d", "touch t", "env"):
        blocked = _call(server, lambda c: c.call_tool(PIPELINE_TOOL, {"command": command}))
        assert blocked.is_error and "not available" in blocked.content[0].text
    assert (tmp_path / "f.txt").exists()


# ── typed params ─────────────────────────────────────────────────────────────

def test_params_become_a_typed_input_schema(registry):
    tools = {t.name: t for t in _call(build_server(registry), lambda c: c.list_tools()).tools}
    schema = tools["filter"].input_schema
    assert schema["required"] == ["pattern"] and schema["additionalProperties"] is False
    assert schema["properties"]["ignore_case"]["type"] == "boolean"
    assert schema["properties"]["max"]["type"] == "integer"
    assert tools["queryDns"].input_schema["properties"]["output"]["enum"] == ["table", "hosts", "ips", "json"]
    assert tools["queryDns"].input_schema["properties"]["domains"]["type"] == "array"
    assert "Arguments (pass in `args`" not in tools["filter"].description


def test_params_map_to_cli_args(registry):
    result = _call(build_server(registry), lambda c: c.call_tool(
        "filter", {"pattern": "a", "count": True, "invert": False, "max": 1, "stdin": "a\nb\na\n"}))
    assert not result.is_error and result.content[0].text == "1\n"


@pytest.mark.parametrize("arguments, fragment", [
    ({"stdin": "x"}, "missing required parameter(s): pattern"),
    ({"pattern": "x", "bogus": 1}, "unknown parameter(s): bogus"),
    ({"pattern": "x", "max": "ten"}, "'max' must be an integer"),
    ({"pattern": "x", "count": "yes"}, "'count' must be a boolean"),
])
def test_bad_params_are_tool_errors(registry, arguments, fragment):
    result = _call(build_server(registry), lambda c: c.call_tool("filter", arguments))
    assert result.is_error and fragment in result.content[0].text


def test_enum_values_are_checked(registry):
    result = _call(build_server(registry), lambda c: c.call_tool(
        "queryCert", {"domains": ["example.com"], "output": "xml"}))
    assert result.is_error and "'xml' not in table, names, json" in result.content[0].text


def test_modules_without_params_keep_the_args_array(registry, tmp_path):
    (tmp_path / "echoargs.py").write_text("def run(args, stdin):\n    return ' '.join(args)\n")
    local = ModuleRegistry(tmp_path)
    local.load()
    tools = {t.name: t for t in _call(build_server(local), lambda c: c.list_tools()).tools}
    assert tools["echoargs"].input_schema == MODULE_INPUT_SCHEMA
    result = _call(build_server(local), lambda c: c.call_tool("echoargs", {"args": ["-x", "y"]}))
    assert result.content[0].text == "-x y"


def test_to_argv_orders_switches_before_positionals():
    declared = [
        {"name": "items", "type": "array", "description": "d"},
        {"name": "tag", "flag": "--tag", "type": "array", "description": "d"},
        {"name": "verbose", "flag": "--verbose", "type": "boolean", "description": "d"},
        {"name": "limit", "flag": "--limit", "type": "number", "description": "d"},
    ]
    argv = params.to_argv(declared, {"items": ["a", "b"], "tag": ["x", "y"], "verbose": True, "limit": 5.0})
    assert argv == ["--tag", "x", "--tag", "y", "--verbose", "--limit", "5", "a", "b"]


@pytest.mark.parametrize("bad, fragment", [
    ([{"name": "Bad-Name", "description": "d"}], "snake_case"),
    ([{"name": "stdin", "description": "d"}], "snake_case"),
    ([{"name": "on", "type": "boolean", "description": "d"}], "a boolean needs a `flag`"),
    ([{"name": "x", "type": "float", "description": "d"}], "`type` must be one of"),
    ([{"name": "x", "flag": "nodash", "description": "d"}], "must look like --name"),
    ([{"name": "x"}], "needs a `description`"),
    ([{"name": "a", "type": "array", "description": "d"}, {"name": "b", "description": "d"}], "may follow"),
    ([{"name": "x", "description": "d", "colour": "red"}], "unknown key(s) colour"),
])
def test_param_table_problems(bad, fragment):
    assert any(fragment in p for p in params.problems(bad))


def test_invalid_params_table_stops_the_module_loading(tmp_path):
    (tmp_path / "broken.py").write_text("def run(args, stdin):\n    return ''\n")
    (tmp_path / "broken.skill").write_text('summary = "x"\n[[params]]\nname = "on"\ntype = "boolean"\ndescription = "d"\n')
    local = ModuleRegistry(tmp_path)
    local.load()
    assert "broken" not in local.modules
    assert "a boolean needs a `flag`" in local.warnings[0]


# ── man pages as resources ───────────────────────────────────────────────────

def test_man_pages_are_resources(registry):
    async def fn(c):
        listed = await c.list_resources()
        page = await c.read_resource(MAN_URI + "fetch")
        builtin = await c.read_resource(MAN_URI + "grep")
        return listed, page, builtin

    listed, page, builtin = _call(build_server(registry), fn)
    uris = {str(r.uri) for r in listed.resources}
    assert {MAN_URI + "fetch", MAN_URI + "filter", MAN_URI + "grep"} <= uris
    assert MAN_URI + "rm" not in uris and MAN_URI + "cd" not in uris  # not usable over MCP
    assert page.contents[0].mime_type == "text/markdown"
    assert page.contents[0].text == registry.get("fetch").doc_md
    assert "grep" in builtin.contents[0].text


def test_unknown_resource_is_an_error(registry):
    async def fn(c):
        with pytest.raises(MCPError, match="Unknown resource"):
            await c.read_resource(MAN_URI + "rm")

    _call(build_server(registry), fn)


# ── hot reload and list_changed ──────────────────────────────────────────────

def _module_dir(tmp_path):
    (tmp_path / "hello.py").write_text("def run(args, stdin):\n    return 'hi'\n")
    local = ModuleRegistry(tmp_path)
    local.load()
    return local


def test_reload_notifies_legacy_clients(tmp_path):
    local = _module_dir(tmp_path)
    app = ShellcraftMCP(local)
    watcher = ModuleWatcher(tmp_path)
    seen = []

    async def handler(message):
        seen.append(getattr(getattr(message, "root", message), "method", None))

    async def main():
        async with Client(app.server, mode="legacy", message_handler=handler) as client:
            await client.list_tools()  # the server remembers this session
            (tmp_path / "bye.py").write_text("def run(args, stdin):\n    return 'bye'\n")
            assert await app.reload_if_changed(watcher) == ["bye.py"]
            with anyio.fail_after(5):
                while "notifications/tools/list_changed" not in seen:
                    await anyio.sleep(0.01)
            return {t.name for t in (await client.list_tools()).tools}

    names = anyio.run(main)
    assert {"hello", "bye"} <= names
    assert "notifications/resources/list_changed" in seen


def test_reload_notifies_listening_clients(tmp_path):
    local = _module_dir(tmp_path)
    app = ShellcraftMCP(local)

    async def main():
        async with Client(app.server) as client:
            async with client.listen(tools_list_changed=True, resources_list_changed=True) as sub:
                await app.notify_changed()
                with anyio.fail_after(5):
                    return type(await sub.__anext__()).__name__

    assert anyio.run(main) in ("ToolsListChanged", "ResourcesListChanged")


def test_server_advertises_list_changed(registry):
    caps = build_server(registry).create_initialization_options().capabilities
    assert caps.tools.list_changed and caps.resources.list_changed


def test_watcher_reports_added_changed_and_removed_files(tmp_path):
    (tmp_path / "a.py").write_text("x")
    watcher = ModuleWatcher(tmp_path)
    assert watcher.changes() == []
    (tmp_path / "a.skill").write_text("s")
    (tmp_path / "notes.txt").write_text("ignored")
    assert watcher.changes() == ["a.skill"]
    (tmp_path / "a.py").write_text("longer")
    (tmp_path / "a.skill").unlink()
    assert watcher.changes() == ["a.py", "a.skill"]


# ── HTTP transport address ───────────────────────────────────────────────────

@pytest.mark.parametrize("value, expected", [
    ("127.0.0.1:8765", ("127.0.0.1", 8765)),
    ("localhost:9000", ("localhost", 9000)),
    ("[::1]:8765", ("::1", 8765)),
    (":8765", ("127.0.0.1", 8765)),
])
def test_http_address_loopback(value, expected):
    assert parse_http_address(value) == expected


@pytest.mark.parametrize("value, fragment", [
    ("0.0.0.0:8765", "refusing to listen on 0.0.0.0"),
    ("192.168.1.10:8765", "refusing"),
    ("myhost.lan:8765", "refusing"),
    ("127.0.0.1", "invalid address"),
    ("127.0.0.1:99999", "invalid address"),
])
def test_http_address_refused(value, fragment):
    with pytest.raises(AddressError, match=fragment):
        parse_http_address(value)
