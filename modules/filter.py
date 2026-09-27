"""filter — keep (or drop) lines of the input stream that match a pattern."""

from __future__ import annotations

from core.commands.text import compile_pattern, match_lines
from core.modkit import ArgParser

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

    regex = compile_pattern("filter", opts.pattern, opts.ignore_case, opts.fixed)
    matches = match_lines(stdin.splitlines(), regex, opts.invert, opts.max)
    if opts.count:
        return f"{len(matches)}\n"
    return "".join(f"{n}:{line}\n" if opts.line_number else f"{line}\n" for n, line in matches)
