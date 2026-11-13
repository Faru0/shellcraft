"""Portable ports of common Unix commands, registered as ShellCraft builtins.

Importing this package registers every command in shellcraft.builtins.BUILTINS.
"""

from shellcraft.commands import (  # noqa: F401
    ai,
    ask_ai,
    cond,
    curl,
    diff,
    files,
    findings,
    fleet,
    func,
    info,
    jq,
    misc,
    profile,
    review,
    sed,
    tasks,
    text,
)
