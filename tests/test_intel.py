"""modules/_intel.py: what the lookup modules, `ioc` and `enrich` share."""

import inspect
import json
import os
import time
import urllib.error
from datetime import UTC, datetime, timedelta

import pytest

from shellcraft.modkit import ModuleError
from tests.conftest import FakeHTTP, FakeResponse

MD5, SHA1 = "d41d8cd98f00b204e9800998ecf8427e", "da39a3ee5e6b4b0d3255bfef95601890afd80709"
SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


@pytest.fixture
def intel(registry, clean_env):
    return inspect.unwrap(registry.get("ioc").run).__globals__["_intel"]


def test_it_is_a_helper_not_a_command(registry):
    assert registry.get("_intel") is None and registry.get("intel") is None


# ── indicators ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, kind", [
    ("8.8.8.8", "ipv4"), ("2606:4700:4700::1111", "ipv6"), ("[2001:db8::1]", "ipv6"), ("::1", "ipv6"),
    ("example.com", "domain"), ("www.Example.CO.UK", "domain"), ("example.com.", "domain"),
    ("xn--bcher-kva.example", "domain"), ("host.internal", "domain"),
    ("http://example.com/a?b=c", "url"), ("HTTPS://example.com", "url"), ("ftp://files.example/x", "url"),
    (MD5, "md5"), (SHA1, "sha1"), (SHA256.upper(), "sha256"),
    ("bob@example.com", "email"), ("first.last+tag@mail.example.org", "email"),
    ("999.1.1.1", None), ("localhost", None), ("not an indicator", None), ("abc123", None), ("", None),
    ("-bad-.com", None), (MD5[:-1], None), ("g" * 32, None), ("@example.com", None),
])
def test_kind_of(intel, text, kind):
    assert intel.kind_of(text) == kind


@pytest.mark.parametrize("defanged, real", [
    ("hxxp://evil[.]com/x", "http://evil.com/x"), ("hxxps[:]//evil[.]com", "https://evil.com"),
    ("hXXp://evil.com", "http://evil.com"), ("h**p://evil.com", "http://evil.com"),
    ("https[://]evil.com", "https://evil.com"), ("fxp://files.example", "ftp://files.example"),
    ("1[.]2[.]3[.]4", "1.2.3.4"), ("1(.)2{.}3[dot]4", "1.2.3.4"), ("evil(dot)com", "evil.com"),
    ("2001[:]db8[:][:]1", "2001:db8::1"), ("bob[@]evil[.]com", "bob@evil.com"), ("bob[at]evil[.]com", "bob@evil.com"),
    ("nothing to change, http://ok.example", "nothing to change, http://ok.example"),
])
def test_refang(intel, defanged, real):
    assert intel.refang(defanged) == real


@pytest.mark.parametrize("real, defanged", [
    ("http://evil.com/a.b?c=d.e", "hxxp://evil[.]com/a.b?c=d.e"), ("https://evil.com", "hxxps://evil[.]com"),
    ("ftp://files.example/x", "ftp://files[.]example/x"), ("1.2.3.4", "1[.]2[.]3[.]4"),
    ("2001:db8::1", "2001[:]db8[:][:]1"), ("evil.example.com", "evil[.]example[.]com"),
    ("bob@evil.com", "bob[@]evil[.]com"), (MD5, MD5), ("plain words", "plain words"),
])
def test_defang_and_back(intel, real, defanged):
    assert intel.defang(real) == defanged
    assert intel.refang(defanged) == real


@pytest.mark.parametrize("typed, value, kind", [
    (" 8.8.8[.]8 ", "8.8.8.8", "ipv4"), ("2606:4700:4700:0:0:0:0:1111", "2606:4700:4700::1111", "ipv6"),
    ("[2001:DB8::1]", "2001:db8::1", "ipv6"), ("WWW.Example[.]COM.", "www.example.com", "domain"),
    (SHA256.upper(), SHA256, "sha256"), ("Bob@Example.COM", "bob@example.com", "email"),
    ("hxxp://Evil[.]com/Path", "http://Evil.com/Path", "url"), ("what", "what", None),
])
def test_clean(intel, typed, value, kind):
    assert intel.clean(typed) == (value, kind)


