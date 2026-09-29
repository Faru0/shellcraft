"""diff: compare files (or directories) line by line."""

from __future__ import annotations

import difflib
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from rich.text import Text

from core.builtins import builtin
from core.commands._io import lines_out
from core.context import Styled
from core.modkit import ArgParser, ModuleError

if TYPE_CHECKING:
    from core.context import ShellContext

_SPACE = re.compile(r"\s+")


@builtin("diff", "Compare files line by line", "diff [-u | -U N] [-i] [-w] [-b] [-q] [-s] [-r] FILE1 FILE2",
         category="text", doc="""\
# diff

Compare two files line by line and show what changed. Either file can be `-` for stdin, so
`fetch https://x/config | diff saved.conf -` works. Identical files give empty output.

On screen, removed lines are red, added lines green and hunk headers highlighted. Piped or
redirected output is plain text, so `diff -u old new > change.patch` writes a normal patch.

## Options

| Option | Meaning |
| --- | --- |
| `-u` | Unified format: changes with 3 lines of context around them, marked `-` and `+`. It's the format Git and GitHub use, and the easiest to read. |
| `-U N` | Unified format with N lines of context. |
| `-i` | Ignore case: treat upper and lower case as the same. |
| `-w` | Ignore all white space, such as trailing spaces, tabs or indentation changes. |
| `-b` | Ignore changes in the amount of white space (a run of spaces equals one). |
| `-q` | Brief: only say whether the files differ, not how. |
| `-s` | Also say when the files are identical. |
| `-r` | With two directories, compare their subdirectories too. |

Without `-u`, the output uses the classic format: `3c3` (change), `5a6,7` (add) or `8d7`
(delete), then `< ` lines from FILE1 and `> ` lines from FILE2.

With two directories, `diff` compares the files they share and reports `Only in DIR: NAME` for
the rest. With a file and a directory, it compares the file with the same name in the directory.
Binary files are reported as `Binary files A and B differ`.

Unlike Unix `diff`, finding differences is not an error, so pipelines continue.

## Examples

```
diff old.txt new.txt
diff -u config.bak config.ini
diff -uw main.py main_fixed.py
diff -q -r backup/ project/
diff -i names.txt names_sorted.txt | wc -l
```
""")
def diff(ctx: ShellContext, args: list[str], stdin: str) -> Styled:
    parser = ArgParser("diff")
    parser.add_argument("-u", dest="unified", action="store_true")
    parser.add_argument("-U", "--unified", dest="context", type=int, metavar="N")
    parser.add_argument("-i", "--ignore-case", action="store_true")
    parser.add_argument("-w", "--ignore-all-space", action="store_true")
    parser.add_argument("-b", "--ignore-space-change", action="store_true")
    parser.add_argument("-q", "--brief", action="store_true")
    parser.add_argument("-s", "--report-identical-files", dest="identical", action="store_true")
    parser.add_argument("-r", "--recursive", action="store_true")
    parser.add_argument("files", nargs=2, metavar="FILE")
    opts = parser.parse_args(args)
    if opts.context is not None and opts.context < 0:
        raise ModuleError("diff: -U must be >= 0")

    key = _normalizer(opts.ignore_case, opts.ignore_all_space, opts.ignore_space_change)
    context = opts.context if opts.context is not None else (3 if opts.unified else None)
    out = _Output()
    left, right = opts.files
    if left == "-" and right == "-":
        return out.result()
    left_dir, right_dir = _is_dir(left), _is_dir(right)
    if left_dir and right_dir:
        _diff_dirs(Path(left), Path(right), opts, key, context, stdin, out)
    else:
        if left_dir:
            left = str(Path(left) / Path(right).name)
        elif right_dir:
            right = str(Path(right) / Path(left).name)
        _diff_files(left, right, opts, key, context, stdin, out)
    return out.result()


class _Output:
    """Collects plain lines for pipes and styled lines for the screen."""

    def __init__(self) -> None:
        self.plain: list[str] = []
        self.styled = Text()

    def add(self, line: str, style: str = "") -> None:
        self.plain.append(line)
        self.styled.append(line + "\n", style=style)

    def result(self) -> Styled:
        self.styled.rstrip()
        return Styled(self.styled, lines_out(self.plain))


def _is_dir(name: str) -> bool:
    return name != "-" and Path(name).expanduser().is_dir()


def _normalizer(ignore_case: bool, all_space: bool, space_change: bool) -> Callable[[str], str] | None:
    if not (ignore_case or all_space or space_change):
        return None

    def key(line: str) -> str:
        if all_space:
            line = _SPACE.sub("", line)
        elif space_change:
            line = _SPACE.sub(" ", line).rstrip()
        return line.lower() if ignore_case else line

    return key


def _read(name: str, stdin: str) -> tuple[str | None, str]:
    """(text or None for binary, header timestamp)."""
    if name == "-":
        return stdin, ""
    path = Path(name).expanduser()
    try:
        raw = path.read_bytes()
        stamp = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    except FileNotFoundError:
        raise ModuleError(f"diff: {name}: no such file or directory") from None
    except IsADirectoryError:
        raise ModuleError(f"diff: {name}: is a directory") from None
    except PermissionError:
        raise ModuleError(f"diff: {name}: permission denied") from None
    if b"\x00" in raw[:8192]:
        return None, stamp
    return raw.decode("utf-8", errors="replace"), stamp


