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
