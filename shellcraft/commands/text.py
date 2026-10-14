"""Text-stream commands: echo, cat, grep, head, tail, wc, sort, uniq, cut, tr, tee."""

from __future__ import annotations

import os
import re
import string
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from rich.text import Text

from shellcraft import streams
from shellcraft.builtins import builtin
from shellcraft.commands._io import expand_count, joined, lines_out, linked, links_on, sources, user_path
from shellcraft.context import Styled, WithStatus
from shellcraft.modkit import ArgParser, ModuleError

if TYPE_CHECKING:
    from shellcraft.context import ShellContext


def compile_pattern(prog: str, pattern: str, ignore_case: bool = False, fixed: bool = False) -> re.Pattern:
    try:
        return re.compile(re.escape(pattern) if fixed else pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as exc:
        raise ModuleError(f"{prog}: invalid pattern {pattern!r}: {exc}") from None


def match_lines(lines: Iterable[str], regex: re.Pattern, invert: bool = False,
                limit: int | None = None) -> list[tuple[int, str]]:
    """[(1-based line number, line)] for lines matching `regex` (or not, with invert)."""
    found: list[tuple[int, str]] = []
    if limit is not None and limit <= 0:
        return found  # `grep -m 0` selects nothing, as in GNU grep
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

@builtin("cat", "Concatenate files (or stdin) to the output", "cat [-n] [-s] [-A] [-E] [-T] [FILE...]", category="text",
         streams=True,
         doc="""\
# cat

Concatenate files and write them to the output stream. With no FILE, or with `-`, read stdin.

## Options

| Option | Meaning |
| --- | --- |
| `-n` | Number every output line. |
| `-s` | Squeeze repeated blank lines into one. |
| `-A` | Show every character: `$` at the end of each line, `^I` for a tab, `^M` for a carriage return, other control characters as `^X`. |
| `-E` | Show `$` at the end of each line. |
| `-T` | Show tabs as `^I`. |

## Examples

```
cat notes.txt
cat header.csv rows.csv > all.csv
cat -n script.py | grep def
cat -A data.txt      # find stray tabs and Windows line endings
```
""")
def cat(ctx: ShellContext, args: list[str], stdin: Any) -> Any:
    parser = ArgParser("cat")
    parser.add_argument("-n", "--number", action="store_true")
    parser.add_argument("-s", "--squeeze-blank", action="store_true")
    parser.add_argument("-A", "--show-all", action="store_true")
    parser.add_argument("-E", "--show-ends", action="store_true")
    parser.add_argument("-T", "--show-tabs", action="store_true")
    parser.add_argument("files", nargs="*")
    opts = parser.parse_args(args)
    plain = not (opts.number or opts.squeeze_blank or opts.show_all or opts.show_ends or opts.show_tabs)
    if plain and not opts.files:
        return iter(streams.Lines.of(stdin))  # stdin passes through line by line, as it arrives
    text = joined("cat", opts.files, streams.text(stdin))
    if plain:
        return text
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()
    if opts.squeeze_blank:
        squeezed: list[str] = []
        for line in lines:
            if line == "" and squeezed and squeezed[-1] == "":
                continue
            squeezed.append(line)
        lines = squeezed
    if opts.show_all or opts.show_tabs:
        lines = [ln.replace("\t", "^I") for ln in lines]
    if opts.show_all:
        lines = ["".join(f"^{chr(ord(c) + 64)}" if ord(c) < 32 and c != "\t" else ("^?" if c == "\x7f" else c)
                         for c in ln) for ln in lines]
    if opts.show_all or opts.show_ends:
        lines = [ln + "$" for ln in lines]
    if opts.number:
        lines = [f"{i:6}\t{ln}" for i, ln in enumerate(lines, start=1)]
    return lines_out(lines)


# ── grep ─────────────────────────────────────────────────────────────────────

_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv"}


def _grep_files(names: list[str], recursive: bool) -> list[str]:
    """The files to search: the names as given, or with -r every file under each directory."""
    if not recursive:
        return names
    out: list[str] = []
    for name in names or ["."]:
        path = user_path(name)
        if not path.is_dir():
            out.append(name)
            continue
        for root, dirs, files in os.walk(path):
            dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
            for f in sorted(files):
                out.append(os.path.join(root, f) if name != "." else os.path.relpath(os.path.join(root, f)))
    return out


@builtin("grep", "Print lines matching a pattern",
         "grep [-i -v -c -n -F -l -L -q -w -x -o -r -h -H] [-m N] [-A N] [-B N] [-C N] [-e PAT]... PATTERN [FILE...]",
         category="text", streams=True, doc="""\
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
| `-E`, `-G` | Accepted for compatibility: patterns are always Python regular expressions. |
| `-w` | Match whole words only. |
| `-x` | Match whole lines only. |
| `-o` | Print only the matching part of each line, one per line. |
| `-e PAT` | A pattern; repeat it to search for several (`-e a -e b`). |
| `-l` | Print only the names of files with a match. |
| `-L` | Print only the names of files **without** a match. |
| `-q`, `--quiet` | Print nothing; only the exit status says whether a line matched. |
| `-m N` | Stop after N matches (per file). |
| `-r`, `-R` | Search every file under each directory (`.git`, `__pycache__`, `node_modules` and virtual environments are skipped). |
| `-A N` | Print N lines of context after each match. |
| `-B N` | Print N lines of context before each match. |
| `-C N` | Print N lines of context before and after. |
| `-h` | Never prefix lines with the file name. |
| `-H` | Always prefix lines with the file name. |

With several FILEs (or `-r`), each line is prefixed with `file:`. Context lines are marked with
`-` instead of `:` after the file name or line number, and groups are separated by `--`.

The exit status (`$?`) is 0 when a line was selected and 1 when none was, as in GNU grep. No
match is not an error: there is no error panel, but the prompt shows `[✗ 1]`. That makes grep a
condition: `if grep -q TODO $f { echo $f }`.

## Examples

```
grep -i error app.log
cat app.log | grep -v DEBUG | grep -c timeout
grep -rn TODO shellcraft
grep -C 2 "connection reset" app.log
grep -ow "[a-z]+@[a-z.]+" contacts.txt
for f in *.py { if grep -q TODO $f { echo $f } }
```
""")
def grep(ctx: ShellContext, args: list[str], stdin: Any) -> Any:
    parser = ArgParser("grep")
    parser.add_argument("-e", "--regexp", action="append", default=[], metavar="PAT")
    parser.add_argument("rest", nargs="*")
    parser.add_argument("-i", "--ignore-case", action="store_true")
    parser.add_argument("-v", "--invert-match", action="store_true")
    parser.add_argument("-c", "--count", action="store_true")
    parser.add_argument("-n", "--line-number", action="store_true")
    parser.add_argument("-F", "--fixed-strings", action="store_true")
    parser.add_argument("-E", "--extended-regexp", action="store_true")
    parser.add_argument("-G", "--basic-regexp", action="store_true")
    parser.add_argument("-w", "--word-regexp", action="store_true")
    parser.add_argument("-x", "--line-regexp", action="store_true")
    parser.add_argument("-o", "--only-matching", action="store_true")
    parser.add_argument("-l", "--files-with-matches", action="store_true")
    parser.add_argument("-L", "--files-without-match", action="store_true")
    parser.add_argument("-q", "--quiet", "--silent", action="store_true")
    parser.add_argument("-m", "--max-count", type=int, metavar="N")
    parser.add_argument("-r", "-R", "--recursive", action="store_true")
    parser.add_argument("-A", "--after-context", type=int, metavar="N")
    parser.add_argument("-B", "--before-context", type=int, metavar="N")
    parser.add_argument("-C", "--context", type=int, metavar="N")
    parser.add_argument("-h", "--no-filename", action="store_true")
    parser.add_argument("-H", "--with-filename", action="store_true")
    opts = parser.parse_args(args)

    patterns = list(opts.regexp)
    files = list(opts.rest)
    if not patterns:
        if not files:
            raise ModuleError("grep: missing PATTERN (usage: grep [OPTIONS] PATTERN [FILE...])")
        patterns.append(files.pop(0))
    if opts.context is not None:
        opts.after_context = opts.before_context = opts.context
    context = opts.after_context is not None or opts.before_context is not None
    opts.after_context, opts.before_context = opts.after_context or 0, opts.before_context or 0
    if opts.after_context < 0 or opts.before_context < 0:
        raise ModuleError("grep: context lengths must be >= 0")
    parts = [re.escape(p) if opts.fixed_strings else p for p in patterns]
    if opts.word_regexp:
        parts = [rf"\b(?:{p})\b" for p in parts]
    if opts.line_regexp:
        parts = [rf"^(?:{p})$" for p in parts]
    regex = compile_pattern("grep", "|".join(f"(?:{p})" for p in parts) if len(parts) > 1 else parts[0],
                            opts.ignore_case)

    if not files and not opts.recursive and not (opts.count or opts.files_with_matches or opts.files_without_match
                                                  or context or opts.with_filename):
        return _grep_lines(streams.Lines.of(stdin), regex, opts)  # stdin: matches flow on as they come
    inputs = sources("grep", _grep_files(files, opts.recursive), streams.text(stdin))
    prefix_names = opts.with_filename or (len(inputs) > 1 and not opts.no_filename)

    plain: list[str] = []
    styled = Text()
    selected = False
    link = links_on(ctx)

    def file_link(label: Text) -> Text:  # the file name under the cursor, as a hyperlink on screen
        return linked(label, user_path(name)) if link and name != "-" else label

    for name, text in inputs:
        lines = text.splitlines()
        found = match_lines(lines, regex, opts.invert_match, opts.max_count)
        selected = selected or bool(found)
        if opts.quiet:
            if selected:
                break
            continue
        if opts.files_with_matches or opts.files_without_match:
            if bool(found) == opts.files_with_matches:
                plain.append(name)
                styled.append_text(file_link(Text(name, style="sc.path")))
                styled.append("\n")
            continue
        if opts.count:
            line = f"{name}:{len(found)}" if prefix_names else str(len(found))
            plain.append(line)
            styled.append(line + "\n")
            continue
        if opts.only_matching:
            for number, line in found:
                for match in regex.finditer(line):
                    if not match.group():
                        continue
                    head = (f"{name}:" if prefix_names else "") + (f"{number}:" if opts.line_number else "")
                    plain.append(head + match.group())
                    styled.append(head, style="sc.muted" if not opts.line_number else "sc.accent")
                    styled.append(match.group(), style="sc.match")
                    styled.append("\n")
            continue
        hits = {number for number, _ in found}
        shown: list[int] = []
        if context:
            wanted: set[int] = set()
            for number in hits:
                wanted.update(range(max(1, number - opts.before_context),
                                    min(len(lines), number + opts.after_context) + 1))
            shown = sorted(wanted)
        else:
            shown = sorted(hits)
        previous = None
        for number in shown:
            if context and previous is not None and number != previous + 1:
                plain.append("--")
                styled.append("--\n", style="sc.muted")
            previous = number
            line = lines[number - 1]
            mark = ":" if number in hits else "-"
            head = (f"{name}{mark}" if prefix_names else "") + (f"{number}{mark}" if opts.line_number else "")
            plain.append(head + line)
            if prefix_names:
                styled.append_text(file_link(Text(f"{name}{mark}", style="sc.muted")))
            if opts.line_number:
                styled.append(f"{number}{mark}", style="sc.accent")
            body = Text(line)
            if number in hits and not opts.invert_match:
                body.highlight_regex(regex, style="sc.match")
            styled.append_text(body)
            styled.append("\n")
    styled.rstrip()
    output = Styled(styled, lines_out(plain))
    return output if selected else WithStatus(output, 1)


def _grep_lines(lines: Any, regex: re.Pattern, opts: Any) -> Any:
    """grep over stdin as it arrives: one Styled item per output line; returns the status (0 when
    something matched, 1 when nothing did), as grep does."""
    if opts.max_count is not None and opts.max_count <= 0:
        return 1
    found = 0
    for number, raw in enumerate(lines, start=1):
        line = raw[:-1] if raw.endswith("\n") else raw
        if line.endswith("\r"):
            line = line[:-1]
        if bool(regex.search(line)) == opts.invert_match:
            continue
        found += 1
        if opts.quiet:
            return 0
        head = f"{number}:" if opts.line_number else ""
        if opts.only_matching:
            for match in regex.finditer(line):
                if match.group():
                    text = Text(head, style="sc.accent")
                    text.append(match.group(), style="sc.match")
                    yield Styled(text, head + match.group() + "\n")
        else:
            text = Text(head, style="sc.accent")
            body = Text(line)
            if not opts.invert_match:
                body.highlight_regex(regex, style="sc.match")
            text.append_text(body)
            yield Styled(text, head + line + "\n")
        if opts.max_count is not None and found >= opts.max_count:
            break
    return 0 if found else 1


# ── yes ──────────────────────────────────────────────────────────────────────

@builtin("yes", "Repeat a line until the reader stops", "yes [TEXT...]", category="text", doc="""\
# yes

Print `y` (or TEXT) on a line, again and again, until whatever reads it has had enough: `head`
stops it, as does Ctrl-C at the prompt. Handy to feed a fixed answer, or to test a pipeline.

## Examples

```
yes | head -3
yes hello | head -n 2
```
""")
def yes(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    line = (" ".join(args) if args else "y") + "\n"
    batch = line * 64  # a few lines per item: fast, and still stops at once
    while True:
        streams.check()
        yield batch


# ── head / tail ──────────────────────────────────────────────────────────────

def _head_tail(prog: str, opts, stdin: str, take_last: bool) -> str:
    if opts.lines < 0:
        raise ModuleError(f"{prog}: -n must be >= 0")
    if opts.bytes is not None and opts.bytes < 0:
        raise ModuleError(f"{prog}: -c must be >= 0")
    inputs = sources(prog, opts.files, stdin)
    out: list[str] = []
    for i, (name, text) in enumerate(inputs):
        if len(inputs) > 1:
            out += ([""] if i else []) + [f"==> {name} <=="]
        if opts.bytes is not None:
            data = text.encode("utf-8", errors="replace")
            chunk = (data[-opts.bytes:] if opts.bytes else b"") if take_last else data[:opts.bytes]
            out.append(chunk.decode("utf-8", errors="ignore"))
            continue
        lines = text.splitlines()
        out += (lines[-opts.lines:] if opts.lines else []) if take_last else lines[: opts.lines]
    if opts.bytes is not None and len(inputs) == 1:
        return out[0]
    return lines_out(out)


@builtin("head", "Output the first lines of input", "head [-n N | -N] [-c N] [FILE...]", category="text", streams=True,
         doc="""\
# head

Print the first 10 lines of stdin or of each FILE.

| Option | Meaning |
| --- | --- |
| `-n N`, `--lines N` | Print the first N lines instead (`-N` is shorthand). |
| `-c N`, `--bytes N` | Print the first N bytes instead. |

## Examples

```
head -n 5 data.csv
cat app.log | head -3
head -c 100 big.bin
```
""")
def head(ctx: ShellContext, args: list[str], stdin: Any) -> str:
    parser = ArgParser("head")
    parser.add_argument("-n", "--lines", type=int, default=10, metavar="N")
    parser.add_argument("-c", "--bytes", type=int, metavar="N")
    parser.add_argument("files", nargs="*")
    opts = parser.parse_args(expand_count(args))
    if opts.files or (opts.lines < 0) or (opts.bytes is not None and opts.bytes < 0):
        return _head_tail("head", opts, streams.text(stdin), take_last=False)
    # stdin: read only what is needed, then stop (the rest of the pipeline is told to stop too)
    taken: list[str] = []
    size = 0
    if opts.bytes is not None:
        for line in streams.Lines.of(stdin) if opts.bytes else ():
            taken.append(line)
            size += len(line.encode("utf-8", errors="replace"))
            if size >= opts.bytes:
                break
        data = "".join(taken).encode("utf-8", errors="replace")[:opts.bytes]
        return data.decode("utf-8", errors="ignore")
    for line in streams.Lines.of(stdin) if opts.lines else ():
        taken.append(line[:-1] if line.endswith("\n") else line)
        if len(taken) >= opts.lines:
            break
    return lines_out(taken)


@builtin("tail", "Output the last lines of input", "tail [-n N | -N] [-c N] [-f] [FILE...]", category="text",
         streams=True, doc="""\
# tail

Print the last 10 lines of stdin or of each FILE.

| Option | Meaning |
| --- | --- |
| `-n N`, `--lines N` | Print the last N lines instead (`-N` is shorthand). |
| `-c N`, `--bytes N` | Print the last N bytes instead. |
| `-f`, `--follow` | Keep printing as FILE grows (one file, interactive shell only). Ctrl-C stops it. It can feed a pipeline: `tail -f app.log \\| grep ERROR` prints matches as they are written. |

## Examples

```
tail -n 20 app.log
ls | sort | tail -1
tail -f app.log
tail -f app.log | grep -i error
```
""")
def tail(ctx: ShellContext, args: list[str], stdin: Any) -> Any:
    parser = ArgParser("tail")
    parser.add_argument("-n", "--lines", type=int, default=10, metavar="N")
    parser.add_argument("-c", "--bytes", type=int, metavar="N")
    parser.add_argument("-f", "--follow", action="store_true")
    parser.add_argument("files", nargs="*")
    opts = parser.parse_args(expand_count(args))
    if not opts.follow:
        return _head_tail("tail", opts, streams.text(stdin), take_last=True)
    if len(opts.files) != 1 or opts.files[0] == "-":
        raise ModuleError("tail: -f follows exactly one FILE")
    if not ctx.interactive and ctx.live is None:
        raise ModuleError("tail: -f only works in the interactive shell")
    path = user_path(opts.files[0])
    try:
        position = path.stat().st_size
    except OSError as exc:
        raise ModuleError(f"tail: {opts.files[0]}: {exc.strerror or exc}") from None
    return _follow(opts, path, position)


def _follow(opts: Any, path: Path, position: int) -> Any:
    """`tail -f`: the last lines, then every complete line appended to the file, as it is
    written, until the pipeline stops (Ctrl-C, or the reader finished)."""
    initial = _head_tail("tail", opts, "", take_last=True)
    yield from initial.splitlines(keepends=True)
    pending = b""
    while True:
        streams.wait(0.25)
        try:
            size = path.stat().st_size
        except OSError:
            continue  # rotated away: wait for it to come back
        if size < position:
            position = 0  # truncated
        if size > position:
            with open(path, "rb") as fh:
                fh.seek(position)
                pending += fh.read()
                position = fh.tell()
            *complete, pending = pending.split(b"\n")
            for raw in complete:
                yield raw.decode("utf-8", errors="replace") + "\n"


# ── wc ───────────────────────────────────────────────────────────────────────

@builtin("wc", "Count lines, words, bytes and characters", "wc [-l] [-w] [-c] [-m] [FILE...]", category="text",
         streams=True, doc="""\
# wc

Count lines, words and bytes in stdin or each FILE. With no options, it prints all three.
With several FILEs, it also prints a `total` line.

| Option | Meaning |
| --- | --- |
| `-l` | Lines only. |
| `-w` | Words only. |
| `-c` | Bytes only (UTF-8, as GNU `wc -c`). |
| `-m` | Characters only (`é` is one character but two bytes). |

## Examples

```
wc -l app.log
ls | wc -l
echo é | wc -m
```
""")
def wc(ctx: ShellContext, args: list[str], stdin: Any) -> str:
    parser = ArgParser("wc")
    parser.add_argument("-l", "--lines", action="store_true")
    parser.add_argument("-w", "--words", action="store_true")
    parser.add_argument("-c", "--bytes", action="store_true")
    parser.add_argument("-m", "--chars", action="store_true")
    parser.add_argument("files", nargs="*")
    opts = parser.parse_args(args)
    show = [opts.lines, opts.words, opts.bytes, opts.chars]
    if not any(show):
        show = [True, True, True, False]

    rows: list[tuple[list[int], str]] = []
    if opts.files:
        inputs = [(name, [text]) for name, text in sources("wc", opts.files, streams.text(stdin))]
    else:
        inputs = [("-", streams.Lines.of(stdin))]  # counted as it arrives, never held whole
    for name, pieces in inputs:
        counts = [0, 0, 0, 0]
        last = ""
        for piece in pieces:
            counts[0] += piece.count("\n")
            counts[1] += len(piece.split())
            counts[2] += len(piece.encode("utf-8", errors="replace"))
            counts[3] += len(piece)
            last = piece or last
        if last and not last.endswith("\n"):
            counts[0] += 1
        rows.append(([c for c, s in zip(counts, show) if s], "" if name == "-" else name))
    if len(rows) > 1:
        rows.append(([sum(col) for col in zip(*(r[0] for r in rows))], "total"))
    if len(rows) == 1 and sum(show) == 1 and not rows[0][1]:
        return f"{rows[0][0][0]}\n"
    width = max(len(str(c)) for counts, _ in rows for c in counts)
    return lines_out(" ".join(str(c).rjust(width) for c in counts) + (f" {name}" if name else "")
                     for counts, name in rows)


# ── sort ─────────────────────────────────────────────────────────────────────

_HUMAN = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*([kKmMgGtTpP]?)")
_VERSION_CHUNK = re.compile(r"(\d+)")


def _human_value(text: str) -> float | None:
    match = _HUMAN.match(text)
    if not match:
        return None
    scale = {"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3, "t": 1024 ** 4, "p": 1024 ** 5}
    return float(match.group(1)) * scale[match.group(2).lower()]


def _version_key(text: str) -> tuple:
    return tuple((0, int(part)) if part.isdigit() else (1, part) for part in _VERSION_CHUNK.split(text) if part)


@builtin("sort", "Sort lines of text", "sort [-r] [-n] [-h] [-V] [-u] [-f] [-s] [-k N[,M]] [-t SEP] [FILE...]",
         category="text", doc="""\
# sort

Sort the lines of stdin or FILEs.

| Option | Meaning |
| --- | --- |
| `-r` | Reverse the order. |
| `-n` | Numeric sort (lines that aren't numbers sort first). |
| `-h` | Human numeric sort: `2K` < `1M` < `3G` (as `du -h` and `ls -lh` print). |
| `-V` | Version sort: `v1.2` < `v1.10` < `v2.0`. |
| `-u` | Output each distinct line (or key, with `-k`) once. |
| `-f` | Ignore case. |
| `-s` | Stable: lines with equal keys keep their input order. |
| `-k N[,M]` | Sort by field N, or by fields N through M (1-based). Fields are split on whitespace, or on `-t`. |
| `-t SEP` | Field separator for `-k`. |

## Examples

```
cat names.txt | sort -u
cat sizes.txt | sort -n -r | head -5
du -h -d 1 | sort -h -r
cat data.csv | sort -t , -k 3
ls | sort -V
```
""")
def sort(ctx: ShellContext, args: list[str], stdin: str) -> str:
    parser = ArgParser("sort")
    parser.add_argument("-r", "--reverse", action="store_true")
    parser.add_argument("-n", "--numeric-sort", action="store_true")
    parser.add_argument("-h", "--human-numeric-sort", action="store_true")
    parser.add_argument("-V", "--version-sort", action="store_true")
    parser.add_argument("-u", "--unique", action="store_true")
    parser.add_argument("-f", "--ignore-case", action="store_true")
    parser.add_argument("-s", "--stable", action="store_true")
    parser.add_argument("-k", "--key", metavar="N[,M]")
    parser.add_argument("-t", "--field-separator", metavar="SEP")
    parser.add_argument("files", nargs="*")
    opts = parser.parse_args(args)
    first = last = None
    if opts.key is not None:
        match = re.fullmatch(r"(\d+)(?:,(\d+))?", opts.key)
        if not match or int(match.group(1)) < 1 or (match.group(2) and int(match.group(2)) < int(match.group(1))):
            raise ModuleError("sort: -k must be N or N,M with 1 <= N <= M")
        first = int(match.group(1))
        last = int(match.group(2)) if match.group(2) else first

    def field(line: str) -> str:
        if first is None:
            return line
        parts = line.split(opts.field_separator) if opts.field_separator else line.split()
        return (opts.field_separator or " ").join(parts[first - 1:last])

    def key(line: str):
        value = field(line)
        tail = () if opts.stable else (line,)
        if opts.numeric_sort:
            try:
                return (1, float(value.strip()), *tail)
            except ValueError:
                return (0, 0.0, *tail)
        if opts.human_numeric_sort:
            number = _human_value(value)
            return (0, 0.0, *tail) if number is None else (1, number, *tail)
        if opts.version_sort:
            return (_version_key(value.lower() if opts.ignore_case else value), *tail)
        return (value.lower(), *tail) if opts.ignore_case else (value, *tail)

    lines = sorted(joined("sort", opts.files, stdin).splitlines(), key=key, reverse=opts.reverse)
    if opts.unique:  # one line per distinct key (the whole line without -k), as GNU sort -u
        out: list[str] = []
        previous: Any = object()
        for line in lines:
            k = key(line) if opts.stable else key(line)[:-1]  # drop the whole-line tie-breaker
            if k != previous:
                out.append(line)
                previous = k
        lines = out
    return lines_out(lines)


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

@builtin("tee", "Copy stdin to files and to the output", "tee [-a] FILE...", category="text", writes=True,
         streams=True, doc="""\
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
def tee(ctx: ShellContext, args: list[str], stdin: Any) -> Any:
    parser = ArgParser("tee")
    parser.add_argument("-a", "--append", action="store_true")
    parser.add_argument("files", nargs="+")
    opts = parser.parse_args(args)
    handles = []
    try:
        for name in opts.files:
            handles.append(open(user_path(name), "a" if opts.append else "w", encoding="utf-8", newline=""))  # noqa: SIM115
    except OSError as exc:
        for fh in handles:
            fh.close()
        raise ModuleError(f"tee: {name}: {exc.strerror or exc}") from None
    return _tee_lines(streams.Lines.of(stdin), handles)


def _tee_lines(lines: Any, handles: list[Any]) -> Any:
    """Each line to every file (flushed, so the file grows as the pipeline runs), then onwards."""
    try:
        for line in lines:
            for fh in handles:
                fh.write(line)
                fh.flush()
            yield line
    finally:
        for fh in handles:
            fh.close()


# ── cut ──────────────────────────────────────────────────────────────────────

def _parse_list(prog: str, spec: str) -> list[tuple[int, int | None]]:
    """'1,3-5,7-' -> [(1, 1), (3, 5), (7, None)] (1-based, inclusive; None = to the end)."""
    ranges = []
    for part in spec.split(","):
        m = re.fullmatch(r"(\d*)-(\d*)|(\d+)", part.strip())
        if not m or part.strip() == "-":
            raise ModuleError(f"{prog}: invalid list '{spec}' (use e.g. 1,3-5,7-)")
        if m.group(3):
            lo = hi = int(m.group(3))
        else:
            lo = int(m.group(1)) if m.group(1) else 1
            hi = int(m.group(2)) if m.group(2) else None
        if lo < 1 or (hi is not None and hi < lo):
            raise ModuleError(f"{prog}: invalid range '{part}' (positions start at 1)")
        ranges.append((lo, hi))
    return ranges


def _select(items: list[str], ranges: list[tuple[int, int | None]]) -> list[str]:
    return [item for i, item in enumerate(items, start=1)
            if any(lo <= i and (hi is None or i <= hi) for lo, hi in ranges)]


@builtin("cut", "Select fields or characters from each line", "cut (-f LIST [-d DELIM] [-s] | -c LIST) [FILE...]",
         category="text", doc="""\
# cut

Print selected parts of each line of stdin or FILEs.

| Option | Meaning |
| --- | --- |
| `-f LIST` | Select fields, split on DELIM. |
| `-d DELIM` | Field delimiter, one character (default: TAB). |
| `-s` | With `-f`, skip lines that don't contain DELIM. |
| `-c LIST` | Select characters. |
| `--output-delimiter S` | Join the selected fields with S instead of DELIM. |

LIST is comma-separated positions and ranges, starting at 1: `1,3`, `2-4`, `3-` (to the end),
or `-2` (from the start).

## Examples

```
cat data.csv | cut -d , -f 1,3
cat /etc/passwd | cut -d : -f 1
ls -l | cut -c 1-10
```
""")
def cut(ctx: ShellContext, args: list[str], stdin: str) -> str:
    parser = ArgParser("cut")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("-f", "--fields")
    mode.add_argument("-c", "--characters")
    parser.add_argument("-d", "--delimiter", default="\t")
    parser.add_argument("-s", "--only-delimited", action="store_true")
    parser.add_argument("--output-delimiter")
    parser.add_argument("files", nargs="*")
    opts = parser.parse_args(args)
    if len(opts.delimiter) != 1:
        raise ModuleError("cut: the delimiter must be a single character")

    lines = joined("cut", opts.files, stdin).splitlines()
    if opts.characters:
        ranges = _parse_list("cut", opts.characters)
        return lines_out("".join(_select(list(line), ranges)) for line in lines)

    ranges = _parse_list("cut", opts.fields)
    joiner = opts.output_delimiter if opts.output_delimiter is not None else opts.delimiter
    out = []
    for line in lines:
        if opts.delimiter not in line:
            if not opts.only_delimited:
                out.append(line)
            continue
        out.append(joiner.join(_select(line.split(opts.delimiter), ranges)))
    return lines_out(out)


# ── tr ───────────────────────────────────────────────────────────────────────

_CHAR_CLASSES = {
    "upper": string.ascii_uppercase, "lower": string.ascii_lowercase, "digit": string.digits,
    "alpha": string.ascii_letters, "alnum": string.ascii_letters + string.digits,
    "space": " \t\n\r\f\v", "blank": " \t", "punct": string.punctuation,
    "xdigit": string.hexdigits,
}


def _expand_set(spec: str) -> str:
    """Expand ranges (a-z), classes ([:upper:]) and escapes (\\n) in a tr SET."""
    spec = _ESCAPE.sub(lambda m: _ESCAPES[m.group(1)], spec)
    out: list[str] = []
    i = 0
    while i < len(spec):
        m = re.match(r"\[:(\w+):\]", spec[i:])
        if m:
            if m.group(1) not in _CHAR_CLASSES:
                raise ModuleError(f"tr: unknown class '[:{m.group(1)}:]'")
            out.append(_CHAR_CLASSES[m.group(1)])
            i += m.end()
        elif i + 2 < len(spec) and spec[i + 1] == "-":
            lo, hi = spec[i], spec[i + 2]
            if ord(lo) > ord(hi):
                raise ModuleError(f"tr: range '{lo}-{hi}' is in reverse order")
            out.append("".join(chr(c) for c in range(ord(lo), ord(hi) + 1)))
            i += 3
        else:
            out.append(spec[i])
            i += 1
    return "".join(out)


def _squeeze(text: str, chars: set[str]) -> str:
    """Collapse runs of the same character into one, for characters in `chars`."""
    out: list[str] = []
    for ch in text:
        if not (out and ch == out[-1] and ch in chars):
            out.append(ch)
    return "".join(out)


@builtin("tr", "Translate, delete or squeeze characters", "tr [-d] [-s] [-c] SET1 [SET2]", category="text",
         doc="""\
# tr

Translate, delete or squeeze characters from stdin.

```
tr SET1 SET2       # replace each char of SET1 with the matching char of SET2
tr -d SET1         # delete chars in SET1
tr -s SET1         # squeeze runs of repeated SET1 chars into one
tr -s SET1 SET2    # translate, then squeeze runs of SET2 chars
tr -cd SET1        # delete everything NOT in SET1
```

| Option | Meaning |
| --- | --- |
| `-d`, `--delete` | Delete characters in SET1. |
| `-s`, `--squeeze-repeats` | Squeeze runs of repeated characters into one. |
| `-c`, `--complement` | Use every character NOT in SET1 (with `-d` or `-s`). |

SET syntax: plain characters, ranges such as `a-z` or `0-9`, escapes (`\\n`, `\\t`, `\\\\`) and classes
`[:upper:]`, `[:lower:]`, `[:digit:]`, `[:alpha:]`, `[:alnum:]`, `[:space:]`, `[:blank:]`,
`[:punct:]`, `[:xdigit:]`. If SET2 is shorter than SET1, its last character is repeated.

## Examples

```
echo hello | tr a-z A-Z
cat notes.txt | tr -s " "
cat data.txt | tr -d "\\r"            # strip Windows line endings
echo "phone: 555-0100" | tr -cd 0-9
cat words.txt | tr [:upper:] [:lower:]
```
""")
def tr(ctx: ShellContext, args: list[str], stdin: str) -> str:
    parser = ArgParser("tr")
    parser.add_argument("-d", "--delete", action="store_true")
    parser.add_argument("-s", "--squeeze-repeats", action="store_true")
    parser.add_argument("-c", "--complement", action="store_true")
    parser.add_argument("sets", nargs="+")
    opts = parser.parse_args(args)
    if len(opts.sets) > 2:
        raise ModuleError("tr: too many sets (at most SET1 and SET2)")
    set1 = _expand_set(opts.sets[0])
    set2 = _expand_set(opts.sets[1]) if len(opts.sets) == 2 else None

    if opts.complement and not opts.delete and set2 is not None:
        raise ModuleError("tr: -c is only supported with -d or -s")
    in_set1 = (lambda ch: ch not in set1) if opts.complement else (lambda ch: ch in set1)

    text = stdin
    if opts.delete:
        text = "".join(ch for ch in text if not in_set1(ch))
        if opts.squeeze_repeats:
            if set2 is None:
                raise ModuleError("tr: -ds needs SET2 (the characters to squeeze)")
            text = _squeeze(text, set(set2))
        elif set2 is not None:
            raise ModuleError("tr: extra SET2 with -d (did you mean -ds?)")
        return text

    if set2 is None:
        if not opts.squeeze_repeats:
            raise ModuleError("tr: missing SET2 (or use -d / -s)")
        squeeze = {ch for ch in set(text) if in_set1(ch)} if opts.complement else set(set1)
        return _squeeze(text, squeeze)

    if not set2:
        raise ModuleError("tr: SET2 must not be empty")
    padded = set2 + set2[-1] * max(0, len(set1) - len(set2))
    text = text.translate({ord(a): b for a, b in zip(set1, padded)})
    return _squeeze(text, set(set2)) if opts.squeeze_repeats else text
