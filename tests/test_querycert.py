import json
import urllib.error
import urllib.parse

import pytest

from core.modkit import ModuleError
from tests.conftest import FakeHTTP, FakeResponse

# Field names as crt.sh returns them (checked live, 2026-09).
CERTS = [
    {"issuer_ca_id": 1, "issuer_name": "C=US, O=Let's Encrypt, CN=R11", "common_name": "example.com",
     "name_value": "example.com\nwww.example.com", "id": 300, "not_before": "2026-08-01T00:00:00",
     "not_after": "2099-01-01T00:00:00", "serial_number": "03aa", "result_count": 2},
    {"issuer_ca_id": 2, "issuer_name": "C=GB, O=Sectigo Limited, CN=Sectigo DV", "common_name": "*.example.com",
     "name_value": "*.example.com\nuser@example.com\nsome CA test - example.com", "id": 200,
     "not_before": "2026-01-01T00:00:00", "not_after": "2098-01-01T00:00:00", "serial_number": "0bb",
     "result_count": 3},
    {"issuer_ca_id": 2, "issuer_name": "C=GB, O=Sectigo Limited, CN=Sectigo DV", "common_name": "old.example.com",
     "name_value": "old.example.com", "id": 100, "not_before": "2019-01-01T00:00:00",
     "not_after": "2020-01-01T00:00:00", "serial_number": "0cc", "result_count": 1},
]


@pytest.fixture
def querycert(registry, http: FakeHTTP, monkeypatch):
    module = registry.get("queryCert").run.__globals__
    monkeypatch.setitem(module, "sleep", lambda s: None)
    http.handler = lambda request: FakeResponse(CERTS)
    return registry.get("queryCert").run


def params(url):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


def test_query_parameters(querycert, http):
    querycert(["example.com"], "")
    querycert(["-s", "*.Example.com", "--expired"], "")
    assert params(http.urls[0]) == {"q": "example.com", "output": "json", "exclude": "expired"}
    assert params(http.urls[1]) == {"q": "%.example.com", "output": "json"}


def test_table_skips_expired_and_sorts_newest_first(querycert):
    lines = querycert(["example.com"], "").splitlines()
    assert lines[0].split()[:2] == ["CRT.SH", "ID"]
    assert [ln.split()[0] for ln in lines[1:]] == ["300", "200"]
    assert "Let's Encrypt" in lines[1] and "example.com, www.example.com" in lines[1]


def test_expired_are_marked(querycert):
    out = querycert(["example.com", "--expired"], "")
    assert "2020-01-01 (expired)" in out


def test_names_are_unique_hostnames(querycert):
    assert querycert(["example.com", "-o", "names"], "") == "*.example.com\nexample.com\nwww.example.com\n"


def test_json_and_dedup_across_domains(querycert, http):
    data = json.loads(querycert(["-o", "json"], "example.com\nexample.org\n"))
    assert len(http.requests) == 2
    assert [c["id"] for c in data] == [300, 200]


def test_empty_answer(querycert, http):
    http.handler = lambda request: FakeResponse(b"")
    assert querycert(["example.com"], "") == "(no certificates found)\n"


@pytest.mark.parametrize("failure", [
    lambda url: FakeHTTP.error(url, 502, b"Bad Gateway"),
    lambda url: FakeHTTP.error(url, 429, b""),
    lambda url: urllib.error.URLError(TimeoutError("timed out")),
    lambda url: TimeoutError(),
])
def test_overloaded(querycert, http, failure):
    def fail(request):
        raise failure(request.full_url)
    http.handler = fail
    with pytest.raises(ModuleError, match="overloaded"):
        querycert(["example.com"], "")


def test_html_error_page_is_overload(querycert, http):
    http.handler = lambda request: FakeResponse(b"<html><body>Sorry, something went wrong</body></html>")
    with pytest.raises(ModuleError, match="error page instead of JSON"):
        querycert(["example.com"], "")


@pytest.mark.parametrize("args, fragment", [
    ([], "give a domain"),
    (["localhost"], "not a domain name"),
    (["example.com", "-o", "csv"], "invalid choice"),
])
def test_argument_errors(querycert, args, fragment):
    with pytest.raises(ModuleError, match=fragment):
        querycert(args, "")
