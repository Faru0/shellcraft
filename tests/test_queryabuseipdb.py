import inspect
import json
import urllib.parse

import pytest

from shellcraft.modkit import ModuleError
from tests.conftest import FakeHTTP, FakeResponse

KEY = "s3cr3t-abuse-key-0123456789"
CHECK = "https://api.abuseipdb.com/api/v2/check"
NOISY = "118.25.6.39"
USAGE = "Data Center/Web Hosting/Transit"
PRIVATE = "a private address: AbuseIPDB only tracks public ones"


def answer(ip=NOISY, **fields):
    """What AbuseIPDB sends for an address reported a lot; `fields` change it."""
    data = {"ipAddress": ip, "isPublic": True, "ipVersion": 6 if ":" in ip else 4, "isWhitelisted": None,
            "abuseConfidenceScore": 100, "countryCode": "CN", "usageType": USAGE,
            "isp": "Tencent Cloud Computing (Beijing) Co. Ltd", "domain": "tencent.com",
            "hostnames": ["scan.example.net"], "isTor": False, "totalReports": 1234, "numDistinctUsers": 300,
            "lastReportedAt": "2026-11-12T08:15:00+00:00"}
    return {"data": {**data, **fields}}


def quiet(ip, **fields):
    """What AbuseIPDB sends for a public address nobody reported: a normal record, not a 404."""
    return answer(ip, abuseConfidenceScore=0, totalReports=0, numDistinctUsers=0, lastReportedAt=None,
                  countryCode="US", usageType="Fixed Line ISP", isp="Example ISP", domain="isp.example",
                  hostnames=[], **fields)


def asked(url):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


@pytest.fixture
def abuse(registry, http: FakeHTTP, clean_env):
    clean_env.setenv("ABUSEIPDB_API_KEY", KEY)
    run = registry.get("queryAbuseIPDB").run
    answers = {NOISY: answer()}

    def reply(request):
        ip = asked(request.full_url)["ipAddress"]
        return FakeResponse(answers.get(ip) or quiet(ip))

    http.handler = reply
    run.answers = answers
    run.module = inspect.unwrap(run).__globals__
    run.intel = run.module["_intel"]
    return run


def fail(http, code, headers=None):
    def handler(request):
        raise http.error(request.full_url, code, headers=headers)
    return handler


# ── the output ───────────────────────────────────────────────────────────────

def test_a_reported_address_shows_its_score_reports_and_network(abuse):
    out = abuse([NOISY], "")
    assert out.splitlines()[:2] == [
        f"indicator      {NOISY}",
        f"verdict        malicious: abuse score 100, 1,234 reports from 300 users, last 2026-11-12, {USAGE}",
    ]
    assert out.splitlines()[2:] == [
        "score          100", "reports        1234", "users          300", "last_reported  2026-11-12",
        "country        CN", f"usage          {USAGE}", "isp            Tencent Cloud Computing (Beijing) Co. Ltd",
        "domain         tencent.com", "hostnames      scan.example.net", "tor            no", "whitelisted    no",
        "days           90",
    ]
    assert "note" not in out and "public" not in out  # a new answer; `public` is in the json only
    assert KEY not in out


def test_json_is_the_record_without_raw(abuse):
    record = json.loads(abuse([NOISY, "-o", "json"], ""))
    assert set(record) == {"source", "indicator", "kind", "verdict", "summary", "known_good", "cached", "data"}
    assert (record["source"], record["indicator"], record["kind"]) == ("abuseipdb", NOISY, "ipv4")
    assert (record["verdict"], record["known_good"], record["cached"]) == ("malicious", False, False)
    assert record["data"] == {
        "score": 100, "reports": 1234, "users": 300, "last_reported": "2026-11-12", "country": "CN", "usage": USAGE,
        "isp": "Tencent Cloud Computing (Beijing) Co. Ltd", "domain": "tencent.com",
        "hostnames": ["scan.example.net"], "tor": False, "whitelisted": False, "public": True, "days": 90,
    }
    assert KEY not in json.dumps(record)


def test_raw_is_the_answer_of_abuseipdb(abuse):
    assert json.loads(abuse([NOISY, "-o", "raw"], "")) == answer()
    both = json.loads(abuse([NOISY, "9.9.9.9", "-o", "raw"], ""))
    assert both == [answer(), quiet("9.9.9.9")]


