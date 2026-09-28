import json
import urllib.parse

import pytest

from core.modkit import ModuleError
from tests.conftest import FakeHTTP, FakeResponse

RECORDS = {
    "24.48.0.1": {"status": "success", "query": "24.48.0.1", "country": "Canada", "countryCode": "CA",
                  "city": "Montreal", "lat": 45.6085, "lon": -73.5493, "as": "AS5769 Videotron Ltee",
                  "district": "", "proxy": False, "hosting": True},
    "": {"status": "success", "query": "203.0.113.42", "country": "Testland", "countryCode": "TL"},
    "10.0.0.1": {"status": "fail", "message": "private range", "query": "10.0.0.1"},
}


@pytest.fixture
def ip2geo(registry, http: FakeHTTP, monkeypatch):
    module = registry.get("ip2geo").run.__globals__
    slept = []
    monkeypatch.setitem(module, "sleep", slept.append)
    state = {"remaining": 44}

    def answer(request):
        query = urllib.parse.unquote(urllib.parse.urlsplit(request.full_url).path.removeprefix("/json/"))
        return FakeResponse(RECORDS[query], {"X-Rl": str(state["remaining"]), "X-Ttl": "37"})

    http.handler = answer
    run = registry.get("ip2geo").run
    run.slept, run.state = slept, state
    return run


def params(url):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


def test_table_hides_empty_fields(ip2geo, http):
    out = ip2geo(["24.48.0.1"], "")
    assert http.urls[0].startswith("http://ip-api.com/json/24.48.0.1?fields=status%2Cmessage%2Cquery")
    assert "reverse" not in params(http.urls[0])["fields"]
    assert "country      Canada" in out and "proxy        no" in out and "hosting      yes" in out
    assert "district" not in out


def test_own_ip_and_lang(ip2geo, http):
    assert ip2geo(["-f", "country", "--lang", "de"], "") == "query    203.0.113.42\ncountry  Testland\n"
    assert http.urls[0].startswith("http://ip-api.com/json/?")
    assert params(http.urls[0]) == {"fields": "status,message,query,country", "lang": "de"}


def test_fields_normalized_and_all(ip2geo, http):
    ip2geo(["24.48.0.1", "-f", "country_code,AS", "-f", "lat"], "")
    assert params(http.urls[0])["fields"] == "status,message,query,countryCode,as,lat"
    ip2geo(["24.48.0.1", "-f", "all"], "")
    assert "reverse" in params(http.urls[1])["fields"]


def test_tsv_with_inline_failure(ip2geo):
    out = ip2geo(["-f", "countryCode,city", "-o", "tsv"], "24.48.0.1\n# c\n10.0.0.1\n")
    assert out == "query\tcountryCode\tcity\n24.48.0.1\tCA\tMontreal\n10.0.0.1\tfail: private range\t\n"


def test_json(ip2geo):
    assert json.loads(ip2geo(["24.48.0.1", "-o", "json"], ""))["city"] == "Montreal"
    assert len(json.loads(ip2geo(["24.48.0.1", "10.0.0.1", "-o", "json"], ""))) == 2


def test_single_failure_is_an_error(ip2geo):
    with pytest.raises(ModuleError, match="10.0.0.1: private range"):
        ip2geo(["10.0.0.1"], "")


def test_waits_when_rate_window_is_used_up(ip2geo):
    ip2geo.state["remaining"] = 0
    ip2geo(["24.48.0.1", "24.48.0.1", "24.48.0.1"], "")
    assert ip2geo.slept == [38, 38]  # X-Ttl + 1, and never after the last request


def test_429(ip2geo, http):
    def fail(request):
        raise FakeHTTP.error(request.full_url, 429, b"", {"X-Ttl": "12"})
    http.handler = fail
    with pytest.raises(ModuleError, match="try again in 12s"):
        ip2geo(["24.48.0.1"], "")


@pytest.mark.parametrize("args, fragment", [
    (["-f", "nope"], "unknown field"),
    (["a/b"], "not an IP address or domain name"),
    (["8.8.8.8", "--lang", "xx"], "invalid choice"),
])
def test_argument_errors(ip2geo, args, fragment):
    with pytest.raises(ModuleError, match=fragment):
        ip2geo(args, "")
