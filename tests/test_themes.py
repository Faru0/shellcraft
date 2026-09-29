from core.themes import PRESETS, UI, all_themes


def test_invalid_custom_color_falls_back_to_base():
    warnings: list[str] = []
    config = {"themes": {"mine": {"base": "matrix", "prompt": "not-a-color", "accent": "#123456"}}}
    theme = all_themes(config, warnings)["mine"]
    assert theme.prompt == PRESETS["matrix"].prompt
    assert theme.accent == "#123456"
    assert len(warnings) == 1 and "not-a-color" in warnings[0] and "'prompt'" in warnings[0]
    UI(theme)  # used to raise ValueError: Wrong color format


def test_color_must_work_in_rich_and_prompt_toolkit():
    config = {"themes": {"mine": {"path": "ansired", "muted": "#fff", "error": "red"}}}
    theme = all_themes(config)["mine"]  # no warnings list: stays silent
    assert theme.path == PRESETS["nord"].path  # prompt_toolkit only
    assert theme.muted == PRESETS["nord"].muted  # prompt_toolkit only
    assert theme.error == "red"


def test_every_preset_color_is_valid_and_renders():
    from core.themes import _COLOR_FIELDS, _valid_color

    assert {"dracula", "gruvbox", "catppuccin", "tokyonight"} <= set(PRESETS)
    for theme in PRESETS.values():
        assert all(_valid_color(getattr(theme, f)) for f in _COLOR_FIELDS), theme.name
        UI(theme)
