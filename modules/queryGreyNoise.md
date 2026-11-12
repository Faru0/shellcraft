# queryGreyNoise

Find out whether an IPv4 address is **background noise or a known service**. The answer comes from
[GreyNoise](https://www.greynoise.io/), which runs sensors all over the internet and records who
scans them. It works without a key. A free key raises the daily limit.

## Synopsis

```
queryGreyNoise [IP...] [-o table|json|raw] [--fresh] [--timeout SECONDS]
```

## Description

GreyNoise answers two questions about an address:

- **noise**: its sensors saw this address scanning or probing the internet at large in the last
  90 days. Traffic from it is probably not aimed at you in particular.
- **riot**: the address belongs to a common, known business service, such as public DNS, a CDN or
  software update servers. It is very unlikely to be an attacker.

For an address it knows, you also get its classification (`malicious`, `suspicious`, `benign` or
`unknown`), the name of the service or scanner, the day it was last seen and a link to its page
at GreyNoise.

- Only IPv4 addresses are taken. The community API has no IPv6.
- Targets can also come from stdin, one per line. Blank lines and `#` comments are skipped.
- Defanged input is read: `8.8.8[.]8` works as it is.
- A private address (`10.x`, `192.168.x`, `127.0.0.1` and the like) is not sent to GreyNoise. You
  get `unknown: a private address: GreyNoise only watches the public internet`.

### Setup

You can use `queryGreyNoise` right away. Without a key, GreyNoise answers a small number of
lookups a day. To raise the limit:

1. Create a free account at <https://viz.greynoise.io>.
2. Open "Account", then "API key", and copy the key.
3. Run `settings GREYNOISE_API_KEY` and paste it.

### The verdict

The second line of every record is the **verdict**. The first rule that fits decides it:

| GreyNoise says | Verdict | Summary |
| --- | --- | --- |
| classification `malicious` | `malicious` | `seen scanning the internet, classified malicious`, then the name and the day last seen when there are any |
| `riot` is true | `clean` | `a known service: NAME`, or `a known business service` without a name. In the json, `known_good` is `true`. |
| classification `benign` | `clean` | `a known benign scanner: NAME` (a research scanner, a search engine) |
| classification `suspicious`, or `noise` is true with any other classification | `suspicious` | `seen scanning the internet, classified suspicious` or `seen scanning the internet, not classified`, then the day last seen |
| neither `noise` nor `riot`, or GreyNoise does not know the address | `unknown` | `not seen scanning the internet, not a known service` |

So a malicious classification wins over a known service, and a known service wins over having
been seen scanning.

`unknown` does not mean safe. It means GreyNoise has nothing on the address: it is not part of
the background noise and it is not a known service. An address that only talks to you is worth a
closer look, not less.

Answers are kept for 24 hours in `~/.shellcraft/cache/intel/`, so asking twice costs nothing. A
record that came from there says so in its `note` line. `--fresh` asks again.

## Options

| Option | Meaning |
| --- | --- |
| `-o`, `--output table\|json\|raw` | `table` (default) aligned `field  value` lines; `json` the record with `verdict`, `summary` and `data`; `raw` GreyNoise's whole answer. |
| `--fresh` | Ask again, even when an answer from the last 24 hours is stored. |
| `--timeout S` | Network timeout in seconds (default `10`). |

## Examples

```
queryGreyNoise 8.8.8.8
queryGreyNoise 45.83.64.1 -o json | jq -f .verdict
queryGreyNoise 45.83.64.1 -o json | jq -f .data.last_seen
cat suspects.txt | queryGreyNoise | grep "verdict"
grep "Failed password" auth.log | ioc --type ipv4 --public | queryGreyNoise
```

```
indicator       8.8.8.8
verdict         clean: a known service: Google Public DNS
noise           no
riot            yes
classification  benign
name            Google Public DNS
last_seen       2026-11-10
link            https://viz.greynoise.io/ip/8.8.8.8
message         Success
```

## Errors

| Message | What to do |
| --- | --- |
| `give an IPv4 address` | Name an address, or pipe addresses in. |
| `'x' is not an IPv4 address` | IPv6 addresses, domains, URLs and hashes are not taken. For a domain, look up the address it points at. |
| `GreyNoise rate limit reached; try again later (a key raises the limit: settings GREYNOISE_API_KEY)` | You used up the day's lookups without a key. Set a key (see Setup), or wait until tomorrow. |
| `GreyNoise rate limit reached; try again later` | You used up the lookups of your key. Wait until the limit resets. Stored answers still work. |
| `GreyNoise rejected the API key (401); set a working one: settings GREYNOISE_API_KEY` | The key is wrong or was revoked. Copy it again from your GreyNoise account. |
| `cannot reach GreyNoise` / `timed out` | No network, or GreyNoise is slow: raise `--timeout`. |

## See also

`man enrich`, `man ioc`, `man queryRdap`, `man ip2geo`
