"""fleet: every machine you watch on one screen, the ones that need you first (shellcraft.fleet)."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.console import Group
from rich.table import Table
from rich.text import Text

from shellcraft import fleet as store
from shellcraft.builtins import builtin
from shellcraft.context import CommandError, Styled
from shellcraft.modkit import ArgParser

if TYPE_CHECKING:
    from shellcraft.context import ShellContext

FLEET_DOC = """# fleet

Every machine you watch on one screen: how it looked in the last round, how long ago that was, what
changed and how many findings are open about it. The ones that need you come first.

## Usage

```
fleet                # one line per host and device
fleet UNIT           # one of them in full: its checks, findings and reports
fleet --json         # the same as JSON, for a script
```

UNIT is the id in the first column (`host:web01`, `device:edr/web01`). The name alone is enough:
`fleet web01`. When a host and a device share a name, the name means the host.

## The columns

| Column | Shows |
| --- | --- |
| unit | `host:NAME` for a host read over ssh, `device:AGGREGATOR/NAME` for a device whose files are on an aggregator. |
| status | `ok`, `warn` or `crit`: what the last round said. `-` when no round has reported on it yet. |
| last round | How long ago that was. `LATE` when it is more than twice the interval of the watch plan. `never` when there was none. |
| changed | How many items were new or gone in the checks of that round. |
| findings | Findings about it in the case file that are not dismissed, with the highest severity. |
| summary | One line from the round. |

## Where the rows come from

- every host with `ai = true` in `ssh-hosts.toml` (`man sshrun`)
- every device in the `devices` table of an aggregator (`man aggregator`)
- every host and device in a watch plan (`man watchplan`)
- anything a round has reported on

The status comes from `report save`, which the AI calls at the end of each host in a watch round
(`man report`). A finding counts for a machine when it has the tag `host:NAME` (or just `NAME`), or
when it is a `host` finding with that name.

