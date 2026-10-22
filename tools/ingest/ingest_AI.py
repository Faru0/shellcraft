"""ingest's AI help, all in this file: Claude (Anthropic) or OpenAI, over plain HTTPS with `requests`.

To upgrade the AI side of ingest (a new model, another provider, better prompts), change this file
only. It holds:

- the providers, their API key variables and default models (PROVIDERS), and the HTTP calls;
- the two prompts (CONVERT_PROMPT, DOCS_PROMPT) and how replies are read;
- the wizard's AI steps: choosing the provider and key (choose_provider), asking before anything is
  sent (ask_ai), converting the code (convert_with_ai: the result must still pass the minimal-change
  guard in convert.py) and finishing the .md / .skill (docs_with_ai).

Keys come from the environment or ShellCraft's stored settings; a key typed in at the prompt is
used for this run only and never saved. Everything is shown and asked through tools.ingest.ui.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import requests
from rich.panel import Panel
from rich.syntax import Syntax

from shellcraft.config import load_config
from tools.ingest import ui
from tools.ingest.convert import Conversion, extra_changes

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


# ── the prompts ─────────────────────────────────────────────────────────────
# {{NAME}}-style placeholders are filled in by convert_prompt() and docs_prompt().

CONVERT_PROMPT = r'''You are converting a Python script into a **ShellCraft module** for `{{NAME}}`. ShellCraft calls
`run(args: list[str], stdin: str) -> str` instead of running the file as a script.

Make **exactly the changes listed below and nothing else**. This is a minimal, mechanical edit:

- Do not rename anything, fix bugs, add or remove comments, change quotes, reformat, add type
  hints, reorder code, or "improve" anything — even if it looks wrong.
- Keep every line that is not mentioned exactly as it is (apart from the indentation the
  changes require).
- `@{{DECORATOR}}("{{NAME}}")` (imported from `shellcraft.modkit`) turns what the body `print()`s into the
  output and `sys.exit()` into a clean error, so prints and exits stay as they are.

## Changes to make

{{STEPS}}

## The script (line numbers on the left are for reference only — do not output them)

```
{{NUMBERED_SOURCE}}
```

## Reply format

Reply with the complete converted file in **one** ```python fenced block, and nothing else.
'''

DOCS_PROMPT = r'''You are an expert technical writer for **ShellCraft**, a Python shell whose commands are
pluggable modules. Below is the source of one module, `{{NAME}}.py`, and a first draft of its two
companion files that a script generated from the code. Write the final versions:

1. `{{NAME}}.md`: the human manual shown by `man {{NAME}}` (Markdown rendered in a terminal).
2. `{{NAME}}.skill`: a TOML file that ShellCraft turns into the **MCP tool description**. Other AI
   agents read it to decide *when* to call the tool and *how* to fill in its inputs.

Base both files **only on what the code actually does**. Read the argument parser, the error
messages and what gets printed or returned. Keep what the draft got right; replace its TODOs.

## How ShellCraft runs a module

- The shell calls `run(args: list[str], stdin: str) -> str`. `args` holds the words after the
  command name, already split and unquoted; `stdin` is the previous pipeline stage's output
  (`""` if none). The returned text goes to the next stage, to a file (`>`), or to the screen.
- A `run()` decorated with `@script("{{NAME}}")` returns whatever its body prints; `sys.exit(0)`
  ends it normally and any other exit (including argparse errors) becomes a one-line error.
- Errors reach the user as an error panel and MCP clients as tool errors.
- Through MCP, an AI calls the tool with named parameters from `[[params]]`, such as
  `{"top": 3, "ignore_case": true, "stdin": "..."}`; ShellCraft turns them back into CLI args
  (`["--top", "3", "--ignore-case"]`).
- API keys are environment variables the user sets once with `settings NAME`.

## Rules for `{{NAME}}.md`

Use exactly these headings, in this order:

1. `# {{NAME}}` as the first line, then one plain sentence saying what the command does.
2. `## Synopsis`: a fenced code block with the usage line(s). `[optional]`, `A | B`, `FILE...`.
3. `## Description`: behavior, where input comes from (stdin and/or files), what the output looks
   like, edge cases such as empty input. If it needs API keys, a short setup note naming each
   variable and the `settings NAME` command.
4. `## Options`: a table `| Option | Meaning |` with **every** option, short and long forms in
   backticks in the first column (`` `-n N`, `--top N` ``), and each default. Leave the section
   out only when there are no options. Tab completion reads these rows.
5. `## Examples`: a fenced code block with 3–6 realistic command lines; at least one with a pipe.
6. `## Errors`: the user-visible error cases from the code.
7. `## See also`: related commands.

Concise and practical. No HTML.

## Rules for `{{NAME}}.skill` (TOML)

- **Key order matters.** All plain `key = value` lines (`summary`, `when_to_use`, `usage`,
  `examples`, `notes`) come **before** the first `[[params]]` table; a key written after a table
  header belongs to that table.
- `summary`: one line, at most 200 characters, starting with a verb and saying what comes back.
- `when_to_use`: when an AI should pick this tool and when it should *not* (name alternatives).
  Say whether input goes in `stdin` or in a parameter.
- `usage`: the same synopsis as the `.md`.
- `examples`: a list of strings showing real calls in MCP shape with their results, e.g.
  `'{"top": 3, "stdin": "a b a"}  -> "a 2\nb 1\n"'` (single-quoted TOML literal strings).
- `notes`: output format, empty-input behavior, errors, limits, side effects (network, files
  written). Name each API key variable and say the *user* sets it with `settings NAME`.
- One `[[params]]` table per positional argument and option. Keys: `name` (snake_case, never
  `stdin`), `flag` (the long switch exactly as in the parser; omit for positionals), `short`,
  `metavar`, `type` (`string` | `integer` | `number` | `boolean` | `array`), `values` (fixed
  choices only), `required = true` only if the call fails without it, `description` (meaning
  plus default). Every flag in the parser must appear, and every `flag`/`short` must exist in it.
- End with 2–5 `[[tests]]` tables: `args` (list of CLI strings) or `params` (inline table), an
  optional `stdin`, and exactly one of `expect` (exact output), `contains` (substring) or `error`
  (substring of the error message). Trace the code to be certain of each result; include at
  least one error case; add `network = true` to any case that needs the internet; never depend
  on files that may not exist (except to test a missing-file error).
- No other keys.

## Worked example: a reference module

### `example.py`
```python
{{EXAMPLE_PY}}
```

### `example.md`
````markdown
{{EXAMPLE_MD}}
````

### `example.skill`
```toml
{{EXAMPLE_SKILL}}
```

---

## The module to document: `{{NAME}}.py`
```python
{{SOURCE}}
```

## The generated draft

### `{{NAME}}.md`
````markdown
{{DRAFT_MD}}
````

### `{{NAME}}.skill`
```toml
{{DRAFT_SKILL}}
```

## Reply format

Reply with **exactly two fenced code blocks**:

1. A line `{{NAME}}.md`, then a ` ````markdown ` fence (four backticks) with the full file.
2. A line `{{NAME}}.skill`, then a ` ```toml ` fence with the full file.
'''


# ── prompts and replies ─────────────────────────────────────────────────────

def _fill(template: str, values: dict[str, str]) -> str:
    # NAME last: the other values (source code, docs) may legitimately contain "{{NAME}}".
    for key in [k for k in values if k != "NAME"] + ["NAME"]:
        template = template.replace("{{" + key + "}}", values[key])
    return template


def convert_prompt(source: str, name: str, steps: list[str], decorator: str) -> str:
    numbered = "\n".join(f"{i:>4}| {line}" for i, line in enumerate(source.splitlines(), start=1))
    return _fill(CONVERT_PROMPT, {
        "NAME": name, "DECORATOR": decorator, "NUMBERED_SOURCE": numbered,
        "STEPS": "\n".join(f"{i}. {step}" for i, step in enumerate(steps, start=1)),
    })


def docs_prompt(source: str, name: str, draft_md: str, draft_skill: str) -> str:
    return _fill(DOCS_PROMPT, {
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


# ── the wizard's AI steps ───────────────────────────────────────────────────

def choose_provider(model_override: str | None = None) -> tuple[Provider, str, str] | None:
    """(provider, api key, model), asking for whatever isn't known."""
    choice = ui.ask("Which AI?", choices=list(PROVIDERS), default="claude")
    provider = PROVIDERS[choice]
    key = stored_key(provider)
    if key:
        ui.say(f"Using the {provider.env} key from your environment/settings.", "dim")
    else:
        key = ui.ask(f"{provider.env} (input hidden; used for this run only, not saved)", password=True).strip()
        if not key:
            ui.say("No key given.", "red")
            return None
    return provider, key, model_for(provider, model_override)