@pytest.mark.parametrize("ip, public", [
    ("8.8.8.8", True), ("2606:4700:4700::1111", True), ("10.0.0.1", False), ("192.168.1.1", False),
    ("172.16.0.1", False), ("127.0.0.1", False), ("169.254.1.1", False), ("100.64.0.1", False),
    ("224.0.0.1", False), ("0.0.0.0", False), ("203.0.113.9", False), ("::1", False), ("fe80::1", False),
    ("fd00::1", False), ("2001:db8::1", False), ("example.com", False), ("", False),
])
def test_is_public(intel, ip, public):
    assert intel.is_public(ip) is public


def test_host_of(intel):
    assert intel.host_of("https://User:pw@WWW.Example.com:8443/a?b") == "www.example.com"
    assert intel.host_of("http://[2001:db8::1]:8080/x") == "2001:db8::1"
    assert intel.host_of("http://192.168.1.1/admin") == "192.168.1.1"
    assert intel.host_of("Bob@Mail.Example.com") == "mail.example.com"
    assert intel.host_of("example.com") == "example.com" and intel.host_of("8.8.8.8") == "8.8.8.8"
    assert intel.host_of("http://[bad/", "url") == ""


def found(intel, text):
    return [(f.kind, f.value) for f in intel.find(text)]


def test_find_reads_a_log_in_first_seen_order_with_counts(intel):
    text = ("Failed password for root from 45.155.205.233 port 4022 ssh2\n"
            "Failed password for admin from 45.155.205.233 port 4023 ssh2\n"
            "Accepted publickey for faru from 10.0.0.5 port 50000\n"
            "GET https://cdn.example.net/app.js?v=3 referer 45.155.205.233\n")
    assert intel.find(text) == [
        intel.Found("ipv4", "45.155.205.233", 3), intel.Found("ipv4", "10.0.0.5", 1),
        intel.Found("url", "https://cdn.example.net/app.js?v=3", 1), intel.Found("domain", "cdn.example.net", 1)]


def test_find_reads_defanged_text_and_gives_the_host_of_a_url_and_the_domain_of_an_address(intel):
    assert found(intel, "see hxxps://evil[.]example[.]com/a/b.php?x=1, sent by bob[@]phish[.]top from 1[.]2[.]3[.]4") == [
        ("url", "https://evil.example.com/a/b.php?x=1"), ("domain", "evil.example.com"),
        ("email", "bob@phish.top"), ("domain", "phish.top"), ("ipv4", "1.2.3.4")]
    assert found(intel, "http://192.168.1.1/admin and http://[2001:db8::1]:8080/x") == [
        ("url", "http://192.168.1.1/admin"), ("ipv4", "192.168.1.1"),
        ("url", "http://[2001:db8::1]:8080/x"), ("ipv6", "2001:db8::1")]


def test_find_leaves_the_punctuation_of_the_sentence_out(intel):
    assert found(intel, "It was 8.8.8.8, (1.1.1.1) and 9.9.9.9.") == [("ipv4", "8.8.8.8"), ("ipv4", "1.1.1.1"),
                                                                    ("ipv4", "9.9.9.9")]
    assert found(intel, "Read http://example.com/a_(b). Then (see https://example.org/x).") == [
        ("url", "http://example.com/a_(b)"), ("domain", "example.com"),
        ("url", "https://example.org/x"), ("domain", "example.org")]
    assert found(intel, 'href="http://example.com/x">link</a> <bob@example.com>') == [
        ("url", "http://example.com/x"), ("domain", "example.com"), ("email", "bob@example.com")]
    assert found(intel, "Visit www.Example.ORG. Thanks.") == [("domain", "www.example.org")]


