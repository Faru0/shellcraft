"""Expose loaded modules as MCP tools over stdio.

Each module becomes one tool whose description is its parsed .skill file. A
`shellcraft_pipeline` tool additionally runs full `a | b | c` command lines.
Nothing may be written to stdout here except MCP protocol traffic.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import anyio
import mcp_types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import MCPError

from core import __version__
from core.builtins import BUILTINS
from core.context import ShellContext, to_text
from core.loader import ModuleRegistry
from core.modkit import ModuleError
from core.parser import ParseError
from core.pipeline import PipelineError, run_line

log = logging.getLogger("shellcraft.mcp")

PIPELINE_TOOL = "shellcraft_pipeline"

MODULE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "args": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Command-line arguments, one list item per argument (e.g. [\"-i\", \"error\"]).",
        },
        "stdin": {
            "type": "string",
            "description": "Text piped into the tool, as if from `previous | tool`. Omit or leave empty for none.",
        },
    },
    "additionalProperties": False,
}

PIPELINE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "command": {
            "type": "string",
            "description": "A ShellCraft command line such as `fetch data.csv | filter -i error`.",
        },
    },
    "required": ["command"],
    "additionalProperties": False,
}

SERVER_INSTRUCTIONS = (
    "ShellCraft tools are Unix-style text filters: each takes CLI-style `args` and optional "
    "`stdin` text and returns text. Chain them yourself by passing one tool's output as the next "
    f"tool's `stdin`, or call `{PIPELINE_TOOL}` with a full `a | b` command line."
)


def _pipeline_description(registry: ModuleRegistry) -> str:
    modules = "\n".join(f"  {n}: {registry.get(n).summary}" for n in registry.names())
    usable = sorted(n for n, b in BUILTINS.items() if not (b.stateful or b.writes or b.sensitive))
    return (
        "Run a ShellCraft pipeline: commands joined by `|`, each receiving the previous command's "
        "output as stdin. Use it to combine several tools in one call. File redirection (`>`, `>>`), "
        "file-changing commands (tee, cp, mv, rm, mkdir, touch), env, and shell-state commands "
        "(cd, theme, settings, exit) are disabled. Run `man <command>` inside a pipeline for a command's manual.\n\n"
        f"Modules:\n{modules}\n\nBuilt-in commands: {', '.join(usable)}"
    )


def build_server(registry: ModuleRegistry, allow_system: bool = False) -> Server:
    ctx = ShellContext(registry=registry, allow_redirect=False, allow_system=allow_system,
                       allow_stateful=False, allow_writes=False, allow_sensitive=False)

    async def on_list_tools(_ctx, _params) -> types.ListToolsResult:
        tools = [
            types.Tool(name=spec.name, description=spec.description, input_schema=MODULE_INPUT_SCHEMA)
            for spec in (registry.get(n) for n in registry.names())
        ]
        tools.append(types.Tool(name=PIPELINE_TOOL, description=_pipeline_description(registry),
                                input_schema=PIPELINE_INPUT_SCHEMA))
        return types.ListToolsResult(tools=tools)

    async def on_call_tool(_ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        arguments = params.arguments or {}
        if params.name == PIPELINE_TOOL:
            command = str(arguments.get("command", ""))
            return await _call(lambda: _run_pipeline(command, ctx))

        spec = registry.get(params.name)
        if spec is None:
            raise MCPError(types.INVALID_PARAMS, f"Unknown tool: {params.name}")
        raw_args = arguments.get("args") or []
        if not isinstance(raw_args, list):
            return _error("`args` must be an array of strings")
        args = [str(a) for a in raw_args]
        stdin = str(arguments.get("stdin") or "")
        return await _call(lambda: to_text(spec.run(args, stdin)))

    return Server(
        "shellcraft",
        version=__version__,
        title="ShellCraft",
        instructions=SERVER_INSTRUCTIONS,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


def _run_pipeline(command: str, ctx: ShellContext) -> str:
    result = run_line(command, ctx)
    return "" if result is None else to_text(result.output)


async def _call(fn) -> types.CallToolResult:
    try:
        text = await anyio.to_thread.run_sync(fn)
    except ParseError as exc:
        return _error(f"parse error: {exc.message}")
    except PipelineError as exc:
        return _error(f"{exc.name} (segment {exc.index}/{exc.total}): {exc.message}")
    except ModuleError as exc:
        return _error(str(exc))
    except Exception as exc:  # noqa: BLE001 — tool failures go back to the model, not the transport
        log.exception("tool call failed")
        return _error(f"{type(exc).__name__}: {exc}")
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)])


def _error(message: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=message)], is_error=True)


def serve(registry: ModuleRegistry, allow_system: bool = False) -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="shellcraft-mcp: %(message)s")
    for warning in registry.warnings:
        log.warning(warning)
    log.info("serving %d module(s): %s", len(registry), ", ".join(registry.names()) or "none")
    server = build_server(registry, allow_system=allow_system)

    async def main() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    anyio.run(main)
