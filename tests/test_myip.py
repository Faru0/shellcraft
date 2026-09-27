import pytest

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
