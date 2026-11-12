import inspect
import json
import urllib.error

import pytest

from shellcraft.modkit import ModuleError
from tests.conftest import FakeHTTP, FakeResponse

KEY = "s3cr3t-k3y"
IDB = "https://internetdb.shodan.io/"
API = "https://api.shodan.io/shodan/host/"

# What InternetDB (no key) says.
DNS = {"cpes": [], "hostnames": ["dns.google"], "ip": "8.8.8.8", "ports": [443, 53], "tags": [], "vulns": []}
SCANME = {"cpes": ["cpe:/a:openbsd:openssh:6.6.1p1", "cpe:/a:apache:http_server:2.4.7"],
          "hostnames": ["scanme.nmap.org"], "ip": "45.33.32.156", "ports": [80, 22, 123], "tags": ["cloud"],
          "vulns": ["CVE-2023-38408", "CVE-2014-0226"]}

# What the API (with a key) says about the same host.
HOST = {
    "ip_str": "45.33.32.156", "ports": [80, 22], "hostnames": ["scanme.nmap.org"], "domains": ["nmap.org"],
    "org": "Linode", "isp": "Akamai Connected Cloud", "asn": "AS63949", "os": None,
    "country_name": "United States", "city": "Fremont", "tags": ["cloud"], "vulns": ["CVE-2023-38408"],
    "last_update": "2026-11-10T07:12:44.123456",
    "data": [
        {"port": 80, "transport": "tcp", "product": "Apache httpd", "version": "2.4.7",
         "cpe23": ["cpe:2.3:a:apache:http_server:2.4.7"], "vulns": {"CVE-2014-0226": {"cvss": 6.8}}},
        {"port": 123, "transport": "udp"},
        {"port": 22, "transport": "tcp", "product": "OpenSSH", "version": "6.6.1p1",
         "cpe": ["cpe:/a:openbsd:openssh:6.6.1p1"], "vulns": {"CVE-2023-38408": {"cvss": 9.8}}},
    ],
}


def host(ports=(), tags=(), vulns=()):
    return {"cpes": [], "hostnames": [], "ip": "1.1.1.1", "ports": list(ports), "tags": list(tags),
            "vulns": list(vulns)}


@pytest.fixture
def shodan(registry, http: FakeHTTP, clean_env):
    """queryShodan with no key set. `run.answers` is what the two back ends know, by URL without the key."""
    run = registry.get("queryShodan").run
    answers = {IDB + "8.8.8.8": DNS, IDB + "45.33.32.156": SCANME, API + "45.33.32.156": HOST}

    def answer(request):
        url = request.full_url.split("?")[0]
        if url not in answers:
            raise http.error(request.full_url, 404, {"error": "No information available for that IP."})
        return FakeResponse(answers[url])

    http.handler = answer
    run.answers = answers
    run.env = clean_env
    run.intel = inspect.unwrap(run).__globals__["_intel"]
    return run


# ── the two back ends ────────────────────────────────────────────────────────

def test_without_a_key_the_internetdb_answers(shodan, http):
    out = shodan(["8.8.8.8"], "")
    assert http.urls == [IDB + "8.8.8.8"]
    assert out == ("indicator  8.8.8.8\n"
                   "verdict    unknown: 2 open ports (53, 443)\n"
                   "ports      53, 443\n"
                   "hostnames  dns.google\n"
                   "via        InternetDB (free, no key)\n")  # no `note`: a new answer, not a stored one


def test_internetdb_shows_tags_vulnerabilities_and_cpes(shodan):
    out = shodan(["45.33.32.156"], "")
    assert out.splitlines()[:2] == [
        "indicator  45.33.32.156",
        "verdict    unknown: 3 open ports (22, 80, 123), tags: cloud, 2 known vulnerabilities"]
    assert "ports      22, 80, 123" in out and "tags       cloud" in out
    assert "vulns      CVE-2014-0226, CVE-2023-38408" in out  # sorted
    assert "cpes       cpe:/a:apache:http_server:2.4.7, cpe:/a:openbsd:openssh:6.6.1p1" in out
    assert "\nservices" not in out and "\norg" not in out  # those need a key


