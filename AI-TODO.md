# AI co-pilot ideas

Ideas for `ai/` (agents and skills) and for what an AI can do through ShellCraft. Each one has a
checkbox, a "Done when" line and a Notes line. Tick it when it's built and the suite is green. The
design question under each idea is the one to answer before building it.

## Built

- [x] **Host analysis, second source and setup** (`aggregator`, `health`, eight integrity and persistence checks,
  `shellcraft --setup-analyst` / `--check-analyst`, `[[devices]]` in watch plans, the `edr-analyst` agent and
  the `aggregator-triage` skill): the AI reads, from your aggregator box, the files an EDR keeps per device, or
  goes to the device live, or both; a stale file is a finding; system health is checked too
  - Follow-ups: `hostscan` rules for the integrity checks (a changed system program, a new setuid file, a
    preload library), the report file, a cap on concurrent ssh sessions and `ControlMaster`, and `hostscan join`
    for the netstat and lsof forms
- [x] **Host analysis framework** (`sshrun`, `hostscan`, `baseline`, `feed`, `watchplan`, the `run_func` tool, the
  `analyst` role, four analyst agents and five skills): an AI studies remote hosts over ssh with named
  read-only checks, reads logs with the right reader, audits eBPF, kprobes and uprobes, remembers what it has
  seen, pulls SIEM and Uptime Kuma data, and loops in rounds from a plan. See `docs/ai-host-analysis.md`
  - Notes: the loop is the Claude Code session (orchestrator plus sub agents); there is no daemon. Remote
    research items 1, 2, 3, 4 and 6 below were built this way (item 5 partly: the alert ladder and enrichment
    are in the skills, the report file under `~/.shellcraft/reports` is not built).

- [x] **Easy install** (`shellcraft --install-ai`, `--check-ai`, `--uninstall-ai`): the agents and
  skills into `~/.claude` or `./.claude`, a subset by name, never over a file you edited
  - Notes: the `ai/` files ship in the package as `shellcraft.bundled_ai`. The installer keeps the
    hash of what it wrote in `~/.shellcraft/ai-installed.json`; that is how it tells "ours, safe to
    update" from "edited" or "someone else's". It does not register the MCP server.

- [x] **Scripting skill** (`ai/skills/shellcraft-scripting/SKILL.md`): write, check (`source -n`,
  `func check`) and run ShellCraft scripts, with the mistakes an AI makes most (text vs numbers,
  `elif`, top-level `x = 5`, arrays)
  - Notes: every rule in it was run against the shell first. `tests/test_ai_files.py` checks the
    frontmatter of every agent and skill, that each `mcp__shellcraft__NAME` in an agent's `tools:`
    is a real tool, and that `ai/README.md` lists every file. It found `mcp__shellcraft__fetch` in
    `soc-triage.md`, a tool that doesn't exist; removed.

## Held back

Ideas the user chose not to build yet.

- [ ] **Module-builder agent and skill**: turn "I need a command that does X" into a module
  (`.py`, `.md`, `.skill`) in `~/.shellcraft/modules/`, then loop on `ingest check` until it has no
  failures; also convert an existing script with the `ingest` wizard answered from a script
  - Design question: a `.skill` makes the module callable by every connected AI client. Should the
    agent default to `expose = false` for anything that writes, sends or spends, and ask first?
- [ ] **Session-summary agent**: at the end of a session, read the history, events, flagged items,
  case file and task board, and write what was done, what is open and what to pick up next
  - Design question: where the summary lives (a file under `~/.shellcraft`, a func, an alert).
- [ ] **Debugger agent for failed commands**: read the last failed command, its error and status
  (`get_events`, `get_recent_output`), check `docs/troubleshooting.md`, stage a fix in the clip
  - Design question: does it ever run the fix, or only stage it (Alt-v)?
- [ ] **Docs-helper skill**: answer "how do I…?" from the manual in `docs/` and `man NAME`, quoting
  the page and the exact error messages
  - Design question: how the AI finds the docs when it runs on another machine than the shell.
- [ ] **Risky-command reviewer**: before a destructive command, read it, check `ai-policy.toml`,
  explain what it touches in plain words and call `request_human_review` with that summary
  - Design question: does it also help write the policy file?

## Remote research (an AI that works on other machines)

The picture: you say "SSH into that server, look at the processes and `ss`/`netstat`, tell me if
anything is odd, and report back through ShellCraft." The AI reads, analyses and reports; it
changes nothing on the remote host. These are proposals, nothing here is built.

Facts that shape all of them (from the code):

