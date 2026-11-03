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

### Task 13: Map-first location picker (added at the author's request; mockups page-1 to page-4 approved)

**Intent:** the map is always on the page. Before a scan it is the location picker (replaces the free-text "Locations" input); during a scan the same map shows progress; after, results. Spec files: mockups in `.superpowers/brainstorm/122437-1793561144/content/` (`page-1-home.html` .. `page-4-return-mobile.html`, `map-always.html`).

**Files:** `speedward/core/locations.py` (add `CATALOG`), `speedward/reports/map.py` (add `render_picker_map`), `speedward/web/views.py`, `templates/index.html`, `static/js/results.js` (or new `static/js/picker.js`), `static/css/app.css`, tests in `tests/` and `e2e/`, README/demo.md/CLAUDE.md wording.

**Requirements:**
1. `CATALOG`: about 40 `Place(name, country, continent, lat, lon)` entries covering all 12 defaults plus extras on every continent (for example Paris, Stockholm, Madrid, Toronto, Chicago, Mexico City, Santiago, Cairo, Nairobi, Cape Town, Seoul, Hong Kong, Delhi, Jakarta, Melbourne, Auckland). `name` is the string sent as `{"magic": name}`. Defaults must be a subset. No network lookups; coordinates are static. Names must be unique.
2. `render_picker_map(selected: list[str]) -> str`: same bundled land and style as `render_map`, one `<circle class="sw-pick" data-place="NAME" tabindex="0" role="checkbox" aria-checked=...>` per catalog place with a `<title>`; selected = filled blue (`var(--pin)`), unselected = grey ring. All dynamic text escaped. No outside resources.
3. Index page: map first (below the header strip), then presets (Default 12, One per continent, All, Clear), chips grouped by continent (real `<button type="button" aria-pressed>` toggles, same state as the dots), an "Add another place" text box + Add button (adds a chip without a dot, trimmed, no commas, max 40 chars), then the address box, extra checks, limits, estimate, Start test (as the mockup page-1). The old visible text input is removed; a hidden `locations` input holds the comma-separated selection so `/estimate` and `/scan` are unchanged. Zero selected is an error ("Pick at least one place"), never silently the default.
4. Live estimate: every toggle calls the existing estimate flow; summary line "N locations · about M tests".
5. During a scan the form and picker are disabled and the map stays visible; after a scan the results map (existing `/render` fragment) appears in the same position as the picker, and the picker collapses to a one-line summary "Locations: N selected (change and run again)" that reopens it. Saved results restore the picks.
6. Phone width: map full width, chips stack below, no horizontal page scroll. Keyboard: dots and chips focusable and toggle with Space/Enter.
7. CSP unchanged (no inline script or style attributes; classes only). Text set with `textContent` only. CLI `--locations` unchanged.
8. Tests: unit tests for catalog (unique, defaults subset, continents known, lat/lon in range), picker map (escaping, selected state, no external URL), view renders map and hidden input; e2e: click a dot toggles chip and estimate, presets, add custom place, zero-selection error, picks restored after refresh.

### Task 14: Bigger closable results drawer and SVG focus ring (author-approved mockup page-9-table-drawer.html)

**Files:** `speedward/web/static/js/results.js`, `speedward/web/static/css/app.css`, `speedward/reports/map.py` (focus style for `.sw-pick` / `.sw-dot`), tests in `tests/` and `e2e/test_picker.py` (or a new e2e file).

**Requirements:**
1. The results details drawer opens to about 70% of the viewport height (90% on phone width), table scrolls inside it, map stays visible above it, the clicked dot stays highlighted. Use dvh with a vh fallback.
2. Close: a visible "Close" button with accessible name, Escape key, and clicking the map outside the drawer. Opening moves focus into the drawer; closing returns focus to the dot or button that opened it.
3. Focus ring on SVG dots (`.sw-pick`, `.sw-dot`) must be visible in Firefox and Chromium: use a stroke-based style (for example `:focus-visible{stroke:...;stroke-width:...}`), at least 3:1 contrast against the map; keep it keyboard-only (`:focus-visible`).
4. CSP unchanged: no inline script or style attributes, textContent only.
5. Tests: e2e that the drawer opens taller than before (height >= 60% of viewport), Close button closes it, Escape closes it, focus returns; unit/e2e that the focus style rule exists and the focused dot has a non-default stroke.

