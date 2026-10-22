"""fetch: curl's engine under another name, plus a file or stdin source, --head N and --jq."""

import pytest

from shellcraft.context import to_text
from shellcraft.modkit import ModuleError
from shellcraft.pipeline import run_line


@pytest.fixture
def fetch(registry):
    run = registry.get("fetch").run

    def call(*args, stdin=""):
        return to_text(run(list(args), stdin))
    return call


def test_stdin_passes_through_and_head_keeps_lines(fetch):
    assert fetch(stdin="a\nb\nc\n") == "a\nb\nc\n"
    assert fetch("-", "--head", "2", stdin="a\nb\nc\n") == "a\nb\n"


def test_a_local_file_with_universal_newlines(fetch, tmp_path):
    (tmp_path / "win.txt").write_bytes(b"a\r\nb\r\nc\r\n")
    assert fetch(str(tmp_path / "win.txt"), "--head", "2") == "a\nb\n"


def test_jq_and_the_old_json_path(fetch):
    doc = '{"user": {"name": "ada", "langs": ["py", "c"]}}'
    assert fetch("--jq", "user.name", stdin=doc) == "ada\n"
    assert fetch("--json", "user.langs", stdin=doc) == "py\nc\n"  # fetch's old --json PATH
    with pytest.raises(ModuleError, match="not valid JSON"):
        fetch("--jq", "a", stdin="nope")


def test_missing_and_binary_files(fetch, tmp_path):
    with pytest.raises(ModuleError, match="no such file"):
        fetch("missing.txt")
    (tmp_path / "blob.bin").write_bytes(b"\x7fELF\x00\x01")
    with pytest.raises(ModuleError, match="looks like binary"):
        fetch(str(tmp_path / "blob.bin"))


def test_size_limit_for_files_and_urls(fetch, web, tmp_path):
    (tmp_path / "big.txt").write_text("y" * 2048)
    with pytest.raises(ModuleError, match="larger than 1K"):
        fetch(str(tmp_path / "big.txt"), "--max-size", "1K")
    web.big = 2048
    with pytest.raises(ModuleError, match="Maximum file size exceeded"):
        fetch(f"{web.url}/big", "--max-size", "1k")
    assert len(fetch(f"{web.url}/big", "--max-size", "0")) == 2048


def test_urls_use_the_curl_engine(fetch, web):
    assert fetch(f"{web.url}/json", "--jq", "a.name") == "ada\n"
    fetch(f"{web.url}/echo", "-H", "Authorization: Bearer t", "-d", '{"a": 1}')
    got = web.last
    assert got["method"] == "POST" and got["headers"]["Authorization"] == "Bearer t"
    assert got["headers"]["Content-Type"] == "application/json"  # fetch guesses a JSON body


def test_old_switches_still_work(fetch, web):
    fetch(f"{web.url}/echo", "--method", "put", "--data", "@-", "--timeout", "5", stdin="a,b")
    assert (web.last["method"], web.last["body"]) == ("PUT", "a,b")


def test_head_n_on_a_url(fetch, web):
    assert fetch(f"{web.url}/status/200", "--head", "1") == "status 200\n"


def test_fetch_in_a_pipeline(ctx, tmp_path):
    (tmp_path / "log.txt").write_text("ok\nERROR one\nok\nERROR two\n")
    assert to_text(run_line("fetch log.txt | filter -c ERROR", ctx).output).strip() == "2"