# ── the request ──────────────────────────────────────────────────────────────

def test_the_request_is_the_check_endpoint_with_the_key_in_a_header(abuse, http):
    abuse([NOISY], "")
    assert http.urls == [f"{CHECK}?ipAddress={NOISY}&maxAgeInDays=90"]
    request = http.requests[0]
    assert request.get_method() == "GET"
    assert request.get_header("Key") == KEY
    assert request.get_header("Accept") == "application/json"
    assert KEY not in request.full_url


def test_an_ipv6_address_is_encoded_in_the_query(abuse, http):
    record = json.loads(abuse(["2606:4700:4700::1111", "-o", "json"], ""))
    assert http.urls == [f"{CHECK}?ipAddress=2606%3A4700%3A4700%3A%3A1111&maxAgeInDays=90"]
    assert asked(http.urls[0]) == {"ipAddress": "2606:4700:4700::1111", "maxAgeInDays": "90"}
    assert (record["indicator"], record["kind"], record["verdict"]) == ("2606:4700:4700::1111", "ipv6", "clean")
    # the long spelling is the same address: the same stored answer, no second request
    again = json.loads(abuse(["2606:4700:4700:0:0:0:0:1111", "-o", "json"], ""))
    assert again["indicator"] == "2606:4700:4700::1111" and again["cached"] is True
    assert len(http.urls) == 1


# ── the key ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", [None, "", "   "])
def test_without_a_key_it_says_where_to_get_one_and_asks_nothing(abuse, http, clean_env, value):
    if value is None:
        clean_env.delenv("ABUSEIPDB_API_KEY")
    else:
        clean_env.setenv("ABUSEIPDB_API_KEY", value)
    message = (r'^queryAbuseIPDB: no AbuseIPDB API key\. A free account at https://www\.abuseipdb\.com gives you '
               r'one, under "API"\. Then run: settings ABUSEIPDB_API_KEY$')
    with pytest.raises(ModuleError, match=message):
        abuse([NOISY], "")
    with pytest.raises(ModuleError, match=message):  # one error for the list, not one record per target
        abuse([NOISY, "9.9.9.9", "10.0.0.5"], "")
    with pytest.raises(ModuleError, match=message):  # what enrich gets
        abuse.module["lookup"](NOISY)
    assert http.urls == []


def test_a_bad_target_or_option_is_reported_before_the_missing_key(abuse, http, clean_env):
    clean_env.delenv("ABUSEIPDB_API_KEY")
    with pytest.raises(ModuleError, match="give an IP address"):
        abuse([], "")
    with pytest.raises(ModuleError, match="is not an IP address"):
        abuse(["example.com"], "")
    with pytest.raises(ModuleError, match="--days takes 1 to 365"):
        abuse([NOISY, "--days", "0"], "")
    assert http.urls == []


# ── the verdict ──────────────────────────────────────────────────────────────

FEW = {"totalReports": 2, "numDistinctUsers": 2, "lastReportedAt": "2026-10-30T23:59:59+00:00"}
NONE = {"totalReports": 0, "numDistinctUsers": 0, "lastReportedAt": None}
MANY = "1,234 reports from 300 users, last 2026-11-12"


