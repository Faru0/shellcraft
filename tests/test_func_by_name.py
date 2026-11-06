"""Every func is a command: typing its name runs it (no `# command: yes` needed)."""

import io
import os

import pytest
from rich.console import Console

from shellcraft import ai_run, funcs
from shellcraft.config import load_config
from shellcraft.context import ShellContext, to_text
from shellcraft.loader import ModuleRegistry
from shellcraft.pipeline import PipelineError, run_line
from shellcraft.themes import UI, all_themes


@pytest.fixture(autouse=True)
def _restore(tmp_path):
    saved, cwd = dict(os.environ), os.getcwd()
    os.chdir(tmp_path)
    yield
    os.chdir(cwd)
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture
def ctx(tmp_path):
    modules = tmp_path / "modules"
    modules.mkdir()
    (modules / "greet.py").write_text("def run(args, stdin):\n    return 'module greet'\n")
    registry = ModuleRegistry(modules)
    registry.load()
    return ShellContext(registry=registry, config=load_config(),
                        ui=UI(all_themes({})["cyberpunk"], Console(file=io.StringIO(), width=100)))


def out(ctx, line):
    result = run_line(line, ctx)
    return "" if result is None or result.output is None else to_text(result.output)


def write(name, text, folder=None):
    folder = folder or funcs.default_dir()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.func").write_text(text, encoding="utf-8")


def test_a_func_runs_by_name_with_arguments(ctx):
    write("cool_stuff", "# func: cool_stuff\n# description: does cool things\necho cool $1 $#\n")
    text = out(ctx, "cool_stuff Ada x")
    assert "cool Ada 2" in text and "✓ cool_stuff" in text


def test_a_session_func_runs_by_name(ctx):
    out(ctx, "func new hello 'echo hello $1'")
    assert "hello Ada" in out(ctx, "hello Ada")


def test_a_func_is_not_a_pipe_stage(ctx):
    out(ctx, "func new hello 'echo hello'")
    with pytest.raises(PipelineError, match="runs on its own line"):
        run_line("hello | wc -l", ctx)


def test_name_clashes_lose(ctx):
    write("greet", "echo func greet\n")  # a module
    write("cd", "echo func cd\n")  # a builtin
    write("hi", "echo func hi\n")
    out(ctx, "alias hi='echo alias hi'")
    out(ctx, "greet2() { echo shell def; }")
    write("greet2", "echo func greet2\n")
    assert out(ctx, "greet") == "module greet"
    assert "func cd" not in out(ctx, "cd .")
    assert "alias hi" in out(ctx, "hi") and "func hi" not in out(ctx, "hi")
    assert "shell def" in out(ctx, "greet2") and "func greet2" not in out(ctx, "greet2")
    assert "func greet" in out(ctx, "func run greet")  # always reachable
    listing = out(ctx, "func list")
    assert "hidden by a module" in listing and "hidden by a builtin" in listing
    assert "hidden by an alias" in listing and "hidden by a shell function" in listing
    shown = out(ctx, "func show greet")
    assert "hidden by a module" in shown and "func run greet" in shown
    assert "hidden by a module" in out(ctx, "func check greet")
    assert "not this func" in out(ctx, "func new greet -f 'echo x'")


def test_live_reload_add_edit_delete(ctx):
    with pytest.raises(PipelineError, match="not found"):
        run_line("later", ctx)
    write("later", "echo one\n")
    assert "one" in out(ctx, "later")
    write("later", "echo two\n")
    assert "two" in out(ctx, "later")
    (funcs.default_dir() / "later.func").unlink()
    with pytest.raises(PipelineError, match="not found"):
        run_line("later", ctx)


def test_profile_func_wins_and_lookup_does_not_scan(ctx, monkeypatch):
    from shellcraft import profiles

    write("both", "echo default\n")
    profiles.create("work")
    out(ctx, "profile use work")
    write("both", "echo profile\n", profiles.folder("work") / "funcs")
    monkeypatch.setattr(funcs, "available", lambda c: pytest.fail("scanned the folders"))
    assert "profile" in out(ctx, "both")
    assert funcs.lookup(ctx, "nothing_here") is None