def test_find_hashes_by_length_and_in_lower_case(intel):
    assert found(intel, f"md5 {MD5.upper()} sha1 {SHA1} sha256 {SHA256}") == [("md5", MD5), ("sha1", SHA1),
                                                                             ("sha256", SHA256)]
    assert found(intel, f"x{MD5} {MD5}0 {'a' * 33} {'f' * 128}") == []  # part of something longer


def test_find_ipv6(intel):
    assert found(intel, "from 2606:4700:4700::1111 and ::1, fe80::1%eth0, at 10:20:30 on aa:bb:cc:dd:ee:ff") == [
        ("ipv6", "2606:4700:4700::1111"), ("ipv6", "::1"), ("ipv6", "fe80::1")]
    assert found(intel, "std::vector<int> a::b dead::beef :: 12:30") == []  # code, not addresses
    assert found(intel, "fe80::a dead:beef::cafe") == [("ipv6", "fe80::a"), ("ipv6", "dead:beef::cafe")]


def test_find_does_not_take_these_for_indicators(intel):
    for text in ("version 2.0 of main.py, run.sh and notes.md", "999.1.1.1 and 1.2.3 and 1.2.3.4.5",
                 "v1.2.3.4", "done.It was fine", "e.g. this, i.e. that", "file.txt archive.tar.gz setup.cfg",
                 "self.value obj.method() os.path", "at 10:20:30", "price 3.50 USD", "user@localhost"):
        assert found(intel, text) == [], text


def test_find_file_endings_count_inside_a_url_or_an_address(intel):
    assert found(intel, "http://get.sh/x and bob@mail.zip") == [
        ("url", "http://get.sh/x"), ("domain", "get.sh"), ("email", "bob@mail.zip"), ("domain", "mail.zip")]
    assert found(intel, "cdn.install.sh serves it") == [("domain", "cdn.install.sh")]  # three parts: a host name


def test_find_known_limits_stay_known(intel):
    # documented in `man ioc`: a four-part version reads as an address, a git commit as a SHA-1
    assert found(intel, "upgrade to 1.2.3.4") == [("ipv4", "1.2.3.4")]
    assert found(intel, f"commit {SHA1}") == [("sha1", SHA1)]


def test_kinds_named(intel):
    assert intel.kinds_named(None, "ioc") == intel.KINDS and intel.kinds_named([], "ioc") == intel.KINDS
    assert intel.kinds_named(["ip"], "ioc") == ("ipv4", "ipv6")
    assert intel.kinds_named(["hash,URL", " domains "], "ioc") == ("md5", "sha1", "sha256", "url", "domain")
    assert intel.kinds_named(["ip,ipv4", "IPs"], "ioc") == ("ipv4", "ipv6")
    assert intel.kinds_named(["emails", "sha256"], "ioc") == ("email", "sha256")
    with pytest.raises(ModuleError, match=r"^ioc: unknown type 'file' \(choose from: ip, domain, url, hash, email, "
                                          r"ipv4, ipv6, md5, sha1, sha256\)$"):
        intel.kinds_named(["ip,file"], "ioc")


# ── HTTP ─────────────────────────────────────────────────────────────────────

URL = "https://api.service.example/v1/thing?key=SECRETKEY"


def get(intel, **kwargs):
    return intel.get_json(URL, module="queryX", service="Service", **kwargs)


def test_get_json_sends_the_headers_and_returns_the_answer(intel, http: FakeHTTP):
    http.handler = lambda request: FakeResponse({"ok": True})
    assert get(intel, headers={"X-Key": "k"}) == {"ok": True}
    request = http.requests[0]
    assert request.full_url == URL and request.get_header("X-key") == "k"
    assert request.get_header("User-agent") == intel.AGENT and request.get_header("Accept") == "application/json"
    get(intel, headers={"Accept": "application/rdap+json"})
    assert http.requests[1].get_header("Accept") == "application/rdap+json"


