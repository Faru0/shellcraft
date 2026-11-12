import base64
import inspect
import json
import threading
import urllib.error
import urllib.parse

import pytest

from shellcraft.modkit import Cancelled, ModuleError, run_cancellable
from tests.conftest import FakeHTTP, FakeResponse

BAD_IP = "45.155.205.233"
MD5 = "d41d8cd98f00b204e9800998ecf8427e"
KEYS = {"VIRUSTOTAL_API_KEY": "vt-secret-key", "ABUSEIPDB_API_KEY": "abuse-secret-key"}
NO_VT = "SKIPPED: virustotal (no key: settings VIRUSTOTAL_API_KEY)"
NO_ABUSE = "SKIPPED: abuseipdb (no key: settings ABUSEIPDB_API_KEY)"


def geo(ip, city="Ashburn", country="United States", asn="AS15169 Google LLC", **more):
    return {"status": "success", "query": ip, "country": country, "countryCode": "US", "regionName": "Virginia",
            "city": city, "isp": "Example ISP", "org": "Example Org", "as": asn, "hosting": False, "proxy": False,
            "mobile": False, **more}


def network(owner="Google LLC", prefix="8.8.8.0", length=24):
    return {"handle": "NET-1", "name": "NET", "cidr0_cidrs": [{"v4prefix": prefix, "length": length}],
            "entities": [{"roles": ["registrant"],
                          "vcardArray": ["vcard", [["version", {}, "text", "4.0"], ["fn", {}, "text", owner]]]}]}


def name(created="1995-08-14T04:00:00Z"):
    return {"ldhName": "EXAMPLE.COM",
            "entities": [{"roles": ["registrar"],
                          "vcardArray": ["vcard", [["version", {}, "text", "4.0"],
                                                   ["fn", {}, "text", "Example Registrar, Inc."]]]}],
            "events": [{"eventAction": "registration", "eventDate": created}]}


def engines(malicious=0, suspicious=0, harmless=70, undetected=24, **attributes):
    return {"data": {"id": "x", "attributes": {
        "last_analysis_stats": {"malicious": malicious, "suspicious": suspicious, "harmless": harmless,
                                "undetected": undetected, "timeout": 0}, **attributes}}}


def reports(score=0, total=0, users=0, **more):
    return {"data": {"abuseConfidenceScore": score, "totalReports": total, "numDistinctUsers": users,
                     "lastReportedAt": "2026-11-12T10:00:00+00:00" if total else None, "isPublic": True,
                     "isWhitelisted": False, "isTor": False, "countryCode": "NL", "usageType": "", "isp": "", **more}}


def noise(ip, noise=False, riot=False, classification="unknown", name="unknown", last_seen=None):
    return {"ip": ip, "noise": noise, "riot": riot, "classification": classification, "name": name,
            "last_seen": last_seen, "link": f"https://viz.greynoise.io/ip/{ip}", "message": "Success"}


def url_id(url):
    return base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")


class Internet:
    """The six services, answering from `pages` ({service: {what was asked: answer}}). An answer that
    is an exception is raised; anything not in `pages` is a 404 (ip2geo: "invalid query")."""

    HOSTS = {"ip-api.com": "ip2geo", "rdap.org": "rdap", "internetdb.shodan.io": "shodan", "api.shodan.io": "shodan",
             "www.virustotal.com": "virustotal", "api.abuseipdb.com": "abuseipdb", "api.greynoise.io": "greynoise"}

    def __init__(self, http: FakeHTTP):
        self.pages: dict[str, dict] = {source: {} for source in set(self.HOSTS.values())}
        self.asked: list[tuple[str, str]] = []
        self.lock = threading.Lock()
        http.handler = self.answer

    def answer(self, request):
        url = urllib.parse.urlsplit(request.full_url)
        source = self.HOSTS[url.hostname]
        if source == "abuseipdb":
            what = urllib.parse.parse_qs(url.query)["ipAddress"][0]
        else:
            what = urllib.parse.unquote(url.path.rstrip("/").rpartition("/")[2])
        with self.lock:
            self.asked.append((source, what))
        page = self.pages[source].get(what)
        if isinstance(page, list):  # one answer after the other; the last one stays
            page = page.pop(0) if len(page) > 1 else page[0]
        if isinstance(page, Exception):
            raise page
        if page is None:
            if source == "ip2geo":
                return FakeResponse({"status": "fail", "message": "invalid query", "query": what})
            raise FakeHTTP.error(request.full_url, 404)
        return FakeResponse(page)

    def sources(self, what=None):
        return sorted(source for source, asked in self.asked if what in (None, asked))


