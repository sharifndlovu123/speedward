"""Where each label on the results map goes (approved mockup page-19-map-labels.html).

Pure and deterministic: the same places give the same labels, with no browser measurement. A label
box is estimated from a per-character width table with a safe margin (wide enough for the system sans
fonts used when Inter is not installed), then each label tries a fixed list of spots around its dot.

- A spot is taken only when its box overlaps no other label, no dot or square, no leader line, and
  stays inside the map, so a label near an edge flips left or up instead of being cut off.
- Places closer than CLUSTER_PX share one label: two or three join their own texts with " · " and draw
  thin leader lines; more show only a count ("5 places"). A label never gets words of its own.
- When nothing fits, the label is dropped. Its dot keeps its title and the table lists the place.
- Labels are placed in rank order (blocked places first, then measured ones, then by name), so when
  space is short the most important ones are the ones shown.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import floor, hypot

# Places closer than this (map viewBox pixels at width 1000 and the base label size of 11) share one
# label. It grows with the label size, so on a phone, where labels are larger, more places merge.
CLUSTER_PX = 30.0
BASE_FS = 11.0
# Up to this many close places are named in one label; more are shown as a count.
MERGE_MAX = 3
SEP = " · "
# Text box geometry, as a share of the font size: ascent above and descent below the baseline.
ASC, DESC = 0.95, 0.3
HALO = 1.5  # half the paper halo drawn round each label (stroke-width 3)
GAP = 3.0  # space between a dot and its label, in map units at the base size
RINGS = 3  # spot distances tried: next to the dot, then two steps further out with a leader line
SAFETY = 1.08
# Work limits, so a very large posted report cannot keep /render or /export busy. Above this many
# places no place gets a label (the dots keep their titles and the table lists every place); and
# each set of labels stops trying spots after MAX_SPOT_CHECKS, dropping the labels still waiting.
MAX_LABELLED_PLACES = 200
MAX_SPOT_CHECKS = 20_000

Box = tuple[float, float, float, float]
Segment = tuple[float, float, float, float]

# Rough advance widths in em, rounded up for DejaVu Sans, the widest common fallback.
_NARROW = set("iljI.,:;'|!()[]`")
_WIDE = set("mwMW@%")
_DIRECTIONS = ((1, 0), (-1, 0), (0, -1), (0, 1), (1, -1), (-1, -1), (1, 1), (-1, 1))


@dataclass(frozen=True)
class Mark:
    """One thing to label: a place's dot (key = its row) or the Server square (a negative key)."""
    key: int
    x: float
    y: float
    r: float  # half the size of the dot or square, as drawn
    full: str
    short: str  # the same words without the reason (it stays in the title and the table)
    rank: tuple
    dim: bool = False  # no data: the label is drawn faded


@dataclass(frozen=True)
class Label:
    text: str
    x: float
    y: float  # baseline
    anchor: str  # SVG text-anchor: start, middle or end
    box: Box
    keys: tuple[int, ...]
    kind: str  # single, merged or count
    leaders: tuple[Segment, ...] = ()
    dim: bool = False


@dataclass(frozen=True)
class Placement:
    labels: tuple[Label, ...]
    dropped: tuple[int, ...]  # keys of places that have no label
    checks: int = 0  # spots tried, at most MAX_SPOT_CHECKS


class _Grid:
    """Boxes bucketed by square cells, so a query only looks at what is near it."""

    def __init__(self, cell: float, width: float, height: float):
        self.cell = max(cell, 1.0)
        # Cells outside the map are folded onto its edge, so a huge box costs no more than the map.
        self.last = (floor(width / self.cell) + 1, floor(height / self.cell) + 1)
        self.cells: dict[tuple[int, int], list[int]] = defaultdict(list)
        self.items: list[tuple[Box, object]] = []

    def _keys(self, box: Box):
        c, (mi, mj) = self.cell, self.last
        clamp = lambda v, top: min(max(v, -1), top)  # noqa: E731
        for i in range(clamp(floor(box[0] / c), mi), clamp(floor(box[2] / c), mi) + 1):
            for j in range(clamp(floor(box[1] / c), mj), clamp(floor(box[3] / c), mj) + 1):
                yield i, j

    def add(self, box: Box, item: object = None) -> None:
        n = len(self.items)
        self.items.append((box, item))
        for key in self._keys(box):
            self.cells[key].append(n)

    def near(self, box: Box) -> list[tuple[Box, object]]:
        found = sorted({n for key in self._keys(box) for n in self.cells.get(key, ())})
        return [self.items[n] for n in found]