def raises(http, code, headers=None):
    def handler(request):
        raise http.error(request.full_url, code, headers=headers)
    http.handler = handler


@pytest.mark.parametrize("code, kwargs, message", [
    (401, {"key_var": "X_API_KEY"}, "queryX: Service rejected the API key (401); set a working one: settings X_API_KEY"),
    (403, {"key_var": "X_API_KEY"}, "queryX: Service rejected the API key (403); set a working one: settings X_API_KEY"),
    (401, {}, "queryX: Service denied the request (401)"),
    (403, {}, "queryX: Service denied the request (403)"),
    (404, {}, "queryX: Service has nothing on it"),
    (404, {"what": "8.8.8.8"}, "queryX: Service has nothing on 8.8.8.8"),
    (429, {}, "queryX: Service rate limit reached; try again later"),
    (500, {}, "queryX: Service HTTP 500 error"),
    (422, {"key_var": "X_API_KEY"}, "queryX: Service HTTP 422 error"),
])
def test_get_json_http_errors(intel, http, code, kwargs, message):
    raises(http, code)
    with pytest.raises(ModuleError) as caught:
        get(intel, **kwargs)
    assert str(caught.value) == message


def test_get_json_404_and_429_have_their_own_kinds(intel, http, clean_env):
    raises(http, 404)
    with pytest.raises(intel.NotKnown):
        get(intel)
    raises(http, 429, {"Retry-After": "30"})
    with pytest.raises(intel.RateLimited) as caught:
        get(intel)
    assert caught.value.wait == 30 and str(caught.value) == "queryX: Service rate limit reached; try again in 30s"
    raises(http, 429, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})  # a date: not a number of seconds
    with pytest.raises(intel.RateLimited) as caught:
        get(intel)
    assert caught.value.wait is None and str(caught.value).endswith("try again later")


def test_a_rate_limit_names_the_key_only_when_none_is_set(intel, http, clean_env):
    raises(http, 429, {"Retry-After": "5"})
    with pytest.raises(ModuleError) as caught:
        get(intel, key_var="X_API_KEY")
    assert str(caught.value) == ("queryX: Service rate limit reached; try again in 5s "
                                 "(a key raises the limit: settings X_API_KEY)")
    clean_env.setenv("X_API_KEY", "k")
    with pytest.raises(ModuleError) as caught:
        get(intel, key_var="X_API_KEY")
    assert str(caught.value) == "queryX: Service rate limit reached; try again in 5s"


def test_get_json_network_failures_never_show_the_url(intel, http):
    def refused(request):
        raise urllib.error.URLError("Connection refused")

    def slow(request):
        raise TimeoutError

    def slow_inside(request):
        raise urllib.error.URLError(TimeoutError("timed out"))

    cases = [(refused, "queryX: cannot reach Service: Connection refused"),
             (slow, "queryX: Service timed out after 2.5s"), (slow_inside, "queryX: Service timed out after 2.5s"),
             (lambda request: FakeResponse(b"<html>busy</html>"),
              "queryX: Service returned an unexpected (non-JSON) response"),
             (lambda request: FakeResponse(b"\xff\xfe"), "queryX: Service returned an unexpected (non-JSON) response")]
    for handler, message in cases:
        http.handler = handler
        with pytest.raises(ModuleError) as caught:
            get(intel, timeout=2.5)
        assert str(caught.value) == message
    for code in (401, 404, 429, 500):
        raises(http, code)
        with pytest.raises(ModuleError) as caught:
            get(intel, key_var="X_API_KEY")
        assert "SECRETKEY" not in str(caught.value) and "api.service.example" not in str(caught.value)


def test_need_key(intel, clean_env):
    with pytest.raises(ModuleError) as caught:
        intel.need_key("queryX", "X_API_KEY", "Service", "Get one at https://service.example.")
    assert str(caught.value) == ("queryX: no Service API key. Get one at https://service.example. "
                                 "Then run: settings X_API_KEY")
    clean_env.setenv("X_API_KEY", "   ")
    with pytest.raises(ModuleError, match="no Service API key"):
        intel.need_key("queryX", "X_API_KEY", "Service", "")
    clean_env.setenv("X_API_KEY", " abc ")
    assert intel.need_key("queryX", "X_API_KEY", "Service", "") == "abc"


