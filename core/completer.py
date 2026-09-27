"""Tab completion: commands in command position, theme/man arguments, paths elsewhere."""

from __future__ import annotations

from typing import Iterable

from prompt_toolkit.completion import CompleteEvent, Completer, Completion, PathCompleter
from prompt_toolkit.document import Document

from core.builtins import BUILTINS
from core.context import ShellContext
from core.themes import all_themes

_BREAK_CHARS = " \t|>"


class ShellCompleter(Completer):
    def __init__(self, ctx: ShellContext):
        self.ctx = ctx
        self.paths = PathCompleter(expanduser=True)

    def get_completions(self, document: Document, event: CompleteEvent) -> Iterable[Completion]:
        text = document.text_before_cursor
        start = max(text.rfind(c) for c in _BREAK_CHARS) + 1
        word = text[start:]
        before = text[:start].rstrip()
        segment = before[before.rfind("|") + 1:].split() if "|" in before else before.split()

        if not before.endswith(">") and not segment:
            yield from self._commands(word)
            return
        if not before.endswith(">") and len(segment) == 1 and segment[0] in ("man", "theme"):
            yield from self._arguments(segment[0], word)
            return
        yield from self.paths.get_completions(Document(word, len(word)), event)

    def _commands(self, word: str) -> Iterable[Completion]:
        entries = {name: "builtin" for name in BUILTINS}
        for name in self.ctx.registry.names():
            entries[name] = self.ctx.registry.get(name).summary or "module"
        for name in sorted(entries):
            if name.startswith(word):
                yield Completion(name, start_position=-len(word), display_meta=entries[name])

    def _arguments(self, command: str, word: str) -> Iterable[Completion]:
        if command == "theme":
            options = {t.name: t.label for t in all_themes(self.ctx.config).values()}
        else:
            options = {n: "module" for n in self.ctx.registry.names()} | {n: "builtin" for n in BUILTINS}
        for name in sorted(options):
            if name.startswith(word):
                yield Completion(name, start_position=-len(word), display_meta=options[name])
