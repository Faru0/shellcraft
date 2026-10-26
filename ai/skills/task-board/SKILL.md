---
name: task-board
description: How several AI agents share work through ShellCraft's task board - creating tasks with dependencies, claiming one exclusively with a lease, reporting progress, blocking, finishing. Load it when create_task, claim_task, update_task or list_tasks are in play.
---

# The task board

The board is shared by every agent connected to the same user's ShellCraft, and the user sees it
too (`tasks`; the prompt shows `✦ N tasks`). Pass `agent` (your name, like `worker-2`) on every
call: it is who the board says holds a task.

## For the orchestrator

- `create_task(title, description, priority, depends_on, parent)`. The title says what, the
  description says where to look and what done means, so a worker needs nothing else.
- Make a task for work a worker can do alone and finish: "triage /var/log/auth.log", "look up the
  top 10 source IPs". Not for one command. Put order in `depends_on`: a task cannot be claimed until
  what it depends on is `done`.
- `list_tasks` shows status, owner, what each waits for (`waiting_for`) and a `stale` flag. `claimable: true`
  shows what a worker could take right now.
- A worker that dies stops renewing its claim, and after five minutes the task shows `stale`
  and can be claimed again. Start another worker.

## For a worker

1. `claim_task()` takes the most urgent free task; `claim_task(task_id)` a specific one. An error
   says who holds it, or what it waits for. A claim is a lease of five minutes (`lease_seconds` changes it).
2. Work. At least every few minutes `update_task(task_id, status="working", note="...")`: it renews the
   lease. A good note is one line of progress.
3. Finish with `update_task(task_id, status="done", result="...")`: a few lines, the finding ids
   and what is left. `status="failed"` with the reason if it could not be done. `release_task` if
   you cannot do it at all, so another agent can.
4. Stuck on something only the user can give: `update_task(status="blocked", note="what you need")`.
   The user gets an alert with your note. Wait with `list_tasks(task_id)` or `ask_user` for a quick choice.

Only the owner updates a task. The user can finish or cancel any task from their side.

## Don'ts

- Don't claim two tasks at once.
- Don't sit on a task silently: update it, or release it.
- Don't create tasks for the user's attention; use `ask_user` or an alert for that.