@pytest.mark.parametrize(("fields", "verdict", "summary", "known_good"), [
    # clean: below 25
    ({"abuseConfidenceScore": 0, **NONE}, "clean", "no reports in the last 90 days", False),
    ({"abuseConfidenceScore": 0, **FEW}, "clean", "abuse score 0, 2 reports from 2 users, last 2026-10-30", False),
    ({"abuseConfidenceScore": 3, **FEW}, "clean", "abuse score 3, 2 reports from 2 users, last 2026-10-30", False),
    ({"abuseConfidenceScore": 24}, "clean", f"abuse score 24, {MANY}", False),
    ({"abuseConfidenceScore": 0, "isTor": True, **NONE}, "clean", "no reports in the last 90 days", False),
    # suspicious: 25 to 74
    ({"abuseConfidenceScore": 25}, "suspicious", f"abuse score 25, {MANY}, {USAGE}", False),
    ({"abuseConfidenceScore": 74}, "suspicious", f"abuse score 74, {MANY}, {USAGE}", False),
    ({"abuseConfidenceScore": 30, "isTor": True, "usageType": None, "totalReports": 1, "numDistinctUsers": 1},
     "suspicious", "abuse score 30, 1 report from 1 user, last 2026-11-12, Tor exit", False),
    # malicious: 75 and up
    ({"abuseConfidenceScore": 75}, "malicious", f"abuse score 75, {MANY}, {USAGE}", False),
    ({"abuseConfidenceScore": 100}, "malicious", f"abuse score 100, {MANY}, {USAGE}", False),
    ({"abuseConfidenceScore": 100, "isTor": True}, "malicious", f"abuse score 100, {MANY}, Tor exit, {USAGE}", False),
    ({"abuseConfidenceScore": 100, "usageType": "", "lastReportedAt": None}, "malicious",
     "abuse score 100, 1,234 reports from 300 users", False),
    # the whitelist wins over the score
    ({"isWhitelisted": True, "abuseConfidenceScore": 100}, "clean",
     f"on AbuseIPDB's whitelist, abuse score 100, {MANY}", True),
    ({"isWhitelisted": True, "abuseConfidenceScore": 0, **FEW}, "clean",
     "on AbuseIPDB's whitelist, abuse score 0, 2 reports from 2 users, last 2026-10-30", True),
    ({"isWhitelisted": True, "abuseConfidenceScore": 0, **NONE}, "clean", "on AbuseIPDB's whitelist", True),
    ({"isWhitelisted": False, "abuseConfidenceScore": 80}, "malicious", f"abuse score 80, {MANY}, {USAGE}", False),
    # not public, says AbuseIPDB: that wins over everything
    ({"isPublic": False, "abuseConfidenceScore": 100, "isWhitelisted": True}, "unknown", PRIVATE, False),
])
def test_the_verdict_follows_the_score_the_whitelist_and_public(abuse, fields, verdict, summary, known_good):
    abuse.answers["203.0.114.7"] = answer("203.0.114.7", **fields)
    record = json.loads(abuse(["203.0.114.7", "-o", "json"], ""))
    assert (record["verdict"], record["summary"], record["known_good"]) == (verdict, summary, known_good)
    assert record["data"]["score"] == fields.get("abuseConfidenceScore", 100)
    assert record["data"]["tor"] is bool(fields.get("isTor"))
    assert record["data"]["whitelisted"] is bool(fields.get("isWhitelisted"))
    assert record["data"]["public"] is fields.get("isPublic", True)


def test_the_thresholds_are_module_constants(abuse):
    assert abuse.module["MALICIOUS_AT"] == 75 and abuse.module["SUSPICIOUS_AT"] == 25


def test_a_quiet_address_reads_well_in_the_table(abuse):
    out = abuse(["9.9.9.9"], "")
    assert out.splitlines()[:2] == ["indicator    9.9.9.9", "verdict      clean: no reports in the last 90 days"]
    assert "score        0" in out and "reports      0" in out and "last_reported" not in out


@pytest.mark.parametrize("address", ["10.0.0.5", "192.168.1.1", "127.0.0.1", "169.254.10.10", "fe80::1", "fd00::5",
                                     "2001:db8::1"])
def test_a_private_address_is_answered_without_a_request(abuse, http, address):
    assert abuse([address], "") == f"indicator  {address}\nverdict    unknown: {PRIVATE}\n"
    record = json.loads(abuse([address, "-o", "json"], ""))
    assert (record["verdict"], record["summary"], record["known_good"]) == ("unknown", PRIVATE, False)
    assert record["data"] == {"public": False} and record["cached"] is False
    assert json.loads(abuse([address, "-o", "raw"], "")) is None
    assert http.urls == []


def test_private_and_public_addresses_in_one_list(abuse, http):
    records = json.loads(abuse(["10.0.0.5", NOISY, "-o", "json"], ""))
    assert [r["verdict"] for r in records] == ["unknown", "malicious"]
    assert [asked(u)["ipAddress"] for u in http.urls] == [NOISY]


