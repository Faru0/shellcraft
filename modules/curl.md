# curl

HTTP requests the way **curl** makes them, built on Python's `requests`. The command line is
curl's: a curl command from a README, an API's docs or a browser's **Copy as cURL** runs here as
it is, and its output and exit statuses are curl's.

## Synopsis

```
curl [OPTIONS] URL...
```

## Description

`curl` sends one request per URL, one after another, and writes the response body to its output,
so it pipes like any command: `curl -s https://api.example.com/items | jq -s`. Short options
combine (`-sSL`, `-fsSL`), values attach or follow (`-XPOST`, `-X POST`), options go anywhere.

As curl does, it sends `User-Agent: curl/8.11.1` and `Accept: */*`, follows no redirects without
`-L`, prints the body of an HTTP error unless `-f` is given, and refuses to print a binary body
("Binary output can mess up your terminal"): save it with `-o FILE` or `-O`.

The OS program is still there: `command curl …` runs it (with OS commands on), and
`which -a curl` shows both.

## Options

| Area | Options |
| --- | --- |
| Method and URL | `URL`, `--url URL`, `-X`/`--request METHOD`, `-G`/`--get` (the `-d` data goes in the query), `-I`/`--head`, `--url-query DATA` |
| Headers | `-H`/`--header 'Name: value'` (repeat it; `'Name:'` removes a default header, `'Name;'` sends it empty, `@FILE` reads headers from a file), `-A`/`--user-agent`, `-e`/`--referer`, `--compressed` |
| Body | `-d`/`--data`, `--data-ascii`, `--data-raw`, `--data-binary`, `--data-urlencode`, `--json`, `-F`/`--form` (`name=@file;type=…`, `name=<file`), `--form-string`, `-T`/`--upload-file` (a PUT). `@FILE` reads a file, `@-` reads stdin. Several `-d` are joined with `&`. |
| Auth and cookies | `-u`/`--user user:password`, `--basic`, `--digest`, `--oauth2-bearer TOKEN`, `-b`/`--cookie` (`'a=1; b=2'` or a cookie file), `-c`/`--cookie-jar FILE` |
| Redirects | `-L`/`--location`, `--max-redirs N` |
| TLS | `-k`/`--insecure`, `--cacert FILE`, `--capath`, `-E`/`--cert FILE`, `--key FILE` |
| Proxy | `-x`/`--proxy [http://\|https://]HOST[:PORT]`, `-U`/`--proxy-user`, `--noproxy HOSTS`. `HTTP_PROXY`, `HTTPS_PROXY` and `NO_PROXY` are honored, as curl does. |
| Time and retries | `-m`/`--max-time`, `--connect-timeout`, `--retry N`, `--retry-delay`, `--retry-max-time`, `--retry-all-errors`, `--retry-connrefused` |
| Output | `-o`/`--output FILE`, `-O`/`--remote-name`, `--remote-name-all`, `--output-dir`, `--create-dirs`, `-i`/`--include`, `-D`/`--dump-header FILE`, `-s`/`--silent`, `-S`/`--show-error`, `-v`/`--verbose`, `-w`/`--write-out`, `-f`/`--fail`, `--fail-with-body`, `-r`/`--range`, `--max-filesize` |
| ShellCraft | `--jq PATH`: print one field of a JSON response (`data.items.0.name` or `.data.items[0].name`) |
| Accepted, no effect | `--http1.1`, `--http2`, `-g`/`--globoff`, `--path-as-is`, `-N`/`--no-buffer`, `-#`, `--no-progress-meter`, `-q`, `--raw`, `--ssl-no-revoke` |

`-v` writes the request (`>` lines) and the response headers (`<` lines) to the error stream, so
`2>` and `2>&1` work with it.

### `-w` / `--write-out` variables

`%{http_code}` (also `%{response_code}`), `%{size_download}`, `%{size_header}`, `%{time_total}`,
`%{content_type}`, `%{url_effective}`, `%{redirect_url}`, `%{num_redirects}`, `%{method}`,
`%{scheme}`, `%header{Name}`, and `\n`, `\t`.

## Pasting a curl command

Right-click a request in the browser's developer tools (Network tab), **Copy as cURL**, and paste
it at the prompt:

- **Copy as cURL (bash)**: works as it is. Its `\` line ends continue the command and its
  `$'…'` strings are understood.
- **Copy as cURL (cmd)**, Windows: works as it is. Its `^"…^"` quoting and `^` line ends are
  turned into ShellCraft's quoting before the line runs.

The same goes for rc files and funcs.

## Exit statuses

As curl's: `0` success, `2` a bad option, `3` a bad URL, `6` the host can't be resolved, `7`
can't connect, `22` an HTTP error with `-f`, `23` a write error or a binary body, `28` timed out,
`47` too many redirects, `56` a receive error, `60` the TLS certificate failed, `63` larger than
`--max-filesize`. `-s` hides the message; the status stays in `$?`.

## Not supported yet

SOCKS proxies · "Copy as PowerShell" pastes · HTTP/2 and HTTP/3 · URL globbing (`[1-10]`,
`{a,b}`) · `-K` config files · `--resolve` / `--connect-to` · `-4` / `-6` · `--limit-rate` ·
`--parallel` · `--trace` / `--trace-ascii` · NTLM and Negotiate auth · FTP, SCP, SMTP and the other
protocols · the progress meter · `--libcurl` · `-u USER` without a password (asking for it).

An option from this list stops with "not supported yet" and status 2.

## Examples

```
curl https://example.com
curl -sSL https://example.com/install.sh | head
curl -I https://example.com
curl -s https://api.github.com/repos/python/cpython --jq stargazers_count
curl -X POST https://api.example.com/items -H "Authorization: Bearer $TOKEN" --json '{"name": "x"}'
curl -F "file=@report.pdf" -F "note=weekly" https://upload.example.com/
curl -u admin:secret --digest https://intranet.example.com/status
curl -x http://127.0.0.1:8080 -k https://target.example.com/   # through Burp or ZAP
curl -s -o /dev/null -w '%{http_code} %{time_total}\n' https://example.com
curl -O https://example.com/files/data.csv
```

## See also

`man fetch` · `man command` · `docs/curl.md`
