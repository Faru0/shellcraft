# enrich

Look an **indicator** up in every service that fits it, and get **one verdict** with the reasons.
An indicator is an IP address, a domain, a URL, a file hash or an email address.

## Synopsis

```
enrich [INDICATOR...] [--only SOURCES] [--fresh] [-o text|json] [--timeout SECONDS]
```

## Description

`enrich` asks the lookup modules for you, a few at the same time, and sums up what they say:

| Source | Module | What it adds | Takes | Key |
| --- | --- | --- | --- | --- |
| `ip2geo` | `ip2geo` | Country, city, network (ASN), hosting or proxy | IP, domain | none |
| `rdap` | `queryRdap` | Who it is registered to, and since when | IP, domain | none |
| `shodan` | `queryShodan` | Open ports, services, known vulnerabilities | IP | optional: `SHODAN_API_KEY` |
| `virustotal` | `queryVirusTotal` | How many antivirus engines flag it | IP, domain, URL, hash | needed: `VIRUSTOTAL_API_KEY` |
| `abuseipdb` | `queryAbuseIPDB` | Abuse reports from other administrators | IP | needed: `ABUSEIPDB_API_KEY` |
| `greynoise` | `queryGreyNoise` | Known internet scanner, or known good service | IPv4 | optional: `GREYNOISE_API_KEY` |

- Indicators come from the arguments or from stdin, one per line, so `ioc` pipes straight in.
- Defanged input is read: `hxxp://evil[.]com/x`, `1[.]2[.]3[.]4`.
- Each source is asked only for what it takes. A **URL** goes to VirusTotal as a URL, and the other
  sources get its host. An **email address** is looked up by its domain. A **hash** only goes to
  VirusTotal.
- A **private address** (10.x, 192.168.x, loopback and the like) is never sent to anyone, and
  neither is a URL on such an address.
- A source that needs a key you have not set is not an error. It is listed in a `SKIPPED:` line
  with the setting that adds it. With no key at all you still get four sources for an IP address.
- A source that fails (no network, rate limit) shows `error` on its line. The others still count.
- Answers are kept for 24 hours in `~/.shellcraft/cache/intel/`, so asking again costs nothing.
  Such a line ends in `(stored)`. `--fresh` asks again.

### How the verdict is decided

| Verdict | When |
| --- | --- |
| `malicious` | A source calls it malicious, and no source knows it as a good service. |
| `suspicious` | A source calls it suspicious. Or one says malicious while another knows it as a good service: look yourself. |
| `clean` | A source knows it as a good service (a public DNS resolver, a CDN, a whitelisted address). Or sources looked and none flags it. |
| `unknown` | No source has a judgement. Owner and location are facts, not a judgement. |

`unknown` does not mean safe. It means nobody has reported it yet. What each source calls
malicious or suspicious is written in its own page: `man queryVirusTotal`, `man queryAbuseIPDB`,
`man queryGreyNoise`, `man queryShodan`, `man queryRdap`.

### Setup

Nothing is needed to start. Each key you add brings one more source:

```
settings VIRUSTOTAL_API_KEY
settings ABUSEIPDB_API_KEY
settings SHODAN_API_KEY
settings GREYNOISE_API_KEY
```

All four services give a free key with an account. `man` of each module says where to find it.

### Limits

- The free VirusTotal key allows 4 lookups a minute. In a list, `enrich` waits about a minute
  each time that limit is reached and goes on, for five minutes in all (about 20 new
  indicators). After that, VirusTotal shows `error` for the rest of the run. Ctrl-C stops a wait.
- A single indicator never waits.
- A daily limit (AbuseIPDB, GreyNoise without a key) is not waited out. The line shows `error`.
- Stored answers do not count against any limit.

## Options

| Option | Meaning |
| --- | --- |
| `--only SOURCES` | Ask only these sources, comma-separated or repeated: `ip2geo`, `rdap`, `shodan`, `virustotal`, `abuseipdb`, `greynoise`. |
| `--fresh` | Ask every service again, even when an answer from the last 24 hours is stored. |
| `-o`, `--output text\|json` | `text` (default) a `VERDICT` line and one `SOURCE:` line per service; `json` the verdict, the reasons, each source's record with its `data`, and the skipped sources. |
| `--timeout S` | Network timeout for each service, in seconds (default `10`). |

## Examples

```
enrich 45.155.205.233
enrich evil.example.com d41d8cd98f00b204e9800998ecf8427e
enrich "hxxps://evil[.]example/login"
grep "Failed password" /var/log/auth.log | ioc --type ip --public | enrich | grep ^VERDICT
cat alert.txt | ioc --public | enrich -o json | jq -w 'verdict=malicious' -f indicator
enrich 8.8.8.8 --only shodan,rdap --fresh
```

```
> enrich 45.155.205.233
VERDICT malicious  45.155.205.233  abuseipdb: abuse score 100, 1,234 reports from 300 users, last 2026-11-12
SOURCE: ip2geo     unknown     Amsterdam, Netherlands, AS12345 Example Hosting, hosting
SOURCE: rdap       unknown     Example Hosting LLC, 45.155.205.0/24
SOURCE: shodan     unknown     2 open ports (22, 80)
SOURCE: abuseipdb  malicious   abuse score 100, 1,234 reports from 300 users, last 2026-11-12
SOURCE: greynoise  suspicious  seen scanning the internet, not classified, last seen 2026-11-11

SKIPPED: virustotal (no key: settings VIRUSTOTAL_API_KEY)
```

The lines are made for `grep` and `awk`: `VERDICT`, the verdict, the indicator, then the reasons.
For anything more, use `-o json`.

## Errors

| Message | What to do |
| --- | --- |
| `give an indicator (...)` | Name one, or pipe a list in. |
| `'x' is not an indicator (...)` | It is not an IP address, a domain, a URL, a hash or an email address. For free text, run `ioc` first. |
| `unknown source 'x'` | Use the names in the table above. |

## See also

`man ioc`, `man ip2geo`, `man queryRdap`, `man queryShodan`, `man queryVirusTotal`, `man queryAbuseIPDB`, `man queryGreyNoise`
