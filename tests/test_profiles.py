"""Profiles (shellcraft.profiles, the `profile` command): storage, layering, switching, where
changes go, `profile save`, the wizard, export / import, `--profile`."""

import io
import json
import os

import pytest
from rich.console import Console

from shellcraft import aliases, envvars, profiles
from shellcraft.config import config_dir, load_config, save_config
from shellcraft.context import ShellContext, to_text
from shellcraft.loader import ModuleRegistry
from shellcraft.pipeline import PipelineError, run_line
from shellcraft.themes import UI, all_themes


@pytest.fixture(autouse=True)
def _restore_environ():
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture
def ctx(tmp_path):
    modules = tmp_path / "modules"
    modules.mkdir()
    for name in ("alpha", "beta"):
        (modules / f"{name}.py").write_text(f"def run(args, stdin):\n    return '{name}'\n")
    registry = ModuleRegistry(modules)
    registry.load()
    c = ShellContext(registry=registry, config=load_config(),
                     ui=UI(all_themes({})["cyberpunk"], Console(file=io.StringIO())))
    return c


def out(ctx, line):
    result = run_line(line, ctx)
    return "" if result is None or result.output is None else to_text(result.output)


def base_file():
    return json.loads((config_dir() / "config.json").read_text())


def test_every_new_profile_gets_a_startup_folder(ctx):
    out(ctx, "profile new plain --empty")
    out(ctx, "profile new spy --template osint")
    out(ctx, "profile copy spy spy2")
    for name in ("plain", "spy", "spy2"):
        assert (profiles.folder(name) / "startup").is_dir()
    assert list((profiles.folder("plain") / "startup").iterdir()) == []
    (profiles.folder("plain") / "startup" / "10-a.sc").write_text("x = 1\n")
    profiles.make_startup("plain")
    assert [p.name for p in (profiles.folder("plain") / "startup").iterdir()] == ["10-a.sc"]


def test_create_list_show_copy_rename_delete(ctx):
    out(ctx, "profile new recon --empty -d 'recon work'")
    assert profiles.names() == ["recon"] and profiles.load("recon")["description"] == "recon work"
    listing = out(ctx, "profile")
    assert "* default" in listing and "recon\trecon work" in listing
    out(ctx, "profile copy recon recon2")
    out(ctx, "profile rename recon2 recon3")
    assert profiles.names() == ["recon", "recon3"]
    out(ctx, "profile delete recon3 -y")
    assert profiles.names() == ["recon"]
    for bad in ("default", "Bad Name", "-x", "a" * 40):
        with pytest.raises(profiles.ProfileError):
            profiles.check_name(bad)
    with pytest.raises(PipelineError, match="exists already"):
        run_line("profile new recon --empty", ctx)
    with pytest.raises(PipelineError, match="no profile 'ghost'"):
        run_line("profile use ghost", ctx)
    assert "theme" in out(ctx, "profile show recon")


def test_effective_layers_a_profile_over_the_base(ctx):
    base = load_config()
    base["settings"] = {"links": False, "spinner": False}
    base["disabled_modules"] = ["beta"]
    profiles.create("p", {"theme": "nord", "settings": {"links": True},
                          "modules": {"enable": ["beta"], "disable": ["alpha"]}})
    eff = profiles.effective(base, "p")
    assert eff["theme"] == "nord" and eff["settings"] == {"links": True, "spinner": False}
    assert eff["disabled_modules"] == ["alpha"] and eff.profile == "p"
    assert profiles.overlay_of(eff, base) == {"theme": "nord", "settings": {"links": True},
                                              "modules": {"enable": ["beta"], "disable": ["alpha"]}}


def test_use_applies_everything_and_off_undoes_it(ctx):
    os.environ["SC_HOSTVAL"] = "host"
    out(ctx, "env set SC_MINE=typed")
    profiles.create("osint", {"theme": "nord", "settings": {"system_commands": True},
                              "modules": {"disable": ["alpha"]},
                              "env": {"OSINT_ROOT": "~/osint", "SC_HOSTVAL": "profile"}},
                    rc="env set SC_FROM_RC=1\n", aliases="alias q='echo hi'\n")
    message = out(ctx, "profile use osint")
    assert "◆ osint" in message and "theme nord" in message
    assert ctx.ui.theme.name == "nord" and ctx.allow_system is True  # theme and a setting's apply hook
    with pytest.raises(PipelineError, match="disabled"):
        run_line("alpha", ctx)
    assert os.environ["OSINT_ROOT"] == os.path.expanduser("~/osint") and os.environ["SC_HOSTVAL"] == "profile"
    assert os.environ["SC_FROM_RC"] == "1" and os.environ[profiles.ENV_VAR] == "osint"
    origins = {e.name: e.origin for e in envvars.session(ctx)}
    assert origins["OSINT_ROOT"] == "profile:osint" and origins["SC_FROM_RC"] == "profile:osint"
    assert out(ctx, "q") == "hi\n"  # the profile's alias
    out(ctx, "profile off")
    assert ctx.ui.theme.name == "cyberpunk" and ctx.allow_system is False and out(ctx, "alpha") == "alpha"
    assert "OSINT_ROOT" not in os.environ and "SC_FROM_RC" not in os.environ
    assert os.environ["SC_HOSTVAL"] == "host" and os.environ["SC_MINE"] == "typed"  # typed vars stay
    assert profiles.ENV_VAR not in os.environ
    with pytest.raises(PipelineError, match="not found"):
        run_line("q", ctx)


