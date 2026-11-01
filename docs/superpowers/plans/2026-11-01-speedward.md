# speedward Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A self-hostable CLI and Django web tool that tests a site's performance from locations around the world through Globalping and shows it on a world map, never showing a fake result when a test fails.

**Architecture:** A `speedward` package modelled on siteward: plain data types in `core/`, a Globalping client plus a scan runner that turns API responses into per-location results, pure analysis and report modules, a Typer CLI, and a Django web app. The server holds only in-flight scan state in memory; finished results go to the browser's session storage and are never kept server-side.

**Tech Stack:** Python 3.11+, httpx, Typer, Django 5, Jinja2, whitenoise, gunicorn, pytest + pytest-django; optional extras: `geoip` (maxminddb) and `browser` (playwright).

**Spec:** `docs/superpowers/specs/2026-11-01-speedward-design.md`. Reference implementation for conventions: `/home/sharif/Documents/vs_code/work/devbridge/web_test` (siteward). Read its `core/`, `cli/main.py`, `reports/`, `web/store.py` and `tests/fake_server.py` before Task 2.

## Global Constraints

- Python `>=3.11`; package name `speedward`, console script `speedward`, Django settings module `speedward.web.settings`.
- Never show a number for a failed test: a result is exactly one of status `measured`, `blocked`, `test_failed`, `not_run`; only `measured` carries timings.
- `blocked` means the site refused or timed out (timeout, DNS failure, certificate error, HTTP 403 or 451). Globalping-side errors are `test_failed`; no probe available is `not_run`.
- Failed (non-`measured`) locations are excluded from averages and pass/fail limits, and every summary states how many were excluded.
- Nothing about scanned sites is stored server-side. No database. Logs never contain targets or results.
- `GLOBALPING_TOKEN` is read from the environment/`.env` only. No web input for it; it never appears in the browser, exports, logs or error messages.
- Wording: the key is "free up to a limit". Limits: no account 250 tests/hour and 50 probes per measurement; free key 500 tests/hour and 500 probes per measurement; above that, credits at one credit per test. Never write just "free". Link `https://globalping.io`.
- Estimated test cost is shown before every scan; a scan that will not fit the remaining allowance is refused unless explicitly confirmed (`--yes` on the CLI, a confirm button on the web).
- Saved web results: browser session storage only, default expiry 900 seconds (setting `RESULT_TTL_SECONDS`).
- Defaults: about 10 to 12 locations, at least one per continent; scan = cold http + warm http + ping; traceroute/MTR and DNS comparison are opt-in; up to 5 redirects followed.
- Exit codes: 0 pass, 1 a limit failed, 2 usage error or could not test (no location measured successfully).
- No third-party calls that leak the target (map tiles, geo-IP services). Map data is bundled.
- Display text containing target-controlled strings goes through a `clean()` like siteward's (strip control characters).

## Review Focus

- A site that answers 200 from some probes and times out from others: mixed results must not average the failures in. (Task 5)
- Every probe fails, or Globalping returns an error for the whole measurement: show "could not test", exit code 2, never a pass. (Tasks 5, 8)
- Rate limit hit mid-scan (HTTP 429): remaining locations become `not_run` with the reason "Globalping limit reached", completed ones are kept. (Task 3)
- URL with a path, query, port, IDN host, or a trailing dot: it must be normalised once and the same form used for every measurement. (Task 4)
- Target-controlled header values (for example a hostile `Server` header or redirect URL) containing control characters or HTML: must not reach the terminal or the page unescaped. (Tasks 5, 7, 9)

---

### Task 1: Scaffold, and Globalping API verification spike

**Files:**
- Create: `pyproject.toml`, `speedward/__init__.py` (`__version__ = "0.1.0"`), `tests/__init__.py`, `tests/conftest.py`, `scripts/globalping_spike.py`, `docs/globalping-api-notes.md`
- Modify: `.gitignore` (already ignores `.env`, `.venv/`, `.superpowers/`)

**Interfaces:**
- Produces: installable package; `docs/globalping-api-notes.md` recording the real API shape, which every later task treats as the source of truth for field names.

