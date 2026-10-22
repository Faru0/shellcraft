# MCP server and the AI co-pilot

← [README](../README.md) · [All docs](README.md)

ShellCraft's MCP server: modules as typed tools for AI clients, alerts, the review loop, and
the co-pilot tools that read your shell and run commands in it. The [ai/](../ai/README.md) folder
has agents and skills that teach an AI client to use them.

## Running the server

`python main.py --mcp` starts an MCP server over stdio; `python main.py --mcp-http 127.0.0.1:38765`
serves the same tools over streamable HTTP at `http://127.0.0.1:38765/mcp` (this machine only, with
DNS-rebinding protection).

The HTTP server requires a **bearer token** on every request, because any local user or process
can reach a loopback port: pass `--token TOKEN` (or set `SHELLCRAFT_MCP_TOKEN`), let it generate
one (printed at startup), or turn it off with `--no-token`.

To keep using the shell while the HTTP server runs, start it from inside the shell instead:
`mcp start [HOST:PORT]` runs it in the background as a child process (Ctrl-C at the prompt doesn't
stop it, and the prompt shows `● mcp :38765` while it runs) and prints the
`claude mcp add … --header "Authorization: Bearer …"` line to use. The token is made once and
**saved** in `config.json` (owner-only), and so is the address: every
`mcp start`, in any session, uses the same ones, so a client added once keeps working. `mcp show`
prints the token and the `claude mcp add` line again; `mcp token rotate` makes a new token (a
running server restarts with it) and prints `claude mcp remove shellcraft` and the new `claude mcp
add` line to run. `mcp` shows the status, `mcp log` the log, and `mcp stop` stops it. It also stops when you leave the
shell, even if the shell is killed outright. See `man mcp`.

- **One tool per module that has a `.skill` file** (a module without one is a shell command only), with typed parameters from it, e.g. `{"pattern": "error", "ignore_case": true, "stdin": "..."}`.
- **`send_tui_alert`** shows a short alert in the running shell's status panel (`message`, `severity`: info / warn / crit, `timeout_seconds`, optional `content`: a payload you copy with a right-click and paste with Alt-v), so a model can flag a finished task or a risk where you will see it, and hand you a path or a command along with it.
- **The review loop:** `get_flagged_items`, `request_human_review`, `get_review_status` and `resolve_review_item`, plus the `shellcraft://review/queue` resource. See **Reviewing with an AI** below.
- **The co-pilot tools** reach your **interactive** shell through its private link, published in `~/.shellcraft/mainlink.json` (readable by you only, removed when the shell exits), from the `mcp start` server and a client's own `--mcp` server alike: `get_recent_output` (your screen), `get_jobs` / `get_job_output`, `get_history` (keys redacted), `get_clip` / `set_clip` (what Alt-v pastes: the AI stages a command for you), `open_subshell` (a lite ShellCraft named `✦ AI · finding` in the footer that prints the AI's brief first, with an alert pointing to it), `run_in_shell` (read-only commands run at once with their trace above your prompt; the rest waits for `review approve`, or runs at once after `settings ai_run on`) and `request_session_grant` (the AI asks for that grant once). The `ai/` folder has agents and skills that teach an AI to use them: [ai/README.md](../ai/README.md).
- **`shellcraft_pipeline`** runs a whole command line, such as `{"command": "cat notes.txt | grep -i todo | sort"}`, using the read-only builtins. It **cannot** redirect to files, change files, read environment variables, change the shell's state or settings, or run OS programs (unless you start the server with `--allow-system`).
- **Man pages as resources** at `shellcraft://man/<name>`.
- **Hot reload:** editing, adding or removing a module updates connected clients' tool lists.
- **Limits:** a call that runs longer than `SHELLCRAFT_MCP_TIMEOUT` seconds (default 120) is abandoned with an error, and stdin and output are capped (10 MB in, 2 MB out).
- **Local-only modules:** `expose = false` in a module's `.skill` keeps it out of the tool list and out of `shellcraft_pipeline`; `--expose NAME,...` offers only the modules named.

**Claude Code**

```bash
claude mcp add shellcraft -- /path/to/shellcraft/.venv/bin/python /path/to/shellcraft/main.py --mcp
```

**Claude Desktop** (or any client with an `mcpServers` config)

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

Use absolute paths. On Windows, the Python path is `C:\\path\\to\\shellcraft\\.venv\\Scripts\\python.exe`.
Over HTTP: `claude mcp add --transport http shellcraft http://127.0.0.1:38765/mcp --header "Authorization: Bearer TOKEN"`.
To poke at the server by hand: `npx @modelcontextprotocol/inspector .venv/bin/python main.py --mcp`.

> [!WARNING]
> Connected AI clients can read any file your user account can read (through `curl`, `cat`,
> `grep`, …), and `curl` can request any URL (and send a file's contents with `-d @FILE`); while your interactive shell runs they can also read
> its screen, its subshells' output and your command history, and run the commands you approve
> (or any command after `settings ai_run on`). Modules that use your API keys (`queryDns`,
> `queryCensys`) spend your quota when an AI calls them. Only connect AI clients you trust.

---

## Reviewing with an AI

A two-way queue between you and an AI client, shared through files in `~/.shellcraft/review/`
(readable by you only), so it works with the `mcp start` server and with every client's own
`--mcp` server alike.

- **You talk to the AI** with `tell`: `tell look at the last 50 lines of auth.log` (or pipe text
  into it). It is a flag of type `message`, read with `get_flagged_items`; the `monitor` agent in
  `ai/` watches for them and reports them to the orchestrating AI.
- **You flag things for the AI** with `mark`: what you pipe in, files, or a diff.
  ```
  pytest | mark --type test-failure -p high
  mark --file app.log --file config.toml -m "startup crash"
  mark --diff old.py new.py
  git diff | mark --type diff
  ```
  The AI reads them with `get_flagged_items` (or the `shellcraft://review/queue` resource) and
  closes them with `resolve_review_item`, saying what it did. Each piece keeps its last 256 KB.
  What you mark is stored as is, so mark only what an AI may read.
- **The AI asks you before destructive actions** with `request_human_review` (a title, a
  description, the commands or diffs involved, a priority). It shows in the footer as an alert,
  `info`, `warn` or `crit` for a low, medium or high priority, until you answer:
  ```
  review                       # what is pending
  review show r-…a1b2c3        # one item in full (the last characters of the id are enough)
  review approve a1b2c3 go ahead
  review reject a1b2c3 not on Fridays
  ```
  The AI either waits for your answer (`wait_seconds`, up to a little under the call timeout)
  or asks again with `get_review_status`, and goes ahead only on `approved`. This is advisory:
  ShellCraft's server instructions tell the AI to ask, but it can't stop what the AI does with
  its own tools.
- **The AI runs commands in your shell** with `run_in_shell`, in three tiers. A **read-only**
  command (grep, cat, jq, sort, the query modules; no redirection, no file changes, no `&`) runs
  at once as a background job named `✦ AI: command`, and its trace shows above your prompt like
  any `cmd &`. With the **session grant** everything runs at once: `settings ai_run on` (never
  saved; `✦ run` shows in the prompt while it is on), or the AI asks once with
  `request_session_grant` and you answer `review approve`. Anything else waits in the queue as
  `AI wants to run: command`:
  ```
  review show a1b2c3           # the command and the AI's reason
  review approve a1b2c3        # runs it here, now, with your permissions; the AI gets the output
  review reject a1b2c3 no      # it never runs
  ```

Connected clients are told when the queue changes (a resource-updated notification for
`shellcraft://review/queue`). Items that are done are deleted after 7 days; pending ones stay
until answered.
