"""curl (and fetch) on Python requests, against a real HTTP server on 127.0.0.1."""

import json
import sys

import pytest

from shellcraft.context import CommandError, WithStatus, to_text
from shellcraft.modkit import ModuleError
from shellcraft.pipeline import PipelineError, run_line


@pytest.fixture
def curl(registry):
    run = registry.get("curl").run

    def call(*args, stdin=""):
        return run(list(args), stdin)
    return call


def text(value):
    return to_text(value)


def echo(value):
    return json.loads(text(value))


# ── methods, headers, bodies ─────────────────────────────────────────────────

def test_a_plain_get_sends_curls_headers(curl, web):
    got = echo(curl(f"{web.url}/echo"))
    assert got["method"] == "GET"
    assert got["headers"]["User-Agent"] == "curl/8.11.1"
    assert got["headers"]["Accept"] == "*/*"
    assert "Accept-Encoding" not in got["headers"]  # curl sends none without --compressed


def test_a_url_without_a_scheme_is_http(curl, web):
    assert echo(curl(f"127.0.0.1:{web.port}/echo"))["method"] == "GET"


@pytest.mark.parametrize("args", [["-X", "PATCH"], ["-XPATCH"], ["--request", "PATCH"], ["--request=PATCH"]])
def test_the_method(curl, web, args):
    curl(*args, f"{web.url}/echo")
    assert web.last["method"] == "PATCH"


def test_headers_added_removed_and_empty(curl, web):
    got = echo(curl(f"{web.url}/echo", "-H", "X-One: 1", "-H", "Accept:", "-H", "X-Empty;", "-H", "User-Agent: me"))
    headers = got["headers"]
    assert headers["X-One"] == "1" and headers["X-Empty"] == "" and headers["User-Agent"] == "me"
    assert "Accept" not in headers


def test_headers_from_a_file(curl, web, tmp_path):
    (tmp_path / "h.txt").write_text("X-A: 1\nX-B: 2\n")
    headers = echo(curl(f"{web.url}/echo", "-H", f"@{tmp_path / 'h.txt'}"))["headers"]
    assert (headers["X-A"], headers["X-B"]) == ("1", "2")


def test_user_agent_and_referer(curl, web):
    headers = echo(curl("-A", "agent/1", "-e", "https://ref.example/", f"{web.url}/echo"))["headers"]
    assert (headers["User-Agent"], headers["Referer"]) == ("agent/1", "https://ref.example/")


def test_data_posts_form_urlencoded_and_several_are_joined(curl, web):
    got = echo(curl(f"{web.url}/echo", "-d", "a=1", "-d", "b=2"))
    assert got["method"] == "POST" and got["body"] == "a=1&b=2"
    assert got["headers"]["Content-Type"] == "application/x-www-form-urlencoded"


def test_data_variants(curl, web, tmp_path):
    (tmp_path / "body.txt").write_text("line1\nline2\n")
    curl(f"{web.url}/echo", "-d", f"@{tmp_path / 'body.txt'}")
    assert web.last["body"] == "line1line2"  # -d @file drops the newlines, as curl does
    curl(f"{web.url}/echo", "--data-binary", f"@{tmp_path / 'body.txt'}")
    assert web.last["body"] == "line1\nline2\n"
    curl(f"{web.url}/echo", "--data-raw", "@not-a-file")
    assert web.last["body"] == "@not-a-file"
    curl(f"{web.url}/echo", "--data-urlencode", "q=a b&c", "--data-urlencode", "plain text")
    assert web.last["body"] == "q=a%20b%26c&plain%20text"
    curl(f"{web.url}/echo", "-d", "@-", stdin="from stdin")
    assert web.last["body"] == "from stdin"


def test_json_sets_the_types(curl, web):
    got = echo(curl(f"{web.url}/echo", "--json", '{"a": 1}'))
    assert got["method"] == "POST" and json.loads(got["body"]) == {"a": 1}
    assert got["headers"]["Content-Type"] == "application/json"
    assert got["headers"]["Accept"] == "application/json"


