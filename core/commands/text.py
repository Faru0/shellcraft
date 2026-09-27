"""Text-stream commands: echo, cat, grep, head, tail, wc, sort, uniq, tee."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from rich.text import Text

from core.builtins import builtin
from core.commands._io import expand_count, joined, lines_out, sources
from core.context import Styled
from core.modkit import ArgParser, ModuleError

if TYPE_CHECKING:
    from core.context import ShellContext


def compile_pattern(prog: str, pattern: str, ignore_case: bool = False, fixed: bool = False) -> re.Pattern:
    try:
        return re.compile(re.escape(pattern) if fixed else pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as exc:
        raise ModuleError(f"{prog}: invalid pattern {pattern!r}: {exc}") from None


def match_lines(lines: Iterable[str], regex: re.Pattern, invert: bool = False,
                limit: int | None = None) -> list[tuple[int, str]]:
    """[(1-based line number, line)] for lines matching `regex` (or not, with invert)."""
    found: list[tuple[int, str]] = []
    for number, line in enumerate(lines, start=1):
        if bool(regex.search(line)) == invert:
            continue
        found.append((number, line))
        if limit is not None and len(found) >= limit:
            break
    return found


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", "0": "\0", "a": "\a", "b": "\b", "e": "\x1b"}
_ESCAPE = re.compile(r"\\([ntr\\0abe])")


# ── echo ─────────────────────────────────────────────────────────────────────

@builtin("echo", "Print arguments to the output stream", "echo [-n] [-e] [TEXT...]", category="text", doc="""\
# echo

Write the arguments, separated by spaces, to the output stream.

## Options

| Option | Meaning |
| --- | --- |
| `-n` | Do not add the trailing newline. |
| `-e` | Interpret escapes such as `\\n`, `\\t` and `\\\\`. |

## Examples

```
echo hello world
echo -e "a\\tb\\nc" > table.txt
```
""")
def echo(ctx: ShellContext, args: list[str], stdin: str) -> str:
    newline, escapes = True, False
    while args and args[0] in ("-n", "-e", "-ne", "-en"):
        newline = newline and "n" not in args[0]
        escapes = escapes or "e" in args[0]
        args = args[1:]
    out = " ".join(args)
    if escapes:
        out = _ESCAPE.sub(lambda m: _ESCAPES[m.group(1)], out)
    return out + ("\n" if newline else "")


# ── cat ──────────────────────────────────────────────────────────────────────

@builtin("cat", "Concatenate files (or stdin) to the output", "cat [-n] [FILE...]", category="text", doc="""\
# cat

Concatenate files and write them to the output stream. With no FILE, or with `-`, read stdin.

## Options

| Option | Meaning |
| --- | --- |
| `-n` | Number every output line. |

## Examples

```
cat notes.txt
cat header.csv rows.csv > all.csv
cat -n script.py | grep def
```
""")
def cat(ctx: ShellContext, args: list[str], stdin: str) -> str:
    parser = ArgParser("cat")
    parser.add_argument("-n", "--number", action="store_true")
    parser.add_argument("files", nargs="*")
    opts = parser.parse_args(args)
    text = joined("cat", opts.files, stdin)
    if not opts.number:
        return text
    return lines_out(f"{i:6}\t{ln}" for i, ln in enumerate(text.splitlines(), start=1))


# ── grep ─────────────────────────────────────────────────────────────────────

@builtin("grep", "Print lines matching a pattern", "grep [-i -v -c -n -F -l -m N] PATTERN [FILE...]",
         category="text", doc="""\
# grep

Search stdin or FILEs for lines matching a Python regular expression. Matches are highlighted
on screen. Piped or redirected output is plain text.

## Options

| Option | Meaning |
| --- | --- |
| `-i` | Ignore case. |
| `-v` | Select lines that do **not** match. |
| `-c` | Print only a count of matching lines (per file). |
| `-n` | Prefix each line with its line number. |
| `-F` | Treat PATTERN as a literal string. |
| `-l` | Print only the names of files with a match. |
| `-m N` | Stop after N matches (per file). |

With several FILEs, each line is prefixed with `file:`. No match gives empty output, which is not an error.

## Examples