def test_with_a_key_the_api_answers_with_services_owner_and_location(shodan, http):
    shodan.env.setenv("SHODAN_API_KEY", KEY)
    out = shodan(["45.33.32.156"], "")
    assert http.urls == [f"{API}45.33.32.156?key={KEY}"]  # the key travels in the URL, in no header
    assert KEY not in json.dumps(http.requests[0].header_items())
    assert out.splitlines()[:2] == [
        "indicator    45.33.32.156",
        "verdict      unknown: 3 open ports (22, 80, 123), tags: cloud, 2 known vulnerabilities"]
    assert out.splitlines()[2:] == [
        "ports        22, 80, 123",
        "hostnames    scanme.nmap.org",
        "org          Linode",
        "isp          Akamai Connected Cloud",
        "asn          AS63949",
        "country      United States",
        "city         Fremont",
        "tags         cloud",
        "vulns        CVE-2014-0226, CVE-2023-38408",
        "cpes         cpe:/a:openbsd:openssh:6.6.1p1, cpe:2.3:a:apache:http_server:2.4.7",
        "last_update  2026-11-10",
        "services     22/tcp OpenSSH 6.6.1p1, 80/tcp Apache httpd 2.4.7, 123/udp",
        "via          Shodan API (with your key)",
    ]
    assert KEY not in out


def test_the_key_is_sent_safely_whatever_is_in_it(shodan, http):
    shodan.env.setenv("SHODAN_API_KEY", "  a b&c=d  ")
    shodan(["45.33.32.156"], "")
    assert http.urls == [f"{API}45.33.32.156?key=a+b%26c%3Dd"]


def test_internetdb_option_leaves_the_key_unused(shodan, http):
    shodan.env.setenv("SHODAN_API_KEY", KEY)
    out = shodan(["45.33.32.156", "--internetdb"], "")
    assert http.urls == [IDB + "45.33.32.156"]
    assert "via        InternetDB (free, no key)" in out and "services" not in out


def test_the_two_back_ends_keep_their_answers_apart(shodan, http):
    shodan(["45.33.32.156"], "")
    shodan.env.setenv("SHODAN_API_KEY", KEY)
    out = shodan(["45.33.32.156"], "")  # the keyless answer is not reused once a key is set
    assert http.urls == [IDB + "45.33.32.156", f"{API}45.33.32.156?key={KEY}"]
    assert "org          Linode" in out and "note" not in out
    assert "a stored answer" in shodan(["45.33.32.156", "--internetdb"], "")
    assert "a stored answer" in shodan(["45.33.32.156"], "")
    assert len(http.urls) == 2


def test_services_are_sorted_by_port_and_named_as_far_as_shodan_knows(shodan):
    shodan.env.setenv("SHODAN_API_KEY", KEY)
    shodan.answers[API + "9.9.9.9"] = {
        "ip_str": "9.9.9.9", "tags": ["vpn"], "last_update": "2026-11-01T00:00:00",
        "data": [{"port": 8443, "transport": "tcp", "product": "nginx", "tags": ["self-signed"]},
                 {"port": 53, "transport": "udp"}, {"port": 53, "transport": "tcp", "version": "9.18"},
                 {"port": 22, "transport": "tcp", "product": "OpenSSH", "version": "8.9"},
                 {"port": 22, "transport": "tcp", "product": "OpenSSH", "version": "8.9"},  # seen twice
                 {"transport": "tcp", "product": "no port"}, "not a service"],
    }
    record = json.loads(shodan(["9.9.9.9", "-o", "json"], ""))
    assert record["data"]["services"] == ["22/tcp OpenSSH 8.9", "53/tcp 9.18", "53/udp", "8443/tcp nginx"]
    assert record["data"]["ports"] == [22, 53, 8443]  # from the services when the answer has no `ports`
    assert record["data"]["tags"] == ["vpn", "self-signed"]
    assert record["verdict"] == "unknown"  # a vpn is not suspicious
    assert record["summary"] == "3 open ports (22, 53, 8443), tags: vpn, self-signed"


