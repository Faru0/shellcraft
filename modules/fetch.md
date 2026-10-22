# fetch

`curl` under another name, with two extras: it also reads a **local file** or **stdin**, and
`--head N` keeps the first *N* lines. Every curl option works (`man curl`).

## Synopsis

```
fetch [SOURCE...] [--head N] [--jq PATH] [CURL OPTIONS]
```

## Description

`fetch` is the usual *first* stage of a pipeline:

- a **SOURCE** with a scheme (`https://…`) is requested exactly as `curl` would;
- any other **SOURCE** is read as a file, relative to the current directory;
- with no **SOURCE** (or `-`), stdin passes through.

Differences from `curl`: `--head N` means "the first N lines" (curl's HEAD request is `-I`), a
response or file is limited to 10 MB unless `--max-size` says otherwise, and the old
`--json PATH` still works as `--jq PATH`. Its older switches keep working too: `--timeout`
(curl's `--max-time`), `--max-size` (`--max-filesize`) and `--method` (`-X`).

AI clients get the `curl` tool; `fetch` is not offered as a second one.

## Options

| Option | Meaning |
| --- | --- |
| `--head N` | Keep only the first *N* lines. |
| `--jq PATH` | Print one field of a JSON document: `data.items.0.name` or `.data.items[0].name`. Lists of plain values come out one per line. |
| `--max-size SIZE` | Largest response or file, such as `500K` or `10M` (default `10M`); `0` for no limit. |
| any curl option | `-H`, `-X`, `-d`, `--json`, `-L`, `-u`, `-x`, `--retry`… (`man curl`). |

## Examples

```
fetch notes.txt --head 5
fetch https://example.com --head 20
fetch https://api.github.com/repos/python/cpython --jq stargazers_count
fetch data.json --jq users | filter -i admin > admins.txt
fetch https://api.example.com/items -H "Authorization: Bearer $TOKEN" --json '{"name": "x"}'
echo 'a=1&b=2' | fetch https://example.com/form -X PUT -d @-
```

## See also

`man curl` · `man filter`