- ShellCraft runs OS programs like `ssh`, `ps` and `ss` only when OS commands are on
  (`settings system_commands on`, or `--allow-system`). Checked in a real shell: a background
  command job, the kind `run_in_shell` starts, answered `command not found: ssh` with them off.
  `shellcraft_pipeline` (the read-only sandbox) never runs them. So remote research needs that
  setting on, and the policy or `ai_access` to allow the command.
- A command an AI runs is a background job, so ssh must be key-based (`BatchMode=yes`): nobody is
  there to type a password.
- `run_in_shell` returns at most 256 KB of output, the end kept.
- `ai-policy.toml` can deny or ask per command, so a rule like "ssh only to these hosts" is
  possible today, but it can't see what the remote command does.

- [x] **1. Remote host triage agent and skill** (`host-triage`): given a host name, it runs a fixed
  read-only checklist over ssh (processes, listening ports and connections, logged-in users and
  recent logins, cron and systemd timers, `authorized_keys`, recently changed files), filters it
  with ShellCraft's own tools, records one finding per odd thing in the case file, alerts you with
  `send_tui_alert` and opens a `✦ AI · host` subshell with the evidence
  - Done when: one request ("check web01") gives findings, an alert and a subshell, and no command
    on the remote host wrote anything
  - Design question: the checklist as a list in the skill, or one `.sc` script the agent runs?
- [x] **2. A read-only remote command module** (`sshrun`, with a `.skill`): a typed MCP tool that
  runs one command from a fixed allowlist (`ps`, `ss`, `netstat`, `lsof -i`, `who`, `last`,
  `crontab -l`, `systemctl list-units`…) on a host named in `~/.ssh/config`, with `BatchMode=yes`,
  a timeout, no terminal and an output cap; every call is in the AI audit log
  - Done when: the AI can't pass anything but an allowed command, and policy rules can allow `sshrun`
    while still asking about a raw `ssh`
  - Design question: allowlist in the module, or in `ai-policy.toml` so you can edit it?
- [x] **3. Process and connection analyser modules** (`psnet` or similar): read `ps` and `ss` output
  on stdin and join each connection to its process, user, binary path and parent, then flag the
  same odd things every time: a binary that was deleted, running from `/tmp` or `/dev/shm`, a
  shell started by a web server, listening on `0.0.0.0` on an odd port, many connections to one
  address, an outbound connection to a rare port. Pure text in and out, so it can be tested
  offline and used locally too
  - Done when: a captured `ps`/`ss` sample in the tests gives the expected flags and no false ones
    on a clean sample
  - Design question: flag rules built in, or a rules file you can change?
- [x] **4. Baselines and "what changed"**: save a snapshot per host (process names, listening ports,
  established peers, cron entries, `authorized_keys` hashes) under `~/.shellcraft`, and compare the
  next run. The AI reports only what is new or gone, and a func can run it every night
  - Done when: a new listening port or a new cron entry between two snapshots raises an alert
  - Design question: what counts as normal (a first run with no baseline: all `info`)?
- [x] **5. Enrichment and an escalation ladder**: look up the remote IPs and certificates with
  `ip2geo`, `queryCert` and `queryCensys`, and turn the evidence into one alert level: `info` when
  all is normal, `warn` for something to look at, `crit` only for a likely compromise. Don't alert
  the same finding twice; a report file (`~/.shellcraft/reports/HOST-DATE.md`) holds the details
  and its path rides along in the alert's data
  - Done when: the same odd connection seen on two runs alerts once, and Alt-v pastes the report path
  - Progress: the lookups and the ladder are in. `enrich` gives one verdict per address from up to six
    services, and the three triage skills turn the verdict plus what the host shows into `info`, `warn` or
    `crit`, with one alert per finding (`docs/enrichment.md`). The report file is in too: an analyst ends each
    unit with `report save`, a round that found something writes `~/.shellcraft/reports/host/NAME/TIME-STATUS.md`,
    and its path is the `content` of the alert (`docs/fleet.md`)
  - Design question: how much a `crit` is allowed to interrupt you, and quiet hours.
- [x] **6. A fleet sweep**: the orchestrator makes one task per host (from `~/.ssh/config` or an
  inventory file), workers run the host triage in parallel on the task board, and the case file joins
  what they find across hosts (the same remote IP on three servers is one finding), ending in one
  summary alert
  - Done when: three hosts give three tasks, one merged finding for a shared address and one summary
  - Design question: a limit on how many ssh sessions at once, and shared connections
    (`ControlMaster`) so a sweep isn't a burst of logins.
