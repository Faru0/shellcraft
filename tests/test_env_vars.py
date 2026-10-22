"""`env`: the environment variables set in this session (env set / unset / export / the rc file),
who sees them, secrets, and the `settings` section."""

import io
import os
import subprocess
import sys

import pytest
from rich.console import Console

from shellcraft import envvars, settings, startup
from shellcraft.context import ShellContext, to_text
from shellcraft.loader import ModuleRegistry
from shellcraft.pipeline import PipelineError, run_line
from shellcraft.themes import UI, all_themes


@pytest.fixture(autouse=True)
def _restore_environ():
    """The code under test writes os.environ itself; put it back after each test."""
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture
def ctx(tmp_path):
    modules = tmp_path / "modules"
    modules.mkdir()
    (modules / "readenv.py").write_text("import os\n\ndef run(args, stdin):\n    return os.environ.get(args[0], '-')\n")
    registry = ModuleRegistry(modules)
    registry.load()
    return ShellContext(registry=registry, config={})


def out(ctx, line):
    result = run_line(line, ctx)
    return "" if result is None or result.output is None else to_text(result.output)


def flat(text):
    """Table text as one line: borders and wrapping gone."""
    return " ".join("".join(c if c not in "│┃" else " " for c in text).split())


def test_env_lists_only_this_sessions_variables_and_a_shows_everything(ctx):
    assert "no variables set in this session" in out(ctx, "env")
    out(ctx, "env set API_URL=https://api.example.test")
    out(ctx, "env set GREETING 'hello there'")
    assert out(ctx, "env") == "API_URL=https://api.example.test\nGREETING=hello there\n"
    everything = out(ctx, "env -a")
    assert "API_URL=https://api.example.test" in everything and f"PATH={os.environ['PATH']}" in everything
    assert out(ctx, "env --all") == everything
    assert out(ctx, "env PATH") == f"PATH={os.environ['PATH']}\n"  # a lookup reads the whole environment
    with pytest.raises(PipelineError, match="usage: env"):
        run_line("env -x", ctx)


def test_what_is_set_reaches_modules_programs_and_subshells(ctx):
    out(ctx, "env set SC_PROBE=42 SC_OTHER=7")
    assert os.environ["SC_PROBE"] == "42"
    assert out(ctx, "readenv SC_PROBE") == "42"  # a module, in-process
    assert out(ctx, "echo $SC_PROBE") == "42\n"
    child = subprocess.run([sys.executable, "-c", "import os; print(os.environ['SC_PROBE'])"],
                           capture_output=True, text=True)
    assert child.stdout == "42\n"  # anything started afterwards inherits it
    ctx.variables["SC_PROBE"] = "old"
    out(ctx, "env set SC_PROBE=43")
    assert out(ctx, "echo $SC_PROBE") == "43\n"  # the shell variable follows, never stale


def test_a_host_value_is_overridden_and_comes_back(ctx):
    os.environ["SC_HOST"] = "from the host"
    message = out(ctx, "env set SC_HOST=mine")
    assert "overrides your shell's value" in flat(message) and os.environ["SC_HOST"] == "mine"
    out(ctx, "env set SC_HOST=again")  # a second set keeps the original host value
    assert "restored to your shell's value" in out(ctx, "env unset SC_HOST")
    assert os.environ["SC_HOST"] == "from the host" and "SC_HOST" not in out(ctx, "env")
    out(ctx, "env set SC_NEW=1")
    assert "removed" in out(ctx, "env unset SC_NEW") and "SC_NEW" not in os.environ
    assert "not set in this session" in out(ctx, "env unset SC_NEVER")
    with pytest.raises(PipelineError, match="not a valid name"):
        run_line("env set 1BAD=x", ctx)
    with pytest.raises(PipelineError, match="expected NAME=VALUE"):
        run_line("env set ONLYNAME", ctx)