@pytest.fixture
def net(http: FakeHTTP) -> Internet:
    net = Internet(http)
    net.pages["ip2geo"] |= {"8.8.8.8": geo("8.8.8.8"), "example.com": geo("93.184.216.34", city="Norwell"),
                            BAD_IP: geo(BAD_IP, "Amsterdam", "Netherlands", "AS12345 Example Hosting", hosting=True)}
    net.pages["rdap"] |= {"8.8.8.8": network(), BAD_IP: network("Example Hosting LLC", "45.155.205.0"),
                          "example.com": name()}
    net.pages["shodan"] |= {"8.8.8.8": {"ip": "8.8.8.8", "ports": [53, 443], "hostnames": ["dns.google"]},
                            BAD_IP: {"ip": BAD_IP, "ports": [22, 80]}}
    net.pages["greynoise"] |= {"8.8.8.8": noise("8.8.8.8", riot=True, classification="benign",
                                                name="Google Public DNS"),
                               BAD_IP: noise(BAD_IP, noise=True, last_seen="2026-11-11")}
    net.pages["virustotal"] |= {"8.8.8.8": engines(), BAD_IP: engines(malicious=12, suspicious=2, harmless=60,
                                                                     undetected=20),
                                "example.com": engines(), MD5: engines(harmless=0, undetected=60)}
    net.pages["abuseipdb"] |= {"8.8.8.8": reports(isWhitelisted=True), BAD_IP: reports(100, 1234, 300)}
    return net


@pytest.fixture
def enrich(registry, net, clean_env):
    run = registry.get("enrich").run
    run.module = inspect.unwrap(run).__globals__
    run.intel = run.module["_intel"]
    run.env = clean_env
    slept = []
    clean_env.setattr(run.intel, "sleep", slept.append)  # no test ever really waits
    run.slept = slept
    return run


@pytest.fixture
def keys(enrich):
    for var, value in KEYS.items():
        enrich.env.setenv(var, value)


# ── the lines ────────────────────────────────────────────────────────────────

def test_an_ip_without_any_key_is_answered_by_four_sources_and_two_are_skipped(enrich, net):
    assert enrich(["8.8.8.8"], "") == (
        "VERDICT clean  8.8.8.8  greynoise: a known service: Google Public DNS\n"
        "SOURCE: ip2geo     unknown     Ashburn, United States, AS15169 Google LLC\n"
        "SOURCE: rdap       unknown     Google LLC, 8.8.8.0/24\n"
        "SOURCE: shodan     unknown     2 open ports (53, 443)\n"
        "SOURCE: greynoise  clean       a known service: Google Public DNS\n"
        "\n"
        f"{NO_VT}\n{NO_ABUSE}\n")
    assert net.sources() == ["greynoise", "ip2geo", "rdap", "shodan"]  # nothing was sent to the two without a key


