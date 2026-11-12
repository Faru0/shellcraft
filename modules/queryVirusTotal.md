# queryVirusTotal

Find out **what the antivirus engines say** about an IP address, a domain, a URL or a file hash.
The answer comes from [VirusTotal](https://www.virustotal.com), which checks each of them with
some 70 to 90 engines and blocklists. You need a free API key.

## Synopsis

```
queryVirusTotal [INDICATOR...] [-o table|json|raw] [--fresh] [--timeout SECONDS]
```

## Description

- For every indicator you get the number of engines that call it malicious, suspicious or
  harmless, its reputation among VirusTotal's users, its tags, the day it was last checked and a
  link to its page at VirusTotal.
- For an **IP address** you also get the owner of its network, the ASN, the country and the network.
- For a **domain** you also get the registrar, the day it was created and its categories.
- For a **file hash** (MD5, SHA-1 or SHA-256) you also get the threat label, the file's name, type
  and size, the day it was first sent in, up to five names it was seen under and its other hashes.
- For a **URL** you also get the address it ends up at, the title of the page and its categories.
- Indicators can also come from stdin, one per line. Blank lines and `#` comments are skipped.
- Defanged input is read: `hxxps://evil[.]com/a` and `1[.]2[.]3[.]4` work as they are.
- A private address (your LAN, loopback, link-local) is answered at once with "a private address:
  VirusTotal only knows the public internet". It is not sent to VirusTotal.

`queryVirusTotal` only reads reports that VirusTotal already has. It never uploads a file and it
never sends a URL in for a scan. So a lookup does not tell anyone about a file or a link that
VirusTotal has not seen before. The other side of that: for something new you get
`unknown: VirusTotal has never seen it`. That is an answer, not an error, and it does not mean
the thing is safe.

### Setup

1. Create a free account at <https://www.virustotal.com>.
2. Open your profile, then "API key", and copy the key.
3. Store it:

```
settings VIRUSTOTAL_API_KEY           # asks for the key (hidden)
```

`queryVirusTotal` reads the `VIRUSTOTAL_API_KEY` environment variable, so exporting it in your
own shell works too.

### The verdict

The second line of every record is the **verdict**. The first rule that fits decides it:

| VirusTotal says | Verdict |
| --- | --- |
| 5 or more engines call it malicious | `malicious` |
| 1 to 4 engines call it malicious, or 2 or more call it suspicious | `suspicious` |
| engines checked it, and fewer than that flag it | `clean` |
| no engine has checked it yet, or VirusTotal has never seen it | `unknown` |

The numbers stand after the verdict: `12 of 94 engines call it malicious, 2 suspicious, last
checked 2026-11-01`. For a file the threat label is there too. One engine alone is often a false
alarm, which is why it takes five for `malicious`. `clean` means that no engine knows it as bad
today. It is not a promise.

### Limits

A free key allows **4 lookups a minute and 500 a day**.

- With a list of more than four indicators, `queryVirusTotal` waits about a minute each time
  the limit is reached and then goes on with the next group. Ctrl-C stops the wait.
- It waits for five minutes in all, which is about 20 new indicators. The ones after that come
  back with the rate limit error. Run the same command again a minute later.
- When a wait did not help (the 500 lookups of the day are used up), it stops waiting.
- Answers are kept for 24 hours in `~/.shellcraft/cache/intel/`, so asking again costs nothing.
  A record that came from there says so in its `note` line. `--fresh` asks again, and that costs
  one of your lookups.

## Options

| Option | Meaning |
| --- | --- |
| `-o`, `--output table\|json\|raw` | `table` (default) aligned `field  value` lines; `json` the record with `verdict`, `summary` and `data`; `raw` VirusTotal's whole report. |
| `--fresh` | Ask again, even when an answer from the last 24 hours is stored. |
| `--timeout S` | Network timeout in seconds (default `10`). |

## Examples

```
queryVirusTotal 44d88612fea8a8f36de82e1278abb02f
queryVirusTotal evil.example
queryVirusTotal "hxxps://evil[.]example/login"
queryVirusTotal 8.8.8.8 -o json | jq -f .verdict
queryVirusTotal 44d88612fea8a8f36de82e1278abb02f -o json | jq -f .data.threat_label
cat hashes.txt | queryVirusTotal | grep "malicious"
cat mail.txt | ioc --type url | queryVirusTotal
```

```
indicator      44d88612fea8a8f36de82e1278abb02f
verdict        malicious: 62 of 70 engines call it malicious, virus.eicar/test, last checked 2026-11-01
malicious      62
suspicious     0
harmless       0
undetected     8
engines        70
reputation     3604
tags           attachment
last_analysis  2026-11-01
threat_label   virus.eicar/test
name           eicar.com
type           Text
size           68
first_seen     2006-05-22
names          eicar.com, eicar.txt, eicar.com.txt, test.txt, eicar
sha256         275a021bbfb6489e54d471899f7db9d1663fc695ec2fe2a2c4538aabf651fd0f
md5            44d88612fea8a8f36de82e1278abb02f
sha1           3395856ce81f2b7382dee72602f798b642f14140
link           https://www.virustotal.com/gui/file/44d88612fea8a8f36de82e1278abb02f
```

## Errors

| Message | What to do |
| --- | --- |
| `give an IP address, a domain name, a URL or a file hash (MD5, SHA-1, SHA-256)` | Name an indicator, or pipe indicators in. |
| `'x' is not an IP address, a domain name, a URL or a file hash (MD5, SHA-1, SHA-256)` | Email addresses and other text are not taken. Quote a URL that has `?` or `&` in it. |
| `no VirusTotal API key` | Get a free key and store it: `settings VIRUSTOTAL_API_KEY`. See Setup above. |
| `VirusTotal rejected the API key (401)` | The key is wrong or was replaced. Store a working one: `settings VIRUSTOTAL_API_KEY`. |
| `VirusTotal rate limit reached (a free key allows 4 lookups a minute, 500 a day); try again in a minute` | Wait a minute. If it stays, the 500 lookups of the day are used up. |
| `cannot reach VirusTotal` / `timed out` | No network, or VirusTotal is slow: raise `--timeout`. |

## See also

`man enrich`, `man ioc`, `man queryRdap`, `man ip2geo`, `man settings`
