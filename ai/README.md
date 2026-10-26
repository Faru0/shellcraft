# ShellCraft as a co-pilot: agents and skills for your AI

← [The manual](../docs/README.md) · [MCP and the AI co-pilot](../docs/mcp-and-ai.md)

ShellCraft's MCP server lets an AI work next to you in your terminal: read what is on your
screen, hear what happens in your shell, open a subshell with its findings, stage a command for you
to paste, run commands (at once by default; with `ai off`, read-only ones at once and the rest with
your say-so), ask you questions, and share work between several agents. This folder holds
ready-made **agents** and **skills** that teach an AI client how to use that well. They are plain
Markdown: copy them into your client, or point it at them.

## What the server offers

| Tool | What it does |
| --- | --- |
| every module with a `.skill` | A typed tool (`queryDns`, `ip2geo`, `queryCert`…) |
| `shellcraft_pipeline` | A whole command line in the sandbox (read-only builtins and modules, no files written) |
| `send_tui_alert` | A one-line alert in your footer: `info`, `warn` or `crit`, with optional data you paste with Alt-v |
| `get_recent_output` | The last lines of your screen (your commands as `❯ cmd`, and their output) |
| `get_jobs`, `get_job_output` | Your subshells and what each printed |
| `get_history` | Your last commands (API keys redacted) |
| `get_events`, `search_output` | What happened in the shell (commands with exit status, jobs, Ctrl-C), waiting for the next event if asked; a regex search of the screen. `watch` makes the user's triggers flag events |
| `get_clip`, `set_clip` | Read or stage the clip register (`set_clip` + Alt-v hands you a command) |
| `open_subshell` | A named ShellCraft subshell (`✦ AI · name` in the footer) that prints the AI's brief at start |
| `run_in_shell` | Run a command in your shell with its trace shown. At once by default (`settings ai_access`); with it off, read-only ones at once and the rest after `review approve`. `ai-policy.toml` can deny or ask per command |
| `request_session_grant` | Ask once to run anything for the session. Not needed while `settings ai_access` is on, which is the default |
| `create_task`, `list_tasks`, `claim_task`, `update_task`, `release_task` | The task board several agents share: exclusive claims with a lease, dependencies, notes and results. The user sees it with `tasks` |
| `add_finding`, `list_findings`, `get_finding`, `update_finding` | The case file the agents share: one finding per IP, domain or host, evidence merged, severity and confidence kept. The user reads it with `findings` |
| `get_flagged_items`, `resolve_review_item` | What you flagged with `mark` and the messages you sent with `tell` |
| `request_human_review`, `get_review_status` | Approval before something destructive |
| `ask_user` | A question for the user, with choices if you have them. They answer with `review answer`; the call can wait for it. Replies on any item (`tell -r ID`) arrive in its thread |

The read tools and `open_subshell` / `run_in_shell` reach your **interactive** ShellCraft through its
private link, published in `~/.shellcraft/mainlink.json` (readable by you only, removed when the
shell exits). Start `shellcraft` first; without it those tools say so.

## Connect a client

Claude Code, a server per client (stdio):

```bash
claude mcp add shellcraft -- /path/to/shellcraft/.venv/bin/python /path/to/shellcraft/main.py --mcp
```

Or the server the shell runs for you: type `mcp start` in ShellCraft, then paste the
`claude mcp add --transport http …` line it prints (`mcp show` prints it again).

## Install the agents and skills

Claude Code reads `.claude/agents/*.md` and `.claude/skills/*/SKILL.md` in a project, or
`~/.claude/agents` and `~/.claude/skills` for every project:

```bash
mkdir -p ~/.claude/agents ~/.claude/skills
cp ai/agents/*.md ~/.claude/agents/
cp -r ai/skills/* ~/.claude/skills/
```

Other clients: paste a skill's text into the system prompt or the project instructions. The
skills name the real tools, so they work as written.

| File | Use it for |
| --- | --- |
| `agents/shellcraft-copilot.md` | A co-pilot that watches your session and speaks up when something matters |
| `agents/soc-triage.md` | A triage routine over logs and indicators, SOC style |
| `agents/monitor.md` | Watches for your `mark`s, `tell`s and `watch` triggers, and reports them to the orchestrator without polling. Works with `--role monitor` |
| `agents/orchestrator.md` | Splits a job into tasks on the board, starts workers, merges the case file, asks you when it needs a decision |
| `agents/worker.md` | Claims one task, does it, records findings, reports on the task |
| `skills/shellcraft-copilot/SKILL.md` | The etiquette: which channel for what, alert levels, what never to do |
| `skills/soc-triage/SKILL.md` | The triage steps with concrete pipelines and the finding format |
| `skills/review-loop/SKILL.md` | `mark` / `review`, approvals, `run_in_shell` results, `ask_user` and threads |
| `skills/task-board/SKILL.md` | The shared task board: creating, claiming with a lease, reporting, blocking |
| `skills/case-file/SKILL.md` | The shared case file: one finding per thing, evidence, severity and confidence |

## A first session

1. In ShellCraft: `mcp start`, then `mark --file /var/log/auth.log -m "last night's logins" -p medium`.
2. In Claude Code (with the server added): "Use the shellcraft-copilot agent. Look at what I flagged."
3. The AI reads the flag, triages, and opens `✦ AI · auth.log logins` in your footer with its findings;
   `Ctrl-] 1` (or a click) shows it, `exit` returns.
4. It stages `grep 203.0.113.9 /var/log/auth.log` with `set_clip`; Alt-v pastes it.
5. If it needs to run things itself, it does, and you see each command as a `✦ AI:` job above your
   prompt. Prefer to approve them? `ai off`: then it runs read-only commands as it goes and asks
   for the rest with `review approve`.

## A team session

For a job with parts, run an orchestrator and let it start workers:

1. Add a read-only server for the monitor, so the one that only watches can't do anything else:

   ```bash
   claude mcp add shellcraft-monitor -- /path/to/shellcraft/.venv/bin/python /path/to/shellcraft/main.py --mcp --role monitor
   ```

2. In ShellCraft, tell it what to wake the AI for: `watch on-fail`, and `tell triage the logs in /var/log`.
3. In Claude Code: "Use the orchestrator agent." It reads your message, puts tasks on the board
   (`tasks` shows them, and the prompt counts `✦ N tasks`), starts `worker` agents that claim one each,
   and merges what they find into one case (`findings`). It asks you with `ask_user` when it needs
   a decision (`review answer ID 1`), and you get the report with `findings export report.md`.
4. `ai log` lists every call each agent made, by name.

> **Trust.** A connected client can read your screen, your subshells' output and your command
> history, and, while `settings ai_access` is on (the default), run any command with your
> permissions, at once. Connect only clients you trust, keep secrets off the command line
> (`settings NAME` reads a key without echo and the history redacts it), and use `ai off`, deny
> rules in `ai-policy.toml` and `--role` servers to hold an AI to less.
