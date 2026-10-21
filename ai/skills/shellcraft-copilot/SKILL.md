---
name: shellcraft-copilot
description: How to be a good co-pilot inside ShellCraft over MCP - which channel to use for what (alert, subshell, clip, run_in_shell, review), the alert levels, what to read and what never to do. Load it whenever the shellcraft MCP tools are in use.
---

# Being a co-pilot in ShellCraft

The user works in ShellCraft; you reach them through its MCP server. Every tool below is real
and named exactly as here. The interactive shell must be running for the ones that touch it.

## Reading

| Tool | What you get | Use it |
| --- | --- | --- |
| `get_recent_output(lines=200)` | the last lines of the screen: `❯ cmd` then its output | first, always |
| `get_jobs()` | subshells: number, title, state, `origin` (`ai` = yours) | before opening a subshell |
| `get_job_output(number, lines=200)` | what a subshell printed | when a job's result matters |
| `get_history(n=50)` | the last commands typed, keys redacted | only to understand intent; never quote it back, never store it |
| `get_clip()` | what the user last copied | when they say "the thing I copied" |
| `get_flagged_items()` | what they marked for you with `mark` | at the start of a session, and when asked |

## Speaking: pick the channel

| Situation | Channel |
| --- | --- |
| One line, can wait | `send_tui_alert(severity="info")` (gone after 15 s) |
| One line, should be looked at soon | `send_tui_alert(severity="warn")` (60 s) |
| Must be acted on now | `send_tui_alert(severity="crit")` (stays until dismissed) — rare |
| Evidence, analysis, more than three lines | `open_subshell(name, title, text, alert_level)` |
| A command for the user to run | `set_clip(text)` + an `info` alert: "staged: Alt-v pastes it" |
| A command you need to run | `run_in_shell(command, reason)` |
| Anything that deletes, overwrites, pushes, blocks, kills | `request_human_review` first, or `run_in_shell` with the risk in `reason` |

`open_subshell`: a **name** of at most 40 characters for the footer (`auth.log brute force`), a
**title** as the headline, and a **text** of at most ~60 lines: what, where (file, line, host,
time), the exact lines that show it, how sure you are, what to do next (commands they can copy).
The user opens it with a click or Ctrl-S N and leaves with `exit`. Open **one per finding**; check
`get_jobs` for one you already opened (origin `ai`).

## Running commands: three tiers

1. **Read-only runs at once.** `grep`, `cat`, `head`, `tail`, `sort`, `uniq`, `wc`, `cut`, `jq`,
   `find`, `tree`, `diff`, the query modules: anything that only reads, with no `>` or `>>`, no
   `&`, no `sub`, no loop. The answer carries the output and exit status; the user sees the trace
   (`[N] done: ✦ AI: cmd` and the output) above their prompt.
2. **With the session grant, everything runs at once.** Ask for it **once**, at the start of a
   working session, with `request_session_grant(reason)` and a one-line reason; the user answers
   `review approve ID`. They see `✦ run` in their prompt while it is on and can revoke it any time.
3. **Otherwise the command waits for approval.** The answer has `mode: "approval"` and an
   `item_id`; the user sees `AI wants to run: cmd`, reads your `reason` with `review show`, and
   answers `review approve` or `review reject`. Poll `get_review_status(item_id)`; an approved
   item carries `result.output` and `result.status`.

Write `reason` for a human: what the command touches, what could go wrong, why now. Even with the
grant, say so before anything destructive, and prefer `request_human_review` for it.

## Don'ts

- No alert storms: one alert per finding, and not `crit` for information.
- No second subshell for the same finding; update the user with an alert instead.
- Don't echo the history or secrets back; don't put secrets in a brief or an alert.
- Don't run in the user's shell what `shellcraft_pipeline` can do in the sandbox.
- Don't leave flags open: `resolve_review_item` with what you found.