# ── the record ───────────────────────────────────────────────────────────────

FIELDS = ["ip", "ports", "hostnames", "org", "isp", "asn", "os", "country", "city", "tags", "vulns", "cpes",
          "last_update", "services", "via"]


def test_json_is_the_record_with_the_same_fields_from_both_back_ends(shodan):
    record = json.loads(shodan(["45.33.32.156", "-o", "json"], ""))
    assert list(record) == ["source", "indicator", "kind", "verdict", "summary", "known_good", "cached", "data"]
    assert "raw" not in record
    assert (record["source"], record["indicator"], record["kind"]) == ("shodan", "45.33.32.156", "ipv4")
    assert record["known_good"] is False and record["cached"] is False
    assert list(record["data"]) == FIELDS
    assert record["data"]["via"] == "internetdb" and record["data"]["ports"] == [22, 80, 123]
    assert record["data"]["services"] == [] and record["data"]["org"] == ""

    shodan.env.setenv("SHODAN_API_KEY", KEY)
    record = json.loads(shodan(["45.33.32.156", "-o", "json"], ""))
    assert list(record["data"]) == FIELDS
    assert record["data"]["via"] == "api" and record["data"]["ip"] == "45.33.32.156"
    assert record["data"]["vulns"] == ["CVE-2014-0226", "CVE-2023-38408"]  # top level and services, each once
    assert record["data"]["os"] == "" and record["data"]["last_update"] == "2026-11-10"
    assert KEY not in json.dumps(record)


def test_raw_is_shodans_answer(shodan):
    assert json.loads(shodan(["8.8.8.8", "-o", "raw"], "")) == DNS
    shodan.env.setenv("SHODAN_API_KEY", KEY)
    assert json.loads(shodan(["45.33.32.156", "-o", "raw"], "")) == HOST


def test_a_long_list_of_vulnerabilities_is_cut_in_the_table_only(shodan):
    cves = [f"CVE-2024-{n:04}" for n in range(1, 13)]
    shodan.answers[IDB + "1.1.1.1"] = host([80], vulns=cves)
    out = shodan(["1.1.1.1"], "")
    assert "verdict    unknown: 1 open port (80), 12 known vulnerabilities" in out
    assert f"vulns      {', '.join(cves[:10])} and 2 more (-o json lists them all)\n" in out
    assert json.loads(shodan(["1.1.1.1", "-o", "json"], ""))["data"]["vulns"] == cves


# ── the verdict ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("tags, verdict, summary", [
    ([], "unknown", "1 open port (443)"),
    (["cloud"], "unknown", "1 open port (443), tags: cloud"),
    (["vpn"], "unknown", "1 open port (443), tags: vpn"),
    (["c2"], "malicious", "tagged c2, 1 open port (443)"),
    (["malware"], "malicious", "tagged malware, 1 open port (443)"),
    (["compromised"], "malicious", "tagged compromised, 1 open port (443)"),
    (["cloud", "Malware", "C2"], "malicious", "tagged malware and c2, 1 open port (443), tags: cloud"),
    (["tor"], "suspicious", "tagged tor, 1 open port (443)"),
    (["scanner"], "suspicious", "tagged scanner, 1 open port (443)"),
    (["proxy"], "suspicious", "tagged proxy, 1 open port (443)"),
    (["scanner", "cloud", "tor"], "suspicious", "tagged scanner and tor, 1 open port (443), tags: cloud"),
    (["tor", "c2"], "malicious", "tagged c2, 1 open port (443), tags: tor"),  # the worse tag decides
])
def test_the_verdict_comes_from_the_tags(shodan, tags, verdict, summary):
    shodan.answers[IDB + "1.1.1.1"] = host([443], tags)
    record = json.loads(shodan(["1.1.1.1", "-o", "json"], ""))
    assert (record["verdict"], record["summary"]) == (verdict, summary)


