import io
import ntpath

import pytest
from prompt_toolkit.data_structures import Size

from core import shell
from core.context import ShellContext
from core.output import _find_line, _highlight, _next_match


@pytest.mark.parametrize("cwd, home, expected", [
    ("/home/me", "/home/me", "~"),
    ("/home/me/src/x", "/home/me", "~/src/x"),
    ("/home/meow", "/home/me", "/home/meow"),
    ("/etc", "/home/me", "/etc"),
])
def test_pretty_cwd_posix(cwd, home, expected):
    assert shell._pretty_cwd(cwd, home) == expected


@pytest.mark.parametrize("cwd, expected", [
    (r"c:\users\me", "~"),
    (r"C:\USERS\ME\Projects\x", r"~\Projects\x"),
    (r"C:\Users\Meow", r"C:\Users\Meow"),
])
def test_pretty_cwd_windows_is_case_insensitive(monkeypatch, cwd, expected):
    monkeypatch.setattr(shell.os, "path", ntpath)
    monkeypatch.setattr(shell.os, "sep", "\\")
    assert shell._pretty_cwd(cwd, r"C:\Users\Me") == expected


def test_pager_n_advances_through_matches_on_the_last_screen():
    plain = ["line"] * 100
    for i in (85, 90, 95):
        plain[i] = "needle"
    height, max_top = 20, 80
    top, match, hits = 0, -1, []
    while (hit := _next_match(plain, "NEEDLE", top, match, height, True)) is not None:
        hits.append(hit)
        match, top = hit, min(hit, max_top)
    assert hits == [85, 90, 95]  # used to stop at 85 forever
    assert _next_match(plain, "needle", top, match, height, False) == 90


def test_pager_search_restarts_from_screen_top_after_scrolling_away():
    plain = ["x", "hit", "x", "x", "hit", "x"]
    assert _next_match(plain, "hit", top=3, match=1, height=2, forward=True) == 4
    assert _find_line(plain, "hit", 5, True) is None


def test_highlight_keeps_colors_and_marks_every_match():
    line = "\x1b[31mError: bad error\x1b[0m end"
    out = _highlight(line, "error")
    assert out.count("\x1b[7m") >= 2 and "\x1b[31m" in out
    assert _highlight("nothing here", "zzz") == "nothing here"
    assert _highlight("abab", "ab") == "\x1b[7mab\x1b[27m\x1b[7mab\x1b[27m"


def test_shell_hot_reload_before_a_command(tmp_path):
    from types import SimpleNamespace

    from rich.console import Console

    from core.loader import ModuleRegistry
    from core.watch import ModuleWatcher

    (tmp_path / "one.py").write_text("def run(args, stdin):\n    return '1'\n")
    registry = ModuleRegistry(tmp_path)
    registry.load()
    console = Console(record=True, width=120)
    fake = SimpleNamespace(watcher=ModuleWatcher(tmp_path), ui=SimpleNamespace(console=console),
                           ctx=SimpleNamespace(registry=registry, config={}))

    shell.Shell._hot_reload(fake)  # nothing changed: stays quiet
    assert console.export_text() == ""
    (tmp_path / "two.py").write_text("def run(args, stdin):\n    return '2'\n")
    shell.Shell._hot_reload(fake)
    assert "two" in registry.modules
    assert "modules reloaded (two.py)" in console.export_text()

    fake.ctx.config = {"settings": {"hot_reload": False}}
    (tmp_path / "three.py").write_text("def run(args, stdin):\n    return '3'\n")
    shell.Shell._hot_reload(fake)
    assert "three" not in registry.modules  # setting off: no reload


# ── Windows console (CONIN$ / CONOUT$) ───────────────────────────────────────

def _sized_output(rows, columns):
    from prompt_toolkit.output import DummyOutput

    output = DummyOutput()
    output.get_size = lambda: Size(rows=rows, columns=columns)
    return output


def _fake_windows_console(events):
    """Stands in for create_prompt_toolkit_console: a 120x30 console whose closing is recorded."""
    from prompt_toolkit.input import DummyInput

    def open_console(cleanup):
        events.append("console opened")
        cleanup.callback(events.append, "console closed")
        return shell.DirectConsole(input=DummyInput(), output=_sized_output(30, 120), stream=io.StringIO(),
                                   conin=None, conout=None, output_mode_before=0, input_mode_before=0,
                                   reader="Vt100ConsoleInputReader", vt_input=True)

    return open_console


def _windows_ui():
    from rich.console import Console

    from core.themes import UI, all_themes

    return UI(all_themes({})["cyberpunk"], Console(highlight=False))


