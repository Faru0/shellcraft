"""filter — keep (or drop) lines of the input stream that match a pattern."""

from __future__ import annotations

import re

from core.modkit import ArgParser, ModuleError

SUMMARY = "Keep lines matching a regex (grep-style)"


def run(args: list[str], stdin: str) -> str:
    parser = ArgParser("filter")
    parser.add_argument("pattern")
    parser.add_argument("-i", "--ignore-case", action="store_true")
    parser.add_argument("-v", "--invert", action="store_true")
    parser.add_argument("-c", "--count", action="store_true")
    parser.add_argument("-n", "--line-number", action="store_true")
    parser.add_argument("-F", "--fixed", action="store_true")
    parser.add_argument("-m", "--max", type=int, metavar="N")
    opts = parser.parse_args(args)

    source = re.escape(opts.pattern) if opts.fixed else opts.pattern
    try:
        regex = re.compile(source, re.IGNORECASE if opts.ignore_case else 0)
    except re.error as exc:
        raise ModuleError(f"filter: invalid pattern {opts.pattern!r}: {exc}") from None

    matches: list[str] = []
    for number, line in enumerate(stdin.splitlines(), start=1):
        if bool(regex.search(line)) == opts.invert:
            continue
        matches.append(f"{number}:{line}" if opts.line_number else line)
        if opts.max is not None and len(matches) >= opts.max:
            break

    if opts.count:
        return f"{len(matches)}\n"
    return "".join(f"{m}\n" for m in matches)
