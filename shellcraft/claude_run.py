"""Run the Claude Code client with no terminal: one prompt in, the answer out as it is written.

`ask` (shellcraft.commands.ask_ai) and `shellcraft --watch-rounds` both start it through here:

    claude -p --mcp-config FILE --strict-mcp-config --allowedTools mcp__shellcraft [--tools ""]
           --output-format stream-json --verbose --include-partial-messages [--max-budget-usd N]
           [--agents FILE --agent NAME] ...

The prompt goes in on stdin (never on the command line, where `ps` shows it). FILE is a temporary
owner-only file naming one MCP server, ShellCraft's own; for `ask` its `env` block carries the
address and key of *this* shell (shellcraft.mainlink), so the client talks to the shell the
question was typed in, not to whichever shell started last. The client runs in its own process
group and the whole group is stopped on a cancel.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generator

CLIENT_ENV = "SHELLCRAFT_CLAUDE"  # the client to run, when it is not `claude` on PATH
SERVER = "shellcraft"  # the MCP server's name: its tools are mcp__shellcraft__NAME
STOP_WAIT = 3.0  # seconds a stopped client gets to end before it is killed
# Not to be inherited: a ShellCraft started from inside a Claude Code session is its own session.
_NOT_INHERITED = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")


class ClaudeError(Exception):
    """The client could not be started; the message is one line for the user."""


@dataclass
class Request:
    prompt: str
    servers: dict[str, Any]  # the `mcpServers` map: name -> {"command", "args", "env"}
    tools: tuple[str, ...] = (f"mcp__{SERVER}",)  # what it may use without asking (nobody is there to ask)
    own_tools: tuple[str, ...] | None = None  # the client's own tools it keeps (None: all, (): none; fewer is cheaper)
    blocked: tuple[str, ...] = ()  # tools it must not use
    system: str = ""  # added to the client's system prompt
    budget: float | None = None  # USD; the client stops when it is spent
    model: str = ""
    agent: str = ""  # run as this agent
    agents: dict[str, Any] | None = None  # agents handed over for this run: name -> {"description", "prompt", "tools"}
    resume: str = ""  # a session id: carry on from that conversation
    cwd: str | None = None


@dataclass
class Result:
    text: str = ""  # the final answer
    cost: float | None = None  # USD
    seconds: float = 0.0
    session: str = ""
    turns: int = 0
    error: str = ""  # why it stopped short; empty when it finished


def find_client() -> str:
    named = os.environ.get(CLIENT_ENV, "").strip()
    if named:
        found = shutil.which(named) or (named if os.path.isfile(named) else None)
        if found is None:
            raise ClaudeError(f"{CLIENT_ENV} names {named}, which is not a program")
        return found
    found = shutil.which("claude")
    if found is None:
        raise ClaudeError("the Claude Code client (`claude`) is not on PATH; install it "
                          f"(https://claude.com/claude-code), or set {CLIENT_ENV} to its path")
    return found


def shell_server(modules: Path | None = None, allow_system: bool = False) -> dict[str, Any]:
    """The MCP entry for the running shell: `--mcp` with this interpreter, and in its environment
    the way into this shell (empty outside a main shell: the server then finds one by itself)."""
    import shellcraft
    from shellcraft.jobs import MAIN_LINK_ENV

    root = str(Path(shellcraft.__file__).resolve().parent.parent)
    args = ["-m", "shellcraft.cli", "--mcp"]
    if modules is not None:
        args += ["--modules", str(modules)]
    if allow_system:
        args.append("--allow-system")
    env = {"PYTHONPATH": os.pathsep.join(p for p in (root, os.environ.get("PYTHONPATH")) if p), **MAIN_LINK_ENV}
    return {"command": sys.executable, "args": args, "env": env}


def command(request: Request, client: str, config: str, agents: str = "") -> list[str]:
    """The client's command line (the prompt is not in it: it is written to stdin). `agents` is the
    file that holds request.agents."""
    argv = [client, "-p", "--mcp-config", config, "--strict-mcp-config",
            "--output-format", "stream-json", "--verbose", "--include-partial-messages"]
    if agents:
        argv += ["--agents", agents]
    if request.own_tools is not None:
        argv += ["--tools", ",".join(request.own_tools)]
    if request.tools:
        argv += ["--allowedTools", *request.tools]
    if request.blocked:
        argv += ["--disallowedTools", *request.blocked]
    if request.budget is not None:
        argv += ["--max-budget-usd", f"{request.budget:g}"]
    if request.model:
        argv += ["--model", request.model]
    if request.agent:
        argv += ["--agent", request.agent]
    if request.resume:
        argv += ["--resume", request.resume]
    if request.system:
        argv += ["--append-system-prompt", request.system]
    return argv


def write_config(servers: dict[str, Any]) -> str:
    """The servers as a temporary --mcp-config file only the owner can read (it holds the key to the shell)."""
    return _temp_json("shellcraft-mcp-", {"mcpServers": servers})


def _temp_json(prefix: str, data: dict[str, Any]) -> str:
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return path


def _remove(*paths: str) -> None:
    for path in paths:
        try:
            if path:
                os.unlink(path)
        except OSError:
            pass


def _stop(proc: subprocess.Popen[bytes]) -> None:
    """End the client and what it started (its MCP server): the whole process group."""
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            proc.terminate()
        else:
            os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(STOP_WAIT)
    except (OSError, subprocess.TimeoutExpired):
        try:
            if sys.platform == "win32":
                proc.kill()
            else:
                os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            proc.wait(STOP_WAIT)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _why(event: dict[str, Any], request: Request) -> str:
    """A result event that is not a success, as one line."""
    subtype = str(event.get("subtype") or "")
    if "budget" in subtype:
        spent = f"${request.budget:g}" if request.budget is not None else "the budget"
        return f"stopped: the budget of {spent} for one run was reached"
    if "max_turns" in subtype:
        return "stopped: too many steps"
    said = event.get("result")
    if isinstance(said, str) and said.strip():
        return said.strip().splitlines()[0][:300]
    return f"the client stopped ({subtype or 'no reason given'})"


def run(request: Request, stop: threading.Event | None = None) -> Generator[tuple[str, Any], None, None]:
    """Start the client and yield what happens: ("text", str) for each piece of the answer as it
    is written, ("break", None) where one message of it ends, ("tool", (name, input)) for each tool
    it calls, ("note", str) for a problem worth a line, and last ("result", Result). Setting `stop`
    ends the client (the result then says "stopped"). Raises ClaudeError when it cannot be started."""
    client = find_client()
    config = write_config(request.servers)
    agents = _temp_json("shellcraft-agents-", request.agents) if request.agents else ""
    errors = tempfile.TemporaryFile()  # noqa: SIM115 — closed in the finally below; a file, so stderr never blocks
    group: dict[str, Any] = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}  # type: ignore[attr-defined]
                             if sys.platform == "win32" else {"start_new_session": True})
    env = {k: v for k, v in os.environ.items() if k not in _NOT_INHERITED}
    began = time.monotonic()
    try:
        proc = subprocess.Popen(command(request, client, config, agents), stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=errors, cwd=request.cwd, env=env, **group)
    except OSError as exc:
        errors.close()
        _remove(config, agents)
        raise ClaudeError(f"cannot start {client}: {exc.strerror or exc}") from None

    done = threading.Event()

    def feed() -> None:
        assert proc.stdin is not None
        try:
            proc.stdin.write(request.prompt.encode("utf-8"))
            proc.stdin.close()
        except OSError:
            pass  # it ended before reading: its result (or its stderr) says why

    def watch() -> None:
        while stop is not None and not done.is_set():
            if stop.wait(0.2):
                _stop(proc)
                return

    threading.Thread(target=feed, daemon=True, name="shellcraft:claude-stdin").start()
    threading.Thread(target=watch, daemon=True, name="shellcraft:claude-watch").start()
    result = Result()
    final: dict[str, Any] | None = None
    streamed = False  # the text of the message being written already came piece by piece
    assert proc.stdout is not None
    try:
        for raw in proc.stdout:
            try:
                event = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            kind = event.get("type")
            if kind == "stream_event":
                delta = (event.get("event") or {}).get("delta") or {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    streamed = True
                    yield "text", delta["text"]
            elif kind == "assistant":
                for block in (event.get("message") or {}).get("content") or []:
                    if block.get("type") == "text":
                        if block.get("text") and not streamed:
                            yield "text", block["text"]
                        streamed = False
                        yield "break", None  # the end of a message is the end of a line
                    elif block.get("type") == "tool_use":
                        yield "tool", (str(block.get("name", "")), block.get("input") or {})
            elif kind == "system" and event.get("subtype") == "init":
                result.session = str(event.get("session_id") or "")
                for server in event.get("mcp_servers") or []:
                    if isinstance(server, dict) and server.get("status") not in ("connected", "pending", None):
                        yield "note", f"the {server.get('name')} tools did not start ({server.get('status')})"
            elif kind == "result":
                final = event
        code = proc.wait()
        result.seconds = time.monotonic() - began
        if final is not None:
            result.text = final["result"] if isinstance(final.get("result"), str) else ""
            cost = final.get("total_cost_usd")
            result.cost = float(cost) if isinstance(cost, (int, float)) else None
            result.session = str(final.get("session_id") or result.session)
            result.turns = int(final.get("num_turns") or 0)
            if final.get("is_error") or final.get("subtype") != "success":
                result.error = _why(final, request)
        elif stop is not None and stop.is_set():
            result.error = "stopped"
        else:
            errors.seek(0)
            said = errors.read().decode("utf-8", errors="replace").strip().splitlines()
            result.error = (said[-1][:300] if said else f"the client ended with status {code} and no answer")
        yield "result", result
    finally:
        done.set()
        _stop(proc)
        proc.stdout.close()
        errors.close()
        _remove(config, agents)
