"""ShellCraft: a modular, pipe-friendly Python shell."""

from typing import TYPE_CHECKING, Any

__version__ = "2.0.0"

if TYPE_CHECKING:
    from shellcraft.pretty import listing, table

__all__ = ["__version__", "listing", "table"]


def __getattr__(name: str) -> Any:
    """`from shellcraft import table, listing`: what a module returns for a pretty table or list.
    Loaded on first use, so that `import shellcraft` stays as light as it was."""
    if name in ("table", "listing"):
        from shellcraft import pretty

        return getattr(pretty, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