def test_with_keys_every_source_has_a_line_and_the_bad_ones_are_the_reasons(enrich, net, keys):
    out = enrich([BAD_IP], "")
    assert out == (
        f"VERDICT malicious  {BAD_IP}  virustotal: 12 of 94 engines call it malicious, 2 suspicious; "
        "abuseipdb: abuse score 100, 1,234 reports from 300 users, last 2026-11-12; "
        "greynoise: seen scanning the internet, not classified, last seen 2026-11-11\n"
        "SOURCE: ip2geo      unknown     Amsterdam, Netherlands, AS12345 Example Hosting, hosting\n"
        "SOURCE: rdap        unknown     Example Hosting LLC, 45.155.205.0/24\n"
        "SOURCE: shodan      unknown     2 open ports (22, 80)\n"
        "SOURCE: virustotal  malicious   12 of 94 engines call it malicious, 2 suspicious\n"
        "SOURCE: abuseipdb   malicious   abuse score 100, 1,234 reports from 300 users, last 2026-11-12\n"
        "SOURCE: greynoise   suspicious  seen scanning the internet, not classified, last seen 2026-11-11\n")
    assert net.sources() == ["abuseipdb", "greynoise", "ip2geo", "rdap", "shodan", "virustotal"]
    assert not any(key in out for key in KEYS.values())


def test_a_domain_goes_to_the_sources_that_take_names(enrich, net, keys):
    out = enrich(["Example.COM."], "")
    assert out.splitlines()[0] == "VERDICT clean  example.com  not flagged by virustotal"
    assert [line.split()[1] for line in out.splitlines()[1:]] == ["ip2geo", "rdap", "virustotal"]
    assert net.sources() == ["ip2geo", "rdap", "virustotal"]
    assert "SKIPPED" not in out


def test_a_hash_only_goes_to_virustotal(enrich, net, keys):
    assert enrich([MD5.upper()], "") == (f"VERDICT clean  {MD5}  not flagged by virustotal\n"
                                         "SOURCE: virustotal  clean       none of 60 engines flag it\n")
    assert net.asked == [("virustotal", MD5)]


def test_a_hash_without_the_virustotal_key_says_what_is_missing(enrich, net):
    assert enrich([MD5], "") == (f"VERDICT unknown  {MD5}  no source could be asked: virustotal has no API key\n"
                                 f"\n{NO_VT}\n")
    assert net.asked == []


def test_a_url_goes_to_virustotal_whole_and_its_host_to_the_others(enrich, net, keys):
    url = "http://example.com/Login?x=1"
    net.pages["virustotal"][url_id(url)] = engines(malicious=7)
    out = enrich(["hxxp://example[.]com/Login?x=1"], "")
    assert out == (
        f"VERDICT malicious  {url}  virustotal: 7 of 101 engines call it malicious\n"
        "SOURCE: ip2geo      unknown     for example.com: Norwell, United States, AS15169 Google LLC\n"
        "SOURCE: rdap        unknown     for example.com: registered 1995-08-14, Example Registrar, Inc.\n"
        "SOURCE: virustotal  malicious   7 of 101 engines call it malicious\n")
    assert sorted(net.asked) == [("ip2geo", "example.com"), ("rdap", "example.com"), ("virustotal", url_id(url))]


def test_a_url_with_an_address_as_host_also_reaches_the_ip_sources(enrich, net):
    out = enrich([f"http://{BAD_IP}/gate.php"], "")
    assert out.splitlines()[0].startswith(f"VERDICT suspicious  http://{BAD_IP}/gate.php  greynoise: seen scanning")
    assert f"SOURCE: shodan     unknown     for {BAD_IP}: 2 open ports (22, 80)" in out
    assert net.sources(BAD_IP) == ["greynoise", "ip2geo", "rdap", "shodan"]


def test_an_email_address_is_looked_up_by_its_domain(enrich, net, keys):
    out = enrich(["Billing@Example.com"], "")
    assert out.splitlines()[0] == "VERDICT clean  billing@example.com  not flagged by virustotal"
    assert "SOURCE: virustotal  clean       for example.com: none of 94 engines flag it" in out
    assert set(net.asked) == {("ip2geo", "example.com"), ("rdap", "example.com"), ("virustotal", "example.com")}