def test_shell_runs_on_the_windows_console_and_restores_it(monkeypatch, tmp_path):
    from prompt_toolkit.application.current import get_app_session

    from core.loader import ModuleRegistry

    events = []
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path))
    monkeypatch.setattr(shell, "create_prompt_toolkit_console", _fake_windows_console(events))
    ui = _windows_ui()
    original = ui.console
    before = get_app_session()
    ctx = ShellContext(registry=ModuleRegistry(tmp_path), ui=ui)

    with shell.Shell(ctx) as sh:
        console = sh.direct_console
        assert events == ["console opened"]
        # The prompt reads and writes the console devices, not stdin / stdout.
        assert sh.session.app.input is console.input
        assert sh.session.app.output is console.output
        assert ui.console.file is console.stream
        assert ui.console.is_terminal and not ui.console.legacy_windows
        assert ui.console.color_system == "truecolor"
        assert ui.console.size == (120, 30)  # measured like the prompt: from the console window
        sh.ui.console.print("[sc.accent]hi[/]")  # the theme is on the new console
        assert "hi" in console.stream.getvalue()
    # The Rich console comes back, the session ends, then the console is restored.
    assert ui.console is original
    assert get_app_session() is before
    assert events == ["console opened", "console closed"]


def test_shell_restores_the_windows_console_when_setup_fails(monkeypatch, tmp_path):
    from prompt_toolkit.application.current import get_app_session

    from core.loader import ModuleRegistry

    events = []
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path))
    monkeypatch.setattr(shell, "create_prompt_toolkit_console", _fake_windows_console(events))

    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(shell, "PromptSession", broken)
    ui = _windows_ui()
    original = ui.console
    before = get_app_session()
    with pytest.raises(RuntimeError):
        shell.Shell(ShellContext(registry=ModuleRegistry(tmp_path), ui=ui))
    assert ui.console is original
    assert get_app_session() is before
    assert events == ["console opened", "console closed"]


def test_shell_falls_back_and_says_why_when_the_windows_console_fails(monkeypatch, tmp_path, capsys):
    from core.diagnostics import console_report
    from core.loader import ModuleRegistry

    def broken(cleanup):
        raise OSError("no console")

    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path))
    monkeypatch.setattr(shell, "create_prompt_toolkit_console", broken)
    ui = _windows_ui()
    original = ui.console
    with shell.Shell(ShellContext(registry=ModuleRegistry(tmp_path), ui=ui)) as sh:
        assert sh.direct_console is None
        assert sh.windows_console_error == "no console"
        assert ui.console is original
    assert "Windows console not opened (no console)" in capsys.readouterr().err

    import core.diagnostics as diagnostics
    monkeypatch.setattr(diagnostics.sys, "platform", "win32")
    monkeypatch.setattr(diagnostics.sys, "getwindowsversion", lambda: "10.0.26100", raising=False)
    assert "not open: no console" in console_report(ui, None, "no console")


def test_cli_closes_the_shell_even_when_it_crashes(monkeypatch, tmp_path):
    from core import cli

    events = []

    class FakeShell:
        def __init__(self, ctx):
            console = ctx.ui.console
            events.append(("shell", console.is_terminal, console.color_system, console.legacy_windows))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            events.append("shell closed")

        def loop(self):
            raise RuntimeError("boom")

    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path))
    monkeypatch.setattr(shell, "Shell", FakeShell)
    with pytest.raises(RuntimeError):
        cli.main(["--no-banner", "--modules", str(tmp_path)])
    assert events == [("shell", True, "truecolor", False), "shell closed"]


# ── Console diagnostics ──────────────────────────────────────────────────────

def test_diagnostics_report_lists_every_size_source(monkeypatch):
    import io

    from prompt_toolkit.application.current import create_app_session

    from core.console import build_console
    from core.diagnostics import console_report

    monkeypatch.setenv("COLUMNS", "300")
    ui = _windows_ui()
    ui.use_console(build_console(file=io.StringIO()))
    with create_app_session(output=_sized_output(30, 120)):
        report = console_report(ui)
    lines = report.splitlines()
    assert lines[0].startswith("----- shellcraft console diagnostics")
    assert lines[-1].startswith("----- end of shellcraft console diagnostics")
    assert "prompt_toolkit get_size() = Size(rows=30, columns=120)" in report
    assert "rich ui.console.size = (120, 30)" in report
    assert "COLUMNS='300'" in report
    assert "color_system=truecolor" in report
    assert "\x1b" not in report  # plain text, to paste into a bug report


def test_diagnostics_report_never_raises():
    from core.diagnostics import _flags, _try

    assert _try(lambda: 1 / 0) == "<ZeroDivisionError: division by zero>"
    assert _flags(0x0007, {1: "A", 2: "B", 4: "C"}) == "0x0007 A | B | C"
    assert _flags(0x0021, {1: "A"}) == "0x0021 A | 0x20"
    assert _flags(None, {}) == "<unknown>"


