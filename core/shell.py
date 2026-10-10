"""Interactive REPL built on prompt_toolkit."""

from __future__ import annotations

import contextlib
import getpass
import os
import socket
import sys
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.application.current import create_app_session
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.history import FileHistory, InMemoryHistory
from prompt_toolkit.styles import DynamicStyle
from rich.text import Text

from core import histexpand, script, settings
from core.banner import render_banner
from core.completer import ShellCompleter
from core.config import history_path
from core.console import DirectConsole, build_console, create_prompt_toolkit_console, truecolor_output
from core.context import ShellContext, ShellExit
from core.output import make_spinner_runner, show, show_error
from core.parser import ParseError
from core.pipeline import STATUS_INTERRUPTED, STATUS_SYNTAX, PipelineError, run_line
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
    """The interactive REPL. Use it as a context manager: on Windows it owns the console session
    (see _open_console), and leaving the `with` block restores the console whatever happened."""

    def __init__(self, ctx: ShellContext):
        assert ctx.ui is not None
        self.ctx = ctx
        self.ui = ctx.ui
        self.user_host = _user_host()
        self._cleanup = contextlib.ExitStack()
        self.direct_console: DirectConsole | None = None
        self.windows_console_error: str | None = None  # why CONIN$ / CONOUT$ couldn't be used
        try:
            self._open_console()
            self._setup()
        except BaseException:
            self.close()
            raise

    def _open_console(self) -> None:
        """Windows: run on CONIN$ / CONOUT$ directly (core.console.create_prompt_toolkit_console).
        Elsewhere: stdout, in truecolor when the terminal says it supports it. The app session must be
        current before the PromptSession is built, because prompt_toolkit binds an Application's input
        and output when it is created; the pager and the hidden API-key prompt pick it up the same way."""
        try:
            console = create_prompt_toolkit_console(self._cleanup)
        except OSError as exc:
            # Still usable through prompt_toolkit's own console handling, but say so: redirected
            # stdin / stdout are no longer handled.
            console = None
            self.windows_console_error = str(exc) or type(exc).__name__
            print(f"shellcraft: Windows console not opened ({self.windows_console_error}); "
                  "using stdin/stdout", file=sys.stderr)
        if console is None:
            output = truecolor_output()
            if output is not None:
                self._cleanup.enter_context(create_app_session(output=output))
            return
        self.direct_console = console
        self._cleanup.enter_context(create_app_session(input=console.input, output=console.output))
        # Rich is rebuilt on the CONOUT$ stream. It measures itself from the session's output, the
        # same as the prompt, so both agree on the width and follow resizes.
        previous = self.ui.use_console(build_console(file=console.stream))
        self._cleanup.callback(self.ui.use_console, previous)

    def _setup(self) -> None:
        ctx = self.ctx
        self._cleanup.callback(self._stop_mcp_http)  # registered last, so it runs first on close
        ctx.runner = make_spinner_runner(ctx)
        ctx.interactive = True
        # A script (`for`, `if`, `a ; b`) prints each statement's output and errors as it runs.
        ctx.emit = lambda value: show(self.ui, value, pager=False)
        ctx.emit_error = lambda exc, text: show_error(self.ui, exc, text)
        self.watcher = ModuleWatcher(ctx.registry.directory)

        try:
            path = history_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            history = RedactingFileHistory(str(path))
        except OSError:
            history = RedactingInMemoryHistory()

        ctx.history = history
        self.session: PromptSession = PromptSession(
            history=history,
            auto_suggest=AutoSuggestFromHistory(),
            completer=ShellCompleter(ctx),
            complete_while_typing=True,
            style=DynamicStyle(lambda: self.ui.pt_style),
            include_default_pygments_style=False,
        )
        # The loop saves each command itself, after `!!` / `!$` expansion and after joining the
        # continuation lines of a multi-line `for` / `if`, so ↑ and `history` show what ran.
        self.session.default_buffer.append_to_history = lambda: None  # type: ignore[method-assign]

    def _stop_mcp_http(self) -> None:
        if self.ctx.mcp_http is not None:
            self.ctx.mcp_http.stop()
            self.ctx.mcp_http = None

    def close(self) -> None:
        """Stop a server started with `mcp start`, put back the Rich console, leave the app
        session, restore the console mode and close CONIN$ / CONOUT$ (in that order)."""
        self._cleanup.close()

    def __enter__(self) -> Shell:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _prompt(self) -> FormattedText:
        parts = [
            ("class:frame", "╭─"), ("class:name", " ⚡shellcraft "), ("class:sep", "─ "),
            ("class:user", self.user_host), ("class:sep", " ─ "), ("class:path", _pretty_cwd()),
        ]
        parts += self._mcp_marker()
        if self.ctx.last_status:
            parts += [("class:sep", " "), ("class:failed", f"[✗ {self.ctx.last_status}]")]
        parts += [("", "\n"), ("class:frame", "╰─"), ("class:arrow", "❯ ")]
        return FormattedText(parts)

    @staticmethod
    def _continuation_prompt() -> FormattedText:
        return FormattedText([("class:frame", "  ┆ "), ("class:arrow", "… ")])

    def _mcp_marker(self) -> list[tuple[str, str]]:
        """`● mcp :8765` while a server started with `mcp start` runs; `✗ mcp :8765` if it died
        on its own (until `mcp` or `mcp stop` notices). Nothing when there is none."""
        child = self.ctx.mcp_http
        if child is None:
            return []
        address = f":{child.port}" if child.host in ("127.0.0.1", "localhost") else child.url.split("/")[2]
        if child.running():
            return [("class:sep", " ─ "), ("class:mcp", f"● mcp {address}")]
        return [("class:sep", " ─ "), ("class:mcp.down", f"✗ mcp {address}")]

    def banner(self) -> None:
        self.ui.console.print(render_banner(self.ui, self.ctx.registry))

    def diagnostics(self) -> None:
        """Print the console diagnostics report (the `diagnostics` setting, or --diag)."""
        from core.diagnostics import console_report

        # Plain text, not wrapped, so it can be copied into a bug report as is.
        self.ui.console.print(console_report(self.ui, self.direct_console, self.windows_console_error),
                              markup=False, highlight=False, soft_wrap=True)

    def loop(self) -> int:
        while True:
            try:
                line = self.session.prompt(self._prompt)
                line = self._read_continuation(line)
            except KeyboardInterrupt:
                continue
            except EOFError:
                return self._goodbye(0)
            if line is None:
                continue

            try:
                line, expanded = histexpand.expand(line, self._previous_command())
            except ParseError as exc:
                show_error(self.ui, exc, line)
                self.ctx.last_status = STATUS_SYNTAX
                continue
            if expanded:
                self.ui.console.print(Text(line, style="sc.muted"), markup=False, highlight=False)
            self._remember(line)

            self._hot_reload()
            try:
                result = run_line(line, self.ctx)
            except ShellExit as exc:
                return self._goodbye(exc.code)
            except KeyboardInterrupt:
                self.ui.console.print(Text("^C interrupted", style="sc.warning"))
                self.ctx.last_status = STATUS_INTERRUPTED
                continue
            except (ParseError, PipelineError) as exc:
                show_error(self.ui, exc, line)
                continue

            if result is not None:
                try:
                    show(self.ui, result.output, pager=settings.get(self.ctx.config, "pager"))
                except KeyboardInterrupt:
                    pass

    def _read_continuation(self, line: str) -> str | None:
        """While a `{` or `(` is still open, read more lines (with a `┆ …` prompt) and join them
        into one line. Ctrl-C drops the whole command; Ctrl-D reports what is missing."""
        while script.incomplete(line) is not None:
            try:
                more = self.session.prompt(self._continuation_prompt)
            except KeyboardInterrupt:
                self.ctx.last_status = STATUS_INTERRUPTED
                return None
            except EOFError:
                try:
                    script.parse_script(line)
                except ParseError as exc:
                    show_error(self.ui, ParseError(f"{exc.message} (end of input)", exc.pos), line)
                self.ctx.last_status = STATUS_SYNTAX
                return None
            if more.strip():
                line = script.join_continuation(line, more)  # adds `;` only where one is needed
        return line

    def _previous_command(self) -> str | None:
        """The last saved command, for `!!` and `!$` (from earlier sessions too, as in bash)."""
        history = self.ctx.history
        strings = history.get_strings()
        if strings:
            return strings[-1]
        if not getattr(history, "_loaded", True):  # the prompt loads the file in the background
            return next(iter(history.load_history_strings()), None)
        return None

    def _remember(self, line: str) -> None:
        """Save a command to the history (what prompt_toolkit would do, after our expansion)."""
        if not line.strip():
            return
        strings = self.ctx.history.get_strings()
        if not strings or strings[-1] != line:
            self.ctx.history.append_string(line)

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