```
grep -i error app.log
cat app.log | grep -v DEBUG | grep -c timeout
grep -l TODO main.py core/shell.py
```
""")
def grep(ctx: ShellContext, args: list[str], stdin: str) -> Styled | str:
    parser = ArgParser("grep")
    parser.add_argument("pattern")
    parser.add_argument("files", nargs="*")
    parser.add_argument("-i", "--ignore-case", action="store_true")
    parser.add_argument("-v", "--invert-match", action="store_true")
    parser.add_argument("-c", "--count", action="store_true")
    parser.add_argument("-n", "--line-number", action="store_true")
    parser.add_argument("-F", "--fixed-strings", action="store_true")
    parser.add_argument("-l", "--files-with-matches", action="store_true")
    parser.add_argument("-m", "--max-count", type=int, metavar="N")
    opts = parser.parse_args(args)

    regex = compile_pattern("grep", opts.pattern, opts.ignore_case, opts.fixed_strings)
    inputs = sources("grep", opts.files, stdin)
    prefix_names = len(inputs) > 1

    plain: list[str] = []
    styled = Text()
    for name, text in inputs:
        found = match_lines(text.splitlines(), regex, opts.invert_match, opts.max_count)
        if opts.files_with_matches:
            if found:
                plain.append(name)
                styled.append(name + "\n", style="sc.path")
            continue
        if opts.count:
            line = f"{name}:{len(found)}" if prefix_names else str(len(found))
            plain.append(line)
            styled.append(line + "\n")
            continue
        for number, line in found:
            head = (f"{name}:" if prefix_names else "") + (f"{number}:" if opts.line_number else "")
            plain.append(head + line)
            styled.append(head, style="sc.muted")
            body = Text(line)
            if not opts.invert_match:
                body.highlight_regex(regex, style="sc.match")
            styled.append_text(body)
            styled.append("\n")
    styled.rstrip()
    return Styled(styled, lines_out(plain))


# ── head / tail ──────────────────────────────────────────────────────────────

def _head_tail(prog: str, args: list[str], stdin: str, take_last: bool) -> str:
    parser = ArgParser(prog)
    parser.add_argument("-n", "--lines", type=int, default=10, metavar="N")
    parser.add_argument("files", nargs="*")
    opts = parser.parse_args(expand_count(args))
    if opts.lines < 0:
        raise ModuleError(f"{prog}: -n must be >= 0")
    inputs = sources(prog, opts.files, stdin)
    out: list[str] = []
    for i, (name, text) in enumerate(inputs):
        lines = text.splitlines()
        chunk = (lines[-opts.lines:] if opts.lines else []) if take_last else lines[: opts.lines]
        if len(inputs) > 1:
            out += ([""] if i else []) + [f"==> {name} <=="]
        out += chunk
    return lines_out(out)


@builtin("head", "Output the first lines of input", "head [-n N | -N] [FILE...]", category="text", doc="""\
# head

Print the first 10 lines (or `-n N` lines) of stdin or each FILE. `-N` is shorthand for `-n N`.

## Examples

```
head -n 5 data.csv
cat app.log | head -3
```
""")
def head(ctx: ShellContext, args: list[str], stdin: str) -> str:
    return _head_tail("head", args, stdin, take_last=False)


@builtin("tail", "Output the last lines of input", "tail [-n N | -N] [FILE...]", category="text", doc="""\
# tail

Print the last 10 lines (or `-n N` lines) of stdin or each FILE. `-N` is shorthand for `-n N`.

## Examples

```
tail -n 20 app.log
ls | sort | tail -1
```
""")
def tail(ctx: ShellContext, args: list[str], stdin: str) -> str:
    return _head_tail("tail", args, stdin, take_last=True)


# ── wc ───────────────────────────────────────────────────────────────────────

@builtin("wc", "Count lines, words and characters", "wc [-l] [-w] [-c] [FILE...]", category="text", doc="""\
# wc

Count lines, words and characters in stdin or each FILE. With no options, it prints all three.
With several FILEs, it also prints a `total` line.

| Option | Meaning |
| --- | --- |
| `-l` | Lines only. |
| `-w` | Words only. |
| `-c` | Characters only. |

## Examples

```
wc -l app.log
ls | wc -l
```
""")
def wc(ctx: ShellContext, args: list[str], stdin: str) -> str:
    parser = ArgParser("wc")
    parser.add_argument("-l", "--lines", action="store_true")
    parser.add_argument("-w", "--words", action="store_true")
    parser.add_argument("-c", "--chars", action="store_true")
    parser.add_argument("files", nargs="*")
    opts = parser.parse_args(args)
    show = [opts.lines, opts.words, opts.chars]
    if not any(show):
        show = [True, True, True]

    rows: list[tuple[list[int], str]] = []
    for name, text in sources("wc", opts.files, stdin):
        counts = [text.count("\n") + (1 if text and not text.endswith("\n") else 0), len(text.split()), len(text)]
        rows.append(([c for c, s in zip(counts, show) if s], "" if name == "-" else name))
    if len(rows) > 1:
        rows.append(([sum(col) for col in zip(*(r[0] for r in rows))], "total"))
    if len(rows) == 1 and sum(show) == 1 and not rows[0][1]:
        return f"{rows[0][0][0]}\n"
    width = max(len(str(c)) for counts, _ in rows for c in counts)
    return lines_out(" ".join(str(c).rjust(width) for c in counts) + (f" {name}" if name else "")
                     for counts, name in rows)


# ── sort ─────────────────────────────────────────────────────────────────────

@builtin("sort", "Sort lines of text", "sort [-r] [-n] [-u] [-f] [-k N] [-t SEP] [FILE...]", category="text",
         doc="""\
