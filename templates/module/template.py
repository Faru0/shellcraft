"""template — count the most frequent words in text (a reference ShellCraft module).

Copy this file to modules/<yourname>.py (plus the matching .md and .skill), rename every
"template" to your module's name, and replace the logic. Every convention below is checked
by `python tools/modtest.py modules/<yourname>.py`.
"""

# RULE: the file name IS the command name. modules/wordfreq.py is run as `wordfreq`.
#       Use letters, digits, "_" or "-", start with a letter, and don't reuse a builtin
#       name (ls, cat, grep, …): builtins win, so your module would never run.

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

# RULE: parse arguments with ArgParser, and report problems by raising ModuleError.
#       ArgParser is argparse, except it raises ModuleError instead of printing to the
#       terminal and calling sys.exit(). sys.exit() would kill the whole shell or MCP server.
from core.modkit import ArgParser, ModuleError

# OPTIONAL: one line shown by `help`, `modules` and Tab completion
# (when the .skill file has no summary).
SUMMARY = "Count the most frequent words in text"

# OPTIONAL: text next to the spinner while run() takes longer than ~150 ms.
# Worth setting for anything that touches the network or big files.
SPINNER_TEXT = "counting words…"

_WORD = re.compile(r"[\w']+")


# RULE: the entry point is exactly run(args, stdin) -> str.
#   args  — list of CLI-style strings, already split and unquoted by the shell:
#           `template -n 3 "my file.txt"` arrives as ["-n", "3", "my file.txt"].
#   stdin — the previous pipeline stage's output ("" when there is none).
#   return — your output text. It becomes the next stage's stdin, a file's contents
#           (after > or >>), or the screen output. MCP clients receive it verbatim.
def run(args: list[str], stdin: str) -> str:
    parser = ArgParser("template")  # use your module name: it prefixes error messages
    parser.add_argument("files", nargs="*", metavar="FILE")
    parser.add_argument("-n", "--top", type=int, default=10, metavar="N")
    parser.add_argument("-i", "--ignore-case", action="store_true")
    parser.add_argument("--min-length", type=int, default=1, metavar="N")
    opts = parser.parse_args(args)

    # RULE: validate input yourself and fail with a clear, one-line ModuleError.
    #       The shell shows it in an error panel; MCP clients get it as a tool error.
    if opts.top < 1:
        raise ModuleError("template: -n must be at least 1")
    if opts.min_length < 1:
        raise ModuleError("template: --min-length must be at least 1")

    text = _read_input(opts.files, stdin)
    words = _WORD.findall(text.lower() if opts.ignore_case else text)
    counts = Counter(w for w in words if len(w) >= opts.min_length)

    # RULE: return plain text, one record per line, ending with "\n". That's what lets
    #       `template | head -3`, `template > out.txt` and `template | grep x` work.
    #       Never print(): output must be *returned*. Printing corrupts the MCP stdio
    #       transport and bypasses pipes and redirects.
    #       Break ties deterministically (here: alphabetically) so the output is stable.
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[: opts.top]
    return "".join(f"{word} {count}\n" for word, count in ranked)


def _read_input(files: list[str], stdin: str) -> str:
    # RULE: accept FILE arguments *and* stdin, like Unix tools. No FILE (or "-") = stdin.
    #       Relative paths resolve against the shell's current directory (`cd` changes it).
    if not files:
        return stdin
    parts = []
    for name in files:
        if name == "-":
            parts.append(stdin)
            continue
        try:
            parts.append(Path(name).expanduser().read_text(encoding="utf-8", errors="replace"))
        except FileNotFoundError:
            raise ModuleError(f"template: {name}: no such file") from None
        except IsADirectoryError:
            raise ModuleError(f"template: {name}: is a directory") from None
        except PermissionError:
            raise ModuleError(f"template: {name}: permission denied") from None
    return "\n".join(parts)