def _char_em(c: str) -> float:
    if c == " ":
        return 0.34
    if c in _NARROW:
        return 0.34
    if c in _WIDE:
        return 1.0
    if c.isdigit():
        return 0.66
    if c.isupper():
        return 0.76
    if ord(c) < 0x250:
        return 0.64
    if ord(c) >= 0x2E80:  # CJK and other full-width scripts
        return 1.05
    return 0.78


def text_width(text: str, fs: float) -> float:
    """Estimated width of the label box, halo included."""
    return sum(_char_em(c) for c in text) * fs * SAFETY + 2 * HALO


def text_box(text: str, fs: float, x: float, y: float, anchor: str) -> Box:
    w = text_width(text, fs)
    if anchor == "start":
        x0 = x - HALO
    elif anchor == "end":
        x0 = x + HALO - w
    else:
        x0 = x - w / 2
    return (x0, y - ASC * fs - HALO, x0 + w, y + DESC * fs + HALO)


def box_hits(a: Box, b: Box) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def segment_hits(s: Segment, b: Box) -> bool:
    """Whether the line segment crosses the box (Liang-Barsky clipping)."""
    x0, y0, x1, y1 = s
    dx, dy = x1 - x0, y1 - y0
    lo, hi = 0.0, 1.0
    for p, q in ((-dx, x0 - b[0]), (dx, b[2] - x0), (-dy, y0 - b[1]), (dy, b[3] - y0)):
        if p == 0:
            if q <= 0:
                return False
            continue
        t = q / p
        if p < 0:
            lo = max(lo, t)
        else:
            hi = min(hi, t)
        if lo >= hi:
            return False
    return True


def _clusters(marks: list[Mark], close: float) -> list[list[Mark]]:
    """Closest pairs first, two groups join only while every place in the joined group is within
    `close` of every other one (no chains, so a continent never becomes one cluster by small steps).
    The groups come back in rank order of their best place, each listed west to east."""
    d = lambda a, b: hypot(a.x - b.x, a.y - b.y)  # noqa: E731
    group = {m.key: [m] for m in marks}
    grid = _Grid(close, max((m.x for m in marks), default=0), max((m.y for m in marks), default=0))
    for m in marks:
        grid.add((m.x, m.y, m.x, m.y), m)
    pairs = sorted({(d(a, b), min(a.key, b.key), max(a.key, b.key))
                    for a in marks for _, b in grid.near((a.x - close, a.y - close, a.x + close, a.y + close))
                    if b is not a and d(a, b) < close})
    for _, ka, kb in pairs:
        ga, gb = group[ka], group[kb]
        if ga is gb or any(d(a, b) >= close for a in ga for b in gb):
            continue
        ga.extend(gb)
        for m in gb:
            group[m.key] = ga
    unique = {id(g): g for g in group.values()}.values()
    out = [sorted(g, key=lambda o: (o.x, o.y, o.key)) for g in unique]
    return sorted(out, key=lambda g: min((m.rank, m.key) for m in g))


def _texts(group: list[Mark]) -> list[tuple[str, str]]:
    """(text, kind) to try, longest and fullest first; every word comes from the places themselves."""
    if len(group) == 1:
        m = group[0]
        return [(t, "single") for t in dict.fromkeys((m.full, m.short))]
    count = (f"{len(group)} places", "count")
    if len(group) > MERGE_MAX:
        return [count]
    names = dict.fromkeys((SEP.join(m.full for m in group), SEP.join(m.short for m in group)))
    return [(t, "merged") for t in names] + [count]


def _spots(anchor: Box, text: str, fs: float, k: float):
    """Candidate (x, baseline, text-anchor, box, ring) around the anchor box, nearest first."""
    w = text_width(text, fs)
    h = (ASC + DESC) * fs + 2 * HALO
    cx, cy = (anchor[0] + anchor[2]) / 2, (anchor[1] + anchor[3]) / 2
    for ring in range(RINGS):
        off = GAP * k + ring * h
        for dx, dy in _DIRECTIONS:
            diag = dx != 0 and dy != 0
            if dx > 0:
                x0 = anchor[2] + (off / 2 if diag else off)
            elif dx < 0:
                x0 = anchor[0] - (off / 2 if diag else off) - w
            else:
                x0 = cx - w / 2
            if dy > 0:
                y0 = anchor[3] + (off / 2 if diag else off)
            elif dy < 0:
                y0 = anchor[1] - (off / 2 if diag else off) - h
            else:
                y0 = cy - h / 2
            box = (x0, y0, x0 + w, y0 + h)
            anchor_word = "start" if dx > 0 else "end" if dx < 0 else "middle"
            x = x0 + HALO if dx > 0 else x0 + w - HALO if dx < 0 else cx
            yield x, y0 + HALO + ASC * fs, anchor_word, box, ring


