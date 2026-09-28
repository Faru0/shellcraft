# ip2geo

Geolocate **IP addresses or domain names**: continent, country, region, city, coordinates,
timezone, currency, ISP, organization, ASN, and whether the address is **mobile, a proxy/VPN/Tor
exit, or hosting**. Data comes from the free [ip-api.com](https://ip-api.com/docs/api:json) JSON
endpoint, which needs no API key.

## Synopsis

```
ip2geo [QUERY...] [-f FIELDS]... [-o table|json|tsv] [--lang LANG] [--timeout SECONDS]
```

## Description

- A **query** is an IPv4/IPv6 address or a domain name. A domain is resolved by ip-api.com, and the `query` field shows the IP it used.
- With no query as an argument, queries are read from stdin, one per line. If stdin is empty too, `ip2geo` looks up **your own public IP**.
- One failed lookup, such as a private range (`10.x`, `192.168.x`) or an invalid query, stops with the API's message. With several queries, a failed one is shown in the output (`error …` in the table, `fail: …` in TSV) and the rest still work.
- **Rate limit:** ip-api.com allows 45 requests per minute. `ip2geo` watches the `X-Rl` header, and when it reaches 0 it waits for the `X-Ttl` window before sending the next request, so long lists slow down instead of failing. A 429 response ends the lookup with the number of seconds to wait. Going over the limit repeatedly gets your IP banned for an hour.
- ⚠️ The free endpoint is **HTTP only**, so queries and answers travel unencrypted. ip-api.com also doesn't allow commercial use of it.

## Options

| Option | Meaning |
| --- | --- |
| `-f`, `--fields FIELDS` | Comma-separated fields to request and show; repeatable. `all` adds every field, including the slow `reverse`. `query` always comes first. Default: every field except `reverse`. |
| `-o`, `--output table\|json\|tsv` | `table` (default) aligned `field  value` lines; `json` the API records (an array for several queries); `tsv` a header line plus one tab-separated line per query. |
| `--lang en\|de\|es\|pt-BR\|fr\|ja\|zh-CN\|ru` | Language for `country`, `regionName` and `city` (default `en`). |
| `--timeout S` | Network timeout in seconds (default `10`). |

## Fields

`query`, `continent`, `continentCode`, `country`, `countryCode`, `region`, `regionName`, `city`,
`district`, `zip`, `lat`, `lon`, `timezone`, `offset` (UTC offset in seconds), `currency`, `isp`,
`org`, `as` (`AS15169 Google LLC`), `asname`, `reverse` (reverse DNS), `mobile`, `proxy`,
`hosting`. Case, `-` and `_` are ignored, so `country_code` works too. Empty fields are left out
of the table.

## Examples

```
ip2geo                                        # where am I?
ip2geo 24.48.0.1
ip2geo google.com -f country,city,isp
ip2geo 8.8.8.8 1.1.1.1 -f countryCode,as -o tsv
queryDns example.com -t a -o ips | ip2geo -f country,hosting -o tsv
cat ips.txt | ip2geo -f proxy -o tsv | grep yes      # which addresses are proxies/VPNs
ip2geo 24.48.0.1 --lang de -f country,city
```

## Errors

`private range`, `reserved range` and `invalid query` come from the API. A 429 means the rate
limit was hit. Network failures and timeouts are reported in one line.

## See also

`man myip`, `man queryDns`, `man QueryCensys`
