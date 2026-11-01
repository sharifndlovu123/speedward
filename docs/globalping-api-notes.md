# Globalping API notes (observed)

Observed on 2026-11-01 against `https://api.globalping.io/v1` with `scripts/globalping_spike.py` plus a few one-off single-probe calls. Targets: `example.com` (main run), `httpbin.org`, `expired.badssl.com`, a non-existent subdomain of `example.com`. About 25 tests were consumed.

The sections below, up to "Authenticated run", were observed unauthenticated. Authenticated behaviour (free key) is in the "Authenticated run" section at the end, observed the same day with about 77 more tests. Anything not stated as observed is "not observed".

This file is the source of truth for field names. Anything marked "not available" must be shown as "not available" by the app, never guessed.

## Flow

- `POST /v1/measurements` returns `202` with body `{"id": "...", "probesCount": 4}`. Headers include `Location: https://api.globalping.io/v1/measurements/{id}`.
- `GET /v1/measurements/{id}` returns the measurement. Top-level `status` is `in-progress` until done, then `finished`. Observed finishing within about 1 second for 1 to 4 probes (the first poll after a 1 second wait was already `finished`). Per-probe `result.status` is `finished` or `failed`.
- Top-level keys of a measurement: `id, type, status, createdAt, updatedAt, target, probesCount, locations, measurementOptions, results`. The echoed `locations` is the request's list. `measurementOptions` echoes the options but dropped an empty path `/`.
- Each `results[]` item is `{"probe": {...}, "result": {...}}`.

## Probe location fields (`results[].probe`)

Observed keys: `continent` (e.g. `EU`, `AS`), `region` (e.g. `Western Europe`), `country` (ISO-2, e.g. `DE`), `state` (null outside the US, observed null everywhere), `city`, `asn` (integer), `longitude`, `latitude` (numbers, city-level, 2 decimals), `network` (name string, e.g. `IONOS`, `Amazon.com`, `Telefonica`), `tags` (list), `resolvers` (list; `["private"]` or IPs).

Example:

```json
{"continent":"EU","region":"Western Europe","country":"DE","state":null,"city":"Berlin","asn":8560,
 "longitude":13.41,"latitude":52.52,"network":"IONOS","tags":["datacenter-network","u-h0ger77"],"resolvers":["private"]}
```

Network type is not its own field. It is derived from `tags`: `datacenter-network` or `eyeball-network` (both observed). Cloud probes also carry `aws` and `aws-eu-west-1`. User probes carry `u-<name>` tags (identify a specific probe, see reuse below). An ISP name tag like `u-rodrigostival:VIVO` was observed on an eyeball probe.

## Location filters (request `locations`)

Request `locations` is a list of objects. Observed working: `{"magic": "Berlin"}`, `{"magic": "Tokyo"}`, `{"magic": "aws-eu-west-1"}` (resolved to a Dublin AWS probe, ASN 16509), `{"magic": "eyeball-network+Brazil"}` (resolved to a Sao Paulo probe, tag `eyeball-network`). So `datacenter-network` / `eyeball-network` and cloud region names work through `magic`, combinable with `+`. A string measurement id in place of the list was also accepted (see reuse). `{"country":"AQ"}` with no probes returned `422`:

```json
{"error":{"type":"no_probes_found","message":"No matching IPv4 probes available."},"links":{"documentation":"https://globalping.io/docs/api.globalping.io#post-/v1/measurements"}}
```

## HTTP result

Request used: `{"type":"http","target":"example.com","locations":[...],"measurementOptions":{"request":{"method":"GET","path":"/"},"protocol":"HTTPS"}}`. `target` is a bare hostname (I passed the host, not a URL). `request.method` must be one of `GET, HEAD, OPTIONS` (400 otherwise). `protocol` `HTTP`/`HTTPS` accepted. `port` is not allowed at top level (400 `"port" is not allowed`); `measurementOptions.port` is accepted (see timeouts).

Result keys observed: `status, resolvedAddress, headers, rawHeaders, rawBody, rawOutput, truncated, statusCode, statusCodeName, timings, tls`. For failures also `failureSource`.

Trimmed real example (bodies and long strings shortened, nothing else removed):

