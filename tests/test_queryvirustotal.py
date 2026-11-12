import inspect
import json
import urllib.error

import pytest

from shellcraft.modkit import EnvSetting, ModuleError
from tests.conftest import FakeHTTP, FakeResponse

API = "https://www.virustotal.com/api/v3/"
GUI = "https://www.virustotal.com/gui/"
KEY = "vt-secret-key-0123456789"
CHECKED = 1793523600  # 2026-11-01
WHAT = r"an IP address, a domain name, a URL or a file hash \(MD5, SHA-1, SHA-256\)"
LIMIT = ("VirusTotal rate limit reached (a free key allows 4 lookups a minute, 500 a day); "
         "try again in a minute")

MD5 = "44d88612fea8a8f36de82e1278abb02f"
SHA1 = "3395856ce81f2b7382dee72602f798b642f14140"
SHA256 = "275a021bbfb6489e54d471899f7db9d1663fc695ec2fe2a2c4538aabf651fd0f"
URL = "https://evil.example/a"
URL_ID = "aHR0cHM6Ly9ldmlsLmV4YW1wbGUvYQ"  # the URL in base64 without the padding
URL_SHA = "ab" * 32  # VirusTotal's own name for the URL


def report(kind, ident, *, malicious=0, suspicious=0, harmless=70, undetected=24, timeout=0, **attributes):
    stats = {"malicious": malicious, "suspicious": suspicious, "harmless": harmless, "undetected": undetected,
             "timeout": timeout}
    return {"data": {"id": ident, "type": kind, "links": {"self": API + "x"},
                     "attributes": {"last_analysis_stats": stats, "last_analysis_date": CHECKED, "reputation": 0,
                                    "tags": [], **attributes}}}


IP = report("ip_address", "8.8.8.8", reputation=530, as_owner="GOOGLE", asn=15169, country="US",
            network="8.8.8.0/24")
DOMAIN = report("domain", "evil.example", malicious=12, suspicious=2, harmless=60, undetected=20, reputation=-40,
                tags=["dga", "nxdomain"], registrar="Example Registrar, Inc.", creation_date=1551517200,
                categories={"Sophos": "phishing", "Forcepoint": "malware", "BitDefender": "phishing"})
FILE = report("file", SHA256, malicious=62, harmless=0, undetected=8, reputation=3604, tags=["attachment"],
              meaningful_name="eicar.com", type_description="Text", size=68, first_submission_date=1148288400,
              popular_threat_classification={"suggested_threat_label": "virus.eicar/test"},
              names=["eicar.com", "eicar.txt", "eicar.com.txt", "test.txt", "eicar", "sixth", "seventh"],
              sha256=SHA256, md5=MD5, sha1=SHA1)
LINK = report("url", URL_SHA, malicious=1, url=URL, last_final_url="https://evil.example/landing", title="Sign in",
              categories={"Sophos": "phishing"})


@pytest.fixture
def vt(registry, http: FakeHTTP, clean_env):
    clean_env.setenv("VIRUSTOTAL_API_KEY", KEY)
    run = registry.get("queryVirusTotal").run
    answers = {API + "ip_addresses/8.8.8.8": IP, API + "domains/evil.example": DOMAIN, API + f"files/{MD5}": FILE,
               API + f"files/{SHA1}": FILE, API + f"files/{SHA256}": FILE, API + f"urls/{URL_ID}": LINK}

    def answer(request):
        if request.full_url not in answers:
            raise http.error(request.full_url, 404, {"error": {"code": "NotFoundError"}})
        return FakeResponse(answers[request.full_url])

    http.handler = answer
    run.answers = answers
    run.env = clean_env
    run.module = inspect.unwrap(run).__globals__
    run.intel = run.module["_intel"]
    return run


def fail(http, code, headers=None):
    def handler(request):
        raise http.error(request.full_url, code, headers=headers)
    return handler


# ── the four endpoints ───────────────────────────────────────────────────────

