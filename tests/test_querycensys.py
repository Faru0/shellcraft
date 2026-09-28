import json
import sys
from types import SimpleNamespace

import pytest

from core.modkit import ModuleError

HOST = {
    "extensions": {},
    "resource": {
        "ip": "8.8.8.8",
        "autonomous_system": {"asn": 15169, "name": "GOOGLE", "bgp_prefix": "8.8.8.0/24"},
        "location": {"city": "Mountain View", "province": "California", "country": "United States"},
        "dns": {"names": ["dns.google"]},
        "service_count": 2,
        "services": [
            {"port": 853, "transport_protocol": "tcp", "protocol": "DNS"},
            {"port": 443, "transport_protocol": "tcp", "protocol": "HTTP",
             "software": [{"vendor": "google", "product": "gws"}]},
        ],
    },
}
CERT = {
    "extensions": {},
    "resource": {
        "fingerprint_sha256": "ab" * 32, "names": ["example.com", "www.example.com"], "revoked": False,
        "validation_level": "dv",
        "parsed": {"subject_dn": "CN=example.com", "issuer_dn": "C=US, O=DigiCert Inc",
                   "serial_number_hex": "0a1b", "validity_period": {"not_before": "2026-01-01T00:00:00Z",
                                                                     "not_after": "2027-01-01T00:00:00Z"}},
    },
}
SEARCH = {
    "hits": [
        {"host_v1": {"resource": {"ip": "192.0.2.1", "autonomous_system": {"asn": 64500, "name": "EXAMPLE"},
                                  "location": {"country_code": "IS"}}}},
        {"webproperty_v1": {"resource": {"hostname": "example.com", "port": 443,
                                         "software": [{"product": "nginx"}]}}},
        {"certificate_v1": {"resource": {"fingerprint_sha256": "cd" * 32, "names": ["a.example.com"]}}},
    ],
    "total_hits": 1234.0, "next_page_token": "tok2", "previous_page_token": "", "query_duration_millis": 5,
}


class Payload:
    def __init__(self, data):
        self.data = data

    def model_dump(self, **kwargs):
        assert kwargs == {"mode": "json", "by_alias": True, "exclude_none": True}
        return self.data


def envelope(data):
    return SimpleNamespace(result=SimpleNamespace(result=Payload(data)))


class SDKError(Exception):
    """Shaped like censys_platform.models.SDKBaseError."""

    def __init__(self, status_code, body=""):
        super().__init__(body)
        self.status_code, self.body = status_code, body


class FakeGlobalData:
    def __init__(self):
        self.calls, self.fail = [], None

    def _answer(self, name, data, **kwargs):
        self.calls.append((name, kwargs))
        if self.fail:
            raise self.fail
        return envelope(data)

    def get_host(self, host_id):
        return self._answer("get_host", HOST, host_id=host_id)

    def get_certificate(self, certificate_id):
        return self._answer("get_certificate", CERT, certificate_id=certificate_id)

    def search(self, search_query_input_body):
        return self._answer("search", SEARCH, search_query_input_body=search_query_input_body)


@pytest.fixture
def censys(registry, clean_env):
    module = registry.get("queryCensys").run.__globals__
    clean_env.setenv("CENSYS_API_TOKEN", "censys_test")
    fake = FakeGlobalData()
    clients = []

    def client(token, org, timeout):
        clients.append((token, org, timeout))
        return SimpleNamespace(global_data=fake)

    clean_env.setitem(module, "_client", client)
    run = registry.get("queryCensys").run
    run.fake, run.clients = fake, clients
    return run


def test_host_table(censys):
    out = censys(["host", "8.8.8.8"], "")
    assert censys.clients == [("censys_test", None, 30.0)]
    assert censys.fake.calls == [("get_host", {"host_id": "8.8.8.8"})]
    assert "asn            AS15169 GOOGLE" in out and "location       Mountain View, California" in out
    ports = [ln.split()[0] for ln in out.splitlines() if "/tcp" in ln]
    assert ports == ["443/tcp", "853/tcp"]
    assert "google gws" in out


