import ntpath

import pytest

from core import shell
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


def test_window_size_is_the_visible_window_minus_the_wrap_column():
    from types import SimpleNamespace as NS

    info = NS(srWindow=NS(Left=0, Top=100, Right=119, Bottom=129), dwSize=NS(X=120, Y=9000))
    assert shell._window_size(info) == shell.Size(rows=30, columns=119)
    narrow_buffer = NS(srWindow=NS(Left=0, Top=0, Right=199, Bottom=9), dwSize=NS(X=80, Y=10))
    assert shell._window_size(narrow_buffer).columns == 79


def test_cli_runs_the_interactive_shell_on_the_windows_console(monkeypatch, tmp_path):
    import io

    from rich.console import Console

    from core import cli

    events = []

    class FakeConsole:
        console = Console(file=io.StringIO())

        def rich_console(self):
            return self.console

        def session(self):
            class Session:
                def __enter__(self):
                    events.append("session")

                def __exit__(self, *exc):
                    events.append("session closed")

            return Session()

        def close(self):
            events.append("console closed")

    class FakeShell:
        def __init__(self, ctx):
            events.append(("shell", ctx.ui.console is FakeConsole.console))

        def loop(self):
            raise RuntimeError("boom")

    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path))
    monkeypatch.setattr(shell, "open_windows_console", FakeConsole)
    monkeypatch.setattr(shell, "Shell", FakeShell)
    with pytest.raises(RuntimeError):
        cli.main(["--no-banner", "--modules", str(tmp_path)])
    # Rich and prompt_toolkit both use the console, and it is restored even when the shell crashes.
    assert events == ["session", ("shell", True), "session closed", "console closed"]
