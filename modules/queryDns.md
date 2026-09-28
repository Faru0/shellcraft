# queryDns

Look up a domain's **DNS records** (A, MX, NS, TXT, CNAME) and, for every IP, its **network
owner (ASN), country and netblock**, using the [DnsDumpster API](https://dnsdumpster.com/developer/).

## Synopsis

```
queryDns [DOMAIN...] [-t TYPE]... [-o table|hosts|ips|json] [--page N] [--timeout SECONDS]
```

## Description

- Give one or more **domains** as arguments, or pipe them in, one per line. Blank lines and `#` comments are skipped, and a URL such as `https://www.example.com/x` is reduced to its host name.
- DnsDumpster includes subdomains it has seen, so the A records usually list far more than the domain itself.
- The API allows **one request every 2 seconds**, so with several domains `queryDns` waits 2 seconds between them.
- Free accounts get up to 50 records per domain, and Plus accounts up to 200. Plus accounts can fetch more with `--page 2`, `--page 3`, ….

### API key

You need a free DnsDumpster API key. Sign in at [dnsdumpster.com](https://dnsdumpster.com) and
copy the key from your dashboard, then store it:

```
settings DNSDUMPSTER_API_KEY          # asks for the key (hidden)
```

`queryDns` reads the `DNSDUMPSTER_API_KEY` environment variable, so exporting it in your own
shell works too.

## Options

| Option | Meaning |
| --- | --- |
| `-t`, `--type TYPE` | Only these record types: `a`, `mx`, `ns`, `txt`, `cname`. Repeat it or use commas (`-t a,mx`). Default: all. |
| `-o`, `--output table\|hosts\|ips\|json` | `table` (default) aligned columns; `hosts` unique host names; `ips` unique IP addresses; `json` the API records. |
| `--page N` | Result page for domains with more than 200 records (Plus accounts only). |
| `--timeout S` | Network timeout in seconds (default `30`). |

## Output

The table has the columns `TYPE HOST IP ASN CC NETBLOCK`, one row per IP address. TXT records
follow the table as `TXT  value` lines. `hosts` and `ips` print one value per line, so they pipe
well into `myip`, `sort` or `grep`.

## Examples

```
queryDns example.com
queryDns example.com -t mx,ns
queryDns example.com -o hosts | grep -c .         # how many host names were found
queryDns example.com -t a -o ips | myip -f asn-org  # who hosts each IP
cat domains.txt | queryDns -o json > dns.json
```

## Errors

- **No API key:** the error says how to set one (`settings DNSDUMPSTER_API_KEY`).
- **Invalid API key:** a 401/403 from DnsDumpster is reported as a rejected key.
- **Rate limited:** a 429 means another request was made less than 2 seconds ago; wait and retry.
- Network failures, timeouts and names that aren't domains stop the pipeline with a one-line message.

## See also

`man queryCert`, `man queryCensys`, `man myip`, `man settings`