```json
{"probe": {"continent":"EU","country":"DE","city":"Berlin","asn":8560,"longitude":13.41,"latitude":52.52,"network":"IONOS","tags":["datacenter-network","u-h0ger77"]},
 "result": {
  "status": "finished",
  "resolvedAddress": "172.66.147.243",
  "headers": {"date":"...","content-type":"text/html; charset=utf-8","server":"cloudflare","age":"13619","cf-cache-status":"HIT","content-encoding":"br","cf-ray":"a53cca1a6ccf518e-TXL"},
  "rawHeaders": "Date: ...\nContent-Type: text/html; charset=utf-8\n...",
  "rawBody": "<!doctype html>...",
  "rawOutput": "HTTP/1.1 200\nDate: ...\n...\n\n<!doctype html>...",
  "truncated": false,
  "statusCode": 200,
  "statusCodeName": "OK",
  "timings": {"total": 22, "dns": 3, "tcp": 0, "tls": 11, "firstByte": 5, "download": 3},
  "tls": {"authorized": true, "protocol": "TLSv1.3", "cipherName": "TLS_AES_256_GCM_SHA384",
          "createdAt": "2026-09-26T22:49:11.000Z", "expiresAt": "2026-12-25T22:56:35.000Z",
          "issuer": {"C":"US","O":"SSL Corporation","CN":"Cloudflare TLS Issuing ECC CA 3"},
          "subject": {"CN":"example.com","alt":"DNS:example.com, DNS:*.example.com"},
          "keyType":"EC","keyBits":256,"serialNumber":"...","fingerprint256":"...","publicKey":"..."}}}
```

Answers:

- Timing fields: `timings.total, dns, tcp, tls, firstByte, download`. All six exist, numbers in milliseconds (integers observed). `tls` is `null` for plain HTTP (observed with `protocol: HTTP`). On failure the fields that did not happen are `null`, but `total` is still a number (7 for a DNS failure, 15000 for a TCP timeout) so a failed result must never take `total` as a measurement.
- Status code: yes, `statusCode` (int) and `statusCodeName`.
- Response headers: yes, `headers` (object, lowercase keys, values strings) and `rawHeaders`. Header values may be arrays for repeated headers: not observed.
- Resolved IP: yes, `resolvedAddress`. It can differ between probes and between runs (observed two Cloudflare IPs for example.com).
- HTTP version: not available as a structured field. It appears only in the first line of `rawOutput` (`HTTP/1.1 200` observed on every result). Parsing that line is the only way; treat a missing or unparsable line as "not available". HTTP/2 or HTTP/3 was never observed (all `HTTP/1.1`); the response also carried `alt-svc: h3` headers, which says nothing about what the probe used.
- TLS version: yes, `tls.protocol` (`TLSv1.3`, `TLSv1.2` observed). `tls` is an object for HTTPS and `null` for plain HTTP (observed). Treat `tls.protocol` absent/null as "not available".
- CDN cache status: not a field; read it from `headers` (e.g. `cf-cache-status: HIT`, `age`). Header names are CDN-specific; only Cloudflare was observed.
- Body: `rawBody` is returned in full (small page). `truncated: false`. Behaviour on large bodies: not observed.

## Redirects

- Following redirects is not an option: `measurementOptions.followRedirects` returns `400`: `{"error":{"type":"validation_error","message":"Parameter validation failed.","params":{"measurementOptions.followRedirects":"\"measurementOptions.followRedirects\" is not allowed"}},...}`.
- Redirects are not followed. `httpbin.org/redirect/2` returned `statusCode: 302`, `rawOutput` starting `HTTP/1.1 302`, and the next hop in `headers.location` (`/relative-redirect/1`, a relative URL). There is no chain structure. Walking a chain means one extra measurement per hop, built from `location`, so the cost of a chain is not fixed. `example.com` over plain HTTP returned 200 directly (no redirect observed).
- Other option names (e.g. a `maxRedirects`) were not tried: not observed.

## Probe reuse (repeat on the same probes)

Passing a previous measurement id as `locations` (a string instead of a list) was accepted: `{"type":"http","target":"example.com","locations":"<previous id>","measurementOptions":{...}}`, returning `202` with `probesCount: 4`. The new measurement's `locations` echoes the original list of magics, and the probes had the same user tags (`u-h0ger77` Berlin, `u-zGato` Tokyo, same cloud and Brazil probes) so the same probes were reused in this run. Whether reuse is guaranteed if a probe has gone offline: not observed. Compare `probe.tags`/`asn`/`city` between the two results to confirm reuse at runtime; `tags` on the datacenter probes also carried a `u-...` id, but the probe has no explicit unique id field. The repeat request cost the same as the first (4 for 4 probes).

Warm vs cold: repeat result timings were still non-zero for dns (e.g. 2 ms) and tcp/tls; a connection is not reused between measurements, so a "warm" run only warms server/CDN caches and the probe's DNS cache. Observed Berlin cold 22 ms vs repeat 20 ms; Tokyo 56 vs 76 ms (variation is not evidence of caching).

