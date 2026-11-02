"""Who can reach it: the "More checks" grid and its plain finding, from Globalping's probes only.

Pure functions over a ScanReport; no framework imports and no sentences (speedward/reports words them).
One row per place, one column for the main scan (HTTPS, or HTTP, over IPv4) and one per check that ran.
A cell is "reached" (measured), "blocked" (the site side refused, timed out, has no address or no open
port) or "no_data" (test failed or did not run: Globalping's side, never counted in any figure).

The finding counts only reached and blocked cells. With fewer than MIN_PLACES places that have any such
cell it says there are not enough places, and counts nothing.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from urllib.parse import urlsplit

from speedward.core.checks import PORT, REACH_CHECKS, RESOLVERS_CHECK
from speedward.core.models import LocationResult, ReachResult, ScanReport

MAIN = "main"  # the main scan's own first request (cold http over IPv4)
MIN_PLACES = 3
STATES = ("reached", "blocked", "no_data")
VERDICTS = ("too_few", "all_reached", "some_blocked")
NO_RESULT = "this check did not run for this place"
SITE_FREE = (RESOLVERS_CHECK,)  # checks that never contact the site itself


@dataclass
class Part:
    """One measurement behind a cell (the dns check has one per resolver)."""
    status: str
    reason: str
    ms: float | None = None
    resolver: str | None = None
    redirect: str | None = None
    http_status: int | None = None
    loss_pct: float | None = None
    probe: str | None = None


@dataclass
class Cell:
    column: str
    state: str
    parts: list[Part]


@dataclass
class GridRow:
    row: int  # index in report.locations (the map dot's data-row)
    place: str
    cells: list[Cell]
    worst: str  # "blocked" if any cell is blocked, else "reached" if any is, else "no_data"
    blocked_in: list[str]  # the columns where this place was blocked


@dataclass
class ColumnFinding:
    column: str
    judged: int  # places with a reached or blocked cell in this column
    blocked: int
    places: list[str]  # the blocked ones, in row order


@dataclass
class Grid:
    columns: list[str]
    scheme: str  # "https" or "http": how the main column is named
    port: int | None  # the TCP port the port check tried
    rows: list[GridRow]
    verdict: str
    judged: int  # places with at least one reached or blocked cell
    min_places: int
    findings: list[ColumnFinding] = field(default_factory=list)  # columns with a blocked cell
    nowhere: list[str] = field(default_factory=list)  # places blocked somewhere and reached nowhere

    def to_dict(self) -> dict:
        return asdict(self)


def _label(r: LocationResult) -> str:
    return ", ".join(p for p in (r.location.city, r.location.country) if p) or "unknown location"


def _state(parts: list[Part]) -> str:
    statuses = {p.status for p in parts}
    if "blocked" in statuses:
        return "blocked"
    if "measured" in statuses:
        return "reached"
    return "no_data"


def _main_part(r: LocationResult) -> Part:
    if r.status == "measured":
        return Part("measured", "", r.stages.total if r.stages is not None else None,
                    http_status=r.http_status, probe=None)
    return Part(r.status, r.reason, http_status=r.http_status if r.status == "blocked" else None)


def _part(x: ReachResult) -> Part:
    return Part(x.status, x.reason, x.ms, x.resolver, x.redirect, x.http_status, x.loss_pct, x.probe)


def reach_grid(report: ScanReport) -> Grid:
    results = report.reach or []
    columns = [MAIN] + [c for c in REACH_CHECKS if any(x.check == c for x in results)]
    port = next((x.port for x in results if x.check == PORT), None)
    scheme = (urlsplit(report.target).scheme or "https").lower()
    grouped: dict[tuple[int, str], list[Part]] = {}  # one pass: work grows with rows + results only
    for x in results:
        grouped.setdefault((x.row, x.check), []).append(_part(x))
    rows = []
    for i, r in enumerate(report.locations):
        cells = [Cell(MAIN, "", [_main_part(r)])]
        for c in columns[1:]:
            cells.append(Cell(c, "", grouped.get((i, c)) or [Part("not_run", NO_RESULT)]))
        for cell in cells:
            cell.state = _state(cell.parts)
        states = {c.state for c in cells}
        worst = "blocked" if "blocked" in states else "reached" if "reached" in states else "no_data"
        rows.append(GridRow(i, _label(r), cells, worst, [c.column for c in cells if c.state == "blocked"]))

    judged = sum(1 for g in rows if g.worst != "no_data")
    grid = Grid(columns, scheme if scheme in ("http", "https") else "https", port, rows, "too_few", judged,
                MIN_PLACES)
    if judged < MIN_PLACES:
        return grid
    for k, c in enumerate(columns):
        with_data = [g for g in rows if g.cells[k].state != "no_data"]
        blocked = [g.place for g in with_data if g.cells[k].state == "blocked"]
        if blocked:
            grid.findings.append(ColumnFinding(c, len(with_data), len(blocked), blocked))
    # "Cannot reach it at all": blocked in a check that contacts the site and reached in none of them. A
    # name that resolves (the dns check) does not mean the site can be reached.
    site = [k for k, c in enumerate(columns) if c not in SITE_FREE]
    grid.nowhere = [g.place for g in rows
                    if any(g.cells[k].state == "blocked" for k in site)
                    and all(g.cells[k].state != "reached" for k in site)]
    grid.verdict = "some_blocked" if grid.findings else "all_reached"
    return grid


def grid_from_dict(value, report: ScanReport) -> Grid:
    """A saved grid, accepted only if it is exactly what reach_grid gives for the report's own results.
    Anything else raises ValueError: a grid is never taken on trust."""
    from speedward.core.bottleneck import _same

    fresh = reach_grid(report)
    if not isinstance(value, dict) or not _same(value, fresh.to_dict()):
        raise ValueError("reach grid does not follow from the report's own results")
    return fresh
