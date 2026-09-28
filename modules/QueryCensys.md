# QueryCensys

Look up a **host** (open ports, services, software, ASN, location) or a **certificate** in the
[Censys Platform](https://docs.censys.com/reference/get-started), or run a Censys **search**.
It uses the official `censys-platform` Python SDK.

## Synopsis

```
QueryCensys host IP...             [-o table|json] [--timeout SECONDS]
QueryCensys cert SHA256...         [-o table|json] [--timeout SECONDS]
QueryCensys search QUERY [-n N] [--page-token TOKEN] [-o table|json] [--timeout SECONDS]
```

## Description

- `host IP...`: one record per IP, with the ASN, BGP prefix, location, OS, DNS names and labels, followed by a `PORT PROTOCOL SOFTWARE` table of its services.
- `cert SHA256...`: subject, issuer, validity, names, serial and validation level for each certificate. Fingerprints may contain `:` separators.
- `search QUERY`: runs a query in the [Censys Query Language](https://docs.censys.com/docs/censys-query-language), such as `host.services.port: 22 and host.location.country: "Iceland"`. Quote the query. It prints a `TYPE ASSET DETAILS` row per hit (host, web property or certificate), then a `# N of TOTAL hit(s)` line with the token for the next page.
- IPs and fingerprints can also come from stdin, one per line. For `search`, stdin is the query.

### Setup

1. Install the SDK: `pip install censys-platform`, or `pip install -e ".[censys]"` in the ShellCraft folder.
2. Create a Personal Access Token: in the Censys Platform, click your user icon, then **API Access**, then **Create New Token**. Then run:
   ```
   settings CENSYS_API_TOKEN     # asks for the token (hidden)
   ```
3. **Paid plans only:** copy your organization ID from the *Current Organization* box on the Personal Access Tokens page, then run:
   ```
   settings CENSYS_ORG_ID
   ```

Both values are read from the `CENSYS_API_TOKEN` and `CENSYS_ORG_ID` environment variables, so
exporting them in your own shell works too.

### Plans

**Free** accounts can only look up hosts and certificates, and without `CENSYS_ORG_ID` every
request runs as a Free account. **Search needs a paid plan** (Starter or higher) and its
organization ID, and users in an organization also need the *API Access* role. API calls consume
Censys credits. Free and Starter plans allow one request at a time.

## Options

| Option | Meaning |
| --- | --- |
| `-o`, `--output table\|json` | `table` (default) readable summary; `json` the full Censys record(s). |
| `-n`, `--limit N` | Hits per search page, 1–100 (default `25`). |
| `--page-token TOKEN` | Fetch the next page of a search (the token is printed after the results). |
| `--timeout S` | Network timeout in seconds (default `30`). |

## Examples

```
QueryCensys host 8.8.8.8
QueryCensys host 8.8.8.8 -o json | fetch --json resource.services.0.port
queryDns example.com -t a -o ips | QueryCensys host
QueryCensys cert 3daf28...e4 -o json
QueryCensys search 'host.services.port: 3389 and host.location.country_code: "NL"' -n 50
```

## Errors

| Situation | Message |
| --- | --- |
| No token | how to create one and `settings CENSYS_API_TOKEN` |
| SDK not installed | `pip install censys-platform` |
| 401 | the token was rejected |
| 403 | your plan or role doesn't allow this |
| 404 | no such host or certificate |
| 422 on `search` without an org ID | search needs a paid plan and `settings CENSYS_ORG_ID` |
| 429 / 503 | concurrency or rate limit; wait and retry |

## See also

`man queryDns`, `man queryCert`, `man ip2geo`, `man settings`
