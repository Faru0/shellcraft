"""Theme engine: semantic colors rendered to both Rich and prompt_toolkit styles."""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import Any

from prompt_toolkit.styles import Style as PTStyle
from prompt_toolkit.styles.style import parse_color
from rich.color import Color, ColorParseError
from rich.console import Console
from rich.theme import Theme as RichTheme


def _hex(color: str) -> tuple[int, int, int]:
    r, g, b = Color.parse(color).get_truecolor()
    return r, g, b


def _mix(color: str, other: str, amount: float) -> str:
    """`color` moved `amount` (0..1) of the way towards `other`."""
    a, b = _hex(color), _hex(other)
    return "#" + "".join(f"{int(a[k] + (b[k] - a[k]) * amount):02x}" for k in range(3))


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
    # Surfaces (the completion menu, the pager's search line, scrollbars) are derived from the
    # muted color unless a theme sets them: dark themes get a dark surface, light ones a light one.
    surface: str = ""  # background of menus and toolbars
    surface_text: str = ""  # text on that background
    light: bool = False  # made for a light terminal background

    def _surface(self) -> str:
        return self.surface or _mix(self.muted, "#ffffff" if self.light else "#000000", 0.75)

    def _surface_alt(self) -> str:
        return _mix(self._surface(), "#ffffff" if self.light else "#000000", 0.3)

    def _surface_text(self) -> str:
        return self.surface_text or ("#303030" if self.light else "#d0d0d0")

    def _on_accent(self) -> str:
        return "#ffffff" if self.light else "#000000"

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
                "sc.match": f"bold {self._on_accent()} on {self.accent}",
                "sc.footer.header": f"bold {self._on_accent()} on {self.accent}",
                "sc.footer.main": f"{self._surface_text()} on {self._surface()}",
                "sc.footer.running": f"{self.warning} on {self._surface()}",
                "sc.footer.done": f"{self.success} on {self._surface()}",
                "sc.footer.failed": f"{self.error} on {self._surface()}",
                "sc.footer.current": f"bold {self._surface_text()} on {self._surface()}",
                "sc.footer.hover": f"bold {self.accent} on {self._surface()}",
                "sc.footer.alert.info": f"bold {self._on_accent()} on {self.success}",
                "sc.footer.alert.warning": f"bold #000000 on {self.warning}",
                "sc.footer.alert.critical": f"bold #ffffff on {self.error}",
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
        surface, alt, text = self._surface(), self._surface_alt(), self._surface_text()
        return PTStyle.from_dict(
            {
                "frame": self.muted,
                "name": f"bold {self.prompt}",
                "sep": self.muted,
                "user": self.accent,
                "path": f"bold {self.path}",
                "git": self.success,
                "duration": self.warning,
                "clock": self.muted,
                "arrow": f"bold {self.prompt}",
                "failed": f"bold {self.error}",
                "mcp": self.success,  # the `mcp start` server, running
                "mcp.down": f"bold {self.error}",  # ... or exited on its own
                "auto-suggestion": f"italic {self.muted}",
                # The input line (core/highlight.py)
                "cmd.known": self.success,
                "cmd.unknown": self.error,
                "keyword": f"bold {self.prompt}",
                "operator": f"bold {self.accent}",
                "string": self.path,
                "switch": self.warning,
                "variable": self.accent,
                "comment": f"italic {self.muted}",
                # Menus, toolbars, scrollbars
                "completion-menu": f"bg:{surface} {text}",
                "completion-menu.completion.current": f"bg:{self.accent} {self._on_accent()} bold",
                "completion-menu.meta.completion": f"bg:{alt} {self.muted}",
                "completion-menu.meta.completion.current": f"bg:{self.prompt} {self._on_accent()}",
                "scrollbar.background": f"bg:{alt}",
                "scrollbar.button": f"bg:{self.accent}",
                "bottom-toolbar": f"bg:{surface} {text} noreverse",
                "toolbar": f"bg:{surface} {self.muted} noreverse",
                "toolbar.key": f"bg:{self.accent} {self._on_accent()} bold noreverse",
                "toolbar.error": f"bg:{surface} {self.error} noreverse",
                "toolbar.job": f"bg:{surface} {text} noreverse",
                "toolbar.job.running": f"bg:{surface} {self.warning} noreverse",
                "toolbar.job.done": f"bg:{surface} {self.success} noreverse",
                "toolbar.job.failed": f"bg:{surface} {self.error} noreverse",
                "toolbar.job.hover": f"bg:{surface} {self.accent} bold noreverse",
                "toolbar.job.current": f"bg:{surface} {text} bold noreverse",
                "toolbar.alert.info": f"bg:{self.success} {self._on_accent()} bold noreverse",
                "toolbar.alert.warning": f"bg:{self.warning} #000000 bold noreverse",
                "toolbar.alert.critical": f"bg:{self.error} #ffffff bold noreverse",
                "search-toolbar": f"bg:{surface} {text}",
                "search-toolbar.text": f"bg:{surface} {self.prompt}",
                "rprompt": self.muted,
                # Pager
                "pager.status": f"bg:{self.accent} {self._on_accent()}",
                "pager.status.key": f"bg:{self.accent} {self._on_accent()} bold",
                "pager.title": f"bg:{self.prompt} {self._on_accent()} bold",
                "pager.search": f"bg:{surface} {self.prompt}",
                "pager.help": f"bg:{surface} {text}",
                "pager.help.key": f"bg:{surface} {self.accent} bold",
                "pager.scrollbar": f"{self.muted}",
                "pager.scrollbar.thumb": f"{self.accent}",
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
            name="solarized-light", label="Solarized Light",
            prompt="#268bd2", path="#2aa198", accent="#d33682", muted="#93a1a1",
            error="#dc322f", success="#859900", warning="#b58900",
            gradient_from="#b58900", gradient_to="#d33682",
            surface="#eee8d5", surface_text="#586e75", light=True,
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
            name="catppuccin-latte", label="Catppuccin Latte",
            prompt="#8839ef", path="#04a5e5", accent="#ea76cb", muted="#9ca0b0",
            error="#d20f39", success="#40a02b", warning="#df8e1d",
            gradient_from="#8839ef", gradient_to="#1e66f5",
            surface="#e6e9ef", surface_text="#4c4f69", light=True,
        ),
        ThemeDef(
            name="tokyonight", label="Tokyo Night",
            prompt="#7aa2f7", path="#7dcfff", accent="#bb9af7", muted="#565f89",
            error="#f7768e", success="#9ece6a", warning="#e0af68",
            gradient_from="#7aa2f7", gradient_to="#bb9af7",
        ),
    )
}
DEFAULT_THEME = "cyberpunk"

