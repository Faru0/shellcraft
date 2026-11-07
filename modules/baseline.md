# baseline

The "have I seen this before?" memory for a host-monitoring loop. You collect a snapshot of some
check on a host (processes, sockets, cron, authorized keys, kernel probes, ...), pipe the text in,
and `baseline diff` tells you only what is **new** or **gone** since last time. An unchanged host
costs one line. Pure Python: no network, no subprocess.

## Synopsis

```
baseline diff HOST CHECK [--no-store] [--show-same] [-o text|json]   (snapshot on stdin)
baseline put HOST CHECK                                              (snapshot on stdin)
baseline show HOST [CHECK] [-o text|json]
baseline accept HOST CHECK [ITEM...]                                 (or items on stdin, one per line)
baseline forget HOST [CHECK]
baseline list [-o text|json]
```

## Subcommands

- **diff** compares the snapshot on stdin with the stored one and prints `NEW (n)` lines (`+ item`), `GONE (n)` lines (`- item`) and a last line `HOST CHECK: n new, m gone, k unchanged, a accepted`. The first time a check is seen it just learns: `LEARNING HOST CHECK: recorded N item(s); nothing is flagged on a first run`. Afterwards it stores the new snapshot, unless you pass `--no-store`. `--show-same` also lists the unchanged items (`= item`).
- **put** replaces the baseline with the snapshot, silently. Use it to re-learn on purpose. Accepted marks are cleared.
- **show HOST** lists the checks stored for a host with item counts and last update. **show HOST CHECK** lists that check's items (and the ones gone in the last 30 days).
- **accept** marks items as known-good so they are never reported as new again. Give the items as arguments, written exactly as `diff` printed them, or one per line on stdin. Only accept things the user told you are fine.
- **forget** deletes one check, or all checks of a host.
- **list** shows every host with its checks and item counts.

Items that disappear are kept for 30 days. If one comes back, `diff` prints it as `+ item  (returned; first seen DATE)`, so a flapping item is recognised. A returned item still counts as new.

## Options

| Option | Meaning |
| --- | --- |
| `--no-store` | `diff` only: compare, but leave the stored baseline untouched. |
| `--show-same` | `diff` only: also list unchanged items. |
| `-o`, `--output text\|json` | `text` (default) or `json` (an object) for `diff`, `show` and `list`. |

## Normalisation

A snapshot is turned into a sorted set of items. For every check: blank lines and lines starting
with `#` are dropped (so the `# sshrun HOST CHECK (untrusted remote output)` header disappears),
runs of whitespace collapse to one space, and a `## name` line becomes a prefix (`name: item`)
for the lines below it. The CHECK name adds rules:

| CHECK | Input and item |
| --- | --- |
| `ps` | `PID PPID USER ETIMES ARGS...` becomes `USER ARGS`. Pid, ppid and elapsed time are dropped; numbers of 5 or more digits in ARGS become `N`. A restarted process is not new. |
| `ss` | `LISTEN`/`UNCONN`: `proto STATE local_addr:port procs`. Others: `proto STATE local_port->peer_ip:peer_port procs`, where any port from 32768 up becomes `*`. Pid and fd are dropped. |
| `authorized-keys` | `[options] type SHA256:fingerprint comment`. The key itself is never stored. |
| `bpf-progs`, `bpf-links`, `bpf-maps` | JSON array or `bpftool -j` output: `type name` per object (plus attach type or target for links). Ids, tags and timestamps are dropped. Non-JSON input is read line by line. |
| `cron`, `timers`, `modules`, `kprobes`, `uprobes`, `listening`, `logins`, `exes`, `proc-*`, anything else | Line based. ISO timestamps and `Mon Nov  6 10:00:00 2026` style dates are removed from each line. |

## Where it stores

`baselines/HOST/CHECK.json` under the ShellCraft config folder (`~/.shellcraft`, or `$SHELLCRAFT_HOME`).
Files are written atomically, as UTF-8, readable only by you (0600) where the system supports it.
Each item keeps `first_seen`, `last_seen` and a `seen` count (ISO-8601, UTC). A corrupt file is
renamed to `CHECK.json.bad`, a note says so, and the check is treated as a first run.

HOST and CHECK are 1 to 64 characters: letters, digits, `.`, `_`, `-`, starting with a letter or digit.

## Limits

5000 items per snapshot (extra items are dropped and the output says `truncated`), 200 hosts, 50
checks per host, 500 characters per item.

## Examples

```
ssh web1 'ps -eo pid,ppid,user,etimes,args --no-headers' | baseline diff web1 ps
ss -tunap | baseline diff web1 ss --show-same
baseline accept web1 cron "root */5 * * * * /usr/local/bin/backup"
baseline show web1
baseline list -o json
```
