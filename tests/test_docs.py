"""The manual: links and anchors resolve, no em dashes, and the tutorial's code works as shown."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGES = sorted([ROOT / "README.md", ROOT / "templates" / "README.md", ROOT / "ai" / "README.md",
                *(ROOT / "docs").rglob("*.md")])
FENCE = re.compile(r"^(`{3,})[^\n]*\n.*?^\1\s*$", re.M | re.S)
LINK = re.compile(r"\]\(([^)\s]+)\)|(?:href|src)=\"([^\"]+)\"")
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.M)


def prose(path: Path) -> str:
    """The page without its code blocks (a link or heading inside one is not real)."""
    return FENCE.sub("", path.read_text(encoding="utf-8"))


def slug(heading: str) -> str:
    """GitHub's anchor for a heading."""
    text = re.sub(r"[`*_]|\[([^\]]*)\]\([^)]*\)", lambda m: m.group(1) or "", heading).strip().lower()
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def anchors(path: Path) -> set[str]:
    return {slug(h) for h in HEADING.findall(prose(path))}


@pytest.mark.parametrize("page", PAGES, ids=lambda p: str(p.relative_to(ROOT)))
def test_links_and_anchors_resolve(page):
    broken = []
    for match in LINK.finditer(prose(page)):
        target = match.group(1) or match.group(2)
        if re.match(r"^[a-z]+:", target):  # https:, mailto:
            continue
        file_part, _, anchor = target.partition("#")
        path = (page.parent / file_part).resolve() if file_part else page
        if not path.exists():
            broken.append(f"{target}: no such file")
        elif anchor and path.suffix == ".md" and anchor not in anchors(path):
            broken.append(f"{target}: no heading #{anchor}")
    assert broken == []


@pytest.mark.parametrize("page", PAGES, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_em_dashes(page):
    assert "—" not in page.read_text(encoding="utf-8")


def test_every_manual_page_is_in_the_index():
    index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    pages = {p.relative_to(ROOT / "docs").as_posix() for p in (ROOT / "docs").rglob("*.md")} - {"README.md"}
    assert {p for p in pages if f"]({p})" not in index} == set()


# ── the module tutorial ──────────────────────────────────────────────────────

MARKED = re.compile(r"<!-- file: (\S+)[^>]*-->\s*\n(`{3,})[^\n]*\n(.*?)^\2\s*$", re.M | re.S)


def tutorial_files() -> list[tuple[str, str]]:
    """(file name, content) for every marked block, in page order (a later one is a newer version)."""
    text = (ROOT / "docs" / "modules" / "first-module.md").read_text(encoding="utf-8")
    return [(m.group(1), m.group(3)) for m in MARKED.finditer(text)]


def _registry(folder: Path):
    from shellcraft.loader import ModuleRegistry

    registry = ModuleRegistry(folder)
    registry.load()
    assert registry.warnings == []
    return registry


def _out(registry, line: str) -> str:
    from shellcraft.context import ShellContext, to_text
    from shellcraft.pipeline import run_line

    return to_text(run_line(line, ShellContext(registry=registry, config={})).output)


def test_every_hello_step_gives_the_output_the_page_shows(tmp_path):
    steps = [code for name, code in tutorial_files() if name == "hello.py"]
    assert len(steps) == 3
    shown = [
        [("hello", "hello\n"), ("hello | wc -c", "6\n")],
        [("hello", "hello world\n"), ("hello Ada", "hello Ada\n")],
        [("hello -s Ada Linus", "HELLO ADA\nHELLO LINUS\n"), ("echo Ada Grace | hello", "hello Ada\nhello Grace\n")],
    ]
    for code, cases in zip(steps, shown):
        (tmp_path / "hello.py").write_text(code)
        registry = _registry(tmp_path)
        for line, expected in cases:
            assert _out(registry, line).strip() == expected.strip(), line


def test_hello_fails_with_the_message_the_page_shows(tmp_path):
    from shellcraft.pipeline import PipelineError

    (tmp_path / "hello.py").write_text([c for n, c in tutorial_files() if n == "hello.py"][-1])
    with pytest.raises(PipelineError, match="hello: who should I greet\\? Try: hello Ada"):
        _out(_registry(tmp_path), "hello")


def test_the_finished_ghrepo_module_passes_the_strict_checker(tmp_path):
    from tools.ingest.check import FAIL, WARN, check_module

    files = dict(tutorial_files())
    for name in ("ghrepo.py", "ghrepo.md", "ghrepo.skill"):
        (tmp_path / name).write_text(files[name])
    report = check_module(tmp_path / "ghrepo.py")
    problems = [c for c in report.checks if c.status in (FAIL, WARN)]
    assert problems == []


def test_the_cheat_sheet_code_runs(tmp_path):
    text = (ROOT / "docs" / "cheatsheet.md").read_text(encoding="utf-8")
    blocks = re.findall(r"^```python\n(.*?)^```", text, re.M | re.S)
    assert len(blocks) >= 4
    for code in blocks:
        compile(code, "cheatsheet", "exec")
    module = next(code for code in blocks if 'ArgParser("mycmd")' in code and "items" in code)
    (tmp_path / "mycmd.py").write_text(module)
    assert _out(_registry(tmp_path), "mycmd a b c -n 2") == "a\nb\n"


def test_the_folder_page_lists_every_starter_file():
    from shellcraft import skel

    text = (ROOT / "docs" / "your-shellcraft-folder.md").read_text(encoding="utf-8")
    section = text[text.index("## The starter files"):text.index("## Each file")]
    listed = set(re.findall(r"^\| `([^`]+)` \|", section, re.M))
    assert listed == set(skel.files())
