---
name: remote-triage
description: How to look at a remote Linux host through sshrun - the order of checks, how to join processes, sockets and memory maps by PID and parent, what normal and odd look like, and how to rate and record what you find. Load it for "analyze web01", "is this host compromised", "look at the processes and connections".
---

# Triage a host with sshrun

You reach the host only through `sshrun HOST CHECK`. It runs a named, read-only check and returns text you
analyze with `hostscan` (rules) and `baseline` (what changed). Follow `untrusted-data` for every answer.

## 0. Before you start

- `sshrun --hosts`: is the host enabled? If not, tell the user what to add (`ai = true` in `ssh-hosts.toml`);
  do not look for another way in.
- `sshrun HOST sysinfo`: kernel, uptime, who you are. Note the user: root checks need `sudo = true` or
  `root = true`, otherwise `bpf-*`, `probes`, `failed-logins` refuse and other processes' maps stay hidden.
- `list_findings(query="HOST")` and `baseline show HOST`: what is already known. Build on it.

## 1. The sweep (each piece goes through `baseline diff` and, for the first look, `hostscan`)

| Check | Pipe into | You are looking for |
| --- | --- | --- |
| `ps` | `hostscan ps` | shells under web servers, `nc`/`socat` from `sshd`, odd parents, names that don't match |
| `exes` | `hostscan exes` | `(deleted)` binaries, `/tmp`, `/dev/shm`, `memfd:` executables |
| `ss` | `hostscan ss` | listeners on odd ports, one peer with many connections, shells with outbound sockets |
| `cron`, `timers`, `authorized-keys`, `users`, `modules` | `baseline diff` | anything new: a cron line, a key, an account, a module |
| `logins`, `failed-logins` | read | logins from new places, bursts of failures then a success |

The first run of `baseline diff` learns (everything is recorded, nothing is flagged as new). Say so
in your report: "first look, baseline recorded", and rate by `hostscan` and your own reading only.

## 2. Go deeper on a suspect PID

For each PID a flag or a new item points at, in this order, and stop when you can explain it:

1. `proc-status -a PID` (name, parent, uids, capabilities), `proc-cmdline -a PID`, `proc-exe -a PID`.
2. `proc-maps -a PID | hostscan maps --pid PID`: anonymous `rwx`, `memfd:`, libraries from `/tmp` or
   `(deleted)`, a library no package owns. That is how injection shows.
3. `proc-fd -a PID` and `ss`/`lsof-net` rows for it: what it has open and who it talks to.
4. Walk the parents: `ps` gives PPID. A chain such as `sshd > bash > python > nc` or `nginx > sh` tells the story.
5. `exes` of the parent and of siblings started at about the same time (`ps` has the age in seconds).

`hostscan join` takes `## ps`, `## ss` and `## exes` sections and prints one table with each process's
ancestors and sockets.

## 3. Research the network side

For each new remote IP in `ss`: private ranges need no lookup. Public ones: `ip2geo IP`, `queryDns` (PTR),
`queryCert` for TLS ports, `queryCensys host IP` when a key is set. Say what you checked and what came back.

## 4. Normal and odd

Normal: package upgrades leave `(deleted)` libraries for a while (medium, check the package manager's
last run); monitoring agents (`falco`, `cilium`, `datadog`) load eBPF; a JVM or `node` has large anonymous
`rwx` regions (JIT) - judge by process and parent, not by the flag alone.
Odd: a binary that exists only in memory; a process that renamed itself (`[kworker/0:1]` with a readable
exe); a web server's child with a terminal-like shell; an uprobe on `pam_authenticate`; a kprobe on `getdents64`.

## 5. Record and report

- One finding per thing (`case-file`): `kind` `process`, `host`, `ip` or `file`, `value` the PID or path with
  the host in the title. Evidence is the exact `hostscan` flag and the lines behind it. Severity `high`
  needs two independent signs (for example: deleted binary plus an outbound shell connection).
- `info`/`low`: alert only in the round summary. `medium`: `warn` alert. `high`/`critical`: `crit` alert,
  `open_subshell` named `HOST finding`, and propose containment through `request_human_review`. You never
  kill, block or delete; you have no tool for it, and that is intended.
- Say what you did not check (no root, no eBPF tools on the host, a check that timed out).