def test_an_ip_shows_what_the_engines_say_and_who_owns_it(vt, http):
    out = vt(["8.8.8.8"], "")
    assert http.urls == [API + "ip_addresses/8.8.8.8"]
    assert http.requests[0].get_header("X-apikey") == KEY
    assert http.requests[0].get_header("Accept") == "application/json"
    assert out.splitlines()[:2] == ["indicator      8.8.8.8",
                                    "verdict        clean: none of 94 engines flag it, last checked 2026-11-01"]
    assert out.splitlines()[2:7] == ["malicious      0", "suspicious     0", "harmless       70", "undetected     24",
                                     "engines        94"]
    assert "reputation     530" in out and "last_analysis  2026-11-01" in out
    assert "as_owner       GOOGLE" in out and "asn            15169" in out
    assert "country        US" in out and "network        8.8.8.0/24" in out
    assert out.splitlines()[-1] == f"link           {GUI}ip-address/8.8.8.8"
    assert "tags" not in out  # it has none
    assert "note" not in out  # a new answer, not a stored one


def test_an_ipv6_address_goes_to_the_same_endpoint(vt, http):
    vt.answers[API + "ip_addresses/2001:4860:4860::8888"] = IP
    out = vt(["2001:4860:4860:0:0:0:0:8888"], "")
    assert http.urls == [API + "ip_addresses/2001:4860:4860::8888"]
    assert f"link           {GUI}ip-address/2001:4860:4860::8888" in out


def test_a_domain_shows_registrar_age_and_each_category_once(vt, http):
    out = vt(["Evil.Example"], "")
    assert http.urls == [API + "domains/evil.example"]
    assert http.requests[0].get_header("X-apikey") == KEY
    assert out.splitlines()[:2] == [
        "indicator      evil.example",
        "verdict        malicious: 12 of 94 engines call it malicious, 2 suspicious, last checked 2026-11-01"]
    assert "reputation     -40" in out and "tags           dga, nxdomain" in out
    assert "registrar      Example Registrar, Inc." in out and "created        2019-03-02" in out
    assert "categories     phishing, malware\n" in out
    assert f"link           {GUI}domain/evil.example" in out


@pytest.mark.parametrize(("typed", "kind"), [(MD5, "md5"), (SHA1, "sha1"), (SHA256, "sha256"),
                                             (SHA256.upper(), "sha256")])
def test_a_file_hash_shows_the_file_and_its_threat_label(vt, http, typed, kind):
    record = json.loads(vt([typed, "-o", "json"], ""))
    assert http.urls == [API + f"files/{typed.lower()}"]
    assert http.requests[0].get_header("X-apikey") == KEY
    assert (record["indicator"], record["kind"], record["verdict"]) == (typed.lower(), kind, "malicious")
    assert record["summary"] == "62 of 70 engines call it malicious, virus.eicar/test, last checked 2026-11-01"
    assert record["data"] == {
        "malicious": 62, "suspicious": 0, "harmless": 0, "undetected": 8, "engines": 70, "reputation": 3604,
        "tags": ["attachment"], "last_analysis": "2026-11-01", "threat_label": "virus.eicar/test",
        "name": "eicar.com", "type": "Text", "size": 68, "first_seen": "2006-05-22",
        "names": ["eicar.com", "eicar.txt", "eicar.com.txt", "test.txt", "eicar"],  # the first five
        "sha256": SHA256, "md5": MD5, "sha1": SHA1, "link": f"{GUI}file/{typed.lower()}"}


def test_the_file_fields_in_the_table(vt):
    out = vt([MD5], "")
    assert out.splitlines()[:2] == [
        f"indicator      {MD5}",
        "verdict        malicious: 62 of 70 engines call it malicious, virus.eicar/test, last checked 2026-11-01"]
    for line in ("threat_label   virus.eicar/test", "name           eicar.com", "type           Text",
                 "size           68", "first_seen     2006-05-22",
                 "names          eicar.com, eicar.txt, eicar.com.txt, test.txt, eicar", f"sha256         {SHA256}",
                 f"md5            {MD5}", f"sha1           {SHA1}", f"link           {GUI}file/{MD5}"):
        assert line + "\n" in out


def test_a_url_is_asked_for_by_its_id(vt, http):
    out = vt([URL], "")
    assert http.urls == [API + f"urls/{URL_ID}"]
    assert http.requests[0].get_header("X-apikey") == KEY
    assert out.splitlines()[:2] == [
        f"indicator      {URL}",
        "verdict        suspicious: 1 of 95 engines calls it malicious, last checked 2026-11-01"]
    assert f"url            {URL}" in out and "final_url      https://evil.example/landing" in out
    assert "title          Sign in" in out and "categories     phishing" in out
    assert f"link           {GUI}url/{URL_SHA}" in out  # the page under VirusTotal's own name for the URL


