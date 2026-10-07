"""Turn an analyzed script into a module with the fewest possible changes.

The edit works on source lines, not on a re-printed AST, so comments, blank lines, quoting and
formatting survive. What changes:

- one import line:            from core.modkit import script
- `if __name__ == "__main__":` → @script("<name>") + def run(args: list[str], stdin: str) -> str:
  (its body keeps its indentation; without that block, the def goes where the last top-level
  code was)
- the other top-level code moves into run(), indented one level
- the small `args` / `stdin` edits from the analysis (parse_args() → parse_args(args), …)
"""

from __future__ import annotations

import ast
import difflib
import io
import re
import tokenize
from collections import Counter
from dataclasses import dataclass, field

from tools.ingest.analyze import GUARD, KEEP, Analysis


class ConvertError(Exception):
    """The script can't be rewritten automatically (reported like a conflict)."""


@dataclass
class Conversion:
    source: str  # the new module source
    steps: list[str] = field(default_factory=list)  # numbered, human instructions (manual mode)

    def diff(self, original: str, name: str) -> str:
        return "".join(difflib.unified_diff(
            original.splitlines(keepends=True), _ending(self.source).splitlines(keepends=True),
            f"a/{name}.py", f"b/{name}.py"))


def _ending(text: str) -> str:
    return text if text.endswith("\n") else text + "\n"


def indent_unit(source: str) -> str:
    """The file's own indentation: a tab, or the smallest run of leading spaces (default 4)."""
    widths = []
    for line in source.splitlines():
        stripped = line.lstrip(" \t")
        if not stripped or stripped.startswith("#") or stripped == line:
            continue
        lead = line[:len(line) - len(stripped)]
        if "\t" in lead:
            return "\t"
        widths.append(len(lead))
    return " " * (min(widths) if widths else 4)


def string_lines(source: str) -> set[int]:
    """Line numbers that start inside a multi-line string: indenting them would change the string."""
    inside: set[int] = set()
    starts: list[tuple[int, int]] = []
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    fstring_start = getattr(tokenize, "FSTRING_START", None)
    fstring_end = getattr(tokenize, "FSTRING_END", None)
    for tok in tokens:
        if tok.type == tokenize.STRING and tok.end[0] > tok.start[0]:
            inside.update(range(tok.start[0] + 1, tok.end[0] + 1))
        elif fstring_start is not None and tok.type == fstring_start:
            starts.append(tok.start)
        elif fstring_end is not None and tok.type == fstring_end and starts:
            begin = starts.pop()
            if tok.end[0] > begin[0]:
                inside.update(range(begin[0] + 1, tok.end[0] + 1))
    return inside


def _first_line(node: ast.stmt) -> int:
    decorators = getattr(node, "decorator_list", None) or []
    return min([node.lineno] + [d.lineno for d in decorators])


def convert(analysis: Analysis, name: str) -> Conversion:
    if analysis.conflicts:
        raise ConvertError("the script has conflicts; fix them by hand first")
    if analysis.style == "module":
        return Conversion(analysis.source, ["Nothing to change: the script already has run(args, stdin)."])

    lines = analysis.source.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    for edit in sorted(analysis.edits, key=lambda e: (e.line, -e.col)):
        line = lines[edit.line - 1]
        lines[edit.line - 1] = line[:edit.col] + edit.new + line[edit.end_col:]
    in_string = string_lines(analysis.source)
    plan = analysis.plan

    # Two statements on one line (`a = 1; main()`) can't be split between module level and run().
    by_line: dict[int, set[str]] = {}
    for node, what in plan:
        for ln in range(_first_line(node), node.end_lineno + 1):
            by_line.setdefault(ln, set()).add("keep" if what == KEEP else "move")
    if bad := sorted(ln for ln, kinds in by_line.items() if len(kinds) > 1):
        raise ConvertError(f"line {bad[0]} mixes code that stays with code that moves into run() "
                           "(statements joined by ';'); put them on separate lines")

    # Each statement owns the comment/blank lines just above it (but not the file's header).
    segments: list[tuple[int, int, int]] = []  # (lead_start, start, end), 1-based inclusive
    prev_end = 0
    for i, (node, _) in enumerate(plan):
        start = _first_line(node)
        lead = start if i == 0 else prev_end + 1
        segments.append((lead, start, node.end_lineno))
        prev_end = node.end_lineno
    file_header = lines[:segments[0][1] - 1] if segments else lines
    tail = lines[prev_end:]

    guard = next((n for n, what in plan if what == GUARD), None)
    unit = indent_unit(analysis.source)
    if guard is not None:
        if guard.test.end_lineno != guard.lineno or guard.body[0].lineno == guard.lineno:
            raise ConvertError(f"line {guard.lineno}: put the `if __name__ == \"__main__\":` test on one line and "
                               "its body on the lines below it")
        first_body = lines[guard.body[0].lineno - 1]
        body_indent = first_body[:len(first_body) - len(first_body.lstrip(" \t"))]
    else:
        body_indent = unit

    decorator = analysis.import_name
    import_line = ("from core.modkit import script\n" if decorator == "script"
                   else "from core.modkit import script as shellcraft_script\n")
    run_header = [f"@{decorator}(\"{name}\")\n", "def run(args: list[str], stdin: str) -> str:\n"]

    # Where the import goes: after the leading block of imports (or the docstring/__future__).
    import_after = -1  # index into plan
    for i, (node, _) in enumerate(plan):
        if isinstance(node, (ast.Import, ast.ImportFrom)) or (i == 0 and isinstance(node, ast.Expr)):
            import_after = i
        else:
            break

    moved = [i for i, (_, what) in enumerate(plan) if what != KEEP]
    run_at = moved[-1]

    def indent(ln: int) -> str:
        text = lines[ln - 1]
        if ln in in_string or not text.strip():
            return text
        return body_indent + text

    out: list[str] = list(file_header)
    if import_after < 0:
        out.append(import_line)
    run_block: list[str] = []
    for i, (node, what) in enumerate(plan):
        lead, start, end = segments[i]
        if what == KEEP:
            out.extend(lines[lead - 1:end])
            if i == import_after:
                out.append(import_line)
            continue
        if not run_block:  # the first moved statement: its leading comments go above the def
            run_block.extend(lines[lead - 1:start - 1])
            if run_block and run_block[-1].strip() or not run_block and out and out[-1].strip():
                run_block.append("\n")
            run_block.extend(run_header)
        else:
            run_block.extend(indent(ln) for ln in range(lead, start))
        if what == GUARD:
            run_block.extend(lines[guard.lineno:end])  # the body, as it is (header line dropped)
        else:
            run_block.extend(indent(ln) for ln in range(start, end + 1))
        if i == run_at:
            out.extend(run_block)
    out.extend(tail)
    new_source = "".join(out)

    try:
        ast.parse(new_source)
    except SyntaxError as exc:
        raise ConvertError(f"the rewritten code doesn't parse ({exc.msg}, line {exc.lineno}); "
                           "convert this script by hand") from None
    return Conversion(new_source, _steps(analysis, name, plan, segments, guard, import_after, body_indent,
                                         import_line, run_header, run_at))


