"""mark and review: the user's side of the review loop with an AI (shellcraft.review_spool)."""

from __future__ import annotations

import difflib
import os
from typing import TYPE_CHECKING, Any

from rich.text import Text

from shellcraft import review_spool as spool
from shellcraft.builtins import builtin
from shellcraft.commands._io import read_file
from shellcraft.context import CommandError, Paged, Styled
from shellcraft.modkit import ArgParser

if TYPE_CHECKING:
    from shellcraft.context import ShellContext

MARK_DOC = """# mark

Flag something for an AI to look at: what you pipe in, files, or a diff. It goes into the review
spool (`~/.shellcraft/review/`), where any MCP client of ShellCraft reads it with the
`get_flagged_items` tool or the `shellcraft://review/queue` resource, and marks it resolved when
it has dealt with it (`resolve_review_item`).

## Usage

```
pytest | mark --type test-failure            # the test output, for the AI
pytest | mark -t test-failure -m "flaky login test" -p high
mark --file app.log --file config.toml       # files, by name
mark --diff old.py new.py                    # a unified diff of two files
git diff | mark --type diff
pytest | mark --tee -t test-failure | tail   # pass the input on as well
```

## Options

| Option | Meaning |
| --- | --- |
| `-t TYPE`, `--type TYPE` | What it is: `test-failure`, `diff`, `log`, any word. |
| `-m TITLE`, `--title TITLE` | A short title (default: the type and the folder). |
| `-n NOTE`, `--note NOTE` | A longer note for the AI. |
| `-p LEVEL`, `--priority LEVEL` | `low`, `medium` (default) or `high`. |
| `-f FILE`, `--file FILE` | Add a file (give it more than once). |
| `-d A B`, `--diff A B` | Add the unified diff of two files. |
| `--tee` | Also pass what was piped in on, unchanged. |

Each piece keeps its last 256 KB (the end of a long test run is where its failures are), and says
so when it was cut. What you mark is stored as is: mark only what an AI may read. `review` lists
the marked items and the AI's requests; items that are done are deleted after 7 days.
"""

REVIEW_DOC = """# review

The review queue between you and an AI: what you marked for it (`mark`), and what it asked you
to approve before doing something destructive (its `request_human_review` tool). A request shows
as an alert (`info`, `warn` or `crit` for a low, medium or high priority) until you answer it;
the AI waits for the answer or asks again (`get_review_status`).

## Usage

```
review                        # what is pending, newest first
review --all                  # everything still kept (done items go after 7 days)
review show ID                # one item in full
review approve ID [NOTES...]  # let the AI go ahead
review reject ID [NOTES...]   # tell it not to
review answer ID TEXT|N       # answer a question the AI asked (N picks the Nth choice)
```

When the AI needs a decision from you it asks (`ask_user`), which shows in the footer as `AI asks:
QUESTION`, with numbered choices if it gave some. Answer with words, or with the number of a
choice; the AI, which may be waiting, gets your answer at once. To say more about any item
without answering it, `tell -r ID MESSAGE` adds to the item's thread, which `review show` prints
and the AI reads.

An ID can be shortened to its last characters when only one item matches (`review show a1b2c3`),
and Tab completes them. Only requests from the AI can be approved or rejected, and only while
pending.

## Commands the AI wants to run

With the `run_in_shell` MCP tool the AI runs commands in this shell. A read-only command (grep,
cat, jq, sort, the query modules; no redirection, no file changes) runs at once, as a background
job named `✦ AI: command` whose trace shows above your prompt. Any other command waits here as
`AI wants to run: command` (`review show ID` prints its reason):

```
review approve ID [NOTES...]  # runs it here, now, with your permissions; the AI gets the output
review reject ID [NOTES...]   # it never runs; the AI gets your notes
```

Ctrl-C stops an approved command (the AI is told). `review show ID` afterwards prints the output
under `── result`. With `settings ai_access` on, which is the default, the AI runs anything at
once and nothing waits here (`✦ run` shows in the prompt). With it off, the AI can ask for that
for the session (`request_session_grant`), which shows as `AI asks to run commands for this
session` and is granted with `review approve ID`. A rule in `ai-policy.toml` can still make a
command wait here (`man ai`).
"""


