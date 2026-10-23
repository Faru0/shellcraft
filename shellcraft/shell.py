"""Interactive REPL built on prompt_toolkit."""

from __future__ import annotations

import contextlib
import getpass
import os
import re
import signal
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from prompt_toolkit import PromptSession
from prompt_toolkit.application.current import create_app_session
from prompt_toolkit.application.run_in_terminal import run_in_terminal
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import FormattedText, StyleAndTextTuples
from prompt_toolkit.history import FileHistory, InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings, KeyPress
from prompt_toolkit.key_binding.bindings.mouse import load_mouse_bindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.menus import CompletionsMenuControl
from prompt_toolkit.lexers import DynamicLexer
from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType
from prompt_toolkit.search import SearchDirection, do_incremental_search
from prompt_toolkit.styles import DynamicStyle
from rich.rule import Rule
from rich.text import Text

from shellcraft import aliases, funcs, gitinfo, histexpand, prefix, profiles, script, settings
from shellcraft.alerts import AlertBus
from shellcraft.banner import render_banner
from shellcraft.builtins import BUILTINS
from shellcraft.completer import ShellCompleter
from shellcraft.config import config_dir
from shellcraft.console import DirectConsole, build_console, create_prompt_toolkit_console, truecolor_output
from shellcraft.context import CommandError, ShellContext, ShellExit, set_width_source
from shellcraft.footer import MARK, Footer, panel_lines, parse_mouse, wheel_up
from shellcraft.highlight import KEYWORDS, ShellLexer
from shellcraft.jobs import JobTable, depth
from shellcraft.output import make_spinner_runner, show, show_error, write_plain
from shellcraft.parser import ParseError
from shellcraft.pipeline import STATUS_INTERRUPTED, STATUS_SYNTAX, PipelineError, run_line
from shellcraft.watch import ModuleWatcher

# Prompt segments, in the order `prompt` lists them. The left prompt and the right prompt
# (`prompt -r`) are each a list of these names, saved under "prompt" in config.json.
SEGMENTS: dict[str, str] = {
    "name": "the ⚡shellcraft label (with [N] inside `sub shellcraft`)",
    "profile": "◆ NAME while a profile is active (`profile use NAME`)",
    "user": "user@host",
    "path": "the working directory (~ for home)",
    "git": "⎇ branch, when the directory is in a git repository",
    "mcp": "● mcp :PORT while `mcp start` runs",
    "status": "[✗ N] after a command fails",
    "duration": "⏱ 4.2s when the last command took more than 2 seconds",
    "clock": "the time (HH:MM)",
    "python": "the Python version",
    "jobs": "N sub while subshells started with `sub` are running",
}
DEFAULT_LEFT = ["name", "profile", "user", "path", "git", "mcp", "status", "duration"]
DEFAULT_RIGHT: list[str] = []
SLOW_SECONDS = 2.0  # show the duration of commands that took longer than this


# What the link hands an AI co-pilot at most (shellcraft.mainlink; the MCP tools ask for less).
LINK_SCROLLBACK_LINES = 2000
LINK_SCROLLBACK_CHARS = 512 * 1024
LINK_JOB_LINES = 200
LINK_JOB_MAX_LINES = 5000
LINK_JOB_CHARS = 256 * 1024
LINK_HISTORY_LINES = 500
_SGR = re.compile(r"\x1b\[[0-9;]*m")


def _clamp(value: Any, low: int, high: int) -> int:
    try:
        return max(low, min(int(value), high))
    except (TypeError, ValueError):
        return low if low == high else min(high, max(low, 200))


def _tail(head: dict[str, Any], text: str, limit: int) -> dict[str, Any]:
    """`head` plus `text`, cut to its last `limit` characters (what matters is at the end)."""
    truncated = len(text) > limit
    head.update({"text": text[-limit:] if truncated else text, "truncated": truncated})
    return head


def _pretty_cwd(cwd: str | None = None, home: str | None = None) -> str:
    if cwd is None:
        try:
            cwd = os.getcwd()
        except OSError:  # the directory was deleted under us
            return "(deleted)"
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


def format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, seconds = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def prompt_segments(config: dict[str, Any], side: str) -> list[str]:
    """The segment names saved for one side of the prompt ("left" or "right"), or the default."""
    section = config.get("prompt")
    value = section.get(side) if isinstance(section, dict) else None
    if isinstance(value, list) and all(isinstance(v, str) and v in SEGMENTS for v in value):
        return value
    return DEFAULT_LEFT if side == "left" else DEFAULT_RIGHT


