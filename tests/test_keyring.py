"""API keys in the OS keyring (optional `keyring` package), with a fake backend."""

import json
import os

import pytest

from shellcraft import settings
from shellcraft.context import ShellContext, to_text
from shellcraft.pipeline import PipelineError, run_line


class FakeKeyring:
    def __init__(self):
        self.store = {}
        self.refuse = False

    def get_password(self, service, name):
        return self.store.get((service, name))

    def set_password(self, service, name, value):
        if self.refuse:
            raise RuntimeError("locked")
        self.store[(service, name)] = value

    def delete_password(self, service, name):
        self.store.pop((service, name))


@pytest.fixture
def fake(monkeypatch):
    backend = FakeKeyring()
    monkeypatch.setattr(settings, "_backend", [backend])
    return backend


def test_keys_go_to_the_keyring_and_back(fake, clean_env):
    config = {}
    settings.env_set(config, "DNSDUMPSTER_API_KEY", "secret-value-1234")
    assert config["env"]["DNSDUMPSTER_API_KEY"] == settings.KEYRING_MARKER  # no secret in config.json
    assert fake.store[("shellcraft", "DNSDUMPSTER_API_KEY")] == "secret-value-1234"
    assert settings.env_get(config, "DNSDUMPSTER_API_KEY") == "secret-value-1234"
    assert settings.env_status(config, "DNSDUMPSTER_API_KEY") == ("set", "set ••••1234 (keyring)")
    os.environ.pop("DNSDUMPSTER_API_KEY")
    settings.export_env(config)  # startup: read back from the keyring
    assert os.environ["DNSDUMPSTER_API_KEY"] == "secret-value-1234"
    settings.env_reset(config, "DNSDUMPSTER_API_KEY")
    assert fake.store == {} and "DNSDUMPSTER_API_KEY" not in config["env"]


def test_setting_off_or_no_keyring_keeps_plain_text(fake, clean_env, monkeypatch):
    config = {"settings": {"keyring": False}}
    settings.env_set(config, "CENSYS_API_TOKEN", "tok")
    assert config["env"]["CENSYS_API_TOKEN"] == "tok" and fake.store == {}
    monkeypatch.setattr(settings, "_backend", [None])
    plain = {}
    settings.env_set(plain, "CENSYS_API_TOKEN", "tok2")
    assert plain["env"]["CENSYS_API_TOKEN"] == "tok2"
    marked = {"env": {"CENSYS_ORG_ID": settings.KEYRING_MARKER}}  # stored by a Python that had keyring
    assert settings.env_get(marked, "CENSYS_ORG_ID") is None
    assert settings.env_status(marked, "CENSYS_ORG_ID")[1] == "in the keyring, which can't be reached"


def test_migrate_keys_and_the_settings_command(fake, clean_env, registry, tmp_path, monkeypatch):
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path))
    ctx = ShellContext(registry=registry, config={"env": {"DNSDUMPSTER_API_KEY": "old-plain-text-key"}})
    table = run_line("settings", ctx).output
    assert "settings migrate-keys" in str(table.caption)
    assert "moved to the keyring: DNSDUMPSTER_API_KEY" in to_text(run_line("settings migrate-keys", ctx).output)
    assert ctx.config["env"]["DNSDUMPSTER_API_KEY"] == settings.KEYRING_MARKER
    assert json.loads((tmp_path / "config.json").read_text())["env"] == {"DNSDUMPSTER_API_KEY": "@keyring"}
    assert fake.store[("shellcraft", "DNSDUMPSTER_API_KEY")] == "old-plain-text-key"
    assert "nothing to move" in to_text(run_line("settings migrate-keys", ctx).output)
    assert "set in the keyring" in to_text(run_line("settings CENSYS_API_TOKEN abcdef", ctx).output)
    fake.refuse = True
    with pytest.raises(PipelineError, match="keyring refused"):
        run_line("settings CENSYS_ORG_ID org1", ctx)
    monkeypatch.setattr(settings, "_backend", [None])
    with pytest.raises(PipelineError, match="pip install keyring"):
        run_line("settings migrate-keys", ctx)
