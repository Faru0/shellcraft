---
name: watch-rounds
description: The round protocol for keeping hosts and feeds under watch - read the plan, one task per host or feed, analysts in parallel, merge, one summary alert, wait for the next round. Load it for "keep watching", "sweep these hosts every 15 minutes", "monitor my servers".
---

# A watching round

A watch plan (`watchplan list`, `watchplan show NAME`) says what to check and how often. A round is one pass.
You are the orchestrator; analysts do the looking. Use the `task-board` and `case-file` skills for the board and the
findings, `baseline-and-novelty` for the cheap loop, and `untrusted-data` for everything that comes back.

## One round

1. `watchplan due NAME`. If it says `next round in N s`, wait (see below) instead of starting early.
2. `watchplan tasks NAME` gives the units. `create_task` for each: title `host web01` / `device edr/web01` / `feed kuma`, the
   description holds the unit's JSON (its `id`, checks, logs, `deep`) and the plan's name, and tells the analyst to
   use `baseline diff` on each check, to go deeper (`remote-triage`) only on what is new or flagged, and to end
   with `report save ID --plan NAME`.
3. Start one analyst per ready unit with the Agent tool: `host-analyst` for hosts, `edr-analyst` for devices (files on an aggregator), `feed-analyst` for feeds and
   funcs, `log-reader` when a host has logs to read, `ebpf-auditor` when the plan lists `bpf-*` or `probes`.
   Name them (`host-analyst-web01`). At most 4 at once; the rest wait for a free slot.
4. `list_tasks` until all are `done`, `failed` or `blocked`. A `blocked` task needs you or the user (`ask_user`). A
   stale one lost its analyst: start another once, then report it.
5. Merge: `list_findings`, fix severities that disagree, join findings about the same thing (one `ip` seen
   from three hosts is one finding with three pieces of evidence). `report list` shows what each unit reported.
   A unit whose analyst left no status this round gets one from you: `report save ID --status warn --summary
   "not checked: WHY" --plan NAME`. A quiet unit still has a status, or the user's `fleet` view calls it LATE.
6. **One summary alert per round**, by the worst finding: nothing new → `info` "round N: no change on 5
   hosts" (only if the plan says to report quiet rounds; otherwise stay silent); medium → `warn`; high or
   critical → `crit` plus `open_subshell`. During quiet hours (`watchplan due` says so) only `crit` alerts.
   The same finding never alerts twice: a repeated one only updates its evidence. The alert's `content` is the
   report path of the worst unit (several paths: one per line), so the user pastes it with Alt-v.
7. `watchplan done NAME`, then `resolve_review_item` on what you handled.

## Between rounds

You are a Claude Code session, so waiting is yours to do cheaply: `get_events(wait_seconds=120)` returns as soon as
the user does something (a `tell`, a mark) and otherwise after the wait; repeat until `watchplan due` says due.
`get_events` needs the interactive ShellCraft to be running; if it answers that none is, wait with a short
`shellcraft_pipeline` call such as `sleep 60` instead (no alerts reach the user's footer then, so say so in your summary).
If your client offers a scheduled wake-up or a loop command, use it for the long waits instead of sitting in calls.
Stop when the user says stop, when the plan file goes away, or after the number of rounds you were given.

## Limits to keep

- Each analyst: `baseline diff` first, deep checks only on novelty, at most about 40 `sshrun` calls per host per
  round. A host that times out twice is reported once and skipped until the next round.
- Never run anything yourself that a task covers. You plan, route, merge and report.
- Containment (block, kill, delete, rotate) is the user's: `request_human_review` with the evidence.