class _RedactingHistory:
    """Mixin: store `settings NAME ••••` instead of an API key typed on the command line.

    A line typed with a leading space is not saved at all (bash's `ignorespace`). `aliases`
    (a callable) lets the redaction see through an alias of `settings`.
    """

    aliases: Callable[[], dict[str, str]] | None = None

    def append_string(self, string: str) -> None:
        if string[:1].isspace():
            return
        names = self.aliases() if self.aliases is not None else None
        super().append_string(settings.redact_line(string, names))


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
        self.last_duration: float = 0.0  # seconds the last command took
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
        """Windows: run on CONIN$ / CONOUT$ directly (shellcraft.console.create_prompt_toolkit_console).
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
        # The scroll view: what the console prints is kept, so the wheel can scroll back through
        # it while the shell has the mouse (shellcraft.scrollback).
        from shellcraft.scrollback import CaptureFile, Scrollback

        self.scrollback = Scrollback()
        terminal = self.ui.console.file
        self.ui.console.file = CaptureFile(terminal, self.scrollback)
        self._cleanup.callback(setattr, self.ui.console, "file", terminal)
        ctx.jobs = JobTable(config_dir() / "jobs")
        ctx.jobs.on_change = self._jobs_changed
        self._cleanup.callback(self._stop_jobs)
        # The tab bar on the terminal's last row: this shell and its subshells. Enabled by loop()
        # (after the banner), redrawn after every prompt render, gone at exit.
        # Alerts from subshells, nested shells and MCP clients arrive through the spool folder
        # ~/.shellcraft/alerts, which a thread watches (shellcraft.alerts).
        ctx.alerts = AlertBus()
        ctx.alerts.on_change = self._jobs_changed
        ctx.alerts.on_request = self._sub_requested
        if not ctx.lite:  # the AI's review requests become alerts until answered (shellcraft.review_spool)
            self._start_review_watcher()
        from shellcraft import mainlink

        if ctx.lite:  # one clip register: the main shell's, asked for over its private channel
            ctx.alerts.remote_clip = mainlink.available()
        else:
            try:
                from shellcraft.jobs import MAIN_LINK_ENV

                link = mainlink.MainLink(ctx.alerts)
                MAIN_LINK_ENV.update(link.env)  # handed to every `sub shellcraft` and the `mcp start` child
                self._install_link_handlers(link)  # what an AI co-pilot may ask (shellcraft.mainlink)
                self._cleanup.callback(link.close)
                self._cleanup.callback(MAIN_LINK_ENV.clear)
            except OSError:
                pass  # no channel: a lite subshell keeps a clip register of its own
        ctx.alerts.on_recording = self._sub_recording
        ctx.jobs.on_request = self._open_requests_attached
        if not ctx.lite:  # a lite (nested) shell leaves the spool to the main shell: `alert` forwards to it
            try:
                ctx.alerts.watch(config_dir())
            except OSError:
                pass  # no spool: `alert` still works inside this shell
        self._cleanup.callback(ctx.alerts.close)
        from shellcraft import recording

        self._cleanup.callback(recording.stop, ctx)  # a transcript still open at exit is closed
        self.footer = ctx.footer = Footer(self.ui, ctx.jobs, depth(), alerts=ctx.alerts)
        self.footer.show_hint = lambda: bool(settings.get(ctx.config, "panel_hint"))
        self.footer.is_recording = lambda: ctx.recording is not None
        self.footer.on_change = self._jobs_changed
        ctx.console_report = self.report  # the `diag` command
        self._cleanup.callback(self.footer.disable)
        self._default_mouse = load_mouse_bindings()
        # The panel under the prompt: one line per subshell. `_panel_hover` is the job under
        # the mouse, `_panel_cursor` the one chosen with ↓ / ↑ (None: the input line has the keys).
        self._panel_hover: Any = None
        self._panel_hover_timer: Any = None  # threading.Timer clearing _panel_hover
        self._pressed = "none"  # the button of the last press: Windows reports every release as NONE
        # The wheel at the prompt hands the mouse back to the terminal (its own scrolling) until
        # the next key press or prompt (_footer_mouse, _mouse_wanted).
        self._mouse_released = False
        self._requests_lock = threading.Lock()  # _open_requests: from a wake and from the loop top
        self._yank: tuple[int, int, str] | None = None  # the last Alt-v: (copy number, where, the text)
        ctx.runner = make_spinner_runner(ctx)
        ctx.interactive = True
        set_width_source(self.ui)  # `help | grep x` wraps at the terminal's width, not a fixed 100
        self._cleanup.callback(set_width_source, None)
        # A script (`for`, `if`, `a ; b`) prints each statement's output and errors as it runs.
        ctx.emit = lambda value: show(self.ui, value, pager=False)
        ctx.live = self._print_live  # a pipeline's output that keeps coming (tail -f | grep), as it comes
        self.ui.mouse = settings.get(ctx.config, "mouse")
        self.ui.mouse_always = settings.get(ctx.config, "mouse_always")
        ctx.emit_error = lambda exc, text: show_error(self.ui, exc, text)
        # `echo oops >&2`: the error stream is shown in the error style, right away.
        ctx.emit_stderr = lambda text: self.ui.console.print(Text(text.rstrip("\n"), style="sc.error"),
                                                             markup=False, highlight=False)
        self.watcher = ModuleWatcher(*ctx.registry.directories)

        history = self._open_history()
        ctx.history = history
        ctx.profile_hook = self._profile_switched
        self._lexer = ShellLexer(self._is_known_command)
        self.session: PromptSession = PromptSession(
            # Windows: bound to CONIN$ / CONOUT$ explicitly (the app session does the same, but an
            # explicit binding survives whatever else touches the current session).
            input=self.direct_console.input if self.direct_console is not None else None,
            output=self.direct_console.output if self.direct_console is not None else None,
            history=history,
            auto_suggest=AutoSuggestFromHistory(),
            completer=ShellCompleter(ctx),
            complete_while_typing=True,
            style=DynamicStyle(lambda: self.ui.pt_style),
            include_default_pygments_style=False,
            lexer=DynamicLexer(self._current_lexer),
            rprompt=self._rprompt,
            # Checked on every render: prompt_toolkit turns the terminal's mouse reporting on and
            # off as the menu opens and closes, so a click picks a completion and the wheel scrolls
            # the list, while the terminal keeps the mouse (selection, scrollback) at other times.
            mouse_support=Condition(self._mouse_wanted),
            key_bindings=self._key_bindings(),
        )
        self.session.app.after_render += lambda _app: self.footer.draw()
        self._shorten_output_for_footer()
        # The loop saves each command itself, after `!!` / `!$` expansion and after joining the
        # continuation lines of a multi-line `for` / `if`, so ↑ and `history` show what ran.
        self.session.default_buffer.append_to_history = lambda: None  # type: ignore[method-assign]
        # The toolbar line only exists while there is something on the line to describe.
        self.session.default_buffer.on_text_changed += lambda _buffer: self._update_toolbar()
        self.session.default_buffer.on_cursor_position_changed += lambda _buffer: self._take_mouse_back()
        self._install_mouse()
        self.last_error: tuple[Exception, str] | None = None  # (exception, command line)
        self._interrupt_dropped = False  # a Ctrl-C between commands (see _dropped_interrupt)
        self._tty_attrs: Any = None  # the terminal's attributes before keep_output_on_interrupt

    def _print_live(self, item: Any) -> None:
        """One streamed item of a running pipeline, printed at once (no pager: it is still coming)."""
        from shellcraft.context import Styled

        console = self.ui.console
        if isinstance(item, Styled):
            item = item.renderable
        if isinstance(item, str):
            if write_plain(console, item[:-1] if item.endswith("\n") else item):
                return
            item = Text.from_ansi(item[:-1] if item.endswith("\n") else item)
        console.print(item, markup=False, highlight=False)
        console.file.flush()

    def run_rc(self) -> None:
        """Source ~/.shellcraft/rc, as if each line were typed (shellcraft.startup.source)."""
        from shellcraft import startup

        path = startup.rc_path()
        try:
            startup.source(path, self.ctx)
        except (OSError, UnicodeDecodeError, RecursionError) as exc:
            self.ui.console.print(Text(f"⚙ {path}: {exc}", style="sc.warning"), markup=False, highlight=False)

    def _stop_jobs(self) -> None:
        """At exit: stop the subshells still running, and say so."""
        jobs = self.ctx.jobs
        if jobs is None:
            return
        stopped = jobs.cleanup()
        if stopped:
            names = ", ".join(f"[{j.number}] {j.command}" for j in stopped)
            self.ui.console.print(Text(f"stopped: {names}", style="sc.muted"), markup=False, highlight=False)

    REVIEW_LEVELS = {"low": "info", "medium": "warn", "high": "crit"}

    def _start_review_watcher(self, interval: float = 1.0) -> None:
        """A thread that looks at the review spool every `interval`: an AI's pending review request
        becomes a sticky alert (its level from the request's priority, its data `review show ID`),
        and the alert goes when the request is answered, here or from another shell."""
        import threading

        from shellcraft import review_spool as spool

        stop = threading.Event()
        self._review_alerts: dict[str, Any] = {}

        def check() -> None:
            bus = self.ctx.alerts
            if bus is None:
                return
            pending = {i["id"]: i for i in spool.list_items("pending", "review")}
            for item_id, item in pending.items():
                if item_id not in self._review_alerts:
                    level = self.REVIEW_LEVELS.get(item.get("priority"), "warn")
                    kind = item.get("type")
                    head = ("AI wants to run" if kind == "command" else
                            "AI asks to run commands for this session" if kind == "grant" else "AI asks for review")
                    self._review_alerts[item_id] = bus.post(
                        f"{head}: {item.get('title', '')}", level, timeout=None,
                        source=str(item.get("created_by", "")), content=f"review show {item_id}")
            for item_id in [i for i in self._review_alerts if i not in pending]:
                bus.remove(self._review_alerts.pop(item_id))

        def run() -> None:
            last = None
            while not stop.wait(interval):
                try:
                    now = spool.signature()
                    if now != last:
                        last = now
                        check()
                except Exception:  # noqa: BLE001 — a spool problem must not kill the watcher
                    continue

        self._review_check = check  # for tests and for a check right after `review approve`
        thread = threading.Thread(target=run, daemon=True, name="shellcraft:review-watcher")
        thread.start()
        self._cleanup.callback(stop.set)

    def _sub_recording(self, data: dict[str, Any]) -> None:
        """A subshell started or stopped `record` (from the watcher thread): its line in the
        footer shows it (its bubble in red)."""
        from shellcraft.alerts import job_of

        source = str(data.get("source", ""))
        number = job_of(source)
        job = self.ctx.jobs.get(number) if self.ctx.jobs is not None and number is not None else None
        if self.ctx.jobs is not None:  # for `diag`: what arrived, and which line it lit
            self.ctx.jobs.recording_signals.append(
                f"{'on' if data.get('on') else 'off'} from {source or '(no source)'} -> "
                + (f"job {number}" if job is not None else "no matching job"))
        if job is not None:
            job.recording = bool(data.get("on"))
            self._jobs_changed()

    def _sub_requested(self, data: dict[str, Any]) -> None:
        """`sub …` typed inside a subshell (from the watcher thread): queued, then opened under
        main in the background: right away above the prompt when the prompt is up, else by the
        attach loop or the next prompt."""
        if self.ctx.jobs is not None:
            self.ctx.jobs.requests.append(data)
        self._jobs_changed()
        self._wake(self._open_requests)

    def _wake(self, fn: Callable[[], None]) -> bool:
        """From another thread: run `fn` above the prompt now, if the prompt is up (the way a
        click on the footer runs a command there). False when it isn't: a command is running or
        a subshell is attached, and the loop top or the attach loop does what `fn` would have."""
        app = getattr(self.session, "app", None)
        loop = getattr(app, "loop", None)
        if app is None or not app.is_running or loop is None or loop.is_closed():
            return False
        try:
            loop.call_soon_threadsafe(lambda: run_in_terminal(lambda: self._guarded(fn, "a background update")))
        except RuntimeError:
            return False
        return True

    def _guarded(self, fn: Callable[[], Any], where: str) -> None:
        """Run `fn` above the prompt (from a click or a wake): an error is shown and logged, and
        never reaches prompt_toolkit's event loop, which would end the prompt."""
        try:
            fn()
        except (KeyboardInterrupt, ShellExit):
            pass
        except Exception as exc:  # noqa: BLE001
            self._internal_error(exc, where)

    def _open_requests(self) -> None:
        """At the prompt: open the requested subshells in the background and say so."""
        from shellcraft.builtins import open_requested

        with self._requests_lock:  # a wake and the loop top may both get here
            for note in open_requested(self.ctx):
                self.ui.console.print(Text(note, style="sc.muted"), markup=False, highlight=False)

    # ── what an AI co-pilot may ask over the link (shellcraft.mainlink) ───────

    def _install_link_handlers(self, link: Any) -> None:
        link.handle("ping", self._link_ping)
        link.handle("scrollback", self._link_scrollback)
        link.handle("jobs", self._link_jobs)
        link.handle("job-output", self._link_job_output)
        link.handle("history", self._link_history)
        link.handle("open-sub", self._link_open_ai_sub)
        link.handle("run", self._link_run)
        from shellcraft import briefs

        briefs.sweep()  # briefs no subshell ever read

    def _link_open_ai_sub(self, spec: Any) -> Any:
        """`open_subshell` (from the link thread): a lite ShellCraft named after the AI's finding,
        started in the background, that prints the AI's brief first; an alert points to it, and
        the note is printed above the prompt right away. Answers {"number", "title"}."""
        from shellcraft import briefs
        from shellcraft.builtins import _start_sub
        from shellcraft.context import CommandError

        if not isinstance(spec, dict):
            return ("error", "open-sub needs a mapping")
        if self.ctx.jobs is None:
            return ("error", "this shell has no job table")
        name = " ".join(str(spec.get("name") or "").split())[:40] or "AI"
        title = " ".join(str(spec.get("title") or name).split())[:120]
        text = str(spec.get("text") or "")
        source = str(spec.get("source") or "mcp")
        cwd = spec.get("cwd")
        cwd = cwd if isinstance(cwd, str) and os.path.isdir(cwd) else os.getcwd()
        path = briefs.write(title, name, text, source, cwd)
        try:
            kind, started = _start_sub(self.ctx, self.ctx.jobs, ["shellcraft", "--no-banner", "--brief", str(path)],
                                       cwd, name=f"AI · {name}", origin="ai")
        except CommandError as exc:
            briefs.remove(path)
            return ("error", str(exc))
        if kind != "program":
            briefs.remove(path)
            return ("error", "subshells need a pseudo-terminal here (pip install pywinpty on Windows)")
        job = started
        self.ctx.jobs.notifications.append(
            f"[{job.number}] ✦ {job.title} opened by {source} ({prefix.label()} {job.number} switches to it)")
        if self.ctx.alerts is not None:
            self.ctx.alerts.post(title, spec.get("level") or "info", source=source, content=f"jobs {job.number}")
        self._wake(self._report_finished)
        return {"number": job.number, "title": job.title}

    def _link_run(self, spec: Any) -> Any:
        """`run_in_shell` (from the link thread): a read-only command, or any command with the
        session grant on, runs now as a background command job named `✦ AI: …` (its trace shows
        above the prompt as a finished `cmd &` does); the answer carries its output once it is
        done, within `wait` seconds. Anything else answers {"needs_approval": true}: the MCP
        server then puts it in the review queue."""
        from shellcraft import ai_run
        from shellcraft.builtins import command_job

        if not isinstance(spec, dict) or not isinstance(spec.get("command"), str) or not spec["command"].strip():
            return ("error", "run needs a command")
        if self.ctx.jobs is None:
            return ("error", "this shell has no job table")
        command = spec["command"].strip()
        mode = ai_run.tier(command, self.ctx)
        if mode == "approval":
            return {"needs_approval": True, "mode": "approval"}
        job = command_job(self.ctx, self.ctx.jobs, command, name=ai_run.job_name(command), origin="ai")
        wait = _clamp(spec.get("wait", 0), 0, 600)
        deadline = time.monotonic() + wait
        while job.running and time.monotonic() < deadline:
            time.sleep(0.05)
        if not job.running:
            self._wake(self._report_finished)  # the trace, now, if the prompt is up
        answer = ai_run.output_of(job)
        answer.update({"mode": mode, "pending": job.running, "source": str(spec.get("source") or "")})
        return answer

    def show_brief(self, path: Any) -> None:
        """A lite shell opened by an AI (`--brief FILE`): print what it left, then delete the file."""
        from shellcraft import briefs
        from shellcraft.context import Paged

        data = briefs.read(path)
        console = self.ui.console
        if data is None:
            console.print(Text(f"brief {path}: missing or unreadable", style="sc.muted"))
            return
        stamp = time.strftime("%H:%M", time.localtime(float(data.get("created") or time.time())))
        console.print(Text.assemble(("✦ AI · ", "sc.accent"), (str(data.get("title", "")), "bold"),
                                    (f"   from {data.get('source', '')} · {stamp}", "sc.muted")))
        console.print(Rule(style="sc.border"))
        text = data["text"].rstrip("\n")
        if text:
            value: Any = Paged(text, page=True) if text.count("\n") > 40 else text
            show(self.ui, value, pager=settings.get(self.ctx.config, "pager"), title=f"AI · {data.get('title', '')}")
        console.print(Text(f"(opened by the AI: `exit` returns to main, {prefix.label()} switches)", style="sc.muted"))

    def _link_ping(self) -> dict[str, Any]:
        from shellcraft import __version__

        return {"pid": os.getpid(), "version": __version__, "cwd": os.getcwd(),
                "profile": profiles.active(self.ctx.config) or profiles.DEFAULT}

    def _link_scrollback(self, lines: Any = LINK_SCROLLBACK_LINES, strip_ansi: Any = True) -> dict[str, Any]:
        """The last `lines` of what this shell printed (commands as `❯ cmd`, then their output)."""
        lines = _clamp(lines, 1, LINK_SCROLLBACK_LINES)
        text = self.scrollback.text()
        if strip_ansi:
            text = _SGR.sub("", text)
        kept = text.split("\n")[-lines:]
        return _tail({"lines": len(kept)}, "\n".join(kept), LINK_SCROLLBACK_CHARS)

    def _link_jobs(self) -> list[dict[str, Any]]:
        jobs = self.ctx.jobs
        return [] if jobs is None else [
            {"number": j.number, "title": j.title, "name": j.name, "command": j.command, "kind": j.kind,
             "label": j.label, "running": j.running, "elapsed": round(j.elapsed), "origin": j.origin}
            for j in jobs]

    def _link_job_output(self, number: Any = 0, lines: Any = LINK_JOB_LINES, strip_ansi: Any = True) -> Any:
        """The last `lines` a subshell printed (its log): raw terminal output, so every escape
        sequence goes with `strip_ansi`, not only the colors."""
        job = self.ctx.jobs.get(number) if self.ctx.jobs is not None and isinstance(number, int) else None
        if job is None:
            return ("error", f"no subshell {number} (get_jobs lists them)")
        lines = _clamp(lines, 1, LINK_JOB_MAX_LINES)
        text = job.output
        if strip_ansi:
            from shellcraft.scrollback import _NOT_SGR

            text = _SGR.sub("", _NOT_SGR.sub("", text)).replace("\r\n", "\n")
        kept = text.split("\n")[-lines:]
        head = {"number": job.number, "title": job.title, "label": job.label, "running": job.running}
        return _tail(head, "\n".join(kept), LINK_JOB_CHARS)

    def _link_history(self, n: Any = LINK_HISTORY_LINES) -> dict[str, Any]:
        """The last `n` commands typed, API keys redacted once more (the history file redacts on
        save; older files may predate that)."""
        n = _clamp(n, 1, LINK_HISTORY_LINES)
        names = aliases.get_all(self.ctx.config)
        return {"lines": [settings.redact_line(line, names) for line in self._history_lines()[-n:]]}

    def _open_requests_attached(self) -> None:
        """Inside a subshell (the attach loop): the same, with the notes kept for the next prompt."""
        from shellcraft.builtins import open_requested

        if self.ctx.jobs is not None:
            self.ctx.jobs.notifications.extend(open_requested(self.ctx))

    def _jobs_changed(self) -> None:
        """A job started or finished, or an alert came or went (from another thread): redraw the
        panel at the prompt, or tell the attach loop to redraw the footer."""
        self.footer.dirty = True
        try:
            self.session.app.invalidate()
        except Exception:  # noqa: BLE001 — no app running: the next prompt shows it
            pass

    def _shorten_output_for_footer(self) -> None:
        """While the footer is drawn at the prompt, prompt_toolkit (and Rich, which asks it) must
        think the screen ends where the footer starts: the rows it reports, and on Windows the rows
        below the cursor, are reduced by the footer's height. The footer is outside the scroll
        region, so the prompt and its menus never reach it."""
        from prompt_toolkit.data_structures import Size

        output = self.session.app.output
        real_size = output.get_size
        real_below = getattr(output, "get_rows_below_cursor_position", None)

        def get_size() -> Size:
            size = real_size()
            if self.footer.enabled:
                return Size(rows=max(3, size.rows - self.footer.height()), columns=size.columns)
            return size

        output.get_size = get_size  # type: ignore[method-assign]
        if real_below is not None:
            def below() -> int:
                rows = real_below()
                return max(1, rows - self.footer.height()) if self.footer.enabled else rows

            output.get_rows_below_cursor_position = below  # type: ignore[method-assign]

    def _footer_at_prompt(self) -> None:
        """Keep the footer on at the prompt while the `footer` setting is on (and this is a
        terminal), off otherwise; checked before every prompt, so `settings footer off` acts
        at once."""
        wanted = (settings.get(self.ctx.config, "footer") and bool(self.ui.console.is_terminal)
                  and not getattr(self.ctx, "lite", False))  # a lite subshell: the main shell's footer is there
        if wanted and not self.footer.at_prompt:
            self.footer.at_prompt = True
            self.footer.enable()
        elif not wanted and self.footer.at_prompt:
            self.footer.at_prompt = False
            self.footer.disable()

    def _key_bindings(self) -> KeyBindings:
        """The prefix key (Ctrl-], or `settings prefix_key`) then a digit selects a tab (attach to
        a subshell, or show its output), the same key as inside an attached program. (Alt-N is left
        alone: terminals use it for their own tabs.) Mouse events are looked at first for a click on
        the footer's tabs."""
        kb = KeyBindings()
        for choice, spec in prefix.KEYS.items():  # every choice bound, only the chosen one live
            chosen = Condition(lambda choice=choice: prefix.name() == choice)
            for digit in "0123456789":
                @kb.add(spec.pt, digit, filter=chosen)
                def _(event: Any, number: int = int(digit)) -> None:
                    if number:
                        run_in_terminal(lambda: self._run_clicked(f"jobs {number}"))

            @kb.add(spec.pt, "c", filter=chosen)  # the prefix, then c: fold the subshell rows, or unfold them
            def _fold(event: Any) -> None:
                self.footer.toggle_collapsed()

        @kb.add("c-z")
        def _(event: Any) -> None:
            """Ctrl-Z: ignored. prompt_toolkit would insert a ^Z character; suspending would stop
            the shell with nothing to resume it when ShellCraft is the terminal's own shell."""

        @kb.add("c-s", filter=Condition(lambda: prefix.name() == "ctrl-s"))
        def _prefix_alone(event: Any) -> None:
            pass  # Ctrl-S as the prefix, alone at the prompt: nothing (not prompt_toolkit's forward search)

        @kb.add(Keys.Vt100MouseEvent)
        @kb.add(Keys.WindowsMouseEvent)
        def _mouse(event: Any) -> Any:
            return self._footer_mouse(event)

        @kb.add("escape", "c")  # Alt-c (or Esc then c): copy the top alert, as a right-click on it does
        def _copy_alert(event: Any) -> None:
            bus = self.ctx.alerts
            current = bus.current() if bus is not None else []
            if current:
                bus.copy(current[0])

        # Alt-v (Esc then v) pastes the clip; again right away, an older copy. Esc then Ctrl-V too:
        # that is Ctrl+Alt+v, which AltGr+v is on Windows (AltGr is right Alt + Ctrl) and what
        # Linux terminals and the Windows VT key reader send for it.
        @kb.add("escape", "v")
        @kb.add("escape", "c-v")
        def _paste_clip(event: Any) -> None:
            self._paste_clip(event.current_buffer)

        # ↓ on an empty line moves into the panel ("↓ to manage"); ↑ / ↓ walk it, Enter switches
        # to the chosen subshell, Esc (or typing) leaves it.
        in_panel = Condition(lambda: self._panel_cursor is not None)
        can_enter = Condition(lambda: self._panel_cursor is None and self._stack_shown()
                              and not self.session.default_buffer.text
                              and self.session.default_buffer.complete_state is None)

        @kb.add("down", filter=can_enter)
        def _enter_panel(event: Any) -> None:
            self._panel_cursor = 0

        @kb.add("down", filter=in_panel)
        def _panel_down(event: Any) -> None:
            self._panel_cursor = min(len(self._panel_jobs()) - 1, (self._panel_cursor or 0) + 1)

        @kb.add("up", filter=in_panel)
        def _panel_up(event: Any) -> None:
            self._panel_cursor = None if not self._panel_cursor else self._panel_cursor - 1

        @kb.add("escape", filter=in_panel, eager=True)
        def _panel_leave(event: Any) -> None:
            self._panel_cursor = None

        @kb.add("x", filter=in_panel)
        @kb.add("delete", filter=in_panel)
        def _panel_stop(event: Any) -> None:
            """x / Delete on the chosen subshell: stop it (as `jobs -k N`)."""
            jobs = self._panel_jobs()
            if self._panel_cursor is None or not jobs:
                return
            number = jobs[min(self._panel_cursor, len(jobs) - 1)].number
            run_in_terminal(lambda: self._run_clicked(f"jobs -k {number}"))

        @kb.add("enter", filter=in_panel)
        def _panel_pick(event: Any) -> None:
            jobs = self._panel_jobs()
            number = jobs[self._panel_cursor].number if self._panel_cursor is not None and jobs else None
            self._panel_cursor = None
            if number is not None:
                run_in_terminal(lambda: self._run_clicked(f"jobs {number}"))

        return kb

    def _footer_mouse(self, event: Any) -> Any:
        """A click or a move on the footer row acts on its tabs; anything else goes to
        prompt_toolkit's own mouse handling."""
        parsed = parse_mouse(event.data)
        if parsed is not None:
            kind, x, y, button = parsed
            if kind == "scroll" and self.session.default_buffer.complete_state is None:
                # The shell has the mouse (for the footer), so this turn of the wheel came to
                # us, not to the terminal. The terminal gets the mouse back now, so the turns
                # that follow scroll its own history, as in any terminal; the next key press or
                # prompt takes it again. The `scroll_view` setting opens the scroll view instead.
                if settings.get(self.ctx.config, "scroll_view"):
                    if wheel_up(event.data):
                        run_in_terminal(self._open_scrollback)
                    return None
                self._mouse_released = True
                event.app.invalidate()  # the renderer turns mouse reporting off on this render
                return None
            if kind == "down":
                self._pressed = button  # for the release, which may not say which button it was
            info = None
            if event.key_sequence[0].key == Keys.WindowsMouseEvent and self.direct_console is not None:
                info = self.direct_console.buffer_info()  # Windows: rows count from the top of the
                if info is not None:  # screen buffer, not of the window the footer sits at the bottom of
                    y -= info.srWindow.Top
            acted = self.footer.mouse(kind, x, y, button)
            if acted is not None:
                if acted[0] == "switch" and acted[1]:
                    tab = acted[1]
                    run_in_terminal(lambda: self._run_clicked(f"jobs {tab}"))
                return None
            if info is not None:
                return windows_mouse_to_layout(event, info)
        for binding in self._default_mouse.get_bindings_for_keys((event.key_sequence[0].key,)):
            return binding.handler(event)
        return NotImplemented

    def _open_scrollback(self) -> None:
        """The scroll view: the session's recent output in the viewer, at its end; the wheel
        scrolls, wheel down past the end (or q) closes it."""
        from shellcraft.output import page

        text = self.scrollback.text()
        if text.strip():
            page(self.ui, text, title="scroll view · wheel down past the end, or q, closes it",
                 at_end=3, close_past_end=True)

    def _paste_clip(self, buffer: Any) -> None:
        """Alt-v: paste the newest copy ($CLIP). Pressed again with nothing typed in between, it
        swaps what it pasted for the next older copy ($CLIP1, $CLIP2 …), then back to the newest,
        like Emacs's yank-pop."""
        copies = [c.rstrip("\n") for c in (self.ctx.alerts.clips() if self.ctx.alerts is not None else [])]
        copies = [c for c in copies if c]
        if not copies:
            return
        last = self._yank
        if last is not None:
            slot, start, text = last
            if buffer.cursor_position == start + len(text) and buffer.text[start:start + len(text)] == text:
                buffer.text = buffer.text[:start] + buffer.text[start + len(text):]  # take back the last paste
                buffer.cursor_position = start
                slot = (slot + 1) % len(copies)
                buffer.insert_text(copies[slot])
                self._yank = (slot, start, copies[slot])
                return
        start = buffer.cursor_position
        buffer.insert_text(copies[0])
        self._yank = (0, start, copies[0])

    def _stop_mcp_http(self) -> None:
        if self.ctx.mcp_http is not None:
            self.ctx.mcp_http.stop()
            self.ctx.mcp_http = None

    def close(self) -> None:
        """Stop a server started with `mcp start`, put back the Rich console, leave the app
        session, restore the console mode and close CONIN$ / CONOUT$ (in that order)."""
        self._cleanup.close()

    def __enter__(self) -> Shell:
        self._tty_attrs = keep_output_on_interrupt()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
        restore_tty(self._tty_attrs)
        self._tty_attrs = None

    def _open_history(self) -> Any:
        """The command history of the active profile (its own file), or the base one."""
        try:
            path = profiles.history_file(self.ctx.config)
            path.parent.mkdir(parents=True, exist_ok=True)
            history: Any = RedactingFileHistory(str(path))
        except OSError:
            history = RedactingInMemoryHistory()
        history.aliases = lambda: aliases.get_all(self.ctx.config)
        return history

    def _profile_switched(self, name: str | None) -> None:
        """`profile use`: the profile's own history from the next prompt on, and a fresh prompt."""
        history = self._open_history()
        self.ctx.history = history
        session = getattr(self, "session", None)
        if session is not None:
            session.history = history
            buffer = session.default_buffer
            buffer.history = history
            buffer._load_history_task = None  # loaded again, from the new file, at the next prompt
            buffer.reset()
        self._profile_color = None

    # ── the prompt ───────────────────────────────────────────────────────────

    def _segment(self, name: str) -> StyleAndTextTuples:
        """The fragments of one prompt segment, or [] when it has nothing to show right now. With
        `mouse_always` on, some carry a click handler (a third element) that runs a command."""
        if name == "name":
            label = f" ⚡shellcraft [{depth()}] " if depth() else " ⚡shellcraft "
            return [("class:name", label, self._click("help"))]
        if name == "profile":
            current = profiles.active(self.ctx.config)
            if not current:
                return []
            return [(f"bold {self._profile_style(current)}", f"◆ {current}", self._click("profile show"))]
        if name == "jobs":
            running = len(self.ctx.jobs.running) if self.ctx.jobs is not None else 0
            return [("class:mcp", f"{running} sub", self._click("jobs"))] if running else []
        if name == "user":
            return [("class:user", self.user_host)]
        if name == "path":
            return [("class:path", _pretty_cwd(), self._click("ls"))]
        if name == "git":
            branch = gitinfo.branch()
            return [("class:git", f"⎇ {branch}", self._click("git status"))] if branch else []
        if name == "mcp":
            parts = [(style, text, self._click("mcp log")) for style, text in self._mcp_marker()]
            if getattr(self.ctx, "ai_run", False):  # the session grant is on: say so until it is off
                parts.append(("class:mcp", (" " if parts else "") + "✦ run", self._click("settings ai_run off")))
            return parts
        if name == "status":
            if not self.ctx.last_status:
                return []
            return [("class:failed", f"[✗ {self.ctx.last_status}]", self._click(None))]
        if name == "duration":
            if self.last_duration > SLOW_SECONDS:
                return [("class:duration", f"⏱ {format_duration(self.last_duration)}")]
            return []
        if name == "clock":
            return [("class:clock", time.strftime("%H:%M"))]
        if name == "python":
            return [("class:sep", f"py{sys.version_info[0]}.{sys.version_info[1]}")]
        return []

    def _profile_style(self, name: str) -> str:
        """The profile's color (from its profile.json, read once per switch)."""
        cached = getattr(self, "_profile_color", None)
        if cached is None or cached[0] != name:
            try:
                color = str(profiles.load(name).get("color") or profiles.DEFAULT_COLOR)
            except profiles.ProfileError:
                color = profiles.DEFAULT_COLOR
            if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
                color = profiles.DEFAULT_COLOR
            cached = self._profile_color = (name, color)
        return cached[1]

    def _prompt(self) -> FormattedText:
        """`╭─ ⚡shellcraft ─ user@host ─ ~/path ─ ⎇ main [✗ 1] ⏱ 4.2s` over `╰─❯ `: the segments
        from `prompt`, joined with `─`; the short ones (status, duration) with a space."""
        parts: StyleAndTextTuples = [("class:frame", "╭─")]
        previous: str | None = None  # the last segment shown
        for name in prompt_segments(self.ctx.config, "left"):
            fragments = self._segment(name)
            if not fragments:
                continue
            if previous is None:
                parts.append(("class:sep", "" if name == "name" else " "))
            elif previous == "name":
                parts.append(("class:sep", "─ "))
            else:
                parts.append(("class:sep", " " if name in ("status", "duration") else " ─ "))
            parts += fragments
            previous = name
        parts += [("", "\n"), ("class:frame", "╰─"), ("class:arrow", "❯ ")]
        return FormattedText(parts)

    def _rprompt(self) -> FormattedText | None:
        parts: StyleAndTextTuples = []
        for name in prompt_segments(self.ctx.config, "right"):
            fragments = self._segment(name)
            if fragments:
                if parts:
                    parts.append(("class:sep", " "))
                parts += fragments
        return FormattedText(parts) if parts else None

    @staticmethod
    def _continuation_prompt() -> FormattedText:
        return FormattedText([("class:frame", "  ┆ "), ("class:arrow", "… ")])

    def _mcp_marker(self) -> list[tuple[str, str]]:
        """`● mcp :38765` while a server started with `mcp start` runs; `✗ mcp :38765` if it died
        on its own (until `mcp` or `mcp stop` notices). Nothing when there is none."""
        child = self.ctx.mcp_http
        if child is None:
            return []
        address = f":{child.port}" if child.host in ("127.0.0.1", "localhost") else child.url.split("/")[2]
        if child.running():
            return [("class:mcp", f"● mcp {address}")]
        return [("class:mcp.down", f"✗ mcp {address}")]

    # ── the input line: highlighting, the toolbar and the mouse ──────────────

    def _take_mouse_back(self) -> None:
        """A key press after the wheel handed the mouse to the terminal: the shell wants it back
        (the footer's lines), so the next render turns mouse reporting on again."""
        if self._mouse_released:
            self._mouse_released = False
            app = getattr(self.session, "app", None)
            if app is not None and app.is_running:
                app.invalidate()

    def _mouse_wanted(self) -> bool:
        """Mouse reporting on while a completion menu is open, while the subshell panel shows
        (so its lines can be clicked), or always with `mouse_always`. Otherwise the terminal keeps
        the mouse: the wheel scrolls its scrollback, text can be selected without Shift."""
        if not self.ui.mouse or self._mouse_released:
            return False
        if self.ui.mouse_always or self.session.default_buffer.complete_state is not None:
            return True
        # While the footer lists a subshell or an alert, its lines, an alert's ✖ and the badge
        # are clickable (footer_mouse, on by default): the shell has the mouse, so text is
        # selected with Shift and the wheel opens the scroll view. Otherwise the terminal keeps it.
        return settings.get(self.ctx.config, "footer_mouse") and self._clickable()

    def _click(self, line: str | None) -> Callable[[MouseEvent], Any]:
        """A prompt or toolbar fragment's mouse handler: on a click, run `line` above the prompt
        (None: show the last error again). Other mouse events are left alone."""
        def handler(event: MouseEvent) -> Any:
            if event.event_type != MouseEventType.MOUSE_UP:
                return NotImplemented
            if line is None:
                if self.last_error is not None:
                    exc, text = self.last_error
                    run_in_terminal(lambda: show_error(self.ui, exc, text))
                return None
            run_in_terminal(lambda: self._run_clicked(line))
            return None
        return handler

    def _run_clicked(self, line: str) -> None:
        """Run a command picked with the mouse, printed above the prompt; the typed line stays."""
        self._guarded(lambda: self._run_clicked_now(line), f"running: {line}")

    def _run_clicked_now(self, line: str) -> None:
        self.ui.console.print(Text(f"❯ {line}", style="sc.muted"), markup=False, highlight=False)
        try:
            with self._interruptible():
                result = run_line(line, self.ctx)
        except (ShellExit, KeyboardInterrupt):
            return
        except Exception as exc:  # noqa: BLE001 — shown like an error at the prompt
            show_error(self.ui, exc, line)
            self.last_error = (exc, line)
            return
        if result is not None:
            try:
                with self._interruptible():
                    show(self.ui, result.output, pager=settings.get(self.ctx.config, "pager"), title=line)
            except KeyboardInterrupt:
                pass

    def _install_mouse(self) -> None:
        """Two things prompt_toolkit's PromptSession does not do by itself (for `mouse_always`):
        the wheel over the input line moves through the history (or the Ctrl-R matches), and the
        upper prompt line keeps its click handlers. PromptSession splits a multi-line prompt and
        rebuilds the lines above the input as plain (style, text) pairs, dropping the handlers, so
        that window's text is taken from `_prompt_above` instead."""
        for control in self.session.layout.find_all_controls():
            if isinstance(control, BufferControl):
                control.mouse_handler = self._wheel_then(control.mouse_handler)  # type: ignore[method-assign]
            elif isinstance(control, CompletionsMenuControl):
                control.mouse_handler = self._hover_then(control.mouse_handler)  # type: ignore[method-assign]
            elif isinstance(control, FormattedTextControl) and getattr(control.text, "__name__", "") == "before":
                control.text = self._prompt_above

    def _hover_then(self, default: Callable[[MouseEvent], Any]) -> Callable[[MouseEvent], Any]:
        """The completion menu follows the mouse: the entry under it becomes the current one (as
        ↑ / ↓ do), so the highlight shows what a click would pick. prompt_toolkit's own handler
        only reacts to clicks and the wheel."""
        def handler(event: MouseEvent) -> Any:
            if event.event_type == MouseEventType.MOUSE_MOVE and event.button == MouseButton.NONE:
                buffer = self.session.default_buffer
                state = buffer.complete_state
                if state is None or not state.completions:
                    return NotImplemented
                index = max(0, min(event.position.y, len(state.completions) - 1))
                if state.complete_index == index:
                    return NotImplemented
                buffer.go_to_completion(index)
                return None
            return default(event)
        return handler

    def _prompt_above(self) -> StyleAndTextTuples:
        """The prompt's fragments above its last line (what PromptSession's `before` returns),
        with their click handlers."""
        fragments = list(self._prompt())
        for i in range(len(fragments) - 1, -1, -1):
            style, text, *handler = fragments[i]
            if "\n" in text:
                above = fragments[:i]
                head = text.rsplit("\n", 1)[0]
                if head:
                    above.append((style, head, *handler))  # type: ignore[arg-type]
                return above
        return []

    def _wheel_then(self, default: Callable[[MouseEvent], Any]) -> Callable[[MouseEvent], Any]:
        def handler(event: MouseEvent) -> Any:
            if event.event_type == MouseEventType.MOUSE_MOVE and self._panel_hover is not None:
                self._panel_hover = None  # the mouse left the panel
                default(event)
                return None
            if event.event_type not in (MouseEventType.SCROLL_UP, MouseEventType.SCROLL_DOWN):
                return default(event)
            up = event.event_type == MouseEventType.SCROLL_UP
            app = self.session.app
            if app.layout.is_searching:
                do_incremental_search(SearchDirection.BACKWARD if up else SearchDirection.FORWARD)
            else:
                app.key_processor.feed(KeyPress(Keys.Up if up else Keys.Down), first=True)
            return None
        return handler

    def _is_known_command(self, name: str) -> bool:
        return (name in BUILTINS or name in KEYWORDS or self.ctx.registry.get(name) is not None
                or name in aliases.get_all(self.ctx.config) or name in self.ctx.functions
                or funcs.as_command(self.ctx, name) is not None)

    def _current_lexer(self) -> Any:
        return self._lexer if settings.get(self.ctx.config, "highlight") else None

    def _report_finished(self) -> None:
        """Before a prompt: the notes about jobs that ended (`[1] done: ls`), the output of a
        command job nobody has looked at yet, and then (the `jobs_autoclear` setting) the
        finished jobs leave the stack, as a finished `ls &` leaves bash's job list."""
        jobs = self.ctx.jobs
        if jobs is None:
            return
        fresh = jobs.unreported()
        for note in jobs.pop_notifications():
            self.ui.console.print(Text(note, style="sc.muted"), markup=False, highlight=False)
        for job in fresh:
            if job.kind == "command" and job.output.strip():
                try:
                    show(self.ui, job.output, pager=settings.get(self.ctx.config, "pager"), title=job.command)
                except KeyboardInterrupt:
                    pass
        if settings.get(self.ctx.config, "jobs_autoclear"):
            for job in [j for j in jobs if not j.running]:
                jobs.remove(job)

    def _reset_input(self) -> None:
        """Before a prompt: the input buffer still holds the last command (prompt_toolkit resets
        it only when the prompt starts), which kept the usage toolbar on and drew it as a blank
        row at the bottom of the screen. Clear it first, then decide about the toolbar."""
        buffer = getattr(self.session, "default_buffer", None)
        if buffer is not None and buffer.text:
            reset = getattr(buffer, "reset", None)  # tests stub the buffer
            if reset is not None:
                reset()
            else:
                buffer.text = ""
        self._update_toolbar()

    def _update_toolbar(self) -> None:
        self._mouse_released = False  # typing, or a new prompt: the shell takes the mouse back
        buffer = getattr(self.session, "default_buffer", None)  # tests stub the session
        text = buffer.text if buffer is not None else ""
        if text:
            self._panel_cursor = None  # typing leaves the panel
        wanted = bool(text.strip()) and settings.get(self.ctx.config, "toolbar")
        self.session.bottom_toolbar = self._toolbar if wanted or self._panel_wanted() else None

    def _panel_wanted(self) -> bool:
        """The panel under the prompt: only while the footer is not drawn at the prompt, and never
        in a lite subshell (the main shell's footer lists everything)."""
        return not self.footer.enabled and not getattr(self.ctx, "lite", False) and self._clickable()

    def _panel_jobs(self) -> list[Any]:
        return list(self.ctx.jobs) if self.ctx.jobs is not None else []

    def _toolbar(self) -> FormattedText:
        """Under the prompt: the usage of the command being typed, then the subshell panel."""
        parts: StyleAndTextTuples = []
        text = self.session.default_buffer.text
        if text.strip() and settings.get(self.ctx.config, "toolbar"):
            parts += list(self._usage_line())
        if self._panel_wanted():
            if parts:
                parts.append(("", "\n"))
            parts += self._panel()
        return FormattedText(parts or [("class:toolbar", " ")])

    def _panel(self) -> StyleAndTextTuples:
        """One line per shell, the same stack the footer shows inside a subshell (shellcraft.footer
        .panel_lines). The line under the mouse, or chosen with ↓ / ↑, is highlighted."""
        bus = self.ctx.alerts
        lines = panel_lines(self._panel_jobs(), 0, self._panel_hover, self.ui.console.width, depth(),
                            cursor=self._panel_cursor, alerts=bus.current() if bus is not None else [],
                            hint=settings.get(self.ctx.config, "panel_hint"),
                            expanded=bool(bus is not None and bus.expanded),
                            copied=bus.copied if bus is not None else None,
                            recording=self.ctx.recording is not None)
        parts: StyleAndTextTuples = []
        for i, row in enumerate(lines):
            if i:
                parts.append(("", "\n"))
            if row.style.startswith("alert."):
                cls = f"class:toolbar.{row.style}"
            else:
                cls = "class:toolbar.key" if row.style == "header" else f"class:toolbar.job.{row.style}"
            if row.alert is not None:
                handler = self._alert_handler(row.alert)
            else:
                handler = self._panel_entry(row.tab) if row.tab else self._panel_hover_handler(None)
            text, at = row.text, 0
            recording = any(role == "rec" for _s, _e, _style, role in row.spans)
            if row.hovered and row.tab is not None and not recording:  # the filled circle, accent color
                parts.append(("class:toolbar.job.hover", text[:MARK], handler))
                at = MARK
            for start, end, style, role in row.spans:
                if start > at:
                    parts.append((cls, text[at:start], handler))
                span_handler = (self._badge_handler() if role == "badge"
                                else self._close_handler(row.alert) if role == "close"
                                else self._close_job_handler(row.tab) if role == "close-job" else handler)
                parts.append((f"class:toolbar.{style}" if style else cls, text[start:end], span_handler))
                at = end
            parts.append((cls, text[at:], handler))
        return parts

    def _released(self, event: MouseEvent) -> str:
        """The button a MOUSE_UP releases: the event's own, or (when it says NONE, as every
        release does on Windows) the one recorded when it went down."""
        names = {MouseButton.LEFT: "left", MouseButton.RIGHT: "right", MouseButton.MIDDLE: "middle"}
        return names.get(event.button, self._pressed)

    def _alert_handler(self, alert: Any) -> Callable[[MouseEvent], Any]:
        """An alert row: a right-click copies its content into the clip (its icon dismisses)."""
        def handler(event: MouseEvent) -> Any:
            if event.event_type == MouseEventType.MOUSE_MOVE:
                return self._set_panel_hover(alert)
            if event.event_type == MouseEventType.MOUSE_DOWN:
                return None  # take the press, so its release comes here too
            if event.event_type == MouseEventType.MOUSE_UP and self._released(event) == "right":
                if self.ctx.alerts is not None:
                    self.ctx.alerts.copy(alert)
                return None
            return NotImplemented
        return handler

    def _close_handler(self, alert: Any) -> Callable[[MouseEvent], Any]:
        """The icon at the start of an alert row: a left click dismisses it, a right-click
        copies, as anywhere on the row."""
        def handler(event: MouseEvent) -> Any:
            if event.event_type == MouseEventType.MOUSE_UP:
                if self._released(event) == "right":
                    if self.ctx.alerts is not None:
                        self.ctx.alerts.copy(alert)
                    return None
                return self._dismiss_alert(alert)
            if event.event_type == MouseEventType.MOUSE_DOWN:
                return None
            if event.event_type == MouseEventType.MOUSE_MOVE:
                return self._set_panel_hover(alert)
            return NotImplemented
        return handler

    def _close_job_handler(self, number: int | None) -> Callable[[MouseEvent], Any]:
        """The ✖ on a finished subshell's line: a click forgets that job."""
        def handler(event: MouseEvent) -> Any:
            if event.event_type == MouseEventType.MOUSE_UP and self._released(event) == "left":
                self.footer.remove_job(number)
                return None
            if event.event_type == MouseEventType.MOUSE_MOVE:
                return self._set_panel_hover(number)
            return NotImplemented
        return handler

    def _badge_handler(self) -> Callable[[MouseEvent], Any]:
        """The alert badge on the main row: a click folds or unfolds the alert rows."""
        def handler(event: MouseEvent) -> Any:
            if event.event_type == MouseEventType.MOUSE_UP and self._released(event) == "left":
                if self.ctx.alerts is not None:
                    self.ctx.alerts.toggle()
                return None
            if event.event_type == MouseEventType.MOUSE_MOVE:
                return self._set_panel_hover(0)
            return NotImplemented
        return handler

    def _dismiss_alert(self, alert: Any) -> None:
        self._panel_hover = None
        if self.ctx.alerts is not None:
            self.ctx.alerts.remove(alert)
        return None

    def _panel_entry(self, number: int) -> Callable[[MouseEvent], Any]:
        """A panel line's mouse handler: hover highlights it, a click switches to it."""
        def handler(event: MouseEvent) -> Any:
            if event.event_type == MouseEventType.MOUSE_MOVE:
                return self._set_panel_hover(number)
            if event.event_type == MouseEventType.MOUSE_UP:
                self._panel_hover = None
                run_in_terminal(lambda: self._run_clicked(f"jobs {number}"))
                return None
            return NotImplemented
        return handler

    def _panel_hover_handler(self, number: int | None) -> Callable[[MouseEvent], Any]:
        def handler(event: MouseEvent) -> Any:
            if event.event_type == MouseEventType.MOUSE_MOVE:
                return self._set_panel_hover(number)
            return NotImplemented
        return handler

    @property
    def _panel_cursor(self) -> int | None:
        """The subshell row chosen with ↓ / ↑ (None: the input line has the keys); kept on the
        footer, which draws it."""
        return self.footer.cursor

    @_panel_cursor.setter
    def _panel_cursor(self, value: int | None) -> None:
        self.footer.cursor = value

    def _stack_shown(self) -> bool:
        """Whether the stack is on screen: the footer at the prompt, or the panel under it."""
        return self.footer.enabled or self._panel_wanted()

    def _clickable(self) -> bool:
        """Something in the stack to click: a subshell or an alert (the main row alone is not)."""
        if self.ctx.alerts is not None and self.ctx.alerts.current():
            return True
        return self.ctx.jobs is not None and len(self.ctx.jobs) > 0

    def _set_panel_hover(self, key: Any) -> Any:
        """None when it changed (repaint), NotImplemented when nothing changed. `key` is a tab
        (0 or a job number) or an Alert."""
        if key is not None:
            self._arm_panel_hover_timer()  # cleared after HOVER_TIMEOUT without the mouse moving
        if key is self._panel_hover or (key == self._panel_hover and type(key) is type(self._panel_hover)):
            return NotImplemented
        self._panel_hover = key
        if self.ctx.alerts is not None and self.ctx.alerts.copied is not key:
            self.ctx.alerts.copied = None  # the "copied" hint goes when the mouse moves to another row
        return None

    def _arm_panel_hover_timer(self) -> None:
        import threading

        from shellcraft.footer import HOVER_TIMEOUT

        if self._panel_hover_timer is not None:
            self._panel_hover_timer.cancel()

        def expired() -> None:
            self._panel_hover_timer = None
            if self._panel_hover is not None:
                self._panel_hover = None
                self._jobs_changed()  # redraw the prompt (and the footer with it)

        self._panel_hover_timer = threading.Timer(HOVER_TIMEOUT, expired)
        self._panel_hover_timer.daemon = True
        self._panel_hover_timer.start()

    def _usage_line(self) -> FormattedText:
        """One line under the prompt: the usage of the command being typed."""
        text = self.session.default_buffer.text
        words = _last_segment_words(text)
        if not words:
            return FormattedText([("class:toolbar", " ")])
        name = words[0]
        if name.startswith("\\"):
            name = name[1:]
        resolved = aliases.expand_command(script.shparser.Command(name, []), aliases.get_all(self.ctx.config)).name
        if name in KEYWORDS:
            page = self._click(f"man {'for' if name in ('for', 'do', 'done', 'in') else 'if'}")
            return FormattedText([("class:toolbar.key", f" {name} ", page),
                                  ("class:toolbar", " see: man for, man if", page)])
        if resolved in self.ctx.functions and not words[0].startswith("\\"):
            page = self._click("man function")
            return FormattedText([("class:toolbar.key", f" {resolved} ", page),
                                  ("class:toolbar", f" shell function  — declare -f {resolved}", page)])
        if resolved in BUILTINS:
            b = BUILTINS[resolved]
            page = self._click(f"man {resolved}")
            return FormattedText([("class:toolbar.key", f" {resolved} ", page),
                                  ("class:toolbar", f" {b.usage}  — {b.summary}", page)])
        spec = self.ctx.registry.get(resolved)
        if spec is not None:
            page = self._click(f"man {resolved}")
            usage = (spec.skill.usage if spec.skill and spec.skill.usage else "") or f"{spec.name} [ARGS...]"
            summary = f"  — {spec.summary}" if spec.summary else ""
            return FormattedText([("class:toolbar.key", f" {resolved} ", page),
                                  ("class:toolbar", f" {usage}{summary}", page)])
        if len(words) == 1 and text.rstrip() == text:
            return FormattedText([("class:toolbar", " ")])  # still typing the name
        return FormattedText([("class:toolbar.error", f" {name}: command not found ")])

    # ── output ───────────────────────────────────────────────────────────────

    def banner(self, warnings: Iterable[str] = ()) -> None:
        self.ui.console.print(render_banner(self.ui, self.ctx.registry, warnings, profiles.active(self.ctx.config)))

    def report(self) -> str:
        """The console diagnostics report as plain text, with the live footer and jobs in it."""
        from shellcraft.diagnostics import console_report

        return console_report(self.ui, self.direct_console, self.windows_console_error,
                              jobs=self.ctx.jobs, footer=self.footer)

    def diagnostics(self) -> None:
        """Print the console diagnostics report (the `diagnostics` setting, or --diag)."""
        # Plain text, not wrapped, so it can be copied into a bug report as is.
        self.ui.console.print(self.report(), markup=False, highlight=False, soft_wrap=True)

    def _transient(self) -> bool:
        return settings.get(self.ctx.config, "transient_prompt")

    def _ask(self, message: Any) -> str:
        """One prompt_toolkit prompt; with the transient prompt on, its lines are erased on Enter
        and replaced by a compact `❯ command` line."""
        transient = self._transient()
        self.session.app.erase_when_done = transient
        self._guarded(self._report_finished, "the finished-jobs report")
        self.footer.set_hover(None)
        self._panel_hover = self._panel_cursor = None
        self._footer_at_prompt()
        self._reset_input()
        if not getattr(self, "_first_prompt_done", False):
            self._first_prompt_done = True
            self._startup_report()
        prefill, self.ctx.prefill = self.ctx.prefill, ""  # `clip -i`: the next prompt starts with it
        self._before_prompt()
        line = self.session.prompt(message, default=prefill) if prefill else self.session.prompt(message)
        if transient:
            self.ui.console.print(Text.assemble(("❯ ", "sc.prompt"), (line, "")), markup=False, highlight=False)
        return line

    def _before_prompt(self) -> None:
        """The terminal, tidied for a prompt: a Ctrl-C that arrived between commands left its
        `^C` echo on the current line (the prompt would start right after it), and what the
        terminal delivered meanwhile is kept as typeahead without cursor replies or mouse
        reports (see shellcraft.typeahead)."""
        if self._interrupt_dropped:
            self._interrupt_dropped = False
            self._quietly(lambda: self.ui.console.file.write("\n"))
        if self.ui.console.is_terminal:
            from shellcraft import typeahead

            self._guarded(lambda: typeahead.collect(self.session.input), "the typeahead")

    def _startup_report(self) -> None:
        """Before the first prompt: the startup timing, kept for `diag`, and shown with --timing."""
        from shellcraft import timing

        timing.mark("first prompt")
        timing.save()
        if timing.enabled():
            self.ui.console.print(Text(timing.report(), style="sc.muted"), markup=False, highlight=False)

    PROMPT_FAILURES = 3  # prompt errors in a row before a plain input() prompt takes over

    def loop(self) -> int:
        """Prompt, run, repeat. Only Ctrl-D on an empty prompt and `exit` end it: Ctrl-C, a module
        calling sys.exit() and unexpected errors (logged to ~/.shellcraft/crash.log) never do.
        Between commands a Ctrl-C is dropped (what it would stop has stopped), so a burst of them
        can't land in an unguarded moment; while a command runs it interrupts it as usual."""
        self._prompt_failures = 0
        with self._interrupts_deferred():
            while True:
                try:
                    code = self._iteration()
                except KeyboardInterrupt:
                    self._interrupted()
                    continue
                except ShellExit as exc:
                    return self._goodbye(exc.code)
                except SystemExit as exc:  # a module or a bug called sys.exit(): not how ShellCraft ends
                    stray = CommandError(f"something tried to exit ShellCraft (sys.exit({exc.code!r})); "
                                         "use exit or Ctrl-D")
                    self._quietly(lambda e=stray: show_error(self.ui, e, ""))
                    continue
                except Exception as exc:  # noqa: BLE001 — a bug anywhere must not end the session
                    self._internal_error(exc, "the prompt loop")
                    continue
                if code is not None:
                    return code

    def _iteration(self) -> int | None:
        """One prompt and the command typed at it; an exit code when the session ends."""
        self._quietly(self._open_requests)
        try:
            line = self._ask(self._prompt)
            line = self._read_continuation(line)
        except KeyboardInterrupt:
            return None
        except EOFError:
            return self._goodbye(0)
        except Exception as exc:  # noqa: BLE001 — the prompt itself failed (a rendering bug, asyncio)
            self._internal_error(exc, "the prompt")
            self._prompt_failures += 1
            if self._prompt_failures < self.PROMPT_FAILURES:
                return None
            line = self._plain_input()
            if line is EOFError:
                return self._goodbye(0)
        else:
            self._prompt_failures = 0
        if not isinstance(line, str):
            return None

        try:
            line, expanded = histexpand.expand(line, self._previous_command(), self._history_lines())
        except ParseError as exc:
            show_error(self.ui, exc, line)
            self.ctx.last_status = STATUS_SYNTAX
            return None
        if expanded:
            self.ui.console.print(Text(line, style="sc.muted"), markup=False, highlight=False)
        self._remember(line)
        self.scrollback.command(line)
        if self.ctx.recording is not None:
            self.ctx.recording.command(line)

        self._hot_reload()
        started = time.monotonic()
        try:
            with self._interruptible():
                result = run_line(line, self.ctx)
        except ShellExit:
            raise
        except KeyboardInterrupt:
            self._interrupted()
            return None
        except (ParseError, PipelineError) as exc:
            self._quietly(lambda e=exc, text=line: show_error(self.ui, e, text))
            self.last_error = (exc, line)
            return None
        except Exception as exc:  # noqa: BLE001 — a bug in a command: shown, and logged
            self.last_error = (exc, line)
            self.ctx.last_status = 1
            self._internal_error(exc, f"running: {line}", show_as=line)
            return None
        finally:
            self.last_duration = time.monotonic() - started

        if result is not None:
            try:
                with self._interruptible():
                    show(self.ui, result.output, pager=settings.get(self.ctx.config, "pager"), title=line)
            except KeyboardInterrupt:
                # The print stopped wherever it was: inside a colored span, mid-line. Back to
                # plain text on a fresh line before anything else is written.
                self._quietly(lambda: self.ui.console.file.write("\x1b[0m\n" if self.ui.console.is_terminal else "\n"))
                self._interrupted()
        return None

    def _plain_input(self) -> Any:
        """The fallback when the prompt keeps failing: Python's input(), so `exit` still works."""
        self._quietly(lambda: self.ui.console.print(Text(
            "the prompt keeps failing (details in ~/.shellcraft/crash.log); a plain prompt for now",
            style="sc.warning"), markup=False, highlight=False))
        try:
            with self._interruptible():
                return input("shellcraft> ")
        except KeyboardInterrupt:
            return None
        except EOFError:
            return EOFError

    def _interrupted(self) -> None:
        self.ctx.last_status = STATUS_INTERRUPTED
        self._quietly(lambda: self.ui.console.print(Text("^C interrupted", style="sc.warning")))

    def _internal_error(self, exc: BaseException, where: str, show_as: str = "") -> None:
        """An unexpected error: the error panel (with where the details went), and the crash log."""
        from shellcraft import crashlog

        logged = crashlog.record(exc, where)

        def report() -> None:
            show_error(self.ui, exc, show_as)
            if logged is not None and not show_as:
                self.ui.console.print(Text(f"(an unexpected error in {where}; details in {logged})",
                                           style="sc.muted"), markup=False, highlight=False)

        self._quietly(report)

    @staticmethod
    def _quietly(action: Callable[[], Any]) -> None:
        """Run `action` (a print, a report) so that neither Ctrl-C nor an error in it escapes."""
        try:
            action()
        except KeyboardInterrupt:
            pass
        except Exception:  # noqa: BLE001 — printing must never end the session
            pass

    @contextlib.contextmanager
    def _interrupts_deferred(self) -> Any:
        """Ctrl-C (and Ctrl-Break on Windows) are dropped while this is active, except inside
        `_interruptible()`: between commands nothing is running for them to stop."""
        installed: list[tuple[int, Any]] = []
        if threading.current_thread() is threading.main_thread():
            for name in ("SIGINT", "SIGBREAK"):
                number = getattr(signal, name, None)
                if number is None:
                    continue
                try:
                    installed.append((number, signal.signal(number, self._dropped_interrupt)))
                except (ValueError, OSError):
                    pass
        try:
            yield
        finally:
            for number, previous in installed:
                try:
                    signal.signal(number, previous)
                except (ValueError, OSError):
                    pass

    def _dropped_interrupt(self, signum: int, frame: Any) -> None:
        self._interrupt_dropped = True  # its ^C echo is on the line: the next prompt starts below it

    @contextlib.contextmanager
    def _interruptible(self) -> Any:
        """Ctrl-C (and Ctrl-Break) interrupt again while a command runs or its output prints."""
        swapped: list[tuple[int, Any]] = []
        if threading.current_thread() is threading.main_thread():
            for name in ("SIGINT", "SIGBREAK"):
                number = getattr(signal, name, None)
                if number is None:
                    continue
                try:
                    swapped.append((number, signal.signal(number, signal.default_int_handler)))
                except (ValueError, OSError):
                    pass
        try:
            yield
        finally:
            for number, previous in swapped:
                try:
                    signal.signal(number, previous)
                except (ValueError, OSError):
                    pass

    def _read_continuation(self, line: str) -> str | None:
        """While a `{` or `(` is still open, read more lines (with a `┆ …` prompt) and join them
        into one line. Ctrl-C drops the whole command; Ctrl-D reports what is missing."""
        while script.incomplete(line) is not None or _continued(line):
            try:
                more = self._ask(self._continuation_prompt)
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
            if _continued(line):  # `\` (or a cmd-style curl line's `^`) at the end: the next line goes on it
                line = line + "\n" + more
            elif more.strip():
                line = script.join_continuation(line, more)  # adds `;` only where one is needed
        return line

    def _history_lines(self) -> list[str]:
        """Every saved command, oldest first (for `!N` and `!prefix`)."""
        history = self.ctx.history
        strings = list(history.get_strings())
        if not strings and not getattr(history, "_loaded", True):  # the prompt loads the file lazily
            strings = list(reversed(list(history.load_history_strings())))
        return strings

    def _previous_command(self) -> str | None:
        """The last saved command, for `!!` and `!$` (from earlier sessions too, as in bash)."""
        lines = self._history_lines()
        return lines[-1] if lines else None

    def _remember(self, line: str) -> None:
        """Save a command to the history (what prompt_toolkit would do, after our expansion)."""
        if not line.strip():
            return
        strings = self.ctx.history.get_strings()
        if not strings or strings[-1] != line:
            self.ctx.history.append_string(line)

    def _hot_reload(self) -> None:
        """Reload modules whose files changed since the last command (the `hot_reload` setting)."""
        if getattr(self.ctx, "lite", False):
            return  # a lite shell never reloads modules (the main shell does)
        changed = self.watcher.changes()  # always consumed, so turning the setting on starts fresh
        if not changed or not settings.get(self.ctx.config, "hot_reload"):
            return
        self.ctx.registry.reload(changed)
        msg = Text.assemble(("↻ ", "sc.accent"), (f"modules reloaded ({', '.join(changed)})", "sc.muted"))
        for warning in self.ctx.registry.warnings:
            msg.append(f"\n⚠ {warning}", style="sc.warning")
        self.ui.console.print(msg)

    def _goodbye(self, code: int) -> int:
        self.ui.console.print(Text("⏻ session closed — stay curious.", style="sc.muted"))
        return code


def keep_output_on_interrupt(fd: int | None = None) -> Any:
    """Set NOFLSH on the terminal: a Ctrl-C then interrupts the command without the tty driver
    also discarding whatever output it had queued, which could cut an escape sequence in half
    (the rest of it shows as `;207m` text, and the colors go wrong). Returns what to give
    restore_tty, or None when there is no POSIX terminal."""
    if sys.platform == "win32":
        return None
    try:
        import termios

        fd = sys.stdin.fileno() if fd is None else fd
        before = termios.tcgetattr(fd)
        after = list(before)
        after[3] |= termios.NOFLSH
        termios.tcsetattr(fd, termios.TCSANOW, after)
        return (fd, before)
    except Exception:  # noqa: BLE001 — not a terminal, or no termios
        return None


def restore_tty(saved: Any) -> None:
    if saved is None:
        return
    try:
        import termios

        fd, before = saved
        termios.tcsetattr(fd, termios.TCSANOW, before)
    except Exception:  # noqa: BLE001 — the terminal is gone
        pass


def _continued(line: str) -> bool:
    r"""The line ends with a lone `\` (bash's line continuation, as in a pasted curl command), or a
    cmd-style curl line ends with `^` (Windows' "Copy as cURL (cmd)")."""
    stripped = line.rstrip(" \t")
    if stripped.endswith("\\") and not stripped.endswith("\\\\"):
        return True
    from shellcraft import curlpaste

    return stripped.endswith("^") and curlpaste.looks_like_cmd(line + '^"')


def _last_segment_words(text: str) -> list[str]:
    """The words of the command being typed (after the last `|`, `;`, `&&`, `||`, `do`, `then`…)."""
    from shellcraft.completer import _current_segment

    return _current_segment(text)


def windows_mouse_to_layout(event: Any, info: Any) -> Any:
    """A Windows mouse event (`button;type;x;y`, screen buffer coordinates) handed to the mouse
    handler of what the prompt drew there: the completion menu (a click picks an item, the wheel
    scrolls it), the input line. prompt_toolkit's own Windows handler does this only for its
    Win32Output / Windows10_Output and ignores the event for our DirectOutput, so on Windows the
    menu took no clicks and no wheel. The prompt's first row is the console cursor's row less the
    renderer's cursor row, as prompt_toolkit works it out."""
    from prompt_toolkit.data_structures import Point
    from prompt_toolkit.mouse_events import MouseButton, MouseEvent, MouseEventType

    pieces = event.data.split(";")
    try:
        button, kind = MouseButton(pieces[0]), MouseEventType(pieces[1])
        x, y = int(pieces[2]), int(pieces[3])
    except (ValueError, IndexError):
        return NotImplemented
    renderer = event.app.renderer
    y -= info.dwCursorPosition.Y - renderer._cursor_pos.y
    if y < 0 or x < 0:
        return NotImplemented
    handler = renderer.mouse_handlers.mouse_handlers[y][x]
    return handler(MouseEvent(position=Point(x=x, y=y), event_type=kind, button=button, modifiers=frozenset()))