def _steps(analysis: Analysis, name: str, plan, segments, guard, import_after, body_indent, import_line,
           run_header, run_at) -> list[str]:
    """The same changes, written as instructions for doing them by hand."""
    steps = []
    if import_after >= 0:
        steps.append(f"After line {plan[import_after][0].end_lineno}, add:\n    {import_line.rstrip()}")
    else:
        steps.append(f"At the top of the file (after any comments), add:\n    {import_line.rstrip()}")
    header = "\n    ".join(h.rstrip() for h in run_header)
    if guard is not None:
        steps.append(f"Replace line {guard.lineno}  `{analysis.source.splitlines()[guard.lineno - 1].strip()}`  "
                     f"with:\n    {header}\n  Its body ({_span(guard.body[0].lineno, guard.end_lineno)}) stays as it is.")
    width = "a tab" if body_indent == "\t" else f"{len(body_indent)} spaces"
    first = next(i for i, (_, what) in enumerate(plan) if what != KEEP)
    moved = [(segments[i][1] if i == first else segments[i][0], segments[i][2])
             for i, (_, what) in enumerate(plan) if what not in (KEEP, GUARD)]
    ranges = [_span(a, b) for a, b in _merge(moved)]
    if guard is not None and ranges:
        steps.append(f"Move {', '.join(ranges)} into run(), keeping their order, indented by {width} "
                     "like the rest of the body.")
    elif guard is None and len(ranges) == 1:
        steps.append(f"Put this above line {segments[first][1]}:\n    {header}\n"
                     f"  and indent {ranges[0]} by {width}, so they become its body.")
    elif guard is None:
        steps.append(f"Put this above line {plan[run_at][0].lineno}:\n    {header}\n"
                     f"  then move {', '.join(ranges[:-1])} down below it (keeping their order) and indent "
                     f"all of them, {ranges[-1]} included, by {width}.")
    src_lines = analysis.source.splitlines()
    by_line: dict[int, list] = {}
    for e in analysis.edits:
        by_line.setdefault(e.line, []).append(e)
    for line, edits in sorted(by_line.items()):
        new = src_lines[line - 1]
        for e in sorted(edits, key=lambda e: -e.col):
            new = new[:e.col] + e.new + new[e.end_col:]
        why = "; ".join(e.why for e in edits)
        steps.append(f"Line {line}: change\n    {src_lines[line - 1].strip()}\n  to\n    {new.strip()}\n  ({why})")
    return steps


def _span(a: int, b: int) -> str:
    return f"line {a}" if a == b else f"lines {a}-{b}"


def _merge(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[list[int]] = []
    for start, end in ranges:
        if merged and start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(a, b) for a, b in merged]


# ── the minimal-change guard ────────────────────────────────────────────────

_SPACE = re.compile(r"\s+")


def _norm(source: str) -> Counter:
    return Counter(n for n in (_SPACE.sub("", ln) for ln in source.splitlines()) if n)


def extra_changes(original: str, candidate: str, expected: str) -> list[str]:
    """Lines `candidate` adds or removes beyond what `expected` (the local conversion) does.

    Whitespace and blank lines are ignored, so indentation and spacing don't count; anything else
    — a renamed variable, a "fixed" bug, a reformatted call — is reported."""
    orig, cand, exp = _norm(original), _norm(candidate), _norm(expected)
    problems = []
    for line, count in ((cand - orig) - (exp - orig)).items():
        problems.append(f"added:   {line}" + (f"  (x{count})" if count > 1 else ""))
    for line, count in ((orig - cand) - (orig - exp)).items():
        problems.append(f"removed: {line}" + (f"  (x{count})" if count > 1 else ""))
    return problems