- [ ] **Step 1: Write `pyproject.toml`** mirroring siteward's (setuptools, `speedward*` package discovery, package-data for `reports/*.j2`, `web/templates/**/*`, `web/static/**/*`), dependencies listed in the Tech Stack, extras `geoip`, `browser`, `dev`, `e2e`, and `[project.scripts] speedward = "speedward.cli.main:app"`.
- [ ] **Step 2: Create a venv and install** with `python -m venv .venv && .venv/bin/pip install -e '.[dev]'`; run `.venv/bin/pytest` and expect "no tests ran".
- [ ] **Step 3: Write `scripts/globalping_spike.py`** that POSTs real measurements to `https://api.globalping.io/v1/measurements` (types: http GET, http with repeat via `from` a previous measurement id, ping, traceroute, mtr, dns; locations: `Berlin`, `Tokyo`, `aws-eu-west-1`, `eyeball-network+Brazil`) against a site the operator owns, polls `GET /v1/measurements/{id}` until finished, and dumps raw JSON to `/tmp`-style scratch files. It reads `GLOBALPING_TOKEN` from the environment and also issues one call without it.
- [ ] **Step 4: Run the spike** (needs network) and write `docs/globalping-api-notes.md` answering exactly: which http timing fields exist (dns, tcp, tls, firstByte, download, total) and their units; whether status code, response headers, resolved IP, HTTP version and TLS version are returned; whether following redirects is an option and how the chain appears; how probe reuse works; how probes report timeout, DNS failure and certificate errors; the exact response body and headers for a rate-limit hit (429) and for an unregistered over-limit call; how remaining allowance is read (response headers or a limits endpoint); how to filter by `datacenter-network`/`eyeball-network` and cloud regions; probe location fields (country, city, latitude, longitude, ASN, network). Mark any item the API does not provide as "not available", which later tasks must treat as such.
- [ ] **Step 5: Commit** `git add -A && git commit -m "chore: scaffold speedward and record Globalping API shape"`.

---

### Task 2: Core data types

