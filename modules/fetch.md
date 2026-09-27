# fetch

Pull text into a ShellCraft pipeline from a **URL**, a **local file**, or **stdin**.

## Synopsis

```
fetch [SOURCE] [--head N] [--json PATH] [--timeout SECONDS]
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

## Examples

```
fetch notes.txt
fetch https://example.com --head 20
fetch https://api.github.com/repos/python/cpython --json stargazers_count
fetch data.json --json users | filter -i admin > admins.txt
fetch server.log | filter -c ERROR
```

## Errors

Missing files, HTTP errors, timeouts and invalid JSON stop the pipeline with a styled error.

## See also

`man filter`
