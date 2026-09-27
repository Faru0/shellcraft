import anyio
from mcp.client import Client

from core.mcp_server import PIPELINE_TOOL, build_server


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
                   lambda c: c.call_tool("filter", {"args": ["-i", "b"], "stdin": "apple\nBanana\ncherry\n"}))
    assert not result.is_error
    assert result.content[0].text == "Banana\n"


def test_module_error_is_tool_error(registry):
    result = _call(build_server(registry), lambda c: c.call_tool("filter", {"args": ["("], "stdin": "x"}))
    assert result.is_error and "invalid pattern" in result.content[0].text


def test_pipeline_tool_blocks_redirect_and_system(registry):
    server = build_server(registry)
    ok = _call(server, lambda c: c.call_tool(PIPELINE_TOOL, {"command": "echo a b c | filter -c b"}))
    assert ok.content[0].text == "1\n"
    redirect = _call(server, lambda c: c.call_tool(PIPELINE_TOOL, {"command": "echo x > pwned.txt"}))
    assert redirect.is_error and "redirection is disabled" in redirect.content[0].text
    system = _call(server, lambda c: c.call_tool(PIPELINE_TOOL, {"command": "echo x | tr x y"}))
    assert system.is_error and "command not found" in system.content[0].text
