# speedward demo

## Try it on your own machine

Needs Python 3.11 or newer. Full self-hosting guide: [README.md](README.md).

```bash
git clone <this repository> speedward && cd speedward
python -m venv .venv
. .venv/bin/activate
pip install -e .
```

Optional: get a Globalping key at <https://globalping.io>. It is free up to a limit (250 tests/hour without a key, 500 with one). Put it in a `.env` file in the folder you run from, never in the web page:

```
GLOBALPING_TOKEN=your-key-here
```

Check the cost first, then scan and save the HTML report:

```bash
speedward scan https://example.com --estimate
speedward scan https://example.com --format html --out report.html
```

Open `report.html` in a browser. To use the web UI:

```bash
DEBUG=1 django-admin runserver --settings=speedward.web.settings
```

Open <http://127.0.0.1:8000/>, enter an address, review the estimate, press Start test, then refresh the page to see the saved results, use Export to keep them, and close the tab to watch them go. `DEBUG=1` is for your own machine only.

Only test sites you own or are authorised to test.
(example.com is IANA's demo domain and is used below.)

---

## What a scan looks like

This run was made on 2026-11-01 against `https://example.com` with a Globalping key. The estimate before it read "about 36 tests". Results vary from run to run and with the hour of day; these numbers are from one run and are not a benchmark.

### 1. Check the cost

```bash
speedward scan https://example.com --estimate
```

```
Only test sites you own or are authorised to test.
This scan uses about 36 tests. You have 412 left this hour.
Globalping credits: 0.
```

It stops there, using no tests. Without a key the second line starts with a note about the 250 tests/hour limit.

### 2. Run the scan

```bash
speedward scan https://example.com --format html --out docs/demo-report.html
```

(In this demo the run was made with `--format json` and rendered to HTML and Markdown afterwards, so one scan gave both; it cost the same.) Progress goes to standard error ("Testing the first visit from 12 locations", "Repeating the test on the same probes", "Measuring network delay", "Finished"). The exit code was 0 (no limits set); this run used about 36 tests (the allowance counter dropped by 36). The saved report is [docs/demo-report.html](docs/demo-report.html) (open it in a browser to see the map). The default Markdown format prints this to the terminal; the table and summary below are the Markdown render of the same run. That run was saved once as JSON and then rendered to both Markdown and HTML, so the table and `docs/demo-report.html` match exactly:

## Summary

- Average first byte: 20 ms
- Cache gain: 4%
- Blocked: 0
- 0 locations excluded from the figures (0 blocked); 12 measured.
- Repeat request from the same test machine; not a browser cache test.

## Locations

| Location | No cache | Cache | Delay | CDN | Status |
| --- | --- | --- | --- | --- | --- |
| London, GB | 22 ms | 17 ms | 1 ms | HIT | HTTP 200 |
| Frankfurt, DE | 10 ms | 12 ms | 1 ms | HIT | HTTP 200 |
| New York, US | 12 ms | 8 ms | 3 ms | HIT | HTTP 200 |
| Los Angeles, US | 15 ms | 12 ms | 1 ms | HIT | HTTP 200 |
| Sao Paulo, BR | 10 ms | 8 ms | 1 ms | HIT | HTTP 200 |
| Johannesburg, ZA | 7 ms | 7 ms | 2 ms | HIT | HTTP 200 |
| Lagos, NG | 114 ms | 115 ms | 105 ms | HIT | HTTP 200 |
| Mumbai, IN | 8 ms | 6 ms | 1 ms | HIT | HTTP 200 |
| Singapore, SG | 12 ms | 9 ms | 2 ms | HIT | HTTP 200 |
| Tokyo, JP | 7 ms | 9 ms | 2 ms | HIT | HTTP 200 |
| Sydney, AU | 6 ms | 7 ms | 1 ms | HIT | HTTP 200 |
| Dubai, AE | 13 ms | 16 ms | 106 ms | HIT | HTTP 200 |

## Stage breakdown (average over measured locations)

- DNS: 11 ms
- Connect: 11 ms
- TLS: 20 ms
- First byte: 20 ms
- Download: 1 ms

The report also lists the server identity (answering IPs 172.66.147.243 and 104.20.23.154, TLS 1.3, HTTP/1.1, no redirect on the first response).

## How to read it

- **The map** (top of the HTML report) has one dot per location, coloured by first-byte time. Green is fast, amber is middling, red is slow, against `FAST_MS` (300) and `SLOW_MS` (800).
- **No cache / Cache** are first-byte times for the first visit and a repeat request from the same test machine. The repeat is not a browser cache test. "Cache gain" in the summary can be negative, as here (-3%): the repeat was not faster.
- **Dimmed, dashed grey lines** on the map are places that could not reach the site (blocked, test failed or did not run). Their rows say "no data" with a reason. They show no number and are left out of averages and limits. Every location in this run was measured, so none were dimmed here.
- **Delay** is the network round-trip time from that location to the site (a ping), separate from the page timings. In this run it was 1 to 3 ms in most places and about 105 ms for Lagos and Dubai. If a delay could not be measured it shows "not available", never a guess.
- **Status**: measured, blocked (the site refused or timed out), test failed (a Globalping problem) or did not run.
- **Cost**: 12 locations cost about 36 tests. Add `--traceroute` or `--dns` for more. The cost is shown before every scan, and a scan that does not fit your remaining allowance is refused unless you pass `--yes`.
- **Exit codes**: 0 done and every limit passed (or none set); 1 a limit failed (for example `--max-ttfb 200`); 2 the scan could not run or nothing could be measured.

## Extra checks: path and DNS

```bash
speedward scan https://example.com --traceroute --dns --locations "London,Tokyo"
```

Estimate: about 10 tests; exit code 0. Output trimmed (summary and stage breakdown omitted; the route from London is cut to its first and last hops, and hop names shown are what the probe saw):

```
## DNS

- London, GB: 172.66.147.243, 104.20.23.154 (0 ms)
- Tokyo, JP: 172.66.147.243, 104.20.23.154 (3 ms)

## Route from London, GB

1. _gateway 1 ms, loss 0%
2. 192.168.143.254 0 ms, loss 0%
   ... (hops 3 to 10 omitted)
11. 141.101.71.174 1 ms, loss 0%
12. 141.101.71.219 2 ms, loss 0%
13. example.com 2 ms, loss 0%
```

A hop that did not answer shows as "not available" with 100% loss, and the Tokyo route shows two of those plus one hop with 66.7% loss.

## The web UI

This section is written from the page templates and a real load of the home page (HTTP 200), not from a screenshot.

The home page is a world map that fills the window, with panels floating over it. The top strip shows the title, the authorisation notice and the allowance strip (with a key it reads "Globalping key active: 500 tests/hour, up to 500 locations. 321 of 500 tests left this hour ..."). The left panel holds the Site address, two Extra checks (network path, DNS answers), a collapsible Pass/fail limits section, the cost line ("12 locations · about 36 tests") and Start test. The bottom panel is the location picker: click a dot on the map or a name to switch a place on or off (blue is on, a grey ring is off), use the presets (Default 12, One per continent, All, Clear), or add another place by name. Picking nothing is an error, never the default. As you pick, the estimated cost updates; if it will not fit your allowance a warning appears with a checkbox to go ahead anyway. While the test runs the panels are locked and progress lines appear. Results then show on the same map, with a side panel of summary figures, a table drawer that opens when you click a dot, and buttons for HTML, JSON and Markdown export, Run again, Clear now and "Locations (N)" to reopen the picker. They are kept only in the browser's session storage until the tab closes (15 minutes at most); a banner offers the saved results after a refresh, with your picks restored. Nothing about the tested site is stored on the server.