def _approve_and_run(ctx: ShellContext, item: dict[str, Any], notes: str) -> Any:
    """`review approve` of a command the AI wants to run (`run_in_shell`): run it here, now, in
    this shell with its full permissions, show its output, and store the output with the
    decision so the waiting AI gets both at once. Ctrl-C stops it (the AI is told)."""
    import time

    from shellcraft.context import ShellExit, to_text
    from shellcraft.output import show
    from shellcraft.parser import ParseError
    from shellcraft.pipeline import PipelineError, run_line

    if not ctx.interactive or ctx.ui is None:
        raise CommandError("review: a command the AI wants to run is approved from the interactive shell, "
                           "where it runs")
    command = next((a.get("content", "") for a in item.get("artifacts", []) if a.get("name") == "command"), "")
    command = command.strip()
    if not command:
        raise CommandError(f"review: {item['id']} names no command")
    ctx.ui.console.print(Text(f"❯ {command}", style="sc.muted"), markup=False, highlight=False)
    started = time.monotonic()
    output, status, error = "", 0, None
    try:
        result = run_line(command, ctx)
        output = to_text(result.output) if result is not None else ""
        status = ctx.last_status
        show(ctx.ui, result.output if result is not None else "", pager=False, title=command)
    except (ParseError, PipelineError, CommandError) as exc:
        error, status = str(exc), getattr(exc, "status", 1) or 1
        ctx.ui.console.print(Text(f"✗ {exc}", style="sc.error"), markup=False, highlight=False)
    except KeyboardInterrupt:
        error, status = "interrupted", 130
    except ShellExit:
        error, status = "the command asked the shell to exit; refused", 1
    result_data = spool.result_of(output, status, error, time.monotonic() - started, os.getcwd())
    decided = spool.decide(item["id"], "approved", notes, result=result_data)
    _audit(decided, "approved", status == 0, time.monotonic() - started)
    lines = output.count("\n") + (1 if output and not output.endswith("\n") else 0)
    return Text.assemble(("✓ ", "sc.success"), (decided["id"], "sc.accent"), (" approved and run: ", "sc.success"),
                         (command, "sc.accent"),
                         (f"  (exit {status}, {lines} line{'s' if lines != 1 else ''}; the AI gets the output)"
                          if error is None else f"  ({error}; the AI is told)", "sc.muted"))


def _audit(item: dict[str, Any], decision: str, ok: bool | None = None, seconds: float | None = None) -> None:
    """Your answer to something an AI asked for, in the audit log (`ai log`)."""
    from shellcraft import ai_audit

    what = next((a.get("content", "") for a in item.get("artifacts", []) if a.get("name") == "command"),
                item.get("title", ""))
    ai_audit.record("review", what, client=str(item.get("created_by", "")), tier="approval", decision=decision,
                    ok=ok, seconds=seconds)


def _size(n: int) -> str:
    return f"{n} B" if n < 1024 else f"{n / 1024:.0f} KB" if n < 1024 * 1024 else f"{n / 1048576:.1f} MB"


@builtin("mark", "Flag piped output, files or a diff for an AI to review",
         "mark [-t TYPE] [-m TITLE] [-n NOTE] [-p low|medium|high] [-f FILE]... [-d A B] [--tee]",
         category="shell", stateful=True, doc=MARK_DOC)