def test_not_known_is_an_answer_and_is_stored(abuse, http):
    http.handler = fail(http, 404)
    assert abuse(["1.1.1.1"], "") == "indicator  1.1.1.1\nverdict    unknown: AbuseIPDB has nothing on it\n"
    again = abuse(["1.1.1.1"], "")
    assert "verdict    unknown: AbuseIPDB has nothing on it" in again and "a stored answer" in again
    assert len(http.urls) == 1  # the second answer came from the store
    record = json.loads(abuse(["1.1.1.1", "-o", "json"], ""))
    assert (record["verdict"], record["cached"], record["data"]) == ("unknown", True, {})


# ── the answer store ─────────────────────────────────────────────────────────

def test_answers_are_stored_for_a_day_and_fresh_asks_again(abuse, http, monkeypatch):
    abuse([NOISY], "")
    again = abuse([NOISY], "")
    assert len(http.urls) == 1
    assert "note           a stored answer, less than a day old (--fresh asks again)" in again
    assert json.loads(abuse([NOISY, "-o", "json"], ""))["cached"] is True
    assert json.loads(abuse([NOISY, "-o", "raw"], "")) == answer()
    abuse([NOISY, "--fresh"], "")
    assert len(http.urls) == 2
    monkeypatch.setattr(abuse.intel, "STORE_SECONDS", 0)  # a day later
    assert "note" not in abuse([NOISY], "")
    assert len(http.urls) == 3


def test_the_key_is_not_in_the_store(abuse):
    abuse([NOISY], "")
    kept = list((abuse.intel.store_dir() / "abuseipdb").glob("*.json"))
    assert len(kept) == 1 and KEY not in kept[0].read_text(encoding="utf-8")


# ── --days ───────────────────────────────────────────────────────────────────

def test_days_sets_the_window_in_the_request_and_in_the_record(abuse, http):
    record = json.loads(abuse(["9.9.9.9", "--days", "30", "-o", "json"], ""))
    assert http.urls == [f"{CHECK}?ipAddress=9.9.9.9&maxAgeInDays=30"]
    assert record["data"]["days"] == 30 and record["summary"] == "no reports in the last 30 days"
    assert "days         30" in abuse(["9.9.9.9", "--days", "30"], "")
    assert json.loads(abuse(["9.9.9.9", "--days", "1", "-o", "json"], ""))["summary"] == "no reports in the last day"
    abuse(["9.9.9.9", "--days", "365"], "")
    assert [asked(u)["maxAgeInDays"] for u in http.urls] == ["30", "1", "365"]


def test_a_30_day_answer_and_a_90_day_answer_are_stored_apart(abuse, http):
    def reply(request):  # more reports the further back you look
        query = asked(request.full_url)
        days = int(query["maxAgeInDays"])
        return FakeResponse(answer(query["ipAddress"], abuseConfidenceScore=days, totalReports=days * 10))

    http.handler = reply

    def ask(*options):
        return json.loads(abuse([NOISY, "-o", "json", *options], ""))

    wide, narrow = ask(), ask("--days", "30")
    assert (wide["data"]["score"], wide["data"]["days"], wide["verdict"]) == (90, 90, "malicious")
    assert (narrow["data"]["score"], narrow["data"]["days"], narrow["verdict"]) == (30, 30, "suspicious")
    assert [asked(u)["maxAgeInDays"] for u in http.urls] == ["90", "30"]
    wide, narrow = ask(), ask("--days", "30")  # both from the store now, each its own answer
    assert (wide["data"]["reports"], wide["cached"]) == (900, True)
    assert (narrow["data"]["reports"], narrow["cached"]) == (300, True)
    assert ask("--days", "90")["cached"] is True  # --days 90 is the plain question
    assert len(http.urls) == 2
    assert len(list((abuse.intel.store_dir() / "abuseipdb").glob("*.json"))) == 2
    ask("--days", "30", "--fresh")  # asks for that window again, the other one stays
    assert [asked(u)["maxAgeInDays"] for u in http.urls] == ["90", "30", "30"]


@pytest.mark.parametrize("days", ["0", "366", "-1", "1000"])
def test_days_out_of_range_is_refused(abuse, http, days):
    with pytest.raises(ModuleError, match=r"^queryAbuseIPDB: --days takes 1 to 365$"):
        abuse([NOISY, f"--days={days}"], "")
    with pytest.raises(ModuleError, match=r"^queryAbuseIPDB: --days takes 1 to 365$"):
        abuse.module["lookup"](NOISY, days=int(days))
    assert http.urls == []


