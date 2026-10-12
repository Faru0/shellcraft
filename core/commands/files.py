"""Filesystem commands: ls, find, tree, touch, mkdir, cp, mv, rm."""

from __future__ import annotations

import fnmatch
import os
import shutil
import stat
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from rich.columns import Columns
from rich.console import Group
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from core.builtins import builtin
from core.commands._io import lines_out, user_path
from core.context import Styled
from core.modkit import ArgParser, ModuleError

if TYPE_CHECKING:
    from core.context import ShellContext


# ── ls ───────────────────────────────────────────────────────────────────────

def _human(size: int) -> str:
    for unit in ("B", "K", "M", "G", "T"):
        if size < 1024 or unit == "T":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return str(size)


def _is_executable(path: Path, st: os.stat_result) -> bool:
    if os.name == "nt":
        return path.suffix.lower() in {".exe", ".bat", ".cmd", ".ps1", ".com"}
    return bool(st.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))


def _entry(path: Path, display: str) -> tuple[str, Text, os.stat_result | None]:
    """(plain name, styled name, stat) — directories get a trailing separator."""
    try:
        st = path.lstat()
    except OSError:
        return display, Text(display, style="sc.error"), None
    if stat.S_ISLNK(st.st_mode):
        return display, Text(display + "@", style="sc.warning"), st
    if stat.S_ISDIR(st.st_mode):
        return display + "/", Text(display + "/", style="sc.path"), st
    if _is_executable(path, st):
        return display, Text(display + "*", style="sc.success"), st
    return display, Text(display, style=_EXT_STYLES.get(path.suffix.lower(), "")), st


_EXT_CLASSES = {
    "sc.warning": (".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".zst", ".7z", ".rar", ".deb", ".rpm", ".whl",
                   ".jar", ".iso", ".dmg"),
    "sc.accent": (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".webp", ".ico", ".mp3", ".wav", ".flac",
                  ".ogg", ".mp4", ".mkv", ".mov", ".avi", ".webm", ".pdf"),
    "sc.prompt": (".py", ".js", ".ts", ".rs", ".go", ".c", ".h", ".cpp", ".hpp", ".java", ".rb", ".sh", ".ps1",
                  ".php", ".cs", ".swift", ".kt", ".lua", ".pl", ".r", ".jl", ".toml", ".yaml", ".yml", ".json"),
    "sc.muted": (".pyc", ".pyo", ".o", ".obj", ".class", ".tmp", ".bak", ".swp", ".log", ".lock", ".cache"),
}
_EXT_STYLES = {ext: style for style, exts in _EXT_CLASSES.items() for ext in exts}


def _list_dir(directory: Path, show_all: bool) -> list[tuple[str, Text, os.stat_result | None]]:
    try:
        children = sorted(directory.iterdir(), key=lambda p: p.name.lower())
    except PermissionError:
        raise ModuleError(f"ls: {directory}: permission denied") from None
    entries = []
    if show_all:
        entries += [_entry(directory, "."), _entry(directory.parent, "..")]
    for child in children:
        if show_all or not child.name.startswith("."):
            entries.append(_entry(child, child.name))
    return entries


def _long_table(entries, human: bool) -> tuple[Table, list[str]]:
    table = Table(box=None, show_header=False, pad_edge=False, padding=(0, 1))
    for _ in range(4):
        table.add_column()
    table.columns[1].justify = "right"
    plain = []
    for name, styled, st in entries:
        if st is None:
            mode, size, mtime = "?" * 10, "?", "?"
        else:
            mode = stat.filemode(st.st_mode)
            size = _human(st.st_size) if human else str(st.st_size)
            mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
        table.add_row(Text(mode, style="sc.muted"), size, Text(mtime, style="sc.muted"), styled)
        plain.append(f"{mode} {size:>8} {mtime} {name}")
    return table, plain