def ask_ai(provider: Provider, key: str, model: str, prompt: str, what: str) -> str | None:
    """Send `prompt` after the user says yes; the reply, or None (declined or failed)."""
    ui.say(f"This sends {what} to {provider.label} ({model}).", "yellow")
    if not ui.confirm("Send it?", default=True):
        return None
    with ui.console.status(f"waiting for {provider.label}…"):
        try:
            return complete(provider, key, model, prompt)
        except AIError as exc:
            ui.say(f"AI request failed: {exc}", "red")
            return None


def convert_with_ai(source: str, name: str, conversion: Conversion, import_name: str,
                    model_override: str | None = None) -> str | None:
    """The script converted by an AI, making only the listed changes; None to choose again."""
    chosen = choose_provider(model_override)
    if chosen is None:
        return None
    reply = ask_ai(*chosen, convert_prompt(source, name, conversion.steps, import_name), "your script's source code")
    if reply is None:
        return None
    try:
        candidate = parse_code(reply)
        compile(candidate, f"{name}.py", "exec")
    except (AIError, SyntaxError) as exc:
        ui.say(f"The AI's answer can't be used: {exc}", "red")
        return None
    if problems := extra_changes(source, candidate, conversion.source):
        ui.console.print(Panel("\n".join(problems[:30]), title="The AI changed more than it was asked to",
                               border_style="red", title_align="left"))
        ui.say("Refusing to use it: only the listed changes are allowed. Pick auto or manual instead.", "bold")
        return None
    ui.say("The AI's version makes only the requested changes:")
    ui.show_diff(Conversion(candidate).diff(source, name))
    return candidate


