"""The subshell prefix key: Ctrl-] by default, another one with `settings prefix_key NAME`.

The prefix, then a digit, switches to that subshell; alone (inside a subshell) it returns to the
prompt; then `x` ends the subshell, `c` folds the footer, `v` pastes the clip; twice sends one to
the program. Every key a program might also want costs something there, so each choice says what
it shadows. The choice is saved in config.json (`prefix_key`) and read at startup; the prompt's
key bindings and the attach loop read it each time, so a change applies at once.
"""

from __future__ import annotations

from typing import Any, NamedTuple

CONFIG_KEY = "prefix_key"
DEFAULT = "ctrl-]"


class Key(NamedTuple):
    pt: str  # the prompt_toolkit key name
    byte: bytes  # what a terminal sends for it
    label: str  # how messages name it
    note: str  # what it shadows (completion and `settings`)


KEYS: dict[str, Key] = {
    "ctrl-]": Key("c-]", b"\x1d", "Ctrl-]", "the default: nano's word completion, little else"),
    "ctrl-b": Key("c-b", b"\x02", "Ctrl-B", "tmux's prefix; one character back in bash, nano and the prompt"),
    "ctrl-a": Key("c-a", b"\x01", "Ctrl-A", "GNU screen's prefix; start of line in bash, nano and the prompt"),
    "ctrl-t": Key("c-t", b"\x14", "Ctrl-T", "swap characters in bash; nano's spell check / execute"),
    "ctrl-g": Key("c-g", b"\x07", "Ctrl-G", "cancel in Emacs-style editing; nano's help"),
    "ctrl-s": Key("c-s", b"\x13", "Ctrl-S", "save in nano and micro; forward search in bash"),
}

_current = DEFAULT


def name() -> str:
    return _current


def key() -> Key:
    return KEYS[_current]


def label() -> str:
    """`Ctrl-]` or the chosen key, for messages."""
    return key().label


def byte() -> bytes:
    return key().byte


def normalize(word: str) -> str | None:
    """`ctrl-b`, `Ctrl+B`, `C-b`, `^B` → `ctrl-b`; None for a key that is not offered."""
    w = word.strip().lower().replace("+", "-")
    if w.startswith("^") and len(w) == 2:
        w = "ctrl-" + w[1]
    elif w.startswith("c-"):
        w = "ctrl-" + w[2:]
    return w if w in KEYS else None


def set_key(word: str) -> str:
    """Use `word` from now on; returns its canonical name. Raises ValueError for an unknown key."""
    global _current
    chosen = normalize(word)
    if chosen is None:
        raise ValueError(f"unknown prefix key '{word}' (choose one of: {', '.join(KEYS)})")
    _current = chosen
    return chosen


def from_config(config: dict[str, Any]) -> str:
    """Apply the saved choice (an unknown one falls back to the default)."""
    global _current
    saved = config.get(CONFIG_KEY)
    _current = (normalize(saved) if isinstance(saved, str) else None) or DEFAULT
    return _current
