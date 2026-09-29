import json
from email.message import Message

import pytest

from core.modkit import ModuleError
from tests.conftest import FakeResponse


@pytest.fixture
def fetch(registry):
    return registry.get("fetch").run


def headers(content_type="text/plain; charset=utf-8", **extra):
    msg = Message()
    msg["Content-Type"] = content_type
    for k, v in extra.items():
        msg[k.replace("_", "-")] = v
    return msg


@pytest.fixture
def no_sleep(monkeypatch):
    import time

    slept = []
    monkeypatch.setattr(time, "sleep", slept.append)
    return slept


def test_headers_method_and_json_data(fetch, http):
    http.handler = lambda r: FakeResponse(b"ok\n", headers())
    assert fetch(["https://x.test/api", "-H", "Authorization: Bearer t", "-d", '{"a": 1}'], "") == "ok\n"
    req = http.requests[0]
    assert req.get_method() == "POST"
    assert req.get_header("Authorization") == "Bearer t"
    assert req.get_header("Content-type") == "application/json"
    assert json.loads(req.data) == {"a": 1}


def test_data_from_stdin_with_explicit_method_and_type(fetch, http):
    http.handler = lambda r: FakeResponse(b"", headers())
    fetch(["https://x.test", "-X", "put", "--data", "@-", "--header", "Content-Type: text/csv"], "a,b\n")
    req = http.requests[0]
    assert (req.get_method(), req.data, req.get_header("Content-type")) == ("PUT", b"a,b\n", "text/csv")


def test_form_data_and_file_data(fetch, http, tmp_path):
    http.handler = lambda r: FakeResponse(b"", headers())
    fetch(["https://x.test", "-d", "a=1&b=2"], "")
    assert http.requests[0].get_header("Content-type") == "application/x-www-form-urlencoded"
    (tmp_path / "body.json").write_text('{"k": true}')
    fetch(["https://x.test", "-d", f"@{tmp_path / 'body.json'}"], "")
    assert http.requests[1].data == b'{"k": true}'


@pytest.mark.parametrize("args, fragment", [
    (["https://x.test", "-H", "no-colon"], "invalid header"),
    (["notes.txt", "-X", "POST"], r"only apply to http\(s\) URLs"),
    (["https://x.test", "--max-size", "lots"], "invalid --max-size"),
    (["https://x.test", "--retry", "-1"], "--retry must be >= 0"),
])
def test_bad_options(fetch, http, args, fragment):
    with pytest.raises(ModuleError, match=fragment):
        fetch(args, "")


def test_size_limit_for_urls_and_files(fetch, http, tmp_path):
    http.handler = lambda r: FakeResponse(b"x" * 2048, headers())
    with pytest.raises(ModuleError, match="larger than 1K"):
        fetch(["https://x.test", "--max-size", "1k"], "")
    assert len(fetch(["https://x.test", "--max-size", "0"], "")) == 2048
    (tmp_path / "big.txt").write_text("y" * 2048)
    with pytest.raises(ModuleError, match="larger than 1K"):
        fetch([str(tmp_path / "big.txt"), "--max-size", "1K"], "")


@pytest.mark.parametrize("content_type", ["image/png", "application/pdf", "application/octet-stream"])
def test_rejects_binary_content_type(fetch, http, content_type):
    http.handler = lambda r: FakeResponse(b"whatever", headers(content_type))
    with pytest.raises(ModuleError, match=f"binary \\({content_type}\\)"):
        fetch(["https://x.test/file"], "")


def test_rejects_binary_bytes_in_urls_and_files(fetch, http, tmp_path):
    http.handler = lambda r: FakeResponse(b"PK\x03\x04\x00\x00", headers("text/plain"))
    with pytest.raises(ModuleError, match="looks like binary"):
        fetch(["https://x.test/f"], "")
    (tmp_path / "blob.bin").write_bytes(b"\x7fELF\x00\x01")
    with pytest.raises(ModuleError, match="looks like binary"):
        fetch([str(tmp_path / "blob.bin")], "")


def test_json_content_is_text(fetch, http):
    http.handler = lambda r: FakeResponse({"n": 5}, headers("application/vnd.api+json"))
    assert fetch(["https://x.test", "--json", "n"], "") == "5\n"


def test_retry_on_503_then_success(fetch, http, no_sleep):
    replies = iter([http.error("https://x.test", 503, headers=headers(Retry_After="3")),
                    http.error("https://x.test", 502),
                    FakeResponse(b"finally\n", headers())])

    def handler(request):
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return reply

    http.handler = handler
    assert fetch(["https://x.test", "--retry", "2"], "") == "finally\n"
    assert no_sleep == [3.0, 2.0]  # Retry-After wins, then exponential backoff


def test_no_retry_on_404_or_without_flag(fetch, http, no_sleep):
    def handler(request):
        raise http.error("https://x.test", 404)

    http.handler = handler
    with pytest.raises(ModuleError, match="HTTP 404"):
        fetch(["https://x.test", "--retry", "3"], "")
    assert len(http.requests) == 1 and no_sleep == []


def test_retry_on_connection_errors_gives_up(fetch, http, no_sleep):
    import urllib.error

    def handler(request):
        raise urllib.error.URLError("refused")

    http.handler = handler
    with pytest.raises(ModuleError, match="cannot reach"):
        fetch(["https://x.test", "--retry", "2"], "")
    assert len(http.requests) == 3 and no_sleep == [1.0, 2.0]


def test_files_keep_universal_newlines(fetch, tmp_path):
    (tmp_path / "win.txt").write_bytes(b"a\r\nb\r\n")
    assert fetch([str(tmp_path / "win.txt")], "") == "a\nb\n"