**Files:**
- Create: `speedward/core/__init__.py`, `speedward/core/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces (all in `speedward.core.models`):
  - `clean(text: str) -> str` (same behaviour as siteward's)
  - `STATUSES = ("measured", "blocked", "test_failed", "not_run")`
  - `@dataclass Stages(dns: float|None, connect: float|None, tls: float|None, first_byte: float|None, download: float|None, total: float|None)` (milliseconds; a field the API does not return is `None`)
  - `@dataclass LocationResult(location: Location, status: str, reason: str = "", stages: Stages|None = None, repeat_stages: Stages|None = None, ping_ms: float|None = None, http_status: int|None = None, cdn: str|None = None  # "HIT"|"MISS"|None, server_ip: str|None = None, tls_version: str|None = None, http_version: str|None = None, redirects: list[str] = ..., repeat_same_probe: bool = False)`; `__post_init__` raises `ValueError` if status is not `measured` and `stages`, `ping_ms` or `repeat_stages` is set, and cleans `reason`.
  - `@dataclass Location(country: str, city: str, lat: float|None, lon: float|None, asn: int|None, network: str|None, network_type: str|None  # "isp"|"datacenter"|None)`
  - `@dataclass PathResult(location: Location, hops: list[Hop], status: str, reason: str)` and `@dataclass Hop(n: int, host: str, asn: int|None, rtt_ms: float|None, loss_pct: float|None)`
  - `@dataclass DnsResult(location: Location, status: str, reason: str, answers: list[str], lookup_ms: float|None)`
  - `@dataclass ScanReport(target: str, started: datetime, finished: datetime, version: str, options: dict, server: ServerPosition|None, locations: list[LocationResult], paths: list[PathResult], dns: list[DnsResult], browser: BrowserResult|None)`; `ServerPosition(lat, lon, source)`; `BrowserResult(page_weight_bytes, requests, load_ms, biggest: list[tuple[str,int]])`.
  - `SCHEMA_VERSION = 1` used by JSON export and the browser copy.

- [ ] **Step 1: Write failing tests** in `tests/test_models.py`: `test_non_measured_result_cannot_carry_timings` (a `blocked` `LocationResult` with `stages=Stages(...)` raises `ValueError`); `test_reason_is_cleaned` (a reason containing `\x1b[31m` has the escape replaced); `test_unknown_status_rejected`.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_models.py -v`; expect FAIL (module missing).
- [ ] **Step 3: Implement** the types above in `speedward/core/models.py`.
- [ ] **Step 4: Run** the same command; expect PASS.
- [ ] **Step 5: Commit** `git commit -am "feat: core data types"`.

---

### Task 3: Globalping client, error taxonomy and a fake server

**Files:**
- Create: `speedward/core/globalping.py`, `tests/fake_globalping.py`
- Test: `tests/test_globalping.py`

**Interfaces:**
- Consumes: `docs/globalping-api-notes.md` (Task 1) for field names and the rate-limit response.
- Produces:
  - `class GlobalpingError(Exception)`, `class RateLimited(GlobalpingError)` with `.remaining: int|None`.
  - `class Allowance` with `tests_per_hour: int`, `max_probes: int`, `remaining: int|None`, `authenticated: bool`.
  - `class GlobalpingClient(token: str|None = None, base_url: str = "https://api.globalping.io/v1", timeout: float = 30, http: httpx.Client|None = None)` with `create(type: str, target: str, locations: list[dict], options: dict) -> str` (measurement id), `wait(id: str) -> dict` (finished measurement JSON), and `allowance() -> Allowance`. The token is sent as a bearer header and never appears in exceptions or `repr`.
  - `classify(raw_probe_result: dict) -> tuple[str, str]` returning `(status, reason)`: timeout, DNS failure, certificate error, HTTP 403 and 451 give `("blocked", <reason>)`; probe offline or API error gives `("test_failed", <reason>)`; no probe for the requested location gives `("not_run", <reason>)`.
- `tests/fake_globalping.py` provides a `FakeGlobalping` local HTTP server (like siteward's `FakeServer`) that serves canned measurement JSON copied from the spike's recorded responses, supports forcing 429, per-probe failures, and records requests so tests can assert the token header.

- [ ] **Step 1: Write failing tests**: `test_token_sent_as_bearer_and_never_in_repr`; `test_classify_timeout_is_blocked`; `test_classify_403_is_blocked`; `test_classify_api_error_is_test_failed_not_blocked`; `test_429_raises_rate_limited_with_remaining`; `test_allowance_without_token_is_250_and_50`; `test_allowance_with_token_is_500_and_500`.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_globalping.py -v`; expect FAIL.
- [ ] **Step 3: Implement** the client with httpx; polling interval 0.5 s with a 60 s overall wait; map HTTP errors into the exceptions above.
- [ ] **Step 4: Run**; expect PASS.
- [ ] **Step 5: Commit** `git commit -am "feat: Globalping client, classification, fake server"`.

---

### Task 4: Targets, default locations, budget and estimate

**Files:**
- Create: `speedward/core/target.py`, `speedward/core/locations.py`, `speedward/core/budget.py`
- Test: `tests/test_target.py`, `tests/test_budget.py`

**Interfaces:**
- Produces:
  - `normalise_target(url: str) -> str` in `target.py`: adds `https://` when missing, lowercases and IDNA-encodes the host, strips a trailing dot, drops the fragment, rejects non-http(s) schemes with `ValueError`.
  - `DEFAULT_LOCATIONS: list[dict]` in `locations.py` (Globalping location specs, about 10 to 12, at least one per continent: for example London, Frankfurt, New York, Los Angeles, Sao Paulo, Johannesburg, Lagos, Mumbai, Singapore, Tokyo, Sydney, Dubai) and `parse_locations(spec: str|None) -> list[dict]` for comma-separated overrides, including `+` filters.
  - `@dataclass Plan(locations: list[dict], traceroute: bool, dns: bool, browser: bool)`.
  - `estimate_cost(plan: Plan) -> int` in `budget.py`: locations x (cold http + warm http + ping) plus locations when `traceroute` plus locations when `dns`, counting one test per location per measurement.
  - `check_budget(plan: Plan, allowance: Allowance) -> BudgetVerdict` with `.cost`, `.fits: bool`, `.reason: str` (over the probe cap or over the remaining tests).

- [ ] **Step 1: Write failing tests**: `test_normalise_adds_https`; `test_normalise_idn_and_trailing_dot_give_same_string_as_ascii_form`; `test_normalise_rejects_ftp`; `test_default_locations_cover_six_continents`; `test_estimate_default_plan_is_locations_times_3`; `test_traceroute_adds_one_per_location`; `test_plan_over_probe_cap_does_not_fit`; `test_plan_over_remaining_does_not_fit`.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_target.py tests/test_budget.py -v`; expect FAIL.
- [ ] **Step 3: Implement** the three modules.
- [ ] **Step 4: Run**; expect PASS.
- [ ] **Step 5: Commit** `git commit -am "feat: target normalisation, default locations, test budget"`.

---

### Task 5: Scan runner and analysis

**Files:**
- Create: `speedward/core/runner.py`, `speedward/core/analysis.py`
- Test: `tests/test_runner.py`, `tests/test_analysis.py`

**Interfaces:**
- Consumes: `GlobalpingClient`, `classify`, `Plan`, `ScanReport` and related types.
- Produces:
  - `run_scan(url: str, plan: Plan, client: GlobalpingClient, on_progress: Callable[[str], None] | None = None, geo: Geolocator | None = None) -> ScanReport`: cold http measurement, then a warm http measurement reusing the cold measurement's probes (sets `repeat_same_probe=True`; if reuse is unavailable the warm run uses fresh probes with `repeat_same_probe=False`), then ping, then optional traceroute/mtr and dns. Follows up to 5 redirects and records the chain. A `RateLimited` mid-scan converts every not-yet-run location into `not_run` with reason `"Globalping limit reached"` and keeps completed results. Raises `ScanError` only when the initial measurement cannot be created at all.
  - `cdn_status(headers: dict[str, str]) -> str|None`: `"HIT"` or `"MISS"` from `cf-cache-status`, `x-cache`, or `age > 0`; `None` if there is no signal.
  - `summarise(report: ScanReport) -> Summary` with `measured: int`, `excluded: int`, `avg_first_byte: float|None`, `median_first_byte`, `worst: LocationResult|None`, `cache_gain_pct: float|None` (`None` when no measured location has `repeat_same_probe=True`), `blocked: int`, `slowest_stage: str|None`.
  - `evaluate_limits(report: ScanReport, limits: Limits) -> LimitOutcome` with `Limits(max_ttfb: float|None, max_load: float|None, max_blocked: int|None, max_page_weight: int|None)`; each limit is checked against every `measured` location and one over fails; returns `.failed: list[str]` and `.exit_code` (0, 1, or 2 when `measured == 0`).

- [ ] **Step 1: Write failing tests** with `FakeGlobalping`: `test_failed_locations_excluded_from_average` (two measured at 100 and 300 ms, one blocked: average is 200 and excluded is 1); `test_all_failed_gives_exit_code_2`; `test_one_location_over_max_ttfb_fails`; `test_rate_limit_midscan_keeps_completed_and_marks_rest_not_run`; `test_warm_run_reuses_probes_flag`; `test_cache_gain_none_when_probes_not_reused`; `test_cdn_status_cf_hit`; `test_cdn_status_none_without_signal`; `test_hostile_header_value_is_cleaned` (a `Server` header containing `\x1b]0;x\x07` is stripped by `clean`).
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_runner.py tests/test_analysis.py -v`; expect FAIL.
- [ ] **Step 3: Implement** runner and analysis. Run the three measurement types in parallel threads where the API allows; keep result order stable by location.
- [ ] **Step 4: Run**; expect PASS.
- [ ] **Step 5: Commit** `git commit -am "feat: scan runner and analysis"`.

