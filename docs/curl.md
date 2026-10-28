# curl

← [Manual](README.md)

This page is for you if you call web APIs, download files, or test HTTP services from your shell.
After reading it you'll know how ShellCraft's `curl` differs from the one on your system, which of
curl's options work, how to paste a request straight from your browser, and what to do when a
request fails.

`man curl` lists every option. This page explains how it fits together.

## What ShellCraft's curl is

In most shells, `curl` is a program installed on your system. In ShellCraft, `curl` is a
**builtin**: a command that's part of ShellCraft itself, always there, like `grep` or `cat`. It
reads curl's command line, then sends the request with Python's `requests` library. The code is in
`shellcraft/commands/curl.py` if you're curious.

Why do it that way? Two reasons. First, it works the same on Linux, macOS and Windows, whether or
not curl is installed. Second, you can take a curl command from an API's docs, a README or your
browser's developer tools and run it as it is. The options, the output and the exit statuses are
curl's, so a script that checks `$?` after `curl` behaves the same.

Short options combine the way curl's do (`-sSL`, `-fsSL`), a value can be attached or follow
(`-XPOST` or `-X POST`), and options can go anywhere on the line. It sends
`User-Agent: curl/8.11.1` and `Accept: */*`, as curl does, so servers treat it like curl.

## Common requests

Here are the requests people make most, as a table to copy from. Pick the row closest to what you
want and change the URL.

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
| Use a client certificate | `curl -E client.pem --key client.key URL` |
| Save the body | `curl -o page.html URL`, `curl -O URL/file.zip` |
| Only the status code | `curl -s -o /dev/null -w '%{http_code}\n' URL` |
| Retry a flaky service | `curl --retry 3 --retry-delay 2 URL` |
| One field of a JSON reply | `curl -s URL --jq data.items.0.name` |

A few of these need a word of explanation.

**`-fsSL`** is four options in one, and you'll see it in a lot of install instructions. `-f` makes
an HTTP error such as 404 count as a failure. `-s` hides the error messages and progress. `-S` brings
back the error message when something does fail, so you're not left guessing. `-L` follows
redirects, which matters because many download links redirect at least once.

**Quotes around the header** in `-H "Authorization: Bearer $TOKEN"` keep the header in one piece,
because it contains a space. Double quotes still let `$TOKEN` be replaced by the variable's value.
In the JSON example, single quotes are used instead, because JSON is full of double quotes and you
don't want the shell touching anything inside.

**`-w '%{http_code}\n'`** prints the status code after the request. Combined with `-o /dev/null`,
which throws the body away, you get only the number. That's the quickest way to check whether a
server is up from a script. Other `-w` variables, such as `%{time_total}`, are in `man curl`.

**`--jq`** is ShellCraft's own addition. It picks one field out of a JSON reply, so you don't need a
separate `jq` step for simple lookups. `data.items.0.name` means "the `data` object, its `items`
list, the first item, its `name`". The jq style `.data.items[0].name` works too.

## How it behaves, the curl way

Since it copies curl, it also copies the parts of curl that surprise people:

- **No redirects without `-L`.** If a URL redirects, you get the short redirect page back, not
  the page it points to. Add `-L`.
- **An HTTP error still prints the body.** A 404 or 500 isn't a failure to curl, it's an answer.
  You see the server's error page and the exit status is 0. Add `-f` to make it a failure with
  exit status 22.
- **A binary body isn't printed to the screen.** If the reply is an image, a zip or a PDF, you get
  `curl: Binary output can mess up your terminal.` with status 23. Save it with `-o FILE` or `-O`.
- **`-v` writes to the error stream.** The request lines start with `>` and the response headers
  with `<`. Because it's the error stream, `2>` and `2>&1` work on it, and a pipe after `curl -v`
  only gets the body.

## Exit statuses

Every command finishes with an exit status, a number that `$?` holds afterwards. 0 means success.
Anything else means something went wrong, and curl uses the number to say what. ShellCraft's `curl`
maps each failure to the same number curl would use, so a script written for curl keeps working.

| Status | What happened |
| --- | --- |
| `0` | It worked. An HTTP error page without `-f` counts as this |
| `2` | A bad option, or one that's [not supported yet](#not-supported-yet) |
| `3` | The URL is malformed |
| `6` | The host name can't be resolved |
| `7` | Couldn't connect: nothing is listening, or a firewall said no |
| `22` | The server answered with an HTTP error, and you used `-f` |
| `23` | Couldn't write the output, or the body is binary and would go to the screen |
| `26` | Couldn't read a file you gave with `-d @FILE` or `-F name=@FILE` |
| `28` | Timed out (`-m` or `--connect-timeout`) |
| `37` | Couldn't open the file in a `file://` URL |
| `47` | Too many redirects |
| `56` | The connection broke while receiving the reply |
| `60` | The server's TLS certificate didn't check out |
| `63` | The reply is larger than `--max-filesize` |

`-s` hides the message but not the status. That makes this pattern work in a script, where you
want to decide what to do without any noise on the screen:

```
curl -sf https://example.com/health > /dev/null
echo $?
```

If the server answers with 200, you see `0`. If it answers 404, you see `22`. If the host doesn't
exist, you see `6`.

## If a request fails

