import json
import os
import stat
import sys
import textwrap

import pytest
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from core import settings
from core.cli import main
from core.completer import ShellCompleter
from core.context import ShellContext, to_text
from core.loader import ModuleRegistry
from core.pipeline import PipelineError, run_line
from core.shell import RedactingInMemoryHistory

KEY = "DNSDUMPSTER_API_KEY"
SECRET = "dd_secret_value_1234abcd"


@pytest.fixture
def ctx(registry, tmp_path, clean_env):
    clean_env.chdir(tmp_path)
    clean_env.setenv("SHELLCRAFT_HOME", str(tmp_path / ".home"))
    return ShellContext(registry=registry)


def saved(tmp_path) -> dict:
    return json.loads((tmp_path / ".home" / "config.json").read_text())


def test_modules_declare_their_keys(registry):
    keys = settings.env_settings(registry)
    assert {KEY, "CENSYS_API_TOKEN", "CENSYS_ORG_ID"} <= set(keys)
    assert keys[KEY].label == "queryDns · DnsDumpster API key"
    assert keys["CENSYS_ORG_ID"].label == "QueryCensys · Organization ID"


def test_table_lists_keys_after_on_off_settings_masked(ctx):
    settings.env_set(ctx.config, KEY, SECRET)
    os.environ["CENSYS_ORG_ID"] = "org-from-shell"
    text = to_text(run_line("settings", ctx).output, width=200)
    assert text.index("banner") < text.index(KEY)
    assert "queryDns · DnsDumpster API key" in text and "set ••••abcd" in text
    assert SECRET not in text and "org-from-shell" not in text
    assert "from environment" in text and "not set" in text


def test_set_exports_saves_and_masks(ctx, tmp_path):
    out = to_text(run_line(f"settings {KEY} {SECRET}", ctx).output)
    assert "set" in out and "••••abcd" in out and SECRET not in out
    assert os.environ[KEY] == SECRET
    assert saved(tmp_path)["env"][KEY] == SECRET
    if os.name == "posix":
        mode = stat.S_IMODE((tmp_path / ".home" / "config.json").stat().st_mode)
        assert mode == 0o600


def test_reset_restores_the_shell_value(ctx, tmp_path):
    os.environ[KEY] = "from-my-shell"
    run_line(f"settings {KEY} {SECRET}", ctx)
    assert os.environ[KEY] == SECRET
    out = to_text(run_line(f"settings reset {KEY}", ctx).output)
    assert "cleared" in out and os.environ[KEY] == "from-my-shell"
    assert KEY not in saved(tmp_path).get("env", {})
    run_line(f"settings {KEY} {SECRET}", ctx)
    del os.environ[KEY]
    settings._ORIGINAL_ENV[KEY] = None
    run_line(f"settings reset {KEY}", ctx)
    assert KEY not in os.environ


def test_prompt_reads_hidden_value(ctx, monkeypatch):
    import prompt_toolkit

    asked = {}

    def fake_prompt(label, is_password=False):
        asked.update(label=label, hidden=is_password)
        return f"  {SECRET}  "

    monkeypatch.setattr(prompt_toolkit, "prompt", fake_prompt)
    ctx.interactive = True
    run_line(f"settings {KEY}", ctx)
    assert asked["hidden"] and KEY in asked["label"]
    assert os.environ[KEY] == SECRET


def test_prompt_without_terminal_explains(ctx, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False, raising=False)
    with pytest.raises(PipelineError, match=f"use: settings {KEY} VALUE"):
        run_line(f"settings {KEY}", ctx)


def test_errors(ctx):
    with pytest.raises(PipelineError, match="empty value"):
        run_line(f'settings {KEY} " "', ctx)
    with pytest.raises(PipelineError, match="usage"):
        run_line(f"settings {KEY} a b", ctx)
    with pytest.raises(PipelineError, match=f"unknown setting.*{KEY}"):
        run_line("settings NOPE on", ctx)


def test_startup_exports_saved_keys_in_every_mode(tmp_path, clean_env, capsys):
    clean_env.setenv("SHELLCRAFT_HOME", str(tmp_path))
    clean_env.chdir(tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({"env": {KEY: SECRET, "bad name": "x"}}))
    assert main(["-c", "pwd"]) == 0
    assert os.environ[KEY] == SECRET and "bad name" not in os.environ
    del os.environ[KEY]
    import core.mcp_server

    clean_env.setattr(core.mcp_server, "serve", lambda registry, allow_system=False: None)
    assert main(["--mcp"]) == 0
    assert os.environ[KEY] == SECRET


@pytest.mark.parametrize("line, stored", [
    (f"settings {KEY} {SECRET}", f"settings {KEY} ••••"),
    (f'  settings {KEY} "{SECRET}"', f"  settings {KEY} ••••"),
    (f"settings {KEY}", f"settings {KEY}"),
    ("settings pager off", "settings pager off"),
    (f"settings reset {KEY}", f"settings reset {KEY}"),
    ("echo hi", "echo hi"),
])
def test_history_redacts_values(line, stored):
    history = RedactingInMemoryHistory()
    history.append_string(line)
    assert history.get_strings() == [stored]


def test_mask_hides_short_values():
    assert settings.mask("abc") == "••••"
    assert settings.mask("0123456789abcdef") == "••••cdef"


@pytest.fixture
def complete(registry):
    completer = ShellCompleter(ShellContext(registry=registry))
    return lambda text: [c.text for c in completer.get_completions(Document(text), CompleteEvent())]


def test_completion_offers_key_names(complete):
    assert KEY in complete("settings ") and "pager" in complete("settings ")
    assert complete("settings CEN") == ["CENSYS_API_TOKEN", "CENSYS_ORG_ID"]
    assert KEY in complete("settings reset ")
    assert complete(f"settings {KEY} ") == []  # a secret comes next: no suggestions
    assert complete("settings pager ") == ["off", "on", "toggle"]


def write_module(tmp_path, body):
    (tmp_path / "keyed.py").write_text(textwrap.dedent(body))
    registry = ModuleRegistry(tmp_path)
    registry.load()
    return registry


@pytest.mark.parametrize("declaration, error", [
    ('ENV_SETTINGS = [EnvSetting("lower_case", "L", "d")]', "UPPER_CASE"),
    ('ENV_SETTINGS = [EnvSetting("A_KEY", "", "d")]', "non-empty"),
    ('ENV_SETTINGS = [EnvSetting("A_KEY", "L", "d"), EnvSetting("A_KEY", "L", "d")]', "declared twice"),
    ('ENV_SETTINGS = ["A_KEY"]', "must be core.modkit.EnvSetting"),
    ('ENV_SETTINGS = "A_KEY"', "must be a list"),
])
def test_loader_rejects_bad_declarations(tmp_path, declaration, error):
    registry = write_module(tmp_path, f"""\
        from core.modkit import EnvSetting
        {declaration}
        def run(args, stdin):
            return ""
    """)
    assert registry.get("keyed") is None
    assert any(error in w for w in registry.warnings), registry.warnings