---

### Task 6: Server position

**Files:**
- Create: `speedward/core/geo.py`
- Test: `tests/test_geo.py`

**Interfaces:**
- Produces: `class Geolocator(db_path: str|None, override: tuple[float,float]|None)` with `locate(ip: str|None) -> ServerPosition|None`. The override wins (`source="manual"`); else the optional offline database via the `geoip` extra (`source="database"`); else `None`. A missing database file or missing `maxminddb` import returns `None` without error. Settings `GEOIP_DB`, `SERVER_LAT`, `SERVER_LON` feed it.

- [ ] **Step 1: Write failing tests**: `test_override_wins`; `test_no_database_returns_none`; `test_private_ip_returns_none`.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_geo.py -v`; expect FAIL.
- [ ] **Step 3: Implement** `Geolocator`; no network calls anywhere in this module.
- [ ] **Step 4: Run**; expect PASS.
- [ ] **Step 5: Commit** `git commit -am "feat: server position lookup"`.

---

### Task 7: Reports and the map

**Files:**
- Create: `speedward/reports/__init__.py` (`FORMATS = ("json", "md", "html")`, `render(report, fmt) -> str`), `json_report.py`, `markdown_report.py`, `html_report.py`, `report.html.j2`, `speedward/reports/map.py`, `speedward/reports/worldmap.svg.json` (simplified public-domain Natural Earth outlines, bundled)
- Test: `tests/test_reports.py`, `tests/test_map.py`

**Interfaces:**
- Produces:
  - `render_map(report: ScanReport, width: int = 1000) -> str` in `map.py`: an inline SVG string with equirectangular projection `x=(lon+180)/360*W`, `y=(90-lat)/180*H`; a line (quadratic arc) from the server to each location; colour by first-byte thresholds (`FAST_MS=300`, `SLOW_MS=800`, configurable settings) green/amber/red for `measured`; `blocked`, `test_failed` and `not_run` locations drawn grey and dashed at reduced opacity with a label containing the reason; when `report.server is None` the server marker is placed in a labelled "position unknown" corner. All text goes through `html.escape`.
  - Report content as in the spec: summary tiles, the table (location, no cache, cache, delay, CDN, status/reason), stage breakdown bars, traceroute hops with loss, DNS comparison (flag differing answers), server identity, browser panel, and the line stating how many locations were excluded.
  - JSON export includes `schema_version` and contains no token. The HTML report is self-contained (inline CSS, SVG, no external requests).

- [ ] **Step 1: Write failing tests**: `test_blocked_location_is_dimmed_dashed_with_reason` (SVG contains `stroke-dasharray` for the blocked line and the reason text); `test_failed_location_has_no_timing_text` (no "ms" in that row); `test_map_escapes_hostile_reason` (`<script>` in a reason is escaped); `test_unknown_server_position_is_labelled`; `test_json_has_schema_version_and_no_token`; `test_html_report_has_no_external_urls` (no `http://` or `https://` other than the globalping.io link); `test_excluded_count_is_stated`.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_reports.py tests/test_map.py -v`; expect FAIL.
- [ ] **Step 3: Implement** reports and map.
- [ ] **Step 4: Run**; expect PASS.
- [ ] **Step 5: Commit** `git commit -am "feat: reports and world map"`.

