"""feed: named read-only HTTP sources from feeds.toml."""

import inspect
import json
import urllib.error

import pytest

from shellcraft.modkit import ModuleError
from tests.conftest import FakeResponse

FEEDS = """
[feeds.kuma]
description = "status"
url = "https://kuma.example.com/api/heartbeat"
path = "list.*"
fields = ["status", "msg"]

[feeds.siem]
description = "alerts"
url = "https://siem.example.com/api/alerts?limit=500"
headers = { Authorization = "Bearer {env:SIEM_TOKEN}" }
path = "data.items"
fields = ["time", "rule", "host"]
[feeds.siem.params]
since = "[0-9]{1,6}"
host = "[A-Za-z0-9._-]{1,64}"

[feeds.byid]
url = "https://x.example.com/hosts/{host}/alerts"
[feeds.byid.params]
host = "[a-z0-9-]{1,30}"
"""


@pytest.fixture
def feed(registry, tmp_path, monkeypatch, http):
    home = tmp_path / "home"
    home.mkdir()
    (home / "feeds.toml").write_text(FEEDS)
    monkeypatch.setenv("SHELLCRAFT_HOME", str(home))
    monkeypatch.setenv("SIEM_TOKEN", "s3cr3t-token-value")
    fn = inspect.unwrap(registry.get("feed").run)
    monkeypatch.setitem(fn.__globals__, "_open", lambda request, timeout: http.urlopen(request, timeout))
    http.handler = lambda request: FakeResponse({"list": [{"status": 1, "msg": "up"}, {"status": 0, "msg": "down"}],
                                                 "data": {"items": [{"time": "10:00", "rule": "ssh", "host": "web01"}]}})
    http.run = lambda *a: fn(list(a), "")
    http.file = home / "feeds.toml"
    return http


def test_list(feed):
    text = feed.run("--list")
    assert "kuma" in text and "siem" in text and "since,host" in text and "siem.example.com" in text


def test_a_table_of_the_feeds_fields(feed):
    out = feed.run("kuma")
    assert out.startswith("# feed kuma (untrusted remote data")
    assert "status\tmsg\n1\tup\n0\tdown" in out and out.rstrip().endswith("# end feed kuma")


def test_json_output(feed):
    out = feed.run("kuma", "-o", "json")
    body = out.split("\n", 1)[1].rsplit("\n# end", 1)[0]
    assert json.loads(body) == [{"status": 1, "msg": "up"}, {"status": 0, "msg": "down"}]


def test_the_header_comes_from_the_environment_and_is_not_echoed(feed):
    out = feed.run("siem")
    assert feed.requests[-1].get_header("Authorization") == "Bearer s3cr3t-token-value"
    assert "s3cr3t" not in out and "web01" in out


def test_a_missing_environment_variable_is_named(feed, monkeypatch):
    monkeypatch.delenv("SIEM_TOKEN")
    with pytest.raises(ModuleError, match="SIEM_TOKEN is not set"):
        feed.run("siem")


def test_only_allowed_parameters_with_matching_values(feed):
    feed.run("siem", "-q", "since=3600", "-q", "host=web01")
    assert feed.urls[-1] == "https://siem.example.com/api/alerts?limit=500&since=3600&host=web01"
    for bad in ("since=1h", "host=a b", "host=../x", "limit=9999999", "other=1", "since"):
        with pytest.raises(ModuleError, match="not a parameter|does not match"):
            feed.run("siem", "-q", bad)
    assert len(feed.urls) == 1


def test_a_url_placeholder_is_filled_and_encoded(feed):
    feed.run("byid", "-q", "host=web-1")
    assert feed.urls[-1] == "https://x.example.com/hosts/web-1/alerts"
    with pytest.raises(ModuleError, match="value is missing"):
        feed.run("byid")


def test_unknown_feed_and_usage(feed):
    with pytest.raises(ModuleError, match="no feed named 'zzz'"):
        feed.run("zzz")
    with pytest.raises(ModuleError, match="usage"):
        feed.run()


def test_http_errors_and_redirects_are_clear(feed):
    def boom(request):
        raise feed.error(request.full_url, 503)
    feed.handler = boom
    with pytest.raises(ModuleError, match="HTTP 503"):
        feed.run("kuma")

    def moved(request):
        raise feed.error(request.full_url, 302, headers={"Location": "https://evil.example.com/"})
    feed.handler = moved
    with pytest.raises(ModuleError, match="do not follow redirects"):
        feed.run("kuma")


def test_not_json_and_network_errors(feed):
    feed.handler = lambda request: FakeResponse(b"<html>nope</html>")
    with pytest.raises(ModuleError, match="not JSON"):
        feed.run("kuma")

    def down(request):
        raise urllib.error.URLError("Connection refused")
    feed.handler = down
    with pytest.raises(ModuleError, match="Connection refused"):
        feed.run("kuma")


def test_rows_are_limited_and_the_header_says_so(feed):
    rows = [{"status": i, "msg": "m"} for i in range(30)]
    feed.handler = lambda request: FakeResponse({"list": rows})
    out = feed.run("kuma", "--limit", "5")
    assert out.count("\n") == 1 + 1 + 5 + 1 and "showing 5 of 30 rows" in out
    with pytest.raises(ModuleError, match="--limit is 1 to 5000"):
        feed.run("kuma", "--limit", "0")


def test_secrets_in_the_answer_are_redacted(feed):
    feed.handler = lambda request: FakeResponse({"list": [{"status": 1, "msg": "password=hunter2 ok"}]})
    out = feed.run("kuma")
    assert "hunter2" not in out and "password=REDACTED" in out


def test_a_bad_feeds_file_is_a_clear_error(feed):
    feed.file.write_text('[feeds.x]\nurl = "ftp://x"\n')
    with pytest.raises(ModuleError, match="must be http"):
        feed.run("--list")
    feed.file.write_text("[feeds\n")
    with pytest.raises(ModuleError, match="feeds.toml"):
        feed.run("--list")


def test_pick_paths():
    from shellcraft.loader import ModuleRegistry  # noqa: F401  (the module is loaded by the registry fixture)