def docs_with_ai(source: str, name: str, md_path: Path, skill_path: Path, draft_md: str, draft_skill: str,
                 model_override: str | None = None) -> None:
    """Have an AI turn the drafts into the finished .md and .skill, and save them if the user agrees."""
    import tomllib

    chosen = choose_provider(model_override)
    if chosen is None:
        return
    current_md = md_path.read_text(encoding="utf-8") if md_path.exists() else draft_md
    current_skill = skill_path.read_text(encoding="utf-8") if skill_path.exists() else draft_skill
    reply = ask_ai(*chosen, docs_prompt(source, name, current_md, current_skill), "the module's source code")
    if reply is None:
        return
    try:
        new_md, new_skill = parse_docs(reply)
        tomllib.loads(new_skill)
        if next((ln.strip() for ln in new_md.splitlines() if ln.strip()), "") != f"# {name}":
            raise AIError(f"the .md doesn't start with '# {name}'")
    except (AIError, tomllib.TOMLDecodeError) as exc:
        ui.say(f"The AI's docs can't be used ({exc}); keeping the drafts.", "red")
        return
    ui.console.print(Panel(Syntax(new_skill, "toml", theme="ansi_dark", word_wrap=True), title=skill_path.name))
    if ui.confirm("Save the AI's .md and .skill?", default=True):
        md_path.write_text(new_md, encoding="utf-8")
        skill_path.write_text(new_skill, encoding="utf-8")
        ui.say(f"wrote {md_path} and {skill_path}", "green")
