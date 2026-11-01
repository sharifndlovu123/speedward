"""Local fake of the Globalping API for tests. Nothing here touches the internet.

Canned responses are trimmed copies of real recordings in tests/fixtures/globalping/. The 429
response is NOT a recording (it was never observed): it is whatever a test asks for.
"""
import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "globalping"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


class FakeGlobalping:
    def __init__(self, valid_token: str | None = None):
        self.valid_token = valid_token  # when set, any other bearer token gets 401
        self.requests = []  # (method, path, headers, json body or None)
        self.template = fixture("http_ok")  # what the next POST will "measure"
        self.measurements: dict[str, dict] = {}
        self.in_progress_polls = 0  # GETs that answer "in-progress" before "finished"
        self.force_429: tuple[dict, bytes] | None = None  # (headers, body) for every POST
        self.limits_status = 200
        self.limit = None  # overrides the limit in /limits and the POST headers
        self.remaining = None
        self.credits = None
        self.by_type: dict[str, dict] = {}  # per measurement type template, overrides `template`
        self.queued: dict[str, list[dict]] = {}  # one-shot docs per type, used before templates
        self.rate_limit_after: int | None = None  # accept this many POSTs, then answer 429
        self.reuse_expired = False  # every POST with a measurement id as locations gets 422
        self._accepted = 0
        self._polls: dict[str, int] = {}
        self._counter = 0
        self._httpd = None
        self.url = ""

    # --- test controls -------------------------------------------------
    def use(self, name_or_doc) -> None:
        self.template = fixture(name_or_doc) if isinstance(name_or_doc, str) else name_or_doc

    def set_probe_result(self, result: dict, index: int = 0) -> None:
        """Replace the `result` of one probe in the template (e.g. a per-probe failure)."""
        self.template = copy.deepcopy(self.template)
        self.template["results"][index]["result"] = result

    def queue(self, type: str, name_or_doc) -> None:
        """Use this doc for the next POST of `type` only."""
        doc = fixture(name_or_doc) if isinstance(name_or_doc, str) else name_or_doc
        self.queued.setdefault(type, []).append(doc)

    def posts(self, type: str | None = None) -> list[dict]:
        return [b for m, _, _, b in self.requests if m == "POST" and (type is None or b.get("type") == type)]

    def _doc_for(self, type: str) -> dict:
        if self.queued.get(type):
            return copy.deepcopy(self.queued[type].pop(0))
        return copy.deepcopy(self.by_type.get(type, self.template))

    def rate_limit(self, headers: dict | None = None, body: bytes | str = b"") -> None:
        self.force_429 = (headers or {}, body.encode() if isinstance(body, str) else body)

    def tokens_seen(self) -> list:
        return [h.get("Authorization") for _, _, h, _ in self.requests]

    # --- server --------------------------------------------------------
    def _limits_doc(self, authed: bool) -> dict:
        doc = fixture("limits_auth" if authed else "limits_anon")
        create = doc["rateLimit"]["measurements"]["create"]
        if self.limit is not None:
            create["limit"] = self.limit
        create["remaining"] = self.remaining if self.remaining is not None else create["limit"]
        create["reset"] = 0
        if self.credits is not None and authed:
            doc["credits"] = {"remaining": self.credits}
        return doc

    def start(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, status, body=b"", headers=None):
                if not isinstance(body, bytes):
                    body = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                for k, v in (headers or {}).items():
                    self.send_header(k, str(v))
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                if not n:
                    return None
                return json.loads(self.rfile.read(n))

            def _auth(self):
                auth = self.headers.get("Authorization")
                if auth and outer.valid_token is not None and auth != f"Bearer {outer.valid_token}":
                    self._send(401, {"error": {"type": "unauthorized", "message": "Unauthorized."}})
                    return None
                return bool(auth)

            def do_GET(self):
                outer.requests.append(("GET", self.path, dict(self.headers), None))
                authed = self._auth()
                if authed is None:
                    return
                if self.path == "/v1/limits":
                    if outer.limits_status != 200:
                        return self._send(outer.limits_status, {"error": {"type": "boom"}})
                    return self._send(200, outer._limits_doc(authed))
                if self.path.startswith("/v1/measurements/"):
                    mid = self.path.rsplit("/", 1)[1]
                    doc = outer.measurements.get(mid)
                    if doc is None:
                        return self._send(404, {"error": {"type": "not_found"}})
                    n = outer._polls.get(mid, 0)
                    outer._polls[mid] = n + 1
                    if n < outer.in_progress_polls:
                        doc = dict(doc, status="in-progress", results=[])
                    return self._send(200, doc)
                self._send(404, {"error": {"type": "not_found"}})

            def do_POST(self):
                body = self._body()
                outer.requests.append(("POST", self.path, dict(self.headers), body))
                authed = self._auth()
                if authed is None:
                    return
                if self.path != "/v1/measurements":
                    return self._send(404, {})
                if outer.force_429 is not None:
                    headers, raw = outer.force_429
                    return self._send(429, raw, headers)
                if outer.rate_limit_after is not None and outer._accepted >= outer.rate_limit_after:
                    return self._send(429, b"")
                locations = body.get("locations")
                if isinstance(locations, str):  # probe reuse by measurement id
                    if outer.reuse_expired or locations not in outer.measurements:
                        return self._send(422, {"error": {"type": "no_probes_found",
                                                          "message": "No matching IPv4 probes available."}})
                doc = outer._doc_for(body.get("type"))
                outer._accepted += 1
                outer._counter += 1
                mid = f"fake{outer._counter:04d}"
                doc.update(id=mid, type=body.get("type"), target=body.get("target"))
                outer.measurements[mid] = doc
                cost = len(doc["results"])
                limit = outer.limit or (500 if authed else 250)
                remaining = (outer.remaining if outer.remaining is not None else limit) - cost
                outer.remaining = remaining
                self._send(202, {"id": mid, "probesCount": cost}, {
                    "Location": f"{outer.url}/v1/measurements/{mid}",
                    "x-request-cost": cost, "x-ratelimit-limit": limit,
                    "x-ratelimit-consumed": cost, "x-ratelimit-remaining": remaining,
                    "x-ratelimit-reset": 3600,
                })

            def log_message(self, *args):
                pass

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=lambda: self._httpd.serve_forever(poll_interval=0.01), daemon=True).start()

    @property
    def base_url(self) -> str:
        return self.url + "/v1"

    def stop(self):
        self._httpd.shutdown()
        self._httpd.server_close()
