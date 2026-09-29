import json
import os
from pathlib import Path

import pytest
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from core.completer import ShellCompleter
from core.context import ShellContext, to_text
from core.pipeline import PipelineError, run_line


def out(line, ctx):
    return to_text(run_line(line, ctx).output)


def saved(tmp_path):
    return json.loads((tmp_path / ".home" / "config.json").read_text())


def test_ls_alias_defaults_to_all_and_long(ctx, tmp_path):
    (tmp_path / ".hidden").write_text("x")
    (tmp_path / "shown.txt").write_text("y")
    plain = out("ls", ctx)
    assert ".hidden" not in plain
    run_line("alias ls='ls -a -l'", ctx)
    assert out("ls", ctx) == out("\\ls -a -l", ctx)
    assert ".hidden" in out("ls", ctx) and "-rw" in out("ls", ctx)
    assert out("\\ls", ctx) == plain  # the backslash skips the alias
    assert saved(tmp_path)["aliases"] == {"ls": "ls -a -l"}


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
    assert saved(tmp_path)["aliases"] == {"q": "grep -F 'x'"}
    with pytest.raises(PipelineError, match="ll: not found"):
        run_line("unalias ll", ctx)
    run_line("unalias -a", ctx)
    assert out("alias", ctx) == "" and saved(tmp_path)["aliases"] == {}


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