def test_the_command_header_is_still_read_and_ignored(ctx):
    f = funcs.parse("# func: x\n# command: yes\necho x\n", "x")
    assert f.steps == ["echo x"]
    assert "# command" not in funcs.render(f)
    write("old", "# func: old\n# command: no\necho old\n")
    assert "old" in out(ctx, "old")


def test_func_set_command_and_new_flag_are_no_ops(ctx):
    out(ctx, "func new m 'echo m'")
    note = out(ctx, "func set m command yes")
    assert "every func runs" in note
    out(ctx, "func new k --command 'echo k'")
    assert "k" in out(ctx, "k")
    out(ctx, "func save k")
    assert "# command" not in (funcs.default_dir() / "k.func").read_text()
    assert " cmd" not in out(ctx, "func list")


def test_help_lists_funcs_and_the_count_excludes_them(ctx):
    plain = out(ctx, "help")
    assert "Your funcs" not in plain
    write("cool_stuff", "# description: cool\necho 1\n")
    write("other", "echo 1\n")
    full = out(ctx, "help -a")
    assert "cool_stuff  cool" in full and "other" in full
    rows = [r for r in __import__("shellcraft.builtins", fromlist=["x"])._help_entries(ctx)]
    assert [r[1] for r in rows if r[0] == "Your funcs"] == ["cool_stuff", "other"]
    ctx.ui.console.file.truncate(0)
    from shellcraft import builtins

    screen = builtins._help_screen(ctx, rows, False, None)
    ctx.ui.console.print(screen)
    text = ctx.ui.console.file.getvalue()
    assert "Your funcs" in text and "cool_stuff" in text
    builtin_rows = [r for r in rows if r[0] not in ("Modules", "Your funcs")]
    assert f" {len(builtin_rows)} commands" in text
    assert text.index("Modules") < text.index("Your funcs")


def test_help_name_type_which_man(ctx):
    write("cool_stuff", "# description: does cool things\n# usage: cool_stuff X\necho cool\n")
    assert "does cool things" in out(ctx, "help cool_stuff") and "cool_stuff X" in out(ctx, "help cool_stuff")
    assert "cool_stuff is a func" in out(ctx, "type cool_stuff")
    assert out(ctx, "type -t cool_stuff").strip() == "func"
    assert "cool_stuff: func" in out(ctx, "which cool_stuff")
    page = out(ctx, "man cool_stuff")
    assert "does cool things" in page and "echo cool" in page and "cool_stuff X" in page


def test_func_alone_still_lists(ctx):
    write("cool_stuff", "echo x\n")
    assert "cool_stuff" in out(ctx, "func")


def test_did_you_mean_includes_funcs(ctx):
    write("deploy_all", "echo x\n")
    with pytest.raises(PipelineError, match="did you mean 'deploy_all'"):
        run_line("deploy_al", ctx)
    with pytest.raises(PipelineError, match="did you mean 'deploy_all'"):
        run_line("help deploy_al", ctx)


def test_completion_and_highlighting_see_funcs_without_the_header(ctx):
    from prompt_toolkit.document import Document

    from shellcraft.completer import ShellCompleter

    write("cool_stuff", "# description: cool\necho x\n")
    names = [c.text for c in ShellCompleter(ctx).get_completions(Document("cool"), None)]
    assert "cool_stuff" in names
    assert funcs.as_command(ctx, "cool_stuff") is not None


def test_funcs_never_run_where_aliases_do_not(ctx):
    """The MCP context has allow_aliases off: no func by name, and an AI never counts a func as read-only."""
    write("cool_stuff", "echo x\n")
    assert ai_run._reads_only("cool_stuff", ctx, {}) is False  # not a builtin or a module: needs approval
    ctx.allow_aliases = False
    assert funcs.as_command(ctx, "cool_stuff") is None
    with pytest.raises(PipelineError, match="not found"):
        run_line("cool_stuff", ctx)
    from shellcraft import builtins

    assert not [r for r in builtins._help_entries(ctx) if r[0] == "Your funcs"]
