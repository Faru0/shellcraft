import inspect
import json
from datetime import UTC, datetime, timedelta

import pytest

from shellcraft.modkit import ModuleError
from tests.conftest import FakeHTTP, FakeResponse


def card(name="", email="", tel=""):
    return ["vcard", [["version", {}, "text", "4.0"], ["fn", {}, "text", name], ["email", {}, "text", email],
                      ["tel", {"type": "voice"}, "uri", tel]]]


NETWORK = {
    "handle": "NET-8-8-8-0-2", "name": "GOGL", "startAddress": "8.8.8.0", "endAddress": "8.8.8.255",
    "cidr0_cidrs": [{"v4prefix": "8.8.8.0", "length": 24}], "type": "DIRECT ALLOCATION",
    "events": [{"eventAction": "last changed", "eventDate": "2023-12-28T17:24:56-05:00"},
               {"eventAction": "registration", "eventDate": "2023-12-28T17:24:33-05:00"}],
    "entities": [{"roles": ["registrant"], "vcardArray": card("Google LLC"),
                  "entities": [{"roles": ["abuse"], "vcardArray": card("Abuse", "network-abuse@google.com",
                                                                       "tel:+1-650-253-0000")}]}],
}


def domain(created="1995-08-14T04:00:00Z", name="EXAMPLE.COM"):
    return {
        "ldhName": name, "status": ["client transfer prohibited"],
        "entities": [{"roles": ["registrar"], "publicIds": [{"type": "IANA Registrar ID", "identifier": "376"}],
                      "vcardArray": card("Example Registrar, Inc."),
                      "entities": [{"roles": ["abuse"], "vcardArray": card("", "abuse@registrar.example")}]}],
        "events": [{"eventAction": "registration", "eventDate": created},
                   {"eventAction": "expiration", "eventDate": "2027-08-13T04:00:00Z"}],
        "nameservers": [{"ldhName": "A.NS.EXAMPLE.NET"}, {"ldhName": "B.NS.EXAMPLE.NET"}],
        "secureDNS": {"delegationSigned": True},
    }


@pytest.fixture
def rdap(registry, http: FakeHTTP, clean_env):
    run = registry.get("queryRdap").run
    answers = {"https://rdap.org/ip/8.8.8.8": NETWORK, "https://rdap.org/domain/example.com": domain()}

    def answer(request):
        if request.full_url not in answers:
            raise http.error(request.full_url, 404)
        return FakeResponse(answers[request.full_url])

    http.handler = answer
    run.answers = answers
    run.intel = inspect.unwrap(run).__globals__["_intel"]
    return run


def test_an_ip_shows_its_network_owner_and_abuse_contact(rdap, http):
    out = rdap(["8.8.8.8"], "")
    assert http.urls == ["https://rdap.org/ip/8.8.8.8"]
    assert "rdap+json" in http.requests[0].get_header("Accept")
    assert out.splitlines()[:2] == ["indicator    8.8.8.8", "verdict      unknown: Google LLC, 8.8.8.0/24"]
    assert "network      8.8.8.0/24" in out and "owner        Google LLC" in out
    assert "created      2023-12-28" in out and "abuse_email  network-abuse@google.com" in out
    assert "abuse_phone  +1-650-253-0000" in out
    assert "note" not in out  # a new answer, not a stored one


def test_a_domain_shows_registrar_dates_and_name_servers(rdap):
    out = rdap(["Example.COM"], "")
    assert "verdict       unknown: registered 1995-08-14, Example Registrar, Inc." in out
    assert "registrar_id  376" in out and "expires       2027-08-13" in out
    assert "nameservers   a.ns.example.net, b.ns.example.net" in out and "dnssec        yes" in out
    assert "abuse_email   abuse@registrar.example" in out
    assert "age_days" not in out  # in the json only


def test_a_new_domain_is_suspicious(rdap):
    young = (datetime.now(UTC) - timedelta(days=3)).strftime("%Y-%m-%dT00:00:00Z")
    rdap.answers["https://rdap.org/domain/fresh.example"] = domain(young, "FRESH.EXAMPLE")
    record = json.loads(rdap(["fresh.example", "-o", "json"], ""))
    assert record["verdict"] == "suspicious"
    assert record["summary"] == "registered 3 days ago at Example Registrar, Inc."
    assert record["data"]["age_days"] == 3 and record["source"] == "rdap" and record["kind"] == "domain"
    assert "raw" not in record


def test_a_host_name_is_looked_up_by_its_registered_domain(rdap, http):
    out = rdap(["www.mail.example.com"], "")
    assert http.urls == ["https://rdap.org/domain/www.mail.example.com", "https://rdap.org/domain/mail.example.com",
                         "https://rdap.org/domain/example.com"]
    assert "indicator     www.mail.example.com" in out and "domain        example.com" in out


def test_not_known_is_an_answer_and_is_stored(rdap, http):
    assert rdap(["nothing.example"], "") == ("indicator  nothing.example\n"
                                              "verdict    unknown: the registries have no record of it\n")
    asked = len(http.urls)
    assert "the registries have no record of it" in rdap(["nothing.example"], "")
    assert len(http.urls) == asked  # the second answer came from the store


