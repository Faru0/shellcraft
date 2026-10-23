import io
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shellcraft.context import ShellContext  # noqa: E402
from shellcraft.loader import ModuleRegistry  # noqa: E402

MODULES_DIR = ROOT / "modules"

# A real OS program that exists wherever the tests run: the current interpreter.
OS_UPPER = f'"{sys.executable}" -c "print(input().upper())"'


@pytest.fixture(autouse=True)
def _own_home(tmp_path, monkeypatch):
    """Every test gets its own ~/.shellcraft unless it sets one: a real Shell publishes its link
    there (shellcraft.mainlink) and must never touch the developer's running shell."""
    if not os.environ.get("SHELLCRAFT_HOME"):
        monkeypatch.setenv("SHELLCRAFT_HOME", str(tmp_path / ".shellcraft-test"))


@pytest.fixture
def started(monkeypatch):
    """Run cli.main() through startup with a stand-in shell; returns the contexts it was given."""
    import shellcraft.shell

    class StubShell:
        def __init__(self, ctx):
            contexts.append(ctx)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

        def diagnostics(self):
            pass

        def banner(self, *warnings):
            pass

        def run_rc(self):
            contexts.rc_runs += 1  # type: ignore[attr-defined]

        def show_brief(self, path):
            contexts.briefs.append(path)  # type: ignore[attr-defined]

        def loop(self):
            return 0

    monkeypatch.setattr(shellcraft.shell, "Shell", StubShell)
    contexts = _Contexts()
    return contexts


class _Contexts(list):
    """The contexts cli.main() gave the stand-in shell, and how often it ran the startup scripts."""

    rc_runs = 0
    briefs: list = []


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
    from shellcraft import settings

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


# ── a real HTTP server on 127.0.0.1, for curl ───────────────────────────────

class WebServer:
    """Records every request and answers by path: /echo (the request as JSON), /status/N,
    /redirect/N (N hops to /echo), /json, /binary, /slow, /flaky (503 until `flaky` runs out),
    /cookie (sets one), /digest (401 without a Digest Authorization), /big (`big` bytes)."""

    def __init__(self) -> None:
        import http.server
        import threading

        self.requests: list[dict] = []
        self.flaky = 0
        self.big = 0
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # quiet
                pass

            def _handle(self):
                import json as _json
                import time as _time
                import urllib.parse

                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                record = {"method": self.command, "path": self.path, "headers": dict(self.headers.items()),
                          "header_list": list(self.headers.items()), "body": body.decode("latin-1")}
                owner.requests.append(record)
                path = urllib.parse.urlparse(self.path).path
                status, headers, out = 200, {"Content-Type": "application/json"}, b""
                if path == "/echo":
                    out = _json.dumps(record).encode()
                elif path.startswith("/status/"):
                    status = int(path.rsplit("/", 1)[1])
                    out, headers = f"status {status}\n".encode(), {"Content-Type": "text/plain"}
                elif path.startswith("/redirect/"):
                    hops = int(path.rsplit("/", 1)[1])
                    status, headers = 302, {"Location": f"/redirect/{hops - 1}" if hops > 1 else "/echo"}
                elif path == "/json":
                    out = b'{"a": {"b": [1, 2, 3], "name": "ada"}, "list": ["x", "y"]}'
                elif path == "/binary":
                    out, headers = b"\x89PNG\r\n\x1a\n\x00\x00\x00", {"Content-Type": "image/png"}
                elif path == "/slow":
                    _time.sleep(1.5)
                    out, headers = b"late\n", {"Content-Type": "text/plain"}
                elif path == "/flaky":
                    if owner.flaky > 0:
                        owner.flaky -= 1
                        status, out = 503, b"busy\n"
                    else:
                        out = b"ok\n"
                    headers = {"Content-Type": "text/plain", "Retry-After": "0"}
                elif path == "/cookie":
                    headers = {"Content-Type": "text/plain", "Set-Cookie": "token=xyz; Path=/"}
                    out = b"set\n"
                elif path == "/digest":
                    if not self.headers.get("Authorization", "").startswith("Digest "):
                        status = 401
                        headers = {"WWW-Authenticate": 'Digest realm="test", nonce="abc", qop="auth"',
                                   "Content-Type": "text/plain"}
                        out = b"no\n"
                    else:
                        out, headers = b"digest ok\n", {"Content-Type": "text/plain"}
                elif path == "/big":
                    out, headers = b"x" * owner.big, {"Content-Type": "text/plain"}
                elif path.startswith("/files/"):
                    out, headers = b"file body\n", {"Content-Type": "text/plain"}
                else:
                    status, out = 404, b"not found\n"
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(out)

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _handle

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()

    @property
    def last(self) -> dict:
        return self.requests[-1]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def web(monkeypatch):
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy",
                 "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    server = WebServer()
    yield server
    server.close()