@builtin("ls", "List directory contents", "ls [-a] [-l] [-h] [-1] [-t] [-S] [-r] [-R] [-d] [PATH...]",
         category="files", doc="""\
# ls

List directory contents. It is the same on Windows and Linux.

On screen, names are shown in colored columns: **directories/**, `executables*`, `links@`, and
file types by extension (archives, images and media, source code, build leftovers). Piped or
redirected output is plain text with one name per line, so `ls | grep py` just works.

## Options

| Option | Meaning |
| --- | --- |
| `-a` | Include hidden entries (names starting with `.`), plus `.` and `..`. |
| `-l` | Long format: permissions, size, modified time, name. |
| `-h` | With `-l`, human-readable sizes (`4.0K`, `1.2M`). |
| `-1` | One entry per line on screen too. |
| `-t` | Sort by modification time, newest first. |
| `-S` | Sort by size, largest first. |
| `-r` | Reverse the sort order. |
| `-R` | List subdirectories recursively. |
| `-d` | List directories themselves, not their contents. |

## Examples

```
ls
ls -la ~/projects
ls -lt | head          # the newest files
ls -lSh                # the biggest files
ls modules | grep skill | wc -l
```
""")
def ls(ctx: ShellContext, args: list[str], stdin: str) -> Styled:
    parser = ArgParser("ls")
    parser.add_argument("-a", "--all", action="store_true")
    parser.add_argument("-l", dest="long", action="store_true")
    parser.add_argument("-h", "--human-readable", dest="human", action="store_true")
    parser.add_argument("-1", dest="one", action="store_true")
    parser.add_argument("-t", dest="by_time", action="store_true")
    parser.add_argument("-S", dest="by_size", action="store_true")
    parser.add_argument("-r", "--reverse", action="store_true")
    parser.add_argument("-R", "--recursive", action="store_true")
    parser.add_argument("-d", "--directory", action="store_true")
    parser.add_argument("paths", nargs="*")
    opts = parser.parse_args(args)

    def order(entries: list) -> list:
        if opts.by_time:
            entries = sorted(entries, key=lambda e: -(e[2].st_mtime if e[2] else 0))
        elif opts.by_size:
            entries = sorted(entries, key=lambda e: -(e[2].st_size if e[2] else 0))
        return list(reversed(entries)) if opts.reverse else entries

    targets = opts.paths or ["."]
    sections: list[tuple[str | None, list]] = []
    files: list = []
    for raw in targets:
        path = user_path(raw)
        if not path.exists() and not path.is_symlink():
            raise ModuleError(f"ls: {raw}: no such file or directory")
        if path.is_dir() and not opts.directory:
            sections.append((raw, _list_dir(path, opts.all)))
            if opts.recursive:
                for sub, shown in _subdirs(path, raw, opts.all):
                    sections.append((shown, _list_dir(sub, opts.all)))
        else:
            files.append(_entry(path, raw))
    if files:
        sections.insert(0, (None, files))
    titled = len(sections) > 1

    renderables, plain = [], []
    for i, (title, entries) in enumerate(sections):
        entries = order(entries)
        if titled and title is not None:
            if i:
                renderables.append(Text(""))
                plain.append("")
            renderables.append(Text(f"{title}:", style="sc.accent"))
            plain.append(f"{title}:")
        if opts.long:
            table, lines = _long_table(entries, opts.human)
            renderables.append(table)
            plain += lines
        else:
            names = [styled for _, styled, _ in entries]
            renderables.append(Group(*names) if opts.one else Columns(names, padding=(0, 2)))
            plain += [name for name, _, _ in entries]
    return Styled(Group(*renderables), lines_out(plain))


def _subdirs(root: Path, shown: str, show_hidden: bool):
    """Every directory under root (depth first, sorted), as (path, displayed path)."""
    for child in _children(root, show_hidden):
        if child.is_dir() and not child.is_symlink():
            display = os.path.join(shown, child.name)
            yield child, display
            yield from _subdirs(child, display, show_hidden)


# ── mkdir ────────────────────────────────────────────────────────────────────