def test_a_defanged_url_reaches_the_urls_endpoint_refanged(vt, http):
    record = json.loads(vt(["hxxps://evil[.]example/a", "-o", "json"], ""))
    assert http.urls == [API + "urls/aHR0cHM6Ly9ldmlsLmV4YW1wbGUvYQ"]
    assert (record["indicator"], record["kind"]) == ("https://evil.example/a", "url")


def test_a_url_id_has_no_padding_and_only_characters_that_fit_a_path(vt, http):
    for url, ident in (("http://example.com/login?next=/a", "aHR0cDovL2V4YW1wbGUuY29tL2xvZ2luP25leHQ9L2E"),
                       ("https://evil.example/a?x=???>>>", "aHR0cHM6Ly9ldmlsLmV4YW1wbGUvYT94PT8_Pz4-Pg")):
        vt.answers[API + f"urls/{ident}"] = report("url", "", url=url)  # an answer without VirusTotal's own name
        record = json.loads(vt([url, "-o", "json"], ""))
        assert http.urls[-1] == API + f"urls/{ident}"
        assert record["indicator"] == url and record["data"]["link"] == f"{GUI}url/{ident}"


def test_only_reports_are_read_nothing_is_sent_in(vt, http):
    vt([MD5, URL, "8.8.8.8", "evil.example", "new.example"], "")
    assert len(http.requests) == 5
    assert all(r.get_method() == "GET" and r.data is None for r in http.requests)


# ── the output ───────────────────────────────────────────────────────────────

def test_json_is_the_record_without_raw(vt):
    record = json.loads(vt(["8.8.8.8", "-o", "json"], ""))
    assert set(record) == {"source", "indicator", "kind", "verdict", "summary", "known_good", "cached", "data"}
    assert (record["source"], record["indicator"], record["kind"]) == ("virustotal", "8.8.8.8", "ipv4")
    assert (record["verdict"], record["known_good"], record["cached"]) == ("clean", False, False)
    assert record["summary"] == "none of 94 engines flag it, last checked 2026-11-01"
    assert record["data"] == {
        "malicious": 0, "suspicious": 0, "harmless": 70, "undetected": 24, "engines": 94, "reputation": 530,
        "tags": [], "last_analysis": "2026-11-01", "as_owner": "GOOGLE", "asn": 15169, "country": "US",
        "network": "8.8.8.0/24", "link": f"{GUI}ip-address/8.8.8.8"}


def test_raw_is_the_whole_report(vt):
    assert json.loads(vt(["8.8.8.8", "-o", "raw"], "")) == IP
    assert json.loads(vt(["8.8.8.8", "evil.example", "-o", "raw"], "")) == [IP, DOMAIN]


# ── the verdict ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("stats", "verdict", "summary"), [
    ({"malicious": 0}, "clean", "none of 94 engines flag it, last checked 2026-11-01"),
    ({"malicious": 1}, "suspicious", "1 of 95 engines calls it malicious, last checked 2026-11-01"),
    ({"malicious": 4}, "suspicious", "4 of 98 engines call it malicious, last checked 2026-11-01"),
    ({"malicious": 5}, "malicious", "5 of 99 engines call it malicious, last checked 2026-11-01"),
    ({"suspicious": 1}, "clean", "1 of 95 engines calls it suspicious, last checked 2026-11-01"),
    ({"suspicious": 2}, "suspicious", "2 of 96 engines call it suspicious, last checked 2026-11-01"),
    ({"malicious": 4, "suspicious": 3}, "suspicious",
     "4 of 101 engines call it malicious, 3 suspicious, last checked 2026-11-01"),
    ({"malicious": 5, "suspicious": 1}, "malicious",
     "5 of 100 engines call it malicious, 1 suspicious, last checked 2026-11-01"),
    ({"harmless": 0, "undetected": 0, "timeout": 3}, "clean", "none of 3 engines flag it, last checked 2026-11-01"),
    ({"harmless": 0, "undetected": 0, "last_analysis_date": None}, "unknown", "no engine has checked it yet"),
])
def test_the_verdict_at_its_thresholds(vt, stats, verdict, summary):
    vt.answers[API + "domains/judged.example"] = report("domain", "judged.example", **stats)
    record = json.loads(vt(["judged.example", "-o", "json"], ""))
    assert (record["verdict"], record["summary"]) == (verdict, summary)
    counted = {"malicious": 0, "suspicious": 0, "harmless": 70, "undetected": 24, "timeout": 0} | stats
    assert record["data"]["engines"] == sum(counted[name] for name in ("malicious", "suspicious", "harmless",
                                                                         "undetected", "timeout"))
    assert vt.module["MALICIOUS_AT"] == 5


