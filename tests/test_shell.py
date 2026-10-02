import contextlib
import ntpath

import pytest

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

def test_windows_console_is_only_used_on_windows(monkeypatch):
    monkeypatch.setattr(shell.sys, "platform", "linux")
    assert shell.open_windows_console() is None


def test_windows_console_falls_back_without_a_console(monkeypatch):
    def no_console():
        raise OSError("no console")

    monkeypatch.setattr(shell.sys, "platform", "win32")
    monkeypatch.setattr(shell, "WindowsConsole", no_console)
    assert shell.open_windows_console() is None


def test_window_size_is_the_visible_window_not_the_buffer():
    from types import SimpleNamespace as NS

    # conhost: a 9001-row scrollback, scrolled down; only srWindow is the visible window.
    info = NS(srWindow=NS(Left=0, Top=100, Right=119, Bottom=129), dwSize=NS(X=120, Y=9001))
    assert shell._window_size(info) == shell.Size(rows=30, columns=120)
    # Without delayed wrap, writing the last column wraps at once, so it is left out.
    assert shell._window_size(info, full_width=False) == shell.Size(rows=30, columns=119)
    # A buffer wider than the window (horizontal scrollbar): the window counts.
    wide_buffer = NS(srWindow=NS(Left=0, Top=0, Right=99, Bottom=9), dwSize=NS(X=300, Y=10))
    assert shell._window_size(wide_buffer).columns == 100
    narrow_buffer = NS(srWindow=NS(Left=0, Top=0, Right=199, Bottom=9), dwSize=NS(X=80, Y=10))
    assert shell._window_size(narrow_buffer).columns == 80


def _sized_output(rows, columns):
    from prompt_toolkit.output import DummyOutput

    output = DummyOutput()
    output.get_size = lambda: shell.Size(rows=rows, columns=columns)
    return output


def test_terminal_console_measures_like_the_prompt(monkeypatch):
    import io

    from prompt_toolkit.application.current import create_app_session

    monkeypatch.setenv("COLUMNS", "300")  # a stale snapshot must not win over the live size
    monkeypatch.setenv("LINES", "99")
    sizes = [(30, 120)]
    output = _sized_output(0, 0)
    output.get_size = lambda: shell.Size(*sizes[0])
    console = shell.TerminalConsole(file=io.StringIO(), force_terminal=True)
    with create_app_session(output=output):
        assert (console.width, console.height) == (120, 30)
        sizes[0] = (40, 90)  # the window was resized
        assert console.size == (90, 40)
        console.width = 50  # an explicit width still wins
        assert console.size == (50, 40)


def test_terminal_console_falls_back_to_rich_without_an_output(monkeypatch):
    import io

    from prompt_toolkit.application.current import create_app_session

    output = _sized_output(0, 0)

    def broken():
        raise OSError("not a terminal")

    output.get_size = broken
    monkeypatch.setenv("COLUMNS", "77")
    console = shell.TerminalConsole(file=io.StringIO(), force_terminal=True)
    with create_app_session(output=output):
        assert console.width == 77


class _FakeWindowsConsole:
    def __init__(self, events):
        import io

        self.events = events
        self.stream = io.StringIO()

    @contextlib.contextmanager
    def session(self):
        from prompt_toolkit.application.current import create_app_session

        self.events.append("session")
        try:
            with create_app_session(output=_sized_output(30, 120)):  # a 120x30 window
                yield
        finally:
            self.events.append("session closed")

    def close(self):
        self.events.append("console closed")


def _windows_ui():
    from rich.console import Console

    from core.themes import UI, all_themes

    return UI(all_themes({})["cyberpunk"], Console(highlight=False))


def test_shell_runs_on_the_windows_console_and_restores_it(monkeypatch, tmp_path):
    from core.loader import ModuleRegistry

    events = []
    console = _FakeWindowsConsole(events)
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path))
    monkeypatch.setattr(shell, "open_windows_console", lambda: console)
    ui = _windows_ui()
    original = ui.console
    ctx = ShellContext(registry=ModuleRegistry(tmp_path), ui=ui)

    with shell.Shell(ctx) as sh:
        assert events == ["session"]
        assert ui.console.file is console.stream
        assert ui.console.is_terminal and not ui.console.legacy_windows
        assert ui.console.color_system == "truecolor"
        assert ui.console.size == (120, 30)  # measured like the prompt: from the console window
        sh.ui.console.print("[sc.accent]hi[/]")  # the theme is on the new console
        assert "hi" in console.stream.getvalue()
    # The Rich console comes back first, then the session ends, then the console closes.
    assert ui.console is original
    assert events == ["session", "session closed", "console closed"]


def test_shell_restores_the_windows_console_when_setup_fails(monkeypatch, tmp_path):
    from core.loader import ModuleRegistry

    events = []
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path))
    monkeypatch.setattr(shell, "open_windows_console", lambda: _FakeWindowsConsole(events))

    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(shell, "PromptSession", broken)
    ui = _windows_ui()
    original = ui.console
    with pytest.raises(RuntimeError):
        shell.Shell(ShellContext(registry=ModuleRegistry(tmp_path), ui=ui))
    assert ui.console is original
    assert events == ["session", "session closed", "console closed"]


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