@builtin("mkdir", "Create directories", "mkdir [-p] DIR...", category="files", writes=True, doc="""\
# mkdir

Create each DIR.

| Option | Meaning |
| --- | --- |
| `-p` | Create missing parent directories too, and don't complain if DIR already exists. |

## Examples

```
mkdir build
mkdir -p data/2024/raw
```
""")
def mkdir(ctx: ShellContext, args: list[str], stdin: str) -> str:
    parser = ArgParser("mkdir")
    parser.add_argument("-p", "--parents", action="store_true")
    parser.add_argument("dirs", nargs="+")
    opts = parser.parse_args(args)
    for name in opts.dirs:
        try:
            user_path(name).mkdir(parents=opts.parents, exist_ok=opts.parents)
        except FileExistsError:
            raise ModuleError(f"mkdir: {name}: already exists") from None
        except FileNotFoundError:
            raise ModuleError(f"mkdir: {name}: parent directory missing (use -p)") from None
        except OSError as exc:
            raise ModuleError(f"mkdir: {name}: {exc.strerror or exc}") from None
    return ""


# ── cp / mv ──────────────────────────────────────────────────────────────────

def _destinations(prog: str, sources: list[str], dest: str) -> list[tuple[Path, Path]]:
    target = user_path(dest)
    if len(sources) > 1 and not target.is_dir():
        raise ModuleError(f"{prog}: target '{dest}' is not a directory")
    pairs = []
    for src in sources:
        path = user_path(src)
        if not path.exists() and not path.is_symlink():
            raise ModuleError(f"{prog}: {src}: no such file or directory")
        out = target / path.name if target.is_dir() else target
        if out.resolve() == path.resolve():
            raise ModuleError(f"{prog}: '{src}' and '{out}' are the same file")
        pairs.append((path, out))
    return pairs


@builtin("cp", "Copy files and directories", "cp [-r] SRC... DEST", category="files", writes=True, doc="""\
# cp

Copy SRC to DEST. If there are several SRCs, DEST must be an existing directory.

| Option | Meaning |
| --- | --- |
| `-r` | Copy directories recursively. |

## Examples

```
cp notes.txt notes.bak
cp -r modules modules-backup
cp a.txt b.txt archive/
```
""")
def cp(ctx: ShellContext, args: list[str], stdin: str) -> str:
    parser = ArgParser("cp")
    parser.add_argument("-r", "-R", "--recursive", dest="recursive", action="store_true")
    parser.add_argument("paths", nargs="+")
    opts = parser.parse_args(args)
    if len(opts.paths) < 2:
        raise ModuleError("cp: missing destination (usage: cp [-r] SRC... DEST)")
    for src, out in _destinations("cp", opts.paths[:-1], opts.paths[-1]):
        try:
            if src.is_dir():
                if not opts.recursive:
                    raise ModuleError(f"cp: {src}: is a directory (use -r)")
                if out.resolve().is_relative_to(src.resolve()):  # cp -r DIR DIR/sub would never end
                    raise ModuleError(f"cp: cannot copy a directory, '{src}', into itself, '{out}'")
                shutil.copytree(src, out, dirs_exist_ok=True)
            else:
                shutil.copy2(src, out)
        except OSError as exc:
            raise ModuleError(f"cp: {src}: {exc.strerror or exc}") from None
    return ""


@builtin("mv", "Move or rename files and directories", "mv SRC... DEST", category="files", writes=True, doc="""\
# mv

Move or rename SRC to DEST. If there are several SRCs, DEST must be an existing directory.

## Examples

```
mv draft.md final.md
mv a.log b.log logs/
```
""")
def mv(ctx: ShellContext, args: list[str], stdin: str) -> str:
    parser = ArgParser("mv")
    parser.add_argument("paths", nargs="+")
    opts = parser.parse_args(args)
    if len(opts.paths) < 2:
        raise ModuleError("mv: missing destination (usage: mv SRC... DEST)")
    for src, out in _destinations("mv", opts.paths[:-1], opts.paths[-1]):
        try:
            shutil.move(str(src), str(out))
        except OSError as exc:
            raise ModuleError(f"mv: {src}: {exc.strerror or exc}") from None
    return ""


