# MCP and the AI co-pilot

← [Manual](README.md)

This page is for you if you use an AI assistant such as Claude Code or Claude Desktop and want it
to work in your shell with you. After reading it you'll be able to connect one, know exactly what
it can see and do, and set the limits you're comfortable with. The [ai/](../ai/README.md) folder
has agents and skills that teach an AI client to work this way.

## What MCP is

An AI assistant on its own can only talk. To do anything, like run a command or read a file, it
needs **tools**, and something on your machine has to offer them. MCP, the Model Context Protocol,
is the common language for that. A program that offers tools is an **MCP server**. The AI app that
uses them is an **MCP client**.

ShellCraft can be an MCP server. Once you connect a client to it, the AI sees a list of tools:
each of your modules, a way to run ShellCraft command lines, ways to read your screen and your
jobs, and ways to ask you things. It decides when to call them, and ShellCraft runs the call and
sends back the result.

You don't need to know the protocol to use it. You need to know three things, which the rest of
this page covers: how to connect a client, what each tool lets the AI do, and how you stay in
control of it.

## Read this first

> [!WARNING]
> A connected AI client can read any file your user account can read, through commands like
> `cat`, `grep` and `curl`, and `curl` can send a file's contents anywhere. While your shell runs,
> it can also read the screen, your subshells' output and your command history, and **run any
> command, at once, with your permissions**: `settings ai_access` is on by default. Turn it off
> with `ai off` to approve each non-read-only command yourself, or add deny and ask rules in
> `ai-policy.toml`. Modules that use your API keys spend your quota when the AI calls them.
> Only connect AI clients you trust.

