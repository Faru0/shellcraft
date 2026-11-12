import inspect
import json
import urllib.error

import pytest

from shellcraft.modkit import ModuleError
from tests.conftest import FakeHTTP, FakeResponse

API = "https://api.greynoise.io/v3/community/"
KEY = "gn-secret-key-123"
UNSEEN = "not seen scanning the internet, not a known service"
PRIVATE = "a private address: GreyNoise only watches the public internet"

GOOGLE = {"ip": "8.8.8.8", "noise": False, "riot": True, "classification": "benign", "name": "Google Public DNS",
          "link": "https://viz.greynoise.io/ip/8.8.8.8", "last_seen": "2026-11-10", "message": "Success"}
QUIET = {"ip": "9.9.9.9", "noise": False, "riot": False, "message": "IP not observed scanning the internet."}


def seen(ip="45.83.64.1", **fields):
    """GreyNoise's answer for an address it knows; a field set to None is left out."""
    answer = {"ip": ip, "noise": True, "riot": False, "classification": "malicious", "name": "unknown",
              "link": f"https://viz.greynoise.io/ip/{ip}", "last_seen": "2026-11-10", "message": "Success", **fields}
    return {k: v for k, v in answer.items() if v is not None}


@pytest.fixture
def greynoise(registry, http: FakeHTTP, clean_env):
    run = registry.get("queryGreyNoise").run
    answers = {API + "8.8.8.8": GOOGLE, API + "9.9.9.9": QUIET, API + "45.83.64.1": seen()}

    def answer(request):
        if request.full_url not in answers:
            raise http.error(request.full_url, 404)
        return FakeResponse(answers[request.full_url])

    http.handler = answer
    run.answers = answers
    run.intel = inspect.unwrap(run).__globals__["_intel"]
    return run


def fail(http, code, headers=None):
    def handler(request):
        raise http.error(request.full_url, code, headers=headers)
    return handler


def test_a_known_service_is_clean_and_shows_what_greynoise_said(greynoise, http):
    out = greynoise(["8.8.8.8"], "")
    assert http.urls == ["https://api.greynoise.io/v3/community/8.8.8.8"]
    assert out == ("indicator       8.8.8.8\n"
                   "verdict         clean: a known service: Google Public DNS\n"
                   "noise           no\n"
                   "riot            yes\n"
                   "classification  benign\n"
                   "name            Google Public DNS\n"
                   "last_seen       2026-11-10\n"
                   "link            https://viz.greynoise.io/ip/8.8.8.8\n"
                   "message         Success\n")
    assert "note" not in out  # a new answer, not a stored one


def test_a_malicious_scanner_in_the_table(greynoise):
    out = greynoise(["45.83.64.1"], "")
    assert out.splitlines()[:2] == [
        "indicator       45.83.64.1",
        "verdict         malicious: seen scanning the internet, classified malicious, last seen 2026-11-10"]
    assert "noise           yes" in out and "riot            no" in out
    assert "\nname" not in out  # the name "unknown" is no name


def test_json_is_the_record_without_raw(greynoise):
    record = json.loads(greynoise(["8.8.8.8", "-o", "json"], ""))
    assert list(record) == ["source", "indicator", "kind", "verdict", "summary", "known_good", "cached", "data"]
    assert record == {
        "source": "greynoise", "indicator": "8.8.8.8", "kind": "ipv4", "verdict": "clean",
        "summary": "a known service: Google Public DNS", "known_good": True, "cached": False,
        "data": {"noise": False, "riot": True, "classification": "benign", "name": "Google Public DNS",
                 "last_seen": "2026-11-10", "link": "https://viz.greynoise.io/ip/8.8.8.8", "message": "Success"}}


def test_raw_is_greynoises_answer(greynoise):
    assert json.loads(greynoise(["8.8.8.8", "-o", "raw"], "")) == GOOGLE
    assert json.loads(greynoise(["8.8.8.8", "9.9.9.9", "-o", "raw"], "")) == [GOOGLE, QUIET]


def test_it_works_without_a_key_and_sends_no_key_header(greynoise, http):
    greynoise(["8.8.8.8"], "")
    request = http.requests[0]
    assert request.get_header("Accept") == "application/json"
    assert not request.has_header("Key")
    assert "key" not in {name.lower() for name, _ in request.header_items()}


