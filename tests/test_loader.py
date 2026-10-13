from core.loader import ModuleRegistry, SkillInfo


def test_sample_modules_have_all_three_files(registry):
    assert {"fetch", "filter"} <= set(registry.names())
    for name in ("fetch", "filter"):
        spec = registry.get(name)
        assert spec.doc_md and spec.skill and spec.skill.parsed


def test_broken_modules_are_skipped(tmp_path):
    (tmp_path / "good.py").write_text("def run(args, stdin):\n    return stdin.upper()\n")
    (tmp_path / "syntax.py").write_text("def run(:\n")
    (tmp_path / "norun.py").write_text("x = 1\n")  # no run(), no .skill: a helper, skipped quietly
    (tmp_path / "_private.py").write_text("raise SystemExit\n")
    (tmp_path / "norun2.py").write_text("x = 1\n")
    (tmp_path / "norun2.skill").write_text('summary = "meant to be a command"\n')  # then the missing run() is reported
    reg = ModuleRegistry(tmp_path)
    reg.load()
    assert reg.names() == ["good"]
    assert len(reg.warnings) == 2 and any("norun2" in w and "run" in w for w in reg.warnings)
    assert reg.get("good").run([], "hi") == "HI"


def test_skill_description():
    skill = SkillInfo.parse(
        'summary = "Do X"\nwhen_to_use = "When Y"\nexamples = ["x -a"]\n'
        '[[args]]\nname = "-a"\ndescription = "All"\n'
    )
    text = skill.to_description()
    assert text.startswith("Do X")
    assert "When to use:\nWhen Y" in text and "-a: All" in text and "x -a" in text


def test_freeform_skill_passthrough():
    skill = SkillInfo.parse("Just call it with a pattern.\nIt's great: really = yes [")
    assert not skill.parsed
    assert skill.to_description().startswith("Just call it")


def test_summary_fallbacks(tmp_path):
    (tmp_path / "doc.py").write_text('"""Uppercase things."""\ndef run(args, stdin):\n    return stdin\n')
    reg = ModuleRegistry(tmp_path)
    reg.load()
    assert reg.get("doc").summary == "Uppercase things."
    assert reg.get("doc").description == "Uppercase things."


def test_modules_import_helpers_from_the_folder(tmp_path):
    """`import localthing` and `from . import localthing` both work; a file without run() and
    without a .skill is a helper, not a command, and no warning is shown."""
    from core.loader import ModuleRegistry

    (tmp_path / "localthing.py").write_text("VALUE = 'local'\n\ndef helper():\n    return VALUE\n")
    (tmp_path / "uses.py").write_text("import localthing\nfrom . import localthing as rel\n\n"
                                      "def run(args, stdin):\n    return localthing.helper() + '+' + rel.VALUE + '\\n'\n")
    registry = ModuleRegistry(tmp_path)
    registry.load()
    assert registry.names() == ["uses"] and registry.warnings == []
    assert registry.get("uses").run([], "") == "local+local\n"
    # lazy registries skip helpers too, and a helper edit reaches the module through reload
    lazy = ModuleRegistry(tmp_path, lazy=True)
    lazy.load()
    assert lazy.names() == ["uses"] and lazy.get("uses").run([], "") == "local+local\n"
    (tmp_path / "localthing.py").write_text("VALUE = 'edited'\n\ndef helper():\n    return VALUE\n")
    lazy.reload(["localthing.py"])
    assert lazy.names() == ["uses"] and lazy.get("uses").run([], "") == "edited+edited\n"
    # a helper with a .skill beside it is a command after all, and reports its missing run()
    (tmp_path / "localthing.skill").write_text('summary = "x"\n')
    registry.load()
    assert "localthing" in registry.names() or any("localthing" in w for w in registry.warnings)
