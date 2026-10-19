"""Tab completion: commands, per-command option switches and their values, positional values,
and paths (quote-aware).

Option switches come from each command's own metadata (a module's .skill and .md, a
builtin's doc), so new modules get completion without any code here.
"""

from __future__ import annotations

import re
from typing import Iterable

from prompt_toolkit.completion import CompleteEvent, Completer, Completion, PathCompleter
from prompt_toolkit.document import Document

from shellcraft import aliases
from shellcraft.builtins import BUILTINS
from shellcraft.context import ShellContext
from shellcraft.options import OptionSpec, builtin_options, positional_values
from shellcraft.parser import Command
from shellcraft.settings import SETTINGS, env_settings
from shellcraft.themes import all_themes
from shellcraft.usage import Expect, expect

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
    while words and words[0] in ("if", "elif", "else", "then", "do", "while", "until", "-py", "!"):
        words = words[1:]
    return words


def split_word(text: str) -> tuple[str, str, str | None]:
    """(text before the word, the word being typed, the quote it is inside or None).

    Walks the line tracking quotes, so a word that starts with `"` or `'` runs to the end of
    the line even when it holds spaces: `cat "my fi` → ('cat ', 'my fi', '"')."""
    start, quote, quote_at = 0, None, -1
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote, quote_at = ch, i
        elif ch in _BREAK_CHARS:
            start = i + 1
    if quote is not None and quote_at >= start:
        return text[:quote_at + 1], text[quote_at + 1:], quote
    return text[:start], text[start:], None


