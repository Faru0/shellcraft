# ioc

Pull the **indicators** out of any text: IP addresses, domain names, URLs, file hashes and email
addresses. Give it a log, a mail, an alert or a report and you get a clean list, one per line.
It works on your machine and asks nobody.

## Synopsis

```
ioc [TEXT...] [-t TYPES] [--public] [--defang | --refang] [-c] [-o list|json]
```

## Description

The text comes from stdin (`cat auth.log | ioc`) or from the arguments (`ioc "seen 1.2.3.4"`).

- Each indicator is printed **once**, in the order it was first seen.
- **Defanged** text is read: `hxxp://evil[.]com`, `1[.]2[.]3[.]4`, `bob[@]evil[.]com`,
  `evil(dot)com`. The output is the real value, ready for a lookup.
- A **URL** also gives its host, and an **email address** also gives its domain, so you can look
  up both.
- IP addresses are written the standard way and names, hashes and email addresses in lower case,
  so the same indicator written two ways counts as one.
- When nothing is found the output is empty.

The types, for `--type`:

| Type | What it matches |
| --- | --- |
| `ip` | IPv4 and IPv6 addresses (`ipv4` or `ipv6` for only one of them) |
| `domain` | Host and domain names with a known ending (`.com`, `.de`, `.top`, ...) |
| `url` | `http://`, `https://` and `ftp://` links |
| `hash` | MD5, SHA-1 and SHA-256 in hex (`md5`, `sha1`, `sha256` for only one) |
| `email` | Email addresses |

### What it cannot know

`ioc` reads patterns, not meaning. Keep these in mind:

- A version number with four parts (`1.2.3.4`) reads as an IP address.
- 40 hex characters may be a git commit and not a SHA-1. 32 may be any ID.
- A name counts as a domain only when it ends in a known ending. File names such as `main.py`,
  `run.sh` and `notes.md` are left out, unless they stand inside a URL or an email address.
- `--public` judges addresses only. A name is always kept, because only a lookup can tell where
  it leads.

## Options

| Option | Meaning |
| --- | --- |
| `-t`, `--type TYPES` | Only these types, comma-separated or repeated: `ip`, `domain`, `url`, `hash`, `email`. |
| `--public` | Leave out addresses that are not on the internet (private, loopback, link-local, reserved, documentation ranges) and URLs that point at one. |
| `--defang` | Print the indicators defanged: `hxxp://evil[.]com/x`, `1[.]2[.]3[.]4`. Safe to paste into a ticket or a chat, where nobody can click them. |
| `--refang` | Print the real values. This is the default; the option exists so a script can say it. |
| `-c`, `--count` | Start each line with how often the indicator was seen, most seen first. |
| `-o`, `--output list\|json` | `list` (default) one per line; `json` a list of `{"type", "value", "count"}`. |

## Examples

```
grep "Failed password" /var/log/auth.log | ioc --type ip --public --count | head
cat phish.eml | ioc --type url,domain
cat alert.txt | ioc --public | enrich
ioc --defang "http://evil.example/x was served from 203.0.113.9"
cat report.txt | ioc -o json | jq -w 'type=sha256' -f value
```

```
> grep "Failed password" auth.log | ioc --type ip --public --count | head -3
412  45.155.205.233
 96  193.32.162.10
  3  8.8.8.8
```

## Errors

| Message | What to do |
| --- | --- |
| `give some text (as arguments, or on stdin)` | Pipe text in, or pass it as an argument. |
| `unknown type 'x'` | Use `ip`, `domain`, `url`, `hash` or `email`. |
| `argument --refang: not allowed with argument --defang` | Choose one of the two. |

## See also

`man enrich`, `man queryRdap`, `man ip2geo`
