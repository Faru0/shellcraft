import json
import re

import pytest
import requests
from urllib3.exceptions import MaxRetryError, NameResolutionError

from core.modkit import ModuleError

RECORDS = {
    None: {"ip": "203.0.113.42", "country": "Testland", "country_iso": "TL", "asn": "AS64500",
           "asn_org": "Example Net", "user_agent": {"product": "x"}},
    "8.8.8.8": {"ip": "8.8.8.8", "country": "United States", "country_iso": "US", "country_eu": False,
                "latitude": 37.751, "longitude": -97.822, "asn": "AS15169", "asn_org": "Google LLC"},
    "1.1.1.1": {"ip": "1.1.1.1", "country": "Australia", "country_iso": "AU", "asn": "AS13335",
                "asn_org": "Cloudflare, Inc."},
}


@pytest.fixture
def myip(registry, monkeypatch):
    module = registry.get("myip").run.__globals__

    def fake_get(path, ip, timeout):
        if path.startswith("/port/"):
            return {"ip": "203.0.113.42", "port": int(path.split("/")[-1]), "reachable": True}
        if ip not in RECORDS:
            raise ModuleError(f"myip: could not parse IP: {ip}")
        return RECORDS[ip]

    monkeypatch.setitem(module, "_get", fake_get)
    return registry.get("myip").run


def test_own_ip(myip):
    assert myip([], "") == "203.0.113.42\n"


def test_info_for_given_ip_skips_user_agent(myip):
    out = myip(["8.8.8.8"], "")
    assert "asn_org      Google LLC" in out and "country_eu   no" in out and "user_agent" not in out


def test_field_and_coordinates(myip):
    assert myip(["8.8.8.8", "-f", "coordinates"], "") == "37.751,-97.822\n"
    assert myip(["-f", "country-iso"], "") == "TL\n"


def test_stdin_ips_tab_separated(myip):
    assert myip(["-f", "asn"], "8.8.8.8\n# comment\n\n1.1.1.1\n") == "8.8.8.8\tAS15169\n1.1.1.1\tAS13335\n"


def test_port(myip):
    assert myip(["-p", "443"], "") == "203.0.113.42:443 reachable from the internet\n"


@pytest.mark.parametrize("args, fragment", [
    (["bogus-ip"], "could not parse IP"),
    (["-f", "nope"], "unknown field"),
    (["1.1.1.1", "-p", "22"], "only checks your own IP"),
    (["-p", "70000"], "between 1 and 65535"),
])
def test_errors(myip, args, fragment):
    with pytest.raises(ModuleError, match=fragment):
        myip(args, "")


# --- _get: the HTTP layer, with requests.get stubbed ------------------------------------------


def _response(status=200, body=b"", reason="OK"):
    response = requests.Response()
    response.status_code, response.reason = status, reason
    response._content = body if isinstance(body, bytes) else json.dumps(body).encode()
    return response


@pytest.fixture
def get(registry, monkeypatch):
    """Returns (_get, calls); set `get.result` to a Response or an exception to raise."""
    module = registry.get("myip").run.__globals__
    calls = []

    def fake(url, **kwargs):
        calls.append((url, kwargs))
        if isinstance(fake.result, Exception):
            raise fake.result
        return fake.result

    fake.result = _response(body={"ip": "203.0.113.42"})
    monkeypatch.setattr(requests, "get", fake)
    return module["_get"], calls, fake


def test_get_sends_ip_param_headers_and_timeout(get):
    _get, calls, _ = get
    assert _get("/json", "8.8.8.8", 3.0) == {"ip": "203.0.113.42"}
    url, kwargs = calls[0]
    assert url == "https://ipconfig.io/json"
    assert kwargs["params"] == {"ip": "8.8.8.8"} and kwargs["timeout"] == 3.0
    assert kwargs["headers"]["Accept"] == "application/json"
    _get("/json", None, 3.0)
    assert calls[1][1]["params"] is None


@pytest.mark.parametrize("result, fragment", [
    (_response(400, {"status": 400, "error": "could not parse IP: x"}, "Bad Request"), "myip: could not parse IP: x"),
    (_response(502, b"<html>bad gateway</html>", "Bad Gateway"), "ipconfig.io returned 502 Bad Gateway"),
    (_response(500, ["not", "a", "dict"], "Server Error"), "ipconfig.io returned 500 Server Error"),
    (_response(200, b"<html></html>"), "non-JSON"),
    (_response(200, ["a", "list"]), "non-JSON"),
    (requests.ConnectTimeout("slow"), "timed out after 2s"),
    (requests.ReadTimeout("slow"), "timed out after 2s"),
    (requests.ConnectionError(MaxRetryError(None, "/json", NameResolutionError(
        "ipconfig.io", object(), "Failed to resolve 'ipconfig.io'"))),
     "cannot reach ipconfig.io: Failed to resolve 'ipconfig.io'"),
    (requests.exceptions.SSLError("certificate verify failed"), "cannot reach ipconfig.io: certificate verify failed"),
    (requests.TooManyRedirects("loop"), "request to ipconfig.io failed: loop"),
])
def test_get_errors(get, result, fragment):
    _get, _, fake = get
    fake.result = result
    with pytest.raises(ModuleError, match=re.escape(fragment)):
        _get("/json", None, 2.0)