_OPTIONAL_FIELDS = ("surface", "surface_text")  # may be empty: derived from `muted` then
_COLOR_FIELDS = [f.name for f in fields(ThemeDef) if f.name not in ("name", "label", "light", *_OPTIONAL_FIELDS)]


def all_themes(config: dict[str, Any], warnings: list[str] | None = None) -> dict[str, ThemeDef]:
    """Presets plus custom themes from config (each may inherit from a `base` preset).

    A color that Rich or prompt_toolkit can't parse keeps the base preset's value and adds a
    message to `warnings` (when given) instead of crashing the UI later.
    """
    themes = dict(PRESETS)
    custom = config.get("themes")
    for name, spec in (custom.items() if isinstance(custom, dict) else ()):
        if not isinstance(spec, dict):
            continue
        base = themes.get(spec.get("base", "nord"), PRESETS["nord"])
        overrides: dict[str, Any] = {}
        for key, value in spec.items():
            if key == "light" and isinstance(value, bool):
                overrides[key] = value
                continue
            if key not in (*_COLOR_FIELDS, *_OPTIONAL_FIELDS) or not isinstance(value, str):
                continue
            if _valid_color(value):
                overrides[key] = value
            elif warnings is not None:
                warnings.append(f"theme '{name}': invalid color {value!r} for '{key}', "
                                f"using {getattr(base, key) or 'the default'}")
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
        # The `mouse` setting: prompt_toolkit takes the mouse only while a completion menu is open
        # and in the pager, so the terminal's own selection and wheel scrollback work the rest of
        # the time. Off means never.
        self.mouse = True
        # The `mouse_always` setting: keep the mouse on at the prompt (clicks on segments and the
        # toolbar, wheel through history), at the cost of the terminal's own selection.
        self.mouse_always = False

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