def test_days_must_be_a_whole_number(abuse, http):
    for bad in ("week", "7.5", ""):
        with pytest.raises(ModuleError, match=r"^queryAbuseIPDB: argument --days: invalid int value"):
            abuse([NOISY, f"--days={bad}"], "")
    with pytest.raises(ModuleError, match="--days takes 1 to 365"):
        abuse.module["lookup"](NOISY, days=7.5)
    assert http.urls == []


# ── the targets ──────────────────────────────────────────────────────────────

def test_targets_from_stdin_defanged_and_several_records(abuse, http):
    out = abuse([], f"# from the firewall\n118[.]25[.]6[.]39  seen twice\n\n9.9.9[.]9\n{NOISY}\n")
    first, second = out.split("\n\n")
    assert first.startswith(f"indicator      {NOISY}\n") and second.startswith("indicator    9.9.9.9\n")
    assert [asked(u)["ipAddress"] for u in http.urls] == [NOISY, "9.9.9.9"]  # the repeated one is asked once
    records = json.loads(abuse(["-o", "json"], f"{NOISY}\n9.9.9.9\n"))
    assert [(r["indicator"], r["verdict"]) for r in records] == [(NOISY, "malicious"), ("9.9.9.9", "clean")]
    assert json.loads(abuse(["9[.]9[.]9[.]9", "-o", "json"], ""))["indicator"] == "9.9.9.9"


def test_what_is_not_an_ip_address_is_refused(abuse, http):
    for bad in ("example.com", "http://118.25.6.39/x", "d41d8cd98f00b204e9800998ecf8427e", "a@example.com",
                "118.25.6", "118.25.6.39/24", "nonsense"):
        with pytest.raises(ModuleError, match=r"^queryAbuseIPDB: '.*' is not an IP address$"):
            abuse([bad], "")
    with pytest.raises(ModuleError, match=r"^queryAbuseIPDB: give an IP address "):
        abuse([], "\n# nothing\n")
    with pytest.raises(ModuleError, match="invalid choice"):
        abuse([NOISY, "-o", "xml"], "")
    assert http.urls == []


# ── errors ───────────────────────────────────────────────────────────────────

def test_errors_name_the_service_and_never_the_key_or_the_url(abuse, http):
    def error(pattern, *options):
        with pytest.raises(ModuleError, match=pattern) as caught:
            abuse(["1.1.1.1", *options], "")
        assert KEY not in str(caught.value) and "api.abuseipdb.com" not in str(caught.value)

    http.handler = fail(http, 401)
    error(r"^queryAbuseIPDB: AbuseIPDB rejected the API key \(401\); set a working one: settings ABUSEIPDB_API_KEY$")
    http.handler = fail(http, 403)
    error(r"^queryAbuseIPDB: AbuseIPDB rejected the API key \(403\); set a working one: settings ABUSEIPDB_API_KEY$")
    http.handler = fail(http, 429, {"Retry-After": "29000"})  # the daily limit is used up
    error(r"^queryAbuseIPDB: AbuseIPDB rate limit reached; try again in 29000s$")
    http.handler = fail(http, 429)
    error(r"^queryAbuseIPDB: AbuseIPDB rate limit reached; try again later$")
    http.handler = fail(http, 422)
    error(r"^queryAbuseIPDB: AbuseIPDB HTTP 422 error$")
    http.handler = fail(http, 500)
    error(r"^queryAbuseIPDB: AbuseIPDB HTTP 500 error$")

    def timeout(request):
        raise TimeoutError
    http.handler = timeout
    error(r"^queryAbuseIPDB: AbuseIPDB timed out after 3s$", "--timeout", "3")
    http.handler = lambda request: FakeResponse(b"<html>busy</html>")
    error(r"^queryAbuseIPDB: AbuseIPDB returned an unexpected \(non-JSON\) response$")
    for odd in ({"errors": [{"detail": "The ip address must be a valid IPv4 or IPv6 address.", "status": 422}]},
                {"data": None}, ["data"]):
        http.handler = lambda request, odd=odd: FakeResponse(odd)
        error(r"^queryAbuseIPDB: AbuseIPDB returned an unexpected response$")


