"""Starter files in ~/.shellcraft: written once each, harmless as they come, `shellcraft --init`."""

import json

from shellcraft import aliases, funcs, skel, startup
from shellcraft.config import load_config

EXPECTED = {"rc", "aliases", "funcs/example.func.sample", "startup/README.txt",
            "startup/10-example.py.sample", "startup/30-watcher.py.sample"}


def test_the_starter_set():
    assert set(skel.files()) == EXPECTED


def test_seed_writes_each_file_once_and_a_deleted_one_stays_deleted(tmp_path):
    config = load_config()
    assert set(skel.seed(config, tmp_path)) == EXPECTED
    assert {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()} >= EXPECTED
    assert set(config["seeded"]) == EXPECTED
    (tmp_path / "rc").unlink()
    assert skel.seed(config, tmp_path) == []  # recorded: never written again
    assert not (tmp_path / "rc").exists()


def test_seed_keeps_your_own_files(tmp_path):
    (tmp_path / "aliases").write_text("alias ll='ls -l'\n")
    config = load_config()
    written = skel.seed(config, tmp_path)
    assert "aliases" not in written and "aliases" in config["seeded"]
    assert (tmp_path / "aliases").read_text() == "alias ll='ls -l'\n"


def test_the_starters_do_nothing_until_you_change_them(tmp_path, registry):
    from shellcraft.context import ShellContext

    skel.seed(load_config(), tmp_path)
    found, bad = aliases.parse_file((tmp_path / "aliases").read_text())
    assert found == {} and bad == []
    assert funcs.load_dir(tmp_path / "funcs", "default") == {}  # the example is a .sample
    assert startup.scripts(tmp_path) == []  # so are the startup examples
    ran, failed = startup.source(tmp_path / "rc", ShellContext(registry=registry, config={}))
    assert failed == 0


def test_the_examples_work_once_renamed(tmp_path):
    skel.seed(load_config(), tmp_path)
    (tmp_path / "funcs" / "example.func.sample").rename(tmp_path / "funcs" / "recon.func")
    recon = funcs.load_dir(tmp_path / "funcs", "default")["recon"]
    assert recon.command and len(recon.steps) == 4
    (tmp_path / "startup" / "10-example.py.sample").rename(tmp_path / "startup" / "10-example.py")
    assert [p.name for p in startup.scripts(tmp_path)] == ["10-example.py"]
    compile((tmp_path / "startup" / "30-watcher.py.sample").read_text(), "30-watcher.py", "exec")


def test_an_interactive_start_seeds_and_a_lite_one_does_not(tmp_path, monkeypatch, started):
    from shellcraft import cli

    home = tmp_path / "home"
    monkeypatch.setenv("SHELLCRAFT_HOME", str(home))
    cli.main(["--lite"])
    assert not (home / "rc").exists()
    cli.main(["--no-banner"])
    assert (home / "rc").is_file() and (home / "startup" / "README.txt").is_file()
    assert started.rc_runs == 1  # the commented rc is sourced like any rc: it does nothing


def test_init_creates_what_is_missing_and_force_refreshes_only_examples(tmp_path, monkeypatch, capsys):
    from shellcraft import cli

    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path))
    (tmp_path / "rc").write_text("theme nord\n")
    assert cli.main(["--init"]) == 0
    out = capsys.readouterr().out
    assert "kept" in out and "created" in out
    assert (tmp_path / "rc").read_text() == "theme nord\n"
    (tmp_path / "startup" / "README.txt").write_text("old")
    (tmp_path / "aliases").write_text("alias x='ls'\n")
    assert cli.main(["--init", "--force"]) == 0
    assert (tmp_path / "startup" / "README.txt").read_text() != "old"  # refreshed
    assert (tmp_path / "aliases").read_text() == "alias x='ls'\n"  # yours: never
    assert set(json.loads((tmp_path / "config.json").read_text())["seeded"]) == EXPECTED
