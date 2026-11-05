"""The agents and skills in ai/: each file has the frontmatter a client needs, and every
`mcp__shellcraft__NAME` an agent may use is a tool the server really has (a misspelt one is
dropped without a word by the client, and the agent just cannot use it)."""

import re
from pathlib import Path

import pytest

from shellcraft import mcp_server

ROOT = Path(__file__).resolve().parents[1]
AGENTS = sorted((ROOT / "ai" / "agents").glob("*.md"))
SKILLS = sorted((ROOT / "ai" / "skills").glob("*/SKILL.md"))
CLIENT_TOOLS = {"Read", "Write", "Edit", "Bash", "Grep", "Glob", "Agent"}


def _frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert match, f"{path.name}: no frontmatter"
    fields = dict(line.split(": ", 1) for line in match.group(1).splitlines() if ": " in line)
    return fields


def _server_tools() -> set[str]:
    names = {mcp_server.PIPELINE_TOOL, mcp_server.ALERT_TOOL}
    for attr in dir(mcp_server):
        value = getattr(mcp_server, attr)
        if attr.isupper() and isinstance(value, dict) and ("DESCRIPTIONS" in attr or "SCHEMAS" in attr):
            names |= set(value)
    names |= {p.stem for p in (ROOT / "modules").glob("*.skill")}  # the bundled modules
    return names


@pytest.mark.parametrize("path", AGENTS, ids=lambda p: p.stem)
def test_an_agent_names_real_tools(path):
    fields = _frontmatter(path)
    assert fields["name"] == path.stem and fields["description"].strip()
    tools = [t.strip() for t in fields["tools"].split(",")]
    known = _server_tools()
    unknown = [t for t in tools
               if not (t in CLIENT_TOOLS or (t.startswith("mcp__shellcraft__") and t.removeprefix("mcp__shellcraft__") in known))]
    assert unknown == [], f"{path.name}: tools that do not exist: {unknown}"


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: p.parent.name)
def test_a_skill_has_its_name_and_description(path):
    fields = _frontmatter(path)
    assert fields["name"] == path.parent.name and len(fields["description"]) > 40


def test_the_readme_lists_every_agent_and_skill():
    readme = (ROOT / "ai" / "README.md").read_text(encoding="utf-8")
    for path in AGENTS:
        assert f"agents/{path.name}" in readme, f"{path.name} is not in ai/README.md"
    for path in SKILLS:
        assert f"skills/{path.parent.name}/SKILL.md" in readme, f"{path.parent.name} is not in ai/README.md"
