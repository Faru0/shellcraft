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
    from shellcraft.context import ShellContext
    from shellcraft.loader import ModuleRegistry
    from shellcraft.modkit import EnvSetting


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


def _apply_mouse(ctx: ShellContext, value: bool) -> None:
    if ctx.ui is not None:
        ctx.ui.mouse = value


def _apply_mouse_always(ctx: ShellContext, value: bool) -> None:
    if ctx.ui is not None:
        ctx.ui.mouse_always = value


SETTINGS: dict[str, Setting] = {
    s.key: s
    for s in (
        Setting("system_commands", "Use OS commands", False,
                "Fall back to programs on PATH (ls, git, cmd.exe built-ins…) when no "
                "ShellCraft builtin or module matches. Off keeps behavior identical on "
                "Windows and Linux.", _apply_system_commands),
        Setting("pager", "Pager", True,
                "Open man pages, and whatever is piped into `less`, in the scrollable viewer. Off: print "
                "them instead."),
        Setting("spinner", "Live spinners", True,
                "Show an animated spinner while a slow command runs."),
        Setting("banner", "Startup banner", True,
                "Show the ShellCraft banner when the shell starts."),
        Setting("diagnostics", "Diagnostics", False,
                "Offer the `diag` command (console, color, key, mouse and footer checks to copy into a "
                "bug report). Off: `diag` is hidden. `shellcraft --diag` turns this on and prints the "
                "report at startup."),
        Setting("hot_reload", "Hot reload", True,
                "Reload modules when their .py, .md or .skill files change, in the shell and "
                "the MCP server (which then tells clients the tool list changed)."),
        Setting("lazy_modules", "Lazy modules", True,
                "Import a module's code the first time it runs, not at startup (faster start; "
                "an import error shows up when the module is first used)."),
        Setting("startup", "Startup scripts", True,
                "Run the Python scripts in ~/.shellcraft/startup/ when the interactive shell starts "
                "(man startup)."),
        Setting("clear_on_start", "Clear on start", True,
                "Clear the screen when the interactive shell starts (never in --mcp mode)."),
        Setting("toolbar", "Bottom toolbar", True,
                "Show the usage of the command being typed in a line under the prompt."),
        Setting("highlight", "Syntax highlighting", True,
                "Color the command line as you type: known commands, operators, quotes."),
        Setting("transient_prompt", "Transient prompt", False,
                "After Enter, collapse the two-line prompt to one line, so the scrollback stays "
                "dense."),
        Setting("mouse", "Mouse", True,
                "Click and scroll in the completion menu and the pager. The terminal keeps the "
                "mouse (text selection, wheel scrollback) whenever no menu is open.", _apply_mouse),
        Setting("mouse_always", "Mouse at the prompt", False,
                "Keep the mouse on while typing: click to place the cursor, click a prompt segment "
                "(⚡ → help, path → ls, ⎇ branch → git status, ● mcp → mcp log, [✗ N] → the last "
                "error), click the toolbar for the man page, wheel through history and Ctrl-R "
                "matches. The terminal then needs Shift for text selection and scrollback.",
                _apply_mouse_always),
        Setting("footer_mouse", "Clickable footer at the prompt", True,
                "While the footer lists a subshell or an alert, the shell has the mouse at the prompt: "
                "the footer's lines, an alert's ✖ and the badge can be clicked, and text is selected "
                "with Shift and a drag. The wheel hands the mouse back to the terminal, so its turns "
                "scroll the terminal's own history until the next key press. Off: the terminal keeps "
                "the mouse at the prompt and the footer answers the keys (Ctrl-] N, ↓, Ctrl-] c, "
                "alert -c, Alt-c). With nothing to click the terminal always keeps the mouse."),
        Setting("scroll_view", "Wheel opens the scroll view", False,
                "While the shell has the mouse at the prompt (footer_mouse), a turn of the wheel up "
                "opens the scroll view: the session's recent output in the viewer, at its end (wheel "
                "down past the end, or q, closes it). Off: the wheel hands the mouse to the terminal "
                "instead, so its turns scroll the terminal's own history."),
        Setting("footer", "Shell footer", True,
                "The shell stack (main, then one line per subshell) on the last rows of the terminal, "
                "at the prompt and inside every subshell: click a line, or Ctrl-] N, to switch; Ctrl-] c "
                "folds it. Off: a panel under the prompt only while something is running, and nothing "
                "inside subshells."),
        Setting("jobs_autoclear", "Finished jobs clear themselves", True,
                "A finished subshell or background command leaves the stack at the next prompt, after "
                "its `[N] done` note and, for a command, its output. Off: it stays listed as done until "
                "`jobs -c`, and `jobs N` shows its output."),
        Setting("panel_hint", "Subshell panel key hints", False,
                "Show a header line at the top of the subshell panel with the keys that work there "
                "(click or Ctrl-] N switches, Ctrl-] alone returns, Ctrl-] x ends it). Off by default "
                "to keep the panel short."),
        Setting("links", "Clickable paths", False,
                "File names from ls, tree, find, du and grep are terminal hyperlinks: Ctrl-click "
                "(Cmd-click on macOS) opens them, in terminals that support links. Off by default: "
                "accidental clicks in a listing opened files."),
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


# ── where a key lives: config.json ──

OLD_KEYRING_MARKER = "@keyring"  # a key an older version kept in the OS keyring (no longer read)


def from_old_keyring(config: dict[str, Any], name: str) -> bool:
    """A key an older ShellCraft stored in the OS keyring: it has to be set again."""
    return _section(config, "env").get(name) == OLD_KEYRING_MARKER


def env_get(config: dict[str, Any], name: str) -> str | None:
    value = _section(config, "env").get(name)
    if value == OLD_KEYRING_MARKER:
        return None
    return value if isinstance(value, str) and value else None


def env_set(config: dict[str, Any], name: str, value: str) -> None:
    """Store a key in config.json (owner-only once it holds keys) and export it now."""
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
    for name in list(_section(config, "env")):
        if isinstance(name, str) and ENV_NAME.match(name) and not os.environ.get(name):
            value = env_get(config, name)
            if value:
                _export(name, value)


def _export(name: str, value: str) -> None:
    _ORIGINAL_ENV.setdefault(name, os.environ.get(name))
    os.environ[name] = value


def env_status(config: dict[str, Any], name: str) -> tuple[str, str]:
    """(state, text) for display: ("set", "set ••••ab12"), ("environment", …) or ("unset", …)."""
    value = env_get(config, name)
    current = os.environ.get(name)
    if value is None and from_old_keyring(config, name) and not current:
        return "unset", "stored in the OS keyring by an older version: set it again"
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
# `env set -s NAME VALUE` / `env set -s NAME=VALUE`: a secret environment variable.
_ENV_SECRET_STMT = re.compile(r"^(\s*(?:(?:do|then|else|!)\s+)*\\?env\s+set\s+-s\s+"
                              r"[A-Za-z_][A-Za-z0-9_]*)(=|\s+)(\S.*)$")


def redact_line(line: str, aliases: Mapping[str, str] | None = None) -> str:
    """Hide the value in `settings NAME VALUE` and `env set -s NAME VALUE` so API keys and secrets
    never reach the history file.

    Every statement of the line is checked (`echo x ; settings KEY abc`, a `for` body), a
    leading `\\` is seen through, and so is an alias of `settings` when `aliases` is given.
    """
    if "settings" not in line and "env" not in line and not aliases:
        return line
    parts = _STATEMENT_SPLIT.split(line)  # statements and the separators between them
    for i in range(0, len(parts), 2):
        match = _SETTINGS_STMT.match(parts[i])
        if match and _is_settings(match.group(2), aliases):
            parts[i] = f"{match.group(1)}{match.group(2)}{match.group(3)}{match.group(4)}{match.group(5)}••••"
        elif secret := _ENV_SECRET_STMT.match(parts[i]):
            parts[i] = f"{secret.group(1)}{secret.group(2)}••••"
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