def test_a_key_is_sent_in_the_key_header(greynoise, http, clean_env):
    clean_env.setenv("GREYNOISE_API_KEY", f"  {KEY}\n")
    greynoise(["8.8.8.8"], "")
    request = http.requests[0]
    assert request.full_url == "https://api.greynoise.io/v3/community/8.8.8.8"  # the key is not in the URL
    assert request.get_header("Key") == KEY
    assert request.get_header("Accept") == "application/json"
    clean_env.setenv("GREYNOISE_API_KEY", "   ")  # an empty key is no key
    greynoise(["8.8.8.8", "--fresh"], "")
    assert not http.requests[1].has_header("Key")


@pytest.mark.parametrize(("noise", "riot", "classification", "name", "last_seen", "verdict", "summary", "known_good"), [
    # classified malicious: malicious, whatever else is true
    (True, False, "malicious", "unknown", "2026-11-10", "malicious",
     "seen scanning the internet, classified malicious, last seen 2026-11-10", False),
    (True, False, "malicious", "Mirai", "2026-11-10", "malicious",
     "seen scanning the internet, classified malicious, Mirai, last seen 2026-11-10", False),
    (True, False, "malicious", "Mirai", None, "malicious",
     "seen scanning the internet, classified malicious, Mirai", False),
    (False, False, "malicious", None, None, "malicious", "seen scanning the internet, classified malicious", False),
    (True, True, "malicious", "Some CDN", "2026-11-10", "malicious",  # malicious wins over riot
     "seen scanning the internet, classified malicious, Some CDN, last seen 2026-11-10", False),
    (False, True, "malicious", "unknown", None, "malicious",
     "seen scanning the internet, classified malicious", False),
    # a known business service: clean and known good
    (False, True, "benign", "Google Public DNS", "2026-11-10", "clean", "a known service: Google Public DNS", True),
    (False, True, "unknown", "unknown", None, "clean", "a known business service", True),
    (False, True, None, None, None, "clean", "a known business service", True),
    (True, True, "unknown", "Cloudflare", "2026-11-10", "clean", "a known service: Cloudflare", True),  # riot wins over noise
    (True, True, "suspicious", "Cloudflare", "2026-11-10", "clean", "a known service: Cloudflare", True),
    (True, True, "benign", "unknown", "2026-11-10", "clean", "a known business service", True),
    # a benign scanner: clean, but not known good
    (True, False, "benign", "Shodan.io", "2026-11-10", "clean", "a known benign scanner: Shodan.io", False),
    (True, False, "benign", "unknown", "2026-11-10", "clean", "a known benign scanner", False),
    (False, False, "benign", "Censys", None, "clean", "a known benign scanner: Censys", False),
    # seen scanning, or classified suspicious: suspicious
    (True, False, "suspicious", "unknown", "2026-11-10", "suspicious",
     "seen scanning the internet, classified suspicious, last seen 2026-11-10", False),
    (False, False, "suspicious", "unknown", None, "suspicious", "seen scanning the internet, classified suspicious", False),
    (True, False, "unknown", "unknown", "2026-11-10", "suspicious",
     "seen scanning the internet, not classified, last seen 2026-11-10", False),
    (True, False, "unknown", "Some Host", "2026-11-10", "suspicious",
     "seen scanning the internet, not classified, last seen 2026-11-10", False),
    (True, False, None, None, None, "suspicious", "seen scanning the internet, not classified", False),
    (True, False, "something new", None, "2026-11-10", "suspicious",
     "seen scanning the internet, not classified, last seen 2026-11-10", False),
    # neither: unknown
    (False, False, "unknown", "unknown", None, "unknown", UNSEEN, False),
    (False, False, None, None, None, "unknown", UNSEEN, False),
    (False, False, "unknown", "Some Host", "2026-11-01", "unknown", UNSEEN, False),
])
def test_the_verdict_rules(greynoise, noise, riot, classification, name, last_seen, verdict, summary, known_good):
    greynoise.answers[API + "5.6.7.8"] = seen("5.6.7.8", noise=noise, riot=riot, classification=classification,
                                              name=name, last_seen=last_seen)
    record = json.loads(greynoise(["5.6.7.8", "-o", "json"], ""))
    assert (record["verdict"], record["summary"], record["known_good"]) == (verdict, summary, known_good)
    assert (record["data"]["noise"], record["data"]["riot"]) == (noise, riot)
    assert record["data"]["classification"] == (classification or "")