def test_a_private_address_is_never_sent_out(enrich, net, keys):
    for address in ("10.0.0.5", "192.168.1.20", "127.0.0.1", "fe80::1", "http://192.168.1.1/admin"):
        out = enrich([address], "")
        assert out.startswith(f"VERDICT unknown  {address}  a private address: the lookup services only know "
                              "the public internet\n")
    assert net.asked == []  # not even the URL: its path is nobody's business either


def test_what_no_source_takes_says_so(enrich, net):
    assert enrich([MD5, "--only", "rdap"], "") == f"VERDICT unknown  {MD5}  no source takes an MD5 hash\n"
    assert enrich(["2001:4860:4860::8888", "--only", "greynoise"], "") == (
        "VERDICT unknown  2001:4860:4860::8888  no source takes an IPv6 address\n")
    assert net.asked == []


# ── lists, stdin, json ───────────────────────────────────────────────────────

def test_a_list_from_ioc_gets_one_block_each_and_the_skipped_lines_once(enrich, registry, net):
    found = registry.get("ioc").run([], f"Failed password for root from {BAD_IP} port 22\nseen 8.8.8[.]8 and {BAD_IP}")
    assert found == f"{BAD_IP}\n8.8.8.8\n"
    out = enrich([], found)
    blocks = out.split("\n\n")
    assert len(blocks) == 3 and out.count("SKIPPED: ") == 2
    assert blocks[0].startswith(f"VERDICT suspicious  {BAD_IP}  greynoise: seen scanning")
    assert blocks[1].startswith("VERDICT clean  8.8.8.8  ")
    assert blocks[2] == f"{NO_VT}\n{NO_ABUSE}\n"
    assert [line.split()[1] for line in out.splitlines() if line.startswith("VERDICT")] == ["suspicious", "clean"]


def test_stdin_takes_the_first_word_of_a_line_and_skips_comments_and_repeats(enrich, net):
    out = enrich([], f"# from the firewall\n\n{BAD_IP}   443 hits\n8.8.8.8\n{BAD_IP}\n")
    assert [line.split()[2] for line in out.splitlines() if line.startswith("VERDICT")] == [BAD_IP, "8.8.8.8"]


def test_json_has_the_verdict_the_reasons_each_record_and_what_was_skipped(enrich, net):
    result = json.loads(enrich(["8.8.8.8", "-o", "json"], ""))
    assert {k: result[k] for k in ("indicator", "kind", "verdict", "reasons")} == {
        "indicator": "8.8.8.8", "kind": "ipv4", "verdict": "clean",
        "reasons": ["greynoise: a known service: Google Public DNS"]}
    assert [s["source"] for s in result["sources"]] == ["ip2geo", "rdap", "shodan", "greynoise"]
    assert result["skipped"] == [{"source": "virustotal", "why": "no key: settings VIRUSTOTAL_API_KEY"},
                                 {"source": "abuseipdb", "why": "no key: settings ABUSEIPDB_API_KEY"}]
    shodan = result["sources"][2]
    assert shodan["data"]["ports"] == [53, 443] and shodan["verdict"] == "unknown" and "raw" not in shodan
    assert result["sources"][3]["known_good"] is True
    several = json.loads(enrich(["8.8.8.8", MD5, "--output", "json"], ""))
    assert [r["indicator"] for r in several] == ["8.8.8.8", MD5] and several[1]["sources"] == []
    assert several[1]["reasons"] == ["no source could be asked: virustotal has no API key"]


# ── --only, --fresh, the store ───────────────────────────────────────────────

def test_only_asks_the_named_sources(enrich, net, keys):
    out = enrich(["8.8.8.8", "--only", "shodan,RDAP"], "")
    assert out == ("VERDICT unknown  8.8.8.8  no source has a judgement on it\n"
                   "SOURCE: rdap    unknown     Google LLC, 8.8.8.0/24\n"
                   "SOURCE: shodan  unknown     2 open ports (53, 443)\n")
    assert net.sources() == ["rdap", "shodan"]
    net.asked.clear()
    enrich(["8.8.8.8", "--only", "greynoise", "--only", "ip2geo", "--fresh"], "")
    assert net.sources() == ["greynoise", "ip2geo"]


