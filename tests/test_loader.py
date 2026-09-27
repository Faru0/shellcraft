from core.loader import ModuleRegistry, SkillInfo


def test_sample_modules_have_all_three_files(registry):
    assert {"fetch", "filter"} <= set(registry.names())
    for name in ("fetch", "filter"):
        spec = registry.get(name)
        assert spec.doc_md and spec.skill and spec.skill.parsed


def test_broken_modules_are_skipped(tmp_path):
    (tmp_path / "good.py").write_text("def run(args, stdin):\n    return stdin.upper()\n")
    (tmp_path / "syntax.py").write_text("def run(:\n")
    (tmp_path / "norun.py").write_text("x = 1\n")
    (tmp_path / "_private.py").write_text("raise SystemExit\n")
    reg = ModuleRegistry(tmp_path)
    reg.load()
    assert reg.names() == ["good"]
    assert len(reg.warnings) == 2
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
