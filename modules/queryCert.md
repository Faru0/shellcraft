# queryCert

List the **TLS certificates** issued for a domain, and the **host names** they cover, from public
Certificate Transparency logs via [crt.sh](https://crt.sh). It needs no API key.

## Synopsis

```
queryCert [DOMAIN...] [-s] [--expired] [-o table|names|json] [--timeout SECONDS]
```

## Description

- Give one or more **domains** as arguments, or pipe them in, one per line. A URL is reduced to its host name.
- Without `-s`, `queryCert` finds certificates whose names match the domain exactly. That includes wildcard certificates such as `*.example.com` that list the domain too.
- With `-s`, it searches `%.DOMAIN`: every certificate for any **subdomain**. Combined with `-o names`, this is a quick subdomain finder.
- By default, only certificates that are **still valid** are shown. `--expired` includes expired ones, which are marked `(expired)` in the table.
- The newest certificates come first. Duplicates across several domains are shown once.

crt.sh is a free, shared service that is often **slow or overloaded**. Big domains can take a
minute or more, so the default timeout is 90 seconds. When crt.sh answers with an error, or with
an error page instead of JSON, `queryCert` says it is overloaded; wait a minute and try again.

## Options

| Option | Meaning |
| --- | --- |
| `-s`, `--subdomains` | Search every subdomain (`%.DOMAIN`) instead of the exact name. |
| `--expired` | Include expired certificates. |
| `-o`, `--output table\|names\|json` | `table` (default); `names` unique host names, one per line; `json` the crt.sh records. |
| `--timeout S` | Network timeout in seconds (default `90`). |

## Output

- `table` has the columns `CRT.SH ID  NOT BEFORE  NOT AFTER  ISSUER  NAMES`. The ID links to `https://crt.sh/?id=ID`.
- `names` lists host names only, including wildcards like `*.example.com`. E-mail addresses and free-text common names are left out.
- `json` is an array of crt.sh records with the fields `id`, `issuer_ca_id`, `issuer_name`, `common_name`, `name_value` (names separated by newlines), `not_before`, `not_after`, `serial_number` and `result_count`.

## Examples

```
queryCert example.com
queryCert -s example.com -o names                 # subdomain discovery
queryCert -s example.com -o names | grep -v "^\*" | queryDns -o ips
queryCert example.com --expired -o json > certs.json
cat domains.txt | queryCert -o names | sort | uniq
```

## Errors

- **crt.sh overloaded:** HTTP 429/5xx, a timeout, or an HTML error page. Retry later, or raise `--timeout`.
- Names that aren't domains, and network failures, stop the pipeline with a one-line message.

## See also

`man queryDns`, `man queryCensys`
