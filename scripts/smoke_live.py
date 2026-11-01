#!/usr/bin/env python
"""Live smoke check: one real scan of a site YOU own, through Globalping.

    python scripts/smoke_live.py https://your-site.example

Needs an explicit URL (there is no default, so it never tests a site by accident). Uses
GLOBALPING_TOKEN from the environment or a .env in the current directory if set. Prints the
estimate first and stops if the scan will not fit in the remaining allowance. Uses real tests.
Exit codes: 0 ok, 1 an assertion failed, 2 could not run.
"""
from __future__ import annotations

import sys

from speedward.cli.main import _environment
from speedward.core.budget import Plan, check_budget, estimate_cost
from speedward.core.geo import geolocator_from_env
from speedward.core.globalping import GlobalpingClient, GlobalpingError
from speedward.core.locations import parse_locations
from speedward.core.runner import ScanError, run_scan
from speedward.core.target import normalise_target
from speedward.reports import render


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not argv[1].strip():
        print("Usage: python scripts/smoke_live.py URL  (a site you own or are authorised to test)",
              file=sys.stderr)
        return 2
    url = argv[1]
    print("Only test sites you own or are authorised to test.")
    try:
        normalise_target(url)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    env = _environment()
    client = GlobalpingClient(token=env.get("GLOBALPING_TOKEN") or None)
    plan = Plan(locations=parse_locations(None))
    try:
        allowance = client.allowance()
    except GlobalpingError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    cost = estimate_cost(plan)
    left = "unknown" if allowance.remaining is None else allowance.remaining
    print(f"Estimate: about {cost} tests. Left this hour: {left}.")
    verdict = check_budget(plan, allowance)
    if not verdict.fits:
        print(f"Stopping: {verdict.reason}", file=sys.stderr)
        return 2
    try:
        report = run_scan(url, plan, client, geo=geolocator_from_env(env))
    except (ScanError, GlobalpingError) as exc:
        print(f"The scan could not run: {exc}", file=sys.stderr)
        return 2

    problems = []
    html = render(report, "html")
    if 'class="sw-dot"' not in html:
        problems.append("the HTML report map has no location dots")
    for row in report.locations:
        if row.status != "measured" and (row.stages or row.repeat_stages or row.ping_ms is not None):
            problems.append(f"{row.location.city}: a {row.status} row carries a timing")
    for line in render(report, "md").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("| ") and len(cells) == 6 and cells[0] not in ("Location", "---"):
            if not cells[5].startswith(("HTTP", "measured")) and any(" ms" in c for c in cells[1:5]):
                problems.append(f"{cells[0]}: status '{cells[5]}' next to a timing")
    measured = sum(1 for r in report.locations if r.status == "measured")
    print(f"Locations: {len(report.locations)}, measured: {measured}.")
    if problems:
        for p in problems:
            print(f"FAIL: {p}", file=sys.stderr)
        return 1
    print("OK: map has dots and no unmeasured row shows a timing.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
