# template

Count the most frequent words in a text stream or in files, and print the top ones with their counts.

## Synopsis

```
template [-n N] [-i] [--min-length N] [FILE...]
```

## Description

`template` reads stdin, or each FILE, splits the text into words (letters, digits, `_` and `'`)
and prints the most frequent ones as `word count`, one per line, highest first. Ties are sorted
alphabetically, so the output is stable.

With no FILE, or with `-`, it reads stdin, so it works at the end of a pipeline. Relative paths
are resolved from the current directory.

## Options

| Option | Meaning |
| --- | --- |
| `-n N`, `--top N` | Show the top *N* words (default `10`). |
| `-i`, `--ignore-case` | Count `The` and `the` as the same word (output is lowercase). |
| `--min-length N` | Ignore words shorter than *N* characters (default `1`). |

## Examples

```
template notes.txt
cat README.md | template -i -n 5
template --min-length 4 chapter1.txt chapter2.txt > top-words.txt
fetch https://example.com | template -n 3
```

## Errors

- A missing or unreadable FILE stops the pipeline with `template: FILE: no such file`.
- `-n` and `--min-length` must be at least 1.

## See also

`man wc`, `man sort`, `man uniq`