def test_org_id_and_stdin_hosts(censys, clean_env):
    clean_env.setenv("CENSYS_ORG_ID", "org-123")
    data = json.loads(censys(["host", "-o", "json"], "8.8.8.8\n1.1.1.1\n"))
    assert censys.clients[0][1] == "org-123"
    assert [c[1]["host_id"] for c in censys.fake.calls] == ["8.8.8.8", "1.1.1.1"]
    assert isinstance(data, list) and data[0]["resource"]["ip"] == "8.8.8.8"


def test_cert(censys):
    out = censys(["cert", ":".join(["AB"] * 32)], "")
    assert censys.fake.calls == [("get_certificate", {"certificate_id": "ab" * 32})]
    assert "issuer            C=US, O=DigiCert Inc" in out and "revoked           no" in out


def test_search(censys):
    out = censys(["search", "host.services.port:", "22", "-n", "3", "--page-token", "tok1"], "")
    body = censys.fake.calls[0][1]["search_query_input_body"]
    assert body == {"query": "host.services.port: 22", "page_size": 3, "page_token": "tok1"}
    lines = out.splitlines()
    assert lines[1].split()[:3] == ["host", "192.0.2.1", "AS64500"]
    assert lines[2].split()[:2] == ["web", "example.com:443"]
    assert lines[-1] == "# 3 of 1234 hit(s); next page: --page-token tok2"


def test_missing_token(censys, clean_env):
    clean_env.delenv("CENSYS_API_TOKEN")
    with pytest.raises(ModuleError, match="settings CENSYS_API_TOKEN"):
        censys(["host", "8.8.8.8"], "")
    assert censys.clients == []


def test_missing_sdk(registry, clean_env):
    clean_env.setenv("CENSYS_API_TOKEN", "censys_test")
    clean_env.setitem(sys.modules, "censys_platform", None)  # makes `import censys_platform` fail
    with pytest.raises(ModuleError, match="pip install censys-platform"):
        registry.get("queryCensys").run(["host", "8.8.8.8"], "")


@pytest.mark.parametrize("command, org, status, body, fragment", [
    ("host", None, 401, '{"error": {"message": "Access credentials are invalid"}}', "rejected the API token"),
    ("host", None, 403, '{"detail": "not in your plan"}', r"API Access role \(not in your plan\)"),
    ("host", None, 404, "", "no host 8.8.8.8"),
    ("search", None, 422, '{"detail": "Missing Organization ID"}', "settings CENSYS_ORG_ID"),
    ("search", "org", 422, '{"detail": "bad query"}', r"rejected the request \(422\): bad query"),
    ("host", None, 429, "", "concurrent requests"),
    ("host", None, 503, "", "rate limit"),
    ("host", None, 500, "oops", "error 500: oops"),
])
def test_api_errors(censys, clean_env, command, org, status, body, fragment):
    if org:
        clean_env.setenv("CENSYS_ORG_ID", org)
    censys.fake.fail = SDKError(status, body)
    with pytest.raises(ModuleError, match=fragment):
        censys([command, "8.8.8.8"], "")


def test_network_errors(censys):
    connect_error = type("ConnectError", (Exception,), {})
    read_timeout = type("ReadTimeout", (Exception,), {})
    censys.fake.fail = connect_error("dns failure")
    with pytest.raises(ModuleError, match="cannot reach api.platform.censys.io"):
        censys(["host", "8.8.8.8"], "")
    censys.fake.fail = read_timeout()
    with pytest.raises(ModuleError, match="did not answer in time"):
        censys(["host", "8.8.8.8"], "")


def test_real_sdk_errors_carry_status_and_body():
    """The module relies on these attributes of the installed SDK's errors (skipped without the SDK)."""
    models = pytest.importorskip("censys_platform.models")
    import httpx

    response = httpx.Response(401, text='{"error": {"message": "Access credentials are invalid"}}',
                              request=httpx.Request("GET", "https://api.platform.censys.io/v3/global/asset/host/x"))
    error = models.SDKError("API error occurred", response, response.text)
    assert isinstance(error, models.SDKBaseError)
    assert error.status_code == 401 and "credentials" in error.body


@pytest.mark.parametrize("args, fragment", [
    ([], "give a command"),
    (["host"], "host needs an IP address"),
    (["cert", "xyz"], "not a SHA-256 fingerprint"),
    (["search", "q", "-n", "0"], "between 1 and 100"),
])
def test_argument_errors(censys, args, fragment):
    with pytest.raises(ModuleError, match=fragment):
        censys(args, "")