def test_only_with_a_source_that_has_no_key_is_listed_not_an_error(enrich, net):
    assert enrich(["8.8.8.8", "--only", "virustotal"], "") == (
        f"VERDICT unknown  8.8.8.8  no source could be asked: virustotal has no API key\n\n{NO_VT}\n")


def test_an_unknown_source_names_the_ones_there_are(enrich):
    with pytest.raises(ModuleError, match=r"^enrich: unknown source 'whois' \(choose from: ip2geo, rdap, shodan, "
                                          r"virustotal, abuseipdb, greynoise\)$"):
        enrich(["8.8.8.8", "--only", "rdap,whois"], "")


def test_answers_are_stored_and_fresh_asks_again(enrich, net):
    enrich(["8.8.8.8"], "")
    assert len(net.asked) == 4
    again = enrich(["8.8.8.8"], "")
    assert len(net.asked) == 4  # nothing was asked the second time
    assert again.count(" (stored)\n") == 4 and again.startswith("VERDICT clean  8.8.8.8  greynoise: a known service")
    assert all(s["cached"] for s in json.loads(enrich(["8.8.8.8", "-o", "json"], ""))["sources"])
    assert " (stored)" not in enrich(["8.8.8.8", "--fresh"], "")
    assert len(net.asked) == 8


def test_the_timeout_reaches_every_service(enrich, net, http, monkeypatch):
    import urllib.request

    seen = []
    real = urllib.request.urlopen
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout=None: (seen.append(timeout),
                                                                                  real(request, timeout))[1])
    enrich(["8.8.8.8", "--timeout", "3"], "")
    assert seen == [3.0, 3.0, 3.0, 3.0]


# ── a source that fails ──────────────────────────────────────────────────────

def test_a_failing_source_is_an_error_on_its_line_and_the_others_still_count(enrich, net, keys):
    net.pages["rdap"][BAD_IP] = FakeHTTP.error("https://rdap.org/", 500)
    net.pages["virustotal"][BAD_IP] = urllib.error.URLError("[Errno -2] Name or service not known")
    net.pages["shodan"][BAD_IP] = TimeoutError()
    out = enrich([BAD_IP], "")
    assert out.splitlines()[0].startswith(f"VERDICT malicious  {BAD_IP}  abuseipdb: abuse score 100")
    assert "SOURCE: rdap        error       RDAP HTTP 500 error\n" in out
    assert "SOURCE: shodan      error       Shodan InternetDB timed out after 10s\n" in out
    assert "SOURCE: virustotal  error       cannot reach VirusTotal: [Errno -2] Name or service not known\n" in out
    assert not any(key in out for key in KEYS.values())
    record = json.loads(enrich([BAD_IP, "-o", "json", "--only", "rdap"], ""))
    assert record["verdict"] == "unknown" and record["reasons"] == ["no source answered"]
    assert record["sources"][0]["error"] == "RDAP HTTP 500 error"


def test_a_rejected_key_is_an_error_line_that_names_the_setting(enrich, net, keys):
    net.pages["abuseipdb"]["8.8.8.8"] = FakeHTTP.error("https://api.abuseipdb.com/", 401)
    out = enrich(["8.8.8.8"], "")
    assert ("SOURCE: abuseipdb   error       AbuseIPDB rejected the API key (401); set a working one: "
            "settings ABUSEIPDB_API_KEY\n") in out
    assert "SKIPPED" not in out


def test_a_lookup_module_that_breaks_does_not_take_the_others_down(enrich, net, monkeypatch):
    rdap = enrich.module["sources_found"]()["rdap"]

    def broken(indicator, **options):
        raise KeyError("handle")

    monkeypatch.setattr(rdap, "lookup", broken)
    out = enrich(["8.8.8.8"], "")
    assert "SOURCE: rdap       error       KeyError: 'handle'\n" in out
    assert out.startswith("VERDICT clean  8.8.8.8  ")


