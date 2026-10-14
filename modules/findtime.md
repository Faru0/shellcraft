# findtime

Show the current time from an internet time source, in UTC and the local time zone.

## Synopsis

```
findtime [-u] [-o] [-t SECONDS]
```

## Description

Asks timeapi.io for the time; if it can't be reached, falls back to the `Date` header of an HTTPS
server (accurate to the second). Prints the UTC time, the same moment in the local time zone, and
which source answered. `--offset` compares it with this machine's clock, to check that the clock
is right. Needs network access; the local clock is never used as the answer.

## Options

| Option | Meaning |
| --- | --- |
| `-u`, `--utc` | Print only the UTC time, one line (`2026-10-13 21:40:05 UTC`). |
| `-o`, `--offset` | Also show how far the local clock is ahead or behind. |
| `-t SECONDS`, `--timeout SECONDS` | Seconds to wait for each source (default 5, at most 60). |

## Examples

```
findtime
findtime -u
findtime --offset
findtime -u > now.txt
```

## Errors

- `findtime: no time source could be reached (…)`: no network, or both sources down.
- `findtime: --timeout must be between 0 and 60 seconds`