### Task 15: Server pin so connection lines always draw (author chose option A of mockup page-10-lines.html)

**Problem:** `render_map` draws arcs only when `report.server` is known (geo-IP DB or SERVER_LAT/SERVER_LON); in a default setup no lines show. No outside lookups are allowed.

**Requirements:**
1. Web form: a "Server" pin on the map the person can drag (pointer and keyboard: arrow keys move it, with a visible focus ring) and a "Server is in:" select listing the catalog cities plus "Not set". Lines from the pin to each selected place appear as faint previews in picker mode and as speed-coloured arcs (dashed grey for unreachable) in results.
2. The chosen position (two numbers, validated finite, lat -90..90, lon -180..180) is kept in this tab only (sessionStorage, same expiry as results) and posted with /scan as `server_lat`/`server_lon`; `/estimate` unchanged. A configured override or geo-IP result still wins; a manual pin applies only when neither exists. The source is recorded as "manual (pin)" in the report so it is not mistaken for a measured location.
3. If no position is set, the map shows "Server position unknown: drop the pin or set SERVER_LAT/SERVER_LON" with no fake lines.
4. Core stays Django-free; the validation lives in core (extend Geolocator or runner input), web stays thin. Never log the position or the target.
5. CSP unchanged, textContent only, keyboard operable, WCAG: pin has role and accessible name, instructions announced.
6. Tests: unit (validation, precedence, report source label, arcs drawn when pin given, none when unknown), views (bad values refused with a fixed message), e2e (drag/keys move pin, select sets it, lines appear in picker and results, survives refresh, cleared with results).
7. Update README and demo.md (how lines work and the setting).

### Task 16: Lines to the server that answered each probe (author-approved mockup page-11-answering-server.html)

**Problem / intent:** the author does not want a dragged server pin as the main path. Position should come from where the content was actually found: each measured result's answering IP (`server_ip`, from Globalping `resolvedAddress`), located offline. A CDN or anycast address makes the offline database unreliable (shared IPs are registered at one place), so uncertain cases must draw nothing and say so. Never a guess.

**Requirements:**
1. Per-result server position: add to `LocationResult` an optional `server_position` (lat, lon, source in a fixed set: "database", "cdn header") plus an `edge_note`/uncertain flag; strict `from_dict`/`to_dict` validation like the existing fields; old reports without it still load.
2. Resolution in core (no Django, no network): for each measured result with a public IP, use the geo-IP database (existing Geolocator/GEOIP_DB) -> "database". Where a CDN header names the edge city, prefer it -> "cdn header": parse Cloudflare `cf-ray` suffix (airport code, e.g. `-NRT`) via a small BUNDLED IATA code to lat/lon table (a few hundred major codes, static, with a data-source note); other CDNs only if the header format is certain, else skip. Headers must be kept long enough for this (store only the derived code, never raw headers in the report).
3. Uncertain rule: if the same IP is returned to probes on 2+ continents (anycast/shared), or the database position is more than ~2,500 km from a cdn-header position for the same result, or the IP is private/not in the database, draw no square and mark "edge location uncertain" / "position not available" in the table. Document thresholds as named constants.
4. Map (reports/map.py): a circle per test location (as now), a square at each result's server position, a speed-coloured line between them (dashed grey for unreachable, with no square). When all located results share one IP, draw one "Server" square with lines from every probe to it (the single-origin case in the mockup). Tooltip on a square: IP, source, and "CDN edge, not necessarily your origin" when IPs differ between probes. All text escaped.
5. Results table: new "Answered by" and "Position source" columns exactly as the mockup; values "not available" when unknown.
6. The existing server pin and "Server is in:" list stay but only as a fallback when NO result has a located position and no override: labelled "manual (pin)" as now, and the UI shows the mockup note "No server positions yet... How to set it up / Place a server manually (your own claim)". A configured SERVER_LAT/LON override still wins for the single "Server" square.
7. Exports (HTML, JSON, Markdown) carry the new fields and sources; Markdown includes the columns.
8. README and demo.md: a short "download the location file" step (MaxMind GeoLite2 City or DB-IP Lite City, licence note), the Docker/.env setting, and an explanation of CDN edge vs origin and the uncertain cases. CSP unchanged, textContent only, keyboard/WCAG: squares have titles and accessible names.
9. Tests (fake server, never real Globalping; fake geo DB or monkeypatched reader): unit for cf-ray parsing, IATA table sanity (codes unique, lat/lon in range), uncertain rules, strict from_dict, map output (squares, lines, escaping), markdown/json export; e2e that results show squares/columns and the fallback note when no database is set.

