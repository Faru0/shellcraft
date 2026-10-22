# curl and fetch

← [README](../README.md) · [All docs](README.md)

ShellCraft's `curl` takes curl's command line and makes the request with Python's `requests`, so
it works the same on Linux, macOS and Windows, with or without curl installed. A command from an
API's docs, a README or the browser's developer tools runs as it is. `fetch` is the same command
under another name, with a few extras for reading files.

Every option is in `man curl`; this page is the overview.

## What works

| You want to | Type |
| --- | --- |
| Read a page or an API | `curl https://api.example.com/items` |
| Follow redirects, quietly, fail on errors | `curl -fsSL https://example.com/install.sh` |
| Only the headers | `curl -I https://example.com` |
| Headers and body | `curl -i https://example.com` |
| See the whole exchange | `curl -v https://example.com` (`> ` request, `< ` response, on the error stream) |
| Send a header, remove one | `curl -H "Authorization: Bearer $TOKEN" -H "Accept:" URL` |
| POST a form | `curl -d user=ada -d lang=py URL` |
| POST JSON | `curl --json '{"name": "x"}' URL` |
| Send a file, or stdin | `curl -d @body.json URL`, `cat body.json \| curl -d @- URL` |
| Upload a file in a form | `curl -F "file=@report.pdf;type=application/pdf" -F note=weekly URL` |
| PUT a file | `curl -T data.csv URL` |
| Another method | `curl -X DELETE URL` |
| Log in | `curl -u user:password URL` (`--digest` for digest), `--oauth2-bearer TOKEN` |
| Keep cookies | `curl -c jar.txt URL` then `curl -b jar.txt URL` |
| Go through a proxy (Burp, ZAP) | `curl -x http://127.0.0.1:8080 -k URL` |
| Save the body | `curl -o page.html URL`, `curl -O URL/file.zip` |
| Just the status code | `curl -s -o /dev/null -w '%{http_code}\n' URL` |
| Retry a flaky service | `curl --retry 3 --retry-delay 2 URL` |
| One field of a JSON reply | `curl -s URL --jq data.items.0.name` |

It behaves as curl does: no redirects without `-L`, an HTTP error's body is still printed (`-f`
turns it into exit status 22), a binary body isn't printed to the screen (use `-o`), and the exit
statuses are curl's (`6` unknown host, `7` can't connect, `28` timed out…), in `$?`.

## Pasting "Copy as cURL"

In Chrome, Edge or Firefox, open the developer tools, right-click a request in the Network tab
and pick **Copy as cURL**. Paste it at the ShellCraft prompt and press Enter.

- **Copy as cURL (bash)** (Linux, macOS, and Windows' bash option): its lines end in `\`, which
  joins them into one command, and its `$'…'` strings (a body with quotes or newlines) are read
  as bash reads them.
- **Copy as cURL (cmd)** (Windows): its `^"…^"` quoting, `^` escapes and `^` line ends are turned
  into ShellCraft's quoting before the command runs.

The request goes out with the same method, headers, cookies and body as in the browser. This also
works in rc files, funcs and scripts. **Copy as PowerShell** is not supported yet.

## The OS curl

ShellCraft's `curl` wins over the one on your PATH. To run that one, put `command` in front (OS
commands must be on: `settings system_commands on`):

```
command curl --http3 https://example.com
command -v curl              # where it is
which -a curl                # both: ShellCraft's module and the OS program
```

## fetch

`fetch` is `curl` with fetch's habits:

- a source without a scheme is a **local file** (`fetch notes.txt`), and no source (or `-`) reads
  **stdin**;
- `--head N` keeps the first *N* lines (curl's HEAD request is `-I`);
- responses and files are limited to 10 MB unless `--max-size` says otherwise;
- `--json PATH` from older versions still works as `--jq PATH`, and `--timeout`, `--max-size` and
  `--method` still work.

```
fetch server.log | filter -c ERROR
fetch https://example.com --head 20
fetch data.json --jq users | filter -i admin
```

## AI clients

Over MCP, `curl` is a tool with named parameters (`url`, `method`, `header`, `data`, `json`,
`form`, `user`, `location`, `proxy`, `jq`…). `fetch` is not offered as a second tool. Commands run
for an AI client write no files, so `-o`, `-O`, `-D FILE` and `-c` are refused there; the response
comes back as text.

## Not supported yet

These stop with "not supported yet" (status 2), or aren't recognised:

- SOCKS proxies (`--socks5`, `-x socks5://…`)
- "Copy as PowerShell" pastes
- HTTP/2 and HTTP/3 (`--http2` is accepted and HTTP/1.1 is used)
- URL globbing: `[1-10]`, `{a,b}`
- `-K` / `--config` files
- `--resolve`, `--connect-to`, `-4`, `-6`
- `--limit-rate`, `--parallel`
- `--trace`, `--trace-ascii`
- NTLM and Negotiate auth
- FTP, SCP, SMTP and the other protocols
- the progress meter, `--libcurl`
- `-u USER` without a password (curl asks for it)

`command curl` runs the OS program for any of these.
