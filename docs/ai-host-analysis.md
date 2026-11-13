# Host analysis with an AI co-pilot

← [The manual](README.md) · [MCP and the AI co-pilot](mcp-and-ai.md) · [ai/ agents and skills](../ai/README.md)

You tell your AI "analyze web01", or "keep an eye on these servers and my monitoring", and it does the
looking: processes, sockets, memory maps, eBPF programs, kprobes and uprobes, cron, logins and logs, over
ssh. It researches the addresses it finds (`enrich`, see [Enrichment](enrichment.md)), remembers what it has
already seen, and tells you through your ShellCraft footer. It can read a lot and change nothing. This page
sets it up and explains why that is safe.

## Two ways in

There are two sources, and the AI can use both:

- **Live, per device.** The AI goes to a device over ssh and runs named read-only checks (`sshrun`). Use this for
  a fresh look, for the deep checks (memory maps, eBPF, package integrity), and to confirm something odd.
- **From your aggregator.** If you already have an EDR or a scheduled job that writes the output of commands
  (process list, `netstat`, `ss`, `lsof`, a quick eBPF check, memory and load) into one folder per device on an
  aggregator box, the AI reads those files (`aggregator`) and compares them with the previous run. It runs
  nothing on the devices. Use this for the whole network at once, for history, and for system health.

"Check out device XYZ" works either way. If XYZ is in your aggregator the `edr-analyst` agent reads its folder;
if XYZ is a live host the `host-analyst` agent connects; if it is both, the files come first and the live check
confirms what looks wrong.

## What you get

```
You  ->  Claude Code (the orchestrator agent)
           |-> host-analyst, log-reader, feed-analyst, ebpf-auditor   (live, over ssh)
           |-> edr-analyst                                             (reads the aggregator's files)
                                                       |
                  sshrun (named read-only checks)   aggregator (the EDR files)
                  hostscan (security rules)         health (memory, load, disk, OOM)
                  baseline (what is new)            feed (SIEM, Uptime Kuma)   run_func (funcs marked # ai: read)
                                                       |
                       findings (the case file)  +  alerts in your footer  +  a subshell with the evidence
```

- **`sshrun`** runs one named check on a host from your ssh config: `ps`, `ss`, `proc-maps 1203`,
  `bpf-progs`, `probes`, `log-tail /var/log/auth.log`. It cannot run anything else (`man sshrun`).
- **`aggregator`** lists your devices and reads the newest file of each kind from the aggregator box, with its
  age. A file that stopped updating is reported as a finding (`man aggregator`).
- **`hostscan`** reads that output and flags what looks wrong: a deleted binary, a shell under a web server,
  an unknown listener, an injected library, a risky eBPF program, a setuid shell, a changed system program,
  a sudoers line that gives everything away (`man hostscan`).
- **`health`** reads memory, load, disk, top and kernel messages and flags a system that is not healthy
  (`man health`).
- **`baseline`** keeps what each host or device looked like and shows only what is new or gone (`man baseline`).
- **`feed`** and **`run_func`** bring in data from your other systems (`man feed`, [Funcs](funcs.md)).
- **`watchplan`** is the list of things to keep checking, and how often (`man watchplan`).

## The short way

One command does the setup below for you and a second one checks it:

```bash
shellcraft --setup-analyst web01 db01 --sudo --project        # live hosts from your ssh config
shellcraft --setup-analyst --aggregator edr-box --root /srv/edr --project   # an aggregator box
shellcraft --check-analyst                                    # says what is wrong, if anything
```

It tests that ssh works, adds `[hosts.NAME]` with `ai = true` to `~/.shellcraft/ssh-hosts.toml`, writes
`./.mcp.json` with the analyst server (named `shellcraft`, which the agents need), installs the analyst agents
and writes a starter watch plan for each host. With `--sudo` it sets `sudo = true` only on hosts where
`sudo -n` already works. With `--aggregator` it looks at the device folders on the aggregator, guesses the kinds
of file from their names (`ps-2026-11-07T1930.txt` is kind `ps`), and writes `~/.shellcraft/aggregators.toml`
and a watch plan with one entry per device. It only adds: a host, a plan or a server you already have is left
alone, and `--dry-run` shows what would change. The steps it does are explained next.