# ── rm ───────────────────────────────────────────────────────────────────────

def _protected(path: Path) -> str | None:
    resolved = path.resolve()
    if resolved == Path(resolved.anchor):
        return "refusing to remove a filesystem root"
    if resolved == Path.home().resolve():
        return "refusing to remove your home directory"
    cwd = Path.cwd().resolve()
    if resolved == cwd or resolved in cwd.parents:
        return "refusing to remove the current directory or one of its parents"
    return None


@builtin("rm", "Remove files and directories", "rm [-r] [-f] PATH...", category="files", writes=True, doc="""\
# rm

Remove each PATH. **There is no trash can:** removed files are gone.

| Option | Meaning |
| --- | --- |
| `-r` | Remove directories and their contents. |
| `-f` | Ignore paths that don't exist. |

For safety, `rm` always refuses a filesystem root, your home directory, and the current
directory or any of its parents (including `.` and `..`).

## Examples

```
rm old.log
rm -r build
rm -f maybe-missing.tmp
```
""")
def rm(ctx: ShellContext, args: list[str], stdin: str) -> str:
    parser = ArgParser("rm")
    parser.add_argument("-r", "-R", "--recursive", dest="recursive", action="store_true")
    parser.add_argument("-f", "--force", action="store_true")
    parser.add_argument("paths", nargs="+")
    opts = parser.parse_args(args)
    recursive, force = opts.recursive, opts.force

    targets = []
    for raw in opts.paths:  # validate everything before deleting anything
        path = user_path(raw)
        if not path.exists() and not path.is_symlink():
            if force:
                continue
            raise ModuleError(f"rm: {raw}: no such file or directory")
        if reason := _protected(path):
            raise ModuleError(f"rm: {raw}: {reason}")
        if path.is_dir() and not path.is_symlink() and not recursive:
            raise ModuleError(f"rm: {raw}: is a directory (use -r)")
        targets.append((raw, path))

    for raw, path in targets:
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
        except OSError as exc:
            raise ModuleError(f"rm: {raw}: {exc.strerror or exc}") from None
    return ""


# ── find ─────────────────────────────────────────────────────────────────────

