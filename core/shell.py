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


def _pretty_cwd() -> str:
    cwd = os.getcwd()
    home = str(Path.home())
    if cwd == home:
        return "~"
    if cwd.startswith(home + os.sep):
        return "~" + cwd[len(home):]
    return cwd


def _user_host() -> str:
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001 — no login name (containers, odd Windows setups)
        user = "user"
    return f"{user}@{socket.gethostname().split('.')[0]}"


class _RedactingHistory:
    """Mixin: store `settings NAME ••••` instead of an API key typed on the command line."""

    def append_string(self, string: str) -> None:
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

    def _goodbye(self, code: int) -> int:
        self.ui.console.print(Text("⏻ session closed — stay curious.", style="sc.muted"))
        return code
