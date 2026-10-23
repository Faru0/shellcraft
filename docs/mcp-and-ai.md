# MCP and the AI co-pilot

← [Manual](README.md)

ShellCraft can be an MCP server, so an AI client such as Claude Code or Claude Desktop can use
your modules as tools, read what's on your screen, and run commands in your shell, with your
approval. This page shows how to connect a client, what it can do, and how you stay in control.
The [ai/](../ai/README.md) folder has agents and skills that teach an AI client to work this way.

> [!WARNING]
> A connected AI client can read any file your user account can read, through commands like
> `cat`, `grep` and `curl`, and `curl` can send a file's contents anywhere. While your shell runs,
> it can also read the screen, your subshells' output and your command history, and run the
> commands you approve. Modules that use your API keys spend your quota when the AI calls them.
> Only connect AI clients you trust.

## Connect a client

The simplest setup: the AI client starts its own ShellCraft server whenever it needs one.

**Claude Code**

```bash
claude mcp add shellcraft -- /path/to/shellcraft/.venv/bin/python /path/to/shellcraft/main.py --mcp
```

**Claude Desktop**, or any client with an `mcpServers` setting:

```json
{
  "mcpServers": {
    "shellcraft": {
      "command": "/path/to/shellcraft/.venv/bin/python",
      "args": ["/path/to/shellcraft/main.py", "--mcp"]
    }
  }
}
```

Use full paths. On Windows, the Python path is `C:\\path\\to\\shellcraft\\.venv\\Scripts\\python.exe`.

To try the server by hand: `npx @modelcontextprotocol/inspector .venv/bin/python main.py --mcp`.

## Or run the server from your shell

`mcp start` runs an HTTP server in the background of your interactive shell, so you keep working
while the AI connects to it:

```
mcp start             # start it, and print the `claude mcp add` line to use
mcp                   # is it running?
mcp show              # the token and the `claude mcp add` line again
mcp log               # its log
mcp stop              # stop it
mcp token rotate      # a new token; prints the lines to update your client
```

The prompt shows `● mcp :38765` while it runs, and it stops when you leave ShellCraft. Its address
and token are saved in `config.json`, so a client you add once keeps working across sessions.

Every request must carry the token, because any program on your machine can reach a local port.
From a terminal, the same server is `shellcraft --mcp-http 127.0.0.1:38765`, with `--token TOKEN`
or `--no-token`.

## What the AI can do

| Tool | What it's for |
| --- | --- |
| One tool per module | Every module with a `.skill` file, with typed parameters. A module without one stays a shell command only |
| `shellcraft_pipeline` | Run a whole command line, such as `cat notes.txt \| grep -i todo \| sort`, using the read-only commands |
| `send_tui_alert` | Show an alert in your footer, optionally with data you can copy with a right-click |
| `get_recent_output` | Read your screen |
| `get_jobs`, `get_job_output` | Your subshells and background commands, and their output |
| `get_history` | Your command history, with secrets hidden |
| `get_clip`, `set_clip` | Read the clip register, or put a command there for you to paste with Alt-v |
| `open_subshell` | Open a light ShellCraft in your footer, named `✦ AI · finding`, that shows its findings |
| `run_in_shell` | Run a command in your shell. See [Commands the AI runs](#commands-the-ai-runs) |
| `request_human_review` and friends | Ask you before doing something risky. See [Reviewing with an AI](#reviewing-with-an-ai) |

The screen, jobs, history and clip tools need your interactive shell to be running. They work
with both kinds of server.

Limits that protect you:

- `shellcraft_pipeline` can't write files, change settings, read environment variables or run
  your system's programs, unless you start the server with `--allow-system`.
- A tool call that takes longer than 120 seconds is stopped (`SHELLCRAFT_MCP_TIMEOUT` changes it).
- `expose = false` in a module's `.skill` file keeps it away from AI clients entirely, and
  `--expose NAME,...` offers only the modules you name.

Man pages are available to the AI too, at `shellcraft://man/NAME`. When you add, change or remove a
module, connected clients see the change at once.

## Reviewing with an AI

You and the AI share a queue of messages, kept in `~/.shellcraft/review/` where only you can
read them.

### Tell the AI something

```
tell look at the last 50 lines of auth.log
```

### Flag something for it to look at

```
pytest | mark --type test-failure -p high
mark --file app.log --file config.toml -m "startup crash"
mark --diff old.py new.py
git diff | mark --type diff
```

The AI reads these and closes each one, saying what it did. Everything you mark is stored as it
is, so mark only what an AI may read.

### The AI asks before risky actions

When the AI wants your approval, it shows in the footer as an alert until you answer:

```
review                         # what's waiting
review show a1b2c3             # one item in full (the end of its id is enough)
review approve a1b2c3 go ahead
review reject a1b2c3 not on Fridays
```

This is advisory. ShellCraft tells the AI to ask, but it can't stop what the AI does with its own
tools.

## Commands the AI runs

`run_in_shell` works in three tiers:

1. **Read-only commands** such as `grep`, `cat`, `jq` and the query modules run at once, as a
   background job named `✦ AI: command`. You see what ran above your prompt.
2. **Anything else waits for you** in the review queue as `AI wants to run: command`.
   `review approve` runs it in your shell with your permissions and gives the AI the output.
   `review reject` means it never runs.
3. **A session grant** lets everything run at once: `settings ai_run on`, or approve the AI's own
   request for it. It's never saved, and `✦ run` shows in the prompt while it's on.

Finished review items are deleted after 7 days. Pending ones stay until you answer them.
