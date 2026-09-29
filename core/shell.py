"""Interactive REPL built on prompt_toolkit."""

from __future__ import annotations

import getpass
import os
import socket
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.history import FileHistory, InMemoryHistory
from prompt_toolkit.styles import DynamicStyle
from rich.text import Text

from core import settings
from core.banner import render_banner
from core.completer import ShellCompleter
from core.config import history_path
from core.context import ShellContext, ShellExit
from core.output import make_spinner_runner, show, show_error
from core.parser import ParseError
from core.pipeline import PipelineError, run_line
from core.watch import ModuleWatcher


def _pretty_cwd(cwd: str | None = None, home: str | None = None) -> str:
    cwd = os.getcwd() if cwd is None else cwd
    home = str(Path.home()) if home is None else home
    # normcase: Windows paths are case-insensitive (C:\Users\Me vs c:\users\me).
    folded, folded_home = os.path.normcase(cwd), os.path.normcase(home)
    if folded == folded_home:
        return "~"
    if folded.startswith(folded_home.rstrip(os.sep) + os.sep):
        return "~" + cwd[len(home.rstrip(os.sep)):]
    return cwd


def _user_host() -> str:
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001 — no login name (containers, odd Windows setups)
        user = "user"
    return f"{user}@{socket.gethostname().split('.')[0]}"


class _RedactingHistory:
    """Mixin: store `settings NAME ••••` instead of an API key typed on the command line.

    A line typed with a leading space is not saved at all (bash's `ignorespace`).
    """

    def append_string(self, string: str) -> None:
        if string[:1].isspace():
            return
        super().append_string(settings.redact_line(string))


class RedactingFileHistory(_RedactingHistory, FileHistory):
    pass


class RedactingInMemoryHistory(_RedactingHistory, InMemoryHistory):
    pass


class Shell:
    def __init__(self, ctx: ShellContext):
        assert ctx.ui is not None
        self.ctx = ctx
        self.ui = ctx.ui
        self.last_failed = False
        self.user_host = _user_host()
        ctx.runner = make_spinner_runner(ctx)
        ctx.interactive = True
        self.watcher = ModuleWatcher(ctx.registry.directory)

        try:
            path = history_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            history = RedactingFileHistory(str(path))
        except OSError:
            history = RedactingInMemoryHistory()

        self.session: PromptSession = PromptSession(
            history=history,
            auto_suggest=AutoSuggestFromHistory(),
            completer=ShellCompleter(ctx),
            complete_while_typing=True,
            style=DynamicStyle(lambda: self.ui.pt_style),
            include_default_pygments_style=False,
        )

    def _prompt(self) -> FormattedText:
        parts = [
            ("class:frame", "╭─"), ("class:name", " ⚡shellcraft "), ("class:sep", "─ "),
            ("class:user", self.user_host), ("class:sep", " ─ "), ("class:path", _pretty_cwd()),
        ]
        if self.last_failed:
            parts += [("class:sep", " "), ("class:failed", "[✗]")]
        parts += [("", "\n"), ("class:frame", "╰─"), ("class:arrow", "❯ ")]
        return FormattedText(parts)

    def banner(self) -> None:
        self.ui.console.print(render_banner(self.ui, self.ctx.registry))

    def loop(self) -> int:
        while True:
            try:
                line = self.session.prompt(self._prompt)
            except KeyboardInterrupt:
                continue
            except EOFError:
                return self._goodbye(0)

            self._hot_reload()
            try:
                result = run_line(line, self.ctx)
            except ShellExit as exc:
                return self._goodbye(exc.code)
            except KeyboardInterrupt:
                self.ui.console.print(Text("^C interrupted", style="sc.warning"))
                self.last_failed = True
                continue
            except (ParseError, PipelineError) as exc:
                show_error(self.ui, exc, line)
                self.last_failed = True
                continue

            self.last_failed = False
            if result is not None:
                try:
                    show(self.ui, result.output, pager=settings.get(self.ctx.config, "pager"))
                except KeyboardInterrupt:
                    pass

    def _hot_reload(self) -> None:
        """Reload modules whose files changed since the last command (the `hot_reload` setting)."""
        changed = self.watcher.changes()  # always consumed, so turning the setting on starts fresh
        if not changed or not settings.get(self.ctx.config, "hot_reload"):
            return
        self.ctx.registry.load()
        msg = Text.assemble(("↻ ", "sc.accent"), (f"modules reloaded ({', '.join(changed)})", "sc.muted"))
        for warning in self.ctx.registry.warnings:
            msg.append(f"\n⚠ {warning}", style="sc.warning")
        self.ui.console.print(msg)

    def _goodbye(self, code: int) -> int:
        self.ui.console.print(Text("⏻ session closed — stay curious.", style="sc.muted"))
        return code
