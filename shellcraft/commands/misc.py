"""Small everyday commands: printf, seq, xargs, basename, dirname, realpath, sleep, stat, whoami,
hostname, uname, time, type, export, unset."""

from __future__ import annotations

import getpass
import os
import platform
import re
import shutil
import socket
import stat as statmod
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.text import Text

from shellcraft import aliases, envvars
from shellcraft.builtins import BUILTINS, builtin
from shellcraft.commands._io import lines_out, user_path
from shellcraft.context import CommandError, Styled, WithStatus, to_text
from shellcraft.modkit import ArgParser, ModuleError

if TYPE_CHECKING:
    from shellcraft.context import ShellContext


# ── printf ───────────────────────────────────────────────────────────────────

_FORMAT = re.compile(r"%([-+ 0#]*)(\*|\d+)?(?:\.(\*|\d+))?([diouxXeEfgGcsq%b])")
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", "a": "\a", "b": "\b", "f": "\f", "v": "\v",
            "e": "\x1b", "0": "\0", '"': '"'}


def _unescape(text: str) -> str:
    """printf's escapes, `\\033` (octal, 1 to 3 digits) and `\\x1b` (hex) included, as in bash."""
    return re.sub(r"\\([0-7]{1,3}|x[0-9a-fA-F]{1,2}|[ntr\\abfve\"])", _escape, text)


def _escape(m: re.Match[str]) -> str:
    code = m.group(1)
    if code[0] in "01234567":
        return chr(int(code, 8))
    if code[0] == "x":
        return chr(int(code[1:], 16))
    return _ESCAPES[code]


def _printf_once(fmt: str, args: list[str]) -> tuple[str, list[str]]:
    """Apply the format to as many arguments as it uses; returns (text, remaining arguments)."""
    out: list[str] = []
    pos = 0
    rest = list(args)

    def take() -> str:
        return rest.pop(0) if rest else ""

    for match in _FORMAT.finditer(fmt):
        out.append(_unescape(fmt[pos:match.start()]))
        pos = match.end()
        flags, width, precision, conv = match.groups()
        if conv == "%":
            out.append("%")
            continue
        if width == "*":
            width = take()
        if precision == "*":
            precision = take()
        value = take()
        spec = "%" + flags + (width or "") + (f".{precision}" if precision is not None else "")
        try:
            if conv in "di":
                out.append((spec + "d") % _to_int(value))
            elif conv in "ouxX":
                out.append((spec + conv) % _to_int(value))
            elif conv in "eEfgG":
                out.append((spec + conv) % (float(value) if value else 0.0))
            elif conv == "c":
                out.append((spec + "s") % value[:1])
            elif conv == "b":
                out.append((spec + "s") % _unescape(value))
            elif conv == "q":
                out.append((spec + "s") % _quote(value))
            else:
                out.append((spec + "s") % value)
        except ValueError:
            raise ModuleError(f"printf: {value!r}: expected a number for %{conv}") from None
    out.append(_unescape(fmt[pos:]))
    return "".join(out), rest


def _to_int(value: str) -> int:
    value = value.strip()
    if not value:
        return 0
    if value[:1] in ("'", '"') and len(value) >= 2:
        return ord(value[1])
    return int(value, 0) if value.lower().startswith(("0x", "-0x", "0o", "-0o", "0b", "-0b")) else int(value)


def _quote(value: str) -> str:
    return value if re.fullmatch(r"[A-Za-z0-9_./:=@+-]+", value) else "'" + value.replace("'", "'\\''") + "'"


@builtin("printf", "Print formatted text", "printf FORMAT [ARG...]", category="text", doc="""\
# printf

Print ARGs according to FORMAT, as the C / bash `printf`. The format is reused until every
argument is consumed, so `printf "%s\\n" a b c` prints three lines. No newline is added.

## Conversions

| Conversion | Meaning |
| --- | --- |
| `%s` | A string. `%-10s` pads to 10 columns on the right, `%.3s` cuts to 3 characters. |
| `%d`, `%i` | An integer (`%05d` zero-pads, `%+d` always shows the sign). Also `0x1f` and `'a` (its code). |
| `%x`, `%X`, `%o` | Hexadecimal / octal. |
| `%f`, `%e`, `%g` | Floating point (`%.2f`). |
| `%c` | The first character of the argument. |
| `%b` | A string with its own `\\n`-style escapes interpreted. |
| `%q` | The argument quoted so that it can be reused as shell input. |
| `%%` | A percent sign. |

Escapes in FORMAT: `\\n`, `\\t`, `\\r`, `\\\\`, `\\a`, `\\e`, `\\0`.

## Examples

```
printf "%-12s %6.2f\\n" apples 3.5 pears 12
printf "%05d\\n" 42
printf "%s\\n" *.py
```
""")
def printf(ctx: ShellContext, args: list[str], stdin: str) -> str:
    if not args:
        raise ModuleError("printf: missing FORMAT (usage: printf FORMAT [ARG...])")
    fmt, rest = args[0], args[1:]
    text, rest = _printf_once(fmt, rest)
    uses_args = any(m.group(4) != "%" for m in _FORMAT.finditer(fmt))
    while rest and uses_args:
        more, rest = _printf_once(fmt, rest)
        text += more
    return text


# ── seq ──────────────────────────────────────────────────────────────────────

