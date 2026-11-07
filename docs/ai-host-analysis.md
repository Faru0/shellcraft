# Host analysis with an AI co-pilot

← [The manual](README.md) · [MCP and the AI co-pilot](mcp-and-ai.md) · [ai/ agents and skills](../ai/README.md)

You tell your AI "analyze web01", or "keep an eye on these servers and my monitoring", and it does the
looking: processes, sockets, memory maps, eBPF programs, kprobes and uprobes, cron, logins and logs, over
ssh. It researches the addresses it finds, remembers what it has already seen, and tells you through your
ShellCraft footer. It can read a lot and change nothing. This page sets it up and explains why that is safe.

## What you get

```
You  ->  Claude Code (the orchestrator agent)  ->  host-analyst, log-reader, feed-analyst, ebpf-auditor
                                                       |
                  sshrun (named read-only checks)   hostscan (rules)   baseline (what is new)
                  feed (SIEM, Uptime Kuma)          run_func (your funcs marked # ai: read)
                                                       |
                       findings (the case file)  +  alerts in your footer  +  a subshell with the evidence
```

- **`sshrun`** runs one named check on a host from your ssh config: `ps`, `ss`, `proc-maps 1203`,
  `bpf-progs`, `probes`, `log-tail /var/log/auth.log`. It cannot run anything else (`man sshrun`).
- **`hostscan`** reads that output and flags what looks wrong: a deleted binary, a shell under a web server,
  an unknown listener, an injected library, a risky eBPF program (`man hostscan`).
- **`baseline`** keeps what each host looked like and shows only what is new or gone (`man baseline`).
- **`feed`** and **`run_func`** bring in data from your other systems (`man feed`, [Funcs](funcs.md)).
- **`watchplan`** is the list of things to keep checking, and how often (`man watchplan`).

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
claude mcp add shellcraft-analyst -- /path/to/shellcraft/.venv/bin/python /path/to/shellcraft/main.py --mcp --role analyst
shellcraft --install-ai --team --user      # the agents and skills
```

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

Then: *"Use the orchestrator agent and keep watching the prod plan."* Each round it makes one task per host and
feed, starts the analysts, merges what they find, sends one summary alert and waits for the next round. It
stops when you tell it to. During quiet hours only critical alerts come through.

The loop lives in your Claude Code session, so it runs while that session is open. Each round is cheap
because analysts look only at what `baseline` says is new.

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
| Root too wide | `sudo -n` only on hosts you mark, only for the commands in the sudoers lines you installed |
| Secrets in output | Passwords, tokens and private keys are replaced with `REDACTED` before the AI sees them |
| Prompt injection from a host | Output is framed as untrusted data, and the `untrusted-data` skill tells every agent how to treat it |
| A runaway loop | `[budget]` in `ai-policy.toml`, per-call timeouts, a 128 KB output cap, one round at a time |
| Knowing what happened | Every call is in `ai log`, with the agent's name |

## When something fails

| You see | Do this |
| --- | --- |
| `web01 is not enabled for AI use` | Add `[hosts.web01]` with `ai = true` to `~/.shellcraft/ssh-hosts.toml` |
| `is not a Host in ~/.ssh/config` | Add an explicit `Host web01` entry (wildcards do not count) |
| `cannot reach web01` | Run `ssh -o BatchMode=yes web01 true` yourself: it needs a key and a known host key |
| `needs root on web01` | Set `sudo = true` (and install the sudoers lines) or `root = true` |
| `sudo wants a password` | The sudoers lines are missing or the command path differs: `sshrun --sudoers web01` |
| `path not allowed on this host` | Add the log's folder to `log_paths` for the host |
| `the analyst role may not use this tool` | That is the role working; run the command yourself if it is yours to run |