def test_get_puts_the_data_in_the_query(curl, web):
    curl("-G", f"{web.url}/echo", "-d", "a=1", "--data-urlencode", "b=x y")
    assert web.last["method"] == "GET" and web.last["path"] == "/echo?a=1&b=x%20y"
    curl(f"{web.url}/echo?z=0", "--url-query", "k=v")
    assert web.last["path"] == "/echo?z=0&k=v"


def test_multipart_form_with_a_file(curl, web, tmp_path):
    (tmp_path / "up.txt").write_text("uploaded\n")
    curl(f"{web.url}/echo", "-F", "name=ada", "-F", f"file=@{tmp_path / 'up.txt'};type=text/plain")
    body = web.last["body"]
    assert web.last["headers"]["Content-Type"].startswith("multipart/form-data; boundary=")
    assert 'name="name"' in body and "ada" in body
    assert 'filename="up.txt"' in body and "uploaded" in body and "Content-Type: text/plain" in body


def test_upload_file_is_a_put(curl, web, tmp_path):
    (tmp_path / "up.txt").write_text("payload")
    curl("-T", str(tmp_path / "up.txt"), f"{web.url}/echo")
    assert (web.last["method"], web.last["body"]) == ("PUT", "payload")


# ── auth, cookies, redirects ─────────────────────────────────────────────────

def test_basic_and_bearer_auth(curl, web):
    headers = echo(curl("-u", "ada:secret", f"{web.url}/echo"))["headers"]
    assert headers["Authorization"] == "Basic YWRhOnNlY3JldA=="
    headers = echo(curl("--oauth2-bearer", "tok", f"{web.url}/echo"))["headers"]
    assert headers["Authorization"] == "Bearer tok"


def test_digest_auth(curl, web):
    assert text(curl("--digest", "-u", "ada:secret", f"{web.url}/digest")) == "digest ok\n"
    assert web.last["headers"]["Authorization"].startswith('Digest username="ada"')


def test_cookies_sent_and_saved(curl, web, tmp_path):
    assert echo(curl("-b", "a=1; b=2", f"{web.url}/echo"))["headers"]["Cookie"] == "a=1; b=2"
    jar = tmp_path / "jar.txt"
    curl("-c", str(jar), f"{web.url}/cookie")
    assert "token\txyz" in jar.read_text()
    assert echo(curl("-b", str(jar), f"{web.url}/echo"))["headers"]["Cookie"] == "token=xyz"


def test_redirects_are_followed_only_with_location(curl, web):
    assert text(curl(f"{web.url}/redirect/2")) == ""  # curl's default: don't follow
    assert echo(curl("-L", f"{web.url}/redirect/2"))["path"] == "/echo"
    with pytest.raises(ModuleError, match=r"\(47\) Maximum \(1\) redirects followed") as info:
        curl("-L", "--max-redirs", "1", f"{web.url}/redirect/3")
    assert info.value.status == 47


# ── output ───────────────────────────────────────────────────────────────────

def test_head_and_include(curl, web):
    out = text(curl("-I", f"{web.url}/json"))
    assert out.startswith("HTTP/1.1 200 OK\n") and "Content-Type: application/json" in out
    assert web.last["method"] == "HEAD" and "{" not in out
    out = text(curl("-i", f"{web.url}/json"))
    assert out.startswith("HTTP/1.1 200") and out.rstrip().endswith("}")


def test_include_with_location_shows_every_hop(curl, web):
    out = text(curl("-iL", f"{web.url}/redirect/1"))
    assert out.count("HTTP/1.1 ") == 2 and "302" in out


def test_verbose_goes_to_the_error_stream(ctx, web):
    result = run_line(f"curl -v {web.url}/json 2>&1", ctx)
    out = to_text(result.output)
    assert "> GET /json HTTP/1.1" in out and "< HTTP/1.1 200 OK" in out and '"name": "ada"' in out
    result = run_line(f"curl -v {web.url}/json 2>/dev/null", ctx)
    assert "> GET" not in to_text(result.output)


