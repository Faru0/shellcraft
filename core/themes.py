"""Theme engine: semantic colors rendered to both Rich and prompt_toolkit styles."""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import Any

from prompt_toolkit.styles import Style as PTStyle
from prompt_toolkit.styles.style import parse_color
from rich.color import Color, ColorParseError
from rich.console import Console
from rich.theme import Theme as RichTheme


@dataclass(frozen=True)
class ThemeDef:
    name: str
    label: str
    prompt: str
    path: str
    accent: str
    muted: str
    error: str
    success: str
    warning: str
    gradient_from: str
    gradient_to: str

    def rich_theme(self) -> RichTheme:
        return RichTheme(
            {
                "sc.prompt": f"bold {self.prompt}",
                "sc.path": self.path,
                "sc.accent": self.accent,
                "sc.muted": self.muted,
                "sc.error": f"bold {self.error}",
                "sc.success": self.success,
                "sc.warning": self.warning,
                "sc.border": self.accent,
                "sc.match": f"bold reverse {self.accent}",
                # Markdown (man pages) picks up the palette too.
                "markdown.h1": f"bold {self.prompt}",
                "markdown.h1.border": self.accent,
                "markdown.h2": f"bold underline {self.accent}",
                "markdown.h3": f"bold {self.path}",
                "markdown.code": f"bold {self.success}",
                "markdown.link": self.path,
                "markdown.item.bullet": self.accent,
                "status.spinner": self.accent,
            }
        )

    def pt_style(self) -> PTStyle:
        return PTStyle.from_dict(
            {
                "frame": self.muted,
                "name": f"bold {self.prompt}",
                "sep": self.muted,
                "user": self.accent,
                "path": f"bold {self.path}",
                "arrow": f"bold {self.prompt}",
                "failed": f"bold {self.error}",
                "mcp": self.success,  # the `mcp start` server, running
                "mcp.down": f"bold {self.error}",  # ... or exited on its own
                "auto-suggestion": f"italic {self.muted}",
                "completion-menu": "bg:#1c1c1c #d0d0d0",
                "completion-menu.completion.current": f"bg:{self.accent} #000000 bold",
                "completion-menu.meta.completion": f"bg:#262626 {self.muted}",
                "completion-menu.meta.completion.current": f"bg:{self.prompt} #000000",
                "scrollbar.background": "bg:#303030",
                "scrollbar.button": f"bg:{self.accent}",
                # Pager
                "pager.status": f"bg:{self.accent} #000000",
                "pager.status.key": f"bg:{self.accent} #000000 bold",
                "pager.search": f"bg:#262626 {self.prompt}",
            }
        )


PRESETS: dict[str, ThemeDef] = {
    t.name: t
    for t in (
        ThemeDef(
            name="cyberpunk", label="Cyberpunk Neon",
            prompt="#ff2a6d", path="#05d9e8", accent="#f706cf", muted="#7a5c99",
            error="#ff3864", success="#00ff9f", warning="#fcee0c",
            gradient_from="#ff2a6d", gradient_to="#05d9e8",
        ),
        ThemeDef(
            name="matrix", label="Matrix Green",
            prompt="#00ff41", path="#39ff14", accent="#00c832", muted="#3b6e3b",
            error="#ff3333", success="#00ff41", warning="#d4ff00",
            gradient_from="#004d14", gradient_to="#00ff41",
        ),
        ThemeDef(
            name="nord", label="Nord",
            prompt="#88c0d0", path="#8fbcbb", accent="#b48ead", muted="#616e88",
            error="#bf616a", success="#a3be8c", warning="#ebcb8b",
            gradient_from="#5e81ac", gradient_to="#8fbcbb",
        ),
        ThemeDef(
            name="solarized", label="Solarized",
            prompt="#268bd2", path="#2aa198", accent="#d33682", muted="#657b83",
            error="#dc322f", success="#859900", warning="#b58900",
            gradient_from="#b58900", gradient_to="#d33682",
        ),
        ThemeDef(
            name="dracula", label="Dracula",
            prompt="#bd93f9", path="#8be9fd", accent="#ff79c6", muted="#6272a4",
            error="#ff5555", success="#50fa7b", warning="#f1fa8c",
            gradient_from="#bd93f9", gradient_to="#ff79c6",
        ),
        ThemeDef(
            name="gruvbox", label="Gruvbox",
            prompt="#fe8019", path="#8ec07c", accent="#83a598", muted="#928374",
            error="#fb4934", success="#b8bb26", warning="#fabd2f",
            gradient_from="#fb4934", gradient_to="#fabd2f",
        ),
        ThemeDef(
            name="catppuccin", label="Catppuccin Mocha",
            prompt="#cba6f7", path="#89dceb", accent="#f5c2e7", muted="#7f849c",
            error="#f38ba8", success="#a6e3a1", warning="#f9e2af",
            gradient_from="#cba6f7", gradient_to="#89b4fa",
        ),
        ThemeDef(
            name="tokyonight", label="Tokyo Night",
            prompt="#7aa2f7", path="#7dcfff", accent="#bb9af7", muted="#565f89",
            error="#f7768e", success="#9ece6a", warning="#e0af68",
            gradient_from="#7aa2f7", gradient_to="#bb9af7",
        ),
    )
}

_COLOR_FIELDS = [f.name for f in fields(ThemeDef) if f.name not in ("name", "label")]


def all_themes(config: dict[str, Any], warnings: list[str] | None = None) -> dict[str, ThemeDef]:
    """Presets plus custom themes from config (each may inherit from a `base` preset).

    A color that Rich or prompt_toolkit can't parse keeps the base preset's value and adds a
    message to `warnings` (when given) instead of crashing the UI later.
    """
    themes = dict(PRESETS)
    for name, spec in (config.get("themes") or {}).items():
        if not isinstance(spec, dict):
            continue
        base = themes.get(spec.get("base", "nord"), PRESETS["nord"])
        overrides = {}
        for key, value in spec.items():
            if key not in _COLOR_FIELDS or not isinstance(value, str):
                continue
            if _valid_color(value):
                overrides[key] = value
            elif warnings is not None:
                warnings.append(f"theme '{name}': invalid color {value!r} for '{key}', "
                                f"using {getattr(base, key)}")
        themes[name] = replace(base, name=name, label=spec.get("label", name), **overrides)
    return themes


def _valid_color(value: str) -> bool:
    try:
        parse_color(value)
        Color.parse(value)
    except (ValueError, ColorParseError):
        return False
    return True


class UI:
    """Holds the active theme plus the Rich console and prompt_toolkit style built from it."""

    def __init__(self, theme: ThemeDef, console: Console | None = None):
        self.console = console or Console(highlight=False)
        self.theme = theme
        self.pt_style = theme.pt_style()
        self.console.push_theme(theme.rich_theme())

    def use_console(self, console: Console) -> Console:
        """Switch to another Rich console with the current theme on it; returns the previous one."""
        previous = self.console
        if previous is not console:
            console.push_theme(self.theme.rich_theme())
            self.console = console
        return previous

    def set_theme(self, theme: ThemeDef) -> None:
        self.console.pop_theme()
        self.console.push_theme(theme.rich_theme())
        self.theme = theme
        self.pt_style = theme.pt_style()
