import pytest
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from core.completer import ShellCompleter
from core.context import ShellContext
from core.loader import SkillInfo
from core.options import OptionSpec, builtin_options, from_markdown, from_skill, merge
from tests.conftest import ROOT

MYIP_SKILL = SkillInfo.parse((ROOT / "modules/myip.skill").read_text())
MYIP_MD = (ROOT / "modules/myip.md").read_text()


def by_flag(options, flag):
    return next(o for o in options if flag in o.flags)


def test_from_skill_parses_flags_metavars_and_values():
    opts = from_skill(MYIP_SKILL)
    assert [o.flags for o in opts] == [("-i", "--info"), ("-j", "--json"), ("-f", "--field"),
                                       ("-p", "--port"), ("--timeout",)]
    field = by_flag(opts, "-f")
    assert field.metavar == "FIELD" and "country_iso" in field.values
    assert by_flag(opts, "--timeout").metavar == "SECONDS"
    assert by_flag(opts, "-i").metavar is None


def test_from_markdown_reads_option_tables():
    opts = from_markdown(MYIP_MD)
    assert {o.flags for o in opts} == {("-i", "--info"), ("-j", "--json"), ("-f", "--field"),
                                       ("-p", "--port"), ("--timeout",)}
    assert by_flag(opts, "-j").help.startswith("Raw JSON record")
    assert "`" not in by_flag(opts, "-i").help and "*" not in by_flag(opts, "-p").help


def test_markdown_escaped_pipe_becomes_values():
    md = "| Option | Meaning |\n| --- | --- |\n| `-type f\\|d\\|l` | Only files, dirs or links. |\n"
    [opt] = from_markdown(md)
    assert opt.flags == ("-type",) and opt.values == ("f", "d", "l")


def test_merge_prefers_markdown_help_and_skill_values():
    merged = merge(from_skill(MYIP_SKILL), from_markdown(MYIP_MD))
    field = by_flag(merged, "--field")
    assert field.help == "Print one field only. Several IPs print as ip<TAB>value."
    assert "asn_org" in field.values and field.metavar == "FIELD"
    only_md = merge([], [OptionSpec(("-x",), None, "x")])
    assert only_md[0].flags == ("-x",)


def test_builtin_options_come_from_docs():
    assert [o.short for o in builtin_options("grep")] == ["-i", "-v", "-c", "-n", "-F", "-l", "-m"]
    assert "-1" in {o.short for o in builtin_options("ls")}
    assert builtin_options("pwd") == ()


def test_module_spec_options_are_cached(registry):
    spec = registry.get("myip")
    assert spec.options is spec.options


@pytest.fixture
def complete(registry):
    completer = ShellCompleter(ShellContext(registry=registry))

    def run(text):
        return [c.text for c in completer.get_completions(Document(text), CompleteEvent())]
    return run


@pytest.mark.parametrize("text, expected", [
    ("myip -", ["-i", "-j", "-f", "-p", "--timeout"]),
    ("myip --t", ["--timeout"]),
    ("myip --", ["--info", "--json", "--field", "--port", "--timeout"]),
    ("myip -i -", ["-j", "-f", "-p", "--timeout"]),
    ("myip -f co", ["coordinates", "country", "country_eu", "country_iso"]),
    ("echo x | myip --i", ["--info"]),
    ("grep -", ["-i", "-v", "-c", "-n", "-F", "-l", "-m"]),
    ("find . -type ", ["d", "f", "l"]),
    ("head -n ", []),
])
def test_switch_completion(complete, text, expected):
    assert complete(text) == expected


def test_paths_still_complete(complete, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "notes.txt").write_text("")
    assert complete("cat no") == ["tes.txt"]
    assert complete("myip -i no") == ["tes.txt"]
    assert complete("unknowncmd no") == ["tes.txt"]
    assert complete("myip -i > no") == ["tes.txt"]
