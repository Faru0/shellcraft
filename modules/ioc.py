"""ioc — pull indicators (IPs, domains, URLs, file hashes, email addresses) out of any text."""

from __future__ import annotations

import json

import _intel

from shellcraft.modkit import ArgParser, ModuleError

SUMMARY = "Pull IPs, domains, URLs, hashes and email addresses out of any text (logs, mails, reports)"

OUTPUTS = ("list", "json")


def run(args: list[str], stdin: str) -> str:
    parser = ArgParser("ioc")
    parser.add_argument("text", nargs="*", metavar="TEXT")
    parser.add_argument("-t", "--type", action="append", metavar="TYPES")
    parser.add_argument("--public", action="store_true")
    fang = parser.add_mutually_exclusive_group()
    fang.add_argument("--defang", action="store_true")
    fang.add_argument("--refang", action="store_true")  # the default, named for scripts that want to say so
    parser.add_argument("-c", "--count", action="store_true")
    parser.add_argument("-o", "--output", default="list", choices=OUTPUTS)
    opts = parser.parse_args(args)

    kinds = _intel.kinds_named(opts.type, "ioc")
    text = " ".join(opts.text) if opts.text else stdin
    if not text.strip():
        raise ModuleError("ioc: give some text (as arguments, or on stdin)")

    found = [f for f in _intel.find(text) if f.kind in kinds and (not opts.public or _public(f))]
    if opts.count:
        found.sort(key=lambda f: -f.count)  # most seen first; a stable sort keeps first-seen order among equals
    shown = [(f.kind, _intel.defang(f.value) if opts.defang else f.value, f.count) for f in found]
    if opts.output == "json":
        return json.dumps([{"type": k, "value": v, "count": n} for k, v, n in shown], indent=2) + "\n"
    if opts.count:
        width = max((len(str(n)) for _, _, n in shown), default=1)
        return "".join(f"{n:>{width}}  {v}\n" for _, v, n in shown)
    return "".join(f"{v}\n" for _, v, _ in shown)


def _public(found: _intel.Found) -> bool:
    """False for an address that is not on the internet (private, loopback, link-local, reserved),
    and for a URL that points at one. Names are kept: only a lookup can tell where they lead."""
    host = _intel.host_of(found.value, found.kind) if found.kind == "url" else found.value
    if found.kind in ("ipv4", "ipv6") or _intel.kind_of(host) in ("ipv4", "ipv6"):
        return _intel.is_public(host.strip("[]"))
    return True
