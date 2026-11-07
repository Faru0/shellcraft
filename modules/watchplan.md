# watchplan

The list of things to **keep checking**: which hosts and checks, which feeds and funcs, how often.
You write it as a file; an AI that watches your hosts reads it with this module, makes one task per
host, device or feed, and knows when the next round is due. `watchplan` runs nothing itself.

## Synopsis

```
watchplan list
watchplan show NAME
watchplan check NAME
watchplan tasks NAME
watchplan due NAME
watchplan done NAME
```

## Examples

```
watchplan list
watchplan check prod
watchplan tasks prod
watchplan due prod
```

## The file

`~/.shellcraft/watchplans/NAME.toml`:

```toml
description = "production web tier"
interval_minutes = 15            # 1 to 1440
quiet_hours = "23:00-06:00"      # only crit alerts then (optional)

[[hosts]]
name = "web01"                   # a host enabled in ssh-hosts.toml (man sshrun)
checks = ["ps", "ss", "exes", "cron", "authorized-keys", "probes", "bpf-progs"]
deep = true                      # look closer at anything new (default true)
logs = [ { path = "/var/log/auth.log", grep = "Failed password" } ]

[[devices]]
aggregator = "edr"               # an aggregator in aggregators.toml (man aggregator)
name = "web01"                   # a device: its friendly name or its folder (often an IP)
kinds = ["ps", "netstat", "ss"]  # which kinds of file to read (default: all of the aggregator's kinds)
deep = true

[[feeds]]
name = "kuma"                    # a feed in feeds.toml (man feed)
query = ["since=3600"]

[[funcs]]
name = "inventory"               # a func marked "# ai: read"
args = []
```

## Commands

| Command | Does |
| --- | --- |
| `list` | The plans that exist. |
| `show NAME` | A readable summary and when the next round is due. |
| `check NAME` | Validates the file. Names, paths and counts are checked (at most 60 units). |
| `tasks NAME` | The units of work as JSON, one per host, device, feed and func. |
| `due NAME` | `due now` or `next round in N s`, and whether quiet hours apply. |
| `done NAME` | Records that a round just finished, so the next one is timed from now. |

The time of the last round is kept in `~/.shellcraft/watchplans/.state/NAME.json`.

## Hosts and devices

A `[[hosts]]` entry is read live over ssh (`sshrun`). A `[[devices]]` entry is read from the files your EDR
keeps on an aggregator (`aggregator`). Put the same machine in both and the AI looks at the files first and
confirms anything odd live.
