"""The user modules folder (~/.shellcraft/modules) next to the main one: shadowing, origin,
helpers across folders, hot reload of both, `ingest` writing there, the CLI wiring."""

from pathlib import Path

from shellcraft.context import ShellContext, to_text
from shellcraft.loader import ModuleRegistry
from shellcraft.pipeline import run_line
from shellcraft.watch import ModuleWatcher

RUN = "def run(args, stdin):\n    return {!r}\n"


def _folders(tmp_path):
    main, user = tmp_path / "main", tmp_path / "user"
    main.mkdir()
    user.mkdir()
    (main / "shared.py").write_text(RUN.format("main"))
    (main / "only_main.py").write_text(RUN.format("only main"))
    (user / "shared.py").write_text(RUN.format("user"))
    (user / "mine.py").write_text(RUN.format("mine"))
    return main, user


def test_user_modules_shadow_the_main_ones_and_say_where_they_come_from(tmp_path):
    main, user = _folders(tmp_path)
    for lazy in (False, True):
        reg = ModuleRegistry(main, lazy=lazy, user_directory=user)
        reg.load()
        assert reg.names() == ["mine", "only_main", "shared"] and reg.warnings == []
        assert reg.get("shared").run([], "") == "user" and reg.get("only_main").run([], "") == "only main"
        assert reg.origin("shared") == "user" and reg.origin("mine") == "user" and reg.origin("only_main") == "bundled"
        assert reg.directories == [main, user]
    # the same folder twice is one folder; a missing user folder is fine and silent
    same = ModuleRegistry(main, user_directory=main)
    assert same.directories == [main]
    missing = ModuleRegistry(main, user_directory=tmp_path / "nope")
    missing.load()
    assert missing.names() == ["only_main", "shared"] and missing.warnings == []


def test_helpers_import_across_folders_and_the_user_one_wins(tmp_path):
    main, user = _folders(tmp_path)
    (main / "_util.py").write_text("WHO = 'main util'\n")
    (user / "_util.py").write_text("WHO = 'user util'\n")
    (main / "_only_main.py").write_text("WHO = 'main helper'\n")
    (user / "uses.py").write_text("import _util\nimport _only_main\n\n"
                                  "def run(args, stdin):\n    return _util.WHO + ', ' + _only_main.WHO\n")
    reg = ModuleRegistry(main, user_directory=user)
    reg.load()
    assert reg.get("uses").run([], "") == "user util, main helper"
    (user / "script.py").write_text("import _only_main\nprint(_only_main.WHO)\n")  # a plain script, too
    reg.load()
    assert reg.get("script").run([], "") == "main helper\n"


def test_hot_reload_watches_both_folders(tmp_path):
    main, user = _folders(tmp_path)
    reg = ModuleRegistry(main, user_directory=user)
    reg.load()
    watcher = ModuleWatcher(*reg.directories)
    (user / "shared.py").unlink()  # the user's copy goes: the main one is visible again
    (user / "fresh.py").write_text(RUN.format("fresh"))
    changed = watcher.changes()
    assert changed == ["fresh.py", "shared.py"]
    reg.reload(changed)
    assert reg.get("shared").run([], "") == "main" and reg.origin("shared") == "bundled"
    assert reg.get("fresh").run([], "") == "fresh" and reg.origin("fresh") == "user"
    late = tmp_path / "late"  # a user folder created after the watcher started
    w2 = ModuleWatcher(main, late)
    late.mkdir()
    (late / "new.py").write_text(RUN.format("new"))
    assert w2.changes() == ["new.py"]


def test_modules_and_help_show_the_origin_and_ingest_writes_to_the_user_folder(tmp_path, monkeypatch, capsys):
    main, user = _folders(tmp_path)
    reg = ModuleRegistry(main, user_directory=user)
    reg.load()
    ctx = ShellContext(registry=reg)
    listing = to_text(run_line("modules", ctx).output)
    assert "mine  user" in listing.splitlines() and "only_main  bundled" in listing.splitlines()
    help_text = to_text(run_line("help", ctx).output)
    assert "(user module)" in next(line for line in help_text.splitlines() if line.startswith("mine"))
    import tools.ingest.__main__ as ingest

    seen = {}
    monkeypatch.setattr(ingest, "main", lambda args, modules_dir, prog, console: seen.update(dir=modules_dir) or 0)
    ctx.interactive = True
    from rich.console import Console

    from shellcraft.themes import UI, all_themes

    ctx.ui = UI(all_themes({})["nord"], Console(file=__import__("io").StringIO()))
    (user / "sub").mkdir()
    import shutil

    shutil.rmtree(user)  # not created yet: ingest creates it
    run_line("ingest check --all", ctx)
    assert seen["dir"] == user and user.is_dir()


def test_the_cli_loads_the_user_folder(tmp_path, monkeypatch, started):
    from shellcraft.cli import main

    home = tmp_path / "home"
    (home / "modules").mkdir(parents=True)
    (home / "modules" / "hello.py").write_text(RUN.format("hi"))
    monkeypatch.setenv("SHELLCRAFT_HOME", str(home))
    main(["--modules", str(tmp_path)])
    registry = started[-1].registry
    assert registry.user_directory == Path(home / "modules") and "hello" in registry.names()
