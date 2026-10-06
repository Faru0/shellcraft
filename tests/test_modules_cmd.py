"""`modules`: listing enabled / all modules, enable and disable, and what disabling changes."""

import json
import shutil

import pytest

from core.context import ShellContext, to_text
from core.loader import ModuleRegistry
from core.pipeline import PipelineError, run_line
from tests.conftest import MODULES_DIR


@pytest.fixture
def mods(tmp_path, monkeypatch):
    """A private copy of two modules, plus one whose import leaves a marker file."""
    folder = tmp_path / "mods"
    folder.mkdir()
    for name in ("filter", "myip"):
        for ext in (".py", ".md", ".skill"):
            shutil.copy(MODULES_DIR / f"{name}{ext}", folder / f"{name}{ext}")
    marker = tmp_path / "imported.txt"
    (folder / "noisy.py").write_text(
        f'"""Leaves a marker when imported."""\nopen({str(marker)!r}, "a").write("x")\n'
        "def run(args, stdin):\n    return 'noisy'\n")
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    return folder, marker


def make_ctx(folder, disabled=()):
    registry = ModuleRegistry(folder, disabled)
    registry.load()
    return ShellContext(registry=registry, config={})


def text(ctx, line):
    return to_text(run_line(line, ctx).output)


def test_default_lists_enabled_and_names_the_disabled(mods):
    ctx = make_ctx(mods[0], ["myip"])
    listing = text(ctx, "modules")
    assert "filter" in listing and "noisy" in listing
    assert not any(line.startswith("myip") for line in listing.splitlines())
    assert "1 disabled: myip" in listing


def test_all_lists_every_module_with_its_state(mods):
    ctx = make_ctx(mods[0], ["myip"])
    for flag in ("-a", "--all", "all"):
        listing = text(ctx, f"modules {flag}").splitlines()
        assert any(line.startswith("off") and "myip" in line and "public IP" in line for line in listing)
        assert any(line.startswith("on") and "filter" in line for line in listing)


def test_disabled_module_is_never_imported(mods):
    folder, marker = mods
    ctx = make_ctx(folder, ["noisy"])
    assert not marker.exists()
    assert "Leaves a marker" not in text(ctx, "modules -a")  # summary from .md/.skill only: it has none
    ctx.registry.load()  # a reload keeps it off
    assert not marker.exists() and ctx.registry.get("noisy") is None


def test_disable_then_enable_saves_the_choice(mods, tmp_path):
    ctx = make_ctx(mods[0])
    assert "disabled myip, noisy" in text(ctx, "modules disable myip noisy")
    with pytest.raises(PipelineError) as info:
        run_line("noisy", ctx)
    assert "module 'noisy' is disabled — run: modules enable noisy" in info.value.message
    assert info.value.status == 127
    saved = json.loads((tmp_path / "home" / "config.json").read_text())
    assert saved["disabled_modules"] == ["myip", "noisy"]

    assert "enabled noisy" in text(ctx, "modules enable noisy")
    assert text(ctx, "noisy") == "noisy"
    saved = json.loads((tmp_path / "home" / "config.json").read_text())
    assert saved["disabled_modules"] == ["myip"]


def test_repeats_are_notes_not_errors(mods):
    ctx = make_ctx(mods[0], ["myip"])
    assert "myip is already disabled" in text(ctx, "modules disable myip")
    assert "filter is already enabled" in text(ctx, "modules enable filter")


@pytest.mark.parametrize("line, message", [
    ("modules disable grep", "grep is a builtin, not a module"),
    ("modules disable filtr", "did you mean 'filter'"),
    ("modules enable", "usage: modules enable NAME"),
    ("modules bogus", "usage: modules"),
])
def test_errors(mods, line, message):
    ctx = make_ctx(mods[0])
    with pytest.raises(PipelineError) as info:
        run_line(line, ctx)
    assert message in info.value.message


def test_enable_reports_a_module_that_fails_to_load(mods):
    folder, _ = mods
    (folder / "broken.py").write_text("raise RuntimeError('boom')\n")
    ctx = make_ctx(folder, ["broken"])
    out = text(ctx, "modules enable broken")
    assert "enabled broken" in out and "failed to load" in out and "boom" in out
    assert "⚠ error" in ctx.registry.warnings[0] or "boom" in ctx.registry.warnings[0]


def test_mcp_context_can_list_but_not_toggle(mods):
    ctx = make_ctx(mods[0])
    ctx.allow_stateful = False
    assert "filter" in text(ctx, "modules")
    with pytest.raises(PipelineError) as info:
        run_line("modules disable filter", ctx)
    assert "not available in this context" in info.value.message
    assert ctx.registry.get("filter") is not None


def test_man_and_which_know_disabled_modules(mods):
    ctx = make_ctx(mods[0], ["myip"])
    with pytest.raises(PipelineError) as info:
        run_line("man -p myip", ctx)
    assert "disabled (modules enable myip)" in info.value.message
    assert "ShellCraft module, disabled" in text(ctx, "which myip")


def test_startup_reads_disabled_modules_from_config(mods, tmp_path, started):
    from core import cli

    home = tmp_path / "home"
    home.mkdir()
    (home / "config.json").write_text(json.dumps({"disabled_modules": ["myip"]}))
    cli.main(["--modules", str(mods[0]), "--no-banner"])
    registry = started[0].registry
    assert registry.get("myip") is None and registry.is_disabled("myip") and registry.get("filter")


def test_mcp_server_hides_disabled_modules(mods):
    import anyio
    from mcp.client import Client

    from core.mcp_server import build_server

    registry = ModuleRegistry(mods[0], ["myip"])
    registry.load()

    async def main():
        async with Client(build_server(registry)) as client:
            return [t.name for t in (await client.list_tools()).tools]
    names = anyio.run(main)
    assert "filter" in names and "myip" not in names


def test_completion_offers_subcommands_and_the_right_modules(mods):
    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document

    from core.completer import ShellCompleter

    completer = ShellCompleter(make_ctx(mods[0], ["myip"]))

    def complete(line):
        return [c.text for c in completer.get_completions(Document(line, len(line)), CompleteEvent())]
    assert complete("modules ") == ["-a", "disable", "enable"]
    assert complete("modules enable ") == ["myip"]
    assert complete("modules disable ") == ["filter", "noisy"]
    assert complete("modules disable filter ") == ["noisy"]
    assert complete("for -py x in a { if gr") == ["grep"]
