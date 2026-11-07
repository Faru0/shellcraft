---
name: baseline-and-novelty
description: How to keep a monitoring loop cheap and honest with the baseline tool - learn on the first run, report only what is new or gone, accept known-good items only with the user's say-so, and never treat quiet as safe. Load it when baseline, watchplan or a repeated round is in play.
---

# Novelty, not re-reading

A round over a stable host should cost a few short answers. The `baseline` tool is how: pipe a snapshot in,
get back only what is **new** or **gone** since last time.

```
sshrun web01 ps | baseline diff web01 ps
```

## The rules

1. **First run learns.** The answer starts `LEARNING ... nothing is flagged on a first run`. The host's current state
   is now the normal. That is a decision the user should know about: say it once in the report ("web01 baseline
   recorded just now; if it was already compromised, so is the baseline"), and rate by `hostscan` flags alone.
2. **Look only at what the diff prints.** `NEW` items are the work. A `returned` item was seen before and went
   away: ask why (a service that restarts every night is normal; a listener that comes and goes is not).
   `GONE` items matter too: a monitoring agent or a log shipper that disappeared is a finding.
3. **Accept only with the user's say-so.** `baseline accept HOST CHECK ITEM...` makes an item never show as new.
   Use `ask_user` ("web01 now listens on 9100: node_exporter? accept it as normal?") and accept only on a yes. Never
   accept to make an alert go away.
4. **Quiet is not safe.** No new items means nothing changed since the baseline, not that the baseline is clean.
   Say "no change" in the round summary, not "all clear".
5. **Pair it with the rules.** Run `hostscan` on the same snapshot on a first look and when something is new;
   a new item that `hostscan` also flags goes up a level. A flag on an old, accepted item stays low.
6. **Feeds work the same way.** `feed NAME | baseline diff feed-NAME rows` shows only new rows.
7. **Reset on purpose.** After a reinstall or a big change the user may say "re-learn": `baseline put HOST CHECK`
   replaces the baseline (an explicit step, not something you decide).

## Item shape

Items are normalised lines (PIDs and timestamps removed, ephemeral ports collapsed), so a restarted process is
not new but a changed command line is. `baseline show HOST CHECK` lists what is stored. Authorized keys are
stored as a fingerprint, never as the key.
