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

Optional: download the location file, so the map can place the server that answered each place. Get a City `.mmdb` file (MaxMind GeoLite2 City, free account and licence; or DB-IP Lite City, free, CC BY 4.0; check the licence), then:

```bash
pip install -e ".[geoip]"
echo "GEOIP_DB=/path/to/GeoLite2-City.mmdb" >> .env
```

`scripts/update_geoip.sh` keeps that file current (MaxMind through `geoipupdate`, or `--source dbip` with no account), and with Docker Compose (`docker compose up -d --build`) MaxMind's geoipupdate container does it for you; see the README. The lookup is offline; nothing about your target leaves the machine. Without the file, Cloudflare edges are still placed from their `cf-ray` header.

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

The report below comes from one real run made on 2026-11-02 against `https://example.com` with a Globalping key and a location file set, from the default 12 places (about 36 tests). Results vary from run to run and with the hour of day; these numbers are from one run and are not a benchmark. The estimate output in step 1 is from an earlier run on 2026-11-01, so its allowance figure is that day's.

### 1. Check the cost

```bash
speedward scan https://example.com --estimate
```

```
Only test sites you own or are authorised to test.
This scan uses about 36 tests. You have 412 left this hour.
Globalping credits: 0.
```

It stops there, using no tests. Without a key the second line starts with a note about the 250 tests/hour limit. Current builds also add when the allowance resets to the second line, for example "(resets in 42 min)".

### 2. Run the scan

```bash
speedward scan https://example.com --format html --out docs/demo-report.html
```

(In this demo the run was saved once as JSON and rendered to HTML and Markdown afterwards with the current report code, so one scan gave both; it cost the same.) Progress goes to standard error ("Testing the first visit from 12 locations", "Repeating the test on the same probes", "Measuring network delay", "Finished"); a measurement that could not run would be listed there as "Not run: ...". With no limits set the exit code is 0. The saved report is [docs/demo-report.html](docs/demo-report.html) (open it in a browser to see the map with its squares and lines). The default Markdown format prints this to the terminal; the summary and table below are the Markdown render of the same JSON, so they match `docs/demo-report.html` exactly:

## Summary

- Average first byte: 21 ms
- Cache gain: -7%
- Blocked: 0
- 0 locations excluded from the figures (0 blocked); 12 measured.
- Repeat request from the same test machine; not a browser cache test.

## Locations

| Location | No cache | Cache | Delay | CDN | Answered by | Position source | Status |
| --- | --- | --- | --- | --- | --- | --- | --- |
| London, GB | 18 ms | 14 ms | 2 ms | HIT | edge LHR (London) | CDN header (cf-ray -LHR) | HTTP 200 |
| Frankfurt, DE | 22 ms | 11 ms | 1 ms | HIT | edge FRA (Frankfurt) | CDN header (cf-ray -FRA) | HTTP 200 |
| New York, US | 9 ms | 30 ms | 3 ms | HIT | edge EWR (Newark) | CDN header (cf-ray -EWR) | HTTP 200 |
| Los Angeles, US | 15 ms | 8 ms | 1 ms | HIT | edge LAX (Los Angeles) | CDN header (cf-ray -LAX) | HTTP 200 |
| Sao Paulo, BR | 7 ms | 10 ms | 1 ms | HIT | edge GRU (Sao Paulo) | CDN header (cf-ray -GRU) | HTTP 200 |
| Johannesburg, ZA | 10 ms | 13 ms | 2 ms | HIT | edge JNB (Johannesburg) | CDN header (cf-ray -JNB) | HTTP 200 |
| Lagos, NG | 111 ms | 119 ms | 104 ms | HIT | edge LHR (London) | CDN header (cf-ray -LHR) | HTTP 200 |
| Mumbai, IN | 8 ms | 8 ms | 2 ms | HIT | edge BOM (Mumbai) | CDN header (cf-ray -BOM) | HTTP 200 |
| Singapore, SG | 20 ms | 24 ms | 2 ms | HIT | edge SIN (Singapore) | CDN header (cf-ray -SIN) | HTTP 200 |
| Tokyo, JP | 15 ms | 15 ms | 1 ms | HIT | edge NRT (Tokyo) | CDN header (cf-ray -NRT) | HTTP 200 |
| Sydney, AU | 6 ms | 6 ms | 1 ms | HIT | edge SYD (Sydney) | CDN header (cf-ray -SYD) | HTTP 200 |
| Dubai, AE | 14 ms | 14 ms | 106 ms | HIT | edge MCT (Muscat) | CDN header (cf-ray -MCT) | HTTP 200 |

## Stage breakdown (average over measured locations)

- DNS: 26 ms
- Connect: 11 ms
- TLS: 27 ms
- First byte: 21 ms
- Download: 1 ms

The default line colours were used (green up to `FAST_MS` 300 ms, red from `SLOW_MS` 800 ms). Every repeat request in this run came from the same test machine as the first visit, so no row carries a "different probe" label and the cache gain counts all 12 places.

