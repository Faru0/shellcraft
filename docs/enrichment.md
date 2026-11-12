# Enrichment: is this address bad?

← [Manual](README.md)

You have an IP address in a log, a domain in a mail or a file hash from an alert, and you want to know one
thing: should I worry? `enrich` asks the services that know, all at once, and answers in one line.

```
enrich 8.8.8.8
```

```
VERDICT unknown  8.8.8.8  no source has a judgement on it
SOURCE: ip2geo     unknown     Ashburn, United States, AS15169 Google LLC, hosting, proxy
SOURCE: rdap       unknown     Google LLC, 8.8.8.0/24
SOURCE: shodan     unknown     2 open ports (53, 443), tags: pqc
SOURCE: greynoise  unknown     not seen scanning the internet, not a known service

SKIPPED: virustotal (no key: settings VIRUSTOTAL_API_KEY)
SKIPPED: abuseipdb (no key: settings ABUSEIPDB_API_KEY)
```

That ran with no setup and no account. The first line is the verdict. Each `SOURCE:` line is what one service
said. The `SKIPPED:` lines name the services that would have answered if you had given them a key.

## The two commands

| Command | What it does |
| --- | --- |
| `ioc` | Pulls the indicators out of any text: IP addresses, domains, URLs, file hashes, email addresses. It works on your machine and asks nobody. |
| `enrich` | Looks each indicator up in every service that fits it and gives one verdict. |

They are made to be piped together:

```
grep "Failed password" /var/log/auth.log | ioc --type ip --public | enrich
```

`ioc` finds every address in the lines and prints each one once. `--public` leaves out the addresses of your
own network. `enrich` then looks up what is left.

## The services

| Source | What it adds | Takes | Key |
| --- | --- | --- | --- |
| `ip2geo` | Country, city, network, hosting or proxy | IP, domain | none |
| `rdap` | Who it is registered to, and since when | IP, domain | none |
| `shodan` | Open ports, services, known vulnerabilities | IP | optional |
| `greynoise` | Known internet scanner, or known good service | IPv4 | optional |
| `virustotal` | How many antivirus engines flag it | IP, domain, URL, hash | needed |
| `abuseipdb` | Abuse reports from other administrators | IP | needed |

Every source is also a command of its own, for when you want all it knows and not one line:
`ip2geo`, `queryRdap`, `queryShodan`, `queryGreyNoise`, `queryVirusTotal`, `queryAbuseIPDB`. Each has a page
(`man queryShodan`).

## Add your keys

The two services that judge best need a key. All four keys are free with an account:

```
settings VIRUSTOTAL_API_KEY      # virustotal.com: your profile, then "API key"
settings ABUSEIPDB_API_KEY       # abuseipdb.com: your account, then "API"
settings SHODAN_API_KEY          # account.shodan.io
settings GREYNOISE_API_KEY       # viz.greynoise.io: Account
```

`settings NAME` asks for the value with hidden typing and keeps it for the next start. Each key you add
brings one more source. [Settings and themes](settings-and-themes.md#api-keys) says where keys are stored.

## Reading the verdict

| Verdict | When |
| --- | --- |
| `malicious` | A source calls it malicious, and no source knows it as a good service. |
| `suspicious` | A source calls it suspicious. Or one says malicious while another knows it as a good service: look yourself. |
| `clean` | A source knows it as a good service, or sources looked and none flags it. |
| `unknown` | No source has a judgement. |

Two things to keep in mind:

- **`unknown` does not mean safe.** It means nobody has reported it yet. An address a criminal rented this
  morning is `unknown` everywhere. Owner and location are facts, not a judgement.
- **A verdict is about the address, not about what it did to you.** A `malicious` address that only knocked
  on your door is the daily weather of the internet. The same address with an accepted login is an incident.

## What is sent, and what is not

- A **private address** (10.x, 192.168.x, loopback and the like) is never sent to anyone, and neither is a URL
  on such an address. `enrich` tells you so in the verdict line.
- A **URL** goes to VirusTotal as a URL. The other services only get its host.
- An **email address** is looked up by its domain. The name before the `@` is not sent.
- Only reports that already exist are read. Nothing is uploaded and nothing is sent in for a scan.

## Limits and the store

Free keys have small limits, so answers are kept for 24 hours in `~/.shellcraft/cache/intel/`. Asking again
costs nothing, and such a line ends in `(stored)`. `--fresh` asks again.

- VirusTotal's free key allows 4 lookups a minute. In a list, `enrich` waits about a minute each time that
  limit is reached and goes on, for five minutes in all. That is about 20 new indicators. After that
  VirusTotal shows `error` for the rest of the run. Ctrl-C stops a wait.
- A single indicator never waits.
- A daily limit is not waited out. The line shows `error`, and the other sources still count.

## More ways to use it

```
enrich evil.example.com d41d8cd98f00b204e9800998ecf8427e      # several at once, any kind
enrich "hxxps://evil[.]example/login"                          # defanged input is read
enrich 8.8.8.8 --only shodan,rdap                              # only these sources
cat alert.txt | ioc --public | enrich | grep ^VERDICT          # one line per indicator
cat alert.txt | ioc --defang                                   # safe to paste into a ticket
cat access.log | ioc --type ip --count | head                  # who shows up most
```

For a script, `-o json` gives the verdict, the reasons and each source's full record:

```
cat alert.txt | ioc --public | enrich -o json | jq -w 'verdict=malicious' -f indicator
```

## With the AI co-pilot

The AI agents use the same two commands. When an agent studies a host or a log it runs `enrich` on every new
remote address, puts the `VERDICT` line into the finding as evidence, and picks the alert level from the
verdict together with what it saw:

| What `enrich` said | What the host or the log shows | Alert |
| --- | --- | --- |
| `clean` or `unknown` | nothing odd | `info`, in the round summary only |
| `unknown` | something odd: a new process, a new key, a login at an odd hour | `warn` |
| `suspicious` | anything | `warn` |
| `malicious` | it was kept out: blocked, failed logins, a scan | `warn` |
| `malicious` | it got in or out: an accepted login, an open outbound connection | `crit` |

A finding the agent has seen before is not alerted again. Its `seen` count goes up in the case file
(`findings`), and you hear about it again only when its level went up. The keys you set with `settings` are
the keys the agents use. [Host analysis with an AI](ai-host-analysis.md) has the whole routine.

## See also

`man enrich`, `man ioc`, and the page of each source: `man ip2geo`, `man queryRdap`, `man queryShodan`,
`man queryGreyNoise`, `man queryVirusTotal`, `man queryAbuseIPDB`.