def test_export_and_unset_share_the_list(ctx):
    out(ctx, "export SC_EXP=5")
    assert out(ctx, "env") == "SC_EXP=5\n"
    os.environ["SC_HOST2"] = "h"
    out(ctx, "export SC_HOST2")  # exporting a host value unchanged is not "set here"
    assert "SC_HOST2" not in out(ctx, "env")
    out(ctx, "env set SC_GONE=1")
    out(ctx, "unset SC_GONE")  # bash's unset: gone for the session
    assert "SC_GONE" not in os.environ and "SC_GONE" not in out(ctx, "env")


def test_the_screen_table_prints_long_values_whole(ctx):
    long_value = "/opt/" + "very-long-segment/" * 20
    out(ctx, f"env set LONG_PATH={long_value}")
    console = Console(file=io.StringIO(), width=80, record=True, color_system=None)
    ctx.ui = UI(all_themes({})["nord"], console)
    result = run_line("env", ctx)
    console.print(result.output.renderable)
    screen = console.export_text()
    cells = [line.split("│")[2].strip() for line in screen.splitlines() if line.count("│") == 4]
    assert "".join(cells) == long_value and "…" not in screen and "typed" in screen  # wrapped in the cell, not cut


def test_secrets_are_masked_and_kept_out_of_history(ctx, monkeypatch):
    out(ctx, "env set -s SC_TOKEN tok-1234567890abcd")
    assert os.environ["SC_TOKEN"] == "tok-1234567890abcd"
    assert out(ctx, "env") == "SC_TOKEN=••••abcd\n"  # masked, piped too
    out(ctx, "env unset SC_TOKEN")
    assert "SC_TOKEN" not in os.environ
    with pytest.raises(PipelineError, match="no terminal"):
        run_line("env set -s SC_TOKEN", ctx)  # nothing to ask on (never stored anywhere)
    asked = []

    def fake_ask(c, prompt, hidden=False):
        asked.append((prompt, hidden))
        return "typed-secret-value"

    from shellcraft import ask
    monkeypatch.setattr(ask, "ask", fake_ask)
    out(ctx, "env set -s SC_TOKEN")
    assert asked == [("SC_TOKEN (hidden): ", True)] and os.environ["SC_TOKEN"] == "typed-secret-value"
    assert settings.redact_line("env set -s SC_TOKEN abc") == "env set -s SC_TOKEN ••••"
    assert settings.redact_line("env set SC_PLAIN abc") == "env set SC_PLAIN abc"


def test_the_rc_file_sets_them_and_says_so(ctx, tmp_path):
    rc = tmp_path / "rc"
    rc.write_text("env set SC_FROM_RC=yes\nexport SC_EXPORTED=1\n")
    startup.source(rc, ctx)
    entries = {e.name: e.origin for e in envvars.session(ctx)}
    assert entries == {"SC_FROM_RC": "rc", "SC_EXPORTED": "export"} and os.environ["SC_FROM_RC"] == "yes"
    out(ctx, "env set SC_TYPED=1")
    assert {e.name: e.origin for e in envvars.session(ctx)}["SC_TYPED"] == "typed"


def test_settings_lists_them_and_reset_removes_one(ctx):
    os.environ["SC_HOST3"] = "host"
    out(ctx, "env set SC_HOST3=mine SC_SETTING=" + "v" * 200)
    table = flat(out(ctx, "settings"))
    assert "SC_SETTING" in table and "Environment (this" in table and "overrides" in table
    assert "restored to your shell's value" in out(ctx, "settings reset SC_HOST3")
    assert os.environ["SC_HOST3"] == "host"
    assert "removed" in out(ctx, "settings reset SC_SETTING") and "SC_SETTING" not in os.environ


def test_env_is_not_for_mcp_clients(ctx):
    ctx.allow_sensitive = False
    with pytest.raises(PipelineError):
        run_line("env set X=1", ctx)
    with pytest.raises(PipelineError):
        run_line("env -a", ctx)
