import json

import pytest

from core.modkit import ModuleError
from tests.conftest import FakeHTTP, FakeResponse

KEY = "dd_test_key"
RESULT = {
    "a": [
        {"host": "example.com", "ips": [{"ip": "93.184.215.14", "asn": "15133", "asn_name": "EDGECAST",
                                         "asn_range": "93.184.215.0/24", "country": "United States",
                                         "country_code": "US", "ptr": ""}]},
        {"host": "www.example.com", "ips": [{"ip": "93.184.215.14", "asn": "15133", "asn_name": "EDGECAST",
                                             "asn_range": "93.184.215.0/24", "country_code": "US"},
                                            {"ip": "2606:2800:21f:cb07:6820:80da:af6b:8b2c", "asn": "15133",
                                             "asn_name": "EDGECAST", "country_code": "US"}]},
    ],
    "cname": [],
    "mx": [{"host": "mail.example.com", "ips": [{"ip": "192.0.2.25", "asn": "64500", "asn_name": "MAILNET"}]}],
    "ns": [{"host": "a.iana-servers.net", "ips": [{"ip": "199.43.135.53", "asn": "396566", "asn_name": "ICANN"}]}],
    "txt": ["v=spf1 -all"],
    "total_a_recs": 2,
}


@pytest.fixture
def querydns(registry, http: FakeHTTP, clean_env):
    module = registry.get("queryDns").run.__globals__
    clean_env.setenv("DNSDUMPSTER_API_KEY", KEY)
    slept = []
    clean_env.setitem(module, "sleep", slept.append)
    http.handler = lambda request: FakeResponse(RESULT)
    run = registry.get("queryDns").run
    run.slept = slept
    return run


def test_request_uses_endpoint_and_key(querydns, http):
    querydns(["Example.COM"], "")
    [request] = http.requests
    assert request.full_url == "https://api.dnsdumpster.com/domain/example.com"
    assert request.get_header("X-api-key") == KEY


def test_table(querydns):
    out = querydns(["example.com"], "")
    lines = out.splitlines()
    assert lines[0].split() == ["TYPE", "HOST", "IP", "ASN", "CC", "NETBLOCK"]
    assert lines[1].split()[:4] == ["A", "example.com", "93.184.215.14", "AS15133"]
    assert any(ln.startswith("MX") and "mail.example.com" in ln for ln in lines)
    assert lines[-1] == "TXT  v=spf1 -all"


def test_types_hosts_and_ips(querydns):
    assert querydns(["example.com", "-t", "a", "-o", "hosts"], "") == "example.com\nwww.example.com\n"
    assert querydns(["example.com", "-t", "mx,ns", "-o", "ips"], "") == "192.0.2.25\n199.43.135.53\n"
    assert querydns(["example.com", "-t", "a", "-t", "a", "-o", "ips"], "").splitlines() == [
        "93.184.215.14", "2606:2800:21f:cb07:6820:80da:af6b:8b2c"]


def test_json_keeps_selected_types(querydns):
    data = json.loads(querydns(["example.com", "-t", "txt", "-o", "json"], ""))
    assert data == {"txt": ["v=spf1 -all"], "total_a_recs": 2}


def test_stdin_domains_are_rate_limited(querydns, http):
    out = querydns(["-o", "json", "-t", "ns"], "example.com\n# skip\nhttps://example.org/x\nexample.com\n")
    assert [u.rsplit("/", 1)[1] for u in http.urls] == ["example.com", "example.org"]
    assert querydns.slept == [2.0]
    assert set(json.loads(out)) == {"example.com", "example.org"}


def test_page_parameter(querydns, http):
    querydns(["example.com", "--page", "2"], "")
    assert http.urls == ["https://api.dnsdumpster.com/domain/example.com?page=2"]


def test_cname_strings_and_empty_results(querydns, http):
    http.handler = lambda request: FakeResponse({"a": [], "cname": ["alias.example.com"], "txt": []})
    assert querydns(["example.com", "-t", "cname", "-o", "hosts"], "") == "alias.example.com\n"
    assert querydns(["example.com", "-t", "mx"], "") == "(no records found)\n"


def test_missing_key(querydns, clean_env, http):
    clean_env.delenv("DNSDUMPSTER_API_KEY")
    with pytest.raises(ModuleError, match="settings DNSDUMPSTER_API_KEY"):
        querydns(["example.com"], "")
    assert http.requests == []


@pytest.mark.parametrize("code, body, fragment", [
    (401, {"error": "Invalid API key"}, "rejected the API key"),
    (403, b"forbidden", "rejected the API key"),
    (429, {"error": "Rate limit exceeded"}, "rate limit"),
    (500, {"error": "boom"}, "HTTP 500 for example.com: boom"),
])
def test_http_errors(querydns, http, code, body, fragment):
    def fail(request):
        raise FakeHTTP.error(request.full_url, code, body)
    http.handler = fail
    with pytest.raises(ModuleError, match=fragment):
        querydns(["example.com"], "")


def test_error_in_body_and_bad_json(querydns, http):
    http.handler = lambda request: FakeResponse({"error": "Invalid domain"})
    with pytest.raises(ModuleError, match="Invalid domain"):
        querydns(["example.com"], "")
    http.handler = lambda request: FakeResponse(b"<html>")
    with pytest.raises(ModuleError, match="non-JSON"):
        querydns(["example.com"], "")