def mark(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    parser = ArgParser("mark")
    parser.add_argument("-t", "--type", default="")
    parser.add_argument("-m", "--title", default="")
    parser.add_argument("-n", "--note", default="")
    parser.add_argument("-p", "--priority", default="medium", choices=spool.PRIORITIES)
    parser.add_argument("-f", "--file", action="append", default=[])
    parser.add_argument("-d", "--diff", nargs=2, metavar=("A", "B"))
    parser.add_argument("--tee", action="store_true")
    opts = parser.parse_args(args)
    artifacts = []
    if stdin:
        artifacts.append(spool.artifact("stdin", stdin, "diff" if opts.type == "diff" else "text"))
    for name in opts.file:
        artifacts.append(spool.artifact(name, read_file("mark", name), "file"))
    if opts.diff:
        a, b = opts.diff
        diff = "".join(difflib.unified_diff(read_file("mark", a).splitlines(keepends=True),
                                            read_file("mark", b).splitlines(keepends=True), a, b))
        artifacts.append(spool.artifact(f"{a} → {b}", diff or "(no differences)\n", "diff"))
    if not artifacts:
        raise CommandError("mark: nothing to mark: pipe something in, or give --file / --diff (man mark)")
    kind = opts.type or ("diff" if opts.diff and len(artifacts) == 1 else "output")
    title = opts.title or f"{kind} in {os.path.basename(os.getcwd()) or os.getcwd()}"
    try:
        item = spool.create("flag", title, opts.note, artifacts, opts.priority, kind)
    except (OSError, spool.SpoolError) as exc:
        raise CommandError(f"mark: {exc}") from None
    size = sum(a["bytes"] for a in artifacts)
    cut = any(a["truncated"] for a in artifacts)
    note = Text.assemble(("✓ marked ", "sc.success"), (item["id"], "sc.accent"),
                         (f" for the AI ({_size(size)}{', kept the last 256 KB' if cut else ''})", "sc.muted"))
    if opts.tee and stdin:
        if ctx.emit is not None:
            ctx.emit(note)  # the note on screen; the input goes on down the pipe
        return stdin
    return note


TELL_DOC = """# tell

Send the AI a message: a question, an instruction, a note about what you are doing. It goes into
the review spool like a `mark` (type `message`), where the AI reads it with `get_flagged_items`
(a monitor agent sees it at once) and marks it done with `resolve_review_item`.

## Usage

```
tell look at the last 50 lines of auth.log, something is off
tell -p high the deploy failed, check the log in the subshell
echo "see the attached" | tell                     # the message from a pipe
grep -c failed auth.log | tell -m "failed count"   # a title for it
```

## Options

| Option | Meaning |
| --- | --- |
| `-m TITLE`, `--title TITLE` | A title (default: the first words of the message). |
| `-p LEVEL`, `--priority LEVEL` | `low`, `medium` (default) or `high`. |
| `-r ID`, `--reply ID` | Not a new message: add this to the thread of an item already in the queue (a flag, a request, a question), so the AI reads it where it asked. |

`review` lists messages with the other items; `review show ID` prints one. A message is stored
as is: write only what an AI may read.
"""


@builtin("tell", "Send a message to the AI (a question, an instruction, a note)",
         "tell [-m TITLE] [-p low|medium|high] [-r ID] MESSAGE... | COMMAND | tell",
         category="shell", stateful=True, doc=TELL_DOC)
def tell(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    parser = ArgParser("tell")
    parser.add_argument("-m", "--title", default="")
    parser.add_argument("-p", "--priority", default="medium", choices=spool.PRIORITIES)
    parser.add_argument("-r", "--reply", default="")
    parser.add_argument("words", nargs="*")
    opts = parser.parse_args(args)
    message = " ".join(opts.words).strip()
    if stdin.strip():
        message = (message + "\n\n" if message else "") + stdin.rstrip("\n")
    if not message:
        raise CommandError("tell: say something: tell MESSAGE, or pipe it in (man tell)")
    if opts.reply:
        try:
            item = spool.reply(spool.find(opts.reply)["id"], message)
        except (OSError, spool.SpoolError) as exc:
            raise CommandError(f"tell: {exc}") from None
        return Text.assemble(("✓ replied on ", "sc.success"), (item["id"], "sc.accent"), (f": {item['title']}", "sc.muted"))
    title = opts.title or " ".join(message.split())[:60]
    try:
        item = spool.create("flag", title, message, [], opts.priority, "message")
    except (OSError, spool.SpoolError) as exc:
        raise CommandError(f"tell: {exc}") from None
    return Text.assemble(("✓ told the AI ", "sc.success"), (item["id"], "sc.accent"),
                         (f": {title}", "sc.muted"))


def _age(item: dict[str, Any]) -> str:
    seconds = max(0, int(spool.time.time() - spool._timestamp(item.get("updated") or item.get("created"))))
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


def _line(item: dict[str, Any]) -> Text:
    kind = ("question" if item["kind"] == "question" else "message" if item.get("type") == "message"
            else "→ AI" if item["kind"] == "flag"
            else "AI runs" if item.get("type") == "command" else "AI grant" if item.get("type") == "grant"
            else "AI asks")
    style = {"high": "sc.error", "medium": "sc.warning", "low": "sc.muted"}.get(item.get("priority"), "")
    return Text.assemble((f"{item['id']}  ", "sc.accent"), (f"{kind:<8}", "sc.muted"),
                         (f"{item.get('priority', ''):<7}", style), (f"{item.get('status', ''):<9}", ""),
                         (item.get("title", ""), ""), (f"  {_age(item)} · {item.get('created_by', '')}", "sc.muted"))


def show_text(item: dict[str, Any]) -> str:
    """One item in full, as text."""
    lines = [f"{item['id']}  {item['kind']} · {item['status']} · priority {item.get('priority')}"
             + (f" · {item['type']}" if item.get("type") else ""),
             f"title: {item.get('title', '')}",
             f"from: {item.get('created_by', '')} at {item.get('created', '')} in {item.get('cwd', '')}"]
    if item.get("description"):
        lines += ["", item["description"]]
    if item.get("choices"):
        lines += ["", *[f"  {n}. {c}" for n, c in enumerate(item["choices"], 1)]]
    if item.get("answer"):
        a = item["answer"]
        lines.append(f"answer: {a.get('text', '')}" + (f" (choice {a['choice']})" if a.get("choice") else "")
                     + f" by {a.get('by', '')} at {a.get('at', '')}")
    for key in ("decision", "resolution"):
        if item.get(key):
            d = item[key]
            lines.append(f"{key}: {d.get('status', 'resolved')} by {d.get('by', '')} at {d.get('at', '')}"
                         + (f": {d['notes']}" if d.get("notes") else ""))
    for a in item.get("artifacts", []):
        cut = f", the last {_size(spool.MAX_ARTIFACT)} of {_size(a.get('bytes', 0))}" if a.get("truncated") else ""
        lines += ["", f"── {a.get('name')} ({a.get('kind')}{cut}) " + "─" * 20, a.get("content", "").rstrip("\n")]
    if item.get("thread"):
        lines += ["", "── thread " + "─" * 20]
        lines += [f"{m.get('at', '')[11:19]}  {m.get('by', '')}: {m.get('text', '')}" for m in item["thread"]]
    r = item.get("result")
    if r:
        head = f"── result (exit {r.get('status')}, {r.get('seconds')} s" + (f", {r['error']}" if r.get("error") else "")
        head += f", the last {_size(spool.MAX_RESULT)} of {_size(r.get('bytes', 0))}" if r.get("truncated") else ""
        lines += ["", head + ") " + "─" * 20, (r.get("output") or "(no output)").rstrip("\n")]
    return "\n".join(lines) + "\n"


@builtin("review", "The review queue with an AI: what you marked, what it asks you to approve",
         "review [--all] | show ID | approve ID [NOTES...] | reject ID [NOTES...] | answer ID TEXT|N",
         category="shell", stateful=True, doc=REVIEW_DOC)
def review(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    try:
        if not args or args == ["--all"]:
            items = spool.list_items(None if args else "pending")
            if not items:
                return Text("nothing to review" + ("" if args else " (review --all shows the done ones)"),
                            style="sc.muted")
            out = Text()
            for item in items:
                out.append_text(_line(item))
                out.append("\n")
            plain = "".join(f"{i['id']}\t{i['kind']}\t{i.get('priority')}\t{i['status']}\t{i.get('title', '')}\n"
                            for i in items)
            return Styled(out, plain)
        action, rest = args[0], args[1:]
        if action == "show" and len(rest) == 1:
            text = show_text(spool.find(rest[0]))
            if ctx.ui is not None and ctx.interactive and text.count("\n") > 40:
                return Paged(text, page=True)
            return text
        if action == "answer" and len(rest) >= 2:
            item = spool.find(rest[0])
            decided = spool.answer(item["id"], " ".join(rest[1:]))
            _audit(decided, "answered")
            return Text.assemble(("✓ ", "sc.success"), (decided["id"], "sc.accent"),
                                 (f" answered: {decided['answer']['text']}", "sc.success"))
        if action in ("approve", "reject") and rest:
            item = spool.find(rest[0])
            notes = " ".join(rest[1:])
            if action == "approve" and item.get("type") == "command" and item.get("status") == "pending":
                return _approve_and_run(ctx, item, notes)
            decided = spool.decide(item["id"], "approved" if action == "approve" else "rejected", notes)
            _audit(decided, decided["status"])
            if action == "approve" and decided.get("type") == "grant":
                ctx.ai_run = True
                return Text.assemble(("✓ ", "sc.success"), (decided["id"], "sc.accent"),
                                     (" approved: the AI may run commands here for this session", "sc.success"),
                                     ("  (settings ai_access off revokes it; ✦ run shows in the prompt)", "sc.muted"))
            return Text.assemble(("✓ ", "sc.success"), (decided["id"], "sc.accent"),
                                 (f" {decided['status']}: {decided.get('title', '')}", "sc.success"))
    except spool.SpoolError as exc:
        raise CommandError(f"review: {exc}") from None
    raise CommandError("usage: review [--all] | show ID | approve ID [NOTES...] | reject ID [NOTES...] "
                       "| answer ID TEXT|N")
