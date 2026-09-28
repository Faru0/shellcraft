"""User configuration stored in ~/.shellcraft/config.json."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    "theme": "cyberpunk",
    # Custom themes: {"mytheme": {"base": "nord", "prompt": "#ff00ff", ...}}
    "themes": {},
}


def config_dir() -> Path:
    """Directory holding config and history (override with $SHELLCRAFT_HOME)."""
    override = os.environ.get("SHELLCRAFT_HOME")
    return Path(override) if override else Path.home() / ".shellcraft"


def config_path() -> Path:
    return config_dir() / "config.json"


def history_path() -> Path:
    return config_dir() / "history"


def load_config() -> dict[str, Any]:
    config = {k: (v.copy() if isinstance(v, dict) else v) for k, v in DEFAULT_CONFIG.items()}
    path = config_path()
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"shellcraft: ignoring unreadable config {path}: {exc}", file=sys.stderr)
        else:
            if isinstance(data, dict):
                config.update(data)
    return config


def save_config(config: dict[str, Any]) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(config, indent=2) + "\n"
    if not config.get("env"):
        path.write_text(text, encoding="utf-8")
        return
    # API keys inside: readable by the owner only (created that way, and tightened if it existed).
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # e.g. filesystems without Unix permissions