---

### Task 8: CLI

**Files:**
- Create: `speedward/cli/__init__.py`, `speedward/cli/main.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: Tasks 3 to 7.
- Produces `speedward scan URL` with options `--locations`, `--traceroute`, `--dns`, `--browser`, `--format json|md|html`, `--out PATH`, `--max-ttfb MS`, `--max-load MS`, `--max-blocked N`, `--max-page-weight BYTES`, `--yes` (accept a cost over the remaining allowance), `--estimate` (print the cost and exit 0), `--allow-private`. It prints a notice to stderr ("Only test sites you own or are authorised to test."), prints the estimate and allowance before starting, writes the report to stdout or `--out`, and exits per Global Constraints. Reads `GLOBALPING_TOKEN` from the environment (and `.env` if present via a tiny loader).

- [ ] **Step 1: Write failing tests** with `typer.testing.CliRunner` and `FakeGlobalping`: `test_estimate_prints_cost_and_makes_no_measurements`; `test_scan_refused_when_over_allowance_without_yes` (exit 2, message names remaining tests); `test_failed_limit_exits_1`; `test_all_locations_failed_exits_2`; `test_token_never_in_output`; `test_bad_format_exits_2`.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_cli.py -v`; expect FAIL.
- [ ] **Step 3: Implement** the Typer app following siteward's `cli/main.py` structure.
- [ ] **Step 4: Run**; expect PASS.
- [ ] **Step 5: Commit** `git commit -am "feat: command line interface"`.

