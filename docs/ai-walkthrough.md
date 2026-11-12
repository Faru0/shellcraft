# AI co-pilot: setup walkthrough and daily use

← [Manual](README.md)

This page takes you from nothing to a working AI co-pilot, then shows how you use it day to day. It covers
both halves: the **co-pilot** that sits next to you in your shell, and the **host analysis** team that studies
your servers. Each half stands alone, so do only the one you want. The reference pages are
[MCP and the AI co-pilot](mcp-and-ai.md) and [Host analysis with an AI](ai-host-analysis.md).

## How the pieces fit

ShellCraft does not think. It offers tools, and an AI client (Claude Code) calls them. So you run two things:
ShellCraft (the tools) and Claude Code (the thinking). The agents in `ai/` are instructions that tell Claude
Code how to use those tools well.

> [!WARNING]
> A connected AI acts as you. By default it can run any command in your shell at once. Read
> [Safety](#safety-in-one-minute) before you connect it.

## The short way in: `ask`

If all you want is to ask a question now and then, you need no setup beyond having Claude Code installed.
Type it at the ShellCraft prompt:

```
ls /nope
ask why did that fail
ask fix it                # the command lands in the clip register: Alt-v pastes it
```

`ask` starts the client for one question, connects it to the shell you are in and writes the answer under
your prompt. It follows `ai on` and `ai off` like every AI client. [Ask the AI at your prompt](ask.md) has
the details. The rest of this page is for a co-pilot that stays next to you and works while you do.

## Part 1: the co-pilot in your shell

### 1. Check what you need

- ShellCraft installed and starting (`shellcraft`).
- Claude Code installed (`claude --version`).

### 2. Connect Claude Code to ShellCraft

From a normal terminal, in the folder you want to work in:

```bash
claude mcp add shellcraft -- /path/to/shellcraft/.venv/bin/python /path/to/shellcraft/main.py --mcp
```

Or from inside ShellCraft, type `mcp start` and paste the `claude mcp add --transport http ...` line it prints
(`mcp show` prints it again).

### 3. Install the agents and skills

```bash
shellcraft --install-ai --user          # every project, or --project for this folder only
shellcraft --check-ai --user            # confirm they are installed and current
```

This puts the agents (`shellcraft-copilot`, `orchestrator`, `worker`, `monitor`, ...) and their skills where
Claude Code reads them. It never overwrites a file you edited. Add `--dry-run` to preview.

### 4. Start both sides

1. Start `shellcraft` in one terminal. Leave it open. This is the shell the AI will see.
2. In another terminal run `claude`. Ask: *"Use the shellcraft-copilot agent and tell me what is on my screen."*

If it answers with what is on your screen, you are connected. If it says the tools are missing, run
`/mcp` inside Claude Code and check that `shellcraft` shows as connected.

### 5. Use it

Things you can say to Claude Code:

| You say | What happens |
| --- | --- |
| "What do you make of that output?" | It reads your screen and explains. |
| "Keep an eye on this and tell me if it fails." | It watches events and sends a footer alert (`info`, `warn`, `crit`). |
| "Open a subshell and look into this IP." | A `✦ AI · name` subshell opens with its brief and findings. |
| "Give me the command to do that." | It stages the command in your clip register. Press Alt-v to paste it. Nothing runs. |
| "Run `ss -tulpn` and summarize." | It runs the command in your shell and shows the trace. |

Things you can do from your side:

- `ask QUESTION` gets you a one-off answer at the prompt, without switching to the other terminal.
- `mark` flags something for the AI to look at. `tell` sends it a message. Replies arrive in the same thread.
- `watch` makes your own triggers flag events for it.
- `review` shows what it is asking you (approvals, questions). `review approve` and `review answer` reply.
- `tasks` shows the shared task board. `findings` shows the case file the agents build.

### 6. Several agents at once

For a bigger job, say *"Use the orchestrator agent to investigate X."* It splits the work into tasks on the
board, starts workers, merges their findings into the case file and asks you when it needs a decision. Watch
it with `tasks` and `findings`.

## Part 2: analyzing your hosts

Two ways in, use either or both:

- **Live ssh, per device.** The AI runs named, read-only checks on a host over ssh.
- **The aggregator.** Your EDR writes files per device (process list, netstat, lsof, ...) to one box. The AI
  reads the newest files there and compares them with the run before.

### 1. One command sets it up

```bash
# live ssh hosts (names from ~/.ssh/config)
shellcraft --setup-analyst web01 db01 --project

# add --sudo to use the root-only checks on hosts where `sudo -n` already works
shellcraft --setup-analyst web01 --sudo --project

# an aggregator box: the ssh host plus the folder that holds one folder per device
shellcraft --setup-analyst --aggregator edr-box --root /srv/edr --project
```

It tests ssh, enables the hosts for the AI (`ai = true` in `~/.shellcraft/ssh-hosts.toml`), writes
`./.mcp.json` with the analyst server, installs the agents and writes a starter watch plan. It only adds. Use
`--dry-run` first if you like.

**If your EDR's file names are different** (or change later), tell it:

```bash
shellcraft --setup-analyst --aggregator edr-box --root /srv/edr --project \
  --file ps='proc_*.log' --file netstat='net_*.txt' \
  --device web01=192.168.1.2 --stale-minutes 60
```

- `--file KIND=GLOB` says what a kind's files are called. Repeat it per kind.
- `--device NAME=FOLDER` gives a device folder a friendly name.
- `--stale-minutes N` is how old the newest file may be before it is reported stale.

To change one that is already set up, edit `~/.shellcraft/aggregators.toml`. For a single call, the AI can
pass `--glob ps='proc_*.log'` to the `aggregator` tool. The root folder is not a call option on purpose.

### 2. Check it

```bash
shellcraft --check-analyst
```

It exits 0 when everything is fine, and otherwise prints one line per problem with the fix. The usual ones:
the MCP server must be named exactly `shellcraft`, started with `--role analyst`, and the host must be in your
ssh config and have `ai = true`.

### 3. Ask

Start Claude Code **with the orchestrator as the main agent** (a sub agent cannot start sub agents):

```bash
claude --agent orchestrator
```

Then, in plain words:

- "Use the host-analyst agent on web01." (live ssh)
- "Check out device web01." (the aggregator, the live host, or both; it says which)
- "Run a round of my watch plan." (everything due in `~/.shellcraft/watchplans/`)
- "Is anything wrong with memory or disk on web01?" (the `health` rules)
- "What changed on web01 since last time?" (`baseline diff`, shows only new and gone items)

For an unattended run:

```bash
claude -p "Run a watch round" --agent orchestrator --mcp-config .mcp.json --strict-mcp-config \
  --allowedTools "mcp__shellcraft" "Agent" --max-budget-usd 1
```

### 4. Read the results

- `findings` in ShellCraft is the case file: one finding per host, IP or domain, with evidence, severity and
  confidence. Real findings are kept; an unchanged round is silent.
- The first run of `baseline` learns what is normal, so the first answer says "learned". From the second run,
  only new or gone items are reported.
- A **stale or missing** aggregator file is a finding on its own: the EDR on that device may have stopped.
- Alerts reach your footer only when you have an interactive ShellCraft running.

### 5. Keep it going

Put what you want checked every so often in a watch plan (`~/.shellcraft/watchplans/NAME.toml`): hosts and
their checks, feeds, and `[[devices]]` for aggregator devices. The orchestrator asks which units are due and
records each as done, so you can ask for a round when you like, or let a timer run them:

```
shellcraft --watch-rounds NAME      # one round now, if one is due, with no Claude Code session open
shellcraft --install-timer NAME     # a round every interval_minutes of the plan, on its own
shellcraft --remove-timer NAME
```

A round on a timer sends its alerts to your ShellCraft. With no shell open they wait, and the next shell shows
them as missed. Each round has a budget (`--budget`, default 2 USD) and all rounds of a day have one
(`--day-budget`, default 10 USD). [Rounds on a timer](ai-host-analysis.md#rounds-on-a-timer) has the details.

## Safety in one minute

- **Opt in per host.** A host needs both an ssh config entry and `ai = true`. Without it the AI cannot reach it.
- **Read-only, named checks.** Over ssh the AI picks a check by name from a fixed list. It cannot type its own
  command. `sudo -n` is used only on hosts with `sudo = true`.
- **Data is not instructions.** Whatever comes back from a host or a file is quoted as untrusted. A log line
  saying "ignore your instructions" is recorded as evidence, not obeyed.
- **Secrets are redacted** and outputs are capped.
- **The aggregator is fenced.** A device must be a real folder directly under the root, and a symlink out of
  the root is refused.
- **Your shell is a different matter.** For the co-pilot in Part 1, `ai off` makes it ask before anything that
  is not read-only. `ai-policy.toml` can deny or ask per command. `ai-audit.jsonl` in `~/.shellcraft` records
  what it called.

## When something is off

| You see | Try |
| --- | --- |
| The AI says it has no shellcraft tools | `/mcp` in Claude Code; the server must be named `shellcraft` |
| "not enabled for AI use" | Add `[hosts.NAME]` with `ai = true` to `~/.shellcraft/ssh-hosts.toml` (or rerun setup) |
| "not a Host in" your ssh config | Add the host to `~/.ssh/config` with key login |
| "no file for ps" on a device | The EDR renamed its files: rerun setup with `--file`, or pass `--glob` for the call |
| Everything says STALE | Raise `--stale-minutes`, or check the EDR is still writing |
| A round reports nothing | That is correct when nothing changed |
| The timer runs no rounds | `tail ~/.shellcraft/rounds.log` and `shellcraft --check-analyst`: the day's budget may be used up, or the client is not logged in |
| Anything else | `shellcraft --check-analyst`, then the troubleshooting tables in the two reference pages |