If the guess is wrong, or the names change, tell it. `--file ps='ps_*.log'` sets what a kind's files are called
(repeat it for each kind), `--device web01=192.168.1.2` gives a folder a friendly name, and `--stale-minutes 60`
changes the freshness limit. Passing `--file` beats the guess. To change an aggregator that is already set up,
edit `aggregators.toml`, or for one call only ask for `aggregator edr web01 ps --glob ps='ps_*.log'`. The root
folder is not a call option; it comes from the config or `--root` so the AI cannot point itself somewhere else.

## Set it up

### 1. Make ssh work without a password

Every host needs an explicit `Host` entry in `~/.ssh/config`, a key, and a host key you have accepted.
`sshrun` never types a password and never accepts a new host key.

```
ssh web01 true        # must print nothing and exit 0
```

### 2. Say which hosts the AI may use

Nothing is on by default. Create `~/.shellcraft/ssh-hosts.toml`:

```toml
[hosts.web01]
ai = true
sudo = true                 # root-only checks may use `sudo -n` (step 3)
role = "web"
tags = ["prod"]
log_paths = ["/var/log/nginx/*", "/var/log/auth.log"]    # default is /var/log/*
```

Check it: `sshrun --hosts` lists your hosts and which are on. `sshrun web01 sysinfo` should answer.

### 3. Root-only checks (optional)

Other users' memory maps, `bpftool` and tracefs need root. Instead of connecting as root, let the ssh user
run exactly those commands with `sudo -n`. `sshrun --sudoers web01` prints the lines. On the host, put
them in `/etc/sudoers.d/shellcraft-analysis` with `visudo -f`, using full command paths and your ssh user:

```
# replace USER, and each command with its full path (command -v NAME)
USER ALL=(root) NOPASSWD: bpftool -j prog show
USER ALL=(root) NOPASSWD: cat /sys/kernel/tracing/kprobe_events
```

Read every line before you install it. Without this step the root checks tell you what is missing.

### 4. Start the server for analysts

An **analyst** server offers the host tools, the case file and the board, and has no way to run a command
in your shell (`run_in_shell` is not offered, and the shell would refuse it anyway).

```bash
claude mcp add shellcraft -- /path/to/shellcraft/.venv/bin/python /path/to/shellcraft/main.py --mcp --role analyst
shellcraft --install-ai --team --user      # the agents and skills
```

The server must be named `shellcraft`: the agents list their tools as `mcp__shellcraft__sshrun` and so on. Add it to a
project (`claude mcp add --scope project`) so it is the only `shellcraft` server there, and keep your ordinary,
full-access server in your other projects.

Keep your ordinary `shellcraft` server for yourself. If you also want a rule that nobody can ssh around the
analyst, add this to `~/.shellcraft/ai-policy.toml`:

```toml
[[rule]]
action = "deny"
match = "ssh *"
```

### 5. First look

Start `shellcraft` (the interactive one, so alerts reach your footer), then in Claude Code:

> Use the host-analyst agent on web01.

The first run learns what is normal for the host and says so. Later runs show only what changed.

## When you want plain ssh

`sshrun` is the safe path: named, read-only checks, and nothing else. For a job it doesn't cover, such as
editing a config file on a host, use ssh itself. There is no special setting for this. It follows the
`system_commands` setting you already have:

| `system_commands` | At your prompt | Through the ordinary `shellcraft` server |
| --- | --- | --- |
| on | `ssh web01 'sudo nginx -t'` runs | an AI can use `run_in_shell` with `ssh`, as with any OS command |
| off | `ssh` is refused, `sshrun` still works | `ssh` is refused, the `sshrun` tool still works |

