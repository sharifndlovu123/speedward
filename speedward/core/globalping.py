"""Globalping client, error taxonomy and result classification.

Field names come from docs/globalping-api-notes.md (observed against the real API). The token is
only ever sent as a bearer header: it is kept out of repr, exception messages and logs.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

DEFAULT_BASE_URL = "https://api.globalping.io/v1"
ANON_TESTS_PER_HOUR, ANON_MAX_PROBES = 250, 50
KEY_TESTS_PER_HOUR, KEY_MAX_PROBES = 500, 500


class GlobalpingError(Exception):
    """Anything that stopped a Globalping call. Messages never contain the token."""


class RateLimited(GlobalpingError):
    """HTTP 429. The response shape was never observed, so every field is optional."""

    def __init__(self, message: str = "Globalping limit reached", remaining: int | None = None,
                 retry_after: int | None = None):
        super().__init__(message)
        self.remaining = remaining
        self.retry_after = retry_after  # seconds, from retry-after or x-ratelimit-reset


class ProbeReuseUnavailable(GlobalpingError):
    """The earlier measurement whose probes were to be reused is expired or unknown (HTTP 422)."""


@dataclass
class Allowance:
    tests_per_hour: int
    max_probes: int
    remaining: int | None
    authenticated: bool
    credits: int | None = None
    reset_seconds: int | None = None  # seconds until the allowance resets; None when not reported


def _int(value) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _reset(value) -> int | None:
    n = _int(value)
    return n if n is not None and n >= 0 else None


def parse_rate_headers(headers) -> dict:
    """Read x-ratelimit-* and x-request-cost (lowercase on the wire; httpx is case-blind)."""
    out = {}
    for key, name in (("limit", "x-ratelimit-limit"), ("remaining", "x-ratelimit-remaining"),
                      ("reset", "x-ratelimit-reset"), ("consumed", "x-ratelimit-consumed"),
                      ("cost", "x-request-cost")):
        value = _int(headers.get(name))
        if value is not None:
            out[key] = value
    return out


class GlobalpingClient:
    def __init__(self, token: str | None = None, base_url: str = DEFAULT_BASE_URL,
                 timeout: float = 30, http: httpx.Client | None = None,
                 poll_interval: float = 0.5, wait_timeout: float = 60):
        self._token = token or None
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.wait_timeout = wait_timeout
        self._http = http or httpx.Client(timeout=timeout)
        self.last_rate: dict = {}  # latest x-ratelimit-* / x-request-cost seen on a POST 202

    def __repr__(self) -> str:
        return f"GlobalpingClient(base_url={self.base_url!r}, token={'<set>' if self._token else '<unset>'})"

    __str__ = __repr__

    # --- plumbing ------------------------------------------------------
    def _request(self, method: str, path: str, **kw) -> httpx.Response:
        headers = {"Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            resp = self._http.request(method, self.base_url + path, headers=headers, **kw)
        except httpx.HTTPError as e:  # str(e) can echo URLs; keep only the type
            raise GlobalpingError(f"could not reach Globalping ({type(e).__name__})") from None
        if resp.status_code == 429:
            rate = parse_rate_headers(resp.headers)
            retry = _int(resp.headers.get("retry-after"))
            raise RateLimited(remaining=rate.get("remaining"),
                              retry_after=retry if retry is not None else rate.get("reset"))
        if resp.status_code == 401:
            raise GlobalpingError("Globalping rejected the API key (401)")
        return resp

    @staticmethod
    def _api_message(resp: httpx.Response) -> str:
        try:
            err = resp.json()["error"]
            return str(err.get("message") or err.get("type") or "")[:200]
        except Exception:  # noqa: BLE001 - the body may be anything
            return ""

    # --- API -----------------------------------------------------------
    def create(self, type: str, target: str, locations: list[dict] | str, options: dict) -> str:
        """Start a measurement and return its id.

        `locations` is a list of location dicts, or a previous measurement id (a plain string) to
        repeat the test on the same probes.
        """
        body = {"type": type, "target": target, "locations": locations, "measurementOptions": options or {}}  # the API rejects null
        resp = self._request("POST", "/measurements", json=body)
        if resp.status_code == 422 and isinstance(locations, str):
            raise ProbeReuseUnavailable("the earlier measurement's probes are no longer available")
        if resp.status_code != 202:
            raise GlobalpingError(f"Globalping refused the test ({resp.status_code}) {self._api_message(resp)}".strip())
        self.last_rate = parse_rate_headers(resp.headers)
        try:
            return resp.json()["id"]
        except Exception:  # noqa: BLE001
            raise GlobalpingError("Globalping returned an unreadable answer") from None

    def wait(self, id: str) -> dict:
        deadline = time.monotonic() + self.wait_timeout
        while True:
            resp = self._request("GET", f"/measurements/{id}")
            if resp.status_code != 200:
                raise GlobalpingError(f"Globalping could not return the results ({resp.status_code})")
            try:
                doc = resp.json()
            except ValueError:
                raise GlobalpingError("Globalping returned an unreadable answer") from None
            if doc.get("status") != "in-progress":
                return doc
            if time.monotonic() >= deadline:
                raise GlobalpingError(f"measurement did not finish within {self.wait_timeout:g} s")
            time.sleep(self.poll_interval)

    def allowance(self) -> Allowance:
        """GET /limits (costs no tests). Falls back to the documented 250/500 if unreachable."""
        authed = self._token is not None
        fallback = Allowance(KEY_TESTS_PER_HOUR if authed else ANON_TESTS_PER_HOUR,
                             KEY_MAX_PROBES if authed else ANON_MAX_PROBES,
                             self.last_rate.get("remaining"), authed, None, _reset(self.last_rate.get("reset")))
        try:
            resp = self._request("GET", "/limits")
        except RateLimited:
            return fallback
        except GlobalpingError as e:
            if "rejected the API key" in str(e):
                raise
            return fallback
        if resp.status_code != 200:
            return fallback
        try:
            doc = resp.json()
            create = doc["rateLimit"]["measurements"]["create"]
            limit = _int(create.get("limit"))
            remaining = _int(create.get("remaining"))
            credits = _int((doc.get("credits") or {}).get("remaining"))
            reset = _reset(create.get("reset"))
        except Exception:  # noqa: BLE001
            return fallback
        return Allowance(limit if limit is not None else fallback.tests_per_hour, fallback.max_probes,
                         remaining, authed, credits if authed else None, reset)


# --- classification ---------------------------------------------------

# Causes on the site's side, matched against the probe's rawOutput text.
_TARGET_CAUSES = (
    ("timed out the connection", ("timed out", "etimedout", "timeout")),
    ("DNS lookup failed", ("enodata", "enotfound", "queryA", "queryAAAA", "eai_again", "nxdomain", "dns")),
    ("connection refused", ("econnrefused", "refused")),
    ("connection reset", ("econnreset", "reset by peer")),
    ("TLS handshake failed", ("tls", "ssl", "certificate", "cert_", "handshake")),
)


def _target_cause(raw: str) -> str | None:
    low = raw.lower()
    for label, needles in _TARGET_CAUSES:
        if any(n.lower() in low for n in needles):
            return label
    return None


def classify(raw_probe_result: dict) -> tuple[str, str]:
    """Return (status, reason) for one `results[]` item (or a bare `result`).

    Statuses: "measured", "blocked" (the site refused or timed out), "test_failed" (Globalping or
    the probe failed, or we cannot tell), "not_run" (no probe for the location). Timings are never
    returned: timings.total is numeric even on failure and must not be used.
    """
    if not isinstance(raw_probe_result, dict):
        return "not_run", "no result for this location"
    if "result" in raw_probe_result or "probe" in raw_probe_result:
        result = raw_probe_result.get("result")
    else:
        result = raw_probe_result
    if not isinstance(result, dict) or not result:
        return "not_run", "no probe was available for this location"

    status = result.get("status")
    raw = result.get("rawOutput") if isinstance(result.get("rawOutput"), str) else ""
    if status == "offline":
        return "test_failed", "the test probe went offline"
    if status == "failed":
        if result.get("failureSource") != "target":
            return "test_failed", "the test failed on the Globalping side"
        cause = _target_cause(raw)
        if cause is None:
            return "test_failed", "the test failed for an unrecognised reason"
        return "blocked", cause
    if status != "finished":
        return "test_failed", "the test did not finish"

    tls = result.get("tls")
    if isinstance(tls, dict) and tls.get("authorized") is False:
        err = tls.get("error")
        return "blocked", f"certificate error: {err if isinstance(err, str) and err else 'not trusted'}"
    code = result.get("statusCode")
    if code in (403, 451):
        return "blocked", f"the site answered HTTP {code}"
    return "measured", ""