def _children(directory: Path, show_hidden: bool = True) -> list[Path]:
    try:
        entries = sorted(directory.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return []  # unreadable directories are skipped, like `find` without error spam
    return [e for e in entries if show_hidden or not e.name.startswith(".")]


def _walk(path: Path, shown: str, depth: int, max_depth: int | None):
    yield path, shown, depth
    if max_depth is not None and depth >= max_depth:
        return
    if path.is_dir() and not path.is_symlink():
        for child in _children(path):
            yield from _walk(child, os.path.join(shown, child.name), depth + 1, max_depth)


_SIZE_UNITS = {"c": 1, "b": 512, "w": 2, "k": 1024, "M": 1024 ** 2, "G": 1024 ** 3}
_TESTS_WITH_VALUE = {"-name", "-iname", "-type", "-maxdepth", "-mindepth", "-size", "-mtime", "-newer", "-path",
                     "-ipath"}
_TESTS_ALONE = {"-empty"}


def _parse_size(spec: str) -> tuple[str, int]:
    """`+10M`, `-1k`, `100c` → (sign, bytes). Without a unit, 512-byte blocks as in POSIX find."""
    sign = spec[0] if spec[:1] in "+-" else ""
    rest = spec[len(sign):]
    unit = rest[-1] if rest[-1:] in _SIZE_UNITS else "b"
    digits = rest[:-1] if rest[-1:] in _SIZE_UNITS else rest
    if not digits.isdigit():
        raise ModuleError(f"find: invalid -size '{spec}' (e.g. +10M, -1k, 100c)")
    return sign, int(digits) * _SIZE_UNITS[unit]


def _parse_days(spec: str) -> tuple[str, int]:
    sign = spec[0] if spec[:1] in "+-" else ""
    digits = spec[len(sign):]
    if not digits.isdigit():
        raise ModuleError(f"find: invalid -mtime '{spec}' (e.g. -7, +30, 0)")
    return sign, int(digits)


def _compare(sign: str, value: float, wanted: float) -> bool:
    if sign == "+":
        return value > wanted
    if sign == "-":
        return value < wanted
    return value == wanted


@builtin("find", "Search for files in a directory tree", "find [PATH...] [-name GLOB] [-iname GLOB] [-path GLOB] "
         "[-type f|d|l] [-size [+-]N[ckMG]] [-mtime [+-]DAYS] [-newer FILE] [-empty] [-maxdepth N] [-mindepth N] "
         "[-not TEST | ! TEST]", category="files", doc="""\
# find

Walk each PATH (default `.`) recursively and print every entry that matches all the given tests.
Output has one path per line, so it pipes into `grep`, `wc -l` and so on.

## Tests

| Option | Meaning |
| --- | --- |
| `-name GLOB` | The file name matches GLOB (`*.py`, `data_??.csv`). Quote globs. |
| `-iname GLOB` | Like `-name`, but ignoring case. |
| `-path GLOB` | The whole path matches GLOB (`*/tests/*`); `-ipath` ignores case. |
| `-type f\\|d\\|l` | Only files, directories or symlinks. |
| `-size [+-]N[ckMG]` | Size over (`+`), under (`-`) or exactly N units: `c` bytes, `k` KiB, `M` MiB, `G` GiB (`+10M`). |
| `-mtime [+-]DAYS` | Modified more than (`+`), less than (`-`) or exactly DAYS days ago (`-mtime -7`: this week). |
| `-newer FILE` | Modified more recently than FILE. |
| `-empty` | An empty file or directory. |
| `-maxdepth N` | Descend at most N levels (0 = only the PATHs themselves). |
| `-mindepth N` | Skip entries shallower than N levels. |
| `-not TEST`, `! TEST` | Negate the next test (`-not -name "*.pyc"`). |

Symlinked directories are listed but not followed. Unreadable directories are skipped.

## Examples

```
find . -name "*.py"
find modules -type f -iname "*.SKILL"
find -maxdepth 1 -type d
find . -size +1M -mtime -7
find . -type f -not -name "*.pyc" | wc -l
```
""")
def find(ctx: ShellContext, args: list[str], stdin: str) -> str:
    paths: list[str] = []
    tests: list[tuple[bool, str, str | None]] = []  # (negated, test, value)
    maxdepth: int | None = None
    mindepth = 0
    i, negate = 0, False
    while i < len(args):
        arg = args[i]
        if arg in ("-not", "!"):
            negate = not negate
            i += 1
            continue
        if arg in _TESTS_WITH_VALUE:
            if i + 1 >= len(args):
                raise ModuleError(f"find: {arg} needs a value  (see `man find`)")
            value = args[i + 1]
            if arg in ("-maxdepth", "-mindepth"):
                if not value.isdigit():
                    raise ModuleError(f"find: {arg} must be a number")
                if arg == "-maxdepth":
                    maxdepth = int(value)
                else:
                    mindepth = int(value)
            elif arg == "-type" and value not in ("f", "d", "l"):
                raise ModuleError("find: -type must be f, d or l")
            else:
                tests.append((negate, arg, value))
            negate = False
            i += 2
            continue
        if arg in _TESTS_ALONE:
            tests.append((negate, arg, None))
            negate = False
            i += 1
            continue
        if arg.startswith("-") and len(arg) > 1:
            raise ModuleError(f"find: unknown test '{arg}'  (see `man find`)")
        if tests:
            raise ModuleError(f"find: paths must come before tests: '{arg}'")
        paths.append(arg)
        i += 1

    newer_cache: dict[str, float] = {}
    now = time.time()

    def check(path: Path, name: str, shown: str, test: str, value: str | None) -> bool:
        if test == "-name":
            return fnmatch.fnmatchcase(name, value)
        if test == "-iname":
            return fnmatch.fnmatchcase(name.lower(), value.lower())
        if test == "-path":
            return fnmatch.fnmatchcase(shown, value)
        if test == "-ipath":
            return fnmatch.fnmatchcase(shown.lower(), value.lower())
        if test == "-type":
            if value == "l":
                return path.is_symlink()
            if value == "d":
                return not path.is_symlink() and path.is_dir()
            return not path.is_symlink() and path.is_file()
        try:
            st = path.lstat()
        except OSError:
            return False
        if test == "-size":
            sign, wanted = _parse_size(value)
            unit = _SIZE_UNITS["b"] if value[-1:] not in _SIZE_UNITS else _SIZE_UNITS[value[-1]]
            # POSIX rounds sizes up to whole units before comparing (so -size 1k matches 1..1024 bytes).
            return _compare(sign, -(-st.st_size // unit), -(-wanted // unit))
        if test == "-mtime":
            sign, days = _parse_days(value)
            return _compare(sign, int((now - st.st_mtime) // 86400), days)
        if test == "-newer":
            if value not in newer_cache:
                try:
                    newer_cache[value] = user_path(value).stat().st_mtime
                except OSError:
                    raise ModuleError(f"find: -newer {value}: no such file") from None
            return st.st_mtime > newer_cache[value]
        if test == "-empty":
            if stat.S_ISDIR(st.st_mode):
                return not _children(path)
            return stat.S_ISREG(st.st_mode) and st.st_size == 0
        return True

    def matches(path: Path, name: str, shown: str, depth: int) -> bool:
        if depth < mindepth:
            return False
        return all(check(path, name, shown, test, value) != negated for negated, test, value in tests)

    found: list[str] = []
    for start in paths or ["."]:
        root = user_path(start)
        if not root.exists() and not root.is_symlink():
            raise ModuleError(f"find: {start}: no such file or directory")
        for path, shown, depth in _walk(root, start, 0, maxdepth):
            # Like POSIX find, a start point is matched by its literal basename (so "." is ".").
            name = path.name if depth else os.path.basename(os.path.normpath(start))
            if matches(path, name, shown, depth):
                found.append(shown)
    return lines_out(found)


# ── tree ─────────────────────────────────────────────────────────────────────

@builtin("tree", "Show a directory tree", "tree [-a] [-d] [-L N] [-I GLOB] [--du] [PATH]", category="files", doc="""\
# tree

Show the contents of PATH (default `.`) as a tree, followed by a count of directories and files.
It is a colored tree on screen, and plain `├──`/`└──` text when piped or redirected.

| Option | Meaning |
| --- | --- |
| `-a` | Include hidden entries. |
| `-d` | Directories only. |
| `-L N` | Descend at most N levels. |
| `-I GLOB` | Leave out entries matching GLOB (`-I __pycache__`, `-I "*.pyc\\|*.log"`); repeatable. |
| `--du` | Show the size of every entry (a directory's size includes everything below it). |

## Examples

```
tree
tree -L 2 core
tree -I "__pycache__|.git" -a
tree --du -d
tree -d > layout.txt
```
""")
def tree(ctx: ShellContext, args: list[str], stdin: str) -> Styled:
    parser = ArgParser("tree")
    parser.add_argument("path", nargs="?", default=".")
    parser.add_argument("-a", action="store_true", dest="all")
    parser.add_argument("-d", action="store_true", dest="dirs_only")
    parser.add_argument("-L", type=int, dest="level")
    parser.add_argument("-I", action="append", default=[], dest="ignore", metavar="GLOB")
    parser.add_argument("--du", action="store_true", dest="sizes")
    opts = parser.parse_args(args)
    root = user_path(opts.path)
    if not root.is_dir():
        raise ModuleError(f"tree: {opts.path}: not a directory")
    if opts.level is not None and opts.level < 1:
        raise ModuleError("tree: -L must be >= 1")
    ignored = [g for spec in opts.ignore for g in spec.split("|") if g]

    counts = {"dirs": 0, "files": 0}
    plain = [opts.path]
    rich_root = Tree(Text(opts.path, style="sc.path"), guide_style="sc.muted")

    def size_of(path: Path) -> int:
        try:
            st = path.lstat()
        except OSError:
            return 0
        if not stat.S_ISDIR(st.st_mode):
            return st.st_size
        return sum(size_of(c) for c in _children(path, True))

    def label(entry: Path, name: str, styled: Text) -> tuple[str, Text]:
        if not opts.sizes:
            return name, styled
        size = _human(size_of(entry))
        return f"[{size:>6}]  {name}", Text.assemble((f"[{size:>6}]  ", "sc.accent"), styled)

    def add(directory: Path, node: Tree, prefix: str, depth: int) -> None:
        entries = [e for e in _children(directory, opts.all) if not opts.dirs_only or e.is_dir()]
        entries = [e for e in entries if not any(fnmatch.fnmatchcase(e.name, g) for g in ignored)]
        for i, entry in enumerate(entries):
            last = i == len(entries) - 1
            name, styled, _ = _entry(entry, entry.name)
            name, styled = label(entry, name, styled)
            plain.append(prefix + ("└── " if last else "├── ") + name)
            child = node.add(styled)
            is_dir = entry.is_dir() and not entry.is_symlink()
            counts["dirs" if is_dir else "files"] += 1
            if is_dir and (opts.level is None or depth < opts.level):
                add(entry, child, prefix + ("    " if last else "│   "), depth + 1)

    add(root, rich_root, "", 1)
    summary = f"{counts['dirs']} director{'y' if counts['dirs'] == 1 else 'ies'}"
    if not opts.dirs_only:
        summary += f", {counts['files']} file{'' if counts['files'] == 1 else 's'}"
    if opts.sizes:
        summary = f"{_human(size_of(root))} used in " + summary
    plain += ["", summary]
    return Styled(Group(rich_root, Text(""), Text(summary, style="sc.muted")), lines_out(plain))


# ── touch ────────────────────────────────────────────────────────────────────

@builtin("touch", "Create empty files or update timestamps", "touch [-c] FILE...", category="files",
         writes=True, doc="""\
# touch

Update the modification time of each FILE to now, and create any FILE that doesn't exist as an
empty file.

| Option | Meaning |
| --- | --- |
| `-c` | Don't create missing files. |

## Examples

```
touch notes.md
touch -c build.stamp
```
""")
def touch(ctx: ShellContext, args: list[str], stdin: str) -> str:
    parser = ArgParser("touch")
    parser.add_argument("-c", "--no-create", action="store_true")
    parser.add_argument("files", nargs="+")
    opts = parser.parse_args(args)
    for name in opts.files:
        path = user_path(name)
        try:
            if path.exists():
                os.utime(path, None)
            elif not opts.no_create:
                path.touch()
        except FileNotFoundError:
            raise ModuleError(f"touch: {name}: parent directory missing") from None
        except OSError as exc:
            raise ModuleError(f"touch: {name}: {exc.strerror or exc}") from None
    return ""


# ── du ───────────────────────────────────────────────────────────────────────

@builtin("du", "Show how much space files and directories use", "du [-s] [-h] [-a] [-d N] [-c] [-S] [-b] [PATH...]",
         category="files", doc="""\
# du

Show how much space each directory under PATH (default `.`) uses, including everything below it.
Sizes are in KiB by default (rounded up). Directories are listed after their contents, and the
last line is PATH itself.

Sizes are *apparent* sizes (the total of the file sizes, like GNU `du --apparent-size`), so `du`
gives the same numbers on every operating system. Symbolic links are not followed, and a
hard-linked file is only counted once.

## Options

| Option | Meaning |
| --- | --- |
| `-s` | Summary: one line per PATH, its total. |
| `-h` | Human-readable sizes (`4.0K`, `1.2M`, `3.4G`). |
| `-a` | List files too, not just directories. |
| `-d N` | Only list entries at most N levels below PATH (`-d 1`: PATH's direct children). Everything is still counted. |
| `-c` | Add a grand total at the end. |
| `-S` | Sort by size, largest first. |
| `-b` | Sizes in bytes. |

Folders you can't read are skipped. On screen, a note says how many were skipped.

## Examples

```
du -sh
du -h -d 1 -S ~/projects
du -a -d 1 | sort -n -r | head -5
du -sh modules tests -c
```
""")
def du(ctx: ShellContext, args: list[str], stdin: str) -> Styled:
    parser = ArgParser("du")
    parser.add_argument("-s", "--summarize", action="store_true")
    parser.add_argument("-h", "--human-readable", dest="human", action="store_true")
    parser.add_argument("-a", "--all", action="store_true")
    parser.add_argument("-d", "--max-depth", type=int, metavar="N")
    parser.add_argument("-c", "--total", action="store_true")
    parser.add_argument("-S", "--sort", action="store_true")
    parser.add_argument("-b", "--bytes", action="store_true")
    parser.add_argument("paths", nargs="*")
    opts = parser.parse_args(args)
    if opts.max_depth is not None and opts.max_depth < 0:
        raise ModuleError("du: -d must be >= 0")
    if opts.summarize and opts.max_depth not in (None, 0):
        raise ModuleError("du: -s and -d N can't be combined")
    max_depth = 0 if opts.summarize else opts.max_depth

    seen: set[tuple[int, int]] = set()
    skipped = [0]
    rows: list[tuple[int, str, bool]] = []  # (bytes, path, is_dir)

    def walk(path: Path, shown: str, depth: int) -> int:
        try:
            st = path.lstat()
        except OSError:
            skipped[0] += 1
            return 0
        if not stat.S_ISDIR(st.st_mode):
            if st.st_nlink > 1:
                if (st.st_dev, st.st_ino) in seen:
                    return 0
                seen.add((st.st_dev, st.st_ino))
            if (opts.all or depth == 0) and (max_depth is None or depth <= max_depth):
                rows.append((st.st_size, shown, False))
            return st.st_size
        total = 0  # only file contents count (as GNU du --apparent-size), so every OS agrees
        try:
            children = sorted(os.scandir(path), key=lambda e: e.name.lower())
        except OSError:
            skipped[0] += 1
            children = []
        for child in children:
            total += walk(Path(child.path), os.path.join(shown, child.name), depth + 1)
        if max_depth is None or depth <= max_depth:
            rows.append((total, shown, True))
        return total

    grand = 0
    for raw in opts.paths or ["."]:
        path = user_path(raw)
        if not path.exists() and not path.is_symlink():
            raise ModuleError(f"du: {raw}: no such file or directory")
        grand += walk(path, raw, 0)
    if opts.sort:
        rows.sort(key=lambda r: r[0], reverse=True)
    if opts.total:
        rows.append((grand, "total", False))

    def size(n: int) -> str:
        if opts.bytes:
            return str(n)
        if opts.human:
            return _human(n)
        return str(-(-n // 1024))  # KiB, rounded up like GNU du

    table = Table(box=None, show_header=False, pad_edge=False, padding=(0, 2))
    table.add_column(justify="right", style="sc.accent")
    table.add_column()
    plain = []
    for n, shown, is_dir in rows:
        label = Text(shown, style="bold" if shown == "total" else ("sc.path" if is_dir else ""))
        table.add_row(size(n), label)
        plain.append(f"{size(n)}\t{shown}")
    screen = [table]
    if skipped[0]:
        screen.append(Text(f"du: skipped {skipped[0]} unreadable entr{'y' if skipped[0] == 1 else 'ies'}",
                           style="sc.warning"))
    return Styled(Group(*screen), lines_out(plain))