### Task 17: Creative-minimal restyle and WCAG 2.2 AA pass (author chose option B of mockup page-12-creative-minimal.html)

**Scope:** restyle the existing web UI only (CSS, template markup/classes, small JS for numerals and the load wipe). Keep every behaviour, layout structure (map as page background, panels floating over it, bottom chip strip, results drawer) and all endpoints unchanged. The phone LAYOUT restructure (page-8) and the logo (page-7) are NOT in this task (still awaiting author answers); phone only needs the new styling to work and remain usable.

**Design (mockup page-12 option B; also page-6 WCAG list):**
1. Tokens on `:root` with a light theme "paper" (`--paper:#F0EAE0`, `--ink:#1C1C1C`, land a quiet darker paper tone, hairlines `1px solid var(--ink)`, one accent red `#C62F17` used only for the server marker and the Start button) and a dark theme (near-black `#141414` paper, off-white ink) under `prefers-color-scheme: dark` guarded by `:root:not([data-theme="light"])` and again for `:root[data-theme="dark"]`. Body gets an explicit background.
2. System grotesque stack (Inter if present, then -apple-system, system-ui); no web-font requests. Display numerals use weight 800 with `letter-spacing:-.04em`; small labels uppercase with `letter-spacing:.12em` at >=70% ink.
3. Panels are flat: hairline border, paper background, NO box-shadow, NO backdrop blur, NO radius. Only buttons and location chips (pills, radius 980px) keep rounding. Numbered step labels "01 Address", "02 Places".
4. Results headline: the average first byte, cache gain and unreachable count as large numerals (clamp-based, weight 800 for the first, 100 for the others at >=3:1 as large text) with tiny uppercase captions, replacing the tile boxes (data from the same /render fragment; keep text via textContent and keep the exact existing wording for "measured locations only" and excluded-location notes).
5. Edge labels: tiny uppercase header text with the allowance strip wording from the server unchanged; map coordinates label may show the server position only when known (never invent).
6. Map recoloured to paper/ink; speed colours green/amber/red stay but darkened where needed to reach 3:1 against the map land; unreachable stays dashed grey. Picker dots/selected states have non-colour cues (filled vs ring).
7. Motion: short clip-path wipe on first load and for panel/drawer opening (transform/clip-path/opacity only, cubic-bezier(.19,1,.22,1)); everything under `prefers-reduced-motion: reduce` switches off. No Lenis, no marquee.
8. WCAG 2.2 AA: text 4.5:1 (large 3:1), non-text/UI 3:1 incl. focus rings, visible focus on every control and SVG dot/pin, targets >= 24px (buttons/chips >= 32px), `prefers-reduced-transparency` n/a (no blur), forced-colors tolerable, zoom 200% and 320px width without horizontal page scroll or clipped controls, labels and landmarks, aria-live for progress/errors, no color-only meaning (status also as text).
9. Verification: add an automated axe-core check in the browser tests for picker mode, scanning, results and the open drawer, in light and dark (prefer a dev dependency such as axe-playwright-python in pyproject [e2e] extra; otherwise vendor axe.min.js under e2e/vendor with its licence). Fix real violations; any waived rule needs a written reason in the test. Plus computed-contrast assertions for the key token pairs (unit test over the CSS tokens).
10. CSP unchanged: no inline style attributes or script, textContent only. Update README screenshots/description, demo.md and CLAUDE.md only where they describe appearance.