def test_a_report_that_lacks_what_is_expected_is_unknown_not_a_crash(vt):
    for odd in ({"data": {"id": "x.example", "type": "domain"}}, {"data": None}, [], {"data": {"attributes": {
            "last_analysis_stats": {"malicious": "many", "harmless": True}, "tags": "none", "categories": []}}}):
        vt.answers[API + "domains/odd.example"] = odd
        record = json.loads(vt(["odd.example", "-o", "json", "--fresh"], ""))
        assert (record["verdict"], record["summary"]) == ("unknown", "no engine has checked it yet")
        assert record["data"]["engines"] == 0 and record["data"]["link"] == f"{GUI}domain/odd.example"


def test_never_seen_is_an_answer_and_is_stored(vt, http):
    assert vt(["new.example"], "") == ("indicator  new.example\n"
                                       "verdict    unknown: VirusTotal has never seen it\n")
    asked = len(http.urls)
    again = json.loads(vt(["new.example", "-o", "json"], ""))
    assert (again["verdict"], again["summary"], again["cached"]) == ("unknown", "VirusTotal has never seen it", True)
    assert again["data"] == {}
    assert len(http.urls) == asked  # the second answer came from the store
    assert json.loads(vt(["new.example", "-o", "raw"], "")) is None


# ── the store ────────────────────────────────────────────────────────────────

def test_answers_are_stored_for_a_day_and_fresh_asks_again(vt, http, monkeypatch):
    vt(["8.8.8.8"], "")
    again = vt(["8.8.8.8"], "")
    assert len(http.urls) == 1
    assert "note           a stored answer, less than a day old (--fresh asks again)" in again
    assert json.loads(vt(["8.8.8.8", "-o", "json"], ""))["cached"] is True
    vt(["8.8.8.8", "--fresh"], "")
    assert len(http.urls) == 2
    monkeypatch.setattr(vt.intel, "STORE_SECONDS", 0)  # a day later
    assert "note" not in vt(["8.8.8.8"], "")
    assert len(http.urls) == 3


def test_urls_that_differ_only_in_case_are_stored_apart(vt, http):
    assert "suspicious" in vt([URL], "")
    assert "VirusTotal has never seen it" in vt(["https://evil.example/A"], "")
    assert len(http.urls) == 2 and http.urls[0] != http.urls[1]
    assert "suspicious" in vt([URL], "") and "never seen" in vt(["https://evil.example/A"], "")
    assert len(http.urls) == 2  # both from the store


# ── what it takes ────────────────────────────────────────────────────────────

def test_a_private_address_is_answered_without_asking(vt, http):
    for address in ("10.0.0.5", "192.168.1.1", "127.0.0.1", "fe80::1"):
        record = json.loads(vt([address, "-o", "json"], ""))
        assert (record["verdict"], record["summary"]) == ("unknown",
                                                          "a private address: VirusTotal only knows the public internet")
    assert http.urls == []


def test_targets_from_stdin_defanged_and_several_records(vt, http):
    out = vt([], "# suspects\n8.8.8[.]8  seen twice\n\nhxxps://evil[.]example/a\nevil[.]example\n8.8.8.8\n" + MD5)
    blocks = out.split("\n\n")
    assert [b.splitlines()[0] for b in blocks] == ["indicator      8.8.8.8", f"indicator      {URL}",
                                                  "indicator      evil.example", f"indicator      {MD5}"]
    assert http.urls == [API + "ip_addresses/8.8.8.8", API + f"urls/{URL_ID}", API + "domains/evil.example",
                         API + f"files/{MD5}"]
    records = json.loads(vt(["-o", "json"], "8.8.8.8\nevil.example\n"))
    assert [r["indicator"] for r in records] == ["8.8.8.8", "evil.example"]


