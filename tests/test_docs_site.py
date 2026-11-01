"""The docs website (mkdocs.yml, tools/mkdocs_hooks.py): every page in docs/ is in the site's
navigation, and links that leave docs/ go to the file in the repo."""

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"


def _hooks():
    spec = importlib.util.spec_from_file_location("mkdocs_hooks", ROOT / "tools" / "mkdocs_hooks.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_docs_page_is_in_the_nav():
    nav = (ROOT / "mkdocs.yml").read_text().split("\nnav:\n", 1)[1]
    listed = set(re.findall(r":\s+(\S+\.md)\s*$", nav, re.M))
    pages = {p.relative_to(DOCS).as_posix() for p in DOCS.rglob("*.md")}
    assert pages - listed == set(), "add these pages to nav in mkdocs.yml"
    assert listed - pages == set(), "these nav entries have no page"


def test_links_outside_docs_go_to_the_repo():
    hooks = _hooks()
    text = "[back](../README.md) [log](../../CHANGELOG.md#added) [here](../cheatsheet.md) [x](errors.md)"
    github = hooks.outside_links(text, "scripting/README.md", "https://github.com/Faru0/shellcraft")
    assert "(https://github.com/Faru0/shellcraft/blob/main/README.md)" not in github  # docs/README.md
    assert "[back](../README.md)" in github
    assert "(https://github.com/Faru0/shellcraft/blob/main/CHANGELOG.md#added)" in github
    assert "[here](../cheatsheet.md)" in github and "[x](errors.md)" in github
    gitlab = hooks.outside_links("[r](../README.md)", "cheatsheet.md", "http://gitlab.example/faru/shellcraft/")
    assert gitlab == "[r](http://gitlab.example/faru/shellcraft/-/blob/main/README.md)"
