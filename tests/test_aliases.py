import json
import os
from pathlib import Path

import pytest
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from shellcraft.completer import ShellCompleter
from shellcraft.context import ShellContext, to_text
from shellcraft.pipeline import PipelineError, run_line


def out(line, ctx):
    return to_text(run_line(line, ctx).output)


def saved(tmp_path):
    """The aliases kept in ~/.shellcraft/aliases (the test's home)."""
    from shellcraft.aliases import parse_file

    file = tmp_path / ".home" / "aliases"
    return parse_file(file.read_text())[0] if file.exists() else {}


def test_ls_alias_defaults_to_all_and_long(ctx, tmp_path):
    (tmp_path / ".hidden").write_text("x")
    (tmp_path / "shown.txt").write_text("y")
    plain = out("ls", ctx)
    assert ".hidden" not in plain
    run_line("alias ls='ls -a -l'", ctx)
    assert out("ls", ctx) == out("\\ls -a -l", ctx)
    assert ".hidden" in out("ls", ctx) and "-rw" in out("ls", ctx)
    assert out("\\ls", ctx) == plain  # the backslash skips the alias
    assert saved(tmp_path) == {"ls": "ls -a -l"}


def test_typed_args_are_appended_and_pipes_work(ctx):
    run_line("alias say='echo hello'", ctx)
    assert out("say world", ctx) == "hello world\n"
    assert out("echo x | say again | wc -w", ctx).strip() == "2"


def test_aliases_chain_but_never_loop(ctx):
    run_line("alias a='b 1'", ctx)
    run_line("alias b='echo 2'", ctx)
    assert out("a 3", ctx) == "2 1 3\n"
    run_line("alias b='a x'", ctx)  # a → b → a: stops when a repeats
    with pytest.raises(PipelineError, match="command not found: a"):
        run_line("a", ctx)


def test_list_show_and_remove(ctx, tmp_path):
    run_line("alias ll='ls -l'", ctx)
    run_line("alias q=\"grep -F 'x'\"", ctx)
    assert out("alias", ctx) == "alias ll='ls -l'\nalias q=\"grep -F 'x'\"\n"
    assert out("alias ll", ctx) == "alias ll='ls -l'\n"
    run_line("unalias ll", ctx)
    assert saved(tmp_path) == {"q": "grep -F 'x'"}
    with pytest.raises(PipelineError, match="ll: not found"):
        run_line("unalias ll", ctx)
    run_line("unalias -a", ctx)
    assert out("alias", ctx) == "" and saved(tmp_path) == {}


@pytest.mark.parametrize("line, fragment", [
    ("alias x='a | b'", "single command"),
    ("alias x='echo hi > f'", "single command"),
    ("alias x=''", "empty"),
    ("alias 'bad name'=ls", "invalid alias name"),
    ("alias x='a ; b'", "not supported"),
    ("alias nope", "nope: not found"),
    ("unalias", "usage"),
])
def test_bad_definitions(ctx, line, fragment):
    with pytest.raises(PipelineError, match=fragment):
        run_line(line, ctx)


def test_broken_hand_edited_alias_runs_the_command_as_typed(ctx):
    ctx.config["aliases"] = {"echo": "a | b"}
    assert out("echo ok", ctx) == "ok\n"


def test_cd_alias_works_on_its_own_line(ctx, tmp_path):
    (tmp_path / "sub").mkdir()
    run_line("alias down='cd sub'", ctx)
    run_line("down", ctx)
    assert Path(os.getcwd()) == (tmp_path / "sub").resolve()


def test_which_reports_aliases(ctx):
    run_line("alias ls='ls -a -l'", ctx)
    assert out("which ls", ctx) == "ls: aliased to 'ls -a -l'\n"
    assert "ShellCraft builtin" in out("which -a ls", ctx)


def test_no_aliases_for_mcp_contexts(ctx):
    run_line("alias echo='echo aliased'", ctx)
    mcp = ShellContext(registry=ctx.registry, config=ctx.config, allow_stateful=False, allow_aliases=False)
    assert out("echo plain", mcp) == "plain\n"
    with pytest.raises(PipelineError, match="not available"):
        run_line("alias x=y", mcp)


def test_completion_knows_aliases(ctx):
    run_line("alias ll='ls -l'", ctx)
    completer = ShellCompleter(ctx)

    def complete(text):
        return [c.text for c in completer.get_completions(Document(text), CompleteEvent())]

    assert "ll" in complete("l")
    assert complete("unalias ") == ["ll"]
    assert "-a" in complete("ll -")  # ls's switches


# ── the aliases file (~/.shellcraft/aliases, like ~/.bash_aliases) ────────────

def test_hand_edits_apply_to_the_next_command_and_comments_survive(ctx, tmp_path):
    file = tmp_path / ".home" / "aliases"
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text("# my shortcuts\nalias hi='echo hello'\n\nalias two='echo 2' three=\"echo 3\"\n")
    assert out("hi there", ctx) == "hello there\n" and out("three", ctx) == "3\n"
    file.write_text(file.read_text().replace("echo hello", "echo howdy"))
    os.utime(file, ns=(1, os.stat(file).st_mtime_ns + 10_000_000))  # a new mtime, as an editor gives
    assert out("hi", ctx) == "howdy\n"
    run_line("alias hi='echo hey'", ctx)  # replaces the line where it was
    run_line("alias q=\"grep -F 'x'\"", ctx)  # appended, quoted so it reads back the same
    text = file.read_text()
    assert text.startswith("# my shortcuts\nalias hi='echo hey'\n\nalias two='echo 2' three=")
    assert text.endswith("alias q=\"grep -F 'x'\"\n") and saved(tmp_path)["q"] == "grep -F 'x'"
    run_line("unalias two", ctx)  # the line defined two names: both go with it
    assert "two" not in saved(tmp_path) and "# my shortcuts" in file.read_text()
    run_line("unalias -a", ctx)
    assert file.read_text().startswith("# my shortcuts") and saved(tmp_path) == {}


def test_bad_lines_are_skipped_and_listed(ctx, tmp_path):
    file = tmp_path / ".home" / "aliases"
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text("alias ok='echo ok'\nalias piped='ls | wc -l'\nalias open='unclosed\n")
    assert out("ok", ctx) == "ok\n"
    listing = out("alias", ctx)
    assert listing.startswith("alias ok='echo ok'\n")
    assert "# line 2: alias piped: an alias must be a single command" in listing
    assert "# line 3: No closing quotation (skipped)" in listing
    assert out("alias --file", ctx) == f"{file}\n"


def test_config_aliases_move_into_the_file_once(tmp_path, monkeypatch, started):
    from shellcraft.cli import main

    home = tmp_path / ".home"
    home.mkdir()
    monkeypatch.setenv("SHELLCRAFT_HOME", str(home))
    (home / "config.json").write_text(json.dumps({"aliases": {"ll": "ls -l", "q": "grep -F 'x'"}}))
    assert main([]) == 0
    assert saved(tmp_path) == {"ll": "ls -l", "q": "grep -F 'x'"}
    assert json.loads((home / "config.json").read_text()).get("aliases") in ({}, None)
    assert (home / "aliases").read_text().startswith("# ShellCraft aliases, like ~/.bash_aliases")
