# ShellCraft as a co-pilot: agents and skills for your AI

ShellCraft's MCP server lets an AI work next to you in your terminal: read what is on your
screen, open a subshell with its findings, stage a command for you to paste, run commands (read-only
ones at once, the rest with your say-so), and ask you to approve risky actions. This folder holds
ready-made **agents** and **skills** that teach an AI client how to use that well. They are plain
Markdown: copy them into your client, or point it at them.

## What the server offers

| Tool | What it does |
| --- | --- |
| every module with a `.skill` | A typed tool (`queryDns`, `ip2geo`, `fetch`, `filter`…) |
| `shellcraft_pipeline` | A whole command line in the sandbox (read-only builtins and modules, no files written) |
| `send_tui_alert` | A one-line alert in your footer: `info`, `warn` or `crit`, with optional data you paste with Alt-v |
| `get_recent_output` | The last lines of your screen (your commands as `❯ cmd`, and their output) |
| `get_jobs`, `get_job_output` | Your subshells and what each printed |
| `get_history` | Your last commands (API keys redacted) |
| `get_clip`, `set_clip` | Read or stage the clip register (`set_clip` + Alt-v hands you a command) |
| `open_subshell` | A named ShellCraft subshell (`✦ AI · name` in the footer) that prints the AI's brief at start |
| `run_in_shell` | Run a command in your shell with its trace shown: read-only ones at once, the rest after `review approve` |
| `request_session_grant` | Ask once to run anything for the session (`settings ai_run on`; `✦ run` in the prompt while on) |
| `get_flagged_items`, `resolve_review_item` | What you flagged with `mark` and the messages you sent with `tell` |
| `request_human_review`, `get_review_status` | Approval before something destructive |

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
| `agents/monitor.md` | Watches for your `mark`s and `tell`s and reports them to the orchestrator |
| `skills/shellcraft-copilot/SKILL.md` | The etiquette: which channel for what, alert levels, what never to do |
| `skills/soc-triage/SKILL.md` | The triage steps with concrete pipelines and the finding format |
| `skills/review-loop/SKILL.md` | `mark` / `review`, approvals, `run_in_shell` results |

## A first session

1. In ShellCraft: `mcp start`, then `mark --file /var/log/auth.log -m "last night's logins" -p medium`.
2. In Claude Code (with the server added): "Use the shellcraft-copilot agent. Look at what I flagged."
3. The AI reads the flag, triages, and opens `✦ AI · auth.log logins` in your footer with its findings;
   `Ctrl-] 1` (or a click) shows it, `exit` returns.
4. It stages `grep 203.0.113.9 /var/log/auth.log` with `set_clip`; Alt-v pastes it.
5. If it needs to run things itself: `request_session_grant` → `review approve ID` once, or let it
   run read-only commands as it goes and answer `review approve` for the rest.

> **Trust.** A connected client can read your screen, your subshells' output and your command
> history, and run commands you approve. Connect only clients you trust, and keep secrets off the
> command line (`settings NAME` reads a key without echo and the history redacts it).