def test_the_fields_are_read_carefully(greynoise):
    greynoise.answers[API + "5.6.7.8"] = seen("5.6.7.8", classification="MALICIOUS", name="Unknown",
                                              last_seen="2026-11-10T08:15:00Z", noise="yes")
    record = json.loads(greynoise(["5.6.7.8", "-o", "json"], ""))
    assert record["verdict"] == "malicious"
    assert record["data"] == {"noise": False, "riot": False, "classification": "malicious", "name": "",
                              "last_seen": "2026-11-10", "link": "https://viz.greynoise.io/ip/5.6.7.8",
                              "message": "Success"}
    greynoise.answers[API + "5.6.7.9"] = seen("5.6.7.9", classification="unknown", last_seen="yesterday")
    record = json.loads(greynoise(["5.6.7.9", "-o", "json"], ""))
    assert record["summary"] == "seen scanning the internet, not classified" and record["data"]["last_seen"] == ""


def test_not_observed_is_unknown(greynoise):
    assert greynoise(["9.9.9.9"], "") == ("indicator  9.9.9.9\n"
                                          f"verdict    unknown: {UNSEEN}\n"
                                          "noise      no\n"
                                          "riot       no\n"
                                          "message    IP not observed scanning the internet.\n")


def test_not_known_404_is_an_answer_and_is_stored(greynoise, http):
    wanted = f"indicator  1.2.3.4\nverdict    unknown: {UNSEEN}\nnoise      no\nriot       no\n"
    assert greynoise(["1.2.3.4"], "") == wanted
    assert http.urls == [API + "1.2.3.4"]
    again = greynoise(["1.2.3.4"], "")
    assert again.startswith(wanted) and "note       a stored answer" in again
    assert len(http.urls) == 1  # the second answer came from the store
    record = json.loads(greynoise(["1.2.3.4", "-o", "json"], ""))
    assert (record["verdict"], record["cached"], record["known_good"]) == ("unknown", True, False)
    assert json.loads(greynoise(["1.2.3.4", "-o", "raw"], "")) is None
    assert len(http.urls) == 1


def test_answers_are_stored_for_a_day_and_fresh_asks_again(greynoise, http, monkeypatch):
    greynoise(["8.8.8.8"], "")
    again = greynoise(["8.8.8.8"], "")
    assert len(http.urls) == 1
    assert "note            a stored answer, less than a day old (--fresh asks again)" in again
    assert json.loads(greynoise(["8.8.8.8", "-o", "json"], ""))["cached"] is True
    assert "note" not in greynoise(["8.8.8.8", "--fresh"], "")
    assert len(http.urls) == 2
    monkeypatch.setattr(greynoise.intel, "STORE_SECONDS", 0)  # a day later
    assert "note" not in greynoise(["8.8.8.8"], "")
    assert len(http.urls) == 3


def test_targets_from_stdin_defanged_and_several_records(greynoise, http):
    out = greynoise([], "# suspects\n8.8.8[.]8  seen twice\n\n45[.]83[.]64[.]1\n8.8.8.8\n")
    first, second = out.split("\n\n")
    assert first.startswith("indicator       8.8.8.8\n") and second.startswith("indicator       45.83.64.1\n")
    assert http.urls == [API + "8.8.8.8", API + "45.83.64.1"]  # the same address is asked for once
    records = json.loads(greynoise(["-o", "json"], "8.8.8.8\n9.9.9.9\n"))
    assert [(r["indicator"], r["verdict"]) for r in records] == [("8.8.8.8", "clean"), ("9.9.9.9", "unknown")]
    assert json.loads(greynoise(["8.8.8[.]8", "-o", "json"], ""))["indicator"] == "8.8.8.8"


def test_what_is_not_an_ipv4_address_is_refused(greynoise, http):
    for bad in ("2001:4860:4860::8888", "::1", "example.com", "http://8.8.8.8/x",
                "d41d8cd98f00b204e9800998ecf8427e", "a@example.com", "8.8.8", "nonsense"):
        with pytest.raises(ModuleError, match=r"^queryGreyNoise: '.+' is not an IPv4 address$"):
            greynoise([bad], "")
    with pytest.raises(ModuleError, match="'2001:4860:4860::8888' is not an IPv4 address"):
        greynoise(["8.8.8.8", "2001:4860:4860::8888"], "")  # one wrong target stops the list before any request
    with pytest.raises(ModuleError, match=r"^queryGreyNoise: give an IPv4 address \(as an argument, or one per line"):
        greynoise([], "\n# nothing\n")
    with pytest.raises(ModuleError, match="invalid choice"):
        greynoise(["8.8.8.8", "-o", "xml"], "")
    assert http.urls == []


@pytest.mark.parametrize("address", ["10.0.0.1", "192.168.1.10", "172.16.5.4", "127.0.0.1", "169.254.1.1",
                                     "10[.]0[.]0[.]1"])