@pytest.mark.parametrize("ports, vulns, summary", [
    ([], [], "no open ports seen"),
    ([], ["CVE-2024-0001"], "no open ports seen, 1 known vulnerability"),
    ([22], ["CVE-2024-0002", "CVE-2024-0001"], "1 open port (22), 2 known vulnerabilities"),
    ([8, 7, 6, 5, 4, 3, 2, 1], [], "8 open ports (1, 2, 3, 4, 5, 6, 7, 8)"),
    ([9, 8, 7, 6, 5, 4, 3, 2, 1], [], "9 open ports (1, 2, 3, 4, 5, 6, 7, 8, ...)"),
])
def test_the_summary_counts_ports_and_vulnerabilities(shodan, ports, vulns, summary):
    shodan.answers[IDB + "1.1.1.1"] = host(ports, vulns=vulns)
    record = json.loads(shodan(["1.1.1.1", "-o", "json"], ""))
    assert (record["verdict"], record["summary"]) == ("unknown", summary)  # never `clean`
    assert record["data"]["ports"] == sorted(ports)


# ── not seen, private, stored ────────────────────────────────────────────────

@pytest.mark.parametrize("key, via", [("", "InternetDB (free, no key)"), (KEY, "Shodan API (with your key)")])
def test_not_seen_is_an_answer_and_is_stored(shodan, http, key, via):
    if key:
        shodan.env.setenv("SHODAN_API_KEY", key)
    assert shodan(["9.9.9.9"], "") == f"indicator  9.9.9.9\nverdict    unknown: Shodan has not seen it\nvia        {via}\n"
    record = json.loads(shodan(["9.9.9.9", "-o", "json"], ""))
    assert (record["verdict"], record["summary"], record["cached"]) == ("unknown", "Shodan has not seen it", True)
    assert len(http.urls) == 1  # the second answer came from the store
    assert shodan(["9.9.9.9", "-o", "raw"], "") == "null\n"


@pytest.mark.parametrize("address", ["192.168.1.10", "10.0.0.1", "127.0.0.1", "169.254.1.1", "100.64.0.1", "fe80::1",
                                     "::1", "fd00::1"])
def test_a_private_address_is_answered_without_a_request(shodan, http, address):
    shodan.env.setenv("SHODAN_API_KEY", KEY)
    out = shodan([address], "")
    assert out.splitlines() == [f"indicator  {address}",
                                "verdict    unknown: a private address: Shodan only sees the public internet"]
    record = json.loads(shodan([address, "-o", "json"], ""))
    assert record["verdict"] == "unknown" and record["data"] == {} and record["cached"] is False
    assert http.urls == []


def test_answers_are_stored_for_a_day_and_fresh_asks_again(shodan, http, monkeypatch):
    shodan(["8.8.8.8"], "")
    again = shodan(["8.8.8.8"], "")
    assert len(http.urls) == 1
    assert "note       a stored answer, less than a day old (--fresh asks again)" in again
    assert json.loads(shodan(["8.8.8.8", "-o", "json"], ""))["cached"] is True
    shodan(["8.8.8.8", "--fresh"], "")
    assert len(http.urls) == 2
    monkeypatch.setattr(shodan.intel, "STORE_SECONDS", 0)  # a day later
    assert "note" not in shodan(["8.8.8.8"], "")
    assert len(http.urls) == 3


# ── targets ──────────────────────────────────────────────────────────────────

def test_targets_from_stdin_defanged_and_several_records(shodan, http):
    out = shodan([], "# suspects\n8.8.8[.]8  seen twice\n\n45.33.32[.]156\n8.8.8.8\n192.168.0.1\n")
    first, second, third = out.split("\n\n")
    assert first.startswith("indicator  8.8.8.8\n") and second.startswith("indicator  45.33.32.156\n")
    assert "a private address" in third
    assert http.urls == [IDB + "8.8.8.8", IDB + "45.33.32.156"]
    records = json.loads(shodan(["-o", "json"], "8.8.8.8\n45.33.32.156\n"))
    assert [r["indicator"] for r in records] == ["8.8.8.8", "45.33.32.156"]


