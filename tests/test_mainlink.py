"""The main shell's link (shellcraft.mainlink): published for MCP servers, handlers, stale files."""

import json
import os
import sys

import pytest

from shellcraft import mainlink
from shellcraft.alerts import AlertBus


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path))
    monkeypatch.delenv(mainlink.ADDRESS_ENV, raising=False)
    monkeypatch.delenv(mainlink.KEY_ENV, raising=False)
    return tmp_path


def test_the_link_is_published_for_mcp_servers_and_removed_at_close(home):
    bus = AlertBus()
    link = mainlink.MainLink(bus, home=home)
    try:
        path = home / mainlink.FILE
        assert link.path == path and path.is_file()
        if sys.platform != "win32":
            assert path.stat().st_mode & 0o777 == 0o600
        data = json.loads(path.read_text())
        assert data["pid"] == os.getpid() and data["address"] == str(link.address) and data["key"] == link.key.hex()
        assert mainlink.read_published(home) == (str(link.address), link.key)
        assert mainlink.connect_info() == (str(link.address), link.key)  # no env: the file
        bus.clip = "staged by main"
        assert mainlink.ask(("clip-get", 0)) == "staged by main"  # an MCP server's view
        assert mainlink.ask(("clip-set", "from the AI")) is True and bus.clip == "from the AI"
        assert mainlink.ask(("nonsense",), "default") == "default"
    finally:
        link.close()
    assert not path.exists()
    assert mainlink.connect_info() is None and mainlink.ask(("clip-get", 0), "gone") == "gone"


def test_handlers_answer_and_failures_come_back_as_errors(home):
    link = mainlink.MainLink(AlertBus(), home=home)
    try:
        link.handle("echo", lambda *args: {"args": list(args)})
        link.handle("boom", lambda: 1 / 0)
        assert mainlink.ask(("echo", 1, "two")) == {"args": [1, "two"]}
        assert mainlink.ask(("boom",)) == ("error", "ZeroDivisionError: division by zero")
        assert mainlink.ask(("echo",)) == {"args": []}
        assert mainlink.ask((42,), "default") == "default"  # not a request
    finally:
        link.close()


def test_a_stale_file_is_detected_and_deleted(home):
    path = home / mainlink.FILE
    dead = {"schema": mainlink.SCHEMA, "pid": 2 ** 22 + 7, "address": "/nowhere/sock", "key": "00" * 32,
            "started": 0, "platform": sys.platform}
    path.write_text(json.dumps(dead))
    assert mainlink.read_published(home) is None and not path.exists()  # a dead pid
    alive = dict(dead, pid=os.getpid())
    path.write_text(json.dumps(alive))
    assert mainlink.read_published(home) is None and not path.exists()  # alive, but nothing listens
    path.write_text("{not json")
    assert mainlink.read_published(home) is None  # unreadable: ignored, not ours to delete
    path.write_text(json.dumps(dict(alive, schema=99)))
    assert mainlink.read_published(home) is None


def test_unpublish_leaves_another_shells_file_alone(home):
    path = home / mainlink.FILE
    other = {"schema": mainlink.SCHEMA, "pid": os.getpid() + 1, "address": "x", "key": "00", "started": 0,
             "platform": sys.platform}
    path.write_text(json.dumps(other))
    mainlink.unpublish(path)
    assert path.exists()
    path.write_text(json.dumps(dict(other, pid=os.getpid())))
    mainlink.unpublish(path)
    assert not path.exists()


def test_the_environment_wins_over_the_file(home, monkeypatch):
    link = mainlink.MainLink(AlertBus(), home=home)
    try:
        for name, value in link.env.items():
            monkeypatch.setenv(name, value)
        assert mainlink.available()
        assert mainlink.connect_info() == (str(link.address), link.key)
        monkeypatch.setenv(mainlink.KEY_ENV, "not hex")
        assert mainlink.connect_info() == (str(link.address), link.key)  # falls back to the file
    finally:
        link.close()
