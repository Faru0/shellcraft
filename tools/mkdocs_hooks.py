"""MkDocs hook for the docs website (mkdocs.yml): a link from docs/ to a file outside it, like
`../README.md` or `../CHANGELOG.md`, points at that file in the repo instead of a missing page."""

from __future__ import annotations

import posixpath
import re
from typing import Any

# [text](target) where the target starts with ../ (an anchor or title after it is kept)
_LINK = re.compile(r"\]\((\.\./[^)\s#]*)(#[^)\s]*)?\)")


def blob_url(repo_url: str) -> str:
    """Where files on main live: GitHub's /blob/main/, GitLab's /-/blob/main/."""
    repo_url = repo_url.rstrip("/")
    return f"{repo_url}/blob/main/" if "github.com" in repo_url else f"{repo_url}/-/blob/main/"


def outside_links(markdown: str, page_path: str, repo_url: str) -> str:
    """`markdown` of the page at `page_path` (relative to docs/), with links that leave docs/ made
    absolute to the repo. Links that stay inside docs/ are left for MkDocs."""
    base = posixpath.dirname(page_path)

    def fix(match: re.Match[str]) -> str:
        target = posixpath.normpath(posixpath.join("docs", base, match.group(1)))
        if target.startswith("docs/") or target.startswith(".."):
            return match.group(0)
        return f"]({blob_url(repo_url)}{target}{match.group(2) or ''})"

    return _LINK.sub(fix, markdown)


def on_page_markdown(markdown: str, page: Any, config: Any, files: Any) -> str:
    return outside_links(markdown, page.file.src_uri, config.repo_url or "")
