import io
import sys
import textwrap
import threading
import tomllib
from pathlib import Path

import pytest

from core import params as params_mod
from core.modkit import ModuleError, script
from tests.conftest import ROOT
from tools.ingest import ai, docs
from tools.ingest.__main__ import main as ingest_main
from tools.ingest.analyze import GUARD, KEEP, MOVE, analyze
from tools.ingest.check import FAIL, PASS, WARN, check_module, source_flags
from tools.ingest.convert import ConvertError, convert, extra_changes


def write(tmp_path: Path, name: str, code: str) -> Path:
    path = tmp_path / f"{name}.py"
    path.write_text(textwrap.dedent(code), encoding="utf-8")
    return path


def load(path: Path):
    from core.loader import ModuleRegistry
    return ModuleRegistry(path.parent).load_file(path.stem, path)


FLAT = '''\
    """Shout the words you give it."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Uppercase words.")
    parser.add_argument("words", nargs="*", help="words to shout")
    parser.add_argument("-n", "--times", type=int, default=1, help="repeat N times")
    args = parser.parse_args()

    # do the work
    text = " ".join(args.words) or sys.stdin.read()
    if not text:
        sys.exit("nothing to shout")
    for _ in range(args.times):
        print(text.upper())
'''

WITH_MAIN = '''\
    """Count lines."""
    import argparse
    import sys

    DOC = """multi
    line"""


    def count(text):
        return len(text.splitlines())


    def main():
        p = argparse.ArgumentParser()
        p.add_argument("-v", "--verbose", action="store_true")
        opts = p.parse_args()
        print(count("a\\nb\\n"), "lines" if opts.verbose else "")
        return 0


    if __name__ == "__main__":
        # entry
        sys.exit(main())
'''


# ── @script ─────────────────────────────────────────────────────────────────

def test_script_returns_prints_and_maps_exits():
    @script("t")
    def run(args, stdin):
        import argparse
        p = argparse.ArgumentParser()
        p.add_argument("-n", type=int, default=1)
        opts = p.parse_args(args)
        print("x" * opts.n)
        if opts.n == 3:
            sys.exit(0)
        if opts.n == 4:
            sys.exit("too many")
        return "tail\n"

    assert run(["-n", "2"], "") == "xx\ntail\n"
    assert run(["-n", "3"], "") == "xxx\n"  # exit 0 = normal end
    with pytest.raises(ModuleError, match="^t: too many$"):
        run(["-n", "4"], "")
    with pytest.raises(ModuleError, match="^t: unrecognized arguments: --bad$"):
        run(["--bad"], "")