### Task 18: Ruff lint and GitHub Actions CI (author said "proceed" to the tooling suggestion)

**Requirements:**
1. Add `ruff` to the `dev` extra and a `[tool.ruff]` config in pyproject.toml: target py311, a modest rule set (E, F, I, B, UP are fine; add S only if it does not drown the codebase), line length matching the existing code, per-file ignores only with a comment saying why. Run `ruff check` and fix real findings (unused imports/variables, bugs); use `--fix` only for safe fixes. Do NOT run `ruff format` over the codebase (no mass reformat diff). If a rule produces many style-only findings, turn it off in config with a comment instead of rewriting code.
2. `.github/workflows/ci.yml`: on push to main and on pull_request. Top-level `permissions: contents: read`. Jobs: (a) lint: `ruff check`; (b) unit tests on Python 3.11 and 3.12 with `pip install -e ".[dev,geoip]"` and `pytest -q`; (c) e2e: `pip install -e ".[dev,e2e]"`, `playwright install --with-deps chromium`, `pytest e2e -q`. Pin third-party actions to a major version tag or commit SHA that you verify exists (actions/checkout, actions/setup-python), enable pip caching. No secrets are needed or used: tests use the fake Globalping server; make sure no test or step reads GLOBALPING_TOKEN or hits the real API, and set `GLOBALPING_TOKEN=` empty in the workflow env to guarantee it.
3. Add `pip-audit` as a non-blocking (continue-on-error) separate job OR a documented command in README under Development; choose the simpler. Add `.github/dependabot.yml` for pip and github-actions (weekly).
4. README Development section: how to run ruff, the CI badge (only after the workflow file path is final), and the audit command. CLAUDE.md Commands: add `.venv/bin/ruff check .`.
5. Verify locally: `ruff check .`, `pytest -q`, `pytest e2e -q` all pass. Validate the workflow YAML parses (python yaml.safe_load) and, if `actionlint` is available, run it. Do not push; the CI cannot be proven until it runs on GitHub, say so in the report.

### Task 19: "Where the time goes" bottleneck panel (author approved mockup page-13-bottleneck.html)

**Purpose (author):** speedward should help the person improve a site's speed by showing the bottleneck: what costs the most time and what to look at first. Speed stays the focus.

