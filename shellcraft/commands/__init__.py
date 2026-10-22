"""Portable ports of common Unix commands, registered as ShellCraft builtins.

Importing this package registers every command in shellcraft.builtins.BUILTINS.
"""

from shellcraft.commands import cond, diff, files, func, info, jq, misc, profile, review, text  # noqa: F401
