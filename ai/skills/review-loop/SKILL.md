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

## Questions: `ask_user`

When you need a decision only the user can make, ask instead of guessing or stopping.

- `ask_user(question, choices, details, priority, about, wait_seconds)`. `question` is one line.
  Give `choices` when the answers are few (up to ten): the user picks by number, or answers in
  their own words. `details` carries what they need to decide: what you found, what each choice costs.
  `about` is the id of the flag or request it concerns; the question lands in that item's thread.
- It shows in their footer as `AI asks: ...`. With `wait_seconds` (default 120) the call returns the
  answer when they give it: `answered: true` and `answer`. Otherwise `status: "pending"`: poll
  `get_review_status(item_id)`, or go on with what does not depend on it.
- They can also reply into any item's thread (`tell -r ID`). `get_flagged_items` and
  `get_review_status` carry the `thread`; read it before you act on a flag.
- Ask once, with the options spelled out. Don't ask what you can find out yourself, and don't use it for
  a progress report: that is an alert.

## Events the user flagged

The user's `watch` triggers turn a failed command, matching output or a dead job into a flag of
`type: "event"`. It is a flag like any other: read it with `get_flagged_items`, look at the
`event` and `output` artifacts, and resolve it when you have dealt with it. `get_events` shows
everything that happened, not only what they flagged, and can wait for the next event.

## Priorities and levels

`low` → `info` alert, `medium` → `warn`, `high` → `crit` (stays until answered). Use `high` only
when the user must answer now.