def test_a_private_address_is_not_sent_to_greynoise(greynoise, http, address):
    shown = address.replace("[.]", ".")
    assert greynoise([address], "") == f"indicator  {shown}\nverdict    unknown: {PRIVATE}\n"
    record = json.loads(greynoise([address, "-o", "json"], ""))
    assert record == {"source": "greynoise", "indicator": shown, "kind": "ipv4", "verdict": "unknown",
                      "summary": PRIVATE, "known_good": False, "cached": False, "data": {}}
    assert json.loads(greynoise([address, "-o", "raw"], "")) is None
    assert http.urls == []


def test_a_private_address_in_a_list_costs_no_request(greynoise, http):
    records = json.loads(greynoise(["192.168.1.10", "8.8.8.8", "-o", "json"], ""))
    assert [(r["indicator"], r["summary"]) for r in records] == [
        ("192.168.1.10", PRIVATE), ("8.8.8.8", "a known service: Google Public DNS")]
    assert http.urls == [API + "8.8.8.8"]


def test_a_rate_limit_without_a_key_says_a_key_raises_the_limit(greynoise, http):
    http.handler = fail(http, 429)
    with pytest.raises(ModuleError) as caught:
        greynoise(["1.1.1.1"], "")
    assert str(caught.value) == ("queryGreyNoise: GreyNoise rate limit reached; try again later "
                                 "(a key raises the limit: settings GREYNOISE_API_KEY)")
    http.handler = fail(http, 429, {"Retry-After": "30"})
    with pytest.raises(ModuleError) as caught:
        greynoise(["1.1.1.1"], "")
    assert str(caught.value) == ("queryGreyNoise: GreyNoise rate limit reached; try again in 30s "
                                 "(a key raises the limit: settings GREYNOISE_API_KEY)")
    assert isinstance(caught.value, greynoise.intel.RateLimited) and caught.value.wait == 30


def test_a_rate_limit_with_a_key_does_not_ask_for_one(greynoise, http, clean_env):
    clean_env.setenv("GREYNOISE_API_KEY", KEY)
    http.handler = fail(http, 429)
    with pytest.raises(ModuleError, match=r"^queryGreyNoise: GreyNoise rate limit reached; try again later$"):
        greynoise(["1.1.1.1"], "")
    http.handler = fail(http, 429, {"Retry-After": "7"})
    with pytest.raises(ModuleError, match=r"^queryGreyNoise: GreyNoise rate limit reached; try again in 7s$"):
        greynoise(["1.1.1.1"], "")


def test_a_rejected_key_names_the_setting(greynoise, http, clean_env):
    clean_env.setenv("GREYNOISE_API_KEY", KEY)
    for code in (401, 403):
        http.handler = fail(http, code)
        with pytest.raises(ModuleError) as caught:
            greynoise(["1.1.1.1"], "")
        assert str(caught.value) == (f"queryGreyNoise: GreyNoise rejected the API key ({code}); "
                                     "set a working one: settings GREYNOISE_API_KEY")


def test_errors_name_the_service_and_never_the_url_or_the_key(greynoise, http, clean_env):
    clean_env.setenv("GREYNOISE_API_KEY", KEY)

    def timeout(request):
        raise TimeoutError

    def slow(request):
        raise urllib.error.URLError(TimeoutError("timed out"))

    def offline(request):
        raise urllib.error.URLError("Name or service not known")

    cases = [
        (fail(http, 401), r"rejected the API key \(401\)"),
        (fail(http, 429, {"Retry-After": "7"}), r"^queryGreyNoise: GreyNoise rate limit reached; try again in 7s$"),
        (fail(http, 500), r"^queryGreyNoise: GreyNoise HTTP 500 error$"),
        (timeout, r"^queryGreyNoise: GreyNoise timed out after 3s$"),
        (slow, r"^queryGreyNoise: GreyNoise timed out after 3s$"),
        (offline, r"^queryGreyNoise: cannot reach GreyNoise: Name or service not known$"),
        (lambda request: FakeResponse(b"<html>busy</html>"), r"^queryGreyNoise: GreyNoise returned an unexpected \(non-JSON\) response$"),
        (lambda request: FakeResponse(["not", "a", "record"]), r"^queryGreyNoise: GreyNoise returned an unexpected response$"),
    ]
    for handler, message in cases:
        http.handler = handler
        with pytest.raises(ModuleError, match=message) as caught:
            greynoise(["1.1.1.1", "--timeout", "3"], "")
        text = str(caught.value)
        assert text.startswith("queryGreyNoise: ")
        assert KEY not in text and "api.greynoise.io" not in text and "://" not in text
    assert all(r.get_header("Key") == KEY for r in http.requests)  # the key went out with every one of them
    # a failure is not an answer: nothing was stored, so the next call asks again
    asked = len(http.urls)
    http.handler = lambda request: FakeResponse(seen("1.1.1.1"))
    assert "malicious" in greynoise(["1.1.1.1"], "")
    assert len(http.urls) == asked + 1