---

### Task 9: Web UI

**Files:**
- Create: `speedward/web/{__init__,settings,urls,views,state,wsgi}.py`, `speedward/web/templates/{base,index,_results}.html`, `speedward/web/static/js/results.js`, `speedward/web/static/css/app.css`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `run_scan`, `estimate_cost`, `check_budget`, `render`, `summarise`.
- Produces:
  - Routes: `GET /` (form, status line, banner area); `POST /estimate` (JSON: cost, fits, allowance); `POST /scan` (starts a background scan, returns `{id}`; requires `confirm=1` when the estimate does not fit); `GET /scan/<id>/status` (JSON progress and, when done, the result JSON); `GET /export/<fmt>` is NOT a server route: exports are built from the browser's saved copy by `POST /export/<fmt>` receiving the result JSON and returning the rendered file, without keeping it.
  - `state.py`: `ScanState` in-memory dict keyed by random id holding only the in-flight scan (status, progress lines, the finished report until first successful fetch), deleted on first fetch of the finished result or after 120 seconds; never written to disk or logged.
  - Status line: from `Allowance` shows "No Globalping key set: 250 tests/hour, 50 locations. A key is free up to a limit: https://globalping.io" or "Globalping key active: 500 tests/hour, up to 500 locations. Remaining this hour: N".
  - `results.js`: saves the result JSON in `sessionStorage` under `speedward:result` with a saved-at timestamp; on load, if present and younger than `RESULT_TTL_SECONDS` renders the banner "Showing results for X from N minutes ago. Kept until you close this tab. No tests were used." with Run again (shows cost), Clear now; after expiry removes it and shows the expiry message; if `sessionStorage` throws, shows a notice that results cannot be kept in this window; registers a `beforeunload` handler only while unexported results exist; export buttons (HTML, JSON, Markdown) post the saved JSON to `/export/<fmt>`.
  - Map dots are clickable and highlight the table row; blocked dots show the reason on focus and hover; all dynamic text set via `textContent`.

- [ ] **Step 1: Write failing tests** (pytest-django client + `FakeGlobalping`): `test_index_shows_key_free_up_to_a_limit_wording`; `test_status_line_without_token_shows_250_and_50`; `test_no_token_input_anywhere` (no form field named token; the token value never appears in any response body when set); `test_estimate_endpoint_returns_cost`; `test_scan_requires_confirm_when_over_allowance`; `test_finished_result_removed_from_server_after_first_fetch`; `test_export_does_not_keep_posted_result`; `test_hostile_reason_is_escaped_in_page`; `test_logs_contain_no_target` (capture logging during a scan).
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_web.py -v`; expect FAIL.
- [ ] **Step 3: Implement** Django settings, views, state and templates following siteward's `web/settings.py` and `web/views.py`; JS is plain, no build step, no external scripts.
- [ ] **Step 4: Run**; expect PASS.
- [ ] **Step 5: Write browser-level e2e** `e2e/test_session_storage.py` with Playwright: run a scan against the fake service, reload and see the saved banner with no new requests to `/scan`, close the page and reopen to see the empty start page, and check a private-context storage failure shows the notice. Run `.venv/bin/pytest e2e -v` (after `playwright install chromium`); expect PASS.
- [ ] **Step 6: Commit** `git commit -am "feat: web UI with session-storage results and export"`.

---

### Task 10: Optional real-browser page test

**Files:**
- Create: `speedward/core/browser.py`
- Modify: `speedward/cli/main.py` (the `--browser` option), `speedward/web/views.py` (the checkbox)
- Test: `tests/test_browser.py`

**Interfaces:**
- Produces: `run_browser_test(url: str) -> BrowserResult`, raising `BrowserUnavailable` with the message "Install the browser extra: pip install 'speedward[browser]' && playwright install chromium" when Playwright is missing. Records total transferred bytes, request count, load event time in ms and the 5 largest files, from the host machine.

- [ ] **Step 1: Write failing tests**: `test_missing_playwright_raises_browser_unavailable` (monkeypatch the import); `test_browser_panel_absent_when_not_requested`; `test_browser_result_against_fake_server` (marked to skip when Playwright is not installed; asserts request count equals the fake page's resources).
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_browser.py -v`; expect FAIL.
- [ ] **Step 3: Implement** `run_browser_test` and wire `--browser` and the web checkbox; an unavailable browser reports "not available" in its own panel and never aborts the map.
- [ ] **Step 4: Run**; expect PASS (or the browser test skipped).
- [ ] **Step 5: Commit** `git commit -am "feat: optional real-browser page test"`.

