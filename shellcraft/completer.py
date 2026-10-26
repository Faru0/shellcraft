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

from shellcraft import aliases, envvars, funcs
from shellcraft.builtins import BUILTINS, available
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
            if segment[0] == "env":
                yield from self._env(segment, word)
                return
            if segment[0] == "profile":
                yield from self._profile(segment, word)
                return
            if segment[0] == "func":
                yield from self._func(segment, word)
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
                elif segment[1] == "prefix_key":
                    from shellcraft.prefix import KEYS

                    yield from self._complete(word, {name: spec.note for name, spec in KEYS.items()})
                elif segment[1] == "editor":
                    from shellcraft import editor

                    yield from self._complete(word, editor.found())
                elif segment[1] == "loader":
                    from shellcraft.loading import STYLES

                    yield from self._complete(word, dict(STYLES))
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
        entries = {name: b.summary or "builtin" for name, b in BUILTINS.items()
                   if not b.hidden and available(b, self.ctx.config)}
        for name in self.ctx.registry.names():
            spec = self.ctx.registry.get(name)
            entries[name] = (spec.summary if spec else "") or "module"
        for name, value in self._aliases().items():
            entries[name] = f"alias → {value}"
        if self.ctx.allow_aliases:
            for name in self.ctx.functions:
                entries[name] = "shell function"
            for name, func in funcs.available(self.ctx).items():
                if func.command and name not in entries:
                    entries[name] = f"func · {func.description or 'runs its steps'}"
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

    def _env(self, segment: list[str], word: str) -> Iterable[Completion]:
        """`env <Tab>`: its actions, then variable names (all for a lookup, this session's for unset)."""
        import os

        session = {e.name: "set in this session" for e in envvars.session(self.ctx)}
        if len(segment) == 1:
            options = {"set": "set a variable for this session", "unset": "remove one set here",
                       "-a": "the whole environment"}
            if word:  # a lookup: `env PATH`
                options |= {n: "environment variable" for n in os.environ}
            yield from self._complete(word, options | session)
        elif segment[1] == "set" and len(segment) == 2:
            yield from self._complete(word, {"-s": "a secret: masked, hidden typing, not in history"} | session)
        elif segment[1] == "unset":
            yield from self._complete(word, {n: d for n, d in session.items() if n not in segment[2:]})
        elif segment[1] not in ("set", "-a", "--all"):
            yield from self._complete(word, {n: "environment variable" for n in os.environ if n not in segment})

    def _profile(self, segment: list[str], word: str) -> Iterable[Completion]:
        """`profile <Tab>`: its actions, then profile names, starters, files and keys where they go."""
        from shellcraft import profiles

        names = {n: "profile" for n in profiles.names()}
        if len(segment) == 1:
            yield from self._complete(word, {
                "list": "the profiles", "show": "what a profile sets", "use": "switch to a profile",
                "off": "back to the default setup", "new": "create one (a wizard)", "save": "this session as a profile",
                "edit": "open a profile file in $EDITOR", "set": "change a profile's description, color, theme…",
                "env": "a profile's environment variables", "diff": "what two profiles set differently",
                "copy": "copy a profile", "rename": "rename a profile", "delete": "delete a profile",
                "reload": "apply the active profile again", "export": "write a profile to a JSON file",
                "import": "read a profile from a JSON file", "path": "a profile's folder",
                "starters": "the starter profiles"})
            return
        action, prev = segment[1], segment[-1]
        if prev == "--template":
            yield from self._complete(word, {n: s["data"]["description"] for n, s in profiles.STARTERS.items()})
        elif prev == "--from":
            yield from self._complete(word, names)
        elif action == "use" and len(segment) == 2:
            yield from self._complete(word, names | {"default": "the base setup"})
        elif action in ("show", "edit", "set", "env", "diff", "copy", "rename", "delete", "export", "path", "save") \
                and len(segment) == 2:
            extra = {"default": "the base setup"} if action in ("show", "diff") else {}
            yield from self._complete(word, names | extra)
        elif action == "diff" and len(segment) == 3:
            yield from self._complete(word, names | {"default": "the base setup"})
        elif action == "edit" and len(segment) == 3:
            yield from self._complete(word, {"rc": "lines run when it starts", "aliases": "its aliases",
                                             "config": "profile.json"})
        elif action == "set" and len(segment) == 3:
            yield from self._complete(word, {"description": "", "color": "#rrggbb", "theme": "",
                                             "history": "own|shared"})
        elif action == "set" and len(segment) == 4 and segment[3] == "history":
            yield from self._complete(word, {"own": "its own history", "shared": "the base history"})
        elif action == "env" and len(segment) == 3:
            yield from self._complete(word, {"set": "add a variable", "unset": "remove one"})
        elif action == "new" and len(segment) >= 3:
            yield from self._complete(word, {"--template": "start from a starter", "--from": "copy a profile",
                                             "--empty": "nothing set", "-d": "a description", "--use": "switch to it"})
        elif action == "import" and len(segment) == 2:
            yield from self._paths(word, None, CompleteEvent())

    def _func(self, segment: list[str], word: str) -> Iterable[Completion]:
        """`func <Tab>`: its actions, then func names, step numbers and header fields where they go."""
        names = {n: f.description or f.where for n, f in funcs.available(self.ctx).items()}
        if len(segment) == 1:
            yield from self._complete(word, {
                "list": "the funcs", "show": "a func's header and steps", "run": "run a func", "new": "make one",
                "save": "put a session func on disk", "edit": "open it in your editor", "add": "append a step",
                "insert": "insert a step", "rm": "remove a step", "move": "reorder a step",
                "set": "change a header field", "rename": "rename", "copy": "copy", "delete": "delete",
                "export": "write funcs to a JSON file", "import": "read funcs from a JSON file",
                "path": "where it lives"})
            return
        action = segment[1]
        args = segment[2:]
        if action == "run":
            if not [a for a in args if not a.startswith("-")]:
                yield from self._complete(word, {"-i": "isolated", "-b": "in the background", "-q": "quiet",
                                                 "-v": "show the output"} | names)
            return
        if action == "new":
            if args:
                yield from self._complete(word, {"--save": "keep it on disk", "-e": "write it in your editor",
                                                 "-g": "the default setup's", "-d": "a description",
                                                 "--system": "it runs OS commands", "--quiet": "quiet output",
                                                 "--command": "typing its name runs it"})
            return
        if not args and action in ("show", "view", "save", "edit", "add", "insert", "rm", "move", "set",
                                   "rename", "copy", "delete", "export", "path"):
            yield from self._complete(word, names | ({"--all": "every func"} if action == "export" else {}))
        elif action == "set" and len(args) == 1:
            yield from self._complete(word, {"description": "", "system": "yes|no", "output": "show|quiet",
                                             "command": "yes|no", "usage": ""})
        elif action == "set" and len(args) == 2:
            values = {"system": ("yes", "no"), "command": ("yes", "no"), "output": funcs.OUTPUTS}.get(args[1], ())
            yield from self._complete(word, {v: "" for v in values})
        elif action in ("rm", "insert", "move") and len(args) in (1, 2) and args[0] in names:
            if action == "insert" and len(args) == 2:
                return
            steps = funcs.available(self.ctx)[args[0]].steps
            yield from self._complete(word, {str(i): s[:50] for i, s in enumerate(steps, 1)})
        elif action == "import" and not args:
            yield from self._paths(word, None, CompleteEvent())

    def _aliases(self) -> dict[str, str]:
        return aliases.get_all(self.ctx.config) if self.ctx.allow_aliases else {}

    def _setting_names(self) -> dict[str, str]:
        keys = {name: entry.label for name, entry in env_settings(self.ctx.registry).items()}
        from shellcraft import loading
        from shellcraft.builtins import LOADER_LABEL, PREFIX_LABEL
        from shellcraft.prefix import CONFIG_KEY

        return ({k: s.label for k, s in SETTINGS.items()}
                | {CONFIG_KEY: PREFIX_LABEL, loading.CONFIG_KEY: LOADER_LABEL} | keys)

    def _complete(self, word: str, options: dict[str, str]) -> Iterable[Completion]:
        for name in sorted(options):
            if name.startswith(word):
                yield Completion(name, start_position=-len(word), display_meta=options[name])