The report also lists the server identity (answering IPs 104.20.23.154 and 172.66.147.243, TLS 1.3, HTTP/1.1, no redirect on the first response).

## How to read it

- **The map** (top of the HTML report) has one dot per location, coloured by first-byte time. Green is fast, amber is middling, red is slow, against `FAST_MS` (300) and `SLOW_MS` (800).
- **No cache / Cache** are first-byte times for the first visit and an immediate repeat request, which speedward asks to run on the same test machine. The repeat is not a browser cache test. "Cache gain" in the summary can be negative, as here (-7%): on average the repeat was not faster (New York went from 9 to 30 ms). When the repeat has to run on another machine, its timing is labelled "repeat request (different probe)" (or "..., same city") and that place is left out of the cache gain; if the repeat could not run at all, the cache tile says why, for example "repeat test: Globalping limit reached", and a "Not run" note names the empty column.
- **Dashed grey lines and hollow grey dots** on the map are places that could not reach the site (blocked, test failed or did not run). Their rows say "no data" with a reason. They show no number and are left out of averages and limits. Every location in this run was measured, so none were dimmed here.
- **Squares and lines**: a square marks the server that answered each place, with a line from the place's circle to it, coloured by that place's first-byte time. Behind a CDN (as example.com is, on Cloudflare) each place is answered by a nearby edge: "Answered by" reads like "edge NRT (Tokyo)" and "Position source" like "CDN header (cf-ray -NRT)", and the tooltip says "CDN edge, not necessarily your origin". When the edge is in the same city as the place, the square is drawn as a frame around the circle. In this run most places were answered in their own city, but Lagos was answered by the London edge (LHR) and Dubai by Muscat (MCT), so their lines run to those squares; their delay of about 105 ms fits that. A site on one server shows one "Server" square, placed by the geo-IP database.
- **Uncertain cases draw nothing**: an address shared across continents, one the database and the CDN header disagree on by more than 2,500 km, or one that answered faster than light could travel from its claimed position gets no square and no line, and reads "edge location uncertain (...)"; a private address or no location file reads "position not available (...)". Never a guess. With no position at all, the map says "Server position unknown" and how to set one.
- **Delay** is the network round-trip time from that location to the site (a ping), separate from the page timings. In this run it was 1 to 3 ms in most places and about 105 ms for Lagos and Dubai. If a delay could not be measured it shows "not available", never a guess.
- **Status**: measured, blocked (the site refused or timed out), test failed (a Globalping problem, including the test machine's own DNS resolver failing) or did not run. If a scan cannot start at all, the message says why: Globalping limit reached, no test machine for one of the places you chose, the key was rejected, or the address was not accepted.
- **Cost**: 12 locations cost about 36 tests. Add `--traceroute` or `--dns` for more. The cost is shown before every scan, and a scan that does not fit your remaining allowance is refused unless you pass `--yes`. In the web UI, Start waits until the cost for the current picks is on screen.
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

The home page is a world map that fills the window, with panels floating over it. The look is calm and flat: warm paper and ink (a near-black version follows the device's dark mode), hairline borders, no shadows, rounded pills only on buttons and place chips, and one red used only for the server marker and Start test. The two steps are numbered 01 (address) and 02 (locations). The top strip shows the title, the authorisation notice and the allowance strip (with a key it reads "Globalping key active: 500 tests/hour, up to 500 locations. 321 of 500 tests left this hour ..."). The left panel holds the Site address, two Extra checks (network path, DNS answers), a collapsible Pass/fail limits section, a "Server is in:" list (it sets the draggable Server pin on the map, a fallback used only when no answering server can be placed; faint lines run from the pin to each picked place), the cost line ("12 locations · about 36 tests") and Start test. The bottom panel is the location picker: click a dot on the map or a name to switch a place on or off (a filled dot or a ticked chip is on, an open ring or plain chip is off), use the presets (Default 12, One per continent, All, Clear), or add another place by name. Picking nothing is an error, never the default. As you pick, the estimated cost updates; if it will not fit your allowance a warning appears with a checkbox to go ahead anyway. While the test runs the panels are locked and progress lines appear. Results then show on the same map: a circle per test location, a square at the server that answered it, and a speed-coloured line between them (a CDN gives a square near each place, with the tooltip "CDN edge, not necessarily your origin"; a single origin gives one "Server" square with a line from every place; a shared address whose place is uncertain draws nothing, and the table says "edge location uncertain"). With no location file set, the side panel says "No server positions yet", with "How to set it up" and "Place a server manually (your own claim)". The results have a side panel led by the summary figures in large numerals (average first byte, cache gain, blocked out of all places), a table drawer that opens when you click a dot, and buttons for HTML, JSON and Markdown export, Run again, Clear now and "Locations (N)" to reopen the picker. They are kept only in the browser's session storage until the tab closes (15 minutes at most); a banner offers the saved results after a refresh, with your picks restored. Nothing about the tested site is stored on the server.
