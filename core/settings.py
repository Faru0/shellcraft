"""Settings persisted in ~/.shellcraft/config.json.

- On/off settings (SETTINGS) live under "settings".
- API keys and other secrets that modules declare with ENV_SETTINGS live under "env". They are
  exported to os.environ, where modules read them. At startup a variable already set in the
  environment (the user's shell, an MCP client's `env` block) wins over the stored value; a value
  set with `settings NAME VALUE` during the session replaces it.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Mapping

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
                "Open man pages, and output taller than the terminal, in the scrollable viewer."),
        Setting("spinner", "Live spinners", True,
                "Show an animated spinner while a slow command runs."),
        Setting("banner", "Startup banner", True,
                "Show the ShellCraft banner when the shell starts."),
        Setting("diagnostics", "Console diagnostics", False,
                "Print a plain-text report at startup: window and console sizes from every source, "
                "color detection, console modes and code pages. Copy it into a bug report."),
        Setting("hot_reload", "Hot reload", True,
                "Reload modules when their .py, .md or .skill files change, in the shell and "
                "the MCP server (which then tells clients the tool list changed)."),
        Setting("lazy_modules", "Lazy modules", True,
                "Import a module's code the first time it runs, not at startup (faster start; "
                "an import error shows up when the module is first used)."),
        Setting("clear_on_start", "Clear on start", True,
                "Clear the screen when the interactive shell starts (never in --mcp mode)."),
        Setting("toolbar", "Bottom toolbar", True,
                "Show the usage of the command being typed in a line under the prompt."),
        Setting("highlight", "Syntax highlighting", True,
                "Color the command line as you type: known commands, operators, quotes."),
        Setting("transient_prompt", "Transient prompt", False,
                "After Enter, collapse the two-line prompt to one line, so the scrollback stays "
                "dense."),
    )
}

TRUE_WORDS = {"on", "true", "yes", "1", "enable", "enabled"}
FALSE_WORDS = {"off", "false", "no", "0", "disable", "disabled"}


def _section(config: dict[str, Any], key: str) -> dict[str, Any]:
    section = config.get(key)
    return section if isinstance(section, dict) else {}


def get(config: dict[str, Any], key: str) -> bool:
    value = _section(config, "settings").get(key)
    return SETTINGS[key].default if not isinstance(value, bool) else value


def set_value(config: dict[str, Any], key: str, value: bool) -> None:
    if not isinstance(config.get("settings"), dict):
        config["settings"] = {}
    config["settings"][key] = value


def reset(config: dict[str, Any], key: str) -> None:
    _section(config, "settings").pop(key, None)


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
        spec = registry.get(module)
        if spec is None:
            continue
        try:
            spec.load()  # a lazily loaded module declares its keys in its .py
        except Exception:  # noqa: BLE001 — a broken module has no keys; the warning is recorded
            continue
        for setting in spec.env_settings:
            entries.setdefault(setting.name, EnvEntry(module, setting))
    return entries


def env_get(config: dict[str, Any], name: str) -> str | None:
    value = _section(config, "env").get(name)
    return value if isinstance(value, str) and value else None


def env_set(config: dict[str, Any], name: str, value: str) -> None:
    if not isinstance(config.get("env"), dict):
        config["env"] = {}
    config["env"][name] = value
    _export(name, value)


def env_reset(config: dict[str, Any], name: str) -> None:
    _section(config, "env").pop(name, None)
    original = _ORIGINAL_ENV.pop(name, os.environ.get(name))
    if original is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = original


def export_env(config: dict[str, Any]) -> None:
    """Put every stored value into os.environ (startup, all modes), unless the environment has one."""
    for name, value in _section(config, "env").items():
        if isinstance(name, str) and ENV_NAME.match(name) and isinstance(value, str) and value:
            if not os.environ.get(name):
                _export(name, value)


def _export(name: str, value: str) -> None:
    _ORIGINAL_ENV.setdefault(name, os.environ.get(name))
    os.environ[name] = value


def env_status(config: dict[str, Any], name: str) -> tuple[str, str]:
    """(state, text) for display: ("set", "set ••••ab12"), ("environment", …) or ("unset", …)."""
    value = env_get(config, name)
    current = os.environ.get(name)
    if value is not None and current in (None, "", value):
        return "set", f"set {mask(value)}"
    if current:
        if value is not None:
            return "environment", f"from environment (stored {mask(value)} unused)"
        return "environment", "from environment"
    return "unset", "not set"


def mask(value: str) -> str:
    """Show only the last 4 characters, and none of a short value."""
    return "••••" + (value[-4:] if len(value) >= 12 else "")


# One statement: optional bash keywords (`do settings …`), an optional `\` (alias bypass), the
# command word, the key name, then the value (which may run to the end of the statement).
_SETTINGS_STMT = re.compile(r"^(\s*(?:(?:do|then|else|!)\s+)*\\?)(\S+)(\s+)([A-Z][A-Z0-9_]*)(\s+)(\S.*)$")
_STATEMENT_SPLIT = re.compile(r"(\s*(?:;|&&|\|\||\|)\s*)")


def redact_line(line: str, aliases: Mapping[str, str] | None = None) -> str:
    """Hide the value in `settings NAME VALUE` so API keys never reach the history file.

    Every statement of the line is checked (`echo x ; settings KEY abc`, a `for` body), a
    leading `\\` is seen through, and so is an alias of `settings` when `aliases` is given.
    """
    if "settings" not in line and not aliases:
        return line
    parts = _STATEMENT_SPLIT.split(line)  # statements and the separators between them
    for i in range(0, len(parts), 2):
        match = _SETTINGS_STMT.match(parts[i])
        if match and _is_settings(match.group(2), aliases):
            parts[i] = f"{match.group(1)}{match.group(2)}{match.group(3)}{match.group(4)}{match.group(5)}••••"
    return "".join(parts)


def _is_settings(word: str, aliases: Mapping[str, str] | None) -> bool:
    seen: set[str] = set()
    while word not in seen:
        if word == "settings":
            return True
        seen.add(word)
        target = (aliases or {}).get(word)
        if not target:
            return False
        word = target.split()[0] if target.split() else ""
    return False
