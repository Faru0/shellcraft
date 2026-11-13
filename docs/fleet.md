# The fleet view: every machine on one screen

← [Manual](README.md)

When an AI watches your hosts for you, one question comes up every morning: is everything fine, and was
everything really checked? `fleet` answers it on one screen.

```
fleet
```

```
                                                Fleet
┏━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ unit           ┃ status ┃ last round ┃ changed ┃ findings ┃ summary                               ┃
┡━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ host:web01     │ warn   │ 4m         │       2 │ 1 medium │ new listener on 4444 by /tmp/.x/agent │
│ host:mail01    │ ok     │ 52m LATE   │       0 │ -        │ 7 checks, no change                   │
│ device:edr/pc7 │ -      │ never      │       - │ -        │ no round yet (plan prod)              │
│ host:db01      │ ok     │ 4m         │       0 │ -        │ 2 checks, no change                   │
└────────────────┴────────┴────────────┴─────────┴──────────┴───────────────────────────────────────┘
fleet UNIT shows one in full, report show UNIT prints its last report
```

The machines that need you come first: `crit`, then `warn`, then the ones that are late or were never
checked, then the quiet ones.

## Reading a row

| Column | Shows |
| --- | --- |
| unit | `host:NAME` is a host the AI reads over ssh. `device:AGGREGATOR/NAME` is a device whose files are on an aggregator. |
| status | What the last round said: `ok`, `warn` or `crit`. `-` means no round has reported on it yet. |
| last round | How long ago that was. `LATE` means it is more than twice the interval of the watch plan, so rounds were missed. `never` means there was none. |
| changed | How many items were new or gone in that round, over all checks. |
| findings | Findings about this machine in the case file that are not dismissed, and the highest severity among them. |
| summary | One line from the round, in the words of the analyst. |

`LATE` is the row to take seriously. A watch that stopped looks the same as a quiet night until you see how
old the last round is.

## One machine in full

```
fleet web01
```

```
host:web01  warn  4m ago
new listener on 4444 by /tmp/.x/agent
web server, sudo
watch plan prod, every 15 min, its last round began 5m ago
2 changed, 1 flag(s)

checks  what `baseline` remembers, and what its last look changed
  cron  4m ago    no change (6 item(s))
  ps    4m ago    1 new, 0 gone, 143 now
  ss    4m ago    1 new, 0 gone, 12 now

findings (1)
  3fa9c1  medium   process /tmp/.x/agent  Unknown program listens on 4444

reports  report show host:web01 N prints one
  1  2026-11-12 11:49  warn  ~/.shellcraft/reports/host/web01/20261112-164933-warn.md
```

The name alone is enough (`fleet web01`). When a host and a device share a name, the name means the host,
and `fleet device:edr/web01` is the device.

## Reports

A round writes a report only when it found something. A quiet round leaves one status line and no file.
The report is plain Markdown, written by the analyst that looked at the machine:

```
report show host:web01          # the newest report
report show host:web01 2        # the one before
report list host:web01          # all of them, newest first
report list                     # the last status of every unit
```

The alert of a round carries the path of its report. Right-click the alert to copy it, then Alt-v pastes
the path at your prompt: `less ` and Alt-v reads it.

Reports are kept for 30 days, at most 200 for one machine. Passwords, tokens and keys are replaced by
`REDACTED` before a report is written, but a report still quotes what a host showed, so treat the folder as
private. It is readable by you only.

## Where the rows come from

- every host with `ai = true` in `~/.shellcraft/ssh-hosts.toml` (`man sshrun`)
- every device in the `devices` table of an aggregator (`man aggregator`)
- every host and device in a watch plan (`man watchplan`)
- anything a round has reported on, which is how a feed or a func gets a row

So a host you just enabled shows up at once, with `never`, before any round ran. That is the point: you see
what is not watched yet.

A finding counts for a machine when it has the tag `host:NAME` (or just `NAME`), or when it is a `host`
finding with that name. The analysts tag their findings this way, and you can do the same for one of your own:
`findings set ID --tag host:web01`.

## In a script

`fleet` pipes as plain text, one line per machine with tabs between the columns:

```
fleet | grep -c LATE                               # how many machines missed their rounds
fleet | grep -E "warn|crit|LATE" | cut -f 1,2,6    # the ones that need you: unit, status, summary
fleet --json | jq -w 'status=crit' -f id           # the same data as JSON
```

In a func or a startup file you can have the shell tell you when a machine falls behind:

```
if fleet | grep -q LATE; then alert -l warn "a machine missed its rounds, see fleet"; fi
```

## How the status gets there

You do not write it. At the end of each host, device or feed of a round, the analyst calls `report save`
with a status, one line of summary and the counts. `fleet` only reads what is there, so you can run it as
often as you like, and it costs nothing.

```
report save host:web01 --status ok --summary "7 checks, no change" --changed 0 --plan prod
```

You can call it yourself, for example from a func that checks something the AI does not. The status is
kept in `~/.shellcraft/fleet/`, the reports in `~/.shellcraft/reports/`.

## See also

`man fleet`, `man report`, [Host analysis with an AI](ai-host-analysis.md) for the rounds that fill this
view, and `findings` for the case file behind the findings column.