Two things keep the analyst separate from this. The `analyst` role has no `run_in_shell`, so an AI in that
role never gets plain ssh, whatever the settings say. And for the ordinary server, the `deny` rule above
(`match = "ssh *"`) blocks it. Without that rule, `ai_access` decides: it is on by default, so an AI
client can run `ssh` at once. Turn it off with `settings ai_access off`, or add rules to
`~/.shellcraft/ai-policy.toml` (`ai policy` lists them), if you want to be asked or to name the hosts allowed.

## Reading your aggregator's files

Suppose your EDR (or a scheduled job) writes, for every device, the output of commands into a folder on one
box, a new timestamped file per run:

```
/srv/edr/
  192.168.1.2/
    ps-2026-11-07T1900.txt   netstat-2026-11-07T1900.txt   ss-2026-11-07T1900.txt
    lsof-2026-11-07T1900.txt  ebpf-2026-11-07T1900.txt     free-2026-11-07T1900.txt
  192.168.1.3/ ...
```

Make the box a host (steps 1 and 2 above, `ai = true`), then describe it in `~/.shellcraft/aggregators.toml`
(`--setup-analyst --aggregator` writes this for you from what it finds):

```toml
[aggregators.edr]
host = "edr-box"            # the ssh host
root = "/srv/edr"           # one folder per device below this
stale_minutes = 30          # a kind whose newest file is older than this is reported STALE

[aggregators.edr.files]     # kind = a glob that matches that kind's files
ps = "ps-*.txt"
netstat = "netstat-*.txt"
ss = "ss-*.txt"
lsof = "lsof-*.txt"
ebpf = "ebpf-*.txt"
memory = "free-*.txt"

[aggregators.edr.devices]   # optional: a friendly name for a folder (the folders are often IPs)
web01 = "192.168.1.2"
```

Then say *"Check out device web01."* The `edr-analyst` agent lists the device's kinds with the age of each newest
file, reads them, scans them with `hostscan` and `health`, compares them with the run before through `baseline`,
and records what it finds. A file that stopped updating, or a kind with no file at all, is a finding of its own:
the EDR agent on that device may have stopped. In a watch plan a device is a `[[devices]]` entry:

```toml
[[devices]]
aggregator = "edr"
name = "web01"
kinds = ["ps", "netstat", "ss"]    # default: all of the aggregator's kinds
```

The AI reads files here and runs nothing on the devices. Their text can be steered by whoever controls a device
(a process name, a command line), so it is framed as untrusted data like everything else.

## System health

`health` reads the numbers a stable system needs and flags what is not: low memory or swap in use, load above
the CPU count, a full disk or inode table, an OOM kill (more serious when the process killed was `sshd`,
`cloudflared`, `nginx` and similar), kernel errors, a process hogging memory, many zombies. It works on the
EDR's `free`, `df`, `uptime`, `top`, `vmstat` and `dmesg` files, and on `sshrun HOST free|df|uptime|oom`.
Thresholds are yours to change in `~/.shellcraft/health-rules.toml` (`man health`), for example
`disk_full_pct = 85` or `ignore_mounts = ["/boot/efi"]`.

## Logs, including binary ones

`log-reader` picks the reader for the file: `log-tail` and `log-grep` for text, `zlog` for `.gz`, `binlog`
for wtmp and btmp, `journal`, `strings` for anything else. A log must be inside the host's `log_paths`.

## Your SIEM, Uptime Kuma and other APIs

Describe a source once and the AI can pull it, with only the parameters you allow (`man feed`):

```toml
# ~/.shellcraft/feeds.toml
[feeds.kuma]
url = "https://kuma.example.com/api/status-page/heartbeat/main"
path = "heartbeatList.*"
fields = ["status", "time", "msg"]
```