def test_ipv6_is_looked_up_in_its_short_form(shodan, http):
    shodan.answers[IDB + "2001:4860:4860::8888"] = host([53])
    out = shodan(["2001:4860:4860:0:0:0:0:8888"], "")
    assert http.urls == [IDB + "2001:4860:4860::8888"]
    assert out.startswith("indicator  2001:4860:4860::8888\nverdict    unknown: 1 open port (53)\n")
    assert json.loads(shodan(["[2001:4860:4860::8888]", "-o", "json"], ""))["kind"] == "ipv6"


def test_what_is_not_an_ip_address_is_refused(shodan, http):
    for bad in ("example.com", "http://8.8.8.8/x", "d41d8cd98f00b204e9800998ecf8427e", "a@example.com", "nonsense",
                "8.8.8.8/24"):
        with pytest.raises(ModuleError, match=r"^queryShodan: '.+' is not an IP address$"):
            shodan([bad], "")
    with pytest.raises(ModuleError, match=r"^queryShodan: give an IP address \("):
        shodan([], "\n# nothing\n")
    with pytest.raises(ModuleError, match="invalid choice"):
        shodan(["8.8.8.8", "-o", "xml"], "")
    assert http.urls == []


# ── errors ───────────────────────────────────────────────────────────────────

def failing(http, code, headers=None):
    def handler(request):
        raise http.error(request.full_url, code, headers=headers)
    return handler


def test_errors_with_a_key_name_the_setting_and_never_show_the_key(shodan, http):
    shodan.env.setenv("SHODAN_API_KEY", KEY)

    def error(handler, *args):
        http.handler = handler
        with pytest.raises(ModuleError) as caught:
            shodan(["1.1.1.1", *args], "")
        assert KEY not in str(caught.value) and "api.shodan.io" not in str(caught.value)
        return str(caught.value)

    def timeout(request):
        raise TimeoutError

    def slow(request):
        raise urllib.error.URLError(TimeoutError("timed out"))

    def unreachable(request):
        raise urllib.error.URLError("[Errno -2] Name or service not known")

    assert error(failing(http, 401)) == ("queryShodan: Shodan rejected the API key (401); set a working one: "
                                         "settings SHODAN_API_KEY")
    assert error(failing(http, 403)) == ("queryShodan: Shodan rejected the API key (403); set a working one: "
                                         "settings SHODAN_API_KEY")
    assert error(failing(http, 429, {"Retry-After": "7"})) == "queryShodan: Shodan rate limit reached; try again in 7s"
    assert error(failing(http, 429)) == ("queryShodan: Shodan rate limit reached (one lookup a second); "
                                         "try again in a moment")
    assert error(failing(http, 500)) == "queryShodan: Shodan HTTP 500 error"
    assert error(timeout, "--timeout", "3") == "queryShodan: Shodan timed out after 3s"
    assert error(slow) == "queryShodan: Shodan timed out after 10s"
    assert error(unreachable) == "queryShodan: cannot reach Shodan: [Errno -2] Name or service not known"
    assert error(lambda request: FakeResponse(b"<html>busy</html>")) == (
        "queryShodan: Shodan returned an unexpected (non-JSON) response")
    assert error(lambda request: FakeResponse(["not", "a", "host"])) == (
        "queryShodan: Shodan returned an unexpected response")
    assert all(url == f"{API}1.1.1.1?key={KEY}" for url in http.urls)

    http.handler = lambda request: FakeResponse(host([80]))  # a failure is never stored as an answer
    assert "1 open port (80)" in shodan(["1.1.1.1"], "")