# ── the answer store ─────────────────────────────────────────────────────────

def test_the_store_keeps_an_answer_for_a_day(intel, monkeypatch):
    calls = []

    def fetch():
        calls.append(1)
        return {"n": len(calls)}

    assert intel.answer("svc", "8.8.8.8", fetch) == ({"n": 1}, False)
    assert intel.answer("svc", "8.8.8.8", fetch) == ({"n": 1}, True)
    assert intel.answer("svc", "8.8.8.8", fetch, fresh=True) == ({"n": 2}, False)
    assert intel.answer("svc", "8.8.8.8", fetch) == ({"n": 2}, True)  # the fresh answer replaced the old one
    assert intel.answer("svc", "8.8.4.4", fetch) == ({"n": 3}, False)  # another indicator
    assert intel.answer("other", "8.8.8.8", fetch) == ({"n": 4}, False)  # another service
    # as given: clean() lowers names and hashes before they get here, and in a URL the case counts
    assert intel.answer("svc", "http://x.example/Ab", fetch)[1] is False
    assert intel.answer("svc", "http://x.example/aB", fetch)[1] is False
    assert intel.answer("svc", "http://x.example/Ab", fetch)[1] is True
    monkeypatch.setattr(intel, "STORE_SECONDS", 0)  # a day later
    assert intel.answer("svc", "8.8.8.8", fetch)[1] is False


def test_the_store_is_under_the_shellcraft_folder_and_holds_no_indicator_in_a_file_name(intel):
    intel.answer("svc", "secret-host.example.com", lambda: {"a": 1})
    folder = intel.store_dir()
    assert folder == intel.store_dir().parent.parent / "cache" / "intel"
    assert str(folder).startswith(os.environ["SHELLCRAFT_HOME"])
    files = list((folder / "svc").glob("*.json"))
    assert len(files) == 1 and "secret" not in files[0].name and len(files[0].stem) == 40
    kept = json.loads(files[0].read_text())
    assert kept["indicator"] == "secret-host.example.com" and kept["data"] == {"a": 1} and kept["at"] <= time.time()
    assert not list((folder / "svc").glob(".tmp-*"))


def test_not_known_is_stored_and_other_errors_are_not(intel):
    calls = []

    def unknown():
        calls.append(1)
        raise intel.NotKnown("queryX: Service has nothing on it")

    assert intel.answer("svc", "1.1.1.1", unknown) == (None, False)
    assert intel.answer("svc", "1.1.1.1", unknown) == (None, True) and len(calls) == 1

    def broken():
        calls.append(1)
        raise ModuleError("queryX: Service HTTP 500 error")

    for _ in range(2):
        with pytest.raises(ModuleError, match="HTTP 500"):
            intel.answer("svc", "2.2.2.2", broken)
    assert len(calls) == 3  # asked both times: a failure is never stored


def test_a_broken_or_unwritable_store_never_fails_a_lookup(intel, monkeypatch, tmp_path):
    intel.answer("svc", "8.8.8.8", lambda: 1)
    path = next((intel.store_dir() / "svc").glob("*.json"))
    path.write_text("{not json")
    assert intel.answer("svc", "8.8.8.8", lambda: 2) == (2, False)
    path.write_text('{"data": 5}')  # no time in it
    assert intel.answer("svc", "8.8.8.8", lambda: 3) == (3, False)
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setattr(intel, "store_dir", lambda: blocker / "intel")  # a folder that cannot be made
    assert intel.answer("svc", "8.8.8.8", lambda: 4) == (4, False)
    assert intel.answer("svc", "8.8.8.8", lambda: 5) == (5, False)


