# report

Keeps what a watch round saw on a machine. Each save writes **one status** (ok, warn or crit, with a
one-line summary) that the [`fleet`](../docs/fleet.md) view shows, and a **report file** in Markdown when
the round found something. The AI that watches your hosts calls it at the end of every host. You can
call it too.

## Synopsis

```
report save UNIT --status ok|warn|crit --summary TEXT [--changed N] [--flags N] [--plan NAME]
report list [UNIT]
report show UNIT [N]
```

## Examples

```
report save host:web01 --status ok --summary "7 checks, no change" --changed 0
cat notes.md | report save host:web01 -s warn -m "new listener on port 4444" --changed 2 --flags 1
report list
report list host:web01
report show host:web01
report show host:web01 2
```

## Commands

| Command | Does |
| --- | --- |
| `save UNIT` | Records the status of a unit. Text on stdin becomes the body of a report. A report file is written when the status is `warn` or `crit`, or when there is a body. It prints the path of the file. |
| `list` | The last status of every unit, with how many reports each has. |
| `list UNIT` | The last status of one unit and its reports, newest first, numbered. |
| `show UNIT [N]` | Prints report N of the unit. 1 is the newest and the default. |

## Options

| Option | Meaning |
| --- | --- |
| `-s`, `--status ok\|warn\|crit` | `save`: how the unit looks. `ok`: nothing new. `warn`: have a look. `crit`: it needs you now. |
| `-m`, `--summary TEXT` | `save`: one line that says what the round saw. Up to 300 characters, and about 100 fit a row of `fleet`. |
| `--changed N` | `save`: how many items were new or gone in the checks. |
| `--flags N` | `save`: how many things were flagged as worth a look. |
| `--plan NAME` | `save`: the watch plan the round belongs to. |

## Units

A unit is one thing a watch plan checks, named as `watchplan tasks` names it:

| Unit | Is |
| --- | --- |
| `host:NAME` | A host read live over ssh (`sshrun`). |
| `device:AGGREGATOR/NAME` | A device whose files are on an aggregator (`aggregator`). |
| `feed:NAME` | A feed (`feed`). |
| `func:NAME` | A func the plan runs. |

## Where it is kept

| Path | Holds |
| --- | --- |
| `~/.shellcraft/fleet/host/web01.json` | The newest status of `host:web01`. Each save replaces it. |
| `~/.shellcraft/reports/host/web01/20261112-162233-warn.md` | One report. The name is the time (UTC) and the status. |

A report starts with the unit, the status, the time and the summary, then the body as it was given.
Passwords, tokens and keys in the summary and the body are replaced by `REDACTED` before anything is
written. The files can be read by you alone. Reports older than 30 days are removed by the next save,
and a unit keeps its newest 200.

## See also

`fleet` shows the statuses in one table. `watchplan` is the list of what to check, and `findings` is
the case file with the evidence.
