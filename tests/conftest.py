import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.context import ShellContext  # noqa: E402
from core.loader import ModuleRegistry  # noqa: E402

MODULES_DIR = ROOT / "modules"

# A real OS program that exists wherever the tests run: the current interpreter.
OS_UPPER = f'"{sys.executable}" -c "print(input().upper())"'


@pytest.fixture
def registry() -> ModuleRegistry:
    reg = ModuleRegistry(MODULES_DIR)
    reg.load()
    return reg


@pytest.fixture
def ctx(registry, tmp_path, monkeypatch) -> ShellContext:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / ".home"))
    return ShellContext(registry=registry)


# ── offline stubs for modules that call web APIs ────────────────────────────

API_KEY_VARS = ("DNSDUMPSTER_API_KEY", "CENSYS_API_TOKEN", "CENSYS_ORG_ID")


@pytest.fixture
def clean_env(monkeypatch):
    """No API keys in os.environ, and every change the test makes is undone afterwards."""
    from core import settings

    for name in API_KEY_VARS:
        monkeypatch.setenv(name, "x")  # records the original value (or its absence) for undo
        monkeypatch.delenv(name)
    monkeypatch.setattr(settings, "_ORIGINAL_ENV", {})
    return monkeypatch


class FakeResponse(io.BytesIO):
    def __init__(self, body, headers=None):
        super().__init__(body if isinstance(body, bytes) else json.dumps(body).encode())
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeHTTP:
    """Replaces urllib.request.urlopen. Set `handler(request) -> FakeResponse` or raise from it."""

    def __init__(self):
        self.requests = []
        self.handler = lambda request: FakeResponse({})

    def urlopen(self, request, timeout=None):
        self.requests.append(request)
        return self.handler(request)

    @property
    def urls(self):
        return [r.full_url for r in self.requests]

    @staticmethod
    def error(url, code, body=b"", headers=None):
        body = body if isinstance(body, bytes) else json.dumps(body).encode()
        return urllib.error.HTTPError(url, code, "error", headers or {}, io.BytesIO(body))


@pytest.fixture
def http(monkeypatch) -> FakeHTTP:
    fake = FakeHTTP()
    monkeypatch.setattr(urllib.request, "urlopen", fake.urlopen)
    return fake
