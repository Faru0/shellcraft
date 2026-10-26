---
name: soc-triage
description: A SOC-style triage routine over logs, hosts and indicators using ShellCraft's pipeline tools and bundled modules, with a fixed finding format and escalation rules. Load it for "triage", "is this malicious", "look at this log", "what is this IP/domain".
---

# Triage with ShellCraft

Work in small steps with `shellcraft_pipeline`; each call is one command line. Say what each step
showed before the next. Hand findings over as the `shellcraft-copilot` skill says.

## 1. Scope

- What was flagged (`get_flagged_items`), what is on screen (`get_recent_output`), what the user
  named: a file, a host, an IP, a time window.
- Size it first: `wc -l FILE`, `head -3 FILE`, `tail -3 FILE`; for JSON logs `head -1 FILE | jq .`
  to see the shape (`jq -t` tabulates, `jq -p` lists the paths).

## 2. Look for the usual things

```
grep -i "failed password\|invalid user" auth.log | wc -l
grep -i "failed password" auth.log | grep -o "from [0-9.]*" | sort | uniq -c | sort -n | tail
grep -i "accepted" auth.log | grep -o "for [a-z]* from [0-9.]*" | sort | uniq -c
grep -i "sudo\|COMMAND=" auth.log | tail -20
cat access.log | cut -d' ' -f1 | sort | uniq -c | sort -n | tail        # top clients
grep -E "\.(php|exe|sh)\b" access.log | grep -v " 200 " | tail            # odd requests
jq -w 'level=error' app.json | jq -t                                     # errors in a JSON log
```

Then the indicators: `ip2geo IP`, `queryDns DOMAIN -t A -t MX -t TXT`, `queryCert DOMAIN -s`,
`queryCensys host IP` (when a key is set). A private address (10/8, 172.16/12, 192.168/16) needs
no lookup.

## 3. Write each finding like this

```
WHAT:      brute force against sshd (root, admin), 1,842 attempts in 14 min
WHERE:     /var/log/auth.log, 02:11–02:25, host web-1
EVIDENCE:  Oct 19 02:11:03 sshd[4122]: Failed password for root from 203.0.113.9 port 51022
           (and 1,841 like it; 3 "Accepted password for deploy" from the same IP at 02:24)
SOURCE:    203.0.113.9 — AS64496, hosting provider, Amsterdam (ip2geo); no PTR
CONFIDENCE: high (the accepted logins after the burst are the concern)
NEXT:      grep "203.0.113.9" /var/log/auth.log | grep -i accepted
           last -a | grep deploy
           consider: block the IP, rotate deploy's password / keys
```

## 4. Escalate by severity

| Severity | Alert level | Also |
| --- | --- | --- |
| informational (noise, a known scanner) | `info` | resolve the flag with a one-liner |
| suspicious, needs a look today | `warn` | `open_subshell` with the finding |
| likely compromise or data at risk | `crit` | `open_subshell`, stage the first next command with `set_clip`, propose containment through `request_human_review` |

Containment and clean-up (blocking, killing processes, deleting files, rotating credentials) is
always the user's decision: propose, don't do.

## 5. Record

Keep each finding in the case file (the `case-file` skill): `list_findings` first, then
`add_finding` with kind, value, a one-line title, the exact lines as evidence, and an honest
severity and confidence. The WHAT / WHERE / EVIDENCE format above is for the `open_subshell` brief and
for talking to the user; the case file is for everyone else: other agents, the report. When you work from a task on the
board, claim it and finish it with the finding ids (the `task-board` skill).

## 6. Close

`resolve_review_item` with a short summary: findings, what you did, what is still open.