The message tells you which kind of failure it is. These are the ones you'll meet most:

- `curl: (6) Could not resolve host: api.exmaple.com` means the name doesn't exist. Usually a typo,
  or you're offline, or the name only resolves on a VPN you're not connected to.
- `curl: (7) Failed to connect to 127.0.0.1 port 9: ... Connection refused` means the machine is
  there but nothing is listening on that port. Check the port number and that the service is running.
- `curl: (22) The requested URL returned error: 404` comes with `-f`. The server is fine, the path
  is wrong. Drop `-f` to see the server's own error page, which often says why.
- `curl: (28) Operation timed out after ... milliseconds` means it took longer than your `-m`
  limit. Raise the limit, or add `--retry 3` for a service that's sometimes slow.
- `curl: (60) SSL certificate problem: ...` means the certificate isn't trusted. For a test server
  with a self-signed certificate, `-k` skips the check. For a company certificate authority, point
  at it with `--cacert FILE`.

## Pasting "Copy as cURL" from your browser

This is one of the handiest uses. Say a web page loads some data and you want to fetch the same
thing from your shell, with the same cookies and headers. Your browser can give you the exact
request as a curl command.

1. In Chrome, Edge or Firefox, open the developer tools and go to the **Network** tab.
2. Reload the page, then right-click the request you want.
3. Pick **Copy as cURL**.
4. Paste it at the ShellCraft prompt and press Enter.

The request goes out with the same method, headers, cookies and body as in the browser. Pasting
also works in rc files, funcs and scripts.

Browsers offer more than one flavor of the command, and both common ones work:

- **Copy as cURL (bash)**, which you get on Linux and macOS, and as an option on Windows. Its lines
  end in `\`, which joins them into one command. A body with quotes or newlines is written as a
  `$'…'` string, and ShellCraft reads those the way bash does.
- **Copy as cURL (cmd)**, the Windows default. It wraps every argument in `^"…^"`, escapes special
  characters with `^`, and ends continued lines with `^`. ShellCraft spots a `curl` line written
  that way and turns it into its own quoting before the command runs, so you paste it unchanged.
  That conversion lives in `shellcraft/curlpaste.py`.

**Copy as PowerShell** is not supported yet. Use one of the cURL options instead.

A word of care: a copied request carries your session cookies and tokens. Anyone who sees the
command can act as you on that site until the session expires, so don't paste it into a chat or
commit it to a file.

## The OS curl

Sometimes you need the real curl: an option on the [not supported yet](#not-supported-yet) list,
or HTTP/3. ShellCraft's `curl` wins over the one on your `PATH`, so put `command` in front to run
the OS program:

```
command curl --http3 https://example.com
command -v curl              # where it is
which -a curl                # both: ShellCraft's builtin and the OS program
```

Running OS programs has to be switched on first, because ShellCraft runs only its own commands
until you allow your system's. If it's off, `command curl` stops with `OS commands are off` and
tells you the fix: `settings system_commands on`.

## Reading files and keeping lines

`curl` is for URLs. It does read a `file://` URL, but for a local file `cat` is shorter and is what
the next person reading your script expects. To keep only the first lines of a reply, pipe it to
`head`:

```
cat server.log | grep -c ERROR
curl -s https://example.com | head -n 20
curl -s https://api.example.com/users --jq data | grep -i admin
```

Because `curl` writes the body to its output, it pipes into any other command, the same as `cat`.

## AI clients

A connected AI client runs `curl` through the `shellcraft_pipeline` tool, like any builtin. For
example, it might call `curl -s https://api.example.com/items --jq data`.

Commands run for an AI client there write no files, so `-o`, `-O`, `-D FILE` and `-c` are refused,
and the reply comes back to the AI as text. See [MCP and the AI co-pilot](mcp-and-ai.md) for what
else an AI can and can't do.

## Not supported yet

These are the parts of curl that ShellCraft's `curl` doesn't do yet. The list is the `TODO` in
`shellcraft/commands/curl.py`, and `man curl` shows it too. An option from this list stops with
"not supported yet" and status 2, or isn't recognised:

- SOCKS proxies (`--socks5`, `-x socks5://…`)
- "Copy as PowerShell" pastes
- HTTP/2 and HTTP/3. `--http2` is accepted, and HTTP/1.1 is used
- URL globbing: `[1-10]`, `{a,b}`
- `-K` / `--config` files
- `--resolve`, `--connect-to`, `-4`, `-6`
- `--limit-rate`, `--parallel`
- `--trace`, `--trace-ascii`
- NTLM and Negotiate auth
- FTP, SCP, SMTP and the other protocols
- the progress meter, `--libcurl`
- `-u USER` without a password. Real curl asks for it; this one stops and tells you to use
  `-u USER:PASSWORD`

For example, `curl --socks5 127.0.0.1:1080 https://example.com` stops with:

```
curl: option --socks5: not supported yet (SOCKS proxies; see man curl)
```

One more gap isn't an error, so it's easy to miss. With real curl, `--cert file.pem:secret` gives
the password for an encrypted private key. ShellCraft's `curl` accepts that form but drops the
password, because `requests` has no way to pass a key password on. With an encrypted key the
request won't work. Use a copy of the key without a password, or run the OS curl.

`command curl` runs the OS program for any of these.
