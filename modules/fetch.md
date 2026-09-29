# fetch

Pull text into a ShellCraft pipeline from a **URL**, a **local file**, or **stdin**.

## Synopsis

```
fetch [SOURCE] [--head N] [--json PATH] [--timeout SECONDS] [--max-size SIZE]
      [-H 'NAME: VALUE']... [-X METHOD] [-d DATA] [--retry N]
```

## Description

`fetch` is the usual *first* stage of a pipeline. It reads its source and writes the text to its
output stream, so the next command receives it as stdin.

- **SOURCE** starting with `http://` or `https://` is downloaded (a spinner shows while it runs).
- Any other **SOURCE** is read as a file, relative to the current directory (`cd` changes it).
- With no **SOURCE** (or `-`), `fetch` passes stdin through unchanged.

## Options

| Option | Meaning |
| --- | --- |
| `--head N` | Keep only the first *N* lines. |
| `--json PATH` | Parse the text as JSON and extract a dotted path such as `data.items.0.name`. Lists of scalars come out one item per line. |
| `--timeout S` | Network timeout in seconds (default `15`). |
| `--max-size SIZE` | Largest response or file to accept, such as `500K` or `10M` (default `10M`). `0` means no limit. |
| `-H`, `--header 'NAME: VALUE'` | Add a request header (URLs only). Repeat it for several headers. |
| `-X`, `--method METHOD` | HTTP method (URLs only). The default is `GET`, or `POST` when `--data` is given. |
| `-d`, `--data DATA` | Request body (URLs only): text, `@FILE` to send a file, or `@-` to send stdin. A JSON body gets `Content-Type: application/json`, anything else `application/x-www-form-urlencoded`, unless a `-H` header sets it. |
| `--retry N` | Retry up to *N* times on connection errors, timeouts and HTTP 408, 425, 429 and 5xx. It waits 1 s, 2 s, 4 s… (at most 30 s), or what the server's `Retry-After` header asks for. |

## Examples

```
fetch notes.txt
fetch https://example.com --head 20
fetch https://api.github.com/repos/python/cpython --json stargazers_count
fetch data.json --json users | filter -i admin > admins.txt
fetch server.log | filter -c ERROR
fetch https://api.example.com/items -H "Authorization: Bearer <token>" -d '{"name": "x"}'
echo 'a=1&b=2' | fetch https://example.com/form -X PUT -d @-
fetch https://flaky.example.com/data.json --retry 3 --max-size 50M
```

## Errors

Missing files, HTTP errors, timeouts and invalid JSON stop the pipeline with a styled error.
`fetch` only returns text: binary content (images, audio, video, PDFs, archives, or anything
containing NUL bytes) is rejected, and so is anything larger than `--max-size`.

## See also

`man filter`