def test_old_answers_are_swept_once_a_day(intel):
    intel.answer("svc", "old.example", lambda: 1)
    old = next((intel.store_dir() / "svc").glob("*.json"))
    long_ago = time.time() - 3 * 24 * 3600
    os.utime(old, (long_ago, long_ago))
    intel.answer("svc", "new.example", lambda: 2)
    assert old.exists()  # swept less than a day ago: nothing is removed yet
    os.utime(intel.store_dir() / ".swept", (long_ago, long_ago))
    intel.answer("svc", "newer.example", lambda: 3)
    assert not old.exists() and len(list((intel.store_dir() / "svc").glob("*.json"))) == 2


# ── the command around a lookup ──────────────────────────────────────────────

def test_targets_from_arguments_or_stdin(intel):
    kinds = ("ipv4", "ipv6")
    assert intel.targets("queryX", ["8.8.8[.]8", "8.8.8.8", "::1"], "ignored", kinds, "an IP address") == [
        ("8.8.8.8", "ipv4"), ("::1", "ipv6")]
    assert intel.targets("queryX", [], "# header\n\n1.1.1.1  seen 4 times\n  9.9.9.9\n", kinds, "an IP address") == [
        ("1.1.1.1", "ipv4"), ("9.9.9.9", "ipv4")]
    with pytest.raises(ModuleError) as caught:
        intel.targets("queryX", [], " \n# nothing\n", kinds, "an IP address")
    assert str(caught.value) == "queryX: give an IP address (as an argument, or one per line on stdin)"
    with pytest.raises(ModuleError) as caught:
        intel.targets("queryX", ["8.8.8.8", "example.com"], "", kinds, "an IP address")
    assert str(caught.value) == "queryX: 'example.com' is not an IP address"


def test_parser_has_the_shared_options(intel):
    opts = intel.parser("queryX", "IP").parse_args(["1.1.1.1", "2.2.2.2", "-o", "json", "--fresh", "--timeout", "3"])
    assert (opts.targets, opts.output, opts.fresh, opts.timeout) == (["1.1.1.1", "2.2.2.2"], "json", True, 3.0)
    opts = intel.parser("queryX", "IP", timeout=20).parse_args([])
    assert (opts.targets, opts.output, opts.fresh, opts.timeout) == ([], "table", False, 20)
    with pytest.raises(ModuleError, match="invalid choice"):
        intel.parser("queryX", "IP").parse_args(["-o", "xml"])


def test_each_one_target_raises_and_a_list_records_the_failure(intel):
    def fn(indicator):
        if indicator == "2.2.2.2":
            raise ModuleError("queryX: Service HTTP 500 error")
        return intel.record("x", indicator, "ipv4", "clean", "fine")

    with pytest.raises(ModuleError, match="HTTP 500"):
        intel.each("queryX", "x", [("2.2.2.2", "ipv4")], fn)
    records = intel.each("queryX", "x", [("1.1.1.1", "ipv4"), ("2.2.2.2", "ipv4"), ("3.3.3.3", "ipv4")], fn)
    assert [r["verdict"] for r in records] == ["clean", "unknown", "clean"]
    assert records[1]["error"] == "Service HTTP 500 error" and "error" not in records[0]


