"""Portable ports of common Unix commands, registered as ShellCraft builtins.

Importing this package registers every command in core.builtins.BUILTINS.
"""

from core.commands import files, info, text  # noqa: F401
