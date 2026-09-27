# filter

Keep only the lines of the input stream that match a **regular expression**, like `grep`.

## Synopsis

```
... | filter [-i] [-v] [-c] [-n] [-F] [-m N] PATTERN
```

## Description

`filter` reads text from stdin, which is the output of the previous pipeline stage. It writes out every
line that matches **PATTERN**. PATTERN uses Python `re` syntax. Quote it if it contains
spaces or `|` characters: `filter "foo|bar"`.

If nothing matches, the output is empty. That is not treated as an error.

## Options

| Option | Meaning |
| --- | --- |
| `-i`, `--ignore-case` | Case-insensitive matching. |
| `-v`, `--invert` | Output lines that do **not** match. |
| `-c`, `--count` | Output only the number of matching lines. |
| `-n`, `--line-number` | Prefix each line with its input line number (`12:text`). |
| `-F`, `--fixed` | Treat PATTERN as a literal string, not a regex. |
| `-m N`, `--max N` | Stop after *N* matching lines. |

## Examples

```
fetch app.log | filter -i "error|warn"
fetch app.log | filter -v DEBUG | filter -c timeout
fetch data.csv | filter -n "^2024-" > rows-2024.txt
fetch README.md | filter -F "a.b" >> hits.txt
```

## See also

`man fetch`