def test_what_is_not_an_indicator_it_takes_is_refused(vt, http):
    for bad in ("a@example.com", "nonsense", "d41d8cd98f00b204e9800998ecf8427", "1.2.3.4.5"):
        with pytest.raises(ModuleError, match=f"^queryVirusTotal: '{bad}' is not {WHAT}$"):
            vt([bad], "")
    with pytest.raises(ModuleError, match="is not an IP address"):
        vt(["8.8.8.8", "a@example.com"], "")  # nothing is looked up when one of them is wrong
    with pytest.raises(ModuleError, match=f"^queryVirusTotal: give {WHAT} "):
        vt([], "\n# nothing\n")
    with pytest.raises(ModuleError, match="invalid choice"):
        vt(["8.8.8.8", "-o", "xml"], "")
    assert http.urls == []


# ── the key ──────────────────────────────────────────────────────────────────

def test_without_a_key_there_is_one_error_and_no_request(vt, http):
    vt.env.delenv("VIRUSTOTAL_API_KEY")
    wanted = (r"^queryVirusTotal: no VirusTotal API key\. A free account at https://www\.virustotal\.com gives you "
              r'one: open your profile, then "API key"\. Then run: settings VIRUSTOTAL_API_KEY$')
    with pytest.raises(ModuleError, match=wanted):
        vt(["8.8.8.8"], "")
    with pytest.raises(ModuleError, match=wanted):  # one error for the list, not a record for each
        vt(["8.8.8.8", "evil.example", MD5, "-o", "json"], "")
    with pytest.raises(ModuleError, match=wanted):  # what `enrich` gets
        vt.module["lookup"]("8.8.8.8")
    vt.env.setenv("VIRUSTOTAL_API_KEY", "   ")
    with pytest.raises(ModuleError, match=wanted):
        vt(["8.8.8.8"], "")
    with pytest.raises(ModuleError, match="give an IP address"):  # what is wrong with the input comes first
        vt([], "")
    assert http.urls == []


def test_a_stored_answer_still_needs_the_key(vt, http):
    vt(["8.8.8.8"], "")
    vt.env.delenv("VIRUSTOTAL_API_KEY")
    with pytest.raises(ModuleError, match="no VirusTotal API key"):
        vt(["8.8.8.8"], "")
    assert len(http.urls) == 1


# ── errors ───────────────────────────────────────────────────────────────────

def test_errors_name_the_service_and_never_the_url_or_the_key(vt, http):
    seen = []

    def error_of(handler, args, match):
        http.handler = handler
        with pytest.raises(ModuleError, match=match) as caught:
            vt(args, "")
        seen.append(str(caught.value))

    error_of(fail(http, 401), ["1.1.1.1"], r"^queryVirusTotal: VirusTotal rejected the API key \(401\); "
                                           r"set a working one: settings VIRUSTOTAL_API_KEY$")
    error_of(fail(http, 403), [MD5], r"^queryVirusTotal: VirusTotal rejected the API key \(403\); "
                                     r"set a working one: settings VIRUSTOTAL_API_KEY$")
    error_of(fail(http, 500), [URL], r"^queryVirusTotal: VirusTotal HTTP 500 error$")
    error_of(fail(http, 400), ["evil.example"], r"^queryVirusTotal: VirusTotal HTTP 400 error$")

    def timeout(request):
        raise TimeoutError
    error_of(timeout, ["1.1.1.1", "--timeout", "3"], r"^queryVirusTotal: VirusTotal timed out after 3s$")

    def unreachable(request):
        raise urllib.error.URLError("no route to host")
    error_of(unreachable, [URL], r"^queryVirusTotal: cannot reach VirusTotal: no route to host$")
    error_of(lambda request: FakeResponse(b"<html>busy</html>"), [MD5], r"unexpected \(non-JSON\) response")
    assert len(seen) == 7
    for text in seen:
        assert KEY not in text and "api/v3" not in text and URL_ID not in text


