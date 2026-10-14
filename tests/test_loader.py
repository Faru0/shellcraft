import pytest

from shellcraft.loader import ModuleRegistry, SkillInfo


def test_sample_modules_have_all_three_files(registry):
    assert {"fetch", "filter"} <= set(registry.names())
    for name in ("fetch", "filter"):
        spec = registry.get(name)
        assert spec.doc_md and spec.skill and spec.skill.parsed


def test_broken_modules_are_skipped(tmp_path):
    (tmp_path / "good.py").write_text("def run(args, stdin):\n    return stdin.upper()\n")
    (tmp_path / "syntax.py").write_text("def run(:\n")
    (tmp_path / "_private.py").write_text("raise SystemExit\n")  # _name.py: a helper, never a command
    reg = ModuleRegistry(tmp_path)
    reg.load()
    assert reg.names() == ["good"]
    assert len(reg.warnings) == 1 and "syntax.py" in reg.warnings[0]
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
    """`_name.py` is a helper: never a command, importable as `import _name` or `from . import _name`."""
    from shellcraft.loader import ModuleRegistry

    (tmp_path / "_localthing.py").write_text("VALUE = 'local'\n\ndef helper():\n    return VALUE\n")
    (tmp_path / "uses.py").write_text("import _localthing\nfrom . import _localthing as rel\n\n"
                                      "def run(args, stdin):\n    return _localthing.helper() + '+' + rel.VALUE + '\\n'\n")
    registry = ModuleRegistry(tmp_path)
    registry.load()
    assert registry.names() == ["uses"] and registry.warnings == []
    assert registry.get("uses").run([], "") == "local+local\n"
    lazy = ModuleRegistry(tmp_path, lazy=True)
    lazy.load()
    assert lazy.get("uses").run([], "") == "local+local\n"
    (tmp_path / "_localthing.py").write_text("VALUE = 'edited'\n\ndef helper():\n    return VALUE\n")
    lazy.reload(["_localthing.py"])
    assert lazy.names() == ["uses"] and lazy.get("uses").run([], "") == "edited+edited\n"


def test_every_py_is_a_command_skill_makes_a_tool_md_makes_a_man_page(tmp_path):
    """A .py without run() is a plain script (run as `python FILE ARGS`, stdin piped, never
    imported); only a module with a .skill is an MCP tool; only one with a .md has a man page."""
    from shellcraft.loader import ModuleRegistry

    (tmp_path / "_util.py").write_text("def shout(s):\n    return s.upper()\n")
    (tmp_path / "plain.py").write_text('"""Shout the arguments and stdin."""\nimport sys\nimport _util\n'
                                       'print(_util.shout(" ".join(sys.argv[1:]) + sys.stdin.read()))\n')
    (tmp_path / "fails.py").write_text("import sys\nsys.exit('no good')\n")
    (tmp_path / "tool.py").write_text("def run(args, stdin):\n    return 'tool'\n")
    (tmp_path / "tool.skill").write_text('summary = "a tool"\n')
    (tmp_path / "tool.md").write_text("# tool\n\nA tool.\n")
    for lazy in (False, True):
        registry = ModuleRegistry(tmp_path, lazy=lazy)
        registry.load()
        assert registry.names() == ["fails", "plain", "tool"] and registry.warnings == []
        plain = registry.get("plain")
        assert plain.run(["hi"], " there") == "HI THERE\n"
        assert plain.summary == "Shout the arguments and stdin."
        assert not plain.exposed and plain.doc_md is None  # no .skill: no MCP tool; no .md: no man page
        assert registry.get("tool").exposed and registry.get("tool").doc_md
        with pytest.raises(Exception, match="no good"):
            registry.get("fails").run([], "")



def test_help_flags_module_files_without_run(tmp_path):
    from shellcraft.builtins import SCRIPT_HINT
    from shellcraft.context import ShellContext, to_text
    from shellcraft.loader import ModuleRegistry
    from shellcraft.pipeline import run_line

    (tmp_path / "plain.py").write_text('"""Print hi."""\nprint("hi")\n')
    (tmp_path / "proper.py").write_text('"""Proper."""\ndef run(args, stdin):\n    return "ok"\n')
    registry = ModuleRegistry(tmp_path)
    registry.load()
    text = to_text(run_line("help", ShellContext(registry=registry)).output)
    plain = next(line for line in text.splitlines() if line.startswith("plain"))
    proper = next(line for line in text.splitlines() if line.startswith("proper"))
    assert "Print hi." in plain and SCRIPT_HINT in plain and SCRIPT_HINT not in proper


def test_modules_lists_helpers_with_description_and_users(tmp_path):
    from shellcraft.context import ShellContext, to_text
    from shellcraft.loader import ModuleRegistry, helpers
    from shellcraft.pipeline import run_line

    (tmp_path / "_net.py").write_text('"""Shared HTTP helpers."""\n')
    (tmp_path / "_fmt.py").write_text("X = 1\n")
    (tmp_path / "_fmt.md").write_text("# _fmt\n\nTable formatting for the query modules.\n")
    (tmp_path / "_lonely.py").write_text("Y = 2\n")
    (tmp_path / "query.py").write_text("import _net\nfrom . import _fmt\ndef run(args, stdin):\n    return ''\n")
    (tmp_path / "other.py").write_text("from _net import get\ndef run(args, stdin):\n    return ''\n")
    found = {name: (desc, used) for name, _p, desc, used in helpers(tmp_path)}
    assert found == {"_fmt": ("Table formatting for the query modules.", ["query"]),
                     "_lonely": ("", []),
                     "_net": ("Shared HTTP helpers.", ["other", "query"])}
    registry = ModuleRegistry(tmp_path)
    registry.load()
    text = to_text(run_line("modules", ShellContext(registry=registry)).output)
    assert "helper  _net  Shared HTTP helpers.  (used by other, query)" in text
    assert "helper  _fmt  Table formatting for the query modules.  (used by query)" in text
    assert "helper  _lonely" in text and "_net" not in registry.names()
