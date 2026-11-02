"""Map label placement (Task 23): pure, deterministic, never overlapping, never cut off, never invented."""
from speedward.core.locations import CATALOG, DEFAULT_LOCATIONS
from speedward.reports._labels import (
    CLUSTER_PX,
    MERGE_MAX,
    Mark,
    box_hits,
    place_labels,
    segment_hits,
    text_box,
    text_width,
)

W, H = 1000.0, 500.0
BY = {p.name: p for p in CATALOG}
DEFAULT_12 = [d["magic"] for d in DEFAULT_LOCATIONS]
EUROPE_7 = ["London", "Frankfurt", "Paris", "Stockholm", "Madrid", "Warsaw", "Istanbul"]
ALL_32 = [p.name for p in CATALOG]
SETS = {"default-12": DEFAULT_12, "europe-7": EUROPE_7, "all-32": ALL_32}
SIZES = (11.0, 15.0, 30.0)


def proj(name):
    p = BY[name]
    return (p.lon + 180) / 360 * W, (90 - p.lat) / 180 * H


def marks(names, times=(38, 120, 260, 410, 920), blocked=()):
    out = []
    for i, n in enumerate(names):
        x, y = proj(n)
        if n in blocked:
            full, short = f"{n} blocked: connection timed out", f"{n} blocked"
            rank = (0, n.casefold(), i)
        else:
            full = short = f"{n} {times[i % len(times)]}ms"
            rank = (1, n.casefold(), i)
        out.append(Mark(key=i, x=x, y=y, r=5.0, full=full, short=short, rank=rank))
    return out


def dot_boxes(ms):
    return [(m.x - m.r, m.y - m.r, m.x + m.r, m.y + m.r) for m in ms]


def run(names, fs=11.0, blocked=(), server=None):
    ms = marks(names, blocked=blocked)
    obstacles = dot_boxes(ms)
    fixed = []
    if server is not None:
        sx, sy = server
        obstacles.append((sx - 7, sy - 7, sx + 7, sy + 7))
        fixed.append(Mark(key=-1, x=sx, y=sy, r=7.0, full="Server (manual setting)",
                          short="Server (manual setting)", rank=(-1,)))
    return ms, obstacles, place_labels(ms, obstacles, W, H, fs, fixed=fixed)


def check_clean(ms, obstacles, placed):
    labels = placed.labels
    for i, a in enumerate(labels):
        x0, y0, x1, y1 = a.box
        assert x0 >= 0 and y0 >= 0 and x1 <= W and y1 <= H, f"{a.text} is cut off"
        for b in labels[i + 1:]:
            assert not box_hits(a.box, b.box), f"{a.text} overlaps {b.text}"
        for o in obstacles:
            assert not box_hits(a.box, o), f"{a.text} covers a dot or square"
        for b in labels:
            for seg in b.leaders:
                if b is not a:
                    assert not segment_hits(seg, a.box), f"a leader of {b.text} crosses {a.text}"


def test_text_width_grows_with_text_and_size():
    assert text_width("Tokyo 920ms", 11) > text_width("Tokyo", 11) > 0
    assert text_width("Tokyo", 22) > 2 * text_width("Tokyo", 11) - 4
    assert text_width("東京", 11) > text_width("ab", 11)


def test_text_box_follows_the_anchor():
    start = text_box("Tokyo", 11, 100, 50, "start")
    end = text_box("Tokyo", 11, 100, 50, "end")
    mid = text_box("Tokyo", 11, 100, 50, "middle")
    assert start[0] < 100 < start[2] and start[0] > 95
    assert end[2] > 100 > end[0] and end[2] < 105
    assert abs((mid[0] + mid[2]) / 2 - 100) < 1e-9
    assert start[1] < 50 < start[3]


def test_segment_hits():
    assert segment_hits((0, 0, 10, 10), (4, 4, 6, 6))
    assert not segment_hits((0, 0, 10, 0), (4, 4, 6, 6))
    assert segment_hits((0, 5, 10, 5), (4, 4, 6, 6))