def test_answers_are_stored_for_a_day_and_fresh_asks_again(rdap, http, monkeypatch):
    rdap(["8.8.8.8"], "")
    again = rdap(["8.8.8.8"], "")
    assert len(http.urls) == 1
    assert "note         a stored answer, less than a day old (--fresh asks again)" in again
    assert json.loads(rdap(["8.8.8.8", "-o", "json"], ""))["cached"] is True
    rdap(["8.8.8.8", "--fresh"], "")
    assert len(http.urls) == 2
    monkeypatch.setattr(rdap.intel, "STORE_SECONDS", 0)  # a day later
    assert "note" not in rdap(["8.8.8.8"], "")
    assert len(http.urls) == 3


def test_raw_is_the_registry_answer(rdap):
    assert json.loads(rdap(["8.8.8.8", "-o", "raw"], "")) == NETWORK


def test_targets_from_stdin_defanged_and_several_records(rdap):
    out = rdap([], "# suspects\n8.8.8[.]8  seen twice\n\nexample[.]com\n8.8.8.8\n")
    first, second = out.split("\n\n")
    assert first.startswith("indicator    8.8.8.8\n") and second.startswith("indicator     example.com\n")
    records = json.loads(rdap(["-o", "json"], "8.8.8.8\nexample.com\n"))
    assert [r["indicator"] for r in records] == ["8.8.8.8", "example.com"]


def test_what_is_not_an_ip_or_a_domain_is_refused(rdap, http):
    for bad in ("http://example.com/x", "d41d8cd98f00b204e9800998ecf8427e", "a@example.com", "nonsense"):
        with pytest.raises(ModuleError, match="is not an IP address or a domain name"):
            rdap([bad], "")
    with pytest.raises(ModuleError, match="give an IP address or a domain name"):
        rdap([], "\n# nothing\n")
    assert http.urls == []


def test_errors_name_the_service_and_never_the_url(rdap, http):
    def fail(code, headers=None):
        def handler(request):
            raise http.error(request.full_url, code, headers=headers)
        return handler

    http.handler = fail(429, {"Retry-After": "7"})
    with pytest.raises(ModuleError, match=r"^queryRdap: RDAP rate limit reached; try again in 7s$"):
        rdap(["1.1.1.1"], "")
    http.handler = fail(500)
    with pytest.raises(ModuleError, match=r"^queryRdap: RDAP HTTP 500 error$"):
        rdap(["1.1.1.1"], "")
    http.handler = fail(403)
    with pytest.raises(ModuleError, match=r"^queryRdap: RDAP denied the request \(403\)$"):
        rdap(["1.1.1.1"], "")

    def timeout(request):
        raise TimeoutError
    http.handler = timeout
    with pytest.raises(ModuleError, match=r"^queryRdap: RDAP timed out after 3s$"):
        rdap(["1.1.1.1", "--timeout", "3"], "")
    http.handler = lambda request: FakeResponse(b"<html>busy</html>")
    with pytest.raises(ModuleError, match="unexpected \\(non-JSON\\) response"):
        rdap(["1.1.1.1"], "")


def test_in_a_list_a_failure_is_one_record_and_a_rate_limit_is_waited_out_once(rdap, http, monkeypatch):
    slept = []
    monkeypatch.setattr(rdap.intel, "sleep", slept.append)
    limited = {"left": 1}
    normal = http.handler

    def handler(request):
        if request.full_url.endswith("/ip/9.9.9.9"):
            raise http.error(request.full_url, 500)
        if request.full_url.endswith("/ip/8.8.8.8") and limited["left"]:
            limited["left"] -= 1
            raise http.error(request.full_url, 429, headers={"Retry-After": "5"})
        return normal(request)

    http.handler = handler
    out = rdap(["8.8.8.8", "9.9.9.9", "example.com"], "")
    assert slept == [6]
    blocks = out.split("\n\n")
    assert "owner        Google LLC" in blocks[0]
    assert blocks[1] == "indicator  9.9.9.9\nerror      RDAP HTTP 500 error"
    assert "domain        example.com" in blocks[2]
    assert json.loads(rdap(["9.9.9.9", "example.com", "-o", "json"], ""))[0]["error"] == "RDAP HTTP 500 error"


def test_lookup_is_what_enrich_calls(rdap):
    module = inspect.unwrap(rdap).__globals__
    record = module["lookup"]("8.8.8[.]8")
    assert (record["source"], record["indicator"], record["kind"], record["verdict"]) == ("rdap", "8.8.8.8", "ipv4",
                                                                                         "unknown")
    assert record["known_good"] is False and record["raw"] == NETWORK
    assert module["SOURCE"] == "rdap" and module["KINDS"] == ("ipv4", "ipv6", "domain") and module["KEY_VAR"] == ""
    with pytest.raises(ModuleError, match="is not an IP address or a domain name"):
        module["lookup"]("http://x.example/")