# sort

Sort the lines of stdin or FILEs.

| Option | Meaning |
| --- | --- |
| `-r` | Reverse the order. |
| `-n` | Numeric sort (lines that aren't numbers sort first). |
| `-u` | Output each distinct line once. |
| `-f` | Ignore case. |
| `-k N` | Sort by field N (1-based). Fields are split on whitespace, or on `-t`. |
| `-t SEP` | Field separator for `-k`. |

## Examples

```
cat names.txt | sort -u
cat sizes.txt | sort -n -r | head -5
cat data.csv | sort -t , -k 3
```
""")
def sort(ctx: ShellContext, args: list[str], stdin: str) -> str:
    parser = ArgParser("sort")
    parser.add_argument("-r", "--reverse", action="store_true")
    parser.add_argument("-n", "--numeric-sort", action="store_true")
    parser.add_argument("-u", "--unique", action="store_true")
    parser.add_argument("-f", "--ignore-case", action="store_true")
    parser.add_argument("-k", "--key", type=int, metavar="N")
    parser.add_argument("-t", "--field-separator", metavar="SEP")
    parser.add_argument("files", nargs="*")
    opts = parser.parse_args(args)
    if opts.key is not None and opts.key < 1:
        raise ModuleError("sort: -k must be >= 1")

    def field(line: str) -> str:
        if opts.key is None:
            return line
        parts = line.split(opts.field_separator) if opts.field_separator else line.split()
        return parts[opts.key - 1] if len(parts) >= opts.key else ""

    def key(line: str):
        value = field(line)
        if opts.numeric_sort:
            try:
                return (1, float(value.strip()), line)
            except ValueError:
                return (0, 0.0, line)
        return (value.lower(), line) if opts.ignore_case else (value, line)

    lines = joined("sort", opts.files, stdin).splitlines()
    if opts.unique:
        seen: set[str] = set()
        norm = (lambda s: s.lower()) if opts.ignore_case else (lambda s: s)
        lines = [ln for ln in lines if not (norm(ln) in seen or seen.add(norm(ln)))]
    return lines_out(sorted(lines, key=key, reverse=opts.reverse))


# ── uniq ─────────────────────────────────────────────────────────────────────

@builtin("uniq", "Collapse adjacent duplicate lines", "uniq [-c] [-d] [-u] [-i] [FILE]", category="text", doc="""\
# uniq

Collapse *adjacent* identical lines into one. Use `sort` first to group all duplicates.

| Option | Meaning |
| --- | --- |
| `-c` | Prefix each line with its count. |
| `-d` | Only print duplicated lines. |
| `-u` | Only print lines that are not repeated. |
| `-i` | Ignore case when comparing. |

## Examples

```
cat words.txt | sort | uniq -c | sort -n -r | head
```
""")
def uniq(ctx: ShellContext, args: list[str], stdin: str) -> str:
    parser = ArgParser("uniq")
    parser.add_argument("-c", "--count", action="store_true")
    parser.add_argument("-d", "--repeated", action="store_true")
    parser.add_argument("-u", "--unique", action="store_true")
    parser.add_argument("-i", "--ignore-case", action="store_true")
    parser.add_argument("files", nargs="*")
    opts = parser.parse_args(args)

    groups: list[list] = []  # [line, count]
    for line in joined("uniq", opts.files, stdin).splitlines():
        same = groups and (groups[-1][0].lower() == line.lower() if opts.ignore_case else groups[-1][0] == line)
        if same:
            groups[-1][1] += 1
        else:
            groups.append([line, 1])
    if opts.repeated:
        groups = [g for g in groups if g[1] > 1]
    if opts.unique:
        groups = [g for g in groups if g[1] == 1]
    return lines_out(f"{count:7} {line}" if opts.count else line for line, count in groups)


# ── tee ──────────────────────────────────────────────────────────────────────

@builtin("tee", "Copy stdin to files and to the output", "tee [-a] FILE...", category="text", writes=True, doc="""\
# tee

Write stdin to each FILE *and* pass it on unchanged, so you can save an intermediate result in
the middle of a pipeline.

| Option | Meaning |
| --- | --- |
| `-a` | Append to the files instead of overwriting. |

## Examples

```
cat app.log | grep ERROR | tee errors.txt | wc -l
echo checkpoint | tee -a run.log
```
""")
def tee(ctx: ShellContext, args: list[str], stdin: str) -> str:
    parser = ArgParser("tee")
    parser.add_argument("-a", "--append", action="store_true")
    parser.add_argument("files", nargs="+")
    opts = parser.parse_args(args)
    for name in opts.files:
        path = Path(name).expanduser()
        try:
            with open(path, "a" if opts.append else "w", encoding="utf-8", newline="") as fh:
                fh.write(stdin)
        except OSError as exc:
            raise ModuleError(f"tee: {name}: {exc.strerror or exc}") from None
    return stdin