class ShellCompleter(Completer):
    def __init__(self, ctx: ShellContext):
        self.ctx = ctx
        self.paths = PathCompleter(expanduser=True)

    def get_completions(self, document: Document, event: CompleteEvent) -> Iterable[Completion]:
        text = document.text_before_cursor
        head, word, quote = split_word(text)
        before = head.rstrip()
        if quote is not None:
            before = before[:-1].rstrip()  # drop the opening quote
        segment = _current_segment(before)
        after_redirect = before.endswith(">")

        if quote is None and not after_redirect and not segment:
            yield from self._commands(word)
            return
        if quote is None and not after_redirect and segment:
            if segment[0] == "modules":
                yield from self._modules(segment, word)
                return
            if segment[0] == "unalias":
                yield from self._complete(word, self._aliases())
                return
            if segment[0] == "help" and len(segment) == 1:
                yield from self._arguments("man", word)
                return
            if segment[0] == "sub" and len(segment) == 1:  # a command to run in the background, or a nested shell
                yield from self._complete(word, {"shellcraft": "a nested ShellCraft"})
                yield from self._commands(word)
                return
            if len(segment) == 1 and segment[0] in ("man", "theme", "settings", "prompt"):
                yield from self._arguments(segment[0], word)
                return
            if segment[0] == "theme" and len(segment) == 2 and segment[1] == "preview":
                yield from self._arguments("theme", word)
                return
            if segment[0] == "theme" and len(segment) == 2 and segment[1] == "--sub":
                options = {t.name: t.label for t in all_themes(self.ctx.config).values()}
                yield from self._complete(word, options | {"--clear": "subshells use the main theme"})
                return
            if segment[0] == "prompt":
                from shellcraft.shell import SEGMENTS

                yield from self._complete(word, {k: v for k, v in SEGMENTS.items() if k not in segment})
                return
            if len(segment) == 2 and segment[0] == "settings":
                if segment[1] == "reset":
                    yield from self._complete(word, self._setting_names())
                elif segment[1] in SETTINGS:
                    yield from self._complete(word, {"on": "enable", "off": "disable", "toggle": "flip"})
                return  # after an API-key name comes a secret: offer nothing
            if len(segment) > 2 and segment[0] == "settings":
                return
            options = self._options_for(segment[0])
            prev = segment[-1] if len(segment) > 1 else None
            wanted = next((o for o in options if prev in o.flags and o.metavar), None)
            if wanted is not None:  # the previous word is a switch that takes a value
                if wanted.values:
                    yield from self._complete(word, {v: wanted.label for v in wanted.values})
                elif wanted.takes_path:
                    yield from self._paths(word, quote, event)
                return  # a free-form value (N, PATTERN, …): offering file names would mislead
            guide = self._usage_expect(segment, options)
            if word.startswith("-") and (options or guide is not None):
                yield from self._switches(word, options, used=set(segment[1:]))
                if guide is not None:  # switches the usage line names but the docs don't describe
                    known = {f for o in options for f in o.flags}
                    extra = {f: "switch" for f in guide.flags | guide.free_flags if f not in known}
                    yield from self._complete(word, extra)
                return
            values = self._positional_values(segment)
            if values:
                yield from self._complete(word, values)
                return
            if guide is not None:
                yield from self._guided(guide, segment, word, quote, event, options)
                return
        yield from self._paths(word, quote, event)

    def _usage_expect(self, segment: list[str], options) -> Expect | None:
        """What the command's usage line allows after the words typed so far, or None (no usage
        line, one it can't read, or words it doesn't fit: then file names, as before)."""
        name = aliases.expand_command(Command(segment[0], []), self._aliases()).name
        if name in BUILTINS:
            usage = BUILTINS[name].usage
        else:
            spec = self.ctx.registry.get(name)
            usage = spec.skill.usage if spec is not None and spec.skill is not None else ""
        if not usage:
            return None
        takes_value = {f: bool(o.metavar) for o in options for f in o.flags}
        return expect(name, usage, segment[1:], takes_value)

    def _guided(self, guide: Expect, segment: list[str], word: str, quote: str | None,
                event: CompleteEvent, options) -> Iterable[Completion]:
        """Offer what the usage line says comes next: its words (`mcp <Tab>` → start, stop …), a
        file name only where it takes one, a command where it runs one, and the switches when
        the line can't go on without one (`cut <Tab>` → -f, -c). Where only a free value fits
        (a PATTERN, a MESSAGE) Tab on an empty word offers nothing rather than file names that
        would mislead (the usage toolbar shows what to type); a word typed in part still gets
        the file names that start with it."""
        literals = {w: d or f"{segment[0]} argument" for w, d in guide.literals.items() if w[:1].isalnum()}
        yield from self._complete(word, literals)
        if guide.wants_command:
            yield from self._commands(word)
            return
        if guide.wants_path:
            yield from self._paths(word, quote, event)
            return
        if guide.flags or not (guide.metas or literals):  # a switch is needed, or nothing but switches fits
            helps = {f: o.help or o.label for o in options for f in o.flags}
            flags = guide.flags or guide.free_flags  # the ones it can't do without, else the optional ones
            for flag in sorted(flags):
                if flag.startswith(word):
                    yield Completion(flag, start_position=-len(word), display_meta=helps.get(flag, "switch"))
        if guide.metas and word:  # a value typed in part: a file name that fits it may be what is meant
            yield from self._paths(word, quote, event)

    def _paths(self, word: str, quote: str | None, event: CompleteEvent) -> Iterable[Completion]:
        """Path completions; a name with spaces is quoted unless the word already is."""
        for c in self.paths.get_completions(Document(word, len(word)), event):
            full = word + c.text
            if quote is None and any(ch in full for ch in " \t'\"|>;&"):
                q = "'" if "'" not in full else '"'
                yield Completion(q + full + q, start_position=-len(word), display=c.display)
            else:
                yield c

    def _positional_values(self, segment: list[str]) -> dict[str, str]:
        """`queryCensys <Tab>` → host / cert / search, from the .skill's positional entries;
        `review <Tab>` → its actions, then the review items' ids."""
        name = aliases.expand_command(Command(segment[0], []), self._aliases()).name
        if name == "review":
            return self._review_values(segment)
        spec = self.ctx.registry.get(name)
        if spec is None:
            return {}
        index = sum(1 for w in segment[1:] if not w.startswith("-"))
        values = positional_values(spec, index)
        return {v: f"{segment[0]} argument" for v in values}

    @staticmethod
    def _review_values(segment: list[str]) -> dict[str, str]:
        words = [w for w in segment[1:] if not w.startswith("-")]
        if not words:
            return {"show": "one item in full", "approve": "let the AI go ahead", "reject": "tell it not to"}
        if len(words) == 1 and words[0] in ("show", "approve", "reject"):
            from shellcraft import review_spool

            try:
                items = review_spool.list_items("pending" if words[0] != "show" else None,
                                                "review" if words[0] != "show" else None)
            except OSError:
                return {}
            return {i["id"]: i.get("title", "")[:60] for i in items}
        return {}

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
        entries = {name: b.summary or "builtin" for name, b in BUILTINS.items() if not b.hidden}
        for name in self.ctx.registry.names():
            spec = self.ctx.registry.get(name)
            entries[name] = (spec.summary if spec else "") or "module"
        for name, value in self._aliases().items():
            entries[name] = f"alias → {value}"
        if self.ctx.allow_aliases:
            for name in self.ctx.functions:
                entries[name] = "shell function"
        for name in sorted(entries):
            if name.startswith(word):
                yield Completion(name, start_position=-len(word), display_meta=entries[name])

    def _arguments(self, command: str, word: str) -> Iterable[Completion]:
        if command == "theme":
            options = {t.name: t.label for t in all_themes(self.ctx.config).values()}
            options |= {"preview": "try a theme without saving", "reset": "back to the default",
                        "--sub": "the theme subshells start with"}
        elif command == "settings":
            options = self._setting_names() | {"reset": "restore a default"}
        elif command == "prompt":
            from shellcraft.shell import SEGMENTS

            options = dict(SEGMENTS) | {"reset": "back to the default", "-r": "set the right prompt"}
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
