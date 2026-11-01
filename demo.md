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

This run was made on 2026-11-01 against `https://example.com` with a Globalping key. Results vary from run to run and with the hour of day; these numbers are from one run and are not a benchmark.

### 1. Check the cost

```bash
speedward scan https://example.com --estimate
```

```
Only test sites you own or are authorised to test.
This scan uses about 36 tests. You have 500 left this hour.
Globalping credits: 0.
```

It stops there, using no tests. Without a key the second line starts with a note about the 250 tests/hour limit.

### 2. Run the scan

```bash
speedward scan https://example.com --format html --out docs/demo-report.html
```

Progress goes to standard error ("Testing the first visit from 12 locations", "Repeating the test on the same probes", "Measuring network delay", "Finished"). The exit code was 0 (no limits set); this run used about 36 tests. The saved report is [docs/demo-report.html](docs/demo-report.html) (open it in a browser to see the map). The default Markdown format prints this to the terminal; the table and summary below are from a Markdown render of the same kind of scan (a second real run with the same options, whose HTML is the saved file):

## Summary

- Average first byte: 28 ms
- Cache gain: -3%
- Blocked: 0
- 0 locations excluded from the figures (0 blocked); 12 measured.
- Repeat request from the same test machine; not a browser cache test.

## Locations

| Location | No cache | Cache | Delay | CDN | Status |
| --- | --- | --- | --- | --- | --- |
| London, GB | 19 ms | 9 ms | not available | HIT | HTTP 200 |
| Frankfurt, DE | 16 ms | 18 ms | not available | HIT | HTTP 200 |
| New York, US | 9 ms | 8 ms | not available | HIT | HTTP 200 |
| Los Angeles, US | 12 ms | 13 ms | not available | HIT | HTTP 200 |
| Sao Paulo, BR | 6 ms | 9 ms | not available | HIT | HTTP 200 |
| Johannesburg, ZA | 7 ms | 7 ms | not available | HIT | HTTP 200 |
| Lagos, NG | 110 ms | 127 ms | not available | HIT | HTTP 200 |
| Mumbai, IN | 7 ms | 11 ms | not available | HIT | HTTP 200 |
| Singapore, SG | 14 ms | 12 ms | not available | HIT | HTTP 200 |
| Tokyo, JP | 6 ms | 5 ms | not available | HIT | HTTP 200 |
| Sydney, AU | 8 ms | 6 ms | not available | HIT | HTTP 200 |
| Dubai, AE | 127 ms | 125 ms | not available | HIT | HTTP 200 |

## Stage breakdown (average over measured locations)

- DNS: 24 ms
- Connect: 20 ms
- TLS: 30 ms
- First byte: 28 ms
- Download: 1 ms

The report also lists the server identity (answering IPs 104.20.23.154 and 172.66.147.243, TLS 1.3, HTTP/1.1, no redirect on the first response).

## How to read it

- **The map** (top of the HTML report) has one dot per location, coloured by first-byte time. Green is fast, amber is middling, red is slow, against `FAST_MS` (300) and `SLOW_MS` (800).
- **No cache / Cache** are first-byte times for the first visit and a repeat request from the same test machine. The repeat is not a browser cache test. "Cache gain" in the summary can be negative, as here (-3%): the repeat was not faster.
- **Dimmed, dashed grey lines** on the map are places that could not reach the site (blocked, test failed or did not run). Their rows say "no data" with a reason. They show no number and are left out of averages and limits. Every location in this run was measured, so none were dimmed here.
- **Delay** shows "not available" in this run. It is never filled in with a guess.
- **Status**: measured, blocked (the site refused or timed out), test failed (a Globalping problem) or did not run.
- **Cost**: 12 locations cost about 36 tests. Add `--traceroute` or `--dns` for more. The cost is shown before every scan, and a scan that does not fit your remaining allowance is refused unless you pass `--yes`.
- **Exit codes**: 0 done and every limit passed (or none set); 1 a limit failed (for example `--max-ttfb 200`); 2 the scan could not run or nothing could be measured.

## The web UI

This section is written from the page templates and a real load of the home page (HTTP 200), not from a screenshot.

The home page shows the title, "How fast your site loads from around the world.", the authorisation notice and a status line (with a key it read "Globalping key active: 500 tests/hour, up to 500 locations. Remaining this hour: ..."). Below is the form: Site address, Locations (empty means the default 12 places, listed as the placeholder), two Extra checks (network path, DNS answers), a collapsible Pass/fail limits section, and a Start test button. As you type, an estimated cost is shown; if it will not fit your allowance a warning appears with a checkbox to go ahead anyway. While the test runs, progress lines appear. Results then show the map and table with buttons for HTML, JSON and Markdown export, Run again and Clear now. They are kept only in the browser's session storage until the tab closes (15 minutes at most); a banner offers the saved results after a refresh. Nothing about the tested site is stored on the server.
