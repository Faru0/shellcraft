"""Funcs (shellcraft.funcs, the `func` command) and the editor setting (shellcraft.editor)."""

import io
import json
import os

import pytest
from rich.console import Console

from shellcraft import editor, funcs, profiles
from shellcraft.config import config_dir, load_config
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
                        ui=UI(all_themes({})["cyberpunk"], Console(file=io.StringIO())))


def out(ctx, line):
    result = run_line(line, ctx)
    return "" if result is None or result.output is None else to_text(result.output)


FILE = """# func: recon
# description: quick recon
# system: no
# output: show
# command: yes
# usage: recon DOMAIN
# a comment, not a step
echo "start $1"
for x in a b; do
  echo item $x
done
@ echo marked
"""


def test_parse_and_render_round_trip():
    f = funcs.parse(FILE, "recon")
    assert (f.name, f.description, f.system, f.output, f.command, f.usage) == ("recon", "quick recon", False,
                                                                                "show", True, "recon DOMAIN")
    assert f.steps == ['echo "start $1"', "for x in a b; do echo item $x ; done", "@ echo marked"]
    again = funcs.parse(funcs.render(f), "recon")
    assert again.steps == f.steps and again.usage == f.usage and again.command
    bare = funcs.parse("ls\npwd\n", "plain")
    assert bare.output == "show" and not bare.system and bare.steps == ["ls", "pwd"]


def test_run_like_typed_with_arguments_and_stop_at_failure(ctx, tmp_path):
    out(ctx, "func new work 'mkdir -p sub' 'cd sub' 'v=$1' 'env set W_ARG=$2' 'echo args $# $@'")
    text = out(ctx, "func run work one two")
    assert "args 2 one two" in text and "✓ work: 5 steps" in text
    assert os.getcwd() == str(tmp_path / "sub") and ctx.variables["v"] == "one" and os.environ["W_ARG"] == "two"
    out(ctx, "func new broken 'echo first' 'grep nothing /dev/null' 'echo never'")
    result = run_line("func run broken", ctx)
    text = to_text(result.output)
    assert "first" in text and "never" not in text and "step 2 failed (grep nothing /dev/null)" in text
    assert result.status == 1 and ctx.last_status == 1


def test_quiet_output_shows_only_echo_and_marked_steps(ctx):
    out(ctx, "func new q --quiet 'seq 3' 'echo said' '@ seq 2' 'printf \"%s\\n\" pf'")
    text = out(ctx, "func run q")
    assert "said" in text and "pf" in text and "1\n2\n" in text and "3" not in text.split("✓")[0]
    loud = out(ctx, "func run -v q")
    assert "1\n2\n3" in loud
    out(ctx, "func set q output show")
    assert "1\n2\n3" in out(ctx, "func run q") and "said" in out(ctx, "func run -q q")


def test_isolated_puts_everything_back_and_saves_nothing(ctx, tmp_path):
    os.environ["ISO_HOST"] = "host"
    out(ctx, "func new iso 'mkdir -p deep' 'cd deep' 'x=1' 'env set ISO_HOST=func' 'env set ISO_NEW=1' "
             "'alias zz=\"echo zz\"' 'theme matrix' 'settings links on'")
    out(ctx, "func run -i iso")
    assert os.getcwd() == str(tmp_path) and "x" not in ctx.variables
    assert os.environ["ISO_HOST"] == "host" and "ISO_NEW" not in os.environ
    assert ctx.ui.theme.name == "cyberpunk" and "links" not in (ctx.config.get("settings") or {})
    with pytest.raises(PipelineError, match="not found"):
        run_line("zz", ctx)
    saved = config_dir() / "config.json"
    assert not saved.exists() or "matrix" not in saved.read_text()


def test_system_funcs_need_os_commands(ctx):
    out(ctx, "func new sys --system 'echo hi'")
    with pytest.raises(PipelineError, match="settings system_commands on"):
        run_line("func run sys", ctx)
    ctx.allow_system = True
    assert "hi" in out(ctx, "func run sys")


def test_command_funcs_run_by_name_but_not_in_pipes(ctx):
    out(ctx, "func new hello --command 'echo hello $1'")
    assert "hello Ada" in out(ctx, "hello Ada")
    with pytest.raises(PipelineError, match="runs on its own line"):
        run_line("hello Ada | wc -l", ctx)
    out(ctx, "func new greet --command 'echo func greet'")
    assert out(ctx, "greet") == "module greet"  # a module (or builtin) wins
    out(ctx, "func new plain 'echo plain'")
    with pytest.raises(PipelineError, match="not found"):
        run_line("plain", ctx)  # command: no