def test_a_lookup_module_that_is_not_installed_is_left_out(enrich, net, monkeypatch):
    monkeypatch.setitem(enrich.module, "MODULES", ("ip2geo", "queryRdap", "queryNowhere"))
    assert list(enrich.module["sources_found"]()) == ["ip2geo", "rdap"]
    with pytest.raises(ModuleError, match=r"unknown source 'shodan' \(choose from: ip2geo, rdap\)"):
        enrich(["8.8.8.8", "--only", "shodan"], "")
    assert "SKIPPED" not in enrich(["8.8.8.8"], "")


# ── rate limits ──────────────────────────────────────────────────────────────

def limit(seconds=None):
    return FakeHTTP.error("https://x/", 429, headers={"Retry-After": str(seconds)} if seconds is not None else None)


def test_one_indicator_never_waits_for_a_rate_limit(enrich, net, keys):
    net.pages["virustotal"]["8.8.8.8"] = limit()
    out = enrich(["8.8.8.8"], "")
    assert enrich.slept == []
    assert ("SOURCE: virustotal  error       VirusTotal rate limit reached (a free key allows 4 lookups a minute, "
            "500 a day); try again in a minute\n") in out


def test_in_a_list_the_virustotal_minute_is_waited_out_and_asked_again(enrich, net, keys):
    net.pages["virustotal"][BAD_IP] = [limit(), engines(malicious=12)]
    out = enrich(["8.8.8.8", BAD_IP], "")
    assert enrich.slept == [61]
    assert "SOURCE: virustotal  malicious   12 of 106 engines call it malicious\n" in out and " error " not in out
    assert net.asked.count(("virustotal", BAD_IP)) == 2


def test_a_limit_that_stays_after_the_wait_is_not_waited_for_again(enrich, net, keys):
    net.pages["virustotal"] |= {"8.8.8.8": limit(), BAD_IP: limit(), "1.1.1.1": limit()}
    out = enrich(["8.8.8.8", BAD_IP, "1.1.1.1"], "")
    assert enrich.slept == [61]  # once: the day's lookups are used up
    assert out.count("SOURCE: virustotal  error       VirusTotal rate limit reached") == 3
    assert out.count("VERDICT ") == 3  # the other sources still answered


def test_a_daily_limit_is_not_waited_for(enrich, net, keys):
    net.pages["abuseipdb"] |= {"8.8.8.8": limit(40000), BAD_IP: limit(40000)}
    net.pages["greynoise"] |= {"8.8.8.8": limit(), BAD_IP: limit()}
    out = enrich(["8.8.8.8", BAD_IP], "")
    assert enrich.slept == []
    assert out.count("SOURCE: abuseipdb   error       AbuseIPDB rate limit reached; try again in 40000s\n") == 2
    assert out.count("SOURCE: greynoise   error       GreyNoise rate limit reached; try again later") == 2


def test_each_source_waits_for_its_own_limit(enrich, net, keys):
    net.pages["rdap"][BAD_IP] = [limit(5), network("Example Hosting LLC", "45.155.205.0")]
    net.pages["virustotal"][BAD_IP] = [limit(), engines()]
    out = enrich(["8.8.8.8", BAD_IP], "")
    assert sorted(enrich.slept) == [6, 61] and " error " not in out


# ── the verdict ──────────────────────────────────────────────────────────────

def rec(source, verdict="unknown", summary="", known_good=False, error=""):
    record = {"source": source, "indicator": "x", "kind": "ipv4", "verdict": verdict, "summary": summary,
              "known_good": known_good, "cached": False, "data": {}}
    return record | ({"error": error} if error else {})