@pytest.mark.parametrize("headers", [None, {"Retry-After": "7"}])
def test_the_rate_limit_error_says_what_a_free_key_allows(vt, http, headers):
    http.handler = fail(http, 429, headers)
    with pytest.raises(ModuleError) as caught:
        vt(["1.1.1.1"], "")
    assert str(caught.value) == f"queryVirusTotal: {LIMIT}"
    assert isinstance(caught.value, vt.intel.RateLimited) and caught.value.wait == 60
    assert KEY not in str(caught.value)
    with pytest.raises(vt.intel.RateLimited):  # not stored as an answer
        vt.module["lookup"]("1.1.1.1")
    assert len(http.urls) == 2


def test_in_a_list_a_failure_is_one_record_and_the_rate_limit_is_waited_out(vt, http, monkeypatch):
    slept = []
    monkeypatch.setattr(vt.intel, "sleep", slept.append)
    limited = {"left": 1}
    normal = http.handler

    def handler(request):
        if request.full_url.endswith("/ip_addresses/9.9.9.9"):
            raise http.error(request.full_url, 500)
        if request.full_url.endswith("/domains/evil.example") and limited["left"]:
            limited["left"] -= 1
            raise http.error(request.full_url, 429)
        return normal(request)

    http.handler = handler
    out = vt(["8.8.8.8", "9.9.9.9", "evil.example", MD5], "")
    assert slept == [61]  # the free key's minute, and a second to be safe
    blocks = out.split("\n\n")
    assert "as_owner       GOOGLE" in blocks[0]
    assert blocks[1] == "indicator  9.9.9.9\nerror      VirusTotal HTTP 500 error"
    assert "registrar      Example Registrar, Inc." in blocks[2] and "threat_label   virus.eicar/test" in blocks[3]
    failed = json.loads(vt(["9.9.9.9", "evil.example", "-o", "json"], ""))[0]
    assert failed["error"] == "VirusTotal HTTP 500 error"
    assert (failed["source"], failed["indicator"], failed["kind"], failed["verdict"]) == ("virustotal", "9.9.9.9",
                                                                                          "ipv4", "unknown")


def test_a_limit_that_stays_is_an_error_for_each_of_the_rest(vt, http, monkeypatch):
    slept = []
    monkeypatch.setattr(vt.intel, "sleep", slept.append)
    vt(["8.8.8.8"], "")  # this one is in the store and costs nothing
    http.handler = fail(http, 429)
    records = json.loads(vt(["8.8.8.8", "1.1.1.1", "evil.example", "-o", "json"], ""))
    assert slept == [61]  # the wait did not help: it does not wait again for the others
    assert records[0]["verdict"] == "clean" and records[0]["cached"] is True
    assert [r.get("error") for r in records[1:]] == [LIMIT, LIMIT]
    assert KEY not in json.dumps(records)


# ── what `enrich` relies on ──────────────────────────────────────────────────

def test_lookup_is_what_enrich_calls(vt, http):
    module = vt.module
    record = module["lookup"]("8.8.8[.]8")
    assert (record["source"], record["indicator"], record["kind"], record["verdict"]) == ("virustotal", "8.8.8.8",
                                                                                          "ipv4", "clean")
    assert record["known_good"] is False and record["cached"] is False and record["raw"] == IP
    assert module["lookup"]("8.8.8.8")["cached"] is True
    assert module["lookup"]("8.8.8.8", fresh=True, timeout=3)["cached"] is False
    assert len(http.urls) == 2
    assert module["lookup"]("HXXPS://evil[.]example/a")["kind"] == "url"
    assert module["lookup"]("new.example")["summary"] == "VirusTotal has never seen it"
    assert module["SOURCE"] == "virustotal" and module["KEY_VAR"] == "VIRUSTOTAL_API_KEY"
    assert module["KINDS"] == ("ipv4", "ipv6", "domain", "url", "md5", "sha1", "sha256")
    assert module["ENV_SETTINGS"] == [EnvSetting("VIRUSTOTAL_API_KEY", "VirusTotal API key",
                                                 module["ENV_SETTINGS"][0].description)]
    assert "virustotal.com" in module["ENV_SETTINGS"][0].description
    with pytest.raises(ModuleError, match=f"^queryVirusTotal: 'a@example.com' is not {WHAT}$"):
        module["lookup"]("a@example.com")