For anything a feed cannot do, write a [func](funcs.md) and add one line to its header:

```
# ai: read
```

An AI then runs it with `run_func`, in the same read-only sandbox as `shellcraft_pipeline`: it can read
and print, and it cannot write a file or run an OS program. `func list` marks those funcs.

## Keep watching

Write a plan, `~/.shellcraft/watchplans/prod.toml` (`man watchplan` has every field):

```toml
interval_minutes = 15
quiet_hours = "23:00-06:00"

[[hosts]]
name = "web01"
checks = ["ps", "ss", "exes", "cron", "authorized-keys", "probes"]

[[feeds]]
name = "kuma"
```

Then: *"Use the orchestrator agent and keep watching the prod plan."* Each round it makes one task per host, device and
feed, starts the analysts, merges what they find, sends one summary alert and waits for the next round. It
stops when you tell it to. During quiet hours only critical alerts come through.

The loop lives in your Claude Code session, so it runs while that session is open. Each round is cheap
because analysts look only at what `baseline` says is new. To have rounds run without a session, use a timer.

### What a round leaves behind

Every analyst ends its host, device or feed with a status: `ok`, `warn` or `crit`, one line of summary and
how much changed. `fleet` shows the newest one of every machine on one screen, the ones that need you first:

```
fleet                 # every machine: status, last round, changes, open findings
fleet web01           # one in full: its checks, its findings, its reports
```

A machine whose last round is more than two intervals old is marked `LATE`, so a watch that stopped does
not look like a quiet night. When a round found something, the analyst also writes a Markdown report to
`~/.shellcraft/reports/`, and the alert carries its path: right-click the alert, and Alt-v pastes the path at
your prompt. `report show host:web01` prints the newest one. [The fleet view](fleet.md) has the details.

### Rounds on a timer

One command runs one round and ends:

```
shellcraft --watch-rounds prod
```

It first asks the plan whether a round is due. When none is due it prints `next round in N s; nothing started`
and ends, and that costs nothing. When one is due it starts Claude Code without a window, as the orchestrator,
for this one round. The analysts check each unit, findings go to your case file, and alerts go to your
ShellCraft. With no shell open an alert waits, and the next shell you open shows it as missed.

To have it run on its own:

```
shellcraft --install-timer prod               # a systemd user timer, every interval_minutes of the plan
shellcraft --install-timer prod --every 120   # or your own number of minutes
shellcraft --remove-timer prod
```

Add `--dry-run` to any of the three to see what would happen. For a round that is the command, the agents and
the prompt. For a timer it is the two unit files. Nothing is started or written.

| | |
| --- | --- |
| Nothing to install | A round brings its agents and skills with it. It needs no `--install-ai`, no server entry in Claude Code and no running ShellCraft. It does need the Claude Code client, logged in, and hosts that are enabled for the AI as above. |
| What a round may use | The analyst tools of ShellCraft and its own sub agents. It has no shell, no file editing and no web access on your machine. |
| Cost of one round | `--budget USD` is the most one round may cost (default 2). A round that reaches it is stopped and counts as failed. |
| Cost of a day | `--day-budget USD` is the most all rounds of one day may cost together (default 10, `0` for no limit). When it is used up, rounds stop until tomorrow and you get one alert. |
| The log | Each run is one line in `~/.shellcraft/rounds.log`: time, plan, `ok`, `failed` or `skipped`, cost, seconds and the summary. |
| A round that fails | You get a `warn` alert with the reason: the client is missing, the budget was reached, it ran longer than 30 minutes. The same failure again is logged, not alerted again. |
| Never two at once | A call for a plan whose round is still running starts nothing. |
| Timing | The next round is counted from the start of the last one, so a 15 minute timer on a 15 minute plan runs every time. |
| Quiet hours | A round still runs. Only a `crit` alert is sent. |
| Logged out | A user timer runs while you are logged in. Run `loginctl enable-linger` once and it runs at any time. |
| ssh | The timer's service gets your `PATH` and `SSH_AUTH_SOCK` as they were when you installed it, so a key in your agent works while you are logged in. For rounds while logged out, the host needs a key that works without an agent. |
| No systemd | `--install-timer` prints a crontab line for you to add. |