def test_changes_go_to_the_active_profile_and_globals_to_the_base(ctx):
    profiles.create("soc")
    out(ctx, "profile use soc")
    out(ctx, "theme matrix")
    out(ctx, "settings links on")
    out(ctx, "modules disable beta")
    out(ctx, "alias sev='grep -i crit'")
    out(ctx, "alias -g ll='ls -l'")
    data = profiles.load("soc")
    assert data["theme"] == "matrix" and data["settings"] == {"links": True}
    assert data["modules"] == {"disable": ["beta"]}
    assert "sev" in profiles.read_text("soc", "aliases") and "alias ll" not in profiles.read_text("soc", "aliases")
    assert "ll" in aliases.path().read_text()
    base = base_file() if (config_dir() / "config.json").exists() else {}
    assert base.get("theme", "cyberpunk") == "cyberpunk" and "links" not in base.get("settings", {})
    ctx.config["prefix_key"] = "ctrl-b"  # not a profile key: the base file
    save_config(ctx.config)
    assert base_file()["prefix_key"] == "ctrl-b" and "prefix_key" not in profiles.load("soc")
    out(ctx, "profile off")
    assert ctx.config["theme"] == "cyberpunk" and "sev" not in aliases.get_all(ctx.config)
    assert "ll" in aliases.get_all(ctx.config)


def test_each_profile_keeps_its_own_history(ctx):
    profiles.create("a")
    profiles.create("b", {"history": "shared"})
    ctx.config.profile = "a"
    assert profiles.history_file(ctx.config) == profiles.folder("a") / "history"
    ctx.config.profile = "b"
    assert profiles.history_file(ctx.config) == config_dir() / "history"
    switched = []
    ctx.profile_hook = switched.append
    ctx.config.profile = None
    out(ctx, "profile use a")
    assert switched == ["a"]


def test_save_captures_the_session_and_secrets_become_rc_lines(ctx):
    out(ctx, "theme dracula")
    out(ctx, "settings links on")
    out(ctx, "env set TARGET=acme.test")
    out(ctx, "env set -s API_TOKEN tok-1234567890")
    out(ctx, "profile save acme -d 'acme program'")
    data = profiles.load("acme")
    assert data["theme"] == "dracula" and data["settings"]["links"] is True and data["env"] == {"TARGET": "acme.test"}
    assert data["description"] == "acme program"
    rc = profiles.read_text("acme", "rc")
    assert "env set -s API_TOKEN" in rc and "tok-1234567890" not in json.dumps(data) + rc
    with pytest.raises(PipelineError, match="exists already"):
        run_line("profile save acme", ctx)
    out(ctx, "profile save acme -f")


def test_starters_wizard_diff_export_import(ctx, monkeypatch, tmp_path):
    out(ctx, "profile new bb --template bugbounty")
    assert profiles.load("bb")["theme"] == "dracula" and "subs" in profiles.read_text("bb", "aliases")
    assert "bugbounty" in out(ctx, "profile starters")
    answers = iter(["OSINT work", "osint", "nord", "", "", "CASE=42", "SHODAN_KEY", "", "w=whoami", "", "y",
                    "#112233", "y", "n"])
    from shellcraft import ask
    monkeypatch.setattr(ask, "ask", lambda c, prompt, hidden=False: next(answers))
    ctx.interactive = True
    out(ctx, "profile new intel")
    data = profiles.load("intel")
    assert data["description"] == "OSINT work" and data["theme"] == "nord" and data["color"] == "#112233"
    assert data["env"]["CASE"] == "42" and "OSINT_ROOT" in data["env"]  # from the osint starter
    assert "env set -s SHODAN_KEY" in profiles.read_text("intel", "rc")
    assert "alias w='whoami'" in profiles.read_text("intel", "aliases")
    ctx.interactive = False
    rows = out(ctx, "profile diff bb intel")
    assert "theme\tdracula\tnord" in rows
    target = tmp_path / "bb.json"
    out(ctx, f"profile export bb {target}")
    out(ctx, f"profile import {target} bb2")
    assert profiles.load("bb2")["theme"] == "dracula" and profiles.read_text("bb2", "aliases")
    with pytest.raises(PipelineError, match="exists already"):
        run_line(f"profile import {target} bb2", ctx)
    out(ctx, "profile env bb2 set SCOPE '*.acme.test'")
    assert profiles.load("bb2")["env"]["SCOPE"] == "*.acme.test"
    out(ctx, "profile set bb2 history shared")
    assert profiles.load("bb2")["history"] == "shared"


def test_cli_starts_in_a_profile_and_a_subshell_inherits_it(monkeypatch, tmp_path):
    from shellcraft import cli, shell

    started = []

    class FakeShell:
        def __init__(self, c):
            started.append(c)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

        def banner(self, *a):
            pass

        def loop(self):
            return 0

    monkeypatch.setattr(shell, "Shell", FakeShell)
    profiles.create("work", {"theme": "nord", "env": {"WORK_VAR": "1"}})
    assert cli.main(["--profile", "work", "--no-startup", "--modules", str(tmp_path)]) == 0
    assert started[-1].config.profile == "work" and started[-1].config["theme"] == "nord"
    assert os.environ[profiles.ENV_VAR] == "work" and os.environ["WORK_VAR"] == "1"
    assert cli.main(["--lite", "--modules", str(tmp_path)]) == 0  # SHELLCRAFT_PROFILE: same profile
    assert started[-1].config.profile == "work"
    os.environ.pop(profiles.ENV_VAR)
    assert cli.main(["--no-startup", "--modules", str(tmp_path)]) == 0
    assert started[-1].config.profile is None  # every start is the default
    assert cli.main(["--profile", "nope", "--modules", str(tmp_path)]) == 2


def test_every_starter_alias_is_usable():
    from shellcraft import aliases, profiles

    for name, starter in profiles.STARTERS.items():
        _, bad = aliases.parse_file(starter["aliases"])
        assert bad == [], (name, bad)