def _leader(m: Mark, box: Box) -> Segment | None:
    """From the edge of the dot to the nearest point of the label box; None when they touch."""
    px = min(max(m.x, box[0]), box[2])
    py = min(max(m.y, box[1]), box[3])
    d = hypot(px - m.x, py - m.y)
    if d <= m.r + 1:
        return None
    return (m.x + (px - m.x) * m.r / d, m.y + (py - m.y) * m.r / d, px, py)


def place_labels(marks: list[Mark], obstacles: list[Box], width: float, height: float, fs: float,
                 fixed: list[Mark] = ()) -> Placement:
    """Labels for `fixed` (the Server, never merged) and then for the places, in rank order.

    obstacles: every dot and square on the map, and any text drawn outside this function.
    """
    k = width / 1000
    close = CLUSTER_PX * fs / BASE_FS  # fs is in map units, so this scales with the map width too
    by_key = {m.key: m for m in marks}
    unlabelled = sorted(by_key) if len(by_key) > MAX_LABELLED_PLACES else []
    groups = [[f] for f in sorted(fixed, key=lambda m: (m.rank, m.key))]
    if not unlabelled:
        groups += _clusters(sorted(by_key.values(), key=lambda m: m.key), close)
    h = (ASC + DESC) * fs + 2 * HALO
    cell = max(2 * h, 20 * k)

    still = _Grid(cell, width, height)  # dots, squares and fixed text
    for o in obstacles:
        still.add(o, o)
    labels = _Grid(cell, width, height)  # label boxes placed so far
    lines = _Grid(cell, width, height)  # their leader lines, by bounding box
    placed: list[Label] = []
    dropped: list[int] = list(unlabelled)
    checks = 0

    def seg_box(s: Segment) -> Box:
        return (min(s[0], s[2]), min(s[1], s[3]), max(s[0], s[2]), max(s[1], s[3]))

    def free(box: Box, segs: list[Segment], group: list[Mark]) -> bool:
        if box[0] < 0 or box[1] < 0 or box[2] > width or box[3] > height:
            return False
        if any(box_hits(box, o) for o, _ in still.near(box)):
            return False
        if any(box_hits(box, b) for b, _ in labels.near(box)):
            return False
        if any(segment_hits(s, box) for _, s in lines.near(box)):
            return False
        for s in segs:
            sb = seg_box(s)
            if any(segment_hits(s, b) for b, _ in labels.near(sb)):
                return False
            # A leader may cross its own cluster's dots, never another place's dot or a square.
            for o, _ in still.near(sb):
                if segment_hits(s, o) and not any(o[0] <= m.x <= o[2] and o[1] <= m.y <= o[3] for m in group):
                    return False
        return True

    def anchor_of(group: list[Mark]) -> Box:
        pad = [(m.x - m.r, m.y - m.r, m.x + m.r, m.y + m.r) for m in group]
        return (min(b[0] for b in pad), min(b[1] for b in pad), max(b[2] for b in pad), max(b[3] for b in pad))

    # The space right next to each place still to be labelled. A label first tries spots that keep
    # clear of it, so an early label does not take the only room a later one has.
    near = _Grid(cell, width, height)
    for n, a in enumerate(map(anchor_of, groups)):
        near.add((a[0] - h, a[1] - h, a[2] + h, a[3] + h), n)

    for i, group in enumerate(groups):
        anchor = anchor_of(group)
        label = None
        for text, kind in _texts(group):
            for keep_clear in (True, False):
                for x, y, word, box, ring in _spots(anchor, text, fs, k):
                    if checks >= MAX_SPOT_CHECKS:
                        break
                    checks += 1
                    if box[0] < 0 or box[1] < 0 or box[2] > width or box[3] > height:
                        continue
                    if keep_clear and any(n > i and box_hits(box, z) for z, n in near.near(box)):
                        continue
                    segs: list[Segment] = []
                    if len(group) > 1 or ring > 0:
                        segs = [s for s in (_leader(m, box) for m in group) if s is not None]
                    if free(box, segs, group):
                        label = Label(text, x, y, word, box, tuple(m.key for m in group), kind,
                                      tuple(segs), all(m.dim for m in group))
                        break
                if label is not None:
                    break
            if label is not None:
                break
        if label is None:
            dropped.extend(m.key for m in group if m.key >= 0)
            continue
        placed.append(label)
        labels.add(label.box)
        for s in label.leaders:
            lines.add(seg_box(s), s)
    return Placement(tuple(placed), tuple(sorted(dropped)), checks)
