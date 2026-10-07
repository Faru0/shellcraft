"""Optional AI help: Claude (Anthropic) or OpenAI, over plain HTTPS with `requests`.

Used for two jobs: applying the conversion steps to the code (the result still has to pass the
minimal-change guard in convert.py) and writing the final .md/.skill from the offline draft.
Keys come from the environment or ShellCraft's stored settings; a key typed in at the prompt is
used for this run only and never saved.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import requests

from core.config import load_config

PROMPTS = Path(__file__).resolve().parent / "prompts"
EXAMPLE = Path(__file__).resolve().parents[2] / "templates" / "example"


class AIError(Exception):
    """The AI call failed; the message is meant for the user."""


@dataclass(frozen=True)
class Provider:
    key: str  # "claude" | "openai"
    label: str
    env: str  # the API key variable
    model_env: str  # overrides the default model
    default_model: str


PROVIDERS = {
    "claude": Provider("claude", "Claude (Anthropic)", "ANTHROPIC_API_KEY", "SHELLCRAFT_CLAUDE_MODEL",
                       "claude-opus-5-5"),
    "openai": Provider("openai", "OpenAI", "OPENAI_API_KEY", "SHELLCRAFT_OPENAI_MODEL", "gpt-5"),
}

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
# Claude models that accept server-side refusal fallbacks (the request is re-run on another model).
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}


def model_for(provider: Provider, override: str | None = None) -> str:
    return override or os.environ.get("SHELLCRAFT_AI_MODEL") or os.environ.get(provider.model_env) \
        or provider.default_model


def stored_key(provider: Provider) -> str:
    """The provider's API key from the environment, or from `settings` (config.json), or ''."""
    if value := os.environ.get(provider.env, "").strip():
        return value
    stored = (load_config().get("env") or {}).get(provider.env)
    return stored.strip() if isinstance(stored, str) else ""


def complete(provider: Provider, api_key: str, model: str, prompt: str, timeout: float = 600) -> str:
    """Send one prompt, return the reply text."""
    try:
        if provider.key == "claude":
            return _claude(api_key, model, prompt, timeout)
        return _openai(api_key, model, prompt, timeout)
    except requests.Timeout:
        raise AIError(f"{provider.label} did not answer within {timeout:g}s") from None
    except requests.RequestException as exc:
        raise AIError(f"couldn't reach {provider.label}: {exc}") from None


def _check(provider: str, response: requests.Response) -> dict:
    if response.status_code in (401, 403):
        raise AIError(f"{provider} rejected the API key (HTTP {response.status_code})")
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code >= 400:
        message = (data.get("error") or {}).get("message") if isinstance(data.get("error"), dict) else None
        raise AIError(f"{provider} returned HTTP {response.status_code}: {message or response.text[:200]}")
    return data


def _claude(api_key: str, model: str, prompt: str, timeout: float) -> str:
    headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    body: dict = {"model": model, "max_tokens": 16000, "messages": [{"role": "user", "content": prompt}]}
    if model in _FALLBACK_MODELS:
        headers["anthropic-beta"] = "server-side-fallback-2026-07-01"
        body["fallbacks"] = "default"
    data = _check("Claude", requests.post(ANTHROPIC_URL, headers=headers, json=body, timeout=timeout))
    if data.get("stop_reason") == "refusal":
        raise AIError("Claude declined this request")
    if data.get("stop_reason") == "max_tokens":
        raise AIError("Claude's answer was cut off (too long)")
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    if not text.strip():
        raise AIError("Claude returned no text")
    return text


def _openai(api_key: str, model: str, prompt: str, timeout: float) -> str:
    headers = {"authorization": f"Bearer {api_key}", "content-type": "application/json"}
    body = {"model": model, "messages": [{"role": "user", "content": prompt}]}
    data = _check("OpenAI", requests.post(OPENAI_URL, headers=headers, json=body, timeout=timeout))
    choices = data.get("choices") or [{}]
    if choices[0].get("finish_reason") == "length":
        raise AIError("OpenAI's answer was cut off (too long)")
    text = (choices[0].get("message") or {}).get("content") or ""
    if not text.strip():
        raise AIError("OpenAI returned no text")
    return text


# ── prompts and replies ─────────────────────────────────────────────────────

def _fill(template: str, values: dict[str, str]) -> str:
    # NAME last: the other values (source code, docs) may legitimately contain "{{NAME}}".
    for key in [k for k in values if k != "NAME"] + ["NAME"]:
        template = template.replace("{{" + key + "}}", values[key])
    return template


def convert_prompt(source: str, name: str, steps: list[str], decorator: str) -> str:
    numbered = "\n".join(f"{i:>4}| {line}" for i, line in enumerate(source.splitlines(), start=1))
    return _fill((PROMPTS / "convert.md").read_text(encoding="utf-8"), {
        "NAME": name, "DECORATOR": decorator, "NUMBERED_SOURCE": numbered,
        "STEPS": "\n".join(f"{i}. {step}" for i, step in enumerate(steps, start=1)),
    })


def docs_prompt(source: str, name: str, draft_md: str, draft_skill: str) -> str:
    return _fill((PROMPTS / "docs.md").read_text(encoding="utf-8"), {
        "NAME": name, "SOURCE": source.rstrip(), "DRAFT_MD": draft_md.rstrip(), "DRAFT_SKILL": draft_skill.rstrip(),
        "EXAMPLE_PY": EXAMPLE.with_suffix(".py").read_text(encoding="utf-8").rstrip(),
        "EXAMPLE_MD": EXAMPLE.with_suffix(".md").read_text(encoding="utf-8").rstrip(),
        "EXAMPLE_SKILL": EXAMPLE.with_suffix(".skill").read_text(encoding="utf-8").rstrip(),
    })


_FENCE = re.compile(r"^(?P<fence>`{3,})(?P<lang>[\w+-]*)[ \t]*\n(?P<body>.*?)^(?P=fence)[ \t]*$", re.M | re.S)


def code_blocks(reply: str) -> list[tuple[str, str]]:
    """(language, body) for every fenced block, outermost only."""
    return [(m.group("lang").lower(), m.group("body")) for m in _FENCE.finditer(reply)]


def parse_code(reply: str) -> str:
    blocks = code_blocks(reply)
    python = [body for lang, body in blocks if lang in {"python", "py"}] or [body for _, body in blocks]
    if len(python) != 1:
        raise AIError(f"expected one code block in the reply, found {len(python)}")
    return python[0]


def parse_docs(reply: str) -> tuple[str, str]:
    blocks = code_blocks(reply)
    md = [body for lang, body in blocks if lang in {"markdown", "md"}]
    toml = [body for lang, body in blocks if lang == "toml"]
    if len(md) != 1 or len(toml) != 1:
        raise AIError("expected one ```markdown block and one ```toml block in the reply")
    return md[0], toml[0]