@builtin("seq", "Print a sequence of numbers", "seq [-s SEP] [-w] [FIRST [STEP]] LAST", category="text", doc="""\
# seq

Print the numbers from FIRST (default 1) to LAST, in steps of STEP (default 1), one per line.

| Option | Meaning |
| --- | --- |
| `-s SEP` | Separate the numbers with SEP instead of a new line. |
| `-w` | Pad with leading zeros to the same width. |

## Examples

```
seq 5
seq 0 10 100
seq -w 1 10
seq -s , 1 5
for n in $(seq 1 3); do echo $n; done
```
""")
def seq(ctx: ShellContext, args: list[str], stdin: str) -> str:
    parser = ArgParser("seq")
    parser.add_argument("-s", "--separator", default="\n", metavar="SEP")
    parser.add_argument("-w", "--equal-width", action="store_true")
    parser.add_argument("numbers", nargs="+")
    opts = parser.parse_args(args)
    if len(opts.numbers) > 3:
        raise ModuleError("seq: too many arguments (usage: seq [FIRST [STEP]] LAST)")
    try:
        values = [float(n) if "." in n else int(n) for n in opts.numbers]
    except ValueError as exc:
        raise ModuleError(f"seq: not a number: {exc.args[0].split(':')[-1].strip()}") from None
    first, step, last = (1, 1, values[0]) if len(values) == 1 else (values[0], 1, values[1]) if len(values) == 2 \
        else (values[0], values[1], values[2])
    if step == 0:
        raise ModuleError("seq: STEP must not be 0")
    if len(values) == 2 and last < first:
        return ""
    out: list[Any] = []
    n = first
    while (step > 0 and n <= last + 1e-9) or (step < 0 and n >= last - 1e-9):
        out.append(n)
        n += step
        if len(out) > 1_000_000:
            raise ModuleError("seq: more than 1,000,000 numbers")
    if isinstance(first, float) or isinstance(step, float):
        decimals = max(len(str(x).split(".")[1]) if "." in str(x) else 0 for x in (first, step))
        texts = [f"{x:.{decimals}f}" for x in out]
    else:
        texts = [str(x) for x in out]
    if opts.equal_width and texts:
        width = max(len(t) for t in texts)
        texts = [t.rjust(width, "0") if not t.startswith("-") else "-" + t[1:].rjust(width - 1, "0") for t in texts]
    sep = _unescape(opts.separator)
    return sep.join(texts) + ("\n" if texts else "")


# ── xargs ────────────────────────────────────────────────────────────────────

XARGS_DOC = """# xargs

Build and run a command from stdin: the input is split into items (at whitespace, or at new
lines with `-L` / `-I`) and added to the end of COMMAND's arguments.

## Usage

```
... | xargs COMMAND [ARG...]            # one run with every item appended
... | xargs -n N COMMAND [ARG...]       # a run per N items
... | xargs -I {} COMMAND ... {} ...    # a run per line, {} replaced by the line
```

## Options

| Option | Meaning |
| --- | --- |
| `-n N` | Use at most N items per command run. |
| `-I STR` | One run per input line, with every STR in the arguments replaced by the line. |
| `-L N` | Use N lines per run (items stay split at whitespace). |
| `-r` | Run nothing when the input is empty. |
| `-t` | Print each command line before running it. |
| `-0` | Items are separated by NUL characters (as `find -print0` writes them). |

Each run is a ShellCraft command (a builtin or a module), with its output printed in order. A
failing run shows its error and the next one runs; `$?` is 123 when any run failed, as in GNU
xargs.

## Examples

```
find . -name "*.log" | xargs rm
cat hosts.txt | xargs -n 1 queryDns
ls *.md | xargs -I {} echo "doc: {}"
```
"""


@builtin("xargs", "Run a command with items read from stdin as arguments",
         "xargs [-n N] [-I STR] [-L N] [-r] [-t] COMMAND [ARG...]", category="shell", doc=XARGS_DOC)
