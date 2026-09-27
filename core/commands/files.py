"""Filesystem commands: ls, find, tree, touch, mkdir, cp, mv, rm."""

from __future__ import annotations

import fnmatch
import os
import shutil
import stat
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from rich.columns import Columns
from rich.console import Group
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from core.builtins import builtin
from core.commands._io import lines_out
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
    return display, Text(display), st


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


@builtin("ls", "List directory contents", "ls [-a] [-l] [-h] [-1] [PATH...]", category="files", doc="""\
# ls

List directory contents. It is the same on Windows and Linux.

On screen, names are shown in colored columns: **directories/**, `executables*`, `links@`.
Piped or redirected output is plain text with one name per line, so `ls | grep py` just works.

## Options

| Option | Meaning |
| --- | --- |
| `-a` | Include hidden entries (names starting with `.`), plus `.` and `..`. |
| `-l` | Long format: permissions, size, modified time, name. |
| `-h` | With `-l`, human-readable sizes (`4.0K`, `1.2M`). |
| `-1` | One entry per line on screen too. |

## Examples

```
ls
ls -la ~/projects
ls modules | grep skill | wc -l
```
""")
def ls(ctx: ShellContext, args: list[str], stdin: str) -> Styled:
    parser = ArgParser("ls")
    parser.add_argument("-a", "--all", action="store_true")
    parser.add_argument("-l", dest="long", action="store_true")
    parser.add_argument("-h", "--human-readable", dest="human", action="store_true")
    parser.add_argument("-1", dest="one", action="store_true")
    parser.add_argument("paths", nargs="*")
    opts = parser.parse_args(args)

    targets = opts.paths or ["."]
    sections: list[tuple[str | None, list]] = []
    files: list = []
    for raw in targets:
        path = Path(raw).expanduser()
        if not path.exists() and not path.is_symlink():
            raise ModuleError(f"ls: {raw}: no such file or directory")
        if path.is_dir():
            sections.append((raw, _list_dir(path, opts.all)))
        else:
            files.append(_entry(path, raw))
    if files:
        sections.insert(0, (None, files))
    titled = len(sections) > 1

    renderables, plain = [], []
    for i, (title, entries) in enumerate(sections):
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
            Path(name).expanduser().mkdir(parents=opts.parents, exist_ok=opts.parents)
        except FileExistsError:
            raise ModuleError(f"mkdir: {name}: already exists") from None
        except FileNotFoundError:
            raise ModuleError(f"mkdir: {name}: parent directory missing (use -p)") from None
        except OSError as exc:
            raise ModuleError(f"mkdir: {name}: {exc.strerror or exc}") from None
    return ""


# ── cp / mv ──────────────────────────────────────────────────────────────────

def _destinations(prog: str, sources: list[str], dest: str) -> list[tuple[Path, Path]]:
    target = Path(dest).expanduser()
    if len(sources) > 1 and not target.is_dir():
        raise ModuleError(f"{prog}: target '{dest}' is not a directory")
    pairs = []
    for src in sources:
        path = Path(src).expanduser()
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
        path = Path(raw).expanduser()
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


@builtin("find", "Search for files in a directory tree", "find [PATH...] [-name GLOB] [-iname GLOB] "
         "[-type f|d|l] [-maxdepth N] [-mindepth N]", category="files", doc="""\
# find

Walk each PATH (default `.`) recursively and print every entry that matches all the given tests.
Output has one path per line, so it pipes into `grep`, `wc -l` and so on.

## Tests

| Option | Meaning |
| --- | --- |
| `-name GLOB` | The file name matches GLOB (`*.py`, `data_??.csv`). Quote globs. |
| `-iname GLOB` | Like `-name`, but ignoring case. |
| `-type f\\|d\\|l` | Only files, directories or symlinks. |
| `-maxdepth N` | Descend at most N levels (0 = only the PATHs themselves). |
| `-mindepth N` | Skip entries shallower than N levels. |

Symlinked directories are listed but not followed. Unreadable directories are skipped.

## Examples

```
find . -name "*.py"
find modules -type f -iname "*.SKILL"
find -maxdepth 1 -type d
find . -name "*.log" | wc -l
```
""")
def find(ctx: ShellContext, args: list[str], stdin: str) -> str:
    parser = ArgParser("find")
    parser.add_argument("paths", nargs="*")
    parser.add_argument("-name")
    parser.add_argument("-iname")
    parser.add_argument("-type", choices=["f", "d", "l"])
    parser.add_argument("-maxdepth", type=int)
    parser.add_argument("-mindepth", type=int, default=0)
    opts = parser.parse_args(args)

    def matches(path: Path, name: str, depth: int) -> bool:
        if depth < opts.mindepth:
            return False
        if opts.name and not fnmatch.fnmatchcase(name, opts.name):
            return False
        if opts.iname and not fnmatch.fnmatchcase(name.lower(), opts.iname.lower()):
            return False
        if opts.type == "l" and not path.is_symlink():
            return False
        if opts.type == "d" and (path.is_symlink() or not path.is_dir()):
            return False
        if opts.type == "f" and (path.is_symlink() or not path.is_file()):
            return False
        return True

    found: list[str] = []
    for start in opts.paths or ["."]:
        root = Path(start).expanduser()
        if not root.exists() and not root.is_symlink():
            raise ModuleError(f"find: {start}: no such file or directory")
        for path, shown, depth in _walk(root, start, 0, opts.maxdepth):
            # Like POSIX find, a start point is matched by its literal basename (so "." is ".").
            name = path.name if depth else os.path.basename(os.path.normpath(start))
            if matches(path, name, depth):
                found.append(shown)
    return lines_out(found)


# ── tree ─────────────────────────────────────────────────────────────────────

@builtin("tree", "Show a directory tree", "tree [-a] [-d] [-L N] [PATH]", category="files", doc="""\
# tree

Show the contents of PATH (default `.`) as a tree, followed by a count of directories and files.
It is a colored tree on screen, and plain `├──`/`└──` text when piped or redirected.

| Option | Meaning |
| --- | --- |
| `-a` | Include hidden entries. |
| `-d` | Directories only. |
| `-L N` | Descend at most N levels. |

## Examples

```
tree
tree -L 2 core
tree -d > layout.txt
```
""")
def tree(ctx: ShellContext, args: list[str], stdin: str) -> Styled:
    parser = ArgParser("tree")
    parser.add_argument("path", nargs="?", default=".")
    parser.add_argument("-a", action="store_true", dest="all")
    parser.add_argument("-d", action="store_true", dest="dirs_only")
    parser.add_argument("-L", type=int, dest="level")
    opts = parser.parse_args(args)
    root = Path(opts.path).expanduser()
    if not root.is_dir():
        raise ModuleError(f"tree: {opts.path}: not a directory")
    if opts.level is not None and opts.level < 1:
        raise ModuleError("tree: -L must be >= 1")

    counts = {"dirs": 0, "files": 0}
    plain = [opts.path]
    rich_root = Tree(Text(opts.path, style="sc.path"), guide_style="sc.muted")

    def add(directory: Path, node: Tree, prefix: str, depth: int) -> None:
        entries = [e for e in _children(directory, opts.all) if not opts.dirs_only or e.is_dir()]
        for i, entry in enumerate(entries):
            last = i == len(entries) - 1
            name, styled, _ = _entry(entry, entry.name)
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
        path = Path(name).expanduser()
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
