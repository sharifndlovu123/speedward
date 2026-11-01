# speedward: design

Working name `speedward` (the author's guess; rename freely). A sibling to **siteward** (`/home/sharif/Documents/vs_code/work/devbridge/web_test`, repo `sharifndlovu123/siteward`), but for performance instead of security.

## Intent and why

Give the person running a site, thinking as an architect and not as a page visitor, a clear picture of how the site performs from around the world, and where it cannot be reached at all. The headline output is a **world map** with a connection line from the site's server to each test location. Lines are coloured by speed. Locations that cannot reach the site are dimmed and shown with a dashed grey line.

The questions it should answer: where does the time go (DNS, connect, TLS, first byte, download), is it distance or the network path, is a CDN helping, is the DNS answer the same everywhere, and who cannot reach the site.

## Goals

- Run, per test location, a **cold (no-cache) load**, a **warm (cache) load** and **round-trip delay**.
- World map, **layout A**: map across the top; summary tiles (average first byte, cache gain, blocked count, page weight) and a results table beneath. Clicking a dot highlights its row and opens its details.
- Per-location detail, chosen by the author:
  1. **Reason for unreachable**: timed out, DNS failed, 403/blocked, certificate error.
  2. **CDN hit or miss** from response headers (for example `cf-cache-status`, `age`).
  3. **Pass/fail limits** for automation, in the style of siteward's `--fail-on` (for example `--max-ttfb 800`; non-zero exit on failure).
  4. **Full page-load test** in a real browser run from the machine hosting the tool: page weight, request count, load time, biggest files. This is separate from the map, which is based on Globalping's single requests.
  5. **Export** as self-contained HTML report, JSON and Markdown.
  6. **Stage breakdown**: DNS, connect, TLS, first byte, download as a split bar.
  7. **Network path and packet loss** via traceroute/MTR.
  8. **Network type filtering**: home/ISP-hosted vs data-centre probes, and named cloud regions (for example `aws-eu-west-1`, `gcp-us-south1`).
  9. **DNS comparison across regions**: same answer everywhere, lookup time.
  10. **Server identity**: answering IP, redirect chain, TLS version, HTTP version (**only if Globalping returns them; unconfirmed**; otherwise drop or show "not available").
- Self-hostable, no database, nothing about scanned sites stored server-side, same shape as siteward: Python 3.11+, CLI plus local Django web UI, `.env` settings, Docker.

## Non-goals (for now)

- Hosting this as a public service. It is self-hosted only; each operator brings their own Globalping key.
- Repeat runs to show consistency/spread (declined for now; costs test allowance).
- Comparing against a previous run (declined for now).
- Active attack or load testing. This is not a load tool.
- Storing any scan result on the server.

## Anti-goals (a failure even if it "works")

- **Never show a fake or invented result when a test fails.** A failed test shows "no data" and a reason. Failed tests are excluded from averages and pass/fail limits, and the summary states how many were excluded.
- **"Blocked" means the site itself refused or timed out.** A problem with the test service (no probe available, Globalping error, rate limit hit) is labelled separately as "test did not run" or "test failed", so people don't blame their site.
- Never describe the Globalping key as simply "free" (see below).
- The web page never accepts or displays the Globalping key.

## Test locations: Globalping (builder's call to confirm)

Source of probe locations is **Globalping** (globalping.io, public API `POST https://api.globalping.io/v1/measurements`, REST docs at `https://globalping.io/docs/api.globalping.io`). This was the author's direction ("check for a free service"); it was not compared against other services in depth. Supports ping, traceroute, mtr, dns and http tests; location filters by continent, country, city, US state, ASN, network/ISP, `eyeball-network` and `datacenter-network` tags and cloud-region tags, combinable with `+`; and reuse of probes from an earlier measurement.

Limits as found on globalping.io (verify at build time and re-verify in the README):

| | Tests per hour | Probes per measurement |
|---|---|---|
| No account | 250 | 50 |
| Free account and key | 500 | 500 |

Above the free hourly allowance, registered users spend **credits**, one credit per test. For example a ping from 5 locations uses 5 credits. Credits come from GitHub Sponsors donations, or are earned by hosting a probe. What happens to unregistered users above 250 an hour is not documented; treat as a failed test and say so.

**Wording rule:** the key is "free up to a limit" (a free hourly allowance); heavier use costs credits. The README, status line and setup text all say this and link to globalping.io. Never just "free".

### Not yet confirmed (builder must verify first)

The Globalping API reference could not be opened during design. Unconfirmed: the exact HTTP result fields (DNS, TCP, TLS, first byte, download, total, status, headers), whether HTTP/TLS versions and resolved IP are returned, whether cache-style repeat requests can be expressed, and the exact response when a rate limit is hit. Features 6 and 10 above depend on this. If a field is missing, the feature degrades to "not available", never to a guess.

### Cache test

A single remote request cannot reproduce a browser's cache. Define "cold" and "warm" as: first request vs. an immediate repeat request (and conditional request behaviour / cache headers where observable), plus CDN hit/miss headers. The real-browser run (feature 4) gives the true browser-cache picture for the host machine. Document this limitation in the UI and README.

## Key handling

- Optional setting `GLOBALPING_TOKEN` in `.env` (same style as siteward settings) or as an environment variable for the CLI.
- No input box for it in the web UI (the app has no login, so a pasted key would be visible). It never reaches the browser, exports, or logs.
- Works without a key at the lower limits.
- Web UI status line shows one of: no key set (250 tests/hour, 50 locations, link to get a free-up-to-a-limit key), or key active (500/hour, up to 500 locations, tests remaining this hour).

## Test budget

One scan uses many tests: every location times every test type times cold and warm. Before starting, show an **estimated cost** ("about 36 of your 250 tests this hour"). Refuse to start a scan that will not fit in the remaining allowance unless the person confirms. Credits spent beyond the free allowance must be made explicit before confirming.

## Saved results and export

- Results are kept in the browser's **session storage** only: they survive a refresh or back button, and disappear when the tab or window is closed. The server stores nothing.
- A **time limit** (default 15 minutes, configurable through an env setting; the author said "perfect" to the suggestion to keep 15 minutes as a safety net) removes stale results.
- On return within the limit, a banner shows "Showing results from N minutes ago. Kept until you close this tab. No tests were used." with **Run again (shows test cost)** and **Clear now**.
- After expiry, a message says the saved results expired and were removed.
- Private windows may not allow storage; the page says so instead of failing silently.
- **Export** (HTML report, JSON, Markdown) buttons sit in the results header next to Run again and Clear now, with a reminder that results are kept only until the tab closes.
- If the tab is closed with unexported results, the browser's standard "Leave this page?" prompt appears (browsers cannot block closing; it appears only when something is unexported).
- Exports work from the saved copy and contain no key.

## Design decisions by the author

Map style A (connection lines from the server, colour by speed, dimmed dashed grey for unreachable); results layout A; Globalping with a self-supplied key; session storage with export.

**Scope: all ten features under Goals are in the first release.** The author picked, from two mockup lists, reason for unreachable, CDN hit/miss, pass/fail limits, full page load and export (first list), and stage breakdown, network path, network type, DNS comparison and server identity (architect list). Those ten are exactly Goals items 1 to 5 and 6 to 10. Declined: repeat runs for consistency, and comparison with an earlier run.

## Calls made while writing this document (the author has not decided these; review them)

Marked as the writer's calls after the builder check. Change any you disagree with.

1. **First plan task is a verification spike.** Call the real Globalping API, record the actual response shape (timings, status, headers, IP, versions, rate-limit response), and plan the rest around it. If a field is missing, that feature shows "not available" and the rest still ships. Do not stop and replan. Headers are assumed available but must be confirmed in the same spike.
2. **Warm run:** a second Globalping measurement that reuses the same probes as the cold run (Globalping can reuse probes from an earlier measurement). This doubles the http test count in the budget. If the same probes cannot be reused, the "cache gain" tile is hidden and the table shows the repeat timing only, labelled "repeat request (different probe)".
3. **Default scan** = cold http + warm http + ping, at a spread of about 10 to 12 locations (at least one per continent, plus some large markets). Traceroute/MTR and DNS comparison are opt-in (CLI flags, web checkboxes) because they cost extra tests. The cost estimate is computed from the selected options (locations x tests) and assumes one test per location per measurement; confirm the real billing rule in the spike. Without a key the location cap is 50.
4. **Pass/fail:** each limit (`--max-ttfb`, and similarly page load time, blocked count, page weight) is checked against every location that was measured successfully, and any one over the limit fails. Excluded (failed-to-run) locations never count as passing. If no location measured successfully, exit with a distinct code (2) meaning "could not test", not pass and not the normal fail code.
5. **Real-browser test:** shipped as a separate, optional action (`--browser`, a checkbox in the web UI) run from the host machine, shown in its own panel and not on the map. The browser dependency is an optional install extra and off by default.
6. **Server position on the map:** from the answering IP, looked up in an optional offline geo-IP database that the operator installs. No lookups to outside services, so the scanned target is never leaked. There is also a manual override (a setting and CLI flag for latitude/longitude). If neither is available, the map shows the locations around an unplaced "server" marker and says the position is unknown.
7. **Flow:** the Django server runs the scan using the key and returns results to the browser, which keeps them in session storage. The server holds only the in-flight scan's state in memory, discarded when it finishes. Progress is by polling. The CLI and the web UI share one export code path.
8. **Default locations and reasons:** a continent-spread default list, editable by setting or flag. Unreachable reasons are mapped from the raw result: connection timeout, DNS failure, certificate error, HTTP 403 or 451 as "blocked" (the site refused), and any Globalping-side error as "test failed" (not the site's fault). Up to 5 redirects are followed and the chain is reported. The URL must be reachable without a login.

## Constraints

- Match siteward's stack and conventions (Python 3.11+, Typer CLI, Django web, httpx, Jinja2 reports, whitenoise, gunicorn, pytest). Read the siteward repo before planning.
- No database; nothing about scanned sites stored on the server; logs never contain targets or results (same as siteward).
- Siteward-style authorisation notice: only test sites you run or have authority to test.
- The author wants a mockup for any change or idea during design.
- The project folder `site-speed` is not yet a git repo. After this document is approved, initialise it (`git init` and a `CLAUDE.md`) as the author asked ("remember to init").

## Left to the builder

- Package layout, module names, CLI command names and flags (beyond `--max-ttfb`-style limits).
- Map technology (a lightweight, self-hosted option preferred; no third-party map tiles that would leak scanned targets).
- Which default set of locations to test and how to choose representative ones within the probe budget.
- Colour thresholds for fast/medium/slow (make them configurable).
- How to run the real-browser test (for example Playwright or Lighthouse) and keeping that dependency optional.
- Test strategy, including a fake Globalping server for tests, as siteward uses `tests/fake_server.py`.
- Settings names other than `GLOBALPING_TOKEN`.