**Requirements:**
1. Core (no Django, pure functions in `speedward/core/analysis.py` or a new `speedward/core/bottleneck.py`): `diagnose(report) -> Diagnosis` using ONLY results with status `measured` (blocked, test_failed and not_run are excluded and counted separately, never enter any figure). Stages: DNS, connect, TLS, server wait (first byte minus DNS, connect, TLS; if the API's stages do not allow this subtraction exactly, define it from the stage fields actually stored and document it), download. Output: average ms per stage and total; the biggest stage with ms and share of total; per-location dominant stage; cache facts (HIT/MISS counts, repeat gain computed only where the repeat used the same probe; otherwise say so); distance facts (connect time spread between nearest and farthest measured places).
2. Classification rules with named constants (documented in README, tunable like FAST_MS/SLOW_MS where sensible): a stage is "dominant" at >= 40% of total, otherwise "no single stage dominates"; "server wait" vs "distance": server wait is the bottleneck when it dominates in places whose connect time is low (below a named threshold), distance when connect+TLS grow with distance; DNS called out only when its share is large in at least some places. Never claim an unmeasurable cause: wording is "the timings indicate", each card has a "what to check" prompt (origin response time, CDN cache rules, number of regions/edges, DNS provider), not a promise. With fewer than 3 measured places say "not enough places for a conclusion".
3. Rendering: a "Where the time goes" panel in the results (web, HTML export, Markdown, JSON field `diagnosis`) in the creative-minimal style of the mockup (headline sentence with share, stage bar with labelled ms, three cards: Biggest cost, Caching, Distance; per-location list tagged by what holds each back; footnote that failed places are left out). All text escaped / textContent; bar segments non-colour-only (labels in text); contrast tests and axe still pass; reduced-motion respected.
4. Strict `from_dict` for the new field; old reports without it still load and simply show no panel (never a made-up diagnosis); CLI prints a short text version.
5. Tests: unit for every rule and boundary (exactly 40%, <3 places, all-blocked, same-probe vs different-probe repeat, DNS-heavy, distance-heavy, server-heavy), strict loading, exports (html/md/json), e2e that the panel appears with the fake server, axe on the results with the panel (light and dark), and a test that blocked/failed locations never alter any number.
6. README: how the diagnosis works, the thresholds, and an explicit "what it cannot tell you" section.

7. **Block diagram (author approved mockup page-14-block-diagram.html, supersedes the stage bar in item 3):** draw the request path as outlined blocks left to right: Visitor, DNS, Connect, TLS, Edge/CDN (dashed, seen but not timed), Server wait, Download. Each block shows ms and share of total. Marks: "biggest block" (largest share, needs >= 40%: thick outline, dark fill, text label) and "possible block" (share >= 25% or connect/TLS growing with distance: hatched outline, text label); others "fine". Named constants BIGGEST_SHARE=0.40, POSSIBLE_SHARE=0.25. A to-scale share bar sits under the path. Two "what to check" notes for the flagged blocks ("the timings indicate", never a cause). Clicking a place (map dot or table row) redraws the diagram for that place; blocked/failed places show no diagram, only the reason and "left out of every figure". Fewer than 3 measured places or nothing measured: no blocks marked, with the reason. Marking is never colour-only; diagram is a labelled list for screen readers; JSON `diagnosis` carries per-stage ms, share and mark. Tests cover each mark and boundary (exactly 25% and 40%).

### Task 20: "More checks" panel and the "Who can reach it" grid (author approved mockup page-16-more-checks.html)

**Purpose (author):** a global overview of what is accessible and what is not, from Globalping's probes only. Never run local traceroute/mtr/dig; nothing runs from the server. Facts for options are in `docs/globalping-api-notes.md` ("Extra options for speed tests"); anything not observed there must be confirmed with the real API (a few single-probe tests, no secrets printed) before relying on it, else "not available".

**Scope of this task (reachability group only; the speed group is Task 21):**
1. Opt-in checks under the picker, each reusing the main scan's probes by measurement id where possible: HTTPS over IPv4 and IPv6 (`ipVersion`), HTTP vs HTTPS (plain HTTP shows "to HTTPS" when it redirects, using `headers.location`), DNS through different resolvers (a small fixed list of well-known public resolver IPs, named constants; compare `timings.dns` and failures), TCP port 443 reachable (ping `protocol: TCP`, `port: 443`, few packets). Default scan stays 3 tests per place; checks add nothing unless ticked.
2. Cost before every scan: per check and in total in the existing estimate (`/estimate`), refuse over-allowance unless confirmed (existing rule), the checks included. The cost gate and "never start a scan before the cost is shown" still hold.
3. Status model unchanged: measured / blocked / test_failed / not_run; only the site refusing or timing out is "blocked"; Globalping-side problems are "test failed" or "not run" and never enter any figure.
4. Core (no Django): models and strict `from_dict` for the new results (old reports still load, no grid); a pure function producing the grid and a plain finding ("the timings indicate: N of M places cannot reach the site over IPv6", with a check prompt) using only measured/blocked cells, never fabricated; fewer than 3 places says not enough places.
5. Rendering: the "Who can reach it" grid in the web UI, HTML export, Markdown, JSON and CLI text, matching the mockup (symbol plus word per cell, never colour alone; ink and paper tokens, no red), the map dot uses the worst result for a place with the legend saying so; axe and contrast tests in light and dark, phone reflow, reduced motion, strict CSP, textContent/escaping.
6. Tests: unit for every rule and boundary, fake-server tests for each check, estimate arithmetic, strict loading, exports, e2e for the panel and grid; README section (what each check means, costs, what it cannot tell you).

### Task 21: Speed checks that feed the "Where the time goes" diagram (author approved mockup page-16-more-checks.html, "Is it fast?" group)

**Purpose (author):** explain where the time goes and what to look at first, globally, from Globalping only (no local traceroute/mtr/dig, nothing run from the server). Builds on Task 19 (diagnosis and block diagram) and Task 20 (More checks panel, check plumbing in `speedward/core/checks.py`, `reach.py`). Facts for options: `docs/globalping-api-notes.md` ("Extra options for speed tests" and "Reachability checks"); the OpenAPI spec copy is at /tmp/claude-1000/-home-sharif-Documents-vs-code-work-devbridge-site-speed/19813fe9-833e-4513-93fb-8312204762e9/scratchpad/gp-spec.yaml. Anything not observed must be confirmed with a few real single-probe calls (at most ~10, token read from .env without printing) or shown as "not available".

**Checks (all opt-in, default scan stays 3 tests per place; extend the same "More checks" group "Is it fast?" and the cost breakdown):**
1. **Consistency:** repeat the HTTP test 5 times on the main test's probes (reuse by measurement id); show median and slowest server wait and "steady" vs "varies" per place and overall; feeds the Server wait block. +4 tests per place. Fewer than 5 answers shows the count; never fabricate.
2. **HTTP/2 against HTTP/1.1:** one extra http test with `protocol: HTTP2` on the same probes; show the version each answered with (parse the first `rawOutput` line) and the time difference in connect/TLS/first byte. +1 per place.
3. **Compression:** one extra http test sending `Accept-Encoding: br, gzip` (header option); report `content-encoding` or "not compressed" per place, and the download-time difference only where both runs measured. Page size on the wire is NOT available (chunked, no content-length, body capped at 10 KB): say so, never estimate. +1 per place.
4. **DNS delegation trace:** one dns test with `trace: true`; show per-hop time (local resolver, root, TLD, authoritative) and which hop is slowest; feeds the DNS block. +1 per place.
5. **Path loss and jitter (mtr):** one mtr test per place with a small packet count (named constant, within the 16 max); show worst-hop loss and jitter and flag a hop where loss or latency jumps; feeds the Connect block (far away vs a bad hop). +1 per place. Handle the observed MTR details in the notes (asn list, silent hops); wording "the timings indicate".

**Rules:** the status model is unchanged (measured / blocked / test_failed / not_run; failed or not-run never enter a figure or finding); the cost gate holds (estimate, per check and total, over-allowance confirm, never start before cost shown); `/estimate`, CLI `--checks` names and the JSON export carry the new checks with strict `from_dict` and the same caps as `reach.py` (bounded sizes, no OverflowError/NaN; old reports load without them); render in the web results drawer, HTML export, Markdown, JSON and CLI text in the approved creative-minimal style (ink/paper, no red, symbol plus word never colour alone, axe light/dark, phone reflow, reduced motion, strict CSP, textContent/escaping); the Task 19 diagram gains the notes (for example "steady" or "varies 140 to 900 ms" on Server wait; "TLS 1.2 costs one more round trip" only where measured; "not compressed" on Download; slowest DNS hop on DNS; a bad hop on Connect) without changing its thresholds; minimum 3 places for any conclusion.

**Tests and docs:** unit for every rule and boundary, fake-server tests per check, estimate arithmetic, strict loading, exports, e2e for panel and results; README (each check, cost, what it cannot tell you, page size not available).

### Task 22: Follow-ups from the Task 19 to 21 reviews (no new design; author asked for them)

1. **Full Markdown escaping:** the Markdown export's main Locations table, the diagnosis cards and every other place that prints untrusted text (place names, reasons, hostnames, header values) escape Markdown and table syntax the way `md_text` (Task 21) does. Update the earlier tests that pin the old output (for example `edge NRT (Tokyo)`) to the escaped form deliberately, and keep numbers and fixed words readable.
2. **One mtr test per place:** when both the old "Network path" option and the new mtr check are ticked, run a single mtr measurement and use it for both; the estimate, the CLI cost line and the picker hint count it once. If the two use different packet counts, use the larger. Tests for cost arithmetic and for both outputs being filled from one measurement.
3. **Thresholds as settings:** make the Task 19 and Task 21 named constants (biggest 40%, possible 25%, minimum places 3, close connect 40 ms, distance correlation 0.7 and spread 50 ms, DNS large share, varies 2x and 50 ms, bad hop 10% loss, long link 50 ms) overridable from the environment and `.env.example` with validated bounds (e.g. minimum places never below 3, shares between 0 and 1, finite numbers, bad value -> clear startup error). Each saved diagnosis and speed summary already stores the values it used: relax strict loading so a saved copy is accepted when its stored values are within the same bounds and the result is exactly what its own timings give under them (never accept below-bound values such as min_places < 3). Web, CLI, exports all use the configured values; README documents each setting.
4. **Map label collisions (investigate only):** report with a screenshot how often place labels overlap on the map (for example London and Frankfurt) and propose options. Do NOT change the map design; the author wants a mockup first.

### Task 23: Map labels that do not collide (author approved mockup page-19-map-labels.html)

**Problem (measured in Task 22, see task-22-report.md and the findings in /tmp/claude-1000/-home-sharif-Documents-vs-code-work-devbridge-site-speed/19813fe9-833e-4513-93fb-8312204762e9/scratchpad/t22tools/labels-findings.json, tool labels.py):** every label sits at one fixed offset from its dot. Default 12 places: London/Frankfurt overlap, Sydney cut off at the right edge. Europe 7: 6 overlapping pairs. All 32: 15 overlapping pairs, 9 to 11 labels over another place's dot, Sydney/Melbourne/Auckland cut off. The "Server (manual setting)" label runs into Frankfurt. On phones the export's labels are about 4 px high.

**Approved approach (pure rendering in `speedward/reports/map.py`, shared by the web results map and the HTML export; no new data, no outside requests):**
1. Deterministic label placement: for each label try a fixed list of candidate positions around its dot (right, left, above, below, diagonals), pick the first that overlaps no other label box, no other place's dot, the server square, or the map edge; flip left/up near edges so nothing is cut off. Stable ordering (same input gives the same output); label box size from a simple character-width estimate with a safe margin (no browser measurement).
2. Clusters of two or three close places share one label joined by thin leader lines ("London · Paris"); larger clusters show a count label ("Europe · 5" style: a neutral name such as the place count, never an invented region) and every place is still listed in the table and in each dot's title. Define "close" with a named constant in pixels of the map viewBox.
3. If a label still cannot be placed without a collision, drop it (the dot keeps its accessible title and the table lists it); never overlap, never invent text.
4. Phones and small widths: labels keep a readable minimum rendered size (set via viewBox-relative font size or a CSS rule at narrow widths) and only the nearest or most important labels show when space is short (rank: blocked/measured places the person is looking at first, then by name); every place stays reachable by keyboard and in the table.
5. Keep the style: ink and paper tokens, flat, no red (the server marker keeps its red); text never colour-only; dark theme; reduced motion; strict CSP (no inline style attributes); escaping of all place names.
6. Tests: unit tests for the placement function (no collisions for the default 12, Europe 7 and all 32 sets from the measurement tool; no label outside the viewBox; deterministic; cluster merging thresholds; dropped-label fallback), render tests for web and export, an e2e check that re-uses the Task 22 labels.py measurement idea (assert 0 overlapping label pairs and 0 labels cut off for the three sets, desktop and phone widths), axe in light and dark, reflow. README: one short note on how labels are placed and what a merged label means.

### Task 24: Phone layout and a Light / Dark / Auto theme switch (author approved mockups page-20-phone.html and page-21-theme-switch.html)

Mockup sources: /home/sharif/Documents/vs_code/work/devbridge/site-speed/.superpowers/brainstorm/12023-1793700849/content/page-20-phone.html and page-21-theme-switch.html (read the HTML). Desktop stays as it is.

**Phone layout (below 700 px wide), web app only:**
1. One thin top bar: logo and name before a scan, the address in results mode, plus a menu button (Export, Run again, Clear now, Locations, and the theme switch). It never covers more than one line, so the map stays visible.
2. One bottom sheet replacing the stacked panels, with three heights: peek (before a scan: address, cost, Start; in results: headline number, the finding, blocked count), half (places, More checks, summary), full (diagram, Who can reach it, Is it fast?, place table). Drag the handle, tap it, or use a Show/Hide button; keyboard and screen-reader order matches the sections; focus is managed when the height changes; the sheet is a labelled region; reduced motion removes the slide animation. The cost-before-Start rule still holds (cost visible in peek before Start).
3. Diagram as a vertical list of blocks on phones (same marks, texts and to-scale bar; same data, one template: do not fork the diagnosis logic).
4. Wide tables (Who can reach it, Is it fast?, places) scroll sideways inside their own focusable region with the place-name column fixed; place table rows may become cards.
5. Server pin: tap area at least 44 px and a 'Move pin' button as an alternative to dragging (keyboard arrows stay).
6. Map labels: the Task 23 behaviour stays; nothing new.

**Theme switch (all widths):**
1. Light / Dark / Auto segmented radio group (Auto default) in the desktop top bar and in the phone menu. Light and Dark set `data-theme` on `<html>` and remember the choice in `localStorage` for this browser only (wrapped in try/catch, page works without it); Auto removes the attribute and follows the device. Never sent to the server, no cookies.
2. No flash: apply the saved choice before first paint with a small external script file in `<head>` (strict CSP: no inline script), not a deferred module.
3. Accessible: real radio group named 'Colour theme', arrow-key keyboard use, visible focus ring, 44 px tap height on phones, contrast checked in both themes.
4. The map, diagram, grids, favicon and logo follow the theme (`reports/map.py` and tokens already support `data-theme`); exported HTML reports keep following the viewer's device (no switch).

**Rules:** ink/paper tokens, flat hairline, no red except Start and the server marker; text never colour-only; strict CSP (no inline style attributes added); textContent only; no outside requests; nothing stored on the server.

**Tests:** e2e at 390 px and 320 px and 200% zoom (no horizontal page scroll, nothing clipped, map visible with the sheet at peek, sheet heights by pointer and keyboard, focus order, sheet content reachable), theme switch (sets and persists, Auto follows the device via emulated colour scheme, no flash: attribute present at first paint, works with localStorage blocked), axe in light and dark on desktop and phone in every sheet height, contrast tests for any new token pairs, reduced motion. README: how to use the sheet and theme switch.

## Self-Review notes

- Spec coverage: map and dimmed unreachable (Task 7); cold, warm, delay (Tasks 3 to 5); reasons (Tasks 3, 7); CDN (Task 5); pass/fail (Tasks 5, 8); browser test (Task 10); export (Tasks 7, 9); stage breakdown, path, network type, DNS, server identity (Tasks 4, 5, 7); key handling and wording (Tasks 3, 9, 11); budget (Tasks 4, 8, 9); session storage and expiry (Task 9); server position (Task 6); first-task verification (Task 1).
- Names used across tasks match the Interfaces blocks above (`LocationResult`, `Stages`, `Plan`, `Allowance`, `GlobalpingClient`, `classify`, `summarise`, `evaluate_limits`, `Geolocator`).
