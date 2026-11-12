# queryShodan

See **what a public IP address shows to the internet**: its open ports, the software behind them
and the known vulnerabilities of that software. The answer comes from [Shodan](https://www.shodan.io/),
which scans the whole internet all the time, so you learn it without sending a single packet to
the address yourself. It works with no key. A key adds more detail.

## Synopsis

```
queryShodan [IP...] [-o table|json|raw] [--internetdb] [--fresh] [--timeout SECONDS]
```

## Description

- Give one or more **IP addresses** (v4 or v6) as arguments, or pipe them in, one per line. Blank
  lines and `#` comments are skipped.
- Defanged input is read: `8.8.8[.]8` works as it is.
- A private address (your LAN, loopback, link-local) is answered at once with "a private address:
  Shodan only sees the public internet". No request is sent. Use `hostscan` for those.
- `vulns` are CVE ids that Shodan matched from the version numbers it saw. Treat them as leads to
  check, not as proof: a patched package often keeps its old version number.

There are two back ends. The `via` line of every record says which one answered.

| | No key: InternetDB | With a key: Shodan API |
| --- | --- | --- |
| Open ports, host names, tags | yes | yes |
| Known vulnerabilities, CPEs | yes | yes |
| Services with product and version (`22/tcp OpenSSH 8.9`) | no | yes |
| Organisation, provider, ASN | no | yes |
| Country, city, operating system, day of the last scan | no | yes |
| Limits | free for non-commercial use, refreshed about once a week | about one request a second |

### Setup

You can skip this: with no key `queryShodan` asks the free
[InternetDB](https://internetdb.shodan.io/).

To get the services with their versions, the organisation and the location, add a key. Create a
free account at [account.shodan.io](https://account.shodan.io), copy the API key from that page
(the free account's key works for host lookups), then store it:

```
settings SHODAN_API_KEY          # asks for the key (hidden)
```

`queryShodan` reads the `SHODAN_API_KEY` environment variable, so exporting it in your own shell
works too. With a key set, `--internetdb` still asks the keyless InternetDB.

### The verdict

The second line of every record is the **verdict**. Shodan describes what a host shows, it does
not judge it, so the verdict comes only from the tags Shodan puts on the host:

- `malicious`: a tag is `c2`, `malware` or `compromised`.
- `suspicious`: no such tag, but one is `tor`, `scanner` or `proxy`.
- `unknown`: everything else. A `vpn` or `cloud` tag changes nothing. `queryShodan` never says
  `clean`: a host with no open ports can still be hostile.

After the verdict come the facts: how many ports are open (the first 8 are listed), the tags, and
how many known vulnerabilities there are. An address with no record gives `unknown: Shodan has
not seen it`. That is an answer, not an error.

Answers are kept for 24 hours in `~/.shellcraft/cache/intel/`, so asking twice costs nothing. A
record that came from there says so in its `note` line. `--fresh` asks again. The two back ends
keep their answers apart, so the first lookup after you set a key asks the Shodan API.

## Options

| Option | Meaning |
| --- | --- |
| `-o`, `--output table\|json\|raw` | `table` (default) aligned `field  value` lines; `json` the record with `verdict`, `summary` and `data`; `raw` Shodan's whole answer. |
| `--internetdb` | Ask the keyless InternetDB even when a key is set. |
| `--fresh` | Ask again, even when an answer from the last 24 hours is stored. |
| `--timeout S` | Network timeout in seconds (default `10`). |

In the table, `vulns` and `cpes` show their first 10 entries. `-o json` has them all.

## Examples

```
queryShodan 8.8.8.8
queryShodan 45.33.32.156 --internetdb
queryShodan 45.33.32.156 -o json | jq -f .data.services
queryShodan 45.33.32.156 -o json | jq -f .data.vulns
cat addresses.txt | queryShodan | grep "known vulnerabilit"
grep "Failed password" auth.log | ioc --type ip --public | queryShodan
```

With no key:

```
indicator  8.8.8.8
verdict    unknown: 2 open ports (53, 443)
ports      53, 443
hostnames  dns.google
via        InternetDB (free, no key)
```

With a key:

```
indicator    45.33.32.156
verdict      unknown: 3 open ports (22, 80, 123), tags: cloud, 2 known vulnerabilities
ports        22, 80, 123
hostnames    scanme.nmap.org
org          Linode
isp          Akamai Connected Cloud
asn          AS63949
country      United States
city         Fremont
tags         cloud
vulns        CVE-2014-0226, CVE-2023-38408
cpes         cpe:/a:openbsd:openssh:6.6.1p1, cpe:2.3:a:apache:http_server:2.4.7
last_update  2026-11-10
services     22/tcp OpenSSH 6.6.1p1, 80/tcp Apache httpd 2.4.7, 123/udp
via          Shodan API (with your key)
```

## Errors

| Message | What to do |
| --- | --- |
| `give an IP address` | Name an address, or pipe addresses in. |
| `'x' is not an IP address` | Shodan is asked by address. For a domain, get its addresses first: `queryDns`. |
| `Shodan rejected the API key (401)` | The key is wrong or was reset. Store a working one: `settings SHODAN_API_KEY`. Until then `--internetdb` works. |
| `Shodan rate limit reached (one lookup a second); try again in a moment` | You asked too fast. With a list, `queryShodan` waits that second by itself. |
| `cannot reach Shodan` / `timed out` | No network, or Shodan is slow: raise `--timeout`. |

## See also

`man enrich`, `man ioc`, `man queryRdap`, `man ip2geo`, `man hostscan`, `man settings`
