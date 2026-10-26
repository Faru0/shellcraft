---
name: case-file
description: How AI agents record what they find in ShellCraft's shared case file - one finding per IP, domain, hash, user or host, evidence merged, severity and confidence rated honestly. Load it when add_finding, list_findings, get_finding or update_finding are in play.
---

# The case file

Findings from every agent go into one case, so the user sees one picture, not one report per
agent. They read it with `findings` and export it with `findings export report.md`. Pass `agent`
(your name) on every call.

## Before you look at something

`list_findings(query="203.0.113.9")` or `list_findings(kind="ip")`. If another agent already
looked at it, build on that (`get_finding` shows the evidence) instead of repeating the work.

## Recording

`add_finding(kind, value, title, summary, evidence, severity, confidence, tags, task_id)`

- **kind** is what the finding is about: `ip`, `domain`, `url`, `hash`, `user`, `host`, `file`,
  `process`, `other`. **value** is the thing itself. The same kind and value is the same finding:
  adding it again merges your evidence into it, keeps the higher severity and confidence, and
  counts how many times it was seen. The answer says `created: false` when that happened.
- **title** says what is wrong or notable, in a line: "SSH brute force, then a login as deploy".
- **evidence** is the command and its exact output lines, or a note: `{"command": "grep ...",
  "output": "..."}`. Quote the lines that show it, not the whole log.
- **severity**: `info` (seen, nothing wrong), `low`, `medium`, `high` (likely compromise or data at
  risk), `critical` (active, act now). A new `high` or `critical` raises an alert for the user.
- **confidence**: `low` (a hunch), `medium` (fits, not proven), `high` (the evidence shows it).
  Severity and confidence are separate: a critical thing you are not sure of is critical, low confidence.

Rate honestly. A high rating you cannot back with evidence costs the user's attention.

## Changing

`update_finding(finding_id, ...)`: more `evidence`, a new `severity` or `confidence`, `status`
(`confirmed` when you proved it, `dismissed` when it was nothing), `add_tags` and `remove_tags`, or
a new `summary`. Do not delete findings; dismiss them with a reason in the summary.

## Don'ts

- No secrets in evidence. Cut a key or password out of an output line.
- One finding per thing. Put a story about several things in a `summary`, not in a new finding.