def test_write_out(curl, web):
    out = text(curl("-s", "-o", "/dev/null", "-w",
                    r"%{http_code} %{content_type} %{method} %header{content-length}\n", f"{web.url}/json"))
    assert out.startswith("200 application/json GET ") and out.endswith("\n")
    out = text(curl("-sL", "-w", "%{num_redirects} %{url_effective}", f"{web.url}/redirect/2"))
    assert out.endswith(f"2 {web.url}/echo")


def test_output_files(curl, web, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert text(curl("-o", "out.json", f"{web.url}/json")) == ""
    assert json.loads((tmp_path / "out.json").read_text())["a"]["name"] == "ada"
    curl("-O", f"{web.url}/files/report.txt")
    assert (tmp_path / "report.txt").read_text() == "file body\n"
    curl("--create-dirs", "--output-dir", "sub/dir", "-O", f"{web.url}/files/x.txt")
    assert (tmp_path / "sub" / "dir" / "x.txt").is_file()
    curl("-o", "img.png", f"{web.url}/binary")
    assert (tmp_path / "img.png").read_bytes().startswith(b"\x89PNG")


def test_binary_is_not_printed(curl, web):
    with pytest.raises(ModuleError, match="Binary output can mess up your terminal") as info:
        curl(f"{web.url}/binary")
    assert info.value.status == 23


def test_fail_gives_22(curl, web):
    assert text(curl(f"{web.url}/status/404")) == "status 404\n"  # without -f: the body, status 0
    with pytest.raises(ModuleError, match=r"\(22\) The requested URL returned error: 404") as info:
        curl("-f", f"{web.url}/status/404")
    assert info.value.status == 22
    result = curl("-fs", f"{web.url}/status/500")
    assert isinstance(result, WithStatus) and (result.value, result.status) == ("", 22)
    result = curl("--fail-with-body", "-s", f"{web.url}/status/500")
    assert (text(result), result.status) == ("status 500\n", 22)


def test_combined_short_flags(curl, web):
    assert echo(curl("-fsSL", f"{web.url}/redirect/1"))["path"] == "/echo"


def test_several_urls(curl, web):
    out = text(curl(f"{web.url}/status/200", f"{web.url}/status/201"))
    assert out == "status 200\nstatus 201\n"


def test_jq(curl, web):
    assert text(curl(f"{web.url}/json", "--jq", "a.name")) == "ada\n"
    assert text(curl(f"{web.url}/json", "--jq", ".a.b[1]")) == "2\n"
    assert text(curl(f"{web.url}/json", "--jq", "list")) == "x\ny\n"


def test_range_and_max_filesize(curl, web):
    curl("-r", "0-9", f"{web.url}/echo")
    assert web.last["headers"]["Range"] == "bytes=0-9"
    web.big = 5000
    with pytest.raises(ModuleError, match=r"\(63\) Maximum file size exceeded") as info:
        curl("--max-filesize", "1K", f"{web.url}/big")
    assert info.value.status == 63


# ── errors, timeouts, retries, proxies ───────────────────────────────────────

def test_timeout_is_28(curl, web):
    with pytest.raises(ModuleError, match=r"\(28\) Operation timed out") as info:
        curl("-m", "0.3", f"{web.url}/slow")
    assert info.value.status == 28


def test_connection_refused_is_7_and_silent_keeps_the_status(curl):
    with pytest.raises(ModuleError, match=r"\(7\) Failed to connect to 127.0.0.1 port 1"):
        curl("http://127.0.0.1:1/")
    result = curl("-s", "http://127.0.0.1:1/")
    assert (result.value, result.status) == ("", 7)


def test_unknown_and_unsupported_options_are_2(curl):
    with pytest.raises(ModuleError, match="option --frobnicate: is unknown") as info:
        curl("--frobnicate", "http://x")
    assert info.value.status == 2
    with pytest.raises(ModuleError, match=r"not supported yet \(SOCKS proxies"):
        curl("--socks5", "h:1080", "http://x")
    with pytest.raises(ModuleError, match="not supported yet"):
        curl("-x", "socks5://h:1080", "http://x")
    with pytest.raises(ModuleError, match="requires parameter"):
        curl("http://x", "-H")


def test_retry_on_503(curl, web, monkeypatch):
    import time

    monkeypatch.setattr(time, "sleep", lambda s: None)
    web.flaky = 2
    assert text(curl("--retry", "3", f"{web.url}/flaky")) == "ok\n"
    assert len(web.requests) == 3


def test_an_http_proxy(curl, web):
    got = echo(curl("-x", web.url, "-U", "p:w", "http://example.invalid/echo"))
    assert got["path"] == "http://example.invalid/echo"  # the absolute URL: it went through the proxy
    assert got["headers"]["Proxy-Authorization"] == "Basic cDp3"


def test_env_proxy_and_noproxy(curl, web, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", web.url)
    assert echo(curl("http://example.invalid/echo"))["path"] == "http://example.invalid/echo"
    with pytest.raises(ModuleError):  # --noproxy: straight to the (unresolvable) host
        curl("--noproxy", "*", "http://example.invalid/echo")


def test_insecure_is_accepted(curl, web):
    assert echo(curl("-k", f"{web.url}/echo"))["method"] == "GET"


def test_help_and_version(curl):
    assert "Usage: curl [options...] <url>" in text(curl("--help"))
    assert text(curl("-V")).startswith("curl 8.11.1 (ShellCraft, Python requests ")


def test_exit_status_reaches_the_shell(ctx, web):
    result = run_line(f"curl -s {web.url}/status/404 -f; echo $?", ctx)
    assert to_text(result.output).strip().endswith("22")


# ── pasted commands ──────────────────────────────────────────────────────────

BASH_PASTE = r"""curl 'URL/echo?q=1' \
  -H 'accept: application/json, text/plain, */*' \
  -H 'accept-language: en-US,en;q=0.9' \
  -H 'content-type: application/json' \
  -b 'session=abc123; theme=dark' \
  -H 'user-agent: Mozilla/5.0 (X11; Linux x86_64)' \
  --data-raw $'{"name":"O\'Brien","note":"line1\\nline2","pct":"100%"}'"""



def chrome_cmd(argv):
    """A command line the way Chrome's "Copy as cURL (cmd)" writes it (its escapeStringWin)."""
    import re

    def esc(s):
        s = s.replace("\\", "\\\\").replace('"', '\\"')
        s = re.sub(r"[^a-zA-Z0-9\s_\-:=+~'/.',?;()*`&]", lambda m: "^" + m.group(0), s)
        s = re.sub(r"%(?=[a-zA-Z0-9_])", "%^", s)
        s = re.sub(r"\r?\n", "^\n\n", s)
        return '^"' + s + '^"'
    return argv[0] + " " + " ^\n  ".join(a if a.startswith("-") else esc(a) for a in argv[1:])


CMD_PASTE = chrome_cmd(["curl", "URL/echo?q=1", "-H", "accept: application/json, text/plain, */*",
                        "-H", "accept-language: en-US,en;q=0.9", "-H", "content-type: application/json",
                        "-b", "session=abc123; theme=dark", "-H", "user-agent: Mozilla/5.0 (X11; Linux x86_64)",
                        "--data-raw", '{"name":"O\'Brien","note":"%PATH% & <b>","pct":"100%"}'])


@pytest.mark.parametrize("paste", [BASH_PASTE, CMD_PASTE], ids=["bash", "cmd"])
def test_a_pasted_browser_command_runs_as_is(ctx, web, paste):
    result = run_line(paste.replace("URL", web.url), ctx)
    assert result.status == 0, to_text(result.output)
    got = web.last
    assert got["method"] == "POST" and got["path"] == "/echo?q=1"
    assert got["headers"]["accept"] == "application/json, text/plain, */*"
    assert got["headers"]["user-agent"] == "Mozilla/5.0 (X11; Linux x86_64)"
    assert got["headers"]["Cookie"] == "session=abc123; theme=dark"
    note = "line1\nline2" if paste is BASH_PASTE else "%PATH% & <b>"
    assert json.loads(got["body"]) == {"name": "O'Brien", "note": note, "pct": "100%"}


def test_the_parser_reads_ansi_c_strings_and_continuations():
    from shellcraft.parser import join_continued_lines, parse

    args = parse(r"""echo $'a\tb\n\x41é\101\'q\\' 'plain'""").segments[0].args
    assert args == ["a\tb\nAé" + "A'q\\", "plain"]
    assert join_continued_lines("echo a \\\n  b") == "echo a   b"
    assert join_continued_lines("echo 'a \\\n b'") == "echo 'a \\\n b'"  # inside single quotes: kept


def test_the_prompt_waits_for_a_continued_line():
    from shellcraft.shell import _continued

    assert _continued("curl https://x \\")
    assert not _continued("echo a\\\\")
    assert _continued('curl ^"https://x^" ^')
    assert not _continued("echo 2^")


def test_cmd_form_conversion():
    from shellcraft import curlpaste

    assert curlpaste.from_cmd("echo ^\"x^\"") == "echo ^\"x^\""  # only curl / fetch lines
    assert curlpaste.from_cmd('curl ^"https://x/?a=1^&b=2^" -H ^"A: b^"') == "curl $'https://x/?a=1&b=2' -H $'A: b'"


# ── command: the OS program ──────────────────────────────────────────────────

def test_command_runs_the_os_program(ctx):
    import shutil

    exe = shutil.which("python3") or shutil.which("python") or sys.executable
    name = "python3" if shutil.which("python3") else "python"
    ctx.allow_system = True
    assert to_text(run_line(f"command {name} -c 'print(6*7)'", ctx).output) == "42\n"
    assert to_text(run_line(f"command -v {name}", ctx).output).strip() == exe
    assert run_line("command -v no-such-program-xyz", ctx).status == 1
    ctx.allow_system = False
    with pytest.raises(PipelineError, match="OS commands are off"):
        run_line(f"command {name} -c 1", ctx)
    with pytest.raises(PipelineError, match="no such program"):
        run_line("command no-such-program-xyz", ctx)


def test_which_shows_the_module_and_the_os_curl(ctx):
    import shutil

    if not shutil.which("curl"):
        pytest.skip("no OS curl")
    lines = to_text(run_line("which -a curl", ctx).output).splitlines()
    assert lines[0].startswith("curl: ShellCraft module") and "(shadowed" in lines[1]


# ── MCP ──────────────────────────────────────────────────────────────────────

def test_the_mcp_curl_tool(registry, web):
    spec = registry.get("curl")
    argv = spec.argv({"url": [f"{web.url}/json"], "jq": "a.name", "header": ["X-A: 1"], "location": True})
    assert to_text(spec.run(argv, "")) == "ada\n"
    assert web.last["headers"]["X-A"] == "1"
    assert registry.get("fetch").skill.expose is False


def test_curl_error_is_a_command_error_with_status():
    sys.path.insert(0, "modules")
    from _curl import CurlError

    assert issubclass(CurlError, CommandError) and issubclass(CurlError, ModuleError)


def test_over_mcp_curl_writes_no_files(registry, web, tmp_path, monkeypatch):
    from shellcraft.context import ShellContext

    monkeypatch.chdir(tmp_path)
    ctx = ShellContext(registry=registry, allow_writes=False)
    with pytest.raises(PipelineError, match="would write files"):
        run_line(f"curl -o out.txt {web.url}/json", ctx)
    assert not (tmp_path / "out.txt").exists()
    assert "ada" in to_text(run_line(f"curl -s {web.url}/json", ctx).output)
    assert to_text(run_line(f"curl -so /dev/null -w '%{{http_code}}' {web.url}/json", ctx).output) == "200"
