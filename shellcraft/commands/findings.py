"""findings: the case file your AI agents (and you) keep (shellcraft.findings)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rich.text import Text

from shellcraft import findings as case
from shellcraft.builtins import builtin
from shellcraft.context import CommandError, Paged, Styled
from shellcraft.modkit import ArgParser

if TYPE_CHECKING:
    from shellcraft.context import ShellContext

FINDINGS_DOC = """# findings

The case file: what your AI agents found, kept once, with the evidence. Two agents that find the
same IP or domain do not make two findings. Adding it again merges: both agents' evidence, the
higher severity and confidence, and a count of how often it was seen.

## Usage

```
findings                          # the current case, most severe first
findings show ID                  # one finding in full, with its evidence
findings add KIND VALUE [-t TITLE] [-s SEVERITY] [-c CONFIDENCE] [-n NOTE]
findings set ID [-t TITLE] [-s SEVERITY] [-c CONFIDENCE] [--status open|confirmed|dismissed] [--tag TAG]
findings rm ID
findings case                     # the cases, the current one marked
findings case NAME                # switch to NAME (made if new)
findings export [--json] [FILE]   # a report in Markdown (or JSON); to the screen without FILE
```

KIND is `ip`, `domain`, `url`, `hash`, `user`, `host`, `file`, `process` or `other`. SEVERITY is
`info`, `low`, `medium`, `high` or `critical`; CONFIDENCE is `low`, `medium` or `high`. An ID may
be the end of the full one. The AI reads and writes the same case with `add_finding`,
`list_findings`, `get_finding` and `update_finding`. A new high or critical finding raises an alert.

