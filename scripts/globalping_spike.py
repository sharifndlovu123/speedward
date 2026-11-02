"""Throwaway spike: record the real Globalping API shape. Not part of the package.

Usage: python scripts/globalping_spike.py TARGET_URL OUT_DIR
Reads GLOBALPING_TOKEN from the environment (never printed or written).
Makes about 10 measurements, so it fits the unauthenticated 250/hour limit.
"""
import json
import os
import sys
import time
from urllib.parse import urlsplit

import httpx

API = "https://api.globalping.io/v1"
LOCATIONS = [{"magic": m} for m in ("Berlin", "Tokyo", "aws-eu-west-1", "eyeball-network+Brazil")]


def save(out, name, resp):
    # Only response headers are stored; request headers (which carry the token) never are.
    body = resp.json() if resp.content else None
    with open(os.path.join(out, name + ".json"), "w") as f:
        json.dump({"status": resp.status_code, "headers": dict(resp.headers), "body": body}, f, indent=2)
    return body


def post(client, out, name, payload):
    r = client.post(f"{API}/measurements", json=payload)
    body = save(out, name + ".post", r)
    print(name, "POST", r.status_code)
    return r, body


def poll(client, out, name, mid):
    for _ in range(60):
        r = client.get(f"{API}/measurements/{mid}")
        body = r.json()
        if body.get("status") != "in-progress":
            save(out, name + ".result", r)
            print(name, "finished", body.get("status"))
            return body
        time.sleep(1)
    print(name, "timed out polling")


def main():
    target, out = sys.argv[1], sys.argv[2]
    os.makedirs(out, exist_ok=True)
    host = urlsplit(target).hostname
    path = urlsplit(target).path or "/"
    headers = {"User-Agent": "speedward-spike"}
    token = os.environ.get("GLOBALPING_TOKEN")
    authed = dict(headers, Authorization=f"Bearer {token}") if token else None
    with httpx.Client(headers=headers, timeout=30) as client:
        r = client.get(f"{API}/limits")
        save(out, "limits.unauth", r)
        print("limits GET", r.status_code)
        http_opts = {"request": {"method": "GET", "path": path}, "protocol": "HTTPS"}
        specs = {
            "http": ({"type": "http", "target": host, "locations": LOCATIONS, "measurementOptions": http_opts}),
            "ping": ({"type": "ping", "target": host, "locations": LOCATIONS}),
            "traceroute": ({"type": "traceroute", "target": host, "locations": LOCATIONS[:2]}),
            "mtr": ({"type": "mtr", "target": host, "locations": LOCATIONS[:2]}),
            "dns": ({"type": "dns", "target": host, "locations": LOCATIONS}),
        }
        first_id = None
        for name, payload in specs.items():
            r, body = post(client, out, name, payload)
            if r.status_code == 202:
                poll(client, out, name, body["id"])
                if name == "http":
                    first_id = body["id"]
        if first_id:  # repeat from the same probes
            payload = {"type": "http", "target": host, "locations": first_id,
                       "measurementOptions": http_opts}
            r, body = post(client, out, "http_repeat", payload)
            if r.status_code == 202:
                poll(client, out, "http_repeat", body["id"])
        # one call that follows redirects, against http:// which redirects to https://
        payload = {"type": "http", "target": host, "locations": LOCATIONS[:1],
                   "measurementOptions": {"request": {"method": "GET", "path": path}, "protocol": "HTTP"}}
        r, body = post(client, out, "http_plain", payload)
        if r.status_code == 202:
            poll(client, out, "http_plain", body["id"])
    if authed:
        with httpx.Client(headers=authed, timeout=30) as c2:
            r = c2.get(f"{API}/limits")
            save(out, "limits.auth", r)
            print("limits (authenticated) GET", r.status_code)
    else:
        print("GLOBALPING_TOKEN not set: authenticated calls skipped")


if __name__ == "__main__":
    main()