def test_errors_without_a_key_name_the_internetdb(shodan, http):
    http.handler = failing(http, 429, {"Retry-After": "2"})
    with pytest.raises(ModuleError, match=r"^queryShodan: Shodan InternetDB rate limit reached; try again in 2s$"):
        shodan(["1.1.1.1"], "")
    http.handler = failing(http, 403)
    with pytest.raises(ModuleError, match=r"^queryShodan: Shodan InternetDB denied the request \(403\)$"):
        shodan(["1.1.1.1"], "")
    http.handler = failing(http, 502)
    with pytest.raises(ModuleError, match=r"^queryShodan: Shodan InternetDB HTTP 502 error$"):
        shodan(["1.1.1.1"], "")

    def timeout(request):
        raise TimeoutError
    http.handler = timeout
    with pytest.raises(ModuleError, match=r"^queryShodan: Shodan InternetDB timed out after 10s$"):
        shodan(["1.1.1.1"], "")


def test_no_key_is_not_an_error(shodan, http):
    shodan.env.setenv("SHODAN_API_KEY", "   ")  # blank counts as not set
    assert "via        InternetDB (free, no key)" in shodan(["8.8.8.8"], "")
    assert http.urls == [IDB + "8.8.8.8"]


def test_in_a_list_a_failure_is_one_record_and_a_rate_limit_is_waited_out(shodan, http, monkeypatch):
    shodan.env.setenv("SHODAN_API_KEY", KEY)
    shodan.answers[API + "8.8.8.8"] = {"ip_str": "8.8.8.8", "ports": [53], "org": "Google LLC", "data": []}
    slept = []
    monkeypatch.setattr(shodan.intel, "sleep", slept.append)
    limited = {"left": 1}
    normal = http.handler

    def handler(request):
        if "/9.9.9.9?" in request.full_url:
            raise http.error(request.full_url, 500)
        if "/8.8.8.8?" in request.full_url and limited["left"]:
            limited["left"] -= 1
            raise http.error(request.full_url, 429, headers={"Retry-After": "5"})
        return normal(request)

    http.handler = handler
    out = shodan(["8.8.8.8", "9.9.9.9", "45.33.32.156"], "")
    assert slept == [6]
    blocks = out.split("\n\n")
    assert "org        Google LLC" in blocks[0]
    assert blocks[1] == "indicator  9.9.9.9\nerror      Shodan HTTP 500 error"
    assert "org          Linode" in blocks[2]
    assert KEY not in out
    failed = json.loads(shodan(["9.9.9.9", "45.33.32.156", "-o", "json"], ""))[0]
    assert failed["error"] == "Shodan HTTP 500 error" and failed["verdict"] == "unknown"


# ── what enrich uses ─────────────────────────────────────────────────────────

def test_lookup_is_what_enrich_calls(shodan, http):
    module = inspect.unwrap(shodan).__globals__
    assert module["SOURCE"] == "shodan" and module["KINDS"] == ("ipv4", "ipv6")
    assert module["KEY_VAR"] == "SHODAN_API_KEY"
    assert [(s.name, s.label) for s in module["ENV_SETTINGS"]] == [("SHODAN_API_KEY", "Shodan API key")]
    assert "account.shodan.io" in module["ENV_SETTINGS"][0].description

    record = module["lookup"]("8.8.8[.]8")
    assert (record["source"], record["indicator"], record["kind"], record["verdict"]) == ("shodan", "8.8.8.8", "ipv4",
                                                                                         "unknown")
    assert record["known_good"] is False and record["cached"] is False and record["raw"] == DNS
    assert module["lookup"]("8.8.8.8")["cached"] is True
    assert module["lookup"]("8.8.8.8", fresh=True, timeout=2)["cached"] is False

    shodan.env.setenv("SHODAN_API_KEY", KEY)
    assert module["lookup"]("45.33.32.156")["raw"] == HOST
    assert module["lookup"]("45.33.32.156", internetdb=True)["raw"] == SCANME
    assert http.urls[-2:] == [f"{API}45.33.32.156?key={KEY}", IDB + "45.33.32.156"]
    assert module["lookup"]("192.168.1.1")["summary"] == "a private address: Shodan only sees the public internet"
    with pytest.raises(ModuleError, match=r"^queryShodan: 'example.com' is not an IP address$"):
        module["lookup"]("example.com")
