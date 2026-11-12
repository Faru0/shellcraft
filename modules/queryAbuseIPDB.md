# queryAbuseIPDB

Find out **how often an IP address was reported for abuse**: brute force logins, port scans, spam
and the like. The answer comes from [AbuseIPDB](https://www.abuseipdb.com), where server owners
report the addresses that attack them. You need a free API key.

## Synopsis

```
queryAbuseIPDB [IP...] [--days N] [-o table|json|raw] [--fresh] [--timeout SECONDS]
```

## Description

- For every **IP address** (v4 or v6) you get the abuse score from 0 to 100, the number of reports
  and of the users who sent them, the day of the last report, the country, the ISP, the usage type
  (hosting, mobile, fixed line), the domain, the host names, and if it is a Tor exit.
- Reports count for the last 90 days. `--days N` changes that, from 1 to 365.
- Targets can also come from stdin, one per line. Blank lines and `#` comments are skipped.
- Defanged input is read: `1[.]2[.]3[.]4` works as it is.
- A private address (`10.0.0.5`, `192.168.1.1`, `fe80::1`) is not sent to AbuseIPDB. It only tracks
  public addresses, so you get `unknown` and no check is used.

### Setup

1. Create a free account at <https://www.abuseipdb.com>.
2. Open "API" in your account and create a key.
3. Store it:

```
settings ABUSEIPDB_API_KEY          # asks for the key (hidden)
```

`queryAbuseIPDB` reads the `ABUSEIPDB_API_KEY` environment variable, so exporting it in your own
shell works too. The free key allows **1,000 checks a day**. When they are used up, AbuseIPDB
answers with a rate limit error until the next day.

### The verdict

The second line of every record is the **verdict**. The first rule that fits decides it:

| AbuseIPDB says | Verdict | Summary |
| --- | --- | --- |
| the address is not public | `unknown` | `a private address: AbuseIPDB only tracks public ones` |
| the address is on its whitelist | `clean` | `on AbuseIPDB's whitelist`, then the score and the reports when there are any. In the json, `known_good` is `true`. |
| abuse score **75 or more** | `malicious` | `abuse score 100, 1,234 reports from 300 users, last 2026-11-12`, then `Tor exit` and the usage type when they apply |
| abuse score **25 to 74** | `suspicious` | the same line |
| abuse score **below 25** | `clean` | `no reports in the last 90 days`, or `abuse score 3, 2 reports from 2 users, last 2026-10-30` |

So the whitelist wins over the score: a public DNS server that people report by mistake stays
`clean`. A Tor exit is not a verdict by itself. It is named in the summary of a flagged address
and it is always in the `tor` field.

`clean` does not mean safe. It means nobody reported the address in the days you asked about. An
address that only attacks you is not in anybody else's reports.

Answers are kept for 24 hours in `~/.shellcraft/cache/intel/`, so asking twice costs no check. A
record that came from there says so in its `note` line. `--fresh` asks again. Every `--days`
window is kept apart, so a 30 day answer is never shown for a 90 day question.

## Options

| Option | Meaning |
| --- | --- |
| `--days N` | How far back reports count, in days: `1` to `365` (default `90`). |
| `-o`, `--output table\|json\|raw` | `table` (default) aligned `field  value` lines; `json` the record with `verdict`, `summary` and `data`; `raw` AbuseIPDB's whole answer. |
| `--fresh` | Ask again, even when an answer from the last 24 hours is stored. It costs one check. |
| `--timeout S` | Network timeout in seconds (default `10`). |

## Examples

```
queryAbuseIPDB 118.25.6.39
queryAbuseIPDB 118.25.6.39 --days 30
queryAbuseIPDB 2606:4700:4700::1111
queryAbuseIPDB 118.25.6.39 -o json | jq -f .data.score
cat addresses.txt | queryAbuseIPDB | grep "malicious"
grep "Failed password" auth.log | ioc --type ip --public | queryAbuseIPDB
```

```
indicator      118.25.6.39
verdict        malicious: abuse score 100, 1,234 reports from 300 users, last 2026-11-12, Data Center/Web Hosting/Transit
score          100
reports        1234
users          300
last_reported  2026-11-12
country        CN
usage          Data Center/Web Hosting/Transit
isp            Tencent Cloud Computing (Beijing) Co. Ltd
domain         tencent.com
tor            no
whitelisted    no
days           90
```

## Errors

| Message | What to do |
| --- | --- |
| `no AbuseIPDB API key` | Get a free key and store it: `settings ABUSEIPDB_API_KEY`. See Setup above. |
| `AbuseIPDB rejected the API key (401)` | The key is wrong or was deleted. Set a working one: `settings ABUSEIPDB_API_KEY`. |
| `give an IP address` | Name a target, or pipe targets in. |
| `'x' is not an IP address` | AbuseIPDB knows addresses only. For a domain or a URL use `enrich` or `queryVirusTotal`. |
| `--days takes 1 to 365` | Give a number of days in that range. |
| `AbuseIPDB rate limit reached; try again in Ns` | The 1,000 checks of the day are used up. Stored answers still work. With a list, `queryAbuseIPDB` waits by itself when the wait is about a minute or less. |
| `AbuseIPDB HTTP 422 ...` | AbuseIPDB did not accept the request. Check the address and `--days`. |
| `AbuseIPDB returned an unexpected response` | The answer was not a record. Try again later. |
| `cannot reach AbuseIPDB` / `timed out` | No network, or the service is slow: raise `--timeout`. |

With a list, one target that fails does not stop the others. It gets a record with an `error`
line in place of a verdict.

## See also

`man enrich`, `man ioc`, `man queryRdap`, `man queryGreyNoise`, `man queryVirusTotal`, `man ip2geo`, `man settings`