## Failures

- DNS failure (name does not exist): `result.status: "failed"`, `failureSource: "target"`, `statusCode: null`, `resolvedAddress: null`, `rawOutput: "queryA ENODATA does-not-exist-speedward-spike.example.com"`, `timings.total: 7`, other timings `null`.
- Timeout (closed port 81 via `measurementOptions.port: 81`, `protocol: HTTP`): `status: "failed"`, `failureSource: "target"`, `rawOutput: "Request timed out while establishing the TCP connection."`, `timings: {"total":15000,"dns":0,"tcp":null,...}`, `statusCode: null`. So the probe times out at 15 s.
- Certificate error (`expired.badssl.com`): the measurement did NOT fail. `result.status: "finished"`, `statusCode: 200`, timings present, and `tls: {"authorized": false, "error": "CERT_HAS_EXPIRED", "protocol": "TLSv1.2", ...}`. A certificate problem is therefore detected from `tls.authorized == false` / `tls.error`, and a status 200 with `authorized: false` must not be reported as a clean measured result. Other certificate error codes: not observed.
- `failureSource` is the only observed flag separating a target-side failure from a Globalping-side one; its only observed value is `"target"`. Other values and a Globalping-side probe error: not observed. Treat an unknown or missing `failureSource` on a failed result as `test_failed`, not `blocked`.
- HTTP 403 / 451 from the site: not observed (returned as a normal finished result with `statusCode` set, since non-2xx codes like 302 were).
- Request rejected: `400 validation_error` with `error.params` naming the field (examples above). Unknown or no matching probes: `422 no_probes_found`.

## Ping, traceroute, MTR, DNS results

Request shape: `{"type":"ping|traceroute|mtr|dns","target":"example.com","locations":[...]}` with no options. Echoed `measurementOptions` was absent (null) for all four.

Ping (default: 3 packets):

```json
{"status":"finished","rawOutput":"PING example.com (104.20.23.154) 56(84) bytes ...",
 "resolvedAddress":"104.20.23.154","resolvedHostname":"example.com",
 "timings":[{"ttl":57,"rtt":0.641},{"ttl":57,"rtt":0.862},{"ttl":57,"rtt":0.731}],
 "stats":{"min":0.641,"max":0.862,"avg":0.744,"total":3,"loss":0,"rcv":3,"drop":0}}
```

`rtt`, `min`, `avg`, `max` are milliseconds (floats); `loss` is a percent (0 observed). Failure form of ping: not observed.

Traceroute (`hops[]`; unanswered hops have null address and empty `timings`):

```json
{"status":"finished","rawOutput":"traceroute to example.com (172.66.147.243), 20 hops max...",
 "resolvedAddress":"172.66.147.243","resolvedHostname":"example.com",
 "hops":[{"resolvedAddress":null,"resolvedHostname":null,"timings":[]},
         {"resolvedAddress":"172.66.147.243","resolvedHostname":"example.com","timings":[{"rtt":1.833},{"rtt":1.857}]}]}
```

No ASN field on traceroute hops (not available). 9 hops observed with 8 silent.

MTR (`hops[]` with `asn` as a list, empty for private addresses, and `stats`):

```json
{"status":"finished","rawOutput":"Host  Loss% Drop Rcv  Avg  StDev  Javg ...",
 "resolvedAddress":"104.20.23.154","resolvedHostname":"example.com",
 "hops":[{"asn":[8560],"resolvedAddress":"212.227.120.29","resolvedHostname":"port-channel-2-1460.nf1-bp1-l8.rs.ber.de.net.ionos.com",
          "stats":{"min":0.386,"max":1.003,"avg":0.6,"total":3,"loss":0,"rcv":3,"drop":0,"stDev":0.3,"jMin":0.4,"jMax":0.6,"jAvg":0.5},
          "timings":[{"rtt":1.003},{"rtt":0.386},{"rtt":0.399}]}]}
```

`asn` is a list (empty `[]` for private hops). Silent hops can appear in `rawOutput` as "(waiting for reply)"; how they appear in `hops[]`: not observed. Note: the traceroute result had 9 hop entries while MTR found 8, so hop counts differ by tool.

DNS (A query, default resolver of the probe):

```json
{"status":"finished","rawOutput":"; <<>> DiG 9.18.47 ...","statusCodeName":"NOERROR","statusCode":0,
 "answers":[{"name":"example.com.","type":"A","ttl":56,"class":"IN","value":"104.20.23.154"},
            {"name":"example.com.","type":"A","ttl":56,"class":"IN","value":"172.66.147.243"}],
 "timings":{"total":0},"resolver":"private"}
```

