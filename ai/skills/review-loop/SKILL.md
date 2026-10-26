---
name: review-loop
description: The two-way review queue between a ShellCraft user and an AI - flags the user marks, approval requests, commands run through run_in_shell, the session grant, and how to poll results. Load it when get_flagged_items, request_human_review, run_in_shell or request_session_grant are in play.
---

# The review loop

Everything lives in the user's `~/.shellcraft/review/` as items with an id like
`r-20261019-142233-a1b2c3`; the user handles them with `review`, you with the tools.

## Flags: the user → you

- The user marks things with `mark` (`pytest | mark -t test-failure -p high`, `mark --file app.log`).
- `get_flagged_items(status="pending", kind="flag")` returns them newest first, with the content
  (the last 256 KB of each piece). `include_artifacts=false` for a quick list.
- When you have dealt with one: `resolve_review_item(item_id, resolution_notes)`, one paragraph.

## Approval: you → the user

- `request_human_review(title, description, artifacts, priority, wait_seconds)` for anything
  destructive or hard to undo. The user sees `AI asks for review: TITLE` in their footer, reads it
  with `review show ID`, and answers `review approve ID notes` or `review reject ID notes`.
- With `wait_seconds` the call returns when they answer (up to 600 s); otherwise poll
  `get_review_status(item_id)`. Go ahead only on `approved`, and `resolve_review_item` afterwards.

## Commands: `run_in_shell`

- `run_in_shell(command, reason, wait_seconds)` runs in the user's interactive shell.
  - `mode: "read-only"` or `"granted"`: it ran; the answer has `output`, `status`, `number` (the
    job), `pending: true` if it was still running at the deadline (then `get_job_output(number)`).
  - `mode: "approval"`: it waits in the queue as `AI wants to run: COMMAND`; the answer has
    `item_id`. Poll `get_review_status`: `approved` carries `result` (`output`, `status`,
    `error`, `seconds`), `rejected` carries the user's notes.
  - An error that says `not run`: the policy or your role does not allow it. Do not retry it
    another way; tell the user what you needed and why.
- Access is **on by default** (`settings ai_access`), so `run_in_shell` runs at once. The user
  sees `✦ run` in their prompt and can turn it off with `ai off`; then `request_session_grant`
  asks once for the session, and `granted: true` means later runs go at once. If access is already
  on it answers `granted: true` without asking. A deny rule always wins, and an ask rule makes a
  command wait even with access on.
- Pass `agent` (your name, like `worker-2`) on every call so the user can tell agents apart.

## Priorities and levels

`low` → `info` alert, `medium` → `warn`, `high` → `crit` (stays until answered). Use `high` only
when the user must answer now.