`fleet` only reads. The statuses are in `~/.shellcraft/fleet/` and the reports in
`~/.shellcraft/reports/`.
"""

STATUS_STYLES = {"ok": "sc.success", "warn": "sc.warning", "crit": "bold sc.error", "": "sc.muted"}
SEVERITY_STYLES = {"info": "sc.muted", "low": "sc.accent", "medium": "sc.warning", "high": "sc.error",
                   "critical": "bold sc.error"}
EMPTY = ("no machines yet: give a host `ai = true` in ~/.shellcraft/ssh-hosts.toml (man sshrun), "
         "or let a watch round run (man watchplan)")


def _last(unit: store.Unit) -> str:
    if unit.age is None:
        return "never"
    return f"{store.age_text(unit.age)}{' LATE' if unit.late else ''}"


def _found(unit: store.Unit) -> str:
    return f"{unit.findings} {unit.worst}".strip() if unit.findings else "-"


def _summary(unit: store.Unit) -> str:
    if unit.summary:
        return unit.summary
    return f"no round yet (plan {unit.plan})" if unit.plan else "not in a watch plan"


def _number(value: int | None) -> str:
    return "-" if value is None else str(value)


def _overview(units: list[store.Unit]) -> Any:
    if not units:
        return Styled(Text(EMPTY, style="sc.muted"), "")
    table = Table(title="Fleet", title_style="sc.prompt", border_style="sc.border", header_style="sc.accent")
    for column in ("unit", "status", "last round", "changed", "findings", "summary"):
        table.add_column(column, justify="right" if column == "changed" else "left")
    plain = []
    for unit in units:
        last = _last(unit)
        table.add_row(Text(unit.id, style="sc.path"), Text(unit.status or "-", style=STATUS_STYLES[unit.status]),
                      Text(last, style="sc.warning" if unit.late else "sc.muted" if unit.age is None else ""),
                      _number(unit.changed), Text(_found(unit), style=SEVERITY_STYLES.get(unit.worst, "sc.muted")),
                      Text(_summary(unit), style="" if unit.summary else "sc.muted"))
        plain.append("\t".join((unit.id, unit.status or "-", last, _number(unit.changed), _found(unit),
                                _summary(unit))))
    hint = Text.assemble(("fleet UNIT", "sc.accent"), (" shows one in full, ", "sc.muted"),
                         ("report show UNIT", "sc.accent"), (" prints its last report", "sc.muted"))
    return Styled(Group(table, hint), "\n".join(plain) + "\n")


def _ago(stamp: str) -> str:
    when = store.epoch(stamp)
    return "" if when is None else f"{store.age_text(max(0, int(store.now() - when)))} ago"


def _local(stamp: str) -> str:
    when = store.epoch(stamp)
    return stamp if when is None else f"{datetime.fromtimestamp(when):%Y-%m-%d %H:%M}"


def _short(path: str) -> str:
    """A path under your home folder with ~, as you would type it."""
    home = str(Path.home())
    return "~" + path[len(home):] if path.startswith(home + "/") else path


def _check_line(check: store.Check) -> str:
    if check.first:
        return f"first run: {check.present} item(s) learned"
    if not check.new and not check.gone:
        return f"no change ({check.present} item(s))"
    return f"{check.new} new, {check.gone} gone, {check.present} now"


def _detail(found: store.Detail) -> Any:
    unit = found.unit
    out = Text()
    out.append(unit.id, style="sc.prompt")
    out.append(f"  {unit.status or 'no status yet'}", style=STATUS_STYLES[unit.status])
    if unit.age is not None:
        out.append(f"  {store.age_text(unit.age)} ago", style="sc.warning" if unit.late else "")
        out.append("  LATE" if unit.late else "", style="sc.warning")
    plain = [f"{unit.id}\t{unit.status or '-'}\t{_last(unit)}"]
    facts = [unit.summary, unit.about,
             f"watch plan {unit.plan}, every {unit.interval} min" + (f", its last round began {_ago(found.plan_last)}"
                                                                    if found.plan_last else ", no round yet")
             if unit.plan else "not in a watch plan",
             ", ".join(f"{value} {word}" for value, word in ((unit.changed, "changed"), (unit.flags, "flag(s)"))
                       if value is not None)]
    for fact in facts:
        if fact:
            out.append(f"\n{fact}", style="" if fact == unit.summary else "sc.muted")
            plain.append(fact)
    if found.checks:
        out.append("\n\nchecks", style="sc.accent")
        out.append("  what `baseline` remembers, and what its last look changed", style="sc.muted")
        width = max(len(check.name) for check in found.checks)
        for check in found.checks:
            said = _check_line(check)
            out.append(f"\n  {check.name:<{width}}  {_ago(check.updated):<9}", style="sc.muted")
            out.append(f" {said}", style="sc.warning" if check.new or check.gone else "")
            plain.append(f"check\t{check.name}\t{check.updated}\t{said}")
    if found.findings:
        out.append(f"\n\nfindings ({len(found.findings)})", style="sc.accent")
        for item in found.findings:
            severity = str(item.get("severity", ""))
            out.append(f"\n  {str(item.get('id', ''))[-6:]}  ", style="sc.accent")
            out.append(f"{severity:<9}", style=SEVERITY_STYLES.get(severity, ""))
            out.append(f"{item.get('kind', '')} {item.get('value', '')}", style="sc.path")
            out.append(f"  {item.get('title', '')}")
            plain.append(f"finding\t{item.get('id', '')}\t{severity}\t{item.get('kind', '')}\t{item.get('value', '')}"
                         f"\t{item.get('title', '')}")
    if found.reports:
        out.append("\n\nreports", style="sc.accent")
        out.append(f"  report show {unit.id} N prints one", style="sc.muted")
        for number, item in enumerate(found.reports, start=1):
            out.append(f"\n  {number}  {_local(item['time'])}  ", style="sc.muted")
            out.append(f"{item['status']:<5}", style=STATUS_STYLES[item["status"]])
            out.append(f" {_short(item['path'])}", style="sc.path")
            plain.append(f"report\t{item['time']}\t{item['status']}\t{item['path']}")
    return Styled(out, "\n".join(plain) + "\n")


@builtin("fleet", "Every machine you watch on one screen: status, last round, changes, findings",
         "fleet [UNIT] [--json]", category="shell", doc=FLEET_DOC)
def fleet(ctx: ShellContext, args: list[str], stdin: str) -> Any:
    parser = ArgParser("fleet")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("unit", nargs="?")
    opts = parser.parse_args(args)
    try:
        if opts.unit:
            found = store.detail(opts.unit)
            return json.dumps(found.as_dict(), indent=1) + "\n" if opts.json else _detail(found)
        units = store.gather()
    except store.FleetError as exc:
        raise CommandError(f"fleet: {exc}") from None
    if opts.json:
        return json.dumps([asdict(unit) for unit in units], indent=1) + "\n"
    return _overview(units)