def test_default_and_profile_layers_and_where_saves_go(ctx):
    out(ctx, "func new shared --save 'echo default version'")
    assert (funcs.default_dir() / "shared.func").exists()
    profiles.create("osint")
    out(ctx, "profile use osint")
    assert "default version" in out(ctx, "func run shared")  # default funcs work in a profile
    out(ctx, "func new shared -f --save 'echo profile version'")
    assert (funcs.profile_dir("osint") / "shared.func").exists()
    assert "profile version" in out(ctx, "func run shared")
    assert "osint (hides default)" in out(ctx, "func list")
    out(ctx, "func new glob -g --save 'echo g'")
    assert (funcs.default_dir() / "glob.func").exists()
    out(ctx, "func new temp 'echo t'")
    assert "session" in out(ctx, "func") and not list(funcs.default_dir().glob("temp*"))
    out(ctx, "func save temp")
    assert (funcs.profile_dir("osint") / "temp.func").exists()
    out(ctx, "profile off")
    assert "default version" in out(ctx, "func run shared") and "temp" not in funcs.available(ctx)


def test_managing_steps_fields_names_and_files(ctx, tmp_path):
    out(ctx, "func new m --save 'echo one'")
    out(ctx, "func add m 'echo three'")
    out(ctx, "func insert m 2 'echo two'")
    assert funcs.find(ctx, "m").steps == ["echo one", "echo two", "echo three"]
    out(ctx, "func move m 3 1")
    out(ctx, "func rm m 2")
    assert funcs.find(ctx, "m").steps == ["echo three", "echo two"]
    out(ctx, "func set m description 'my func'")
    out(ctx, "func set m command yes")
    f = funcs.find(ctx, "m")
    assert f.description == "my func" and f.command and "# command: yes" in f.path.read_text()
    out(ctx, "func rename m m2")
    out(ctx, "func copy m2 m3")
    assert {"m2", "m3"} <= set(funcs.available(ctx)) and "m" not in funcs.available(ctx)
    out(ctx, "func delete m3 -y")
    target = tmp_path / "funcs.json"
    out(ctx, f"func export --all {target}")
    out(ctx, "func delete m2 -y")
    out(ctx, f"func import {target}")
    assert funcs.find(ctx, "m2").steps == ["echo three", "echo two"]
    with pytest.raises(PipelineError, match="exists already"):
        run_line(f"func import {target}", ctx)
    assert "echo three" in to_text(run_line("func show m2", ctx).output)


def test_guided_creation_and_background_run(ctx, monkeypatch, tmp_path):
    from shellcraft import ask

    answers = iter(["does things", "n", "quiet", "y", "g DOMAIN", "echo step $1", "seq 2", ""])
    monkeypatch.setattr(ask, "ask", lambda c, prompt, hidden=False: next(answers))
    ctx.interactive = True
    out(ctx, "func new g --save")
    f = funcs.find(ctx, "g")
    assert (f.description, f.output, f.command, f.usage) == ("does things", "quiet", True, "g DOMAIN")
    assert f.steps == ["echo step $1", "seq 2"]
    from shellcraft.jobs import JobTable

    ctx.jobs = JobTable(tmp_path / "jobs")
    assert "running in the background" in out(ctx, "func run -b g example.com")
    job = ctx.jobs.get(1)
    assert job.name == "func g" and ctx.jobs.wait_finished(job, timeout=5)
    assert "step example.com" in job.output and "✓ g: 2 steps" in job.output


def test_profile_export_import_and_save_carry_funcs(ctx, tmp_path):
    profiles.create("p")
    out(ctx, "profile use p")
    out(ctx, "func new pf --save 'echo pf'")
    out(ctx, "func new sess 'echo s'")
    out(ctx, "profile save p2 -y")
    assert {"pf", "sess"} <= {x.stem for x in (profiles.folder("p2") / "funcs").glob("*.func")}
    target = tmp_path / "p.json"
    out(ctx, f"profile export p {target}")
    assert "pf" in json.loads(target.read_text())["funcs"]
    out(ctx, f"profile import {target} p3")
    assert (profiles.folder("p3") / "funcs" / "pf.func").exists()


def test_the_editor_setting(ctx, monkeypatch):
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "vim")
    assert editor.command(ctx.config) == ["vim"] and editor.source(ctx.config) == "$EDITOR"
    out(ctx, "settings editor code")
    assert json.loads((config_dir() / "config.json").read_text())["editor"] == "code"
    argv = editor.command(ctx.config)
    assert editor.is_window(argv) and editor.with_wait(argv) == ["code", "--wait"]
    assert editor.with_wait(["code", "--wait"]) == ["code", "--wait"] and not editor.is_window(["vim"])
    assert not editor.is_window(["emacs", "-nw"]) and editor.is_window(["emacs"])
    ran = []
    monkeypatch.setattr(editor.subprocess, "run", lambda argv, check=False: ran.append(argv))
    editor.open_file(ctx, config_dir() / "x.txt")
    assert ran[0][1:] == ["--wait", str(config_dir() / "x.txt")]
    out(ctx, "settings reset editor")
    assert editor.command(ctx.config) == ["vim"]
    assert "editor" in out(ctx, "settings")
