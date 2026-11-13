"""report — keep what a watch round saw on a host: a status for `fleet`, and a report when it found something.

The stores are in shellcraft/fleet.py: ~/.shellcraft/fleet (one status record per unit, always written) and
~/.shellcraft/reports (a Markdown file, only when the status is not ok or a report body is given). See
modules/report.md.
"""

from __future__ import annotations

from shellcraft import fleet
from shellcraft.modkit import ArgParser, ModuleError

SUMMARY = "Keep what a watch round saw on a host: its status for the fleet view, and a report when it found something"

USAGE = ("report: usage: report save UNIT --status ok|warn|crit --summary TEXT [--changed N] [--flags N] "
         "[--plan NAME] | list [UNIT] | show UNIT [N]")


def run(args: list[str], stdin: str) -> str:
    parser = ArgParser("report")
    parser.add_argument("action", nargs="?", choices=("save", "list", "show"))
    parser.add_argument("unit", nargs="?")
    parser.add_argument("number", nargs="?", type=int, default=1)  # show UNIT N: 1 is the newest report
    parser.add_argument("-s", "--status", choices=fleet.STATUSES)
    parser.add_argument("-m", "--summary", default="")
    parser.add_argument("--changed", type=int)
    parser.add_argument("--flags", type=int)
    parser.add_argument("--plan", default="")
    opts = parser.parse_args(args)
    if opts.action is None:
        raise ModuleError(USAGE)
    try:
        if opts.action == "save":
            return _save(opts, stdin)
        if opts.action == "show":
            if not opts.unit:
                raise ModuleError("report: show needs a unit (report list shows them)")
            return fleet.read_report(opts.unit, opts.number)[1]
        return _list(opts.unit)
    except fleet.FleetError as exc:
        raise ModuleError(f"report: {exc}") from None
    except OSError as exc:
        raise ModuleError(f"report: {exc.filename or ''}: {exc.strerror or exc}") from None


def _save(opts, body: str) -> str:
    if not opts.unit or opts.status is None:
        raise ModuleError("report: save needs a unit and a status: report save host:web01 --status ok "
                          '--summary "no change"')
    record = fleet.save(opts.unit, opts.status, opts.summary, body, opts.changed, opts.flags, opts.plan)
    said = f"{record['unit']}: {record['status']} recorded\n"
    return said + (f"report: {record['report']}\n" if record["report"] else "")


def _counts(record: dict) -> str:
    parts = [f"{record[key]} {word}" for key, word in (("changed", "changed"), ("flags", "flag(s)"))
             if record[key] is not None]
    return ", ".join(parts)


def _list(unit: str | None) -> str:
    if unit:
        record = fleet.record(unit)
        listed = fleet.reports(unit)
        head = (f"{record['unit']}  {record['status']}  {record['time']}  {record['summary']}\n" if record
                else f"{unit}: no status yet\n")
        return head + "".join(f"{number}  {item['time']}  {item['status']}  {item['path']}\n"
                              for number, item in enumerate(listed, start=1))
    records = fleet.records()
    if not records:
        return "no status yet: a round ends each host with `report save`\n"
    count: dict[str, int] = {}
    for item in fleet.reports():
        count[item["unit"]] = count.get(item["unit"], 0) + 1
    lines = []
    for name, record in sorted(records.items()):
        extra = ", ".join(part for part in (_counts(record), f"{count.get(name, 0)} report(s)") if part)
        lines.append(f"{name}  {record['status']}  {record['time']}  {record['summary']}  ({extra})\n")
    return "".join(lines)