In plain words: the AI acts as you. If you wouldn't type a command yourself, don't leave the AI
free to type it either. [Commands the AI runs](#commands-the-ai-runs) shows how to tighten this.

## Connect a client

There are two ways to run the server. Pick the first unless you have a reason not to.

### Let the client start it

The simplest setup is to tell the AI client how to start ShellCraft's server. It then starts one
whenever it needs it and stops it when it's done. This server talks to the client over its own
input and output, so there's no network port involved.

For **Claude Code**, run this once in a normal terminal:

```bash
claude mcp add shellcraft -- /path/to/shellcraft/.venv/bin/python /path/to/shellcraft/main.py --mcp
```

For **Claude Desktop**, or any client with an `mcpServers` setting, add this to its configuration:

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

Replace `/path/to/shellcraft` with the folder you installed ShellCraft in, and use full paths. The
client starts the server from its own folder, not yours, so a relative path won't be found. On
Windows, the Python path is `C:\\path\\to\\shellcraft\\.venv\\Scripts\\python.exe`. The doubled
backslashes are needed inside JSON.

To check the server works before involving an AI, open it in the MCP inspector, which lists the
tools and lets you call them by hand:

```bash
npx @modelcontextprotocol/inspector .venv/bin/python main.py --mcp
```

### Or run the server from your shell

The second way is to run the server inside your interactive shell. Use it when you want to start
and stop the server yourself, or connect a client that only speaks HTTP. `mcp start` runs an HTTP
server in the background, so you keep working while the AI connects to it:

```
mcp start             # start it, and print the `claude mcp add` line to use
mcp                   # is it running?
mcp show              # the token and the `claude mcp add` line again
mcp log               # its log
mcp stop              # stop it
mcp token rotate      # a new token; prints the lines to update your client
```

While it runs, the prompt shows `● mcp :38765`, the port it listens on. It stops when you leave
ShellCraft. Its address and token are saved in `config.json`, so a client you add once keeps working
across sessions.

Every request must carry the token. That's because the server listens on a local port, and any
program on your machine can reach a local port. The token is what tells your AI client apart from
anything else. If you think the token leaked, `mcp token rotate` makes a new one.

From a terminal, the same server is `shellcraft --mcp-http 127.0.0.1:38765`, with `--token TOKEN`
or `--no-token`. Only use `--no-token` on a machine nobody else uses.

## What the AI can do

Here is every tool the server offers, and what it's for. You'll rarely need this table, but it
answers "could the AI have done that?".

| Tool | What it's for |
| --- | --- |
| One tool per module | Every module with a `.skill` file, with typed parameters. A module without one stays a shell command only |
| `shellcraft_pipeline` | Run a whole command line, such as `cat notes.txt \| grep -i todo \| sort`, using the read-only commands |
| `send_tui_alert` | Show an alert in your footer, optionally with data you can copy with a right-click |
| `get_recent_output` | Read your screen |
| `get_jobs`, `get_job_output` | Your subshells and background commands, and their output |
| `get_history` | Your command history, with secrets hidden |
| `get_events` | What happened in your shell: each command with its exit status and time, each job that ended, each Ctrl-C. It can wait for the next one, so the AI doesn't poll |
| `search_output` | Search your screen for a pattern; each match says which command printed it |
| `get_clip`, `set_clip` | Read the clip register, or put a command there for you to paste with Alt-v |
| `open_subshell` | Open a light ShellCraft in your footer, named `✦ AI · finding`, that shows its findings |
| `run_in_shell` | Run a command in your shell. See [Commands the AI runs](#commands-the-ai-runs) |
| `request_session_grant` | Ask you for permission to run commands for the rest of the session |
| `create_task`, `list_tasks`, `claim_task`, `update_task`, `release_task` | A task board shared by several AI agents. See [The task board](#the-task-board) |
| `add_finding`, `list_findings`, `get_finding`, `update_finding` | A case file shared by the agents. See [The case file](#the-case-file) |
| `ask_user` | Ask you a question, with choices if it has them, and wait for your answer. See [The AI asks you a question](#the-ai-asks-you-a-question) |
| `request_human_review`, `get_review_status`, `get_flagged_items`, `resolve_review_item` | Ask you before doing something risky, and read what you flagged. See [Reviewing with an AI](#reviewing-with-an-ai) |

Man pages are available to the AI too, as resources at `shellcraft://man/NAME`. A resource is
something the AI can read, the way a tool is something it can call. When you add, change or remove
a module, connected clients see the change at once.

### Two kinds of tools

It helps to split those tools in two.

Some work without your shell: the module tools and `shellcraft_pipeline`. The server runs them
itself, in a sandbox, so they work even when no ShellCraft window is open.

The rest look into your interactive shell: the screen, jobs, history, events, clip and
`run_in_shell`. Those need your shell to be running, because that's where the screen and the
jobs are. They work with both kinds of server. If your shell isn't open, the AI is told so, and
can tell you.

### Limits that always apply

Some limits protect you no matter how you set things up:

- `shellcraft_pipeline` can't write files, change settings, read environment variables or run
  your system's programs, unless you start the server with `--allow-system`. It only has the
  read-only commands. That's why it's safe to leave on.
- A tool call that takes longer than 120 seconds is stopped. The `SHELLCRAFT_MCP_TIMEOUT`
  environment variable changes the limit, in seconds.
- `expose = false` in a module's `.skill` file keeps it away from AI clients entirely, and
  `--expose NAME,...` on the server's command line offers only the modules you name. Use these for
  modules that spend money or delete things. See [the module reference](modules/reference.md#expose--false-local-only).
- History and events hide API keys you typed on a command line.

## Commands the AI runs

`run_in_shell` is the tool that matters most, because it runs a real command in your real shell,
with your permissions. This section is about how ShellCraft decides whether a command runs at once,
waits for you, or is refused.

### With access on (the default)

By default, an AI client can run any command in your shell, at once. That's the `ai_access`
setting, and it starts out on, because a co-pilot that stops for every `ls` isn't much help.

You still see everything. Each command runs as a background job named `✦ AI: command`, so its
`done` line and output show above your prompt like any `&` job. `✦ run` shows in your prompt
while access is on, as a reminder.

Turn it off at any time:

```
ai off
```

`settings ai_access off` does the same. The choice is saved, so it stays off until you turn it on
with `ai on`. A [profile](profiles.md) can set its own value, so you can keep it on for a
throwaway lab and off for work.

### With access off

With access off, `run_in_shell` works in two tiers.

1. **Read-only commands run at once.** These are commands such as `grep`, `cat`, `jq` and the query
   modules. A line counts as read-only when every command in it is a builtin that doesn't write
   files, change the shell's state or reveal secrets, or is a module. It also needs no redirection
   to a file, no `&`, no `sub`, no loop or function, no OS program, and no alias or shell function,
   since those could stand for anything.
2. **Anything else waits for you.** It shows in the review queue as `AI wants to run: command`.
   `review approve` runs it in your shell with your permissions and gives the AI the output.
   `review reject` means it never runs.

If the AI has a lot to do, it can ask for access for the rest of the session with
`request_session_grant`. You see the request in the review queue, and `review approve` grants it.

### Rules and roles

The `ai_access` setting is all or nothing. When you want something in between, such as "never
`rm -r`, always ask before `git push`, everything else is fine", write rules in
`~/.shellcraft/ai-policy.toml`. The file is optional. `ai-policy.toml.sample` in your ShellCraft
folder explains every option.

```toml
[[rule]]
action = "deny"          # never runs, and the AI is told why
match  = "rm -r*"

[[rule]]
action = "ask"           # waits for review approve, even with access on
match  = "git push*"

[role.scout]
run   = "read-only"
tools = ["get_recent_output", "get_jobs", "send_tui_alert"]
```

Each rule has an `action` and a `match`. The match is a glob on the command line: `*` means "any
text", so `rm -r*` catches `rm -r build` and `rm -rf /tmp/x`. The three actions are:

- `deny`: the command never runs, and the AI is told which rule stopped it.
- `ask`: the command waits for `review approve`, even with access on.
- `allow`: the command runs at once, even with access off.

When several rules match, `deny` wins over `ask`, and `ask` wins over `allow`. A line that holds
several commands, joined with `;`, `&&`, `||`, `|` or `&`, is denied or asked about when any one
of them matches. It's only allowed when every one of them matches an `allow` rule. A line with a
redirection or a command substitution never matches an `allow` rule unless the rule's own pattern
names it, so allowing `git status*` doesn't also allow `git status > ~/x`.

A rule can be narrowed further with `role = "worker"` (only for that role), `cwd = "/home/me/work/*"`
(only while the shell is in a matching folder) or `until = "2026-12-31 18:00"` (it stops applying
after that time).

A **role** is a set of limits for one kind of AI agent. Start a server for one role, and it offers
only what that role may use. The shell checks the role again before it runs anything, so a client
can't talk its way past it:

```bash
claude mcp add shellcraft-monitor -- /path/to/python /path/to/main.py --mcp --role monitor
```

Three roles are built in. `monitor` reads and alerts but never runs a command. `orchestrator` and
`worker` follow your rules. Your own roles set `run` to `none`, `read-only` or `policy` (follow
the rules), and `tools` to the list of tools they may use, or leave `tools` out for all of them.

Every tool also takes an optional `agent` name, such as `worker-2`. It shows in the footer
(`✦ AI worker-2: …`) and in the log, so you can tell several agents apart.

Two more sections of the file limit cost rather than commands. `[budget]` caps module and pipeline
calls per server, which stops a runaway agent from burning through an API quota. `[cache]` reuses a
module's answer for a while, as [Module answers are remembered](#module-answers-are-remembered)
explains.

### What the AI did

`ai log` lists every tool call an AI made: when, which tool, which client and agent, the command,
how it was answered (read-only, granted, approval, denied) and whether it worked. Your
`review approve` and `review reject` answers are in it too.

`ai` on its own shows the current state, the policy and the last few actions. `ai policy` and
`ai roles` show what's loaded, which is the first thing to check when a rule doesn't seem to
apply. A mistake in `ai-policy.toml` never stops the shell: the parts that parse are used, and `ai`
lists the problems under the policy line.

## Telling the AI when something happens

An AI that has to keep asking "anything new?" wastes time and money. Instead, your shell keeps a
numbered record of what happened in it: every command (the line, the exit status, how long it
took, the folder), every background job or subshell that ended, and every Ctrl-C. An AI reads it
with `get_events`, and it can ask to wait for the next one, so it hears about things as they
happen without checking on a timer. API keys you set on a command line are hidden in it, as they
are in your history.

Often you only want the AI woken for some things, such as a failed test run. That's what a trigger
is for:

```
watch on-fail                    # a command exits with an error (Ctrl-C does not count)
watch match 'denied|Traceback'   # a command's output matches
watch job-died --cmd 'make*'     # a job ends with an error
watch                            # list them
watch off w1                     # remove one
```

When a trigger fires, the event is added to the review queue as a flag, and an alert shows in
your footer. The AI's monitor sees it the same way as anything you `mark`.

Triggers set at the prompt last for the session. To keep one, add a `[[trigger]]` to
`ai-policy.toml`. An AI's own commands never fire a trigger, so it can't wake itself up in a loop,
and the same command is flagged at most every 30 seconds.

## The task board

When several AI agents work for you, one of them (the orchestrator) can split the job into tasks
and the others (workers) can take one each. The board is shared, so no two agents do the same
task, and you see all of it.

```
tasks                          # open, claimed, working and blocked tasks
tasks show a1b2c3              # one task: description, notes, result
tasks add 'check the certs' -p high --after f4e5d6
tasks done|fail|cancel a1b2c3  # finish one yourself
```

While there is work, the prompt shows `✦ 3 tasks: 2 working`. A task that is blocked, because the
agent needs something from you, or has failed raises an alert.

An agent **claims** a task, and a claim is exclusive: a second agent is told who has it. A claim
is a lease of five minutes that every progress note renews. So if a worker crashes, its task
becomes free again after a while and shows as stale, instead of being stuck forever. A task can
wait for others (`--after`), and can't be claimed until they are done.

Agents give their name with `agent` on every call, so you can tell them apart in `tasks`, in the
footer and in `ai log`. The board is also a resource, `shellcraft://tasks`, and clients are told
when it changes.

## The case file

When agents investigate something, they write what they find into one shared case. The point is
that the same IP found by two workers becomes one finding with both workers' evidence, not two
separate ones. You read it with `findings`:

```
findings                       # the current case, most severe first
findings show a1b2c3           # one finding, with its evidence
findings add ip 203.0.113.9 -s high -t 'Brute force' -n 'from auth.log'
findings set a1b2c3 --status confirmed
findings case acme             # switch to another case (made if new)
findings export report.md      # a report grouped by severity, with the evidence
```

Adding a finding for something already in the case merges the two: the evidence of both, the
higher severity and confidence, and a count of how many times it was seen. A new high or critical
finding raises an alert in your footer.

Each finding has a kind (an IP, a domain, a hash, a user, a host, a file…), a severity from info to
critical, a confidence, a status (open, confirmed, dismissed), tags, and evidence: the command and
the lines that show it.

### Module answers are remembered

Two agents asking `queryDns example.com` a minute apart spend one API call, not two. The answer is
kept for five minutes. `[cache] seconds` in `ai-policy.toml` changes that, `0` turns it off, and
`exclude` names modules that are never kept, such as one that tells the time. An agent passes
`fresh: true` to ask again. Answers that failed are never kept, and editing a module forgets what
it said.

## Reviewing with an AI

You and the AI share a queue of messages, kept in `~/.shellcraft/review/` where only you can
read them. It goes both ways: you can send things to the AI, and the AI can ask you things.

### Tell the AI something

Use `tell` when you want to point the AI at something without switching to its window:

```
tell look at the last 50 lines of auth.log
```

### Flag something for it to look at

Use `mark` to hand the AI something concrete: a failing test run, a few files, a diff.

```
pytest | mark --type test-failure -p high
mark --file app.log --file config.toml -m "startup crash"
mark --diff old.py new.py
git diff | mark --type diff
```

The AI reads these and closes each one, saying what it did. Everything you mark is stored as it
is, so mark only what an AI may read. A config file with a password in it is still a config file
with a password in it.

### The AI asks before risky actions

When the AI wants your approval, it shows in the footer as an alert until you answer:

```
review                         # what's waiting
review show a1b2c3             # one item in full (the end of its id is enough)
review approve a1b2c3 go ahead
review reject a1b2c3 not on Fridays
```

The words after the id are passed to the AI, so a reason helps it do better next time.

This kind of request is advisory. ShellCraft tells the AI to ask, but it can't stop what the AI does
with its own tools outside ShellCraft. Only `run_in_shell` and the rules above are enforced.

### The AI asks you a question

When an AI needs a decision, it can ask you instead of guessing or stopping. The question shows in
your footer as an alert, with numbered choices if it gave some:

```
AI asks: Block 203.0.113.9?  [1] block it  [2] watch it  [3] ignore
```

Answer with the item's id, then a choice number or your own words:

```
review answer a1b2c3 1             # pick a choice by its number
review answer a1b2c3 not yet, ask me after lunch    # or say it in your own words
```

The AI, which is usually waiting, gets the answer at once. To add context to any item without
answering it, reply into its thread: `tell -r a1b2c3 it is the CEO's laptop`. `review show` prints
the thread, and the AI reads it with the item.

Finished review items are deleted after 7 days. Pending ones stay until you answer them.

## If it doesn't work

- **The client doesn't list ShellCraft's tools.** Check the paths in the client's configuration are
  full paths to the virtual environment's Python and to `main.py`. Then try the MCP inspector line
  from [Let the client start it](#let-the-client-start-it): if it shows the tools, the server is
  fine and the client's configuration is the problem.
- **A module is missing from the tools.** It needs a `.skill` file, and `expose = false` hides it.
  [Troubleshooting](troubleshooting.md#an-ai-client-cant-see-my-module) has the full list.
- **The AI gets `no interactive ShellCraft is running on this machine`.** The screen, jobs, history
  and `run_in_shell` tools need your interactive ShellCraft open. Start `shellcraft` in a terminal,
  and have the AI try again.
- **A command the AI ran is waiting forever.** Access is off, or an `ask` rule matched. Run `review`
  to see it and approve or reject it.