def test_each_waits_out_a_short_rate_limit_in_a_list(intel, monkeypatch):
    slept = []
    monkeypatch.setattr(intel, "sleep", slept.append)
    limits = {"1.1.1.1": [10], "2.2.2.2": [10], "3.3.3.3": [None]}

    def fn(indicator):
        if limits.get(indicator):
            raise intel.RateLimited("queryX: Service rate limit reached", limits[indicator].pop())
        return intel.record("x", indicator, "ipv4")

    with pytest.raises(intel.RateLimited):  # one target: no waiting, the error says when to come back
        intel.each("queryX", "x", [("3.3.3.3", "ipv4")], fn)
    limits["3.3.3.3"] = [None]
    records = intel.each("queryX", "x", [(ip, "ipv4") for ip in limits], fn)
    assert slept == [11, 11]  # each short limit is waited out; one with no time given is not
    assert [r.get("error") for r in records] == [None, None, "Service rate limit reached"]
    slept.clear()
    limits["1.1.1.1"] = [intel.MAX_WAIT + 1]  # too long to sit through
    records = intel.each("queryX", "x", [("1.1.1.1", "ipv4"), ("4.4.4.4", "ipv4")], fn)
    assert slept == [] and records[0]["error"] and "error" not in records[1]
    slept.clear()
    limits.update({"1.1.1.1": [10, 10], "2.2.2.2": [10]})  # still there after the wait: it stops waiting
    records = intel.each("queryX", "x", [("1.1.1.1", "ipv4"), ("2.2.2.2", "ipv4"), ("4.4.4.4", "ipv4")], fn)
    assert slept == [11]
    assert [bool(r.get("error")) for r in records] == [True, True, False]


def test_a_list_waits_five_minutes_in_all_and_no_longer(intel, monkeypatch):
    slept = []
    monkeypatch.setattr(intel, "sleep", slept.append)
    asked = []

    def fn(indicator):  # the free VirusTotal key: four a minute
        asked.append(indicator)
        if asked.count(indicator) == 1 and len(set(asked)) % 5 == 0:
            raise intel.RateLimited("queryX: Service rate limit reached", 60)
        return intel.record("x", indicator, "ipv4")

    wanted = [(f"9.9.9.{n}", "ipv4") for n in range(1, 31)]
    records = intel.each("queryX", "x", wanted, fn)
    assert slept == [61, 61, 61, 61] and sum(slept) <= intel.MAX_WAITED
    assert [n for n, r in enumerate(records, 1) if r.get("error")] == [25, 30]  # no fifth wait


def test_show_table_json_and_raw(intel):
    one = intel.record("x", "8.8.8.8", "ipv4", "clean", "nothing reported", data={"owner": "Google", "ports": [53, 443],
                                                                                 "empty": "", "tor": False},
                       raw={"whole": "answer"})
    stored = intel.record("x", "1.1.1.1", "ipv4", cached=True, raw=None)
    failed = intel.record("x", "9.9.9.9", "ipv4") | {"error": "Service HTTP 500 error"}
    rows = lambda data: list(data.items())  # noqa: E731
    assert intel.show([one], "table", rows) == ("indicator  8.8.8.8\nverdict    clean: nothing reported\n"
                                                "owner      Google\nports      53, 443\ntor        no\n")
    assert intel.show([one, stored, failed], "table", rows).split("\n\n")[1:] == [
        "indicator  1.1.1.1\nverdict    unknown\nnote       a stored answer, less than a day old (--fresh asks again)",
        "indicator  9.9.9.9\nerror      Service HTTP 500 error\n"]
    as_json = json.loads(intel.show([one], "json", rows))
    assert "raw" not in as_json and as_json["data"]["ports"] == [53, 443] and as_json["known_good"] is False
    assert [r["indicator"] for r in json.loads(intel.show([one, stored], "json", rows))] == ["8.8.8.8", "1.1.1.1"]
    assert json.loads(intel.show([one], "raw", rows)) == {"whole": "answer"}
    assert json.loads(intel.show([one, stored], "raw", rows)) == [{"whole": "answer"}, None]


def test_day_and_days_since(intel):
    assert intel.day("2026-11-12T08:09:10Z") == "2026-11-12" and intel.day("2026-11-12") == "2026-11-12"
    assert intel.day(0) == "" and intel.day(86400) == "1970-01-02" and intel.day(1700000000) == "2023-11-14"
    for junk in ("", "soon", None, True, [], -5):
        assert intel.day(junk) == "" and intel.days_since(junk) is None
    three = (datetime.now(UTC) - timedelta(days=3)).strftime("%Y-%m-%dT00:00:00Z")
    assert intel.days_since(three) == 3 and intel.days_since("2999-01-01") == 0
