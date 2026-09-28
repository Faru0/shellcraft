# myip

Show your **public IP address**, or look up the **country, city, ASN and network owner** of any
IPv4/IPv6 address. Data comes from [ipconfig.io](https://docs.ipconfig.io/reference), which uses
MaxMind GeoLite2, refreshed nightly.

## Synopsis

```
myip [IP...] [-i | -j | -f FIELD | -p PORT] [--timeout SECONDS]
```

## Description

- With **no IP**, `myip` prints your own public IP address. That is the address the internet sees.
- With one or more **IPs**, it prints a detail record for each one.
- If no IP is given as an argument, `myip` reads IPs from stdin, one per line. Blank lines and `#` comments are skipped. This lets it work at the end of a pipeline.

Country and ASN are reliable. City and coordinates are best-effort, and they are often missing for
anycast addresses such as `1.1.1.1`.

## Options

| Option | Meaning |
| --- | --- |
| `-i`, `--info` | Full detail record as aligned `key  value` lines (the default when IPs are given). |
| `-j`, `--json` | Raw JSON record. With several IPs, you get a JSON array. |
| `-f`, `--field F` | Print one field only. Several IPs print as `ip<TAB>value`. |
| `-p`, `--port N` | Check whether TCP port *N* on **your** public IP is reachable from the internet. |
| `--timeout S` | Network timeout in seconds (default `10`). |

## Fields

`ip`, `hostname`, `country`, `country_iso`, `country_eu`, `region_name`, `region_code`, `city`,
`zip_code`, `latitude`, `longitude`, `coordinates` (`lat,lon`), `metro_code`, `time_zone`,
`asn`, `asn_org`, `ip_decimal`. Dashes work too, for example `-f country-iso`.

## Examples

```
myip                                  # 203.0.113.42
myip -i                               # everything about your connection
myip -f country                       # just your country
myip 1.1.1.1                          # details for Cloudflare DNS
myip 8.8.8.8 1.1.1.1 -f asn-org       # owner of each address
myip 2606:4700:4700::1111 --json      # IPv6, raw JSON
myip -p 22                            # is my SSH port open to the world?
fetch ips.txt | myip -f country-iso > countries.tsv
myip -i | filter -i "country|asn"
```

## Errors

An invalid address stops the pipeline with the API's message (`could not parse IP: …`). Network
failures and timeouts are reported the same way.

## See also

`man ip2geo` (ISP, proxy/hosting flags, domain names), `man fetch`, `man filter`
