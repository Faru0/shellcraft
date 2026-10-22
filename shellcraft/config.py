"""User configuration stored in ~/.shellcraft/config.json."""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    "theme": "cyberpunk",
    # Custom themes: {"mytheme": {"base": "nord", "prompt": "#ff00ff", ...}}
    "themes": {},
}

# What each known section must be. A section of the wrong type (a hand edit gone wrong, or a
# newer ShellCraft's layout) is dropped with a warning instead of crashing every mode at startup.
SECTION_TYPES: dict[str, type] = {
    "theme": str, "sub_theme": str, "themes": dict, "settings": dict, "env": dict, "aliases": dict,
    "disabled_modules": list, "prompt": dict,
}
# Sections whose entries are merged one key at a time when saving (see save_config).
MERGED_SECTIONS = ("themes", "settings", "env", "aliases", "prompt")



class Config(dict):
    """The config as a dict, remembering how it looked when it was last read from or written to
    disk (`baseline`), so save_config() can tell which keys this session changed and leave the
    rest to other sessions. A plain dict works everywhere a Config does (tests build them).
    `profile` names the active profile (shellcraft.profiles), whose keys save_config() routes to it."""

    baseline: dict[str, Any] | None = None
    profile: str | None = None
    frozen: bool = False  # `func run -i`: nothing this session changes is written while it runs


def config_dir() -> Path:
    """Directory holding config and history (override with $SHELLCRAFT_HOME)."""
    override = os.environ.get("SHELLCRAFT_HOME")
    return Path(override) if override else Path.home() / ".shellcraft"


def config_path() -> Path:
    return config_dir() / "config.json"


def history_path() -> Path:
    return config_dir() / "history"


def _read(path: Path, warn: bool = True) -> dict[str, Any] | None:
    """The file's content as a dict, or None when it is missing or unreadable."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        if warn:
            print(f"shellcraft: ignoring unreadable config {path}: {exc}", file=sys.stderr)
        return None
    if not isinstance(data, dict):
        if warn:
            print(f"shellcraft: ignoring config {path}: expected a JSON object", file=sys.stderr)
        return None
    return data


def validate(data: dict[str, Any], warnings: list[str] | None = None) -> dict[str, Any]:
    """Drop every known section whose type is wrong (`"env": [1]`), noting it in `warnings`."""
    clean: dict[str, Any] = {}
    for key, value in data.items():
        expected = SECTION_TYPES.get(key)
        if expected is not None and not isinstance(value, expected):
            if warnings is not None:
                warnings.append(f"config: ignoring '{key}': expected {expected.__name__}, "
                                f"got {type(value).__name__}")
            continue
        clean[key] = value
    return clean


def load_config() -> Config:
    config = Config({k: (v.copy() if isinstance(v, dict) else v) for k, v in DEFAULT_CONFIG.items()})
    data = _read(config_path())
    if data is not None:
        warnings: list[str] = []
        config.update(validate(data, warnings))
        for warning in warnings:
            print(f"shellcraft: {warning} (in {config_path()})", file=sys.stderr)
    config.baseline = copy.deepcopy(dict(config))
    return config


def save_config(config: dict[str, Any]) -> None:
    """Write the config, keeping what other sessions saved in the meantime.

    Only the keys this session changed since the config was loaded (or last saved) are written
    over the file's current content; inside the merged sections (settings, env, aliases, …) that
    is done entry by entry, so an alias saved in one shell survives a theme change in another.
    The file is written through a temporary file and renamed into place, so a crash mid-write
    never leaves a truncated config (and the stored API keys) behind.
    """
    if getattr(config, "frozen", False):
        return  # an isolated func is running: its changes are undone afterwards, never saved
    profile = getattr(config, "profile", None)
    if profile:  # a profile's keys go to its profile.json, the rest (API keys, MCP…) to config.json
        from shellcraft import profiles

        owned = set(profiles.OWNED)
        base_part = Config({k: v for k, v in config.items() if k not in owned})
        base_part.baseline = {k: v for k, v in (config.baseline or {}).items() if k not in owned} \
            if isinstance(config, Config) and config.baseline is not None else None
        _write(base_part)
        profiles.store_overlay(profile, config)
        if isinstance(config, Config):
            config.baseline = copy.deepcopy(dict(config))
        return
    _write(config)


def _write(config: dict[str, Any]) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    baseline = config.baseline if isinstance(config, Config) else None
    merged = _merge(config, _read(path, warn=False), baseline)
    text = json.dumps(merged, indent=2) + "\n"
    # API keys inside: readable by the owner only (created that way, and tightened if it existed).
    mode = 0o600 if merged.get("env") else 0o644
    fd, tmp = tempfile.mkstemp(prefix=".config-", suffix=".json", dir=path.parent)
    try:
        os.fchmod(fd, mode)
    except (OSError, AttributeError):
        pass  # e.g. filesystems without Unix permissions
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    if mode == 0o600:
        try:
            os.chmod(path, mode)
        except OSError:
            pass
    if isinstance(config, Config):
        config.baseline = copy.deepcopy(dict(merged))


def _merge(config: dict[str, Any], on_disk: dict[str, Any] | None,
           baseline: dict[str, Any] | None) -> dict[str, Any]:
    """This session's changes (config vs. baseline) applied over what is on disk now."""
    if on_disk is None or baseline is None:
        return dict(config)
    result = validate(on_disk)
    for key in set(config) | set(baseline):
        current, before = config.get(key), baseline.get(key)
        if key in MERGED_SECTIONS and isinstance(current, dict) and isinstance(before, dict):
            section = result.get(key)
            section = dict(section) if isinstance(section, dict) else {}
            for name in set(current) | set(before):
                if name not in current:
                    section.pop(name, None)  # removed in this session
                elif current[name] != before.get(name, _MISSING):
                    section[name] = current[name]
            result[key] = section
        elif current != before:
            if key in config:
                result[key] = current
            else:
                result.pop(key, None)
    return result


_MISSING = object()
