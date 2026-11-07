"""run_func: an AI runs only the funcs the user marked `# ai: read`, in the read-only sandbox."""

import anyio
import pytest
from mcp.client import Client

from shellcraft import ai_policy, funcs, mainlink
from shellcraft.mcp_server import build_server


def _call(server, fn):
    async def main():
        async with Client(server) as client:
            return await fn(client)
    return anyio.run(main)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / ".home"))
    monkeypatch.delenv(mainlink.ADDRESS_ENV, raising=False)
    monkeypatch.delenv(mainlink.KEY_ENV, raising=False)
    (tmp_path / ".home" / "funcs").mkdir(parents=True)
    ai_policy._cache.clear()
    return tmp_path / ".home"


def write_func(home, name, body, ai="read"):
    header = f"# func: {name}\n# description: test\n# system: no\n# output: show\n" + (f"# ai: {ai}\n" if ai else "")
    (home / "funcs" / f"{name}.func").write_text(header + body, encoding="utf-8")


def run_func(server, name, args=None):
    return _call(server, lambda c: c.call_tool("run_func", {"name": name, "args": args or []}))


def test_a_marked_func_runs_with_its_arguments(registry, home):
    write_func(home, "greet", "echo hello $1\necho second line\n")
    out = run_func(build_server(registry), "greet", ["world"])
    assert not out.is_error
    assert out.content[0].text.splitlines() == ["hello world", "second line"]


def test_an_unmarked_func_is_refused(registry, home):
    write_func(home, "plain", "echo no\n", ai="")
    out = run_func(build_server(registry), "plain")
    assert out.is_error and "# ai: read" in out.content[0].text


def test_any_other_ai_value_is_not_read(registry, home):
    write_func(home, "odd", "echo no\n", ai="write")
    assert run_func(build_server(registry), "odd").is_error


@pytest.mark.parametrize("name", ["missing", "../x", "a b", ""])
def test_unknown_and_odd_names_are_refused(registry, home, name):
    assert run_func(build_server(registry), name).is_error


def test_the_sandbox_still_applies_to_a_marked_func(registry, home, tmp_path):
    write_func(home, "writer", "echo owned > pwned.txt\n")
    out = run_func(build_server(registry), "writer")
    assert out.is_error and not (tmp_path / "pwned.txt").exists()
    write_func(home, "remover", "mkdir made\n")
    assert run_func(build_server(registry), "remover").is_error and not (tmp_path / "made").exists()


def test_a_func_with_system_steps_does_not_get_os_commands(registry, home):
    write_func(home, "sys", "ssh localhost true\n")
    out = run_func(build_server(registry), "sys")
    assert out.is_error


def test_a_failing_step_names_the_step(registry, home):
    write_func(home, "broken", "echo ok\nnosuchcommand\n")
    out = run_func(build_server(registry), "broken")
    assert out.is_error and "step 2" in out.content[0].text


def test_the_tool_is_listed_and_role_gated(registry, home):
    tools = _call(build_server(registry), lambda c: c.list_tools()).tools
    assert "run_func" in {t.name for t in tools}
    monitor = _call(build_server(registry, role="monitor"), lambda c: c.list_tools()).tools
    assert "run_func" not in {t.name for t in monitor}
    write_func(home, "greet", "echo hi\n")
    refused = run_func(build_server(registry, role="monitor"), "greet")
    assert refused.is_error and "may not use" in refused.content[0].text


def test_an_analyst_role_can_use_it_and_nothing_else_that_runs(registry, home):
    (home / ai_policy.FILE).write_text('[role.analyst]\nrun = "none"\ntools = ["run_func"]\n', encoding="utf-8")
    ai_policy._cache.clear()
    write_func(home, "greet", "echo hi\n")
    server = build_server(registry, role="analyst")
    assert run_func(server, "greet").content[0].text.strip() == "hi"
    other = _call(server, lambda c: c.call_tool("run_in_shell", {"command": "ls", "reason": "x"}))
    assert other.is_error and "may not use" in other.content[0].text


def test_the_ai_header_round_trips():
    func = funcs.parse("# func: x\n# description: d\n# ai: read\necho 1\n", "x")
    assert func.ai == "read" and "# ai: read" in funcs.render(func)
    assert funcs.parse("# func: x\n# ai: nonsense\necho 1\n", "x").ai == ""
    assert "# ai:" not in funcs.render(funcs.parse("# func: x\necho 1\n", "x"))