def test_a_failure_is_not_stored(abuse, http):
    normal = http.handler
    for broken in (fail(http, 500), fail(http, 429, {"Retry-After": "29000"}), lambda request: FakeResponse({})):
        http.handler = broken
        with pytest.raises(ModuleError):
            abuse([NOISY], "")
    http.handler = normal
    out = abuse([NOISY], "")
    assert "verdict        malicious" in out and "note" not in out
    assert len(http.urls) == 4


def test_in_a_list_a_failure_is_one_record_and_a_rate_limit_is_waited_out_once(abuse, http, monkeypatch):
    slept = []
    monkeypatch.setattr(abuse.intel, "sleep", slept.append)
    limited = {"left": 1}
    normal = http.handler

    def handler(request):
        ip = asked(request.full_url)["ipAddress"]
        if ip == "9.9.9.9":
            raise http.error(request.full_url, 500)
        if ip == NOISY and limited["left"]:
            limited["left"] -= 1
            raise http.error(request.full_url, 429, headers={"Retry-After": "5"})
        return normal(request)

    http.handler = handler
    out = abuse([NOISY, "9.9.9.9", "8.8.4.4"], "")
    assert slept == [6]
    blocks = out.split("\n\n")
    assert "verdict        malicious" in blocks[0]
    assert blocks[1] == "indicator  9.9.9.9\nerror      AbuseIPDB HTTP 500 error"
    assert "indicator    8.8.4.4\nverdict      clean: no reports in the last 90 days" in blocks[2]
    failed = json.loads(abuse(["9.9.9.9", "8.8.4.4", "-o", "json"], ""))[0]
    assert failed["error"] == "AbuseIPDB HTTP 500 error"
    assert (failed["source"], failed["indicator"], failed["kind"], failed["verdict"]) == ("abuseipdb", "9.9.9.9",
                                                                                         "ipv4", "unknown")


def test_in_a_list_a_used_up_daily_limit_is_not_waited_for(abuse, http, monkeypatch):
    slept = []
    monkeypatch.setattr(abuse.intel, "sleep", slept.append)
    http.handler = fail(http, 429, {"Retry-After": "29000"})
    records = json.loads(abuse(["1.1.1.1", "9.9.9.9", "10.0.0.5", "-o", "json"], ""))
    assert slept == []
    assert [r.get("error") for r in records] == ["AbuseIPDB rate limit reached; try again in 29000s"] * 2 + [None]
    assert records[2]["summary"] == PRIVATE  # the private one needs no check, so the limit does not touch it
    assert KEY not in json.dumps(records)


# ── what enrich uses ─────────────────────────────────────────────────────────

def test_lookup_is_what_enrich_calls(abuse, http, registry):
    module = abuse.module
    record = module["lookup"]("118[.]25[.]6[.]39")
    assert (record["source"], record["indicator"], record["kind"], record["verdict"]) == ("abuseipdb", NOISY, "ipv4",
                                                                                         "malicious")
    assert record["known_good"] is False and record["cached"] is False and record["raw"] == answer()
    assert record["data"]["days"] == 90
    assert module["lookup"](NOISY, timeout=3.0)["cached"] is True
    assert module["lookup"](NOISY, fresh=True)["cached"] is False
    assert module["lookup"](NOISY, days=7)["data"]["days"] == 7
    assert [asked(u)["maxAgeInDays"] for u in http.urls] == ["90", "90", "7"]
    assert module["lookup"]("192.168.0.9")["summary"] == PRIVATE
    assert module["SOURCE"] == "abuseipdb" and module["KINDS"] == ("ipv4", "ipv6")
    assert module["KEY_VAR"] == "ABUSEIPDB_API_KEY"
    assert [s.name for s in registry.get("queryAbuseIPDB").env_settings] == ["ABUSEIPDB_API_KEY"]
    for bad in ("example.com", "http://x.example/", "d41d8cd98f00b204e9800998ecf8427e"):
        with pytest.raises(ModuleError, match=r"^queryAbuseIPDB: '.*' is not an IP address$"):
            module["lookup"](bad)
