import json

import pytest

from shellcraft.modkit import ModuleError

MD5 = "d41d8cd98f00b204e9800998ecf8427e"
SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
TEXT = f"""Nov 12 10:01:02 web sshd[812]: Failed password for root from 45.155.205.233 port 4022 ssh2
Nov 12 10:01:03 web sshd[812]: Failed password for root from 45.155.205.233 port 4023 ssh2
GET hxxps://evil[.]example[.]com/a/b.php?x=1 from 10.0.0.5, also http://192.168.1.1/admin
mail from bob[@]phish[.]top with {MD5.upper()} and {SHA256}, sent again by bob@phish.top
resolver 2606:4700:4700::1111, loopback ::1 and 127.0.0.1, files main.py and notes.md
"""


@pytest.fixture
def ioc(registry):
    return registry.get("ioc").run


def test_one_per_line_once_in_first_seen_order(ioc):
    assert ioc([], TEXT).splitlines() == [
        "45.155.205.233", "https://evil.example.com/a/b.php?x=1", "evil.example.com", "10.0.0.5",
        "http://192.168.1.1/admin", "192.168.1.1", "bob@phish.top", "phish.top", MD5, SHA256,
        "2606:4700:4700::1111", "::1", "127.0.0.1"]


def test_text_as_arguments(ioc):
    assert ioc(["seen", "9.9.9[.]9", "twice"], "ignored 1.1.1.1") == "9.9.9.9\n"
    assert ioc(["Seen at EVIL.Example.COM and 9.9.9.9"], "") == "evil.example.com\n9.9.9.9\n"


def test_nothing_found_is_empty_output_and_no_text_is_an_error(ioc):
    assert ioc([], "nothing to see in main.py, version 2.0\n") == ""
    assert ioc(["-o", "json"], "nothing") == "[]\n"
    for stdin in ("", "  \n"):
        with pytest.raises(ModuleError, match=r"^ioc: give some text \(as arguments, or on stdin\)$"):
            ioc([], stdin)


@pytest.mark.parametrize("types, lines", [
    (["-t", "ip"], ["45.155.205.233", "10.0.0.5", "192.168.1.1", "2606:4700:4700::1111", "::1", "127.0.0.1"]),
    (["--type", "ipv6"], ["2606:4700:4700::1111", "::1"]),
    (["-t", "domain"], ["evil.example.com", "phish.top"]),
    (["-t", "url"], ["https://evil.example.com/a/b.php?x=1", "http://192.168.1.1/admin"]),
    (["-t", "hash"], [MD5, SHA256]),
    (["-t", "md5"], [MD5]),
    (["-t", "email"], ["bob@phish.top"]),
    (["-t", "email,hash"], ["bob@phish.top", MD5, SHA256]),
    (["-t", "email", "-t", "sha256"], ["bob@phish.top", SHA256]),
    (["-t", "URLs, Domains"], ["https://evil.example.com/a/b.php?x=1", "evil.example.com",
                              "http://192.168.1.1/admin", "phish.top"]),
])
def test_type(ioc, types, lines):
    assert ioc(types, TEXT).splitlines() == lines


def test_unknown_type(ioc):
    with pytest.raises(ModuleError, match=r"^ioc: unknown type 'file' \(choose from: ip, domain, url, hash, email"):
        ioc(["-t", "ip,file"], TEXT)


def test_public_drops_addresses_that_are_not_on_the_internet_and_urls_that_point_at_one(ioc):
    assert ioc(["--public"], TEXT).splitlines() == [
        "45.155.205.233", "https://evil.example.com/a/b.php?x=1", "evil.example.com", "bob@phish.top",
        "phish.top", MD5, SHA256, "2606:4700:4700::1111"]
    assert ioc(["--public"], "http://[fe80::1]:8080/x http://[2606:4700:4700::1111]/ 169.254.169.254 fd00::1") == (
        "http://[2606:4700:4700::1111]/\n2606:4700:4700::1111\n")
    assert ioc(["--public"], "intranet.corp.example.com stays: only a lookup can tell") == "intranet.corp.example.com\n"


def test_defang_and_refang(ioc):
    assert ioc(["--defang"], TEXT).splitlines() == [
        "45[.]155[.]205[.]233", "hxxps://evil[.]example[.]com/a/b.php?x=1", "evil[.]example[.]com", "10[.]0[.]0[.]5",
        "hxxp://192[.]168[.]1[.]1/admin", "192[.]168[.]1[.]1", "bob[@]phish[.]top", "phish[.]top", MD5, SHA256,
        "2606[:]4700[:]4700[:][:]1111", "[:][:]1", "127[.]0[.]0[.]1"]
    assert ioc(["--refang"], TEXT) == ioc([], TEXT)
    assert ioc([], ioc(["--defang"], TEXT)) == ioc([], TEXT)  # there and back again
    with pytest.raises(ModuleError, match="not allowed with argument --defang"):
        ioc(["--defang", "--refang"], TEXT)


def test_count_puts_the_most_seen_first(ioc):
    lines = ioc(["--count"], TEXT).splitlines()
    assert lines[:4] == ["2  45.155.205.233", "2  bob@phish.top", "2  phish.top",
                         "1  https://evil.example.com/a/b.php?x=1"]
    assert len(lines) == 13
    many = ioc(["-c", "-t", "ip"], "1.1.1.1 " + "8.8.8.8 " * 12 + "9.9.9.9 9.9.9.9")
    assert many == "12  8.8.8.8\n 2  9.9.9.9\n 1  1.1.1.1\n"
    assert ioc(["-c", "--defang", "-t", "ip"], "8.8.8.8 8.8.8.8") == "2  8[.]8[.]8[.]8\n"


def test_json(ioc):
    assert json.loads(ioc(["-o", "json", "-t", "ip,hash", "--public"], TEXT)) == [
        {"type": "ipv4", "value": "45.155.205.233", "count": 2}, {"type": "md5", "value": MD5, "count": 1},
        {"type": "sha256", "value": SHA256, "count": 1}, {"type": "ipv6", "value": "2606:4700:4700::1111", "count": 1}]
    assert json.loads(ioc(["--output", "json", "--defang", "-t", "url"], "http://a.example/x"))[0] == {
        "type": "url", "value": "hxxp://a[.]example/x", "count": 1}
    with pytest.raises(ModuleError, match="invalid choice"):
        ioc(["-o", "csv"], TEXT)


def test_its_output_is_what_the_lookup_commands_read(ioc, registry):
    import inspect

    intel = inspect.unwrap(ioc).__globals__["_intel"]
    wanted = intel.targets("enrich", [], ioc(["--public", "-t", "ip,domain"], TEXT), intel.KINDS, "an indicator")
    assert wanted == [("45.155.205.233", "ipv4"), ("evil.example.com", "domain"), ("phish.top", "domain"),
                      ("2606:4700:4700::1111", "ipv6")]


def test_no_network_and_no_store(ioc, http):
    ioc(["--public"], TEXT)
    assert http.urls == []
