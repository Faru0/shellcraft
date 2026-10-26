"""tasks: the board your AI agents share (shellcraft.tasks)."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from rich.text import Text

from shellcraft import tasks as board
from shellcraft.builtins import builtin
from shellcraft.context import CommandError, Paged, Styled
from shellcraft.modkit import ArgParser

if TYPE_CHECKING:
    from shellcraft.context import ShellContext

TASKS_DOC = """# tasks

The task board your AI agents share. An orchestrating agent puts tasks on it, workers claim them
one each, report progress and finish them, and you see it all here. While there is work, the
prompt shows `✦ N tasks`; a task that is blocked or fails raises an alert.

## Usage

```
tasks                         # open, claimed, working and blocked tasks
tasks --all                   # finished ones too
tasks show ID                 # one task: description, notes, result
tasks add TITLE... [-p PRIORITY] [-d TEXT] [--after ID] [--parent ID]   # put a task on the board yourself
tasks done|fail|cancel ID [NOTE...]   # finish one whoever holds it
tasks clear                   # delete the finished ones
```

An ID may be the end of the full one, such as `a1b2c3`.

## How agents use it

`create_task` puts a task on the board, `claim_task` takes one (exclusive: no two agents get the
same task), `update_task` reports progress and finishes it, `release_task` gives it back. A claim
is a lease of five minutes that every update renews, so a worker that dies drops its task and
another can take it; the board marks such a task **stale**. A task may wait for others
(`--after`), and cannot be claimed until they are done.

## Statuses

`open`, `claimed`, `working`, `blocked` (the agent needs you), `done`, `failed`, `cancelled`.
Finished tasks are deleted after 7 days.
"""

STYLES = {"open": "sc.muted", "claimed": "sc.accent", "working": "sc.accent", "blocked": "sc.warning",
          "done": "sc.success", "failed": "sc.error", "cancelled": "sc.muted"}


def _short(task: dict[str, Any]) -> str:
    return task["id"][-6:]


def _line(task: dict[str, Any], names: dict[str, str]) -> Text:
    status = "stale" if task.get("stale") else task["status"]
    waits = f"  waits for {', '.join(names.get(d, d[-6:]) for d in task['waiting_for'])}" if task.get("waiting_for") else ""
    owner = f"  {task['owner']}" if task.get("owner") else ""
    style = "sc.warning" if status == "stale" else STYLES[task["status"]]
    return Text.assemble((f"{_short(task)}  ", "sc.accent"), (f"{status:<9}", style),
                         (f"{task.get('priority', ''):<7}", {"high": "sc.error", "medium": "sc.warning"}
                          .get(task.get("priority"), "sc.muted")),
                         (task["title"], ""), (owner, "sc.path"), (waits, "sc.muted"))


def _text(task: dict[str, Any]) -> str:
    lines = [f"{task['id']}  {task['status']}{' (stale)' if task.get('stale') else ''} · priority {task.get('priority')}",
             f"title: {task['title']}",
             f"from: {task.get('created_by', '')} at {task.get('created', '')}"
             + (f" · owner {task['owner']}" if task.get("owner") else "")]
    if task.get("parent"):
        lines.append(f"part of: {task['parent']}")
    if task.get("depends_on"):
        lines.append(f"after: {', '.join(task['depends_on'])}")
    if task.get("source"):
        lines.append(f"from review item: {task['source']}")
    if task.get("lease_until") and task["status"] in ("claimed", "working", "blocked"):
        lines.append("claim until " + time.strftime("%H:%M:%S", time.localtime(task["lease_until"])))
    if task.get("description"):
        lines += ["", task["description"]]
    if task.get("notes"):
        lines += ["", "── notes " + "─" * 20]
        lines += [f"{n.get('at', '')[11:19]}  {n.get('by', '')}: {n.get('text', '')}" for n in task["notes"]]
    if task.get("result"):
        lines += ["", "── result " + "─" * 20, task["result"]]
    return "\n".join(lines) + "\n"


@builtin("tasks", "The task board your AI agents share",
         "tasks [--all] | show ID | add TITLE... [-p PRIORITY] [-d TEXT] [--after ID] | done|fail|cancel ID [NOTE...] "
         "| clear",
         category="shell", stateful=True, doc=TASKS_DOC)
def tasks(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    try:
        if not args or args == ["--all"]:
            rows = board.list_tasks("all" if args else "active")
            if not rows:
                return Text("no tasks" + ("" if args else " (tasks --all shows the finished ones)"), style="sc.muted")
            names = {t["id"]: _short(t) for t in rows}
            out = Text()
            for task in rows:
                out.append_text(_line(task, names))
                out.append("\n")
            plain = "".join(f"{_short(t)}\t{t['status']}\t{t.get('priority')}\t{t.get('owner', '')}\t{t['title']}"
                            + (f"\twaits for {','.join(names.get(d, d[-6:]) for d in t['waiting_for'])}"
                               if t.get("waiting_for") else "") + "\n" for t in rows)
            return Styled(out, plain)
        action, rest = args[0], args[1:]
        if action == "show" and len(rest) == 1:
            text = _text(board.find(rest[0]))
            return Paged(text, page=True) if ctx.ui is not None and ctx.interactive and text.count("\n") > 40 else text
        if action == "add":
            parser = ArgParser("tasks add")
            parser.add_argument("-p", "--priority", default="medium", choices=("low", "medium", "high"))
            parser.add_argument("-d", "--description", default="")
            parser.add_argument("--after", action="append", default=[])
            parser.add_argument("--parent", default="")
            parser.add_argument("words", nargs="+")
            opts = parser.parse_args(rest)
            after = [board.find(a)["id"] for a in opts.after]
            parent = board.find(opts.parent)["id"] if opts.parent else ""
            task = board.create(" ".join(opts.words), opts.description, parent, after, "", opts.priority, "shell")
            return Text.assemble(("✓ ", "sc.success"), (_short(task), "sc.accent"), f" added: {task['title']}")
        if action in ("done", "fail", "cancel") and rest:
            task = board.find(rest[0])
            status = {"done": "done", "fail": "failed", "cancel": "cancelled"}[action]
            changed = board.finish(task["id"], status, "user", " ".join(rest[1:]))
            return Text.assemble(("✓ ", "sc.success"), (_short(changed), "sc.accent"), f" {status}: {changed['title']}")
        if action == "clear" and not rest:
            gone = [t for t in board.list_tasks("all") if t["status"] in board.DONE]
            for task in gone:
                board.remove(task["id"])
            return Text(f"removed {len(gone)} finished task{'s' if len(gone) != 1 else ''}", style="sc.muted")
    except board.TaskError as exc:
        raise CommandError(f"tasks: {exc}") from None
    raise CommandError("usage: tasks [--all] | show ID | add TITLE... | done|fail|cancel ID [NOTE...] | clear")
