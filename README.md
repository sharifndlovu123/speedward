# speedward

Speedward tests how fast a website loads from places around the world and shows the result on a world map. It runs the tests through the [Globalping](https://globalping.io) network. You run it yourself, as a command-line tool or as a small web app. It is the sibling of [siteward](https://github.com/sharifndlovu123/siteward), the passive security scanner, and uses the same stack and conventions.

**Only test sites you own or are authorised to test.** Every scan sends real requests to the target from many machines.

See [demo.md](demo.md) for a short walkthrough built from a real run.

## What you need

- Python 3.11 or newer, or Docker.
- Internet access to `api.globalping.io`.
- Optional: a Globalping key (below). The tool works without one, with lower limits.

## Install

```bash
git clone <this repository> speedward && cd speedward
python -m venv .venv
. .venv/bin/activate
pip install -e .
speedward scan --help
```

Optional extras: `pip install -e ".[geoip]"` (offline position of the server that answered each place, see below).

## Get a Globalping key

The key is optional and free up to a limit. Create a free account at <https://globalping.io> and make a key in its dashboard.

| | No key | Free key |
| --- | --- | --- |
| Tests per hour | 250 | 500 |
| Locations per run | 50 | up to 500 |

Above the free allowance each test costs one Globalping credit. Credits are bought through GitHub Sponsors or earned by hosting a Globalping probe. See <https://globalping.io> for current terms; they are Globalping's, not this project's.

Put the key in a file named `.env` in the folder you run speedward from:

```
GLOBALPING_TOKEN=your-key-here
```

Start from `.env.example` (`cp .env.example .env`). The key stays on the machine running speedward. The web page has no input for it, and it never reaches the browser, exports or logs. Do not commit `.env` (it is in `.gitignore`).

Only an allowlist of keys is read from `.env` (everything in `.env.example`). `GLOBALPING_BASE_URL`, which points at a different Globalping endpoint (used by the tests), is read from the real environment only, and speedward refuses to send a key to any endpoint that is not Globalping or this machine.

## First scan from the command line

Check the cost first. This prints the estimate and your remaining allowance, then stops without running anything:

```bash
speedward scan https://example.com --estimate
```

Then run it. The default is 12 locations (London, Frankfurt, New York, Los Angeles, Sao Paulo, Johannesburg, Lagos, Mumbai, Singapore, Tokyo, Sydney, Dubai), about 36 tests:

```bash
speedward scan https://example.com                                    # Markdown to the terminal
speedward scan https://example.com --format html --out report.html    # HTML report with the map
speedward scan https://example.com --locations Berlin,Tokyo,Brazil    # your own places (the web UI picks places on the map instead)
```

Options (all from `speedward scan --help`):

| Option | Meaning |
| --- | --- |
| `--locations` | Comma-separated places, for example `Berlin,Tokyo,Brazil` |
| `--traceroute` | Also trace the network path (more tests) |
| `--dns` | Also check DNS answers per place (more tests) |
| `--format` | `json`, `md` (default) or `html` |
| `--out FILE` | Write the report to a file |
| `--max-ttfb MS`, `--max-load MS`, `--max-blocked N` | Pass/fail limits |
| `--yes` | Go ahead even if the scan costs more than the tests left |
| `--estimate` | Print cost and allowance, then stop |
| `--verbose` | More detail when something goes wrong |

Exit codes: `0` scan done and every limit passed (or none set), `1` a limit failed, `2` the scan could not run or no location could be tested. Messages go to standard error, the report to standard output (or `--out`).

## Run the web UI locally

```bash
DEBUG=1 django-admin runserver --settings=speedward.web.settings
```

Open <http://127.0.0.1:8000/>. Enter an address, review the estimate, press Start test. `DEBUG=1` is for your own machine only.

## Run it with Docker

```bash
docker build -t speedward .
docker run --rm -p 127.0.0.1:8000:8000 \
  -e SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(50))')" \
  -e ALLOWED_HOSTS=localhost,127.0.0.1 \
  --env-file .env speedward
```

The image runs gunicorn with one worker and eight threads as a non-root user. `.dockerignore` keeps `.env` out of the image, so pass settings with `-e` or `--env-file` at run time. `--env-file` takes the file as written, so keep values unquoted. Binding to `127.0.0.1` on the host (as above) keeps it private; see the next section before changing that.

## Run it for other people (read this first)

- **The web app has no login and no per-visitor rate limit.** Anyone who can reach it can start scans and spend your Globalping tests (and credits). Bind it to loopback, or put it behind your own authentication (a reverse proxy with basic auth or SSO, a VPN). `ALLOWED_HOSTS` only checks the Host header; it is not access control.
- **Use exactly one gunicorn worker, with threads** (`--workers 1 --threads 8`, as the Dockerfile does). Scan progress is held in that process's memory. More workers break progress polling and multiply the concurrency limit (`MAX_CONCURRENT_SCANS`).
- **Never expose a `DEBUG=1` instance.** Outside `DEBUG` the server refuses to start without a fixed `SECRET_KEY`.
- **Over HTTPS**, set `CSRF_TRUSTED_ORIGINS` to your full origin (for example `https://speed.example.com`) and `ALLOWED_HOSTS` to the host name. Speedward does not turn on HSTS or the `Secure` flag on its CSRF cookie itself, so have your reverse proxy terminate TLS and send `Strict-Transport-Security`. The app sends no session cookie; its CSRF cookie is `HttpOnly` and `SameSite=Strict`.
- Behind a proxy, pass the original `Host` header through.
- The Globalping key is server-side only.

Example without Docker:

```bash
pip install -e .
export SECRET_KEY=... ALLOWED_HOSTS=speed.example.com CSRF_TRUSTED_ORIGINS=https://speed.example.com
gunicorn speedward.web.wsgi:application --bind 127.0.0.1:8000 --workers 1 --threads 8 --timeout 60
```

Run `STATIC_ROOT=$PWD/staticfiles django-admin collectstatic --noinput --settings=speedward.web.settings` once, and start gunicorn with the same `STATIC_ROOT`. Whitenoise serves the files.

## Settings

Set these in the environment or in `.env` (the real environment wins). Defaults come from `speedward/web/settings.py` and `speedward/core/config.py`.

| Setting | Default | Meaning |
| --- | --- | --- |
| `GLOBALPING_TOKEN` | none | Globalping key. Optional. |
| `FAST_MS` | `300` | Timings at or under this are shown as fast |
| `SLOW_MS` | `800` | Timings over this are shown as slow. Must be above `FAST_MS` |
| `GEOIP_DB` | none | Path to an offline City `.mmdb` database, used to place the server that answered each place (see below) |
| `SERVER_LAT`, `SERVER_LON` | none | Server position by hand. Wins over everything else for the one Server square |
| `RESULT_TTL_SECONDS` | `900` | Web UI: how long the browser keeps results. Minimum 60 |
| `MAX_CONCURRENT_SCANS` | `2` | Web UI: scans at once. Minimum 1 |
| `SECRET_KEY` | random per start | Web UI: required when `DEBUG` is off |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Web UI: accepted Host names. Not access control |
| `CSRF_TRUSTED_ORIGINS` | none | Web UI: full origins when served over HTTPS |
| `DEBUG` | off | Web UI: `1`, `true`, `yes` or `on` enable it. Local use only |

Not a `.env` setting: `STATIC_ROOT` (real environment only; defaults to `./staticfiles`).

## Optional: lines to the server that answered each place

Each measured place records the IP address that answered it. Speedward puts that server on the map as a **square**, with a speed-coloured line from the place's circle to it. This is done offline: nothing about your target is sent to an outside service.

**Download the location file (once).** Positions come from an offline City database:

1. Download a City database in `.mmdb` form: **MaxMind GeoLite2 City** (free account and licence from maxmind.com; the licence asks you to keep it up to date and credit MaxMind) or **DB-IP Lite City** (free from db-ip.com, CC BY 4.0, credit DB-IP). Check the licence before use.
2. Install the extra: `pip install -e ".[geoip]"` (the Docker image already has it).
3. Point speedward at the file: `GEOIP_DB=/path/to/GeoLite2-City.mmdb` in `.env`. With Docker, mount it read-only and add to the `docker run` line above: `-v /srv/geo:/geo:ro -e GEOIP_DB=/geo/GeoLite2-City.mmdb`. The file stays on your server.

**CDN edge or origin.** A site behind a CDN is answered by the CDN's nearest edge, not by your own server, so each place can get a different square. When the addresses differ between places, a square's tooltip says "CDN edge, not necessarily your origin". Cloudflare names the edge in its `cf-ray` header (for example `-NRT` for Tokyo); speedward keeps only that airport code (never the headers) and places the edge from a bundled airport table (OurAirports, public domain; built by `scripts/build_iata.py`). That works even without the location file. Other CDNs' headers are not used, because their format is not certain. When every place reached the same address and the database places it, the map draws one **Server** square with a line from every place.

**Uncertain cases draw nothing.** A position is drawn only when it can be trusted. The table's **Position source** column says why there is none:

- "edge location uncertain (shared address)": the same address answered places on 2 or more continents (anycast or a shared CDN address, which databases register at one place). It is still drawn if every place's connect time is long enough for the database position (a single origin); a connect time faster than light could travel there rules it out.
- "edge location uncertain (geo-IP database and CDN header disagree)": the two are more than 2,500 km apart.
- "edge location uncertain (delay too short for the database position)": the connect time is faster than light could reach the database position.
- "position not available (...)": a private address, an address not in the database, no database set, or no answering address.

The thresholds are named constants in `speedward/core/edge.py`.

**Fallback: a position you set yourself.** If no place could be placed, the web results say "No server positions yet" with a setup link and **Place a server manually (your own claim)**. That opens the Server pin: drag it on the map, focus it and use the arrow keys (Shift moves further, Delete removes it), or pick a city in "Server is in:". The pin is two numbers kept in this browser tab only (same expiry as results, cleared by "Clear now") and sent with the next scan; it is used only when no answering server can be placed, and is recorded as "manual (pin)" in exports, because it is your claim and not a measured location. `SERVER_LAT` and `SERVER_LON` always win: they draw one Server square, recorded as "manual (setting)".

With no position at all, the map says "Server position unknown: set GEOIP_DB, drop the pin or set SERVER_LAT/SERVER_LON" and draws no lines. Everything else still works.

## How to read the map and the statuses

The map has one dot per test location, coloured by first-byte time against `FAST_MS` and `SLOW_MS`, and a square at the server that answered it where that can be placed (see above). Each row in the table has one status:

- **measured**: the test ran and the site answered. Only these rows have timings and count in averages and pass/fail limits.
- **blocked**: the site itself refused the request or timed out.
- **test failed**: a problem on the Globalping side (for example the probe errored). This says nothing about your site.
- **did not run**: the test never started.

Places that could not reach the site are drawn dimmed, with dashed lines, and show "no data" with a reason. Speedward never shows a made-up number for a failed test, and leaves such places out of averages and limits. Items Globalping does not return (for example the HTTP version on some probes) are shown as "not available".

The Cache column is a repeat request from the same test machine. It is **not a browser cache test**: it shows warm-connection and CDN effects, not what a returning visitor's browser would do.

## Test cost and the allowance

Every location costs tests: about 3 per place for the default scan (36 for 12 places), more with `--traceroute` and `--dns`. Speedward shows the estimate and your remaining hourly allowance before every scan, and refuses a scan that does not fit unless you confirm (`--yes` on the command line, a checkbox in the web UI). Beyond the hourly allowance, tests use Globalping credits; with none, they will not run. To stay under: use `--estimate`, pick fewer places with `--locations`, skip the extra checks, and wait for the hour to roll over.

## Privacy

Nothing about the sites you test is stored on the server: no database, no sessions, no result files. In the web UI, results live in the browser's session storage only: they are gone when the tab closes, and expire after `RESULT_TTL_SECONDS` (15 minutes by default). Export (HTML, JSON or Markdown) to keep them. Logs never contain targets or results. Map outlines are bundled, and no map tiles or geo-IP services are fetched, so nothing about your target leaks that way.

## Upgrading

```bash
git pull
. .venv/bin/activate
pip install -e .
```

For Docker, `git pull` then `docker build -t speedward .` and restart the container. Settings in `.env` carry over.

## Troubleshooting

- **"No Globalping key set"**: not an error. You get 250 tests/hour and 50 locations. Add `GLOBALPING_TOKEN` to `.env` in the folder you run from.
- **401 or an invalid-key message**: the key is wrong or revoked. Check `.env` for stray spaces or quotes and make a new key at <https://globalping.io>.
- **Rate limit / "Refused: ..."**: the scan costs more than the tests you have left. Run `--estimate`, use fewer places, wait, or pass `--yes` if you have credits.
- **Nothing on the map**: open the HTML report, not the Markdown. If every place is dimmed, no location was measured; read the reasons in the table. A target that refuses all Globalping probes shows as blocked.
- **Results gone after closing the tab**: by design. Export first.
- **No squares or no lines**: set `GEOIP_DB` (see "Download the location file"), or set `SERVER_LAT` and `SERVER_LON`, or place the Server pin before the scan. If the table says "edge location uncertain", the address is shared and its place cannot be trusted; that is deliberate.
- **The server will not start**: outside `DEBUG`, set `SECRET_KEY`. A `Bad Request (400)` page means the Host name is missing from `ALLOWED_HOSTS`.
- **`.env` ignored**: it is read from the folder you start the command in, and only the keys in `.env.example` are honoured.

## Not included yet

- The real-browser page test (page weight, request count, biggest files). `--browser` is accepted but prints "not available in this build yet" and does nothing; `--max-page-weight` therefore always fails as "not measured". There is no `ENABLE_BROWSER_TEST` setting.
- Repeat runs for a consistency check.
- Comparison with an earlier run.
- Following redirect chains beyond the first response (Globalping does not follow redirects; only the first response's `Location` is shown).

## Development

```bash
pip install -e ".[dev]"
.venv/bin/pytest -q
```

End-to-end browser tests: `pip install -e ".[e2e]" && playwright install chromium && .venv/bin/pytest e2e`.

`scripts/smoke_live.py URL` runs one real scan of a site you own (uses real tests).

## Credits and licence

- World map outlines come from [Natural Earth](https://www.naturalearthdata.com/) (public domain).
- Tests run on [Globalping](https://globalping.io) by jsDelivr, a service that is free up to a limit.
- Sibling project: siteward.
- MIT licence, see [LICENSE](LICENSE). Security reports: see [SECURITY.md](SECURITY.md).
