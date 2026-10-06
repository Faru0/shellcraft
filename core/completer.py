"""Tab completion: commands, per-command option switches and their values, and paths.

Option switches come from each command's own metadata (a module's .skill and .md, a
builtin's doc), so new modules get completion without any code here.
"""

from __future__ import annotations

import re
from typing import Iterable

from prompt_toolkit.completion import CompleteEvent, Completer, Completion, PathCompleter
from prompt_toolkit.document import Document

from core import aliases
from core.builtins import BUILTINS
from core.context import ShellContext
from core.options import OptionSpec, builtin_options
from core.parser import Command
from core.settings import SETTINGS, env_settings
from core.themes import all_themes

_BREAK_CHARS = " \t|>"


def _current_segment(before: str) -> list[str]:
    """The words of the command being typed: a new one starts after `|`, `;`, `&&` or `||`,
    after a `{` or `}` word (a -py block), and after `if`, `elif`, `else`, `then`, `do`, `-py` or
    `!` (a condition is a command)."""
    words = re.split(r"&&|\|\||[|;]", before)[-1].split()
    for i in range(len(words) - 1, -1, -1):
        if words[i] in ("{", "}"):
            words = words[i + 1:]
            break
    while words and words[0] in ("if", "elif", "else", "then", "do", "-py", "!"):
        words = words[1:]
    return words


class ShellCompleter(Completer):
    def __init__(self, ctx: ShellContext):
        self.ctx = ctx
        self.paths = PathCompleter(expanduser=True)

    def get_completions(self, document: Document, event: CompleteEvent) -> Iterable[Completion]:
        text = document.text_before_cursor
        start = max(text.rfind(c) for c in _BREAK_CHARS) + 1
        word = text[start:]
        before = text[:start].rstrip()
        segment = _current_segment(before)

        if not before.endswith(">") and not segment:
            yield from self._commands(word)
            return
        if not before.endswith(">") and segment and segment[0] == "modules":
            yield from self._modules(segment, word)
            return
        if not before.endswith(">") and segment and segment[0] == "unalias":
            yield from self._complete(word, self._aliases())
            return
        if not before.endswith(">") and len(segment) == 1 and segment[0] in ("man", "theme", "settings"):
            yield from self._arguments(segment[0], word)
            return
        if not before.endswith(">") and len(segment) == 2 and segment[0] == "settings":
            if segment[1] == "reset":
                yield from self._complete(word, self._setting_names())
            elif segment[1] in SETTINGS:
                yield from self._complete(word, {"on": "enable", "off": "disable", "toggle": "flip"})
            return  # after an API-key name comes a secret: offer nothing
        if not before.endswith(">") and len(segment) > 2 and segment[0] == "settings":
            return
        if not before.endswith(">") and segment:
            options = self._options_for(segment[0])
            prev = segment[-1] if len(segment) > 1 else None
            wanted = next((o for o in options if prev in o.flags and o.metavar), None)
            if wanted is not None:  # the previous word is a switch that takes a value
                if wanted.values:
                    yield from self._complete(word, {v: wanted.label for v in wanted.values})
                elif wanted.takes_path:
                    yield from self.paths.get_completions(Document(word, len(word)), event)
                return  # a free-form value (N, PATTERN, …): offering file names would mislead
            if word.startswith("-") and options:
                yield from self._switches(word, options, used=set(segment[1:]))
                return
        yield from self.paths.get_completions(Document(word, len(word)), event)

    def _options_for(self, command: str) -> list[OptionSpec] | tuple[OptionSpec, ...]:
        command = aliases.expand_command(Command(command, []), self._aliases()).name  # ll -<Tab> → ls's switches
        if command in BUILTINS:
            return builtin_options(command)
        spec = self.ctx.registry.get(command)
        return spec.options if spec is not None else ()

    def _switches(self, word: str, options, used: set[str]) -> Iterable[Completion]:
        for option in options:
            if used & set(option.flags):
                continue  # already on the line
            if word.startswith("--"):
                flag = next((f for f in option.flags if f.startswith("--") and f.startswith(word)), None)
            else:  # one short form per option when possible, e.g. "-f" rather than "--field"
                flag = next((f for f in (option.short, option.long) if f and f.startswith(word)), None)
            if flag:
                yield Completion(flag, start_position=-len(word), display=option.label, display_meta=option.help)

    def _commands(self, word: str) -> Iterable[Completion]:
        entries = {name: "builtin" for name in BUILTINS}
        for name in self.ctx.registry.names():
            entries[name] = self.ctx.registry.get(name).summary or "module"
        for name, value in self._aliases().items():
            entries[name] = f"alias → {value}"
        for name in sorted(entries):
            if name.startswith(word):
                yield Completion(name, start_position=-len(word), display_meta=entries[name])

    def _arguments(self, command: str, word: str) -> Iterable[Completion]:
        if command == "theme":
            options = {t.name: t.label for t in all_themes(self.ctx.config).values()}
        elif command == "settings":
            options = self._setting_names() | {"reset": "restore a default"}
        else:
            options = {n: "module" for n in self.ctx.registry.names()} | {n: "builtin" for n in BUILTINS}
        yield from self._complete(word, options)

    def _modules(self, segment: list[str], word: str) -> Iterable[Completion]:
        """modules <Tab>: the subcommands; modules enable/disable <Tab>: the modules it applies to."""
        registry = self.ctx.registry
        if len(segment) == 1:
            yield from self._complete(word, {"enable": "turn modules on", "disable": "turn modules off",
                                             "-a": "show disabled modules too"})
        elif segment[1] == "enable":
            yield from self._complete(word, {n: registry.describe(n) or "disabled module"
                                             for n in registry.disabled_names() if n not in segment[2:]})
        elif segment[1] == "disable":
            yield from self._complete(word, {n: registry.describe(n) or "module"
                                             for n in registry.names() if n not in segment[2:]})

    def _aliases(self) -> dict[str, str]:
        return aliases.get_all(self.ctx.config) if self.ctx.allow_aliases else {}

    def _setting_names(self) -> dict[str, str]:
        keys = {name: entry.label for name, entry in env_settings(self.ctx.registry).items()}
        return {k: s.label for k, s in SETTINGS.items()} | keys

    def _complete(self, word: str, options: dict[str, str]) -> Iterable[Completion]:
        for name in sorted(options):
            if name.startswith(word):
                yield Completion(name, start_position=-len(word), display_meta=options[name])