def test_every_set_and_size_has_no_overlap_no_cover_and_nothing_cut_off():
    for name, names in SETS.items():
        for fs in SIZES:
            for blocked in ((), ("Frankfurt", "Lagos")):
                ms, obstacles, placed = run(names, fs, blocked, server=proj("Frankfurt"))
                check_clean(ms, obstacles, placed)
                assert placed.labels, (name, fs)


def test_at_the_base_size_every_place_is_labelled_in_the_three_sets():
    for name, names in SETS.items():
        ms, _, placed = run(names, 11.0, server=proj("Frankfurt"))
        assert placed.dropped == (), name
        shown = sorted(k for lab in placed.labels for k in lab.keys if k >= 0)
        assert shown == list(range(len(names))), name


def test_server_label_is_placed_first_and_does_not_run_into_frankfurt():
    _, _, placed = run(DEFAULT_12, 11.0, server=proj("Frankfurt"))
    server = [lab for lab in placed.labels if lab.keys == (-1,)]
    assert server and server[0].text == "Server (manual setting)"
    assert placed.labels[0].keys == (-1,)


def test_output_is_deterministic():
    a = run(ALL_32, 11.0, ("Lagos",), server=proj("Frankfurt"))[2]
    b = run(ALL_32, 11.0, ("Lagos",), server=proj("Frankfurt"))[2]
    assert a == b
    shuffled = list(reversed(marks(ALL_32, blocked=("Lagos",))))
    ms = marks(ALL_32, blocked=("Lagos",))
    assert place_labels(shuffled, dot_boxes(ms), W, H, 11.0) == place_labels(ms, dot_boxes(ms), W, H, 11.0)


def test_sydney_and_auckland_are_flipped_inside_the_right_edge():
    _, _, placed = run(ALL_32, 11.0)
    for lab in placed.labels:
        assert lab.box[2] <= W
    east = [lab for lab in placed.labels if "Auckland" in lab.text]
    assert east and east[0].anchor in ("end", "middle")


def test_two_close_places_share_one_label_with_leader_lines():
    names = ["London", "Paris"]  # about 10 map px apart
    _, _, placed = run(names)
    assert len(placed.labels) == 1
    lab = placed.labels[0]
    assert lab.kind == "merged" and lab.keys == (0, 1)
    assert lab.text == "London 38ms · Paris 120ms"  # west to east, each place's own text
    assert len(lab.leaders) == 2


def test_cluster_threshold_is_the_named_constant():
    a = Mark(key=0, x=500, y=200, r=4, full="A 1ms", short="A 1ms", rank=(1, "a", 0))
    near = Mark(key=1, x=500 + CLUSTER_PX - 0.5, y=200, r=4, full="B 2ms", short="B 2ms", rank=(1, "b", 1))
    far = Mark(key=1, x=500 + CLUSTER_PX + 0.5, y=200, r=4, full="B 2ms", short="B 2ms", rank=(1, "b", 1))
    boxes = lambda ms: [(m.x - 4, m.y - 4, m.x + 4, m.y + 4) for m in ms]  # noqa: E731
    merged = place_labels([a, near], boxes([a, near]), W, H, 11.0)
    assert [lab.kind for lab in merged.labels] == ["merged"]
    apart = place_labels([a, far], boxes([a, far]), W, H, 11.0)
    assert sorted(lab.kind for lab in apart.labels) == ["single", "single"]


def test_more_than_three_close_places_show_a_count_not_a_region():
    ms = [Mark(key=i, x=500 + 4 * i, y=200, r=3, full=f"P{i} 1ms", short=f"P{i} 1ms", rank=(1, f"p{i}", i))
          for i in range(MERGE_MAX + 2)]
    placed = place_labels(ms, [(m.x - 3, m.y - 3, m.x + 3, m.y + 3) for m in ms], W, H, 11.0)
    assert len(placed.labels) == 1
    lab = placed.labels[0]
    assert lab.kind == "count" and lab.text == f"{MERGE_MAX + 2} places"
    assert lab.keys == tuple(range(MERGE_MAX + 2))