@pytest.mark.parametrize("records, verdict, reasons", [
    ([rec("a", "malicious", "bad"), rec("b", "clean", "fine")], "malicious", ["a: bad"]),
    ([rec("a", "malicious", "bad"), rec("b", "suspicious", "odd")], "malicious", ["a: bad", "b: odd"]),
    ([rec("a", "malicious", "bad"), rec("b", "clean", "a known service: X", True)], "suspicious",
     ["a: bad", "but b: a known service: X"]),
    ([rec("a", "suspicious", "odd"), rec("b", "clean", "a known service: X", True)], "clean",
     ["b: a known service: X"]),
    ([rec("a", "suspicious", "odd"), rec("b", "clean", "fine"), rec("c")], "suspicious", ["a: odd"]),
    ([rec("a", "clean", "fine"), rec("b", "clean", "ok"), rec("c", summary="Paris")], "clean", ["not flagged by a, b"]),
    ([rec("a", summary="Paris"), rec("b", summary="Example LLC")], "unknown", ["no source has a judgement on it"]),
    ([rec("a", error="timed out"), rec("b", "clean", "fine")], "clean", ["not flagged by b"]),
    ([rec("a", "malicious", "bad", error="timed out")], "unknown", ["no source answered"]),  # an error says nothing
    ([rec("a", "malicious")], "malicious", ["a"]),
    ([], "unknown", []),
])
def test_how_the_sources_become_one_verdict(enrich, records, verdict, reasons):
    assert enrich.module["judge"](records) == (verdict, reasons)


def test_a_good_service_that_is_also_flagged_reads_as_suspicious(enrich, net, keys):
    net.pages["virustotal"]["8.8.8.8"] = engines(malicious=6)
    out = enrich(["8.8.8.8"], "")
    assert out.splitlines()[0] == ("VERDICT suspicious  8.8.8.8  virustotal: 6 of 100 engines call it malicious; "
                                   "but abuseipdb: on AbuseIPDB's whitelist; "
                                   "but greynoise: a known service: Google Public DNS")


def test_the_plan_says_who_is_asked_what(enrich):
    plan, sources = enrich.module["plan"], enrich.module["sources_found"]()
    assert [s for s, _, _ in plan("8.8.8.8", "ipv4", sources)[0]] == ["ip2geo", "rdap", "shodan", "virustotal",
                                                                      "abuseipdb", "greynoise"]
    assert [s for s, _, _ in plan("2001:4860:4860::8888", "ipv6", sources)[0]] == ["ip2geo", "rdap", "shodan",
                                                                                   "virustotal", "abuseipdb"]
    assert plan("a@b.example.com", "email", sources)[0] == [("ip2geo", "b.example.com", "domain"),
                                                            ("rdap", "b.example.com", "domain"),
                                                            ("virustotal", "b.example.com", "domain")]
    assert plan("10.1.2.3", "ipv4", sources) == ([], "a private address: the lookup services only know the "
                                                     "public internet")
    assert plan("a" * 64, "sha256", {"rdap": sources["rdap"]}) == ([], "no source takes a SHA-256 hash")


# ── errors, and stopping ─────────────────────────────────────────────────────

def test_nothing_to_look_up_and_what_is_not_an_indicator(enrich, net):
    with pytest.raises(ModuleError, match=r"^enrich: give an indicator \(an IP address, a domain, a URL, a file hash "
                                          r"or an email address\) \(as an argument, or one per line on stdin\)$"):
        enrich([], "\n# nothing\n")
    with pytest.raises(ModuleError, match=r"^enrich: 'not_an_indicator' is not an indicator"):
        enrich(["8.8.8.8", "not_an_indicator"], "")
    with pytest.raises(ModuleError, match="invalid choice"):
        enrich(["8.8.8.8", "-o", "table"], "")
    assert net.asked == []  # the list is checked before anything is asked


def test_a_cancel_stops_the_run_between_indicators(enrich, net):
    stop = threading.Event()
    real = net.answer

    def answer(request, timeout=None):
        stop.set()  # Ctrl-C while the first indicator is being asked
        return real(request)

    enrich.env.setattr("urllib.request.urlopen", answer)
    with pytest.raises(Cancelled):
        run_cancellable(lambda: enrich(["8.8.8.8", BAD_IP], ""), stop)
    assert BAD_IP not in [what for _, what in net.asked]