`timings.total` is milliseconds (0 observed because the probe's resolver had it cached; integer resolution is coarse). `resolver` echoes the resolver (`private`). Choosing a specific resolver in the request: not tried. The `rawOutput` replaces the resolver address with `x.x.x.x`.

## Rate limits and allowance

- Cost: each probe in a measurement costs 1 test. A 4-probe measurement had `X-Request-Cost: 4` and decreased `X-RateLimit-Remaining` by 4; a 1-probe measurement cost 1; a 2-probe cost 2. Failed requests (400, 422) did not appear to consume allowance (not rigorously verified).
- Headers on the `POST` 202 response (observed): `x-request-cost`, `x-ratelimit-limit: 250`, `x-ratelimit-consumed`, `x-ratelimit-remaining`, `x-ratelimit-reset: 3600` (seconds until reset). The `Access-Control-Expose-Headers` list also names `X-Credits-Consumed`, `X-Credits-Remaining`, `Retry-After`; the credit headers were not seen (no account used).
- `GET /v1/limits` (no token) returns `200`:

```json
{"rateLimit":{"measurements":{"create":{"type":"ip","limit":250,"remaining":250,"reset":0}}}}
```

  Later in the session: `{"limit":250,"remaining":226,"reset":3554}`. `reset` is seconds until the window resets. `type: "ip"` for an unauthenticated caller. The authenticated shape is in "Authenticated run" below. `GET /limits` itself did not consume allowance (remaining was unchanged across calls).
- Polling `GET /v1/measurements/{id}` did not appear in the rate-limit headers (no `x-ratelimit-*` header on GET results).
- Probe-count cap of 50 per measurement (unauthenticated): stated in the published OpenAPI spec ("Non-authenticated requests are limited to a maximum of 50 probes"), not tried here. Authenticated: see below.
- 429 response (rate limit hit) body and headers: not observed. I deliberately did not exhaust the allowance. Same for the unregistered over-limit call (credits-required response). Later tasks must code the 429 handler defensively: treat any `429` as "Globalping limit reached" and read `Retry-After` / `x-ratelimit-reset` if present, and tests for it must use a fake, not a claimed real shape.

## Summary for later tasks

| Item | Status |
| --- | --- |
| http timings dns/tcp/tls/firstByte/download/total (ms) | available; `tls` null on HTTP; failures keep a numeric `total` |
| status code, headers, resolved IP | available |
| TLS version | available (`tls.protocol`) |
| HTTP version | only by parsing `rawOutput` first line; otherwise not available |
| CDN hit/miss | only from response headers (provider-specific) |
| follow redirects | not available (400); next hop from `headers.location` |
| probe reuse | works via previous id as `locations` |
| cert error | `tls.authorized == false` with `tls.error`, result still `finished` |
| DNS failure / timeout | `failed` + `failureSource: "target"` + `rawOutput` text |
| allowance | `x-ratelimit-*` on POST and `GET /v1/limits` |
| authenticated limits, probe cap, reuse, filters | see "Authenticated run" |
| 429 shape, credits headers | not observed |
| traceroute hop ASN | not available (MTR has `asn` list) |

## Authenticated run

Observed on 2026-11-01 with a free API key (no credits balance), about 77 tests (3+3+3 http/ping reuse, 60 ping, 8 filters). Raw responses were kept outside the repo. The key is never recorded here.

### Auth header and limit reporting

- Header that works: `Authorization: Bearer <token>`. An invalid token returns `401`: `{"error":{"type":"unauthorized","message":"Unauthorized."},"links":{"documentation":"https://globalping.io/docs/api.globalping.io#get-/v1/limits"}}`. A bad token is rejected, not silently treated as anonymous (observed on `GET /v1/limits`).
- Allowance doubles: the same `x-ratelimit-*` headers on the `POST /v1/measurements` 202 response, now `x-ratelimit-limit: 500` (was 250). Headers observed on a 202: `x-request-cost: 3`, `x-ratelimit-limit: 500`, `x-ratelimit-consumed: 3`, `x-ratelimit-remaining: 497`, `x-ratelimit-reset: 3600` (seconds until the window resets, counting down: 3600, 3598, 3596, ...). Header names are lowercase on the wire. `consumed` is this request's cost, not a running total; `remaining` is the running balance.
- `x-credits-consumed` / `x-credits-remaining` were NOT present on any response, even authenticated (this account has 0 credits; they are only listed in `Access-Control-Expose-Headers`). Whether they appear once credits exist: not observed.
- `GET /v1/limits` authenticated returns `200`:

```json
{"rateLimit":{"measurements":{"create":{"type":"user","limit":500,"remaining":500,"reset":0}}},"credits":{"remaining":0}}
```

  After spending 77 tests: `{"rateLimit":{"measurements":{"create":{"type":"user","limit":500,"remaining":423,"reset":3576}}},"credits":{"remaining":0}}`. So `type` is `user` (was `ip` unauthenticated), and a `credits.remaining` section is added (absent unauthenticated). `reset` is `0` while no window is open. The unauthenticated call from the same machine at the same time showed its own separate IP window (`type: ip`, limit 250), so the two allowances are independent. `GET /limits` did not consume allowance and carries no `x-ratelimit-*` headers (observed: the response had none). `GET /v1/measurements/{id}` results also carried none.
- Failed requests (400, 422) did not change `remaining` (checked against the totals: 497, 494, 491 after the three accepted calls; the 400 and two 422 calls in between left it unchanged).
- 429: not observed.

### More than 50 probes

`{"type":"ping","target":"example.com","locations":[{"magic":"world"}],"limit":60}` authenticated: `202`, `{"id":"...","probesCount":60}`, `x-request-cost: 60`, remaining 491 to 431. All 60 results finished, from 26 countries. The measurement echoes a top-level `"limit": 60`. The spec gives 500 as the maximum for `limit` (global or per-location, mutually exclusive); 60 is the largest value actually tried. The unauthenticated 50 cap was not tried, so its error message is not observed.

### Probe reuse (`from`)

There is no `from` field or magic. What works is the previous measurement id as the `locations` value (a plain string), as already noted. Tested with three locations (`Berlin`, `Tokyo`, `eyeball-network+Brazil`):

```json
{"type":"http","target":"example.com","locations":"22KyUQXITCD7ryMxu00121QQk",
 "measurementOptions":{"request":{"method":"GET","path":"/"},"protocol":"HTTPS"}}
```

- `202`, `probesCount: 3`, cost 3. The same three probes answered, in the same order: Berlin DE AS8560 `u-rutkk`, Tokyo JP AS3258 `u-JohnFilch123`, Sao Paulo BR AS26599 `u-CTassisF`, identical city/asn/network/tags in both measurements.
- Type and options are not inherited. The same id also worked for a different type (`ping`), with the same three probes.
- Rejected shapes: top-level `"from": "<id>"` returns `400` (`"from" is not allowed`). `locations: [{"magic":"from:<id>"}]` returns `422 no_probes_found`. An unknown id as a string returns `422` `{"error":{"type":"no_probes_found","message":"No matching IPv4 probes available."}}`, the same body as an expired id would be per the spec (`422`).
- Per the spec: reuse only works for the lifetime of the original measurement; the probes come back in the original order; a reused probe that is no longer available yields a test with `status: "offline"` that has only `status` and `rawOutput` (spec only, an offline probe was not observed). The app must treat `offline` like "no data".

### Location filters (authenticated, limit 2 each, ping)

| Request `locations` | Result |
| --- | --- |
| `[{"magic":"datacenter-network"}]` | 2 probes, Hetzner (AS24940) in Falkenstein DE and Helsinki FI, tag `datacenter-network` |
| `[{"magic":"eyeball-network"}]` | 2 probes, both Taiwan AS3462, tag `eyeball-network` |
| `[{"magic":"aws-eu-west-1"}]` | 1 probe despite limit 2: Dublin IE, AS16509, tags `aws-eu-west-1`, `aws`, `datacenter-network` |
| `[{"asn":15169}]` | 1 probe despite limit 2: Sao Paulo BR, AS15169 Google, tags `gcp-southamerica-east1`, `gcp`, `datacenter-network` |
| `[{"magic":"AS3320"}]` | 2 probes, Deutsche Telekom (Hamburg, Nuremberg), `eyeball-network` |

So fewer probes than `limit` can be returned when fewer match, and the cost is the number of probes actually used (1, not 2). Unfiltered tags give random probes, not a fixed one. Cloud region tags (`aws-...`, `gcp-...`) are in `probe.tags`. Note that `limit` there is per request when given at top level; per-location `limit` was not tried in this run.

### Other

- The OpenAPI spec (`https://api.globalping.io/v1/spec.yaml`) lists a top-level `timeout` (integer, 5 to 30, probe-side wall-clock timeout). Not tried here.