def test_three_close_places_fall_back_to_a_count_when_names_do_not_fit():
    # A narrow map: three names side by side cannot fit, the count can.
    ms = [Mark(key=i, x=60 + 5 * i, y=30, r=3, full=f"Placename{i} 100ms", short=f"Placename{i} 100ms",
               rank=(1, f"p{i}", i)) for i in range(3)]
    placed = place_labels(ms, [(m.x - 3, m.y - 3, m.x + 3, m.y + 3) for m in ms], 160, 60, 11.0)
    assert [lab.text for lab in placed.labels] == ["3 places"]


def test_a_long_label_falls_back_to_its_short_text_never_to_new_words():
    m = Mark(key=0, x=80, y=20, r=3, full="Frankfurt blocked: connection timed out",
             short="Frankfurt blocked", rank=(0, "frankfurt", 0))
    placed = place_labels([m], [(77, 17, 83, 23)], 240, 60, 11.0)
    assert [lab.text for lab in placed.labels] == ["Frankfurt blocked"]


def test_a_label_that_cannot_be_placed_is_dropped_not_overlapped():
    m = Mark(key=0, x=10, y=10, r=3, full="Somewhere 100ms", short="Somewhere 100ms", rank=(1, "s", 0))
    placed = place_labels([m], [(7, 7, 13, 13)], 40, 20, 11.0)
    assert placed.labels == () and placed.dropped == (0,)


def test_when_space_is_short_blocked_places_are_labelled_first_then_by_name():
    # Two places too far apart to merge, on a strip where only the space between them is free.
    a = Mark(key=0, x=10, y=17, r=2, full="Zurich 10ms", short="Zurich 10ms", rank=(1, "zurich", 0))
    b = Mark(key=1, x=110, y=17, r=2, full="Oslo blocked", short="Oslo blocked", rank=(0, "oslo", 1))
    placed = place_labels([a, b], [(8, 15, 12, 19), (108, 15, 112, 19)], 120, 34, 11.0)
    assert [lab.keys for lab in placed.labels] == [(1,)] and placed.dropped == (0,)


def test_larger_sizes_merge_more_and_show_fewer_labels():
    _, _, small = run(ALL_32, 11.0)
    _, _, big = run(ALL_32, 30.0)
    assert len(big.labels) < len(small.labels)


def test_no_data_labels_are_dimmed_only_when_every_member_has_no_data():
    m = Mark(key=0, x=500, y=200, r=3, full="Oslo no data", short="Oslo no data", rank=(2, "oslo", 0), dim=True)
    placed = place_labels([m], [(497, 197, 503, 203)], W, H, 11.0)
    assert placed.labels[0].dim


# --- bounded work: a large posted report cannot keep the server busy ----------------------------

def many(n, stacked):
    import random
    rng = random.Random(1)
    out = []
    for i in range(n):
        x, y = (500 + i * 1e-3, 120.0) if stacked else (rng.uniform(5, 995), rng.uniform(5, 495))
        out.append(Mark(key=i, x=x, y=y, r=5.5, full=f"Place{i} {i}ms", short=f"Place{i} {i}ms", rank=(1, f"p{i}", i)))
    return out


def test_spot_checks_are_capped_however_the_places_are_spread():
    from speedward.reports._labels import MAX_LABELLED_PLACES, MAX_SPOT_CHECKS
    for stacked in (False, True):
        ms = many(MAX_LABELLED_PLACES, stacked)
        placed = place_labels(ms, dot_boxes(ms), W, H, 11.0)
        assert 0 < placed.checks <= MAX_SPOT_CHECKS
        shown = {k for lab in placed.labels for k in lab.keys}
        assert shown | set(placed.dropped) == set(range(MAX_LABELLED_PLACES))  # each place is either


