"""Completion guided by a command's usage line (shellcraft.usage)."""

import pytest
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

import shellcraft.commands  # noqa: F401 — registers the command builtins
from shellcraft.builtins import BUILTINS
from shellcraft.completer import ShellCompleter
from shellcraft.context import ShellContext
from shellcraft.loader import ModuleRegistry
from shellcraft.usage import expect, grammar


def _expect(name, words):
    return expect(name, BUILTINS[name].usage, words)


def test_every_builtin_usage_is_read_except_shell_syntax():
    unread = {n for n, b in BUILTINS.items() if grammar(n, b.usage) is None}
    assert unread <= {"for", "if", "select", "function", "while", "until", "case"}


def test_what_comes_next():
    cut = _expect("cut", [])
    assert cut.flags == {"-f", "-c"} and not cut.metas and not cut.can_end  # a switch is needed
    after = _expect("cut", ["-f", "1", "-d", ","])
    assert after.wants_path and after.can_end and "-d" not in after.free_flags  # used already
    assert set(_expect("mcp", []).literals) == {"start", "stop", "restart", "show", "token", "log"}
    assert set(_expect("mcp", ["token"]).literals) == {"rotate"}
    grep = _expect("grep", ["-i", "-m", "3"])  # optional switches anywhere, with their values
    assert [t.text for t in grep.metas] == ["PATTERN"] and not grep.wants_path
    assert _expect("grep", ["-i", "foo"]).wants_path
    assert _expect("time", []).wants_command
    assert _expect("alert", ["-l", "warn"]).flags == {"--list", "-c"}
    assert _expect("mcp", ["bogus"]) is None  # words the usage doesn't fit: file names as before


def test_the_values_of_a_switch_in_parentheses_and_in_a_list():
    g = grammar("alert", BUILTINS["alert"].usage)
    assert g.free["-l"].arg.values == ("info", "warn", "crit")
    assert grammar("mark", BUILTINS["mark"].usage).free["-p"].arg.values == ("low", "medium", "high")


@pytest.fixture
def run(tmp_path, monkeypatch):
    work, mods = tmp_path / "work", tmp_path / "mods"
    work.mkdir()
    mods.mkdir()
    monkeypatch.chdir(work)
    (work / "notes.txt").write_text("")
    (mods / "m.py").write_text("def run(args, stdin):\n    return ''\n")
    (mods / "m.skill").write_text('summary = "a module"\nusage = "m (--to NAME | --list) [FILE]"\n')
    registry = ModuleRegistry(mods)
    registry.load()
    completer = ShellCompleter(ShellContext(registry=registry))
    return lambda t: [c.text for c in completer.get_completions(Document(t), CompleteEvent())]


def test_completion_follows_the_usage(run):
    assert run("cut ") == ["-c", "-f"]  # not the files: cut can't run without one of these
    assert run("cut -f 1 ") == ["notes.txt"]
    assert run("mcp ") == ["log", "restart", "show", "start", "stop", "token"]
    assert run("grep ") == []  # a PATTERN goes here
    assert run("grep no") == ["tes.txt"]  # though a word typed in part may be a file name
    assert run("grep foo ") == ["notes.txt"]
    assert "cat" in run("time ")
    assert run("m ") == ["--list", "--to"]  # a module: from its .skill usage, no docs needed
    assert run("m --") == ["--list", "--to"]
    assert run("m --list ") == ["notes.txt"]
    assert run("ls ") == ["notes.txt"]
