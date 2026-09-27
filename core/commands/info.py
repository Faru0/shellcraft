"""Information commands: date."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from core.builtins import builtin
from core.modkit import ArgParser, ModuleError

if TYPE_CHECKING:
    from core.context import ShellContext


@builtin("date", "Print the current date and time", "date [-u] [--iso] [+FORMAT]", category="info", doc="""\
# date

Print the current date and time. Use `+FORMAT` for a custom format (Python `strftime` codes,
the same as Unix `date`).

| Option | Meaning |
| --- | --- |
| `-u` | Use UTC instead of local time. |
| `--iso` | ISO 8601 format, e.g. `2026-09-27T14:03:11+02:00`. |
| `+FORMAT` | Custom format, e.g. `+%Y-%m-%d` or `"+%H:%M:%S"`. |

Common codes: `%Y` year · `%m` month · `%d` day · `%H` hour · `%M` minute · `%S` second ·
`%A` weekday · `%B` month name · `%Z` timezone · `%s` unix timestamp.

## Examples

```
date
date +%Y-%m-%d
date -u --iso
date +%F > stamp.txt
```
""")
def date(ctx: ShellContext, args: list[str], stdin: str) -> str:
    fmt = None
    rest = []
    for a in args:
        if a.startswith("+"):
            fmt = a[1:]
        else:
            rest.append(a)
    parser = ArgParser("date")
    parser.add_argument("-u", "--utc", action="store_true")
    parser.add_argument("--iso", action="store_true")
    opts = parser.parse_args(rest)

    now = datetime.now(timezone.utc) if opts.utc else datetime.now().astimezone()
    if opts.iso:
        if fmt is not None:
            raise ModuleError("date: use either --iso or +FORMAT, not both")
        return now.isoformat(timespec="seconds") + "\n"
    if fmt is None:
        fmt = "%a %b %d %H:%M:%S %Z %Y"
    # %s (epoch seconds) isn't portable across platforms' strftime, so handle it here.
    fmt = fmt.replace("%s", str(int(now.timestamp())))
    try:
        return now.strftime(fmt) + "\n"
    except ValueError as exc:
        raise ModuleError(f"date: invalid format {fmt!r}: {exc}") from None