def _diff_files(left: str, right: str, opts, key, context: int | None, stdin: str, out: _Output,
                header: str | None = None) -> None:
    a_text, a_stamp = _read(left, stdin)
    b_text, b_stamp = _read(right, stdin)
    if a_text is None or b_text is None:
        if a_text != b_text or _read_bytes(left, stdin) != _read_bytes(right, stdin):
            out.add(f"Binary files {left} and {right} differ", "sc.warning")
        return
    a, b = a_text.splitlines(), b_text.splitlines()
    ka, kb = (list(map(key, a)), list(map(key, b))) if key else (a, b)
    if ka == kb:
        if opts.identical:
            out.add(f"Files {left} and {right} are identical", "sc.muted")
        return
    if opts.brief:
        out.add(f"Files {left} and {right} differ", "sc.warning")
        return
    matcher = difflib.SequenceMatcher(None, ka, kb, autojunk=False)
    if header:  # comparing directories: name each pair of files, like GNU diff
        out.add(header, "bold")
    if context is None:
        _normal(matcher, a, b, out)
    else:
        _unified(matcher, a, b, left, right, a_stamp, b_stamp, context, out)


def _read_bytes(name: str, stdin: str) -> bytes:
    return stdin.encode("utf-8") if name == "-" else Path(name).expanduser().read_bytes()


def _range(start: int, stop: int) -> str:
    """1-based line range for the classic format: 'n' or 'n,m'."""
    return f"{start + 1}" if stop - start <= 1 else f"{start + 1},{stop}"


def _normal(matcher: difflib.SequenceMatcher, a: list[str], b: list[str], out: _Output) -> None:
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "delete":
            out.add(f"{_range(i1, i2)}d{j1}", "sc.accent")
        elif tag == "insert":
            out.add(f"{i1}a{_range(j1, j2)}", "sc.accent")
        else:
            out.add(f"{_range(i1, i2)}c{_range(j1, j2)}", "sc.accent")
        for line in a[i1:i2]:
            out.add(f"< {line}", "sc.error")
        if tag == "replace":
            out.add("---", "sc.muted")
        for line in b[j1:j2]:
            out.add(f"> {line}", "sc.success")


def _hunk_range(start: int, length: int) -> str:
    """Unified-format range: 'start,length', with start 0-based when the range is empty."""
    first = start + 1 if length else start
    return f"{first}" if length == 1 else f"{first},{length}"


def _unified(matcher: difflib.SequenceMatcher, a: list[str], b: list[str], left: str, right: str,
             a_stamp: str, b_stamp: str, context: int, out: _Output) -> None:
    out.add(f"--- {left}" + (f"\t{a_stamp}" if a_stamp else ""), "bold")
    out.add(f"+++ {right}" + (f"\t{b_stamp}" if b_stamp else ""), "bold")
    for group in matcher.get_grouped_opcodes(context):
        i1, i2, j1, j2 = group[0][1], group[-1][2], group[0][3], group[-1][4]
        out.add(f"@@ -{_hunk_range(i1, i2 - i1)} +{_hunk_range(j1, j2 - j1)} @@", "sc.accent")
        for tag, a1, a2, b1, b2 in group:
            if tag == "equal":
                for line in a[a1:a2]:
                    out.add(f" {line}")
                continue
            for line in a[a1:a2]:
                out.add(f"-{line}", "sc.error")
            for line in b[b1:b2]:
                out.add(f"+{line}", "sc.success")


def _diff_dirs(left: Path, right: Path, opts, key, context: int | None, stdin: str, out: _Output) -> None:
    try:
        a_names = {p.name: p for p in left.expanduser().iterdir()}
        b_names = {p.name: p for p in right.expanduser().iterdir()}
    except PermissionError as exc:
        raise ModuleError(f"diff: {exc.filename}: permission denied") from None
    for name in sorted(a_names.keys() | b_names.keys(), key=str.lower):
        a_path, b_path = a_names.get(name), b_names.get(name)
        if a_path is None or b_path is None:
            where = left if b_path is None else right
            out.add(f"Only in {where}: {name}", "sc.warning")
            continue
        a_dir, b_dir = a_path.is_dir(), b_path.is_dir()
        if a_dir and b_dir:
            if opts.recursive:
                _diff_dirs(a_path, b_path, opts, key, context, stdin, out)
            else:
                out.add(f"Common subdirectories: {a_path} and {b_path}", "sc.muted")
        elif a_dir != b_dir:
            kinds = ("directory", "regular file") if a_dir else ("regular file", "directory")
            out.add(f"File {a_path} is a {kinds[0]} while file {b_path} is a {kinds[1]}", "sc.warning")
        elif a_path.is_file() and b_path.is_file():
            _diff_files(str(a_path), str(b_path), opts, key, context, stdin, out, header=f"diff {a_path} {b_path}")