def test_script_capture_is_per_thread():
    barrier = threading.Barrier(2)

    @script("t")
    def run(args, stdin):
        for _ in range(50):
            print(args[0])
            barrier.wait() if _ == 0 else None
        return None

    results = {}
    threads = [threading.Thread(target=lambda w=w: results.__setitem__(w, run([w], ""))) for w in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == {"a": "a\n" * 50, "b": "b\n" * 50}


def test_script_leaves_other_threads_output_alone(capsys):
    started, release = threading.Event(), threading.Event()

    @script("t")
    def run(args, stdin):
        started.set()
        release.wait(5)
        print("inside")

    worker = threading.Thread(target=lambda: run([], ""))
    worker.start()
    started.wait(5)
    print("outside")  # main thread, while the @script call is running
    release.set()
    worker.join()
    assert capsys.readouterr().out == "outside\n"


# ── analysis ────────────────────────────────────────────────────────────────

def test_analysis_of_flat_script(tmp_path):
    a = analyze(write(tmp_path, "shout", FLAT))
    assert a.style == "script" and not a.conflicts
    assert [what for _, what in a.plan] == [KEEP, KEEP, KEEP] + [MOVE] * 7
    assert [(o.flags, o.dest, o.kind) for o in a.options] == [([], "words", "array"),
                                                              (["-n", "--times"], "times", "integer")]
    assert a.uses_stdin and a.errors == ["nothing to shout"]
    assert {e.new for e in a.edits} == {"(args)", "stdin"}


def test_constants_and_definitions_stay_put(tmp_path):
    a = analyze(write(tmp_path, "m", WITH_MAIN))
    kinds = {type(n).__name__ + getattr(n, "name", ""): what for n, what in a.plan}
    assert kinds["Assign"] == KEEP and kinds["FunctionDefmain"] == KEEP and kinds["If"] == GUARD


def test_already_a_module(tmp_path):
    a = analyze(write(tmp_path, "m", "def run(args, stdin):\n    return stdin\n"))
    assert a.style == "module" and not a.conflicts and a.uses_stdin
    assert convert(a, "m").source == a.source


@pytest.mark.parametrize("code, message", [
    ("name = input('Name? ')\nprint(name)\n", "input() waits"),
    ("import sys\ndef f():\n    return sys.argv[1]\nprint(f())\n", "sys.argv is used inside f()"),
    ("import sys\ndef f():\n    return sys.stdin.read()\nprint(f())\n", "sys.stdin is used inside f()"),
    ("import sys\ndef bump():\n    global total\n    total += 1\ntotal = len(sys.argv)\nbump()\n", "`total` is set here"),
    ("from subprocess import run\nrun(['ls'])\n", "the name `run` is already used"),
    ("def run(x):\n    return x\n", "different signature"),
    ("import os\nos._exit(1)\n", "os._exit()"),
    ("import tkinter\ntkinter.Tk()\n", "`tkinter`"),
    ("while True:\n    print(1)\n", "never finishes"),
    ("def helper():\n    return 1\n", "never runs anything"),
    ("import sys\nprint(sys.argv)\n", "can't be rewritten automatically"),
    ("if __name__ == '__main__' and True:\n    print(1)\n", "unusual way"),
])
def test_conflicts(tmp_path, code, message):
    a = analyze(write(tmp_path, "m", code))
    assert any(message in c.message for c in a.conflicts), [c.message for c in a.conflicts]
    with pytest.raises(ConvertError):
        convert(a, "m")


def test_function_reading_moved_global_is_a_conflict(tmp_path):
    code = "import argparse\np = argparse.ArgumentParser()\nargs = p.parse_args()\n" \
           "def show():\n    print(args)\nshow()\n"
    a = analyze(write(tmp_path, "m", code))
    assert any("`args` is set here" in c.message for c in a.conflicts)


# ── conversion ──────────────────────────────────────────────────────────────

def test_flat_script_converts_and_runs(tmp_path):
    a = analyze(write(tmp_path, "orig", FLAT))
    c = convert(a, "shout")
    added = [ln[1:] for ln in c.diff(a.source, "shout").splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    assert "from core.modkit import script" in added
    assert '@script("shout")' in added and "def run(args: list[str], stdin: str) -> str:" in added
    assert "    args = parser.parse_args(args)" in added
    assert "    text = \" \".join(args.words) or stdin" in added
    assert extra_changes(a.source, c.source, c.source) == []

    module = write(tmp_path, "shout", c.source)
    run = load(module).run
    assert run(["-n", "2", "hi"], "") == "HI\nHI\n"
    assert run([], "piped") == "PIPED\n"
    assert run([], "piped") == "PIPED\n"  # parser is rebuilt each call
    with pytest.raises(ModuleError, match="shout: nothing to shout"):
        run([], "")


def test_main_guard_conversion_is_minimal(tmp_path):
    a = analyze(write(tmp_path, "orig", WITH_MAIN))
    c = convert(a, "lines")
    diff = c.diff(a.source, "lines").splitlines()
    removed = [ln[1:] for ln in diff if ln.startswith("-") and not ln.startswith("---")]
    added = [ln[1:] for ln in diff if ln.startswith("+") and not ln.startswith("+++")]
    assert removed == ["def main():", "    opts = p.parse_args()", 'if __name__ == "__main__":',
                       "    sys.exit(main())"]
    assert added == ["from core.modkit import script", "def main(argv=None):", "    opts = p.parse_args(argv)",
                     '@script("lines")', "def run(args: list[str], stdin: str) -> str:", "    sys.exit(main(args))"]
    assert '"""multi\nline"""' in c.source  # multi-line strings untouched
    run = load(write(tmp_path, "lines", c.source)).run
    assert run(["-v"], "") == "2 lines\n"
    with pytest.raises(ModuleError, match="lines: unrecognized arguments: -x"):
        run(["-x"], "")


def test_conversion_keeps_tabs_and_multiline_strings(tmp_path):
    code = 'import sys\nif True:\n\tx = 1\nHELP = 1\nmsg = str("""a\n  b""")\nfor line in sys.stdin:\n\tprint(msg, line, end="")\n'
    a = analyze(write(tmp_path, "m", code))
    c = convert(a, "m")
    assert "\tmsg = str(\"\"\"a\n  b\"\"\")" in c.source  # continuation line not indented
    assert "\tfor line in stdin.splitlines(keepends=True):\n\t\tprint" in c.source
    assert load(write(tmp_path, "mm", c.source)).run([], "x\n") == "a\n  b x\n"


def test_import_alias_when_script_name_is_taken(tmp_path):
    a = analyze(write(tmp_path, "m", "script = 'mine'\nprint(script)\n"))
    c = convert(a, "m")
    assert "from core.modkit import script as shellcraft_script" in c.source
    assert '@shellcraft_script("m")' in c.source


def test_manual_steps_describe_every_change(tmp_path):
    a = analyze(write(tmp_path, "orig", WITH_MAIN))
    steps = "\n".join(convert(a, "lines").steps)
    assert "from core.modkit import script" in steps
    assert 'Replace line 21' in steps and "def main(argv=None):" in steps and "sys.exit(main(args))" in steps


def test_guard_rejects_extra_changes(tmp_path):
    a = analyze(write(tmp_path, "orig", FLAT))
    expected = convert(a, "shout").source
    sneaky = expected.replace("print(text.upper())", "print(text.upper().strip())")
    assert extra_changes(a.source, sneaky, expected) == [
        "added:   print(text.upper().strip())", "removed: print(text.upper())"]
    reindented = expected.replace("    ", "  ")
    assert extra_changes(a.source, reindented, expected) == []


# ── docs drafts ─────────────────────────────────────────────────────────────

def test_docs_draft_parses_and_passes_check(tmp_path):
    a = analyze(write(tmp_path, "orig", FLAT))
    module = write(tmp_path, "shout_x", convert(a, "shout_x").source)
    module.with_suffix(".md").write_text(docs.markdown(a, "shout_x"), encoding="utf-8")
    skill = docs.skill(a, "shout_x")
    module.with_suffix(".skill").write_text(skill, encoding="utf-8")
    data = tomllib.loads(skill)
    assert params_mod.problems(data["params"]) == []
    assert data["usage"] == "shout_x [-n TIMES] [WORDS...]"
    report = check_module(module)
    assert report.count(FAIL) == 0, [c for c in report.checks if c.status == FAIL]
    assert {c.name for c in report.checks if c.status == WARN} == {"[[tests]]"}


def test_docs_mention_api_keys(tmp_path):
    a = analyze(write(tmp_path, "m", "import os\nkey = os.environ.get('ACME_API_KEY')\nprint(key)\n"))
    assert a.env_vars == ["ACME_API_KEY"]
    assert "settings ACME_API_KEY" in docs.markdown(a, "m") and "settings ACME_API_KEY" in docs.skill(a, "m")


# ── check ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("py", sorted((ROOT / "modules").glob("*.py")) + [ROOT / "templates/example.py"],
                         ids=lambda p: p.stem)
def test_bundled_modules_and_example_pass(py):
    report = check_module(py)
    assert report.count(FAIL) == 0, [c for c in report.checks if c.status == FAIL]
    assert report.count(WARN) == 0, [c for c in report.checks if c.status == WARN]


def test_check_catches_printing_and_exit(tmp_path):
    printing = write(tmp_path, "printy", "def run(args, stdin):\n    print('x')\n    return ''\n")
    assert any(c.status == FAIL and "printed" in c.detail for c in check_module(printing).checks)
    exiting = write(tmp_path, "exity", "import sys\ndef run(args, stdin):\n    sys.exit(1)\n")
    assert any(c.status == FAIL and "sys.exit" in c.detail for c in check_module(exiting).checks)


def test_check_without_smoke_does_not_call_run(tmp_path):
    marker = tmp_path / "called"
    py = write(tmp_path, "nosmoke", f"def run(args, stdin):\n    open({str(marker)!r}, 'w').close()\n    return ''\n")
    check_module(py, smoke=False)
    assert not marker.exists()


def test_check_cli_exit_codes(tmp_path):
    assert ingest_main(["check", str(ROOT / "templates/example.py")]) == 0
    assert ingest_main(["check", str(write(tmp_path, "ls", "def run(args, stdin):\n    return ''\n"))]) == 1


def test_source_flags():
    assert source_flags('p.add_argument("-n", "--top", default="-x")\np.add_argument("files")') == [["-n", "--top"]]


# ── AI (no network: requests is mocked) ─────────────────────────────────────

class FakeResponse:
    def __init__(self, status, data):
        self.status_code, self._data, self.text = status, data, str(data)

    def json(self):
        return self._data


def test_claude_request_shape(monkeypatch):
    sent = {}

    def post(url, headers, json, timeout):
        sent.update(url=url, headers=headers, body=json)
        return FakeResponse(200, {"stop_reason": "end_turn", "content": [{"type": "text", "text": "hi"}]})

    monkeypatch.setattr(ai.requests, "post", post)
    assert ai.complete(ai.PROVIDERS["claude"], "k", "claude-opus-5-5", "prompt") == "hi"
    assert sent["url"] == ai.ANTHROPIC_URL and sent["headers"]["x-api-key"] == "k"
    assert sent["headers"]["anthropic-version"] == "2023-06-01"
    assert sent["body"]["messages"] == [{"role": "user", "content": "prompt"}]


def test_openai_request_and_errors(monkeypatch):
    monkeypatch.setattr(ai.requests, "post", lambda url, headers, json, timeout: FakeResponse(
        200, {"choices": [{"finish_reason": "stop", "message": {"content": "ok"}}]}))
    assert ai.complete(ai.PROVIDERS["openai"], "k", "m", "p") == "ok"
    monkeypatch.setattr(ai.requests, "post", lambda *a, **k: FakeResponse(401, {}))
    with pytest.raises(ai.AIError, match="rejected the API key"):
        ai.complete(ai.PROVIDERS["openai"], "k", "m", "p")
    monkeypatch.setattr(ai.requests, "post", lambda *a, **k: FakeResponse(
        200, {"stop_reason": "refusal", "content": []}))
    with pytest.raises(ai.AIError, match="declined"):
        ai.complete(ai.PROVIDERS["claude"], "k", "m", "p")


def test_stored_key_prefers_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text('{"env": {"OPENAI_API_KEY": "stored"}}')
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert ai.stored_key(ai.PROVIDERS["openai"]) == "stored"
    monkeypatch.setenv("OPENAI_API_KEY", "env")
    assert ai.stored_key(ai.PROVIDERS["openai"]) == "env"


def test_prompts_fill_every_placeholder(tmp_path):
    a = analyze(write(tmp_path, "orig", FLAT))
    c = convert(a, "shout")
    prompt = ai.convert_prompt(a.source, "shout", c.steps, "script")
    assert "{{" not in prompt and "  11| text = " in prompt and '@script("shout")' in prompt
    prompt = ai.docs_prompt(c.source, "shout", docs.markdown(a, "shout"), docs.skill(a, "shout"))
    assert "{{" not in prompt and "Count the most frequent words" in prompt and "# shout" in prompt


def test_reply_parsing():
    reply = "shout.md\n````markdown\n# shout\n```\nshout\n```\n````\nshout.skill\n```toml\nsummary = 'x'\n```\n"
    assert ai.parse_docs(reply) == ("# shout\n```\nshout\n```\n", "summary = 'x'\n")
    assert ai.parse_code("Here:\n```python\nprint(1)\n```\n") == "print(1)\n"
    with pytest.raises(ai.AIError):
        ai.parse_code("no code here")


# ── the wizard, end to end ──────────────────────────────────────────────────

def test_wizard_auto(tmp_path, monkeypatch):
    script_path = write(tmp_path, "orig", FLAT)
    modules = tmp_path / "mods"
    answers = iter(["auto", "y", "n", "y"])  # apply · write · AI docs? · smoke test
    monkeypatch.setattr("builtins.input", lambda *a: next(answers))
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    code = ingest_main([str(script_path), "--name", "yellz", "--modules", str(modules)])
    assert code == 0
    assert script_path.read_text() == textwrap.dedent(FLAT)  # the original is untouched
    assert {p.name for p in modules.iterdir() if p.is_file()} == {"yellz.py", "yellz.md", "yellz.skill"}
    assert load(modules / "yellz.py").run(["a"], "") == "A\n"


def test_wizard_stops_on_conflict(tmp_path, monkeypatch):
    script_path = write(tmp_path, "asks", "name = input('Name? ')\nprint(name)\n")
    modules = tmp_path / "mods"
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("should not ask anything"))
    assert ingest_main([str(script_path), "--name", "askz", "--modules", str(modules)]) == 1
    assert not modules.exists()


def test_wizard_manual_writes_patch_only(tmp_path, monkeypatch):
    script_path = write(tmp_path, "orig", WITH_MAIN)
    modules = tmp_path / "mods"
    monkeypatch.chdir(tmp_path)
    answers = iter(["manual"])
    monkeypatch.setattr("builtins.input", lambda *a: next(answers))
    assert ingest_main([str(script_path), "--name", "linez", "--modules", str(modules)]) == 0
    assert "+def main(argv=None):" in (tmp_path / "linez.patch").read_text()
    assert not modules.exists()


def test_wizard_ai_rejects_extra_changes(tmp_path, monkeypatch):
    script_path = write(tmp_path, "orig", FLAT)
    modules = tmp_path / "mods"
    expected = convert(analyze(script_path), "yellq").source
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(ai, "complete", lambda *a, **k: "```python\n" + expected.replace("upper()", "lower()") + "```")
    answers = iter(["ai", "claude", "y", "manual"])  # ai · provider · send · (refused) manual
    monkeypatch.setattr("builtins.input", lambda *a: next(answers))
    monkeypatch.chdir(tmp_path)
    assert ingest_main([str(script_path), "--name", "yellq", "--modules", str(modules)]) == 0
    assert not modules.exists()
