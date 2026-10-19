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
```

An ID can be shortened to its last characters when only one item matches (`review show a1b2c3`),
and Tab completes them. Only requests from the AI can be approved or rejected, and only while
pending.
"""


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


def _age(item: dict[str, Any]) -> str:
    seconds = max(0, int(spool.time.time() - spool._timestamp(item.get("updated") or item.get("created"))))
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


def _line(item: dict[str, Any]) -> Text:
    kind = "→ AI" if item["kind"] == "flag" else "AI asks"
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
    for key in ("decision", "resolution"):
        if item.get(key):
            d = item[key]
            lines.append(f"{key}: {d.get('status', 'resolved')} by {d.get('by', '')} at {d.get('at', '')}"
                         + (f": {d['notes']}" if d.get("notes") else ""))
    for a in item.get("artifacts", []):
        cut = f", the last {_size(spool.MAX_ARTIFACT)} of {_size(a.get('bytes', 0))}" if a.get("truncated") else ""
        lines += ["", f"── {a.get('name')} ({a.get('kind')}{cut}) " + "─" * 20, a.get("content", "").rstrip("\n")]
    return "\n".join(lines) + "\n"


@builtin("review", "The review queue with an AI: what you marked, what it asks you to approve",
         "review [--all] | show ID | approve ID [NOTES...] | reject ID [NOTES...]",
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
        if action in ("approve", "reject") and rest:
            item = spool.find(rest[0])
            decided = spool.decide(item["id"], "approved" if action == "approve" else "rejected", " ".join(rest[1:]))
            return Text.assemble(("✓ ", "sc.success"), (decided["id"], "sc.accent"),
                                 (f" {decided['status']}: {decided.get('title', '')}", "sc.success"))
    except spool.SpoolError as exc:
        raise CommandError(f"review: {exc}") from None
    raise CommandError("usage: review [--all] | show ID | approve ID [NOTES...] | reject ID [NOTES...]")
