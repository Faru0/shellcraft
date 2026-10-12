"""Expose loaded modules as MCP tools, over stdio or streamable HTTP.

Each module becomes one tool whose description is its parsed .skill file; a module that declares
.skill [[params]] gets a typed input schema, otherwise a raw CLI `args` array. A
`shellcraft_pipeline` tool additionally runs full `a | b | c` command lines. Every man page is
also a resource (`shellcraft://man/<name>`). When the modules directory changes, the registry
reloads and connected clients get tools/list_changed and resources/list_changed.
Over stdio, nothing may be written to stdout except MCP protocol traffic.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import secrets
import sys
import weakref
from typing import Any, Callable, Iterable

import anyio
import mcp_types as types
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.stdio import stdio_server
from mcp.server.subscriptions import InMemorySubscriptionBus, ListenHandler, ResourcesListChanged, ToolsListChanged
from mcp.shared.exceptions import MCPError

from core import __version__
from core.builtins import BUILTINS, manual_text
from core.context import CommandError, ShellContext, to_text
from core.loader import ModuleRegistry
from core.modkit import ModuleError
from core.params import ParamError
from core.parser import ParseError
from core.pipeline import PipelineError, run_line
from core.watch import ModuleWatcher

log = logging.getLogger("shellcraft.mcp")

PIPELINE_TOOL = "shellcraft_pipeline"
MAN_URI = "shellcraft://man/"
WATCH_INTERVAL = 1.0  # seconds between checks of the modules directory
# Limits on every tool call, so a hung module or a huge result can't take the server down.
CALL_TIMEOUT = float(os.environ.get("SHELLCRAFT_MCP_TIMEOUT", "120"))  # seconds
MAX_STDIN = 10 * 1024 * 1024  # bytes of `stdin` a call may send
MAX_OUTPUT = 2 * 1024 * 1024  # characters of output returned (the rest is cut, with a note)
TOKEN_ENV = "SHELLCRAFT_MCP_TOKEN"

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
    "ShellCraft tools are Unix-style text filters: each takes its parameters and optional "
    "`stdin` text and returns text. Chain them yourself by passing one tool's output as the next "
    f"tool's `stdin`, or call `{PIPELINE_TOOL}` with a full `a | b` command line. Each command's "
    f"manual is a resource at {MAN_URI}<name>."
)


def _pipeline_description(registry: ModuleRegistry, names: list[str] | None = None) -> str:
    modules = "\n".join(f"  {n}: {registry.get(n).summary}" for n in (registry.names() if names is None else names))
    return (
        "Run a ShellCraft pipeline: commands joined by `|`, each receiving the previous command's "
        "output as stdin. Use it to combine several tools in one call. Inside a pipeline, modules "
        "take CLI-style switches (see each module's usage). File redirection (`>`, `>>`), "
        "file-changing commands (tee, cp, mv, rm, mkdir, touch), env, and shell-state commands "
        "(cd, theme, settings, exit) are disabled. Run `man <command>` inside a pipeline for a command's manual.\n\n"
        "The command line may also be a small script, in bash syntax: `a ; b`, `a && b || c`, "
        "`for h in $(cat hosts.txt); do queryDns $h; done`, `for n in {1..5}; do …; done`, "
        "`if [ \"$x\" = a ]; then …; elif [[ $x == *.gov ]]; then …; else …; fi` (with `test`, `[`, `[[`). "
        "Or with `-py`, where variables are never split and conditions are Python: "
        "`for -py ip in (cat ips.txt) { if (ip.startswith(\"10.\")) { … } else { … } }` "
        "(see `man for` and `man if`). A failing statement in a script shows as a `✗ name: message` "
        "line and the script goes on.\n\n"
        f"Modules:\n{modules}\n\nBuilt-in commands: {', '.join(_usable_builtins())}"
    )


def _usable_builtins() -> list[str]:
    return sorted(n for n, b in BUILTINS.items() if not (b.stateful or b.writes or b.sensitive))


class _Server(Server):
    """Always advertises listChanged for tools and resources, whichever transport asks."""

    def create_initialization_options(self, notification_options=None, *args, **kwargs):
        notification_options = notification_options or NotificationOptions(tools_changed=True,
                                                                            resources_changed=True)
        return super().create_initialization_options(notification_options, *args, **kwargs)


class ShellcraftMCP:
    """The MCP server plus what it needs to tell connected clients that the modules changed."""

    def __init__(self, registry: ModuleRegistry, allow_system: bool = False,
                 expose: Iterable[str] | None = None, timeout: float | None = None):
        self.registry = registry
        # Which modules clients may use: those whose .skill doesn't say `expose = false`, narrowed
        # further by --expose NAME,... when given. The pipeline tool follows the same rule.
        self.expose_only = None if expose is None else set(expose)
        self.timeout = CALL_TIMEOUT if timeout is None else timeout
        self.ctx = ShellContext(registry=registry, allow_redirect=False, allow_system=allow_system,
                                allow_stateful=False, allow_writes=False, allow_sensitive=False,
                                allow_aliases=False, module_filter=self.exposed)
        # Clients on the 2026-07-28+ protocol hear about changes through subscriptions/listen;
        # earlier clients through their connection, which we remember from their requests.
        self.bus = InMemorySubscriptionBus()
        self.connections: weakref.WeakSet[Any] = weakref.WeakSet()
        self.server = _Server(
            "shellcraft",
            version=__version__,
            title="ShellCraft",
            instructions=SERVER_INSTRUCTIONS,
            on_list_tools=self._list_tools,
            on_call_tool=self._call_tool,
            on_list_resources=self._list_resources,
            on_read_resource=self._read_resource,
            on_subscriptions_listen=ListenHandler(self.bus),
        )

    def exposed(self, name: str) -> bool:
        spec = self.registry.get(name)
        if spec is None or not spec.exposed:
            return False
        return self.expose_only is None or name in self.expose_only

    def tool_names(self) -> list[str]:
        return [n for n in self.registry.names() if self.exposed(n)]

    def _remember(self, request_ctx: Any) -> None:
        # The per-request ServerSession is thrown away after each request; the Connection behind it
        # lives as long as the client stays connected. mcp 2.x has no public accessor for it here.
        connection = getattr(request_ctx, "connection", None) or getattr(
            getattr(request_ctx, "session", None), "_connection", None)
        if connection is not None and hasattr(connection, "send_tool_list_changed"):
            try:
                self.connections.add(connection)
            except TypeError:  # not weak-referenceable: this client won't get legacy notifications
                pass

    # ── tools ────────────────────────────────────────────────────────────────

    async def _list_tools(self, request_ctx, _params) -> types.ListToolsResult:
        self._remember(request_ctx)
        tools = [
            types.Tool(name=spec.name, description=spec.description,
                       input_schema=spec.input_schema or MODULE_INPUT_SCHEMA)
            for spec in (self.registry.get(n) for n in self.tool_names())
        ]
        tools.append(types.Tool(name=PIPELINE_TOOL, description=_pipeline_description(self.registry, self.tool_names()),
                                input_schema=PIPELINE_INPUT_SCHEMA))
        return types.ListToolsResult(tools=tools)

    async def _call_tool(self, request_ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        self._remember(request_ctx)
        arguments = dict(params.arguments or {})
        if params.name == PIPELINE_TOOL:
            command = str(arguments.get("command", ""))
            if len(command) > MAX_STDIN:
                return _error(f"the command line is too long (over {MAX_STDIN} characters)")
            return await _call(lambda: _run_pipeline(command, self.ctx), self.timeout)

        spec = self.registry.get(params.name)
        if spec is None or not self.exposed(params.name):
            raise MCPError(types.INVALID_PARAMS, f"Unknown tool: {params.name}")
        stdin = arguments.pop("stdin", None)
        stdin = "" if stdin is None else str(stdin)
        if len(stdin) > MAX_STDIN:
            return _error(f"stdin is too large (over {MAX_STDIN} characters)")
        if spec.input_schema is not None:
            try:
                args = spec.argv(arguments)
            except ParamError as exc:
                return _error(f"{spec.name}: {exc}")
        else:
            raw_args = arguments.get("args") or []
            if not isinstance(raw_args, list):
                return _error("`args` must be an array of strings")
            args = [str(a) for a in raw_args]
        return await _call(lambda: to_text(spec.run(args, stdin)), self.timeout)

    # ── resources: man pages ─────────────────────────────────────────────────

    def _manual_names(self) -> list[str]:
        modules = [n for n in self.tool_names() if self.registry.get(n).doc_md]
        return modules + [n for n in _usable_builtins() if n not in self.registry.modules]

    async def _list_resources(self, request_ctx, _params) -> types.ListResourcesResult:
        self._remember(request_ctx)
        resources = []
        for name in self._manual_names():
            spec = self.registry.get(name)
            summary = spec.summary if spec else BUILTINS[name].summary
            kind = "module" if spec else "built-in command"
            resources.append(types.Resource(uri=MAN_URI + name, name=f"man {name}", title=f"{name} manual",
                                            description=f"Manual for the {kind} `{name}`: {summary}",
                                            mime_type="text/markdown"))
        return types.ListResourcesResult(resources=resources)

    async def _read_resource(self, request_ctx, params: types.ReadResourceRequestParams) -> types.ReadResourceResult:
        self._remember(request_ctx)
        uri = str(params.uri)
        name = uri[len(MAN_URI):] if uri.startswith(MAN_URI) else ""
        if name not in self._manual_names():
            raise MCPError(types.INVALID_PARAMS, f"Unknown resource: {uri}")
        try:
            text = manual_text(self.registry, name)
        except CommandError as exc:
            raise MCPError(types.INVALID_PARAMS, str(exc)) from None
        return types.ReadResourceResult(
            contents=[types.TextResourceContents(uri=uri, mime_type="text/markdown", text=text)])

    # ── hot reload ───────────────────────────────────────────────────────────

    async def notify_changed(self) -> None:
        """Tell every connected client that the tool and resource lists changed."""
        await self.bus.publish(ToolsListChanged())
        await self.bus.publish(ResourcesListChanged())
        for connection in list(self.connections):
            try:
                await connection.send_tool_list_changed()
                await connection.send_resource_list_changed()
            except Exception:  # noqa: BLE001 — a closed connection just stops getting notified
                self.connections.discard(connection)

    async def reload_if_changed(self, watcher: ModuleWatcher) -> list[str]:
        changed = watcher.changes()
        if changed:
            self.registry.reload(changed)
            log.info("modules changed (%s): reloaded %d module(s)", ", ".join(changed), len(self.registry))
            for warning in self.registry.warnings:
                log.warning(warning)
            await self.notify_changed()
        return changed

    async def watch(self, interval: float = WATCH_INTERVAL) -> None:
        watcher = ModuleWatcher(self.registry.directory)
        while True:
            await anyio.sleep(interval)
            await self.reload_if_changed(watcher)


def build_server(registry: ModuleRegistry, allow_system: bool = False, expose: Iterable[str] | None = None,
                 timeout: float | None = None) -> Server:
    return ShellcraftMCP(registry, allow_system, expose, timeout).server


def _run_pipeline(command: str, ctx: ShellContext) -> str:
    result = run_line(command, ctx)
    return "" if result is None else to_text(result.output)


async def _call(fn: Callable[[], str], timeout: float = CALL_TIMEOUT) -> types.CallToolResult:
    try:
        with anyio.fail_after(timeout):
            # abandon_on_cancel: a timed-out call returns an error now; its thread finishes on its own.
            text = await anyio.to_thread.run_sync(fn, abandon_on_cancel=True)
    except TimeoutError:
        return _error(f"the call did not finish within {timeout:.0f} s and was abandoned")
    except ParseError as exc:
        return _error(f"parse error: {exc.message}")
    except PipelineError as exc:
        return _error(f"{exc.name} (segment {exc.index}/{exc.total}): {exc.message}")
    except ModuleError as exc:
        return _error(str(exc))
    except Exception as exc:  # noqa: BLE001 — tool failures go back to the model, not the transport
        log.exception("tool call failed")
        return _error(f"{type(exc).__name__}: {exc}")
    if len(text) > MAX_OUTPUT:
        text = text[:MAX_OUTPUT] + f"\n… [output cut after {MAX_OUTPUT} characters]\n"
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)])


def _error(message: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=message)], is_error=True)


# ── transports ───────────────────────────────────────────────────────────────

class AddressError(ValueError):
    pass


def parse_http_address(value: str) -> tuple[str, int]:
    """'127.0.0.1:8765', 'localhost:8765', '[::1]:8765' or ':8765' → (host, port); loopback only."""
    host, sep, port = value.rpartition(":")
    if not sep or not port.isdigit() or not 0 < int(port) < 65536:
        raise AddressError(f"invalid address '{value}' (expected HOST:PORT, e.g. 127.0.0.1:8765)")
    host = host.strip("[]") or "127.0.0.1"
    if host != "localhost":
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise AddressError(f"refusing to listen on {host}: the MCP HTTP server only binds to this "
                               "machine (127.0.0.1, localhost or ::1), because connected clients can read "
                               "your files")
    return host, int(port)


def _log_startup(app: ShellcraftMCP, watch: bool) -> None:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="shellcraft-mcp: %(message)s")
    for warning in app.registry.warnings:
        log.warning(warning)
    log.info("serving %d module(s): %s", len(app.registry), ", ".join(app.registry.names()) or "none")
    if watch:
        log.info("hot reload on: watching %s", app.registry.directory)


def serve(registry: ModuleRegistry, allow_system: bool = False, watch: bool = True,
          expose: Iterable[str] | None = None) -> None:
    """Serve over stdio."""
    app = ShellcraftMCP(registry, allow_system, expose)
    _log_startup(app, watch)

    async def main() -> None:
        async with anyio.create_task_group() as tg:
            if watch:
                tg.start_soon(app.watch)
            async with stdio_server() as (read_stream, write_stream):
                await app.server.run(read_stream, write_stream, app.server.create_initialization_options())
            tg.cancel_scope.cancel()

    anyio.run(main)


def resolve_token(token: str | None, no_token: bool = False) -> str | None:
    """The bearer token the HTTP transport requires: the one given, else $SHELLCRAFT_MCP_TOKEN,
    else a fresh random one. None only when `no_token` asks for an open server."""
    if no_token:
        return None
    return token or os.environ.get(TOKEN_ENV) or secrets.token_urlsafe(24)


def require_bearer(asgi: Any, token: str) -> Any:
    """ASGI middleware: every HTTP request must carry `Authorization: Bearer <token>`. Any other
    local user or process can reach a loopback port; the token keeps them out."""
    expected = f"Bearer {token}".encode()

    async def guarded(scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            given = headers.get(b"authorization", b"")
            if not secrets.compare_digest(given, expected):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain"), (b"www-authenticate", b"Bearer")]})
                await send({"type": "http.response.body",
                            "body": b"unauthorized: send the header  Authorization: Bearer <token>\n"})
                return
        await asgi(scope, receive, send)

    return guarded


def serve_http(registry: ModuleRegistry, host: str, port: int, allow_system: bool = False,
               watch: bool = True, token: str | None = None, expose: Iterable[str] | None = None) -> None:
    """Serve over streamable HTTP at http://HOST:PORT/mcp (loopback addresses only). With a
    `token`, every request must send `Authorization: Bearer <token>`."""
    import uvicorn
    from mcp.server.transport_security import TransportSecuritySettings

    app = ShellcraftMCP(registry, allow_system, expose)
    _log_startup(app, watch)
    shown = f"[{host}]" if ":" in host else host
    log.info("streamable HTTP on http://%s:%d/mcp", shown, port)
    if token:
        log.info("bearer token: %s  (clients send the header: Authorization: Bearer %s)", token, token)
    else:
        log.warning("no bearer token: any local process can use this server (--no-token)")
    # DNS-rebinding protection: browsers may only reach us under a loopback Host / Origin.
    hosts = sorted({f"{shown}:*", "127.0.0.1:*", "localhost:*", "[::1]:*"})
    security = TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts,
                                         allowed_origins=[f"http://{h}" for h in hosts])
    asgi = app.server.streamable_http_app(host=host, transport_security=security)
    if token:
        asgi = require_bearer(asgi, token)
    config = uvicorn.Config(asgi, host=host, port=port, log_level="warning", lifespan="on")

    async def main() -> None:
        async with anyio.create_task_group() as tg:
            if watch:
                tg.start_soon(app.watch)
            await uvicorn.Server(config).serve()
            tg.cancel_scope.cancel()

    anyio.run(main)