def test_in_a_list_a_failure_is_one_record_and_a_rate_limit_is_waited_out_once(greynoise, http, monkeypatch):
    slept = []
    monkeypatch.setattr(greynoise.intel, "sleep", slept.append)
    limited = {"left": 1}
    normal = http.handler

    def handler(request):
        if request.full_url.endswith("/7.7.7.7"):
            raise http.error(request.full_url, 500)
        if request.full_url.endswith("/8.8.8.8") and limited["left"]:
            limited["left"] -= 1
            raise http.error(request.full_url, 429, headers={"Retry-After": "5"})
        return normal(request)

    http.handler = handler
    out = greynoise(["8.8.8.8", "7.7.7.7", "45.83.64.1"], "")
    assert slept == [6]
    blocks = out.split("\n\n")
    assert "verdict         clean: a known service: Google Public DNS" in blocks[0]
    assert blocks[1] == "indicator  7.7.7.7\nerror      GreyNoise HTTP 500 error"
    assert "verdict         malicious" in blocks[2]
    records = json.loads(greynoise(["7.7.7.7", "45.83.64.1", "-o", "json"], ""))
    assert records[0]["error"] == "GreyNoise HTTP 500 error" and records[0]["verdict"] == "unknown"
    assert "error" not in records[1]


def test_in_a_list_a_used_up_limit_is_one_error_per_address_with_the_hint(greynoise, http, monkeypatch):
    slept = []
    monkeypatch.setattr(greynoise.intel, "sleep", slept.append)
    greynoise(["8.8.8.8"], "")  # stored before the limit ran out
    http.handler = fail(http, 429)
    records = json.loads(greynoise(["8.8.8.8", "1.1.1.1", "10.0.0.1", "-o", "json"], ""))
    assert records[0]["verdict"] == "clean" and records[0]["cached"] is True  # stored answers still work
    assert records[1]["error"] == ("GreyNoise rate limit reached; try again later "
                                   "(a key raises the limit: settings GREYNOISE_API_KEY)")
    assert records[2]["summary"] == PRIVATE
    assert slept == []  # GreyNoise does not say when: a daily limit is not waited for


def test_lookup_is_what_enrich_calls(greynoise, http):
    module = inspect.unwrap(greynoise).__globals__
    record = module["lookup"]("8.8.8[.]8")
    assert (record["source"], record["indicator"], record["kind"], record["verdict"]) == ("greynoise", "8.8.8.8",
                                                                                         "ipv4", "clean")
    assert record["known_good"] is True and record["cached"] is False and record["raw"] == GOOGLE
    assert record["summary"] == "a known service: Google Public DNS"
    assert module["lookup"]("8.8.8.8", timeout=2.0)["cached"] is True
    assert module["lookup"]("8.8.8.8", fresh=True)["cached"] is False
    assert len(http.urls) == 2
    unknown = module["lookup"]("1.2.3.4")
    assert (unknown["verdict"], unknown["summary"], unknown["raw"]) == ("unknown", UNSEEN, None)
    private = module["lookup"]("192.168.0.1")
    assert (private["verdict"], private["summary"], private["data"], private["raw"]) == ("unknown", PRIVATE, {}, None)
    assert len(http.urls) == 3
    for bad in ("2001:db8::1", "example.com", "http://x.example/"):
        with pytest.raises(ModuleError, match="^queryGreyNoise: '.+' is not an IPv4 address$"):
            module["lookup"](bad)


def test_the_constants_enrich_relies_on(greynoise, registry):
    module = inspect.unwrap(greynoise).__globals__
    assert module["SOURCE"] == "greynoise" and module["KINDS"] == ("ipv4",)
    assert module["KEY_VAR"] == "GREYNOISE_API_KEY"
    (setting,) = registry.get("queryGreyNoise").env_settings
    assert (setting.name, setting.label) == ("GREYNOISE_API_KEY", "GreyNoise API key")
    assert "viz.greynoise.io" in setting.description
