# queryRdap

Find out **who an IP address or a domain is registered to**. The answer comes straight from the
registries over [RDAP](https://about.rdap.org/), the successor of whois. It is free and needs no key.

## Synopsis

```
queryRdap [TARGET...] [-o table|json|raw] [--fresh] [--timeout SECONDS]
```

## Description

- For an **IP address** you get the network it belongs to, the owner, the country when the registry
  gives one, and the abuse contact.
- For a **domain** you get the registrar, the day it was registered, when it expires, its status,
  its name servers and the registrar's abuse contact.
- A host name is looked up by its registered domain: `www.example.com` gives the record of `example.com`.
- Targets can also come from stdin, one per line. Blank lines and `#` comments are skipped.
- Defanged input is read: `evil[.]com` and `1[.]2[.]3[.]4` work as they are.

The second line of every record is the **verdict**. RDAP says who and since when, not good or bad,
so the verdict is `unknown` with the main facts after it. There is one exception: a domain
registered **less than 30 days ago** is `suspicious`, because a brand new domain is one of the
better warning signs you can get for free.

"the registries have no record of it" is an answer, not an error. Some country endings have no
RDAP service yet.

Answers are kept for 24 hours in `~/.shellcraft/cache/intel/`, so asking twice costs nothing. A
record that came from there says so in its `note` line. `--fresh` asks again.

## Options

| Option | Meaning |
| --- | --- |
| `-o`, `--output table\|json\|raw` | `table` (default) aligned `field  value` lines; `json` the record with `verdict`, `summary` and `data`; `raw` the registry's whole answer. |
| `--fresh` | Ask again, even when an answer from the last 24 hours is stored. |
| `--timeout S` | Network timeout in seconds (default `10`). |

## Examples

```
queryRdap 8.8.8.8
queryRdap example.com
queryRdap 8.8.8.8 -o json | jq -f .data.abuse_email
queryRdap example.com -o json | jq -f .data.age_days
cat domains.txt | queryRdap | grep "suspicious"
grep "Failed password" auth.log | ioc --type ip --public | queryRdap
```

```
indicator    8.8.8.8
verdict      unknown: Google LLC, 8.8.8.0/24
network      8.8.8.0/24
name         GOGL
handle       NET-8-8-8-0-2
owner        Google LLC
type         DIRECT ALLOCATION
created      2023-12-28
changed      2023-12-28
abuse_email  network-abuse@google.com
abuse_phone  +1-650-253-0000
```

## Errors

| Message | What to do |
| --- | --- |
| `give an IP address or a domain name` | Name a target, or pipe targets in. |
| `'x' is not an IP address or a domain name` | URLs and hashes are not registered. For a URL give its host. |
| `RDAP rate limit reached; try again in Ns` | The registry is asking you to slow down. With a list, `queryRdap` waits by itself when the wait is about a minute or less. |
| `cannot reach RDAP` / `timed out` | No network, or the registry is slow: raise `--timeout`. |

## See also

`man enrich`, `man ioc`, `man ip2geo`, `man queryDns`