The case files are in `~/.shellcraft/findings/CASE/`.
"""

SEVERITY_STYLES = {"info": "sc.muted", "low": "sc.accent", "medium": "sc.warning", "high": "sc.error",
                   "critical": "bold sc.error"}


def _short(item: dict[str, Any]) -> str:
    return item["id"][-6:]


def _line(item: dict[str, Any]) -> Text:
    extra = f"  ×{item['seen']}" if item.get("seen", 1) > 1 else ""
    who = f"  {', '.join(item['agents'])}" if item.get("agents") else ""
    return Text.assemble((f"{_short(item)}  ", "sc.accent"), (f"{item['severity']:<9}", SEVERITY_STYLES[item["severity"]]),
                         (f"{item['kind']:<8}", "sc.muted"), (item["value"], "sc.path"), (f"  {item['title']}"
                          if item["title"] != f"{item['kind']} {item['value']}" else "", ""),
                         (extra, "sc.muted"), (who, "sc.muted"),
                         ("  dismissed" if item.get("status") == "dismissed" else
                          "  ✓" if item.get("status") == "confirmed" else "", "sc.muted"))


def _text(item: dict[str, Any]) -> str:
    lines = [f"{item['id']}  {item['severity']} · confidence {item['confidence']} · {item['status']}",
             f"{item['kind']}: {item['value']}", f"title: {item['title']}",
             f"case: {item['case']} · seen {item.get('seen', 1)} time(s) · {item.get('created', '')}"
             + (f" · by {', '.join(item['agents'])}" if item.get("agents") else "")]
    if item.get("tags"):
        lines.append("tags: " + ", ".join(item["tags"]))
    if item.get("task_id"):
        lines.append(f"task: {item['task_id']}")
    if item.get("summary"):
        lines += ["", item["summary"]]
    for piece in item.get("evidence", []):
        head = f"── {piece.get('by') or 'evidence'} {piece.get('at', '')[11:19]} " + "─" * 16
        lines += ["", head]
        if piece.get("note"):
            lines.append(piece["note"])
        if piece.get("command"):
            lines.append(f"$ {piece['command']}")
        if piece.get("output"):
            lines.append(piece["output"].rstrip("\n"))
    return "\n".join(lines) + "\n"


@builtin("findings", "The case file your AI agents keep: what they found, with the evidence",
         "findings [show ID | add KIND VALUE | set ID | rm ID | case [NAME] | export [--json] [FILE]]",
         category="shell", stateful=True, doc=FINDINGS_DOC)
def findings(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    try:
        if not args:
            rows = case.list_findings()
            name = case.current()
            if not rows:
                return Text(f"case {name}: no findings yet", style="sc.muted")
            out = Text(f"case {name}\n", style="sc.prompt")
            for item in rows:
                out.append_text(_line(item))
                out.append("\n")
            plain = "".join(f"{_short(f)}\t{f['severity']}\t{f['kind']}\t{f['value']}\t{f['title']}\t{f.get('seen', 1)}"
                            f"\t{','.join(f.get('agents', []))}\n" for f in rows)
            return Styled(out, plain)
        action, rest = args[0], args[1:]
        if action == "show" and len(rest) == 1:
            text = _text(case.find(rest[0]))
            return Paged(text, page=True) if ctx.ui is not None and ctx.interactive and text.count("\n") > 40 else text
        if action == "add":
            return _add(rest)
        if action == "set" and rest:
            return _set(rest)
        if action == "rm" and len(rest) == 1:
            item = case.find(rest[0])
            case.remove(item["id"])
            return Text.assemble(("✓ ", "sc.success"), f"removed {item['kind']} {item['value']}")
        if action == "case" and len(rest) <= 1:
            if rest:
                name = case.use(rest[0])
                return Text.assemble(("✓ ", "sc.success"), f"case {name}")
            now = case.current()
            out = Text()
            for name, count in case.cases().items():
                out.append(f"{'●' if name == now else ' '} {name}  ({count})\n", style="sc.accent" if name == now else "")
            return out
        if action == "export":
            return _export(rest)
    except case.FindingError as exc:
        raise CommandError(f"findings: {exc}") from None
    raise CommandError("usage: findings [show ID | add KIND VALUE | set ID | rm ID | case [NAME] | export [FILE]]")


def _add(args: list[str]) -> Any:
    parser = ArgParser("findings add")
    parser.add_argument("-t", "--title", default="")
    parser.add_argument("-s", "--severity", default="info", choices=case.SEVERITIES)
    parser.add_argument("-c", "--confidence", default="medium", choices=case.CONFIDENCES)
    parser.add_argument("-n", "--note", default="")
    parser.add_argument("--tag", action="append", default=[])
    parser.add_argument("kind", choices=case.KINDS)
    parser.add_argument("value", nargs="+")
    opts = parser.parse_args(args)
    item, created = case.add(opts.kind, " ".join(opts.value), opts.title, "", [opts.note] if opts.note else None,
                             opts.confidence, opts.severity, "you", tags=opts.tag)
    return Text.assemble(("✓ ", "sc.success"), (_short(item), "sc.accent"),
                         f" {'added' if created else 'merged into the existing finding'}: {item['kind']} {item['value']}")


def _set(args: list[str]) -> Any:
    parser = ArgParser("findings set")
    parser.add_argument("-t", "--title")
    parser.add_argument("-s", "--severity", choices=case.SEVERITIES)
    parser.add_argument("-c", "--confidence", choices=case.CONFIDENCES)
    parser.add_argument("--status", choices=case.STATUSES)
    parser.add_argument("--tag", action="append", default=[])
    parser.add_argument("id")
    opts = parser.parse_args(args)
    item = case.find(opts.id)
    changed = case.update(item["id"], opts.title, None, opts.severity, opts.confidence, opts.status,
                          opts.tag or None, None, None, "you", item["case"])
    return Text.assemble(("✓ ", "sc.success"), (_short(changed), "sc.accent"), f" updated: {changed['kind']} "
                                                                               f"{changed['value']}")


def _export(args: list[str]) -> Any:
    parser = ArgParser("findings export")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("file", nargs="?")
    opts = parser.parse_args(args)
    text = case.export_json() if opts.json else case.export_markdown()
    if not opts.file:
        return text
    from shellcraft.commands._io import user_path

    path = user_path(opts.file)
    path.write_text(text, encoding="utf-8")
    return Text.assemble(("✓ ", "sc.success"), f"wrote {path}")