def xargs(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    from shellcraft.pipeline import PipelineError, run_argv

    parser = ArgParser("xargs")
    parser.add_argument("-n", "--max-args", type=int, metavar="N")
    parser.add_argument("-I", dest="replace", metavar="STR")
    parser.add_argument("-L", "--max-lines", type=int, metavar="N")
    parser.add_argument("-r", "--no-run-if-empty", action="store_true")
    parser.add_argument("-t", "--verbose", action="store_true")
    parser.add_argument("-0", "--null", action="store_true")
    parser.add_argument("command", nargs="*")
    opts = parser.parse_args(args)
    command = opts.command or ["echo"]
    if opts.max_args is not None and opts.max_args < 1:
        raise ModuleError("xargs: -n must be >= 1")

    if opts.null:
        items = [i for i in stdin.split("\0") if i]
        batches = [items[k:k + opts.max_args] for k in range(0, len(items), opts.max_args)] if opts.max_args \
            else [items]
    elif opts.replace:
        batches = [[line] for line in stdin.splitlines() if line.strip()]
    elif opts.max_lines:
        lines = [ln for ln in stdin.splitlines() if ln.strip()]
        batches = [sum((ln.split() for ln in lines[k:k + opts.max_lines]), [])
                   for k in range(0, len(lines), opts.max_lines)]
    else:
        items = stdin.split()
        batches = [items[k:k + opts.max_args] for k in range(0, len(items), opts.max_args)] if opts.max_args \
            else [items]
    if not batches or (not any(batches) and opts.no_run_if_empty):
        return ""
    if not any(batches):
        batches = [[]]

    plain: list[str] = []
    failed = False
    for batch in batches:
        if opts.replace:
            argv = [a.replace(opts.replace, batch[0]) for a in command]
        else:
            argv = command + batch
        if opts.verbose:
            plain.append("+ " + " ".join(_quote(a) for a in argv))
        try:
            result = run_argv(argv, ctx)
        except PipelineError as exc:
            failed = True
            message = exc.message if not exc.message.startswith(f"{exc.name}: ") else exc.message[len(exc.name) + 2:]
            plain.append(f"✗ {exc.name}: {message}")
            continue
        text = to_text(result)
        if text:
            plain.append(text.rstrip("\n"))
    output = lines_out(plain)
    return WithStatus(output, 123) if failed else output


# ── paths ────────────────────────────────────────────────────────────────────

@builtin("basename", "Strip the directory (and a suffix) from a path", "basename PATH [SUFFIX] | basename -a PATH...",
         category="files", doc="""\
# basename

Print PATH without its leading directories, and without SUFFIX if it ends with it.

| Option | Meaning |
| --- | --- |
| `-a` | Several PATHs, one result per line. |
| `-s SUFFIX` | Remove SUFFIX (implies `-a`). |

## Examples

```
basename /var/log/app.log          # app.log
basename /var/log/app.log .log     # app
basename -s .py shellcraft/cli.py shellcraft/shell.py
```
""")
def basename(ctx: ShellContext, args: list[str], stdin: str) -> str:
    parser = ArgParser("basename")
    parser.add_argument("-a", "--multiple", action="store_true")
    parser.add_argument("-s", "--suffix", metavar="SUFFIX")
    parser.add_argument("paths", nargs="+")
    opts = parser.parse_args(args)
    suffix = opts.suffix
    paths = opts.paths
    if not opts.multiple and suffix is None:
        if len(paths) > 2:
            raise ModuleError("basename: too many arguments (use -a for several paths)")
        if len(paths) == 2:
            paths, suffix = paths[:1], paths[1]
    out = []
    for p in paths:
        name = os.path.basename(p.rstrip("/\\")) or p
        if suffix and name.endswith(suffix) and name != suffix:
            name = name[:-len(suffix)]
        out.append(name)
    return lines_out(out)


@builtin("dirname", "Strip the last part from a path", "dirname PATH...", category="files", doc="""\
# dirname

Print PATH without its last component (`.` when there is none).

## Examples

```
dirname /var/log/app.log     # /var/log
dirname app.log              # .
```
""")
def dirname(ctx: ShellContext, args: list[str], stdin: str) -> str:
    if not args:
        raise ModuleError("dirname: missing PATH")
    return lines_out(os.path.dirname(p.rstrip("/\\")) or ("/" if p.startswith("/") else ".") for p in args)


@builtin("realpath", "Print the absolute path, with symlinks resolved", "realpath [-s] [--relative-to DIR] PATH...",
         category="files", doc="""\
# realpath

Print each PATH as an absolute path, `..` and symbolic links resolved.

| Option | Meaning |
| --- | --- |
| `-s` | Don't resolve symbolic links (just make the path absolute and clean). |
| `-e` | Every PATH must exist. |
| `--relative-to DIR` | Print the path relative to DIR. |

## Examples

```
realpath .
realpath ../modules/myip.py
realpath --relative-to ~ ~/projects/x
```
""")
def realpath(ctx: ShellContext, args: list[str], stdin: str) -> str:
    parser = ArgParser("realpath")
    parser.add_argument("-s", "--strip", "--no-symlinks", action="store_true")
    parser.add_argument("-e", "--canonicalize-existing", action="store_true")
    parser.add_argument("--relative-to", metavar="DIR")
    parser.add_argument("paths", nargs="+")
    opts = parser.parse_args(args)
    out = []
    for p in opts.paths:
        path = user_path(p)
        if opts.canonicalize_existing and not path.exists():
            raise ModuleError(f"realpath: {p}: no such file or directory")
        resolved = Path(os.path.abspath(path)) if opts.strip else Path(os.path.realpath(path))
        if opts.relative_to:
            base = Path(os.path.realpath(user_path(opts.relative_to)))
            try:
                out.append(str(resolved.relative_to(base)) if resolved != base else ".")
            except ValueError:
                out.append(os.path.relpath(resolved, base))
        else:
            out.append(str(resolved))
    return lines_out(out)


@builtin("stat", "Show a file's size, permissions and times", "stat [-c FORMAT] PATH...", category="files", doc="""\
# stat

Show what the filesystem knows about each PATH: type, size, permissions, owner, links,
inode and the access / modification / change times.

| Option | Meaning |
| --- | --- |
| `-c FORMAT` | Print FORMAT instead, with `%n` name, `%s` size, `%F` type, `%a` octal mode, `%A` mode text, `%U` owner, `%y` modified time, `%Y` modified time as seconds, `%i` inode, `%h` links. |

## Examples

```
stat shellcraft/cli.py
stat -c "%s %n" *.py
```
""")
def stat(ctx: ShellContext, args: list[str], stdin: str) -> Styled | str:
    parser = ArgParser("stat")
    parser.add_argument("-c", "--format", metavar="FORMAT")
    parser.add_argument("paths", nargs="+")
    opts = parser.parse_args(args)
    plain: list[str] = []
    styled = Text()
    for p in opts.paths:
        path = user_path(p)
        try:
            st = path.lstat()
        except OSError as exc:
            raise ModuleError(f"stat: {p}: {exc.strerror or 'cannot stat'}") from None
        kind = ("symbolic link" if statmod.S_ISLNK(st.st_mode) else "directory" if statmod.S_ISDIR(st.st_mode)
                else "regular file" if statmod.S_ISREG(st.st_mode) else "special file")
        try:
            import pwd

            owner = pwd.getpwuid(st.st_uid).pw_name
        except (ImportError, KeyError):
            owner = str(st.st_uid)
        times = {k: datetime.fromtimestamp(getattr(st, f"st_{k}time")).strftime("%Y-%m-%d %H:%M:%S")
                 for k in ("a", "m", "c")}
        if opts.format:
            fields = {"n": p, "s": str(st.st_size), "F": kind, "a": f"{statmod.S_IMODE(st.st_mode):o}",
                      "A": statmod.filemode(st.st_mode), "U": owner, "y": times["m"], "Y": str(int(st.st_mtime)),
                      "i": str(st.st_ino), "h": str(st.st_nlink), "%": "%"}
            line = re.sub(r"%([nsFaAUyYih%])", lambda m: fields[m.group(1)], opts.format)
            plain.append(line)
            styled.append(line + "\n")
            continue
        plain += [f"  File: {p}", f"  Size: {st.st_size}\tLinks: {st.st_nlink}\tType: {kind}",
                  f"Access: ({statmod.S_IMODE(st.st_mode):04o}/{statmod.filemode(st.st_mode)})  Owner: {owner}  "
                  f"Inode: {st.st_ino}",
                  f"Access: {times['a']}", f"Modify: {times['m']}", f"Change: {times['c']}"]
        styled.append_text(Text.assemble(("  File: ", "sc.muted"), (p, "sc.path"), "\n",
                                         ("  Size: ", "sc.muted"), str(st.st_size), ("   Links: ", "sc.muted"),
                                         str(st.st_nlink), ("   Type: ", "sc.muted"), kind, "\n",
                                         ("Access: ", "sc.muted"),
                                         f"({statmod.S_IMODE(st.st_mode):04o}/{statmod.filemode(st.st_mode)})",
                                         ("   Owner: ", "sc.muted"), owner, ("   Inode: ", "sc.muted"), str(st.st_ino),
                                         "\n", ("Access: ", "sc.muted"), times["a"], "\n", ("Modify: ", "sc.muted"),
                                         times["m"], "\n", ("Change: ", "sc.muted"), times["c"], "\n"))
    styled.rstrip()
    return Styled(styled, lines_out(plain))


# ── who / where / what ───────────────────────────────────────────────────────

@builtin("whoami", "Print the current user name", "whoami", category="info")
def whoami(ctx: ShellContext, args: list[str], stdin: str) -> str:
    try:
        return getpass.getuser() + "\n"
    except Exception as exc:  # noqa: BLE001
        raise ModuleError(f"whoami: cannot determine the user name: {exc}") from None


@builtin("hostname", "Print the machine's host name", "hostname [-f]", category="info", doc="""\
# hostname

Print the machine's name. `-f` prints the fully qualified name when the system knows it.
""")
def hostname(ctx: ShellContext, args: list[str], stdin: str) -> str:
    if args not in ([], ["-f"], ["--fqdn"]):
        raise ModuleError("hostname: usage: hostname [-f]")
    return (socket.getfqdn() if args else socket.gethostname()) + "\n"


@builtin("uname", "Print the operating system and machine", "uname [-a] [-s] [-n] [-r] [-m] [-p]", category="info",
         doc="""\
# uname

Print the operating system name. The same options as Unix `uname`:

| Option | Meaning |
| --- | --- |
| `-a` | Everything: system, host, release, version, machine. |
| `-s` | The system name (`Linux`, `Windows`, `Darwin`); the default. |
| `-n` | The host name. |
| `-r` | The kernel / OS release. |
| `-v` | The OS version. |
| `-m` | The machine type (`x86_64`, `arm64`). |
| `-p` | The processor. |
""")
def uname(ctx: ShellContext, args: list[str], stdin: str) -> str:
    parser = ArgParser("uname")
    for flag, dest in (("-a", "all"), ("-s", "system"), ("-n", "node"), ("-r", "release"), ("-v", "version"),
                       ("-m", "machine"), ("-p", "processor")):
        parser.add_argument(flag, dest=dest, action="store_true")
    opts = parser.parse_args(args)
    u = platform.uname()
    parts = [("system", u.system), ("node", u.node), ("release", u.release), ("version", u.version),
             ("machine", u.machine), ("processor", u.processor or u.machine)]
    chosen = [v for k, v in parts if opts.all or getattr(opts, k)] or [u.system]
    return " ".join(chosen) + "\n"


# ── sleep / time ─────────────────────────────────────────────────────────────

@builtin("sleep", "Wait for a number of seconds", "sleep SECONDS", category="shell", doc="""\
# sleep

Wait SECONDS (a decimal number, or with a unit: `2s`, `1m`, `0.5`). Ctrl-C stops it.
""")
def sleep(ctx: ShellContext, args: list[str], stdin: str) -> str:
    if len(args) != 1:
        raise ModuleError("sleep: usage: sleep SECONDS")
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([smhd]?)", args[0])
    if not match:
        raise ModuleError(f"sleep: invalid time '{args[0]}' (e.g. 2, 0.5, 3s, 1m)")
    seconds = float(match.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2)]
    from shellcraft import streams

    streams.wait(seconds)  # wakes (Cancelled) when its pipeline or background job is stopped
    return ""


