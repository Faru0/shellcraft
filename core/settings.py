"""Settings persisted in ~/.shellcraft/config.json.

- On/off settings (SETTINGS) live under "settings".
- API keys and other secrets that modules declare with ENV_SETTINGS live under "env". They are
  exported to os.environ, where modules read them.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from core.context import ShellContext
    from core.loader import ModuleRegistry
    from core.modkit import EnvSetting


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


# ── API keys (ENV_SETTINGS) ──────────────────────────────────────────────────

ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")

# The environment as it was before ShellCraft exported anything, so `settings reset NAME`
# can restore a variable the user had set in their own shell.
_ORIGINAL_ENV: dict[str, str | None] = {}


@dataclass(frozen=True)
class EnvEntry:
    module: str
    setting: EnvSetting

    @property
    def name(self) -> str:
        return self.setting.name

    @property
    def label(self) -> str:
        return f"{self.module} · {self.setting.label}"


def env_settings(registry: ModuleRegistry | None) -> dict[str, EnvEntry]:
    """Every ENV_SETTINGS entry of the loaded modules, keyed by variable name (first module wins)."""
    entries: dict[str, EnvEntry] = {}
    if registry is None:
        return entries
    for module in registry.names():
        for setting in registry.get(module).env_settings:
            entries.setdefault(setting.name, EnvEntry(module, setting))
    return entries


def env_get(config: dict[str, Any], name: str) -> str | None:
    value = (config.get("env") or {}).get(name)
    return value if isinstance(value, str) and value else None


def env_set(config: dict[str, Any], name: str, value: str) -> None:
    config.setdefault("env", {})[name] = value
    _export(name, value)


def env_reset(config: dict[str, Any], name: str) -> None:
    (config.get("env") or {}).pop(name, None)
    original = _ORIGINAL_ENV.pop(name, os.environ.get(name))
    if original is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = original


def export_env(config: dict[str, Any]) -> None:
    """Put every stored value into os.environ (startup, all modes)."""
    for name, value in (config.get("env") or {}).items():
        if isinstance(name, str) and ENV_NAME.match(name) and isinstance(value, str) and value:
            _export(name, value)


def _export(name: str, value: str) -> None:
    _ORIGINAL_ENV.setdefault(name, os.environ.get(name))
    os.environ[name] = value


def env_status(config: dict[str, Any], name: str) -> tuple[str, str]:
    """(state, text) for display: ("set", "set ••••ab12"), ("environment", …) or ("unset", …)."""
    value = env_get(config, name)
    if value is not None:
        return "set", f"set {mask(value)}"
    if os.environ.get(name):
        return "environment", "from environment"
    return "unset", "not set"


def mask(value: str) -> str:
    """Show only the last 4 characters, and none of a short value."""
    return "••••" + (value[-4:] if len(value) >= 12 else "")


_SETTINGS_LINE = re.compile(r"^(\s*settings\s+)([A-Z][A-Z0-9_]*)(\s+)(\S.*)$")


def redact_line(line: str) -> str:
    """Hide the value in `settings NAME VALUE` so API keys never reach the history file."""
    match = _SETTINGS_LINE.match(line)
    if not match:
        return line
    return f"{match.group(1)}{match.group(2)}{match.group(3)}••••"
