import textwrap
from pathlib import Path

import pytest

from core.loader import SkillInfo
from tests.conftest import ROOT
from tools.modtest import FAIL, PASS, WARN, main, source_flags, test_module as check_module
from tools.mkprompt import build_prompt

GOOD_SKILL = textwrap.dedent('''\
    summary = "Uppercase text."
    when_to_use = "When text must be uppercase."
    usage = "NAME"
    examples = ['stdin="a" -> "A"']

    [[tests]]
    stdin = "ab"
    expect = "AB"
''')


def make(tmp_path: Path, name: str, py: str, skill: str | None = GOOD_SKILL, md: str | None = None) -> Path:
    path = tmp_path / f"{name}.py"
    path.write_text(textwrap.dedent(py))
    if skill is not None:
        (tmp_path / f"{name}.skill").write_text(skill.replace("NAME", name))
    (tmp_path / f"{name}.md").write_text(md or f"# {name}\n\nUppercase.\n\n## Synopsis\n\n```\n{name}\n```\n\n"
                                               "## Examples\n\n```\necho a | x\n```\n")
    return path


def statuses(report, name_part: str) -> set[str]:
    return {c.status for c in report.checks if name_part in c.name}


GOOD_PY = '''\
    """Uppercase stdin."""
    from core.modkit import ArgParser
    def run(args, stdin):
        ArgParser("m").parse_args(args)
        return stdin.upper()
'''


@pytest.mark.parametrize("py", sorted((ROOT / "modules").glob("*.py")) + [ROOT / "templates/module/template.py"],
                         ids=lambda p: p.stem)
def test_bundled_modules_and_template_pass(py):
    report = check_module(py)
    assert report.count(FAIL) == 0, [c for c in report.checks if c.status == FAIL]
    assert report.count(WARN) == 0, [c for c in report.checks if c.status == WARN]


def test_good_fixture_passes(tmp_path):
    report = check_module(make(tmp_path, "upper", GOOD_PY))
    assert report.count(FAIL) == 0
    assert statuses(report, "test #1") == {PASS}


def test_printing_module_fails(tmp_path):
    py = make(tmp_path, "noisy", '''\
        def run(args, stdin):
            print("hello")
            return stdin.upper()
    ''')
    assert FAIL in statuses(check_module(py), 'run([], "")')


def test_plain_argparse_sys_exit_fails(tmp_path):
    py = make(tmp_path, "exity", '''\
        import argparse
        def run(args, stdin):
            argparse.ArgumentParser().parse_args(args)
            return stdin.upper()
    ''')
    report = check_module(py)
    assert FAIL in statuses(report, "unknown option")
    assert any("sys.exit" in c.detail for c in report.checks)


def test_builtin_name_clash_fails(tmp_path):
    report = check_module(make(tmp_path, "ls", GOOD_PY))
    assert statuses(report, "name") == {FAIL}


def test_bad_toml_fails(tmp_path):
    report = check_module(make(tmp_path, "badtoml", GOOD_PY, skill="summary = 'unterminated\n"))
    assert statuses(report, ".skill TOML") == {FAIL}


def test_misplaced_keys_after_args_table_fail(tmp_path):
    skill = GOOD_SKILL.replace('examples = [\'stdin="a" -> "A"\']\n', "") + textwrap.dedent('''
        [[args]]
        name = "X"
        description = "x"
        examples = ["lost"]
    ''')
    report = check_module(make(tmp_path, "misplaced", GOOD_PY, skill=skill))
    assert any(c.status == FAIL and "ended up inside" in c.detail for c in report.checks)


def test_failing_test_case_shows_diff(tmp_path):
    report = check_module(make(tmp_path, "wrong", GOOD_PY, skill=GOOD_SKILL.replace('"AB"', '"ab"')))
    failed = [c for c in report.checks if c.name.startswith("test #1")]
    assert failed[0].status == FAIL and "-ab" in failed[0].detail and "+AB" in failed[0].detail


def test_undocumented_flag_warns(tmp_path):
    py = make(tmp_path, "flaggy", GOOD_PY.replace('ArgParser("m")', 'p = ArgParser("m"); p.add_argument("--loud"); p'))
    report = check_module(py)
    assert any(c.status == WARN and "--loud" in c.detail for c in report.checks)


def test_source_flags():
    src = 'p.add_argument("-n", "--top", type=int, help="-x")\np.add_argument("files", nargs="*")'
    assert source_flags(src) == [["-n", "--top"]]


def test_tests_key_stays_out_of_mcp_description():
    skill = SkillInfo.parse(GOOD_SKILL)
    assert skill.tests == [{"stdin": "ab", "expect": "AB"}]
    assert "expect" not in skill.to_description() and "tests" not in skill.extra


def test_cli_exit_codes(tmp_path, capsys):
    assert main([str(ROOT / "templates/module/template.py")]) == 0
    assert main([str(make(tmp_path, "ls", GOOD_PY))]) == 1


def test_mkprompt_fills_every_placeholder():
    prompt = build_prompt(ROOT / "modules/myip.py", include_existing=True)
    assert "{{" not in prompt
    assert "def run(args: list[str], stdin: str)" in prompt  # module source embedded
    assert "`myip.md`" in prompt and "Current `myip.skill`" in prompt
    assert "Count the most frequent words" in prompt  # worked example embedded


def test_values_key_is_allowed_and_validated(tmp_path):
    skill = GOOD_SKILL.replace("[[tests]]", '[[args]]\nname = "--mode M"\ndescription = "m"\nvalues = ["a", "b"]\n\n[[tests]]')
    assert check_module(make(tmp_path, "valued", GOOD_PY, skill=skill)).count(FAIL) == 0
    bad = skill.replace('values = ["a", "b"]', "values = 3")
    report = check_module(make(tmp_path, "badvalues", GOOD_PY, skill=bad))
    assert any(c.status == FAIL and "`values` must be" in c.detail for c in report.checks)


def test_values_appear_in_mcp_description():
    skill = SkillInfo.parse('[[args]]\nname = "--mode M"\ndescription = "Mode."\nvalues = ["a", "b"]\n')
    assert "--mode M: Mode. (values: a, b)" in skill.to_description()