@builtin("time", "Run a command and report how long it took", "time COMMAND [ARG...]", category="shell",
         stateful=True, doc="""\
# time

Run COMMAND and print how long it took (`real` time, in seconds) after its output. In a
pipeline, `time` only measures its own segment: `time cat big.log | wc -l` times the `cat`.

The prompt also shows `⏱` with the duration of any command that took more than 2 seconds.

## Examples

```
time find / -name "*.conf"
time queryDns example.com
```
""")
def time_cmd(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    from shellcraft.pipeline import PipelineError, run_argv

    if not args:
        raise ModuleError("time: usage: time COMMAND [ARG...]")
    started = time.monotonic()
    error: PipelineError | None = None
    try:
        result = run_argv(args, ctx, stdin)
    except PipelineError as exc:
        error, result = exc, ""
    elapsed = time.monotonic() - started
    status = result.status if isinstance(result, WithStatus) else (error.status if error else 0)
    value = result.value if isinstance(result, WithStatus) else result
    text = to_text(value)
    if text and not text.endswith("\n"):
        text += "\n"
    line = f"real {elapsed:.3f}s"
    if error is not None:
        message = error.message
        if message.startswith(f"{error.name}: "):
            message = message[len(error.name) + 2:]
        text += f"✗ {error.name}: {message}\n"
    styled = Text.from_ansi(text.rstrip("\n")) if text else Text()
    if text:
        styled.append("\n")
    styled.append_text(Text.assemble(("real ", "sc.muted"), (f"{elapsed:.3f}s", "sc.accent")))
    output = Styled(styled, text + line + "\n")
    return WithStatus(output, status) if status else output


# ── command ──────────────────────────────────────────────────────────────────

@builtin("command", "Run the OS program NAME even when a builtin or module has the name",
         "command NAME [ARG...] | command -v NAME", category="shell", streams=True, doc="""\
# command

Run the operating system's program `NAME`, skipping ShellCraft's builtins, modules, aliases and
functions of the same name, as bash's `command` does. Useful where ShellCraft has its own command
with a program's name: `curl` is ShellCraft's (on Python requests), `command curl` is the system's.

Like any OS program, it needs OS commands on (`settings system_commands on`, or `--allow-system`).

## Usage

```
command NAME [ARG...]     # run the OS program NAME
command -v NAME           # the program's path (nothing, and status 1, when there is none)
```

## Examples

```
command curl -sI https://example.com
command -v curl
command ls -la | grep conf
```
""")
def command(ctx: ShellContext, args: list[str], stdin: Any) -> Any:
    from shellcraft.parser import Command
    from shellcraft.pipeline import _resolve_system, _stream_system

    if args[:1] == ["-v"]:
        if len(args) != 2:
            raise CommandError("command: usage: command -v NAME")
        found = shutil.which(args[1])
        return f"{found}\n" if found else WithStatus("", 1)
    if not args:
        raise CommandError("command: usage: command NAME [ARG...] | command -v NAME")
    argv = _resolve_system(Command(args[0], list(args[1:])))
    if argv is None:
        raise CommandError(f"command: {args[0]}: no such program on the PATH", 127)
    if not ctx.allow_system:
        raise CommandError(f"command: {args[0]}: OS commands are off — run: settings system_commands on", 127)
    return _stream_system(argv, stdin)


# ── type / export / unset ────────────────────────────────────────────────────

@builtin("type", "Say what kind of command a name is", "type [-t] NAME...", category="info", doc="""\
# type

Tell whether each NAME is an alias, a shell function, a shell keyword, a builtin, a module or a
program on PATH, in bash's words (`ls is a shell builtin`); a function is shown with its body.
`-t` prints just the kind: `alias`, `function`, `keyword`, `builtin`, `module` or `file`. `which`
shows the same, with file paths.
""")
def type_cmd(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    import shutil

    from shellcraft.highlight import KEYWORDS

    parser = ArgParser("type")
    parser.add_argument("-t", dest="terse", action="store_true")
    parser.add_argument("names", nargs="+")
    opts = parser.parse_args(args)
    lines: list[str] = []
    missing = 0
    for name in opts.names:
        value = aliases.get_all(ctx.config).get(name) if ctx.allow_aliases else None
        if value is not None:
            lines.append("alias" if opts.terse else f"{name} is aliased to `{value}'")
        elif ctx.allow_aliases and name in ctx.functions:
            lines.append("function" if opts.terse else f"{name} is a function\n{ctx.functions[name].text}")
        elif name in KEYWORDS and name not in ("{", "}", "!", "-py", "in"):
            lines.append("keyword" if opts.terse else f"{name} is a shell keyword")
        elif name in BUILTINS:
            lines.append("builtin" if opts.terse else f"{name} is a shell builtin")
        elif (spec := ctx.registry.get(name)) is not None:
            lines.append("module" if opts.terse else f"{name} is a ShellCraft module ({spec.path})")
        elif (path := shutil.which(name)) is not None:
            note = "" if ctx.allow_system else " (OS commands are off)"
            lines.append("file" if opts.terse else f"{name} is {path}{note}")
        else:
            missing += 1
            if not opts.terse:
                lines.append(f"{name}: not found")
    output = lines_out(lines)
    return WithStatus(output, 1) if missing else output


EXPORT_DOC = """# export, unset

Shell variables. `NAME=VALUE` on a line of its own sets a variable for this session; `$NAME`
then expands in every command line, as in bash. `export` makes it an environment variable as
well, so modules and OS programs see it; `unset` removes it.

## Usage

```
NAME=VALUE                 # set a shell variable (no spaces around =)
export NAME=VALUE          # set it and export it to the environment
export NAME                # export an existing variable
export                     # list the exported variables
unset NAME...              # remove variables (from the environment too)
unset -f NAME...           # remove shell functions (man function)
```

`$HOME`, `$PATH` and other environment variables expand in the interactive shell without an
`export`. Over MCP, only variables set in the script itself expand, so a client can't read
secrets with `$NAME`. Arithmetic: `$((n + 1))`, see `man if` for the operators.

## Examples

```
host=example.com
queryDns $host
export API_URL=https://api.example.com
n=$((n + 1))
```
"""


@builtin("export", "Set a variable and export it to the environment", "export [NAME[=VALUE]...]", category="shell",
         stateful=True, doc=EXPORT_DOC)
def export(ctx: ShellContext, args: list[str], stdin: str) -> str:
    if not args:
        return lines_out(f"declare -x {k}=\"{v}\"" for k, v in sorted(ctx.exported.items()))
    for arg in args:
        name, sep, value = arg.partition("=")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise CommandError(f"export: '{name}': not a valid identifier")
        if sep:
            ctx.variables[name] = os.path.expanduser(value) if value.startswith("~") else value  # as bash does
        elif name not in ctx.variables:
            if name in os.environ:
                ctx.variables[name] = os.environ[name]
            else:
                raise CommandError(f"export: {name} is not set")
        ctx.exported[name] = ctx.variables[name]
        if ctx.variables[name] != os.environ.get(name) or name in ctx.env_vars:
            envvars.set_var(ctx, name, ctx.variables[name], how="export")  # listed by `env`
    return ""


@builtin("unset", "Remove shell variables (or functions: -f)", "unset [-v] NAME... | unset -f NAME...",
         category="shell", stateful=True, doc=EXPORT_DOC)
def unset(ctx: ShellContext, args: list[str], stdin: str) -> str:
    if args[:1] == ["-f"]:
        for name in args[1:]:
            ctx.functions.pop(name, None)
        return ""
    if args[:1] == ["-v"]:
        args = args[1:]
    if not args:
        raise CommandError("unset: usage: unset NAME... | unset -f NAME...")
    for name in args:
        ctx.variables.pop(name, None)
        if ctx.exported.pop(name, None) is not None or name in ctx.env_vars:
            os.environ.pop(name, None)  # bash: gone for the session (`env unset` restores a host value)
        envvars.forget(ctx, name)
    return ""


@builtin("declare", "Show shell functions: declare -f [NAME...] / -F", "declare -f [NAME...] | declare -F",
         category="shell", stateful=True, doc="""\
# declare

Show the shell functions defined in this session (`man function`).

```
declare -f            # every function, as it was defined
declare -f NAME...    # just these
declare -F            # just the names
```

Only `-f` and `-F` are supported; for variables use `NAME=VALUE`, `export` and `unset`.
""")
def declare(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    if not args or args[0] not in ("-f", "-F"):
        raise CommandError("declare: only -f and -F are supported (variables: NAME=VALUE, export, unset)")
    names = args[1:] or sorted(ctx.functions)
    missing = [n for n in names if n not in ctx.functions]
    if args[0] == "-F":
        text = lines_out(f"declare -f {n}" for n in names if n in ctx.functions)
    else:
        text = "".join(ctx.functions[n].text.rstrip() + "\n" for n in names if n in ctx.functions)
    return WithStatus(text, 1) if missing else text


@builtin("shift", "Drop a shell function's first arguments", "shift [N]", category="shell", stateful=True,
         doc="""\
# shift

Inside a shell function, drop the first N arguments (default 1): `$2` becomes `$1`, and `$#`, `$@`
and `$*` follow. With fewer than N arguments nothing changes and the status is 1, as in bash.

```
each() { while [ $# -gt 0 ]; do echo "arg: $1"; shift; done; }
each a "b c" d
```

Outside a function `shift` is an error. See `man function`.
""")
def shift(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    if not ctx.positional:
        raise CommandError("shift: can only be used in a function")
    if len(args) > 1 or (args and not args[0].isdigit()):
        raise CommandError("shift: usage: shift [N]")
    count = int(args[0]) if args else 1
    current = ctx.positional[-1]
    if count > len(current):
        return WithStatus("", 1)
    del current[:count]
    return ""


GETOPTS_DOC = """# getopts

Read a function's options, like `-v` and `-f FILE`, one at a time, as in bash.

## Usage

```
getopts OPTSTRING NAME           # read the next option from the arguments ($@)
getopts OPTSTRING NAME ARG...    # read them from these words instead
```

OPTSTRING lists the option letters. A letter followed by `:` takes a value: `vf:` means `-v`
and `-f FILE`. Each call sets NAME to the next option's letter and `OPTARG` to its value, and
returns status 0. When the options run out it returns 1, so it fits in a `while` loop:

```
backup() {
    local verbose=no file=notes.txt
    while getopts "vf:" opt; do
        case $opt in
            v) verbose=yes ;;
            f) file=$OPTARG ;;
            \\?) return 2 ;;
        esac
    done
    shift $((OPTIND - 1))        # now $1… are what came after the options
    echo "verbose=$verbose file=$file rest=$@"
}
backup -v -f todo.txt extra      # verbose=yes file=todo.txt rest=extra
```

Options can be grouped and the value can be glued on: `-vf todo.txt` and `-ftodo.txt` work
too. The options stop at the first word that doesn't start with `-`, or at `--`, which is
skipped.

## OPTIND

`OPTIND` is the number of the next argument to read. It starts at 1, and each function call,
`def` and func gets its own (the caller's comes back when it ends), so functions that read
options can call each other. After the loop, `shift $((OPTIND - 1))` drops the options. Set
`OPTIND=1` to read the options again.

## Mistakes

For an option that isn't in OPTSTRING, NAME is `?` and you see `getopts: illegal option -- x`.
For a missing value, NAME is `?` and you see `getopts: option requires an argument -- f`.
`OPTARG` is unset in both cases.

Start OPTSTRING with `:` to handle them yourself, without a message: for an unknown option NAME
is `?` and `OPTARG` is the letter; for a missing value NAME is `:` and `OPTARG` is the letter.

```
while getopts ":vf:" opt; do
    case $opt in
        v) echo verbose ;;
        f) echo "file $OPTARG" ;;
        :) echo "-$OPTARG needs a value" ;;
        \\?) echo "no option -$OPTARG" ;;
    esac
done
```

In a `case`, write `\\?)` or `"?")` for the `?` arm: a bare `?)` matches any single letter.
"""


@builtin("getopts", "Read a function's options (-v, -f FILE) one at a time", "getopts OPTSTRING NAME [ARG...]",
         category="shell", stateful=True, doc=GETOPTS_DOC)
def getopts_(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    from shellcraft import getopts
    from shellcraft.pipeline import write_stderr

    if len(args) < 2:
        raise CommandError("getopts: usage: getopts OPTSTRING NAME [ARG...]", 2)
    optstring, name, words = args[0], args[1], args[2:]
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
        raise CommandError(f"getopts: '{name}': not a valid variable name", 2)
    if not words:
        words = list(ctx.positional[-1]) if ctx.positional else []
    found, value, optarg, error = getopts.next_option(ctx, optstring, words)
    ctx.variables[name] = value
    if optarg is None:
        ctx.variables.pop("OPTARG", None)
    else:
        ctx.variables["OPTARG"] = optarg
    if error:
        write_stderr(ctx, error)
    return "" if found else WithStatus("", 1)


SET_DOC = """# set

Shell options, as in bash, and the arguments (`$1`, `$2`…).

## Usage

```
set -e                     # stop at the first command that fails (errexit)
set -u                     # an unset $name is an error (nounset)
set -x                     # show each command before it runs (xtrace)
set -o pipefail            # a pipe fails when any part of it fails
set +e                     # + turns an option off again
set -eu                    # several at once; set -euo pipefail works too
set -o errexit             # the long names: errexit, nounset, xtrace, pipefail
set -o                     # list the options and whether they are on
set +o                     # the same, as commands that turn them back on
set -- A B C               # replace the arguments: $1 is A, $# is 3
set --                     # no arguments
set                        # list the shell variables
```

## -e: stop on errors

After `set -e`, a command that fails stops what is running: the rest of the line typed at the
prompt, the function, the `for` / `if` / `while` block, a file run with `source`, the rc file, a
startup script or a func. It never closes the shell. `$?` keeps the failing command's status.

A failure doesn't stop anything when it is an answer rather than an error:

- the test of an `if`, `elif`, `while` or `until` (and the `-py` conditions);
- a command before `&&` or `||` (`[ -f x ] && echo found`), only the last one counts;
- a command after `!`.

```
set -e
backup() { cp notes.txt /backup/; echo "saved"; }    # no "saved" if cp fails
```

## -u: unset variables

After `set -u`, `$name` or `${name}` for a variable that isn't set is an error,
`NAME: unbound variable` (status 1), and it stops what is running as `-e` does.
`${name:-default}`, `$?`, `$#`, `$@` and `$1`… never are. It catches typos such as `$fiel`.

## -x: trace

After `set -x`, each command is shown on the error stream before it runs, with its variables
expanded: `+ echo hello world`. `set +x` turns it off.

## -o pipefail

Normally a pipe's status is its last command's: `grep x missing.txt | sort` succeeds because
`sort` does. With `set -o pipefail` it is the status of the last part that failed, so the
failure shows (and `set -e` stops on it).

Options last for the session (or until you turn them off). `func run -i` puts them back when
the func ends. Put `set -e` in your rc file to stop it at the first failing line.
"""

_SET_LETTERS = {"e": "errexit", "u": "nounset", "x": "xtrace"}
SET_OPTIONS = ("errexit", "nounset", "pipefail", "xtrace")


@builtin("set", "Shell options (-e, -u, -x, -o pipefail) and the arguments", "set [-eux] [-o NAME] [-- ARG...]",
         category="shell", stateful=True, doc=SET_DOC)
def set_options(ctx: ShellContext, args: list[str], stdin: str) -> str:
    if not args:
        from shellcraft.pipeline import _trace_word

        return lines_out([f"{k}={_trace_word(v)}" for k, v in sorted(ctx.variables.items())])
    changes: list[tuple[str, bool]] = []
    listing = ""
    positional: list[str] | None = None
    i = 0
    while i < len(args):
        arg = args[i]
        i += 1
        if arg == "--":
            positional = args[i:]
            break
        if len(arg) < 2 or arg[0] not in "-+":
            positional = args[i - 1:]  # `set a b`: the arguments, as in bash
            break
        on = arg[0] == "-"
        for letter in arg[1:]:
            if letter == "o":
                if i >= len(args) or args[i][:1] in ("-", "+"):
                    listing = "on" if on else "commands"  # `set -o` / `set +o`: list them
                    continue
                name = args[i]
                i += 1
                if name not in SET_OPTIONS:
                    raise CommandError(f"set: {name}: unknown option (the options: {', '.join(SET_OPTIONS)})", 2)
                changes.append((name, on))
            elif letter in _SET_LETTERS:
                changes.append((_SET_LETTERS[letter], on))
            else:
                raise CommandError(f"set: {arg[0]}{letter}: unknown option (-e, -u, -x, -o NAME)", 2)
    for name, on in changes:
        if on:
            ctx.options.add(name)
        else:
            ctx.options.discard(name)
    if positional is not None:
        if ctx.positional:
            ctx.positional[-1][:] = positional  # inside a function or func: its own arguments
        else:
            ctx.positional.append(list(positional))
    if listing == "on":
        return lines_out([f"{name:<15}{'on' if name in ctx.options else 'off'}" for name in SET_OPTIONS])
    if listing:
        return lines_out([f"set {'-' if name in ctx.options else '+'}o {name}" for name in SET_OPTIONS])
    return ""


TRAP_DOC = """# trap

Run a command when something ends, on Ctrl-C, or after a command fails. Use it to clean up.

## Usage

```
trap 'COMMANDS' EXIT       # when this function, sourced file or func ends (at the prompt: when you leave)
trap 'COMMANDS' INT        # when Ctrl-C stops it
trap 'COMMANDS' ERR        # after a command fails
trap 'COMMANDS' EXIT INT   # several at once
trap - EXIT                # remove it
trap '' INT                # do nothing on Ctrl-C (it still stops)
trap                       # list the traps set here (trap -p does the same)
trap -l                    # the signal names
```

Signal names don't care about case: `exit`, `SIGINT` and `0` (for EXIT) work too.

## Where a trap belongs

A trap belongs to what was running when you set it: a function call (`name() { …; }` or `def`),
a file run with `source`, a func, or the session (the prompt, the rc file and startup scripts). Each has
its own, and they go away when it ends.

- **EXIT** runs when that ends, however it ends: at the end, on `return`, when `set -e` stops it,
  on an error or on Ctrl-C. One set at the prompt, in the rc file or in a startup script belongs
  to the session: it runs when you leave ShellCraft.
- **INT** runs when Ctrl-C stops it. Then it stops as it always does (status 130). Without an INT
  trap of its own, the one of whatever called it runs.
- **ERR** runs after a command fails, where `set -e` would stop: not in an `if` or `while` test,
  on the left of `&&` / `||` or after `!`. Without one of its own, the caller's runs. A command
  that fails inside a trap doesn't run ERR again.

The command runs right there: it sees the variables (`local` ones too) and `$1`. In ERR, `$?` is
the failing command's status, and it is put back afterwards.

```
build() {
    local tmp=build.tmp
    trap 'rm -f $tmp' EXIT       # removed even when a step fails
    set -e
    grep -v '^#' notes.txt > $tmp
    sort $tmp > sorted.txt
}
```
"""


@builtin("trap", "Run a command when a function or file ends, on Ctrl-C or on errors",
         "trap ['COMMANDS' | - | ''] SIGNAL... | trap [-p | -l]", category="shell", stateful=True, doc=TRAP_DOC)
def trap(ctx: ShellContext, args: list[str], stdin: str) -> str:
    from shellcraft import traps

    if args and args[0] == "--":
        args = args[1:]
    if not args or args[0] == "-p":
        wanted = [_trap_signal(word) for word in args[1:]] if len(args) > 1 else None
        return lines_out(traps.listing(ctx, wanted))
    if args[0] == "-l":
        return lines_out(list(traps.SIGNALS))
    if len(args) < 2:
        raise CommandError("trap: usage: trap 'COMMANDS' SIGNAL... (EXIT, INT, ERR); trap - SIGNAL removes it", 2)
    command, signals = args[0], [_trap_signal(word) for word in args[1:]]
    frame = ctx.traps[-1]
    for name in signals:
        if command == "-":
            frame.pop(name, None)
        else:
            frame[name] = command
    return ""


def _trap_signal(word: str) -> str:
    from shellcraft import traps

    name = traps.signal_name(word)
    if name is None:
        raise CommandError(f"trap: {word}: unknown signal (EXIT, INT, ERR)", 2)
    return name


@builtin("local", "Declare a variable local to a shell function", "local NAME[=VALUE]...", category="shell",
         stateful=True, doc="""\
# local

Inside a shell function, give variables values for the rest of the call only: when the function
returns, each one gets its old value back (or disappears if it had none). See `man function`.

```
f() { local count=0 name=$1; … }
```

Outside a function `local` is an error, as in bash.
""")
def local(ctx: ShellContext, args: list[str], stdin: str) -> str:
    raise CommandError("local: can only be used in a function")