---

### Task 11: Packaging, docs and a live smoke check

**Files:**
- Create: `Dockerfile`, `.dockerignore`, `.env.example`, `README.md`, `scripts/smoke_live.py`, `LICENSE` (copy siteward's), `SECURITY.md`
- Modify: `CLAUDE.md` (add real build, test and run commands, and a short architecture section)

**Interfaces:**
- Produces: `.env.example` with `GLOBALPING_TOKEN=`, `RESULT_TTL_SECONDS=900`, `FAST_MS=300`, `SLOW_MS=800`, `GEOIP_DB=`, `SERVER_LAT=`, `SERVER_LON=`, `SECRET_KEY`, `ALLOWED_HOSTS`. README sections: the authorisation notice; quick start; how to get a Globalping key (free up to a limit, credits above it, link to `https://globalping.io`); the cache-test limitation (a repeat request is not a browser cache); privacy (nothing stored server-side, session storage only, export to keep results).

- [ ] **Step 1: Write the Dockerfile** modelled on siteward's (gunicorn on `speedward.web.wsgi`); build with `docker build -t speedward .` and expect success.
- [ ] **Step 2: Write `README.md`, `.env.example`, `SECURITY.md`, `scripts/smoke_live.py`.** The README must explain self-hosting end to end for someone who has never seen the project (author requirement: "remember it's self hosted, so readme.md should also explain that process well and setup"): what you need (Python 3.11+, or Docker), install steps, getting a Globalping key (free up to a limit, credits above it; what each limit means for scans), where to put it (`.env`, never in the web page), a first CLI scan, running the web UI locally, running it with Docker, running it behind your own reverse proxy/auth (the web app has no login), every `.env` setting with its default, the optional extras (`geoip` offline database and how to obtain one, `browser` with `playwright install chromium`), how to read the map and the "no data"/blocked/test-failed statuses, test-cost estimates and how to stay under the hourly allowance, upgrading, and troubleshooting (no key, 401, rate limit, nothing on the map, session results gone after closing the tab). (one real scan against a site the operator owns, asserting a non-empty map and that no non-measured row shows a timing).
- [ ] **Step 3: Update `CLAUDE.md`** with the commands now in use: `.venv/bin/pytest`, a single test (`.venv/bin/pytest tests/test_runner.py::test_name -v`), running the web app (`DEBUG=1 django-admin runserver --settings=speedward.web.settings`), and the layer boundaries (`core` has no Django imports).
- [ ] **Step 4: Run the whole suite** `.venv/bin/pytest -q`; expect all PASS. Run `scripts/smoke_live.py` with and without `GLOBALPING_TOKEN` and record the outcome in the commit message.
- [ ] **Step 5: Commit** `git commit -am "docs: README, packaging, smoke check"`.

---

### Task 12: Allowance timer (added mid-build at the author's request; option B of the timer mockup)

**Files:**
- Create: `speedward/core/allowance_view.py`, `tests/test_allowance_view.py`
- Modify: `speedward/core/globalping.py` (Allowance gains `reset_seconds: int | None`), `speedward/cli/main.py`, `speedward/web/views.py`, `speedward/web/templates/index.html`, `speedward/web/static/js/results.js`, `speedward/web/static/css/app.css`, `tests/test_cli.py`, `tests/test_web.py`, `e2e/test_session_storage.py` (or a new e2e file)

**Interfaces:**
- Produces: `Allowance.reset_seconds` read from `GET /v1/limits` (`rateLimit.measurements.create.reset`, seconds) and the `x-ratelimit-reset` POST header; `describe_allowance(allowance, cost: int | None, now_epoch: float) -> AllowanceView` in `allowance_view.py` with `.tests_left`, `.tests_per_hour`, `.reset_in_minutes: int | None` (rounded UP, never 0 while reset_seconds > 0), `.state` (`"ok"`, `"low"` when tests_left < cost or <= 15% of tests_per_hour, `"unknown"` when remaining is unknown), `.used_before`, `.this_scan`, `.left_after` (never negative; the three bar parts sum to tests_per_hour), `.text` (the strip text), `.keyless: bool`, and `.next_full_scan_in_minutes: int | None`.

Behaviour (author's decisions): web status strip "N of M tests left this hour · resets in X min · last run Y min ago"; amber when low, then says when a full scan will fit again; no-key variant shows the lower limits and "A key is free up to a limit: https://globalping.io"; progress bar beside the cost estimate with three parts (already used, this scan, left afterwards) and a caption such as "38 used already · 36 for this scan · 176 left afterwards"; the countdown ticks down client-side every 30 seconds from the server-supplied `reset_seconds` and `fetchedAt`, and the strip refreshes from `/estimate` after every scan; "last run Y min ago" comes ONLY from the saved result in session storage (`speedward:result` savedAt) so it vanishes with the tab; the server stores nothing; CLI prints "You have N left this hour (resets in X min)." next to the estimate (no last-run line). Never show a guess: if `reset_seconds` or `remaining` is unknown, show "unknown" for that part (and omit the bar) rather than a number.

- [ ] **Step 1: Write failing tests**: `test_reset_rounds_up_to_whole_minutes` (61 s -> 2 min, 1 s -> 1 min, 0 -> None/"now"); `test_unknown_reset_is_not_guessed`; `test_low_state_when_remaining_below_cost`; `test_bar_parts_sum_to_limit_and_never_negative`; `test_keyless_text_says_free_up_to_a_limit_and_never_bare_free`; `test_cli_estimate_prints_reset_countdown`; `test_web_estimate_returns_reset_and_bar_parts`; `test_index_status_strip_shows_countdown`; an e2e test that the strip is present, the countdown text decrements when the browser clock is advanced (Playwright `page.clock`), and "last run" appears after a scan and is gone after closing and reopening the tab.
- [ ] **Step 2: Run the new tests; expect FAIL.**
- [ ] **Step 3: Implement** `Allowance.reset_seconds` plus `describe_allowance`, then CLI, web view/template/JS/CSS. All dynamic text via `textContent`. No server-side storage of the last run.
- [ ] **Step 4: Run** the new tests plus the full suite; expect PASS.
- [ ] **Step 5: Commit** `git commit -am "feat: allowance timer and last-run strip"`.

---

## Self-Review notes

- Spec coverage: map and dimmed unreachable (Task 7); cold, warm, delay (Tasks 3 to 5); reasons (Tasks 3, 7); CDN (Task 5); pass/fail (Tasks 5, 8); browser test (Task 10); export (Tasks 7, 9); stage breakdown, path, network type, DNS, server identity (Tasks 4, 5, 7); key handling and wording (Tasks 3, 9, 11); budget (Tasks 4, 8, 9); session storage and expiry (Task 9); server position (Task 6); first-task verification (Task 1).
- Names used across tasks match the Interfaces blocks above (`LocationResult`, `Stages`, `Plan`, `Allowance`, `GlobalpingClient`, `classify`, `summarise`, `evaluate_limits`, `Geolocator`).
