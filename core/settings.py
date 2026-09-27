"""On/off settings persisted under "settings" in ~/.shellcraft/config.json."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from core.context import ShellContext


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    default: bool
    description: str
    # Applies a new value to the running session (e.g. flips a ShellContext flag).
    apply: Callable[[ShellContext, bool], None] | None = None


def _apply_system_commands(ctx: ShellContext, value: bool) -> None:
    ctx.allow_system = value


SETTINGS: dict[str, Setting] = {
    s.key: s
    for s in (
        Setting("system_commands", "Use OS commands", False,
                "Fall back to programs on PATH (ls, git, cmd.exe built-ins…) when no "
                "ShellCraft builtin or module matches. Off keeps behavior identical on "
                "Windows and Linux.", _apply_system_commands),
        Setting("pager", "Auto-pager", True,
                "Open output taller than the terminal in the scrollable viewer."),
        Setting("spinner", "Live spinners", True,
                "Show an animated spinner while a slow command runs."),
        Setting("banner", "Startup banner", True,
                "Show the ShellCraft banner when the shell starts."),
    )
}

TRUE_WORDS = {"on", "true", "yes", "1", "enable", "enabled"}
FALSE_WORDS = {"off", "false", "no", "0", "disable", "disabled"}


def get(config: dict[str, Any], key: str) -> bool:
    value = (config.get("settings") or {}).get(key)
    return SETTINGS[key].default if not isinstance(value, bool) else value


def set_value(config: dict[str, Any], key: str, value: bool) -> None:
    config.setdefault("settings", {})[key] = value


def reset(config: dict[str, Any], key: str) -> None:
    (config.get("settings") or {}).pop(key, None)


def parse_bool(word: str) -> bool | None:
    word = word.lower()
    if word in TRUE_WORDS:
        return True
    if word in FALSE_WORDS:
        return False
    return None