def test_above_the_place_limit_no_place_label_is_tried_but_the_server_still_is():
    from speedward.reports._labels import MAX_LABELLED_PLACES
    ms = many(MAX_LABELLED_PLACES + 1, False)
    server = Mark(key=-1, x=500, y=250, r=7, full="Server (manual setting)", short="Server (manual setting)",
                  rank=(-1,))
    placed = place_labels(ms, [], W, H, 11.0, fixed=[server])  # room for the Server label
    assert [lab.keys for lab in placed.labels] == [(-1,)]
    assert placed.dropped == tuple(range(MAX_LABELLED_PLACES + 1))
    assert placed.checks <= 50


def test_a_very_long_name_costs_no_more_than_the_map():
    m = Mark(key=0, x=500, y=250, r=4, full="x" * 100_000, short="x" * 100_000, rank=(1, "x", 0))
    placed = place_labels([m], [(496, 246, 504, 254)], W, H, 11.0)
    assert placed.labels == () and placed.dropped == (0,)


# --- review fixes -------------------------------------------------------------------------------

def test_marks_and_obstacles_off_the_map_or_not_finite_get_no_label_and_do_not_raise():
    nan, inf = float("nan"), float("inf")
    bad = [Mark(key=i, x=x, y=y, r=4, full=f"P{i} 1ms", short=f"P{i} 1ms", rank=(1, f"p{i}", i))
           for i, (x, y) in enumerate([(nan, 10), (inf, 10), (1e308, 10), (-1e308, 10), (10, -inf), (500, 1e300)])]
    good = Mark(key=99, x=500, y=250, r=4, full="Good 1ms", short="Good 1ms", rank=(1, "good", 99))
    obstacles = [(nan, 0, 1, 1), (-inf, -inf, inf, inf), (496, 246, 504, 254)]
    placed = place_labels(bad + [good], obstacles, W, H, 11.0)
    assert [lab.keys for lab in placed.labels] == [(99,)]
    assert placed.dropped == tuple(range(len(bad)))
    for width in (float("nan"), float("inf"), 1e308, 10**300):
        place_labels(bad + [good], obstacles, width, H, 11.0)  # never raises
    assert place_labels([good], [], float("nan"), H, 11.0).labels == ()


def test_grouping_work_is_bounded_for_two_dense_blobs_within_reach():
    from speedward.reports._labels import MAX_LABELLED_PLACES, _clusters
    n = MAX_LABELLED_PLACES
    # Two blobs of tightly packed places, just close enough that every cross pair is a candidate.
    ms = [Mark(key=i, x=(100 if i % 2 else 100 + CLUSTER_PX * 0.9) + (i // 2) * 1e-3, y=100.0, r=1,
               full="p", short="p", rank=(1, "p", i)) for i in range(n)]
    stats: dict[str, int] = {}
    groups = _clusters(ms, CLUSTER_PX, W, H, stats)
    assert sorted(len(g) for g in groups) == [n]  # within reach: one group
    pairs = n * (n - 1) // 2
    assert stats["ops"] <= pairs + 16 * n  # one constant-time check per pair, plus moves


def test_two_blobs_out_of_reach_stay_apart_with_bounded_work():
    from speedward.reports._labels import _clusters
    ms = [Mark(key=i, x=(100 if i % 2 else 100 + CLUSTER_PX * 0.8) + (i // 2) * 0.1, y=100.0, r=1,
               full="p", short="p", rank=(1, "p", i)) for i in range(200)]
    stats: dict[str, int] = {}
    groups = _clusters(ms, CLUSTER_PX, W, H, stats)
    assert all(max(m.x for m in g) - min(m.x for m in g) < CLUSTER_PX for g in groups)
    assert stats["ops"] <= 200 * 199 // 2 + 16 * 200
