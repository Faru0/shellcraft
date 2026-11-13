"""The AI in the demo recording (tools/demo3.py): a stand-in for the Claude Code client.

`ask` starts it the way it starts `claude` (it is named by SHELLCRAFT_CLAUDE), so the recording needs
no account, no network and costs nothing, and comes out the same every time. Its sentences are
written here. Everything else is real: it starts ShellCraft's MCP server from the `--mcp-config`
file `ask` hands it and calls the same tools a real client would (`get_recent_output`,
`run_in_shell`, `send_tui_alert`, `set_clip`), so the job in the footer, the alert and what Alt-v pastes all
come from the shell itself. The numbers in its answer are counted from what its command printed,
and it reports no cost because there is none.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import Implementation

SERVER = "shellcraft"
NAME = Implementation(name="demo-ai", version="1")  # the alert and `ai log` show who called
PACE = 0.06  # seconds between the pieces of an answer
THINK = 0.6  # and before each step, so that a viewer can follow
COUNT = "grep ERROR app.log | cut -d ' ' -f 4- | sort | uniq -c"
CHECK = "curl -s $API/api/health --jq checks"


def say(event: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


def write(text: str) -> None:
    """One message of the answer, a few characters at a time, as a client streams it."""
    time.sleep(THINK)
    for piece in re.findall(r"\S+\s*|\s+", text):
        say({"type": "stream_event", "event": {"type": "content_block_delta",
                                                "delta": {"type": "text_delta", "text": piece}}})
        time.sleep(PACE)
    say({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}})


async def call(session: ClientSession, tool: str, **arguments: Any) -> str:
    await asyncio.sleep(THINK)
    say({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": f"mcp__{SERVER}__{tool}", "input": arguments}]}})
    result = await session.call_tool(tool, arguments)
    text = "\n".join(getattr(part, "text", "") for part in result.content)
    say({"type": "user", "message": {"content": [{"type": "tool_result", "content": text}]}})
    return text


async def answer(session: ClientSession) -> str:
    await call(session, "get_recent_output", lines=40)
    write("app.log is the file to look at. Let me count its errors in your shell.\n")
    counted = await call(session, "run_in_shell", command=COUNT, reason="count the errors in app.log by message",
                         wait_seconds=20)
    rows = re.findall(r"^\s*(\d+)\s+(.+?)\s*$", json.loads(counted).get("output", ""), re.M)
    worst = max(rows, key=lambda row: int(row[0]), default=("0", "nothing"))
    total = sum(int(n) for n, _ in rows)
    write(f"{worst[0]} of the {total} errors are `{worst[1]}`: the database stopped answering, the disk is a "
          "side issue.\n")
    await call(session, "send_tui_alert", message=f"app.log: {worst[1]} x{worst[0]}, check the API's health",
               severity="warn", content=CHECK)
    await call(session, "set_clip", text=CHECK)
    last = "The next check is in the clip register: press Alt-v to paste it."
    write(last)
    return last


async def main() -> int:
    sys.stdin.read()  # the question and what the shell added to it
    argv = sys.argv[1:]
    with open(argv[argv.index("--mcp-config") + 1], encoding="utf-8") as fh:
        entry = json.load(fh)["mcpServers"][SERVER]
    began = time.monotonic()
    say({"type": "system", "subtype": "init", "session_id": "demo",
         "mcp_servers": [{"name": SERVER, "status": "connected"}]})
    params = StdioServerParameters(command=entry["command"], args=entry["args"], env=entry.get("env"))
    async with stdio_client(params) as (reader, writer), ClientSession(reader, writer, client_info=NAME) as session:
        await session.initialize()
        last = await answer(session)
    say({"type": "result", "subtype": "success", "is_error": False, "result": last,
         "session_id": "demo", "num_turns": 5, "duration_ms": int((time.monotonic() - began) * 1000)})
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
