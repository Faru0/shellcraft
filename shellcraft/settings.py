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
        Setting("diagnostics", "Console diagnostics", False,
                "Print a plain-text report at startup: window and console sizes from every source, "
                "color detection, console modes and code pages. Copy it into a bug report."),
        Setting("hot_reload", "Hot reload", True,
                "Reload modules when their .py, .md or .skill files change, in the shell and "
                "the MCP server (which then tells clients the tool list changed)."),
        Setting("lazy_modules", "Lazy modules", True,
                "Import a module's code the first time it runs, not at startup (faster start; "
                "an import error shows up when the module is first used)."),
        Setting("keyring", "Keys in the OS keyring", True,
                "Store API keys in the operating system's keyring (Windows Credential Manager, macOS "
                "Keychain, Secret Service) when the optional `keyring` package is installed; "
                "config.json then holds only a marker. Off, or without keyring: plain text in "
                "config.json. `settings migrate-keys` moves stored keys over."),
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
        Setting("footer_mouse", "Clickable footer at the prompt", False,
                "Report the mouse to the shell while a subshell or an alert is listed, so the footer's "
                "lines, an alert's ✖ and the badge can be clicked at the prompt. The terminal's wheel then "
                "moves through the history instead of scrolling the output and selecting text needs Shift "
                "(the terminal's rule while a program has the mouse). Off: the wheel and selection work as "
                "usual at the prompt, and the footer answers the keys (Ctrl-] N, ↓, Ctrl-] c, alert -c, "
                "Alt-c). Inside a subshell the footer is always clickable, and the wheel scrolls the "
                "subshell's page while a drag selects text and a right-click copies it, without Shift."),
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


# ── where a key lives: the OS keyring (optional `keyring` package) or config.json ──

KEYRING_SERVICE = "shellcraft"
KEYRING_MARKER = "@keyring"  # config.json's value for a key kept in the keyring
_backend: list[Any] = []  # [module or None], filled on first use; tests put a fake in


def keyring_backend() -> Any:
    """The `keyring` module when it is installed and has a real backend, else None."""
    if not _backend:
        found: Any = None
        try:
            import keyring  # type: ignore[import-not-found]

            backend = keyring.get_keyring()
            if getattr(backend, "priority", 0) > 0 and "fail" not in type(backend).__module__:
                found = keyring
        except Exception:  # noqa: BLE001 — not installed, or no usable backend
            found = None
        _backend.append(found)
    return _backend[0]


def use_keyring(config: dict[str, Any]) -> bool:
    return get(config, "keyring") and keyring_backend() is not None


def stored_in_keyring(config: dict[str, Any], name: str) -> bool:
    return _section(config, "env").get(name) == KEYRING_MARKER


def env_get(config: dict[str, Any], name: str) -> str | None:
    value = _section(config, "env").get(name)
    if value == KEYRING_MARKER:
        backend = keyring_backend()
        if backend is None:
            return None  # stored in a keyring this Python can't reach (keyring not installed)
        try:
            value = backend.get_password(KEYRING_SERVICE, name)
        except Exception:  # noqa: BLE001 — a locked or broken keyring: the key is unavailable
            return None
    return value if isinstance(value, str) and value else None


def env_set(config: dict[str, Any], name: str, value: str) -> None:
    """Store a key: in the keyring when possible (config.json keeps the marker), else in
    config.json. Raises OSError when the keyring refuses it."""
    if not isinstance(config.get("env"), dict):
        config["env"] = {}
    if use_keyring(config):
        try:
            keyring_backend().set_password(KEYRING_SERVICE, name, value)
        except Exception as exc:  # noqa: BLE001
            raise OSError(f"the keyring refused the key: {exc}") from None
        config["env"][name] = KEYRING_MARKER
    else:
        _keyring_delete(config, name)
        config["env"][name] = value
    _export(name, value)


def _keyring_delete(config: dict[str, Any], name: str) -> None:
    if stored_in_keyring(config, name) and (backend := keyring_backend()) is not None:
        try:
            backend.delete_password(KEYRING_SERVICE, name)
        except Exception:  # noqa: BLE001 — already gone
            pass


def migrate_keys(config: dict[str, Any]) -> list[str]:
    """Move the keys kept in plain text in config.json into the keyring; returns their names.
    Raises OSError when the keyring is not available or refuses one."""
    if keyring_backend() is None:
        raise OSError("no keyring: pip install keyring (or `pip install shellcraft[keyring]`)")
    moved = []
    for name, value in list(_section(config, "env").items()):
        if isinstance(value, str) and value and value != KEYRING_MARKER:
            try:
                keyring_backend().set_password(KEYRING_SERVICE, name, value)
            except Exception as exc:  # noqa: BLE001
                raise OSError(f"the keyring refused {name}: {exc}") from None
            config["env"][name] = KEYRING_MARKER
            moved.append(name)
    return moved


def plaintext_keys(config: dict[str, Any]) -> list[str]:
    return sorted(n for n, v in _section(config, "env").items() if isinstance(v, str) and v and v != KEYRING_MARKER)


def env_reset(config: dict[str, Any], name: str) -> None:
    _keyring_delete(config, name)
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
            value = env_get(config, name)  # from the keyring when the key lives there
            if value:
                _export(name, value)


def _export(name: str, value: str) -> None:
    _ORIGINAL_ENV.setdefault(name, os.environ.get(name))
    os.environ[name] = value


def env_status(config: dict[str, Any], name: str) -> tuple[str, str]:
    """(state, text) for display: ("set", "set ••••ab12"), ("environment", …) or ("unset", …)."""
    value = env_get(config, name)
    current = os.environ.get(name)
    where = " (keyring)" if stored_in_keyring(config, name) else ""
    if value is None and stored_in_keyring(config, name):
        return "unset", "in the keyring, which can't be reached"
    if value is not None and current in (None, "", value):
        return "set", f"set {mask(value)}{where}"
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