What does a round cost? A quiet round on one host with seven checks took 76 seconds and cost $0.92 here. At
that price a round every 15 minutes uses up the default day budget in under three hours, and the rounds then
stop until the next day. Pick the interval to fit what you want to spend: `--every 120` is 12 rounds a day.

`shellcraft --check-analyst` lists your timers and the last round. `systemctl --user list-timers` shows when
the next one runs, and `journalctl --user -u shellcraft-rounds-prod` shows everything a round said.

## What it will not do

- Run a command on a host that is not one of the named checks, or on your machine at all (analyst role).
- Kill, block, delete or change anything. Containment is yours: it proposes it with `request_human_review`.
- Follow instructions it finds in logs, process names or API answers. Those are data, and a line that tries
  to give orders is recorded as a finding.
- Tell you a host is clean. It tells you what it checked, what it could not, and what changed.

## How it is kept safe

| Risk | What stops it |
| --- | --- |
| The AI types its own commands | `sshrun` builds the remote command from fixed templates; arguments are checked and quoted |
| An unlisted host | Only hosts with `ai = true` in `ssh-hosts.toml`; names pass a strict pattern and go after `--` |
| Reading files it should not | Log paths must match the host's `log_paths`, and the host resolves symlinks first |
| Reading outside the aggregator's root | A device must be a real folder directly under the root; a symlink leaving it is refused; a file is read only if it was in the listing and has a plain name |
| Root too wide | `sudo -n` only on hosts you mark, only for the commands in the sudoers lines you installed |
| Secrets in output | Passwords, tokens and private keys are replaced with `REDACTED` before the AI sees them |
| Prompt injection from a host | Output is framed as untrusted data, and the `untrusted-data` skill tells every agent how to treat it |
| Stale answers | The server remembers module answers for five minutes to save API calls, but never for `sshrun`, `aggregator`, `baseline` or `watchplan`, which read the present or write state; `feed` calls in a round pass `fresh` |
| A runaway loop | `[budget]` in `ai-policy.toml`, per-call timeouts, a 128 KB output cap, one round at a time; a round on a timer has a budget, a day budget and 30 minutes |
| Knowing what happened | Every call is in `ai log`, with the agent's name and what it asked (`host=web01 check=proc-cmdline arg=812`) |

## When something fails

| You see | Do this |
| --- | --- |
| `web01 is not enabled for AI use` | Add `[hosts.web01]` with `ai = true` to `~/.shellcraft/ssh-hosts.toml` |
| `is not a Host in ~/.ssh/config` | Add an explicit `Host web01` entry (wildcards do not count) |
| `cannot reach web01` | Run `ssh -o BatchMode=yes web01 true` yourself: it needs a key and a known host key |
| `no aggregator named 'edr'` | Add `[aggregators.edr]` to `~/.shellcraft/aggregators.toml`, or run `shellcraft --setup-analyst --aggregator HOST --root PATH` |
| `STALE ps` / `MISSING lsof` | The EDR has not written that kind lately (or its file name does not match the glob in `aggregators.toml`): check the agent on the device |
| `no server named 'shellcraft'` from `--check-analyst` | The agents need the server called exactly `shellcraft`: `shellcraft --setup-analyst` writes it |
| `needs root on web01` | Set `sudo = true` (and install the sudoers lines) or `root = true` |
| `sudo wants a password` | The sudoers lines are missing or the command path differs: `sshrun --sudoers web01` |
| `path not allowed on this host` | Add the log's folder to `log_paths` for the host |
| `the analyst role may not use this tool` | That is the role working; run the command yourself if it is yours to run |