@pytest.mark.parametrize("argv, setting, printed", [
    (["--diag"], False, True), ([], True, True), ([], False, False),
])
def test_cli_prints_diagnostics_first_when_asked(monkeypatch, tmp_path, argv, setting, printed):
    import json

    from core import cli

    events = []

    class FakeShell:
        def __init__(self, ctx):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

        def diagnostics(self):
            events.append("diagnostics")

        def banner(self):
            events.append("banner")

        def loop(self):
            return 0

    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text(json.dumps({"settings": {"diagnostics": setting}}))
    monkeypatch.setattr(shell, "Shell", FakeShell)
    assert cli.main([*argv, "--modules", str(tmp_path)]) == 0
    assert events == (["diagnostics", "banner"] if printed else ["banner"])


# ── Truecolor prompt on Linux ────────────────────────────────────────────────

class _Tty(io.StringIO):
    def isatty(self):
        return True


def test_shell_runs_the_prompt_in_truecolor(monkeypatch, tmp_path):
    from prompt_toolkit.application.current import get_app_session

    from core.loader import ModuleRegistry

    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path))
    monkeypatch.setenv("COLORTERM", "truecolor")
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("PROMPT_TOOLKIT_COLOR_DEPTH", raising=False)
    monkeypatch.setattr(shell, "create_prompt_toolkit_console", lambda cleanup: None)
    monkeypatch.setattr(shell.sys, "stdout", _Tty())
    before = get_app_session()
    with shell.Shell(ShellContext(registry=ModuleRegistry(tmp_path), ui=_windows_ui())) as sh:
        assert sh.session.app.output.get_default_color_depth().name == "DEPTH_24_BIT"
    assert get_app_session() is before  # the session ends with the shell


# ── help prints, man pages ───────────────────────────────────────────────────

def _screen(monkeypatch, height=10):
    from rich.console import Console

    from core import output
    from core.themes import UI, all_themes

    paged = []
    monkeypatch.setattr(output, "page", lambda ui, rendered: paged.append(rendered))
    console = Console(file=io.StringIO(), force_terminal=True, width=100, height=height)
    return UI(all_themes({})["cyberpunk"], console), paged


@pytest.mark.parametrize("line, pager_setting, paged", [
    ("man echo", True, True),    # shorter than the screen, still paged
    ("help", True, False),       # taller than the screen, still printed
    ("man echo", False, False),  # `settings pager off` still means never
])
def test_help_prints_and_man_pages(monkeypatch, tmp_path, line, pager_setting, paged):
    from core.loader import ModuleRegistry
    from core.output import show
    from core.pipeline import run_line

    ui, pages = _screen(monkeypatch)
    ctx = ShellContext(registry=ModuleRegistry(tmp_path), ui=ui)
    show(ui, run_line(line, ctx).output, pager=pager_setting)
    assert bool(pages) is paged
    assert bool(ui.console.file.getvalue()) is not paged


def test_auto_pager_still_pages_tall_output(monkeypatch):
    from core.output import show

    ui, pages = _screen(monkeypatch, height=10)
    show(ui, "\n".join(map(str, range(50))))
    show(ui, "short")
    assert len(pages) == 1


def test_paged_output_is_plain_for_pipes(tmp_path):
    from core.context import to_text
    from core.loader import ModuleRegistry
    from core.pipeline import run_line

    ctx = ShellContext(registry=ModuleRegistry(tmp_path), ui=_windows_ui())
    assert to_text(run_line("man echo", ctx).output).lstrip().startswith("echo")
    assert "echo" in to_text(run_line("help | grep echo", ctx).output)


@pytest.mark.parametrize("line", ["man -p echo", "man --print echo", "man echo -p"])
def test_man_p_prints_without_the_pager(monkeypatch, tmp_path, line):
    from core.loader import ModuleRegistry
    from core.output import show
    from core.pipeline import run_line

    ui, pages = _screen(monkeypatch)
    ctx = ShellContext(registry=ModuleRegistry(tmp_path), ui=ui)
    show(ui, run_line(line, ctx).output)
    assert not pages
    assert "echo" in ui.console.file.getvalue()


@pytest.mark.parametrize("line", ["man", "man -p", "man echo grep", "man -x echo"])
def test_man_usage_errors(tmp_path, line):
    from core.loader import ModuleRegistry
    from core.pipeline import PipelineError, run_line

    ctx = ShellContext(registry=ModuleRegistry(tmp_path), ui=_windows_ui())
    with pytest.raises(PipelineError, match=r"usage: man \[-p\] COMMAND"):
        run_line(line, ctx)


def test_man_p_completes(tmp_path):
    from core.options import builtin_options

    assert any("-p" in o.flags and "--print" in o.flags for o in builtin_options("man"))
