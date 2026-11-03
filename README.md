<p align="left"><img src="docs/logo.svg" alt="speedward logo" width="72" height="72"></p>

# speedward

[![CI](https://github.com/sharifndlovu123/speedward/actions/workflows/ci.yml/badge.svg)](https://github.com/sharifndlovu123/speedward/actions/workflows/ci.yml)

Speedward tests how fast a website loads from places around the world and shows the result on a world map. It runs the tests through the [Globalping](https://globalping.io) network. You run it yourself, as a command-line tool or as a small web app. It is the sibling of [siteward](https://github.com/sharifndlovu123/siteward), the passive security scanner, and uses the same stack and conventions.

**Only test sites you own or are authorised to test.** Every scan sends real requests to the target from many machines.

See [demo.md](demo.md) for a short walkthrough built from a real run.

## What you need

- Python 3.11 or newer, or Docker with Compose (2.24 or newer if you use `docker-compose.secrets.yml`).
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

Optional extras: `pip install -e ".[geoip]"` (offline position of the server that answered each place, see below). The `browser` extra is reserved for the real-browser page test, which is not built yet; installing it changes nothing today.

## Get a Globalping key

The key is optional and free up to a limit. Create a free account at <https://globalping.io> and make a key in its dashboard.

| | No key | With a key (free up to a limit) |
| --- | --- | --- |
| Tests per hour | 250 | 500 |
| Locations per run | 50 | up to 500 |

Above the free allowance each test costs one Globalping credit. Credits are bought through GitHub Sponsors or earned by hosting a Globalping probe. See <https://globalping.io> for current terms; they are Globalping's, not this project's.

Put the key in a file named `.env` in the folder you run speedward from:

```
GLOBALPING_TOKEN=your-key-here
```

Start from `.env.example` (`cp .env.example .env`). The key stays on the machine running speedward. The web page has no input for it, and it never reaches the browser, exports or logs. Do not commit `.env` (it is in `.gitignore`).

Only an allowlist of keys is read from `.env` (everything in `.env.example` except the `GEOIPUPDATE_*` keys, which only `docker compose` reads, for the geoipupdate container). `GLOBALPING_BASE_URL`, which points at a different Globalping endpoint (used by the tests), is read from the real environment only, and speedward refuses to send a key to any endpoint that is not Globalping or this machine.

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
| `--max-ttfb MS`, `--max-load MS`, `--max-blocked N` | Pass/fail limits (up to 1000000000) |
| `--max-page-weight BYTES` | Not available yet: it needs the browser test, so it is refused with a message instead of always failing |
| `--browser` | Not available yet: prints a note and runs the scan without it |
| `--yes` | Go ahead even if the scan costs more than the tests left |
| `--estimate` | Print cost and allowance, then stop |
| `--verbose` | More detail when something goes wrong |

Measurements that could not run (for example the repeat test after the Globalping limit was reached) are listed on standard error as "Not run: ..." and in the report, with the column they leave empty.

Exit codes: `0` scan done and every limit passed (or none set), `1` a limit failed, `2` the scan could not run or no location could be tested. Messages go to standard error, the report to standard output (or `--out`).

## Run the web UI locally

```bash
DEBUG=1 django-admin runserver --settings=speedward.web.settings
```

Open <http://127.0.0.1:8000/>. Enter an address, review the estimate, press Start test. `DEBUG=1` is for your own machine only.

**Colour theme.** The Light / Dark / Auto switch in the top bar (under the name on wide screens, in the Menu on phones) picks the colour theme. Auto, the default, follows your device. Light or Dark is remembered in this browser only (local storage, never sent to the server, no cookie); if the browser blocks storage the switch still works until you reload. Exported HTML reports have no switch and follow the device of whoever opens them.

**On a phone** (under 700 px wide) the map fills the screen and everything else sits in one bottom sheet with three heights. It starts low (peek): the address, the test cost and Start, or after a scan the headline number and finding. Show more (or tap or drag the bar at the top of the sheet) raises it to half (places, More checks, the summary) and then full (every setting; after a scan the details: the request path as a list of blocks, who can reach it, is it fast? and the place table). Show less lowers it; Close or Escape on the details goes back to half. Wide tables scroll sideways inside their own box while the place name stays in view. Export, Run again, Clear now, Locations and the colour theme are in the Menu in the top bar. The server pin has a larger tap area, and Move pin (under Server is in) places it with one tap on the map instead of a drag; the arrow keys still work.

## Run it with Docker Compose

`docker-compose.yml` runs two containers: **speedward** (built from the `Dockerfile`: gunicorn, one worker with threads, non-root, read-only root file system) and MaxMind's official **geoipupdate** image, which keeps the GeoLite2 City file current in a shared volume that speedward mounts read-only.

```bash
cp .env.example .env          # then fill in SECRET_KEY, GLOBALPING_TOKEN (optional) and the GEOIPUPDATE_ keys
docker compose up -d --build  # start, or rebuild after a git pull
docker compose ps             # speedward should turn "healthy"
docker compose logs -f speedward      # the app log (it never holds targets or results)
docker compose logs geoipupdate       # database downloads, or why one failed
docker compose down           # stop; the downloaded database volume is kept
```

Open <http://127.0.0.1:8000/>.

- **Settings come from `.env` by interpolation, one variable at a time.** Neither container gets `.env` wholesale: speedward receives only its own settings (the list in `docker-compose.yml`), and geoipupdate only `GEOIPUPDATE_ACCOUNT_ID`, `GEOIPUPDATE_LICENSE_KEY` and `GEOIPUPDATE_EDITION_IDS`. The MaxMind licence key therefore never reaches the speedward container, and the Globalping key never reaches geoipupdate. `.dockerignore` keeps `.env`, `secrets/`, `GeoIP.conf` and `*.mmdb` out of the image, so nothing secret is baked in.
- **`SECRET_KEY` is required** (the app refuses to start without it outside `DEBUG`): `python -c "import secrets; print(secrets.token_urlsafe(50))"`.
- **`GEOIP_DB` is fixed to `/geo/GeoLite2-City.mmdb` inside the container**; the `GEOIP_DB` in `.env` is a host path for runs without Docker and is not used here. Until geoipupdate has downloaded the file, speedward runs normally and draws no position lines.
- **Port**: published on `127.0.0.1:8000` only. To open it further, change the `ports` line (for example `"0.0.0.0:8000:8000"`) and read the next section first: the app has no login. Keep `127.0.0.1` in `ALLOWED_HOSTS`, because the health check uses it (it loads `/`, which asks Globalping for the allowance and costs no tests).
- **Updating**: `git pull && docker compose up -d --build`. geoipupdate checks for a new database every 84 hours (twice a week, `GEOIPUPDATE_FREQUENCY`). To move to a newer geoipupdate release, change its pinned image tag.
- **Optional: keep the MaxMind values in files instead of `.env`.** Put them in `secrets/maxmind_account_id` and `secrets/maxmind_license_key` (mode 600, value only, for example `printf '%s' 'VALUE' > secrets/maxmind_license_key`; MaxMind's container also trims a trailing newline), leave the two `GEOIPUPDATE_` keys in `.env` empty, and start with `docker compose -f docker-compose.yml -f docker-compose.secrets.yml up -d --build`. They are passed as Docker secrets through `GEOIPUPDATE_ACCOUNT_ID_FILE` / `GEOIPUPDATE_LICENSE_KEY_FILE`. Use one route, not both. `secrets/` is git-ignored except its README.

Without Compose, `docker build -t speedward .` and `docker run --rm -p 127.0.0.1:8000:8000 -e SECRET_KEY=... -e GLOBALPING_TOKEN=... speedward` still work (pass settings with `-e`; avoid `--env-file .env` if your `.env` holds the MaxMind key).

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
| `FAST_MS` | `300` | Map lines are green up to this first-byte time (web UI, HTML export and CLI HTML report) |
| `SLOW_MS` | `800` | Map lines are red from this first-byte time. Must be above `FAST_MS` |
| `GEOIP_DB` | none | Path to an offline City `.mmdb` database, used to place the server that answered each place (see below). Ignored by `docker-compose.yml`, which uses `/geo/GeoLite2-City.mmdb` |
| `SERVER_LAT`, `SERVER_LON` | none | Server position by hand. Wins over everything else for the one Server square |
| `RESULT_TTL_SECONDS` | `900` | Web UI: how long the browser keeps results. Minimum 60 |
| `MAX_CONCURRENT_SCANS` | `2` | Web UI: scans at once. Minimum 1 |
| `SECRET_KEY` | random per start | Web UI: required when `DEBUG` is off |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Web UI: accepted Host names. Not access control |
| `CSRF_TRUSTED_ORIGINS` | none | Web UI: full origins when served over HTTPS |
| `DEBUG` | off | Web UI: `1`, `true`, `yes` or `on` enable it. Local use only |

**Analysis thresholds.** The rules behind "Where the time goes" and "Is it fast?" (explained in those sections below). They apply to the web UI, the command line and every export. Each value is checked when the web UI or a scan starts: a value that is not a number, not finite or outside its bounds stops it with an error that names the setting and its bounds (never the value given). Every diagnosis and speed summary records the values it was judged by, so an export or a saved result keeps showing the result it was made with.

| Setting | Default | Bounds | Meaning |
| --- | --- | --- | --- |
| `BIGGEST_SHARE` | `0.40` | 0.01 to 1 | A stage is the biggest block at this share of the total or more |
| `POSSIBLE_SHARE` | `0.25` | 0.01 to 1, not above `BIGGEST_SHARE` | A possible block at this share or more |
| `MIN_PLACES` | `3` | whole number, 3 to 1000 | Places needed for any conclusion, in both panels. Never below 3 |
| `NEAR_CONNECT_MS` | `40` | 0 to 1000000000 | A place connecting faster than this is "close" (the "close places wait too" note) |
| `DISTANCE_CORRELATION` | `0.7` | 0 to 1 | Connect or TLS "grows with distance" at this correlation with distance or more |
| `DISTANCE_SPREAD_MS` | `50` | 0 to 1000000000 | ... and when the slowest place takes at least this much longer than the quickest |
| `DNS_SHARE` | `0.25` | 0.01 to 1 | "DNS is large" in a place at this share of its own total or more |
| `VARIES_RATIO` | `2` | 1 to 100 | Consistency: a place varies when its slowest run is at least this many times its median ... |
| `VARIES_MIN_MS` | `50` | 0 to 1000000000 | ... and at least this many ms above it |
| `BAD_HOP_LOSS_PCT` | `10` | 1 to 100 | Path: a bad hop is loss of at least this percentage that carries through to the site |
| `LONG_LINK_MS` | `50` | 1 to 1000000000 | Path: a long link is a rise of at least this many ms at one hop that stays up to the site |

Not a `.env` setting: `STATIC_ROOT` (real environment only; defaults to `./staticfiles`). `GEOIPUPDATE_ACCOUNT_ID`, `GEOIPUPDATE_LICENSE_KEY` and `GEOIPUPDATE_EDITION_IDS` sit in `.env` for `docker compose` only; speedward itself never reads them.

## Optional: lines to the server that answered each place

Each measured place records the IP address that answered it. Speedward puts that server on the map as a **square**, with a speed-coloured line from the place's circle to it. This is done offline: nothing about your target is sent to an outside service.

**Download the location file (once).** Positions come from an offline City database:

1. Download a City database in `.mmdb` form: **MaxMind GeoLite2 City** (free account and licence from maxmind.com; the licence asks you to keep it up to date and credit MaxMind) or **DB-IP Lite City** (free from db-ip.com, CC BY 4.0, credit DB-IP). Check the licence before use.
2. Install the extra: `pip install -e ".[geoip]"` (the Docker image already has it).
3. Point speedward at the file: `GEOIP_DB=/path/to/GeoLite2-City.mmdb` in `.env`. With Docker Compose this is done for you (see above). The file stays on your server.

**Keep it up to date automatically.** The GeoLite2 licence asks you to use a current copy (MaxMind publishes updates twice a week) and to credit MaxMind; DB-IP Lite is CC BY 4.0 and needs credit to DB-IP. Read the current terms on their sites before use; they are theirs, not this project's. The MaxMind licence key is a secret like `GLOBALPING_TOKEN`: keep it out of the repository and out of logs. In `.env` it is read only by `docker compose`, for the geoipupdate container; for runs without Docker it lives in `GeoIP.conf`.

- **(a) Locally, with `scripts/update_geoip.sh`.**
  1. Download `geoipupdate` from the [maxmind/geoipupdate releases](https://github.com/maxmind/geoipupdate/releases) (Linux `amd64` tarball), check it against the release's `checksums.txt` (`sha256sum -c --ignore-missing checksums.txt`), and unpack it into `~/geo/bin/` (the script looks on `PATH`, then in `~/geo/bin/geoipupdate_*/geoipupdate`).
  2. Create the config with your account ID and licence key: `mkdir -p ~/geo && cp docs/GeoIP.conf.example ~/geo/GeoIP.conf && chmod 600 ~/geo/GeoIP.conf`, then edit it. The script refuses to run while it is readable by other users.
  3. Run `scripts/update_geoip.sh` (MaxMind, the default) or `scripts/update_geoip.sh --source dbip` (DB-IP Lite City, no account: downloads this month's file, or last month's when this month's is not out yet, verifies it, then swaps it in as `~/geo/dbip-city-lite-current.mmdb`). Each run verifies the file opens and places a known address before it is used, prints only which file was updated with its size and date, and exits non-zero on any failure. `scripts/update_geoip.sh --check` reports the file's age and exits 1 when it is missing or older than 45 days. `GEOIP_DIR`, `GEOIP_CONF` and `GEOIP_PYTHON` change the defaults (see the top of the script).
  4. Set `GEOIP_DB=$HOME/geo/GeoLite2-City.mmdb` (written out as a full path) in `.env`, or `.../dbip-city-lite-current.mmdb` for DB-IP. On the author's machine the GeoLite2 file is at `~/geo/GeoLite2-City.mmdb` while `GEOIP_DB` still points at a DB-IP file; switching `GEOIP_DB` to the GeoLite2 path is recommended, since that is the file this script keeps current by default.
  5. Schedule it. A `systemd --user` timer, twice a week for MaxMind (for DB-IP use `--source dbip` and `OnCalendar=monthly`):

     `~/.config/systemd/user/speedward-geoip.service`
     ```ini
     [Unit]
     Description=Update the speedward location database

     [Service]
     Type=oneshot
     ExecStart=%h/path/to/speedward/scripts/update_geoip.sh
     ```

     `~/.config/systemd/user/speedward-geoip.timer`
     ```ini
     [Unit]
     Description=Update the speedward location database twice a week

     [Timer]
     OnCalendar=Tue,Fri 04:30
     RandomizedDelaySec=1h
     Persistent=true

     [Install]
     WantedBy=timers.target
     ```

     Then `systemctl --user daemon-reload && systemctl --user enable --now speedward-geoip.timer` (check with `systemctl --user list-timers` and `journalctl --user -u speedward-geoip`). Or a crontab line (`crontab -e`): `30 4 * * 2,5 $HOME/path/to/speedward/scripts/update_geoip.sh >> $HOME/geo/update.log 2>&1` (for DB-IP: `30 4 3 * * ... --source dbip`). No restart is needed: speedward opens the file for each scan.
- **(b) With Docker Compose**: the `geoipupdate` service in `docker-compose.yml` does it (see "Run it with Docker Compose").
- **(c) No account at all**: DB-IP Lite City through `scripts/update_geoip.sh --source dbip` once a month, as in (a).

**CDN edge or origin.** A site behind a CDN is answered by the CDN's nearest edge, not by your own server, so each place can get a different square. When the addresses differ between places, a square's tooltip says "CDN edge, not necessarily your origin". Cloudflare names the edge in its `cf-ray` header (for example `-NRT` for Tokyo); speedward keeps only that airport code (never the headers) and places the edge from a bundled airport table (OurAirports, public domain; built by `scripts/build_iata.py`). That works even without the location file. Other CDNs' headers are not used, because their format is not certain. When every measured place reached the same address (at least two places) and the database places it, the map draws one **Server** square with a line from every place.

**Uncertain cases draw nothing.** A position is drawn only when it can be trusted. The table's **Position source** column says why there is none:

- "edge location uncertain (shared address)": the same address answered places on 2 or more continents (anycast or a shared CDN address, which databases register at one place). It is still drawn if every place's connect time is long enough for the database position (a single origin); a connect time faster than light could travel there rules it out.
- "edge location uncertain (geo-IP database and CDN header disagree)": the two are more than 2,500 km apart.
- "edge location uncertain (delay too short for the database position)" or "(delay too short for the CDN header position)": the connect time is faster than light could reach that position.
- "position not available (...)": a private address, an answering address that could not be read, an address not in the database, no database set (or an unreadable one), or no answering address.

The thresholds are named constants in `speedward/core/edge.py`.

**Fallback: a position you set yourself.** If no place could be placed, the web results say "No server positions yet" with a setup link and **Place a server manually (your own claim)**. That opens the Server pin: drag it on the map, focus it and use the arrow keys (Shift moves further, Delete removes it), or pick a city in "Server is in:". The pin is two numbers kept in this browser tab only (same expiry as results, cleared by "Clear now") and sent with the next scan; it is used only when no answering server can be placed, and is recorded as "manual (pin)" in exports, because it is your claim and not a measured location. `SERVER_LAT` and `SERVER_LON` always win: they draw one Server square, recorded as "manual (setting)".

With no position at all, the map says "Server position unknown: set GEOIP_DB, drop the pin or set SERVER_LAT/SERVER_LON" in the web UI, and "Server position unknown: set GEOIP_DB or SERVER_LAT/SERVER_LON" in CLI reports and exported files (where no pin can be dropped), and draws no lines. Everything else still works.

## How to read the map and the statuses

The map has one dot per test location, coloured by first-byte time against `FAST_MS` and `SLOW_MS` (a place with no data is a hollow grey ring), and a square at the server that answered it where that can be placed (see above).

**Labels.** Each label goes in the first spot around its dot where it covers no other label, dot or square and is not cut off at the map edge. Places that sit close together share one label, joined to their dots by thin lines: "London 38ms · Paris 120ms" names two or three places, and "5 places" stands for more. When there is no room, a label is left out rather than drawn on top of another. Narrow screens (phones) use larger labels, so fewer fit; blocked places are labelled first, then measured ones, then the rest by name. Every place, labelled or not, keeps its dot, which you can hover or focus with the keyboard to read its result, and its row in the table. Reports with more than 200 places show dots only, with a note under the map. The label size follows the width of the browser window, not of the map itself, so a map embedded in a much narrower box than the window (for example an exported report pasted into another page) may show labels smaller than intended.

Each row in the table has one status:

- **measured**: the test ran and the site answered. Only these rows have timings and count in averages and pass/fail limits.
- **blocked**: the site itself refused the request or timed out.
- **test failed**: a problem on the Globalping side (for example the probe errored, or the test machine's own DNS resolver timed out or failed). This says nothing about your site.
- **did not run**: the test never started.

Places that could not reach the site are drawn dimmed, with dashed lines, and show "no data" with a reason. Speedward never shows a made-up number for a failed test, and leaves such places out of averages and limits. Items Globalping does not return (for example the HTTP version on some probes) are shown as "not available".

The Cache column is an immediate repeat request. It is **not a browser cache test**: it shows warm-connection and CDN effects, not what a returning visitor's browser would do. Speedward asks Globalping to repeat it on the same test machine; when that machine is no longer available (or a different one answered), the timing is still shown but labelled "repeat request (different probe)" or "repeat request (different probe, same city)", and that place is left out of the cache gain. The same labels mark a delay, route or DNS answer that came from a different machine than the first visit. When the repeat test could not run at all, the cache tile says why (for example "repeat test: Globalping limit reached").

The probe's network type (ISP or data centre) is recorded in JSON exports as `network_type` but is not shown in the web UI or the other reports yet.

## Where the time goes (the bottleneck)

After a scan, speedward names the stage of a request that costs the most time and what to look at first. It is worked out only from numbers it already measures, and only from **measured** places: blocked, failed and not-run places are counted in a footnote and left out of every figure, average and mark.

In the web UI the headline sits under the summary figures; "See the request path" opens the block diagram in the Details drawer. Select a place (a map dot, a table row or a name in "Each place") to redraw the diagram for that place alone; a blocked or failed place shows only its reason. The HTML and Markdown exports carry the same panel, the JSON export a `diagnosis` field, and the command line prints a short version after the report.

**The stages.** A request is drawn left to right: the visitor (the test place), DNS lookup, connect, TLS handshake, the edge or CDN (drawn dashed: we see whether it was a cache HIT or MISS and which edge answered, but not how long it took), server wait, download. Server wait is Globalping's `firstByte` timing as returned: it already starts after DNS, connect and TLS (in the recorded results the five stages add up to Globalping's total), so nothing is subtracted. A stage that Globalping did not return (TLS on plain HTTP, for example) is "not available", never zero. The summary's "Average first byte" and the `--max-ttfb` limit use the same Globalping `firstByte` field: it starts after the handshake, so it is smaller than a conventional time to first byte (which also counts DNS, connect and TLS), and the panel calls that same field "server wait". Averages are per stage over the measured places that returned it; the total is the sum of those averages, and each share is a stage's average over that total.

**The marks** (outline, pattern and a text label, never colour alone):

| Mark | Rule | Setting (default) |
| --- | --- | --- |
| biggest block | the largest share, and at least 40% of the total. Below that the headline says "no single stage dominates" | `BIGGEST_SHARE = 0.40` |
| possible block | any other stage with at least 25% of the total, or connect / TLS when they grow with distance (below) | `POSSIBLE_SHARE = 0.25` |
| fine | everything else that was measured | |
| (none) | fewer than 3 measured places: the timings are shown but nothing is marked, and it says "not enough places for a conclusion" | `MIN_PLACES = 3` |

**Server wait or distance or DNS.**

- *Server wait:* the card says close places wait too when server wait is at least 40% of the time in a place whose connect time is under 40 ms (`NEAR_CONNECT_MS = 40`).
- *Distance:* connect or TLS "grows with distance" when, across at least 3 places with a known server position, its time correlates with the distance to the server that answered (correlation of at least 0.7, `DISTANCE_CORRELATION`) and the slowest place takes at least 50 ms longer than the quickest (`DISTANCE_SPREAD_MS = 50`). The distance is from the test place to the server that answered it (see "lines to the server that answered" above), or to the one configured or pinned server. Without any position it only reports the plain spread of connect times.
- *DNS* is called out when the lookup is at least 25% of the time in at least one place (`DNS_SHARE = 0.25`).
- *Caching:* HIT and MISS counts come from the CDN headers. The repeat gain uses only places repeated on the same test machine, as the Cache gain figure does; otherwise it says why there is none.

The defaults live in `speedward/core/bottleneck.py`, and each can be changed with the setting of the same name (see "Settings" above for the bounds). Each saved diagnosis records the values it was made with, and a saved copy is only accepted if those values are within the same bounds (so never fewer than 3 places) and the diagnosis is exactly what they give for the report's own timings. A copy saved before `DNS_SHARE` existed is read with DNS judged by `POSSIBLE_SHARE`, as it was then. A report from before this feature loads without a panel.

**What it cannot tell you.**

- *Why* a stage is slow. Server wait covers everything the server does before the first byte (application code, database, a cache MISS at the CDN, a cold start); speedward cannot see inside it. Every sentence says "the timings indicate", and each "what to check" line is a prompt, not a diagnosis.
- How long the edge or CDN itself took: Globalping does not time it separately, so it is drawn but not measured.
- Anything about the browser: one HTML request per place, no images, scripts, rendering or the visitor's own network. A real visitor's page load can be dominated by things this never sees.
- A steady picture: it is one cold request per place (and one repeat). A single slow answer can move an average; run it again before acting on a close call.
- Distance without a server position: with no geo-IP database, pin or `SERVER_LAT`/`SERVER_LON`, it cannot say whether connect time grows with distance.
- Anything from blocked or failed places: they are left out, so a scan where most places failed describes only the few that answered.

## More checks: who can reach it

Under the address, "More checks" adds opt-in reachability checks. Nothing is ticked by default, so the
default scan stays at 3 tests per place. Every check runs from Globalping's probes, reusing the main
test's machines by measurement id (new machines in the same places only if those are gone); nothing runs
from this server, and no local traceroute, mtr, dig or ping is ever used.

| Check | Cost | What it does | Cell |
| --- | --- | --- | --- |
| Over IPv4 and IPv6 | +1 test per place | The same HTTPS request with `ipVersion: 6` | reached (time) or blocked (for example "no IPv6 address (AAAA record) for the name") |
| Over HTTP and HTTPS | +1 | A plain `http://` request (port 80); the redirect is read, not followed | "→ HTTPS", "redirects, not to HTTPS", "served over HTTP", or blocked |
| Through different DNS resolvers | +2 | A DNS lookup through 1.1.1.1 and 8.8.8.8 | resolves (time per resolver), or blocked when the name has no A record or is not found |
| Port reachable (TCP 443) | +1 | 3 TCP connection attempts to 443 (or the port in the address) | open (round trip), or closed when no attempt is answered |

The cost of each ticked check and the total are shown before every scan (web and `--checks` on the CLI,
for example `speedward scan URL --checks ipv6,http,dns,port --estimate`), and a scan over the allowance
still needs your confirmation.

The "Who can reach it" grid has a row per place and a column per check, beside the main test's own
HTTPS-over-IPv4 result. Each cell is a symbol and a word: ● reached, ○ blocked / closed (the site side
refused, timed out, has no address or no open port), ◌ no data (the Globalping side failed or the check
did not run; never counted). The finding counts only reached and blocked cells, for example "The timings
indicate: 2 of 4 places cannot reach the site over IPv6 (Tokyo, Seoul)", and ends with what to check.
With fewer than 3 places that have a result it says there are not enough places. On the map, each dot
shows the worst result for its place: a double ink ring means a check was blocked there; a dashed ring is no data.

What it cannot tell you: why a place is blocked (a firewall, a regional block and a broken route look the
same from outside); whether real visitors in that place see the same (one test machine per place); whether
a resolver answer of SERVFAIL or REFUSED is the site's fault (shown as "no data"); and whether HTTP/3 or
a specific edge is used. A resolving name does not count as reaching the site. The DNS check asks for an IPv4 address (A record): a name with none is shown as blocked, so an IPv6-only site shows blocked there. For an IP-address target the IPv6 and DNS checks are skipped (shown as not run, no tests used). "Cannot reach it at all" needs the main test itself blocked, or at least two site checks with a result and all of them blocked; each check needs results from at least 3 places before the finding counts it.

## More checks: is it fast?

The "Is it fast?" group in "More checks" adds opt-in speed checks that explain where the time goes. Like
the reachability checks they run only from Globalping's probes, on the main test's machines (reused by
measurement id), and nothing is ticked by default.

| Check | CLI name | Cost | What it does | Feeds the diagram's block |
| --- | --- | --- | --- | --- |
| Repeat 5 times (consistency) | `consistency` | +4 tests per place | 4 more runs of the main request; with the main test's own repeat that is 5 server waits per place, all from the same machine | Server wait: "steady over 5 runs" or "varies 140 to 900 ms" |
| HTTP/2 against HTTP/1.1 | `http2` | +1 | The main request with `protocol: HTTP2` (HTTPS addresses only; a plain `http://` address shows "did not run" and uses no tests) | (the panel only) |
| Compression (gzip, brotli) | `compression` | +1 | The main request sending `Accept-Encoding: br, gzip`; reads `Content-Encoding` | Download: "not compressed" or "compressed (br)" |
| DNS delegation trace | `dnstrace` | +1 | A `dns` lookup with `trace: true`: local resolver, root, TLD, the name's own servers | DNS: "slowest step: TLD" |
| Path loss and jitter (mtr) | `mtr` | +1 | An `mtr` path test with 5 packets per hop | Connect: "bad hop 7" or "no bad hop; +82 ms at hop 9 (long link)" |

The cost of each ticked check and the total are shown before every scan (for example
`speedward scan URL --checks consistency,mtr --estimate`), and the over-allowance confirmation applies.
A check the address cannot use (HTTP/2 on a plain `http://` address; IPv6, DNS resolvers and the DNS
trace on an IP address) is skipped and counted as 0 in the estimate. Ticking both "Network path" (or
`--traceroute`) and "Path loss and jitter" runs one mtr test per place (with the larger packet count, 5)
that fills both the routes and the path check; the estimate counts it once ("shares the Network path
test = 0").
TLS 1.2 seen in the main test adds "TLS 1.2 costs one more round trip" to the TLS block once any speed
check ran. The notes sit under each block's own text and never change its mark (biggest / possible block).

The rules, with their defaults in `speedward/core/speed.py`. Each can be changed with a setting (see
"Settings" above), and each speed summary records the values it used; a saved one is accepted only when
they are within the same bounds and the summary is exactly what they give for its own results (one saved
before summaries recorded them is judged by the defaults):

- Consistency: a place **varies** when its slowest run is at least `VARIES_RATIO` (2) times its median
  **and** at least `VARIES_MIN_MS` (50 ms) above it, otherwise it is **steady**. With fewer than 5 runs on
  the same machine (a run failed, was blocked or came from another machine) only the count is shown
  ("3 of 5 runs") and nothing is judged.
- HTTP/2: Globalping fails the request when the server does not offer HTTP/2 (there is no fallback); that
  is shown as "not offered", never as blocked. The time difference is against the main test's repeat on
  the same machine, and only where both ran.
- Compression: judged only on a 2xx answer with a body of a text type (HTML, CSS, JavaScript, JSON, XML,
  SVG). A redirect or error, an empty answer (204) or anything else (an image, no content type) shows
  "no data" with the reason, and never counts as "not compressed". Otherwise "not compressed" when no
  `Content-Encoding` came back. Globalping's own default request
  already asks for `gzip, deflate, br`, so the download difference is between two runs that could both be
  compressed: one run each, mostly noise.
- DNS trace: the slowest step is the one that took the longest (1 ms or more); ties go to the earlier step.
  The step labels are inferred, not reported: the first step is the probe's own resolver, a server named
  `*.root-servers.net` is a root server, the last step is the name's own (authoritative) servers, the first
  step after the root is the TLD and any further ones are "delegation".
- Path: a **bad hop** is the first hop from which loss of at least `BAD_HOP_LOSS_PCT` (10%) carries through every
  later hop to the site. Loss that stops at a middle hop is a router limiting its own replies and is not
  flagged. A **long link** is the largest rise of at least `LONG_LINK_MS` (50 ms) at one hop that stays up to the
  site; the timings indicate distance (for example an ocean crossing). Jitter is the site's own, the mean
  change between consecutive packets (Globalping's `jAvg` was not usable, see the API notes).

Every overall finding needs at least `MIN_PLACES` (3) places with a result of their own; with fewer it says there are
not enough places. Blocked runs are counted and named; failed or not-run ones are "no data". None of them
enter a figure or a finding. A failed DNS trace or path test is never counted against the site.

What it cannot tell you: **page size on the wire is not available** (Globalping's answer is chunked, has no
`Content-Length` and its body is cut at 10 KB), so it is never estimated; whether HTTP/3 would help (not
offered by Globalping); why a server wait varies (load, a cold cache and a slow page look the same from
outside); who runs a bad hop or whether real visitors take the same path (one machine per place, one run);
and whether a site that does not answer the path test drops ICMP or is unreachable (shown as "no end point",
never as blocked).

## Test cost and the allowance

Every location costs tests: about 3 per place for the default scan (36 for 12 places), more with `--traceroute`, `--dns` and `--checks`. Speedward shows the estimate and your remaining hourly allowance before every scan, and refuses a scan that does not fit unless you confirm (`--yes` on the command line, a checkbox in the web UI). Beyond the hourly allowance, tests use Globalping credits; with none, they will not run. To stay under: use `--estimate`, pick fewer places with `--locations`, skip the extra checks, and wait for the hour to roll over.

## Privacy

Nothing about the sites you test is stored on the server: no database, no sessions, no result files. In the web UI, results live in the browser's session storage only: they are gone when the tab closes, and expire after `RESULT_TTL_SECONDS` (15 minutes by default). Export (HTML, JSON or Markdown) to keep them. Logs never contain targets or results. Map outlines are bundled, and no map tiles or geo-IP services are fetched, so nothing about your target leaks that way.

## Upgrading

```bash
git pull
. .venv/bin/activate
pip install -e .
```

For Docker Compose, `git pull` then `docker compose up -d --build`. Settings in `.env` carry over.

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

- The real-browser page test (page weight, request count, biggest files). `--browser` is accepted but prints "not available in this build yet" and does nothing; `--max-page-weight` is refused with a message until the test exists. The `browser` extra in `pyproject.toml` is reserved for it. There is no `ENABLE_BROWSER_TEST` setting.
- Comparison with an earlier run.
- Following redirect chains beyond the first response (Globalping does not follow redirects; only the first response's `Location` is shown).

## Development

Common tasks live in the `justfile` ([just](https://github.com/casey/just), a small task runner; `pip install -e ".[dev]"` puts it in the venv). Run `just` to list them (or `.venv/bin/just`; `sudo apt install just` also works): `just check` (lint and unit tests), `just e2e`, `just serve`, `just estimate URL`, `just up` and `just down` for Docker, `just geoip`. They only wrap the commands below, so you can also run those directly.

```bash
pip install -e ".[dev]"
.venv/bin/pytest -q
.venv/bin/ruff check .
```

Lint is `ruff check` (rules and the reasons for any ignores are in `pyproject.toml`); the code is not auto-formatted, so do not run `ruff format`. CI (`.github/workflows/ci.yml`) runs lint, unit tests on Python 3.11 and 3.12, and the browser tests on every push to main and every pull request. It uses no secrets and an empty `GLOBALPING_TOKEN`, so it never calls the real API.

Check dependencies for known vulnerabilities (not part of CI): `pip install pip-audit && pip-audit`.

End-to-end browser tests: `pip install -e ".[e2e]" && playwright install chromium && .venv/bin/pytest e2e`.

`scripts/smoke_live.py URL` runs one real scan of a site you own (uses real tests).

`tests/test_update_geoip.py` exercises `scripts/update_geoip.sh` without network (fake `geoipupdate` and `curl`, a tiny hand-built database), and `tests/test_compose.py` checks with `docker compose config` (against a dummy env file, skipped without Docker) that the MaxMind key never reaches the speedward container.

## Credits and licence

- World map outlines come from [Natural Earth](https://www.naturalearthdata.com/) (public domain).
- Server positions, when you set up a location file: GeoLite2 data created by MaxMind, available from <https://www.maxmind.com>, or IP geolocation by DB-IP (<https://db-ip.com>, CC BY 4.0).
- Tests run on [Globalping](https://globalping.io) by jsDelivr, a service that is free up to a limit.
- Sibling project: siteward.
- MIT licence, see [LICENSE](LICENSE). Security reports: see [SECURITY.md](SECURITY.md).
