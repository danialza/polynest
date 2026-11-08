#!/usr/bin/env python3
"""
polynest - pack convex polygons onto a rectangular sheet with as little
waste as possible.

The bundled instance is a 200 x 100 sheet and ten shapes (two squares, two
rectangles, three equilateral triangles, two isosceles triangles and a
trapezium), each used at least once. Two solvers live in this one file:

  structured (default)  exploits the instance's 5-unit modularity:
                        exact-cover tiling + triangular-lattice substitution
                        + continuous pose search. Deterministic, 98.51 %.
  generic (--generic)   assumes nothing about the shapes: Bottom-Left-Fill
                        with exact SAT collision tests, slide compaction and
                        a parallel hill-climbing search. Any convex polygon.

Usage:
    python polynest.py                       # structured solver
    python polynest.py --generic             # generic solver
    python polynest.py --generic --budget 30 --workers 4
    python polynest.py --json layout.json    # also export the layout

Only the Python standard library is used for the optimisation itself;
matplotlib draws the result.
"""

import argparse
import math
import random
import time
from concurrent.futures import ProcessPoolExecutor

EPS = 1e-6                              # touching is allowed, overlap is not
CANVAS_W, CANVAS_H = 200.0, 100.0
H5 = 5.0 * math.sqrt(3.0) / 2.0        # height of one side-5 lattice row


# ---- geometry (a polygon is a list of (x, y) points) ---------------------

def poly_area(poly):
    s = 0.0
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def translate(poly, dx, dy):
    return [(x + dx, y + dy) for x, y in poly]


def rotate_about(poly, deg, cx, cy):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return [(cx + (x - cx) * c - (y - cy) * s,
             cy + (x - cx) * s + (y - cy) * c) for x, y in poly]


def rotate_origin(poly, deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return [(x * c - y * s, x * s + y * c) for x, y in poly]


def mirror(poly):
    return [(-x, y) for x, y in poly]


def bbox(poly):
    xs = [x for x, _ in poly]
    ys = [y for _, y in poly]
    return min(xs), min(ys), max(xs), max(ys)


def group_bbox(polys):
    xs = [x for p in polys for x, _ in p]
    ys = [y for p in polys for _, y in p]
    return min(xs), min(ys), max(xs), max(ys)


def normalise(polys):
    x0, y0, _, _ = group_bbox(polys)
    return [translate(p, -x0, -y0) for p in polys]


def centroid(poly):
    return (sum(x for x, _ in poly) / len(poly),
            sum(y for _, y in poly) / len(poly))


def sat_overlap(P, Q):
    """Separating Axis Theorem: True only if convex P and Q really
    penetrate (deeper than EPS).  Touching edges/vertices are allowed."""
    for poly in (P, Q):
        n = len(poly)
        for i in range(n):
            x1, y1 = poly[i]
            x2, y2 = poly[(i + 1) % n]
            ax, ay = y1 - y2, x2 - x1
            ln = math.hypot(ax, ay)
            if ln < 1e-12:
                continue
            pa = [(ax * x + ay * y) / ln for x, y in P]
            qa = [(ax * x + ay * y) / ln for x, y in Q]
            if min(qa) - max(pa) >= -EPS or min(pa) - max(qa) >= -EPS:
                return False
    return True


def point_in_convex(px, py, poly):
    sign = 0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        cr = (x2 - x1) * (py - y1) - (y2 - y1) * (px - x1)
        if abs(cr) < 1e-9:
            continue
        s = 1 if cr > 0 else -1
        if sign == 0:
            sign = s
        elif s != sign:
            return False
    return True


# ---- the ten shapes ------------------------------------------------------

def rect(w, h):
    return [(0, 0), (w, 0), (w, h), (0, h)]


def equilateral(side):
    return [(0, 0), (side, 0), (side / 2.0, side * math.sqrt(3) / 2.0)]


def isosceles(base, leg):
    h = math.sqrt(leg * leg - (base / 2.0) ** 2)
    return [(0, 0), (base, 0), (base / 2.0, h)]


def trapezium(top, bottom, height):
    off = (bottom - top) / 2.0
    return [(0, 0), (bottom, 0), (bottom - off, height), (off, height)]


# The shape table: the ONLY place the shapes are defined.  Add a row here
# (or pass --shapes file.json) and areas / side lengths follow automatically.
SHAPES = {
    "square 20x20": rect(20, 20), "square 25x25": rect(25, 25),
    "rectangle 30x15": rect(30, 15), "rectangle 35x25": rect(35, 25),
    "eq. triangle s=5": equilateral(5), "eq. triangle s=30": equilateral(30),
    "eq. triangle s=40": equilateral(40),
    "iso. triangle 20/25": isosceles(20, 25),
    "iso. triangle 30/35": isosceles(30, 35),
    "trapezium 20/30 h20": trapezium(20, 30, 20),
}
BUNDLED = set(SHAPES)                  # the instance the structured solver knows


def side_lengths(poly):
    return sorted(math.dist(poly[i], poly[(i + 1) % len(poly)])
                  for i in range(len(poly)))


def is_convex(poly):
    signs = set()
    n = len(poly)
    for i in range(n):
        (x1, y1), (x2, y2), (x3, y3) = poly[i], poly[(i + 1) % n], poly[(i + 2) % n]
        cr = (x2 - x1) * (y3 - y2) - (y2 - y1) * (x3 - x2)
        if abs(cr) > 1e-9:
            signs.add(cr > 0)
    return len(signs) == 1


def is_rectangle(poly):
    if len(poly) != 4:
        return False
    for i in range(4):
        (x1, y1), (x2, y2), (x3, y3) = poly[i - 1], poly[i], poly[(i + 1) % 4]
        if abs((x1 - x2) * (x3 - x2) + (y1 - y2) * (y3 - y2)) > 1e-9:
            return False
    return True


def derive_specs():
    """Area and side lengths of every shape - what validate() checks against."""
    global AREAS, SIDES
    AREAS = {n: poly_area(p) for n, p in SHAPES.items()}
    SIDES = {n: side_lengths(p) for n, p in SHAPES.items()}


derive_specs()


# ---- shared checking and drawing (used by both solvers) ------------------

def validate(placed):
    """Re-check the layout from scratch: exact sizes, inside the canvas,
    no overlaps, every shape used at least once.  `placed` is a list of
    (name, polygon) pairs.  Returns the piece counts."""
    counts = {}
    for name, poly in placed:
        counts[name] = counts.get(name, 0) + 1
        got = sorted(math.dist(poly[i], poly[(i + 1) % len(poly)])
                     for i in range(len(poly)))
        assert max(abs(a - b) for a, b in zip(got, sorted(SIDES[name]))) < 1e-6
        assert abs(poly_area(poly) - AREAS[name]) < 1e-6
        for x, y in poly:
            assert -EPS <= x <= CANVAS_W + EPS and -EPS <= y <= CANVAS_H + EPS
    boxes = [bbox(p) for _, p in placed]
    for i in range(len(placed)):
        ax0, ay0, ax1, ay1 = boxes[i]
        for j in range(i + 1, len(placed)):
            bx0, by0, bx1, by1 = boxes[j]
            if bx0 >= ax1 - EPS or ax0 >= bx1 - EPS:
                continue
            if by0 >= ay1 - EPS or ay0 >= by1 - EPS:
                continue
            assert not sat_overlap(placed[i][1], placed[j][1]), \
                f"overlap: {placed[i][0]} / {placed[j][0]}"
    assert set(counts) == set(SIDES), "every shape type must be used at least once"
    return counts


def plot(placed, counts, covered, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon as MplPoly

    cmap = plt.get_cmap("tab10")
    colors = {n: cmap(i % 10) for i, n in enumerate(sorted(counts))}
    fig, ax = plt.subplots(figsize=(12, 6.5))
    ax.add_patch(MplPoly([(0, 0), (CANVAS_W, 0), (CANVAS_W, CANVAS_H),
                          (0, CANVAS_H)], fill=False, edgecolor="black",
                         linewidth=2))
    for name, poly in placed:
        ax.add_patch(MplPoly(poly, closed=True, linewidth=0.5,
                             facecolor=colors[name], edgecolor="black",
                             alpha=0.9))
    handles = [plt.Rectangle((0, 0), 1, 1, fc=colors[n]) for n in sorted(counts)]
    labels = [f"{n}  (x{counts[n]})" for n in sorted(counts)]
    ax.legend(handles, labels, loc="center left",
              bbox_to_anchor=(1.01, 0.5), fontsize=9)
    sheet = CANVAS_W * CANVAS_H
    waste = sheet - covered
    ax.set_title(f"Sheet {CANVAS_W:g} x {CANVAS_H:g}   |   covered {covered:.1f} "
                 f"({100 * covered / sheet:.2f}%)   |   waste {waste:.1f} "
                 f"({100 * waste / sheet:.2f}%)   |   sheet : waste = "
                 f"{sheet / waste:.1f} : 1")
    ax.set_xlim(-2, CANVAS_W + 2)
    ax.set_ylim(-2, CANVAS_H + 2)
    ax.set_aspect("equal")
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    print(f"diagram saved to {path}")


# ==========================================================================
# STRUCTURED SOLVER  (default)  -  reaches 98.51 %
#
# Idea: all sizes are multiples of 5, and a triangle of side 30/40 equals
# exactly 36/64 small side-5 triangles.  So we tile the boxes exactly, drop
# the big triangles onto a small-triangle grid with no waste, and search
# for good spots for the two odd isosceles triangles.
# ==========================================================================

RECT_CELLS = {                         # rectangle sizes in 5-unit cells
    "square 20x20": (4, 4), "square 25x25": (5, 5),
    "rectangle 30x15": (6, 3), "rectangle 35x25": (7, 5),
}
BAND_W = 70.0                          # width of the triangle band
BAND_ROWS = 8                          # its height in lattice rows
POCKET = (70.0, 20.0, 125.0, 40.0)     # box kept free for the trapeziums


def exact_cover(width_cells, height_cells, blocked, rng, node_limit=1_500_000):
    """Tile every free cell of a grid with the four rectangle types (both
    orientations), leaving no gaps.  Depth-first search on the first free
    cell; piece order is shuffled per seed.  Returns (name, x, y, w, h)
    placements in cell units, or None."""
    total = width_cells * height_cells
    full = (1 << total) - 1
    mask0 = 0
    for cx, cy in blocked:
        mask0 |= 1 << (cy * width_cells + cx)

    pieces = []
    for name, (w, h) in RECT_CELLS.items():
        for pw, ph in {(w, h), (h, w)}:
            pieces.append((name, pw, ph))

    def piece_mask(x, y, w, h):
        m = 0
        for yy in range(y, y + h):
            m |= ((1 << w) - 1) << (yy * width_cells + x)
        return m

    nodes = 0

    def recurse(mask, answer):
        nonlocal nodes
        nodes += 1
        if nodes > node_limit:
            return None
        if mask == full:
            return list(answer)
        first = ((~mask) & full & -((~mask) & full)).bit_length() - 1
        x0, y0 = first % width_cells, first // width_cells
        options = []
        for name, w, h in pieces:
            if x0 + w <= width_cells and y0 + h <= height_cells:
                cand = piece_mask(x0, y0, w, h)
                if not (mask & cand):
                    options.append((-(w * h) + rng.random() * 0.5,
                                    name, w, h, cand))
        for _, name, w, h, cand in sorted(options, key=lambda o: o[0]):
            answer.append((name, x0, y0, w, h))
            got = recurse(mask | cand, answer)
            if got is not None:
                return got
            answer.pop()
        return None

    return recurse(mask0, [])


def lattice_cells(cols, rows):
    """All side-5 up/down triangles of a cols x rows band."""
    pts = [[(2.5 * (j % 2) + 5.0 * i, H5 * j)
            for i in range(cols + 1 - j % 2)] for j in range(rows + 1)]
    cells = []
    for j in range(rows):
        lo, up = pts[j], pts[j + 1]
        if j % 2 == 0:
            cells += [[lo[i], lo[i + 1], up[i]] for i in range(cols)]
            cells += [[up[i], up[i + 1], lo[i + 1]] for i in range(cols - 1)]
        else:
            cells += [[up[i], up[i + 1], lo[i]] for i in range(cols)]
            cells += [[lo[i], lo[i + 1], up[i + 1]] for i in range(cols - 1)]
    return cells


def substitution_anchors(side, cols, rows):
    """Every lattice-aligned pose (point-up and point-down) of an
    equilateral triangle whose side is a multiple of 5."""
    k = round(side / 5)
    out = []
    for j in range(rows - k + 1):
        x_shift = 2.5 * (j % 2)
        i = 0
        while x_shift + 5 * i + side <= 5 * cols + 1e-9:
            x0 = x_shift + 5 * i
            out.append([(x0, H5 * j), (x0 + side, H5 * j),
                        (x0 + side / 2, H5 * (j + k))])
            i += 1
    for j in range(rows - k + 1):
        top = j + k
        x_shift = 2.5 * (top % 2)
        i = 0
        while x_shift + 5 * i + side <= 5 * cols + 1e-9:
            x0 = x_shift + 5 * i
            out.append([(x0, H5 * top), (x0 + side, H5 * top),
                        (x0 + side / 2, H5 * j)])
            i += 1
    return out


def covered_cells(tri, cells):
    """Indices of lattice cells whose centre is inside `tri`."""
    out = set()
    x0, y0, x1, y1 = bbox(tri)
    for idx, cell in enumerate(cells):
        cx, cy = centroid(cell)
        if x0 - 1 <= cx <= x1 + 1 and y0 - 1 <= cy <= y1 + 1 \
                and point_in_convex(cx, cy, tri):
            out.add(idx)
    return out


def blocked_mask(poly, cells, cell_boxes):
    """Bit mask of lattice cells that `poly` overlaps."""
    m = 0
    px0, py0, px1, py1 = bbox(poly)
    for idx, (cell, (cx0, cy0, cx1, cy1)) in enumerate(zip(cells, cell_boxes)):
        if cx0 >= px1 - EPS or px0 >= cx1 - EPS:
            continue
        if cy0 >= py1 - EPS or py0 >= cy1 - EPS:
            continue
        if sat_overlap(poly, cell):
            m |= 1 << idx
    return m


def sample_pose(proto, rng, region):
    """A random rigid pose of `proto` inside the region box."""
    rx0, ry0, rx1, ry1 = region
    rot = rotate_about(proto, rng.uniform(0, 360), *centroid(proto))
    x0, y0, x1, y1 = bbox(rot)
    if x1 - x0 > rx1 - rx0 or y1 - y0 > ry1 - ry0:
        return None
    return translate(rot, rng.uniform(rx0 - x0, rx1 - x1),
                     rng.uniform(ry0 - y0, ry1 - y1))


def sample_pose_at(proto, rng, targets, region):
    """A random pose whose centre lands on a random free-cell centre (plus
    jitter).  This finds the tight pockets that uniform sampling misses."""
    rx0, ry0, rx1, ry1 = region
    tx, ty = targets[rng.randrange(len(targets))]
    tx += rng.gauss(0, 2.0)
    ty += rng.gauss(0, 2.0)
    cx, cy = centroid(proto)
    pose = translate(rotate_about(proto, rng.uniform(0, 360), cx, cy),
                     tx - cx, ty - cy)
    x0, y0, x1, y1 = bbox(pose)
    if x0 < rx0 - EPS or y0 < ry0 - EPS or x1 > rx1 + EPS or y1 > ry1 + EPS:
        return None
    return pose


def feasible_poses(proto, rng, targets, region, obstacles, others,
                   max_samples=1500, want=25, seed_pose=None):
    """Collect up to `want` collision-free poses of `proto`."""
    out = []
    if seed_pose is not None:
        out.append(seed_pose)
    for _ in range(max_samples):
        if len(out) >= want:
            break
        pose = sample_pose_at(proto, rng, targets, region)
        if pose is None:
            continue
        if any(sat_overlap(pose, ob) for ob in obstacles):
            continue
        if any(sat_overlap(pose, p) for p in others):
            continue
        out.append(pose)
    return out


def iso_pair_search(free_cells, cell_boxes, obstacles, rng, trials,
                    seed_pose=None):
    """Place the big then the small isosceles triangle, each time keeping
    the pose that blocks the fewest lattice cells."""
    region = (0.0, 0.0, BAND_W, BAND_ROWS * H5)
    targets = [centroid(c) for c in free_cells]
    protos = [isosceles(30, 35), isosceles(20, 25)]
    best = None
    for trial in range(trials):
        placed, mask, ok = [], 0, True
        for pi, proto in enumerate(protos):
            seed = seed_pose if (pi == 0 and trial == 0) else None
            cands = feasible_poses(proto, rng, targets, region,
                                   obstacles, placed, seed_pose=seed)
            if not cands:
                ok = False
                break
            cand_best, cand_score = None, None
            for pose in cands:
                score = (mask | blocked_mask(pose, free_cells,
                                             cell_boxes)).bit_count()
                if cand_score is None or score < cand_score:
                    cand_best, cand_score = pose, score
            placed.append(cand_best)
            mask |= blocked_mask(cand_best, free_cells, cell_boxes)
        if ok and (best is None or mask.bit_count() < best[1]):
            best = (placed, mask.bit_count())
    return best


def joint_refine(pair, free_cells, cell_boxes, obstacles, rng, iters):
    """Small random moves of the pair; keep any move that blocks fewer
    lattice cells."""
    def score(polys):
        m = 0
        for p in polys:
            m |= blocked_mask(p, free_cells, cell_boxes)
        return m.bit_count()

    def legal(polys):
        for p in polys:
            for x, y in p:
                if not (-EPS <= x <= BAND_W + EPS
                        and -EPS <= y <= BAND_ROWS * H5 + EPS):
                    return False
            if any(sat_overlap(p, ob) for ob in obstacles):
                return False
        return not sat_overlap(polys[0], polys[1])

    cur, cur_s = [list(p) for p in pair], score(pair)
    for _ in range(iters):
        which = rng.randrange(3)
        dx, dy = rng.gauss(0, 0.25), rng.gauss(0, 0.25)
        da = rng.gauss(0, 0.6)
        cx = sum(centroid(p)[0] for p in cur) / 2
        cy = sum(centroid(p)[1] for p in cur) / 2
        trial = [translate(rotate_about(p, da, cx, cy), dx, dy)
                 if which in (2, i) else p for i, p in enumerate(cur)]
        if legal(trial):
            s = score(trial)
            if s <= cur_s:
                cur, cur_s = trial, s
    return cur


def postfill(placed, rng, attempts):
    """Drop a few extra side-5 triangles into any leftover gaps."""
    proto = equilateral(5)
    boxes = [bbox(p) for p in placed]
    found = []
    regions = [(0.0, 0.0, BAND_W + 10, BAND_ROWS * H5 + 5),
               (POCKET[0] - 5, POCKET[1] - 2, POCKET[2] + 5, POCKET[3] + 2)]
    for _ in range(attempts):
        pose = sample_pose(proto, rng, regions[rng.randrange(len(regions))])
        if pose is None:
            continue
        if any(not (-EPS <= x <= CANVAS_W + EPS and -EPS <= y <= CANVAS_H + EPS)
               for x, y in pose):
            continue
        px0, py0, px1, py1 = bbox(pose)
        ok = True
        for p, (bx0, by0, bx1, by1) in zip(placed, boxes):
            if bx0 >= px1 - EPS or px0 >= bx1 - EPS:
                continue
            if by0 >= py1 - EPS or py0 >= by1 - EPS:
                continue
            if sat_overlap(pose, p):
                ok = False
                break
        if ok:
            found.append(pose)
            placed.append(pose)
            boxes.append(bbox(pose))
    return found


def build_structured(seed, n_pairs, trials, deep, post_attempts):
    """One full structured construction.  Returns a list of (name, poly)."""
    rng = random.Random(seed)
    placed = []
    band_h = BAND_ROWS * H5

    # 1. tile the box above the band, then the box to its right
    upper_rows = int((CANVAS_H - band_h) // 5)
    tiling = exact_cover(round(BAND_W / 5), upper_rows, [], rng)
    assert tiling, "upper tiling failed"
    for name, x, y, w, h in tiling:
        placed.append((name, translate(rect(5 * w, 5 * h),
                                        5 * x, band_h + 5 * y)))

    rx, ry = round((CANVAS_W - BAND_W) / 5), round(CANVAS_H / 5)
    blocked = [(cx, cy)
               for cx in range(round((POCKET[0] - BAND_W) / 5),
                               round((POCKET[2] - BAND_W) / 5))
               for cy in range(round(POCKET[1] / 5), round(POCKET[3] / 5))]
    tiling = exact_cover(rx, ry, blocked, rng)
    assert tiling, "right tiling failed"
    for name, x, y, w, h in tiling:
        placed.append((name, translate(rect(5 * w, 5 * h),
                                        BAND_W + 5 * x, 5 * y)))

    # 2. the two trapeziums + two corner triangles fill the pocket
    px, py = POCKET[0], POCKET[1]
    placed.append(("trapezium 20/30 h20",
                   [(px, py), (px + 30, py), (px + 25, py + 20),
                    (px + 5, py + 20)]))
    placed.append(("trapezium 20/30 h20",
                   [(px + 30, py), (px + 50, py), (px + 55, py + 20),
                    (px + 25, py + 20)]))
    placed.append(("eq. triangle s=5",
                   [(px, py + 15), (px, py + 20), (px + H5, py + 17.5)]))
    placed.append(("eq. triangle s=5",
                   [(px + 55, py), (px + 55, py + 5),
                    (px + 55 - H5, py + 2.5)]))

    # 3. lattice band; enumerate big-triangle poses; screen disjoint pairs
    cols = round(BAND_W / 5)
    cells = lattice_cells(cols, BAND_ROWS)
    a40 = substitution_anchors(40, cols, BAND_ROWS)
    a30 = substitution_anchors(30, cols, BAND_ROWS)
    pair_candidates = []
    for t40 in a40:
        c40 = covered_cells(t40, cells)
        for t30 in a30:
            c30 = covered_cells(t30, cells)
            if not (c40 & c30):
                pair_candidates.append((t40, t30, c40 | c30))
    rng.shuffle(pair_candidates)

    region = (0.0, 0.0, BAND_W, BAND_ROWS * H5)
    iso_big = isosceles(30, 35)
    feasible = []
    for t40, t30, used in pair_candidates:
        targets = [centroid(cells[i]) for i in range(len(cells))
                   if i not in used]
        for _ in range(600):
            pose = sample_pose_at(iso_big, rng, targets, region)
            if pose and not sat_overlap(pose, t40) and not sat_overlap(pose, t30):
                feasible.append((t40, t30, used, pose))
                break
        if len(feasible) >= n_pairs:
            break

    # 4. search continuous poses for the two isosceles triangles
    best = None
    for t40, t30, used, probe in feasible:
        free_idx = [i for i in range(len(cells)) if i not in used]
        free = [cells[i] for i in free_idx]
        boxes = [bbox(c) for c in free]
        got = iso_pair_search(free, boxes, [t40, t30], rng, trials,
                              seed_pose=probe)
        if got and (best is None or got[1] < best[3]):
            best = (t40, t30, got[0], got[1], free_idx)
    assert best, "no isosceles placement found"
    t40, t30, pair, _, free_idx = best

    free = [cells[i] for i in free_idx]
    boxes = [bbox(c) for c in free]
    pair = joint_refine(pair, free, boxes, [t40, t30], rng, deep)

    placed.append(("eq. triangle s=40", t40))
    placed.append(("eq. triangle s=30", t30))
    placed.append(("iso. triangle 30/35", pair[0]))
    placed.append(("iso. triangle 20/25", pair[1]))

    # 5. keep every untouched lattice cell as a side-5 triangle, then postfill
    for cell in free:
        if not any(sat_overlap(cell, p) for p in pair):
            placed.append(("eq. triangle s=5", cell))
    polys = [p for _, p in placed]
    for pose in postfill(polys, rng, post_attempts):
        placed.append(("eq. triangle s=5", pose))
    return placed


def run_structured(args):
    """Try several seeds and keep the densest layout."""
    seed = 1 if args.seed is None else args.seed
    best = None
    for r in range(args.restarts):
        try:
            cand = build_structured(seed + r, args.pairs, args.trials,
                                    args.deep, args.post)
        except AssertionError:
            continue
        area = sum(AREAS[n] for n, _ in cand)
        print(f"  seed {seed + r}: covered {area:.1f} ({100 * area / (CANVAS_W * CANVAS_H):.2f}%)")
        if best is None or area > best[0]:
            best = (area, cand)
    assert best, "every restart failed"
    return best[1]


# ==========================================================================
# GENERIC SOLVER  (--generic)  -  works for ANY convex shape (about 96.6 %)
#
# It knows nothing about the ten shapes; it places each shape at the lowest,
# then left-most free spot, slides it into gaps, and repeats with different
# orders on all CPU cores, keeping the best layout.
# ==========================================================================

def signature(polys):
    return tuple(sorted((round(x, 3), round(y, 3))
                        for p in polys for x, y in p))


def mirror_group(polys):
    return normalise([mirror(p) for p in polys])


def pair_with_rotated_copy(poly, edge=1):
    """A shape plus a copy of it turned 180 deg about an edge midpoint makes
    a parallelogram (packs better than a lone triangle/trapezium)."""
    x1, y1 = poly[edge]
    x2, y2 = poly[(edge + 1) % len(poly)]
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    return [list(poly), [(2 * cx - x, 2 * cy - y) for x, y in poly]]


class Variant:
    """A placeable rigid piece: one polygon, or a 2-shape composite."""

    def __init__(self, type_name, polys):
        self.type_name = type_name
        self.polys = normalise(polys)
        self.area = sum(poly_area(p) for p in self.polys)
        self.orientations = self._orientations()

    def _orientations(self):
        angles = set()
        for p in self.polys:
            n = len(p)
            for i in range(n):
                (x1, y1), (x2, y2) = p[i], p[(i + 1) % n]
                a = math.degrees(math.atan2(y2 - y1, x2 - x1))
                angles.add(round(-a, 3) % 360)
                angles.add(round(-a + 180, 3) % 360)
        out, seen = [], set()
        for base in (self.polys, mirror_group(self.polys)):
            for ang in sorted(angles):
                polys = normalise([rotate_origin(p, ang) for p in base])
                sig = signature(polys)
                if sig not in seen:
                    seen.add(sig)
                    out.append(polys)
        return out


def make_variants(shapes=None):
    """Turn the shape table into placeable pieces.  The solver never looks at
    the names - only at vertices - so any convex polygons work."""
    variants = {}
    for name, poly in (shapes or SHAPES).items():
        single = Variant(name, [poly])
        pair = None
        if not is_rectangle(poly):
            pair = Variant(name, pair_with_rotated_copy(poly))
        variants[name] = {"single": single, "pair": pair}
    return variants


class Packer:
    def __init__(self):
        self.placed = []            # dicts: variant, polys, aabbs
        self.covered = 0.0

    def _candidates(self, polys):
        _, _, bx, by = group_bbox(polys)
        new_vs = [(x, y) for p in polys for x, y in p]
        cands = {(0.0, 0.0), (CANVAS_W - bx, 0.0), (0.0, CANVAS_H - by)}
        for rec in self.placed:
            for pp in rec["polys"]:
                for px, py in pp:
                    for nx, ny in new_vs:
                        dx, dy = px - nx, py - ny
                        cands.add((dx, dy))
                        cands.add((dx, 0.0))
                        cands.add((0.0, dy))
                        cands.add((CANVAS_W - bx, dy))
        return cands

    def _fits(self, polys, dx, dy, bx, by):
        if dx < -EPS or dy < -EPS:
            return None
        if dx + bx > CANVAS_W + EPS or dy + by > CANVAS_H + EPS:
            return None
        moved, boxes = [], []
        for p in polys:
            mp = translate(p, dx, dy)
            xs = [x for x, _ in mp]
            ys = [y for _, y in mp]
            moved.append(mp)
            boxes.append((min(xs), min(ys), max(xs), max(ys)))
        for rec in self.placed:
            for pp, (ax0, ay0, ax1, ay1) in zip(rec["polys"], rec["aabbs"]):
                for mp, (bx0, by0, bx1, by1) in zip(moved, boxes):
                    if bx0 >= ax1 - EPS or ax0 >= bx1 - EPS:
                        continue
                    if by0 >= ay1 - EPS or ay0 >= by1 - EPS:
                        continue
                    if sat_overlap(mp, pp):
                        return None
        return moved, boxes

    def _settle(self, polys, dx, dy, bx, by):
        """Slide the piece down, then left, in shrinking steps."""
        for _ in range(2):
            moved = False
            for step in (6.0, 3.0, 1.5, 0.7, 0.3, 0.1, 0.04):
                while dy - step >= -EPS and self._fits(polys, dx, dy - step, bx, by):
                    dy -= step
                    moved = True
            for step in (6.0, 3.0, 1.5, 0.7, 0.3, 0.1, 0.04):
                while dx - step >= -EPS and self._fits(polys, dx - step, dy, bx, by):
                    dx -= step
                    moved = True
            if not moved:
                break
        return dx, dy

    def place(self, variant, orientations=None, leftfirst=False):
        key = (lambda c: (c[0], c[1])) if leftfirst else (lambda c: (c[1], c[0]))
        best = None
        for polys in (orientations or variant.orientations):
            _, _, bx, by = group_bbox(polys)
            for dx, dy in sorted(self._candidates(polys), key=key):
                rank = (dx, dy) if leftfirst else (dy, dx)
                if best and rank >= best[:2]:
                    break
                if self._fits(polys, dx, dy, bx, by):
                    best = (rank[0], rank[1], polys, dx, dy, bx, by)
                    break
        if best is None:
            return False
        _, _, polys, dx, dy, bx, by = best
        dx, dy = self._settle(polys, dx, dy, bx, by)
        moved, boxes = self._fits(polys, dx, dy, bx, by)
        self.placed.append({"variant": variant, "polys": moved, "aabbs": boxes})
        self.covered += variant.area
        return True


def random_genome(variants, rng):
    order = sorted(variants, key=lambda n: -variants[n]["single"].area)
    for i in range(len(order) - 1):
        if rng.random() < 0.35:
            order[i], order[i + 1] = order[i + 1], order[i]
    return {"order": order,
            "pairs": {n: (variants[n]["pair"] is not None and rng.random() < 0.95)
                      for n in variants},
            "leftfirst": rng.random() < 0.5,
            "rects_first": rng.random() < 0.85,
            "oseed": rng.randrange(1 << 30)}


def mutate_genome(g, variants, rng):
    g = {**g, "pairs": dict(g["pairs"]), "order": list(g["order"])}
    for _ in range(rng.choice((1, 1, 2))):
        move = rng.randrange(5)
        if move == 0:
            g["leftfirst"] = not g["leftfirst"]
        elif move == 1:
            n = rng.choice([n for n in variants if variants[n]["pair"]])
            g["pairs"][n] = not g["pairs"][n]
        elif move == 2:
            i, j = rng.sample(range(len(g["order"])), 2)
            g["order"][i], g["order"][j] = g["order"][j], g["order"][i]
        elif move == 3:
            g["rects_first"] = not g["rects_first"]
        else:
            g["oseed"] = rng.randrange(1 << 30)
    return g


def build_layout(variants, genome, orient_cap=6):
    pk = Packer()
    orng = random.Random(genome["oseed"])
    for name in genome["order"]:                 # one of each required shape
        v = variants[name]["pair"] if genome["pairs"][name] else variants[name]["single"]
        ors = v.orientations
        if len(ors) > orient_cap:
            ors = orng.sample(ors, orient_cap)
        if not pk.place(v, ors, leftfirst=genome["leftfirst"]):
            return None
    rects = [v["single"] for v in variants.values()
             if len(v["single"].polys) == 1 and len(v["single"].polys[0]) == 4
             and v["pair"] is None]
    rects.sort(key=lambda v: -v.area)
    others = sorted([v["pair"] or v["single"] for v in variants.values()
                     if v["pair"]], key=lambda v: -v.area)
    pools = (rects, rects + others) if genome["rects_first"] \
        else (sorted(rects + others, key=lambda v: -v.area),)
    for pool in pools:                           # greedy fill, biggest first
        dead = set()
        while True:
            progressed = False
            for v in pool:
                if id(v) in dead:
                    continue
                if pk.place(v):
                    progressed = True
                    break
                dead.add(id(v))
            if not progressed:
                break
    for v in sorted((v["single"] for v in variants.values()),
                    key=lambda v: v.area)[:2]:    # patch gaps with small ones
        while pk.place(v):
            pass
    return pk


def search_island(args):
    """One CPU core: try random genomes and mutations of the best so far
    until the deadline; return the densest layout as (name, poly) pairs."""
    global CANVAS_W, CANVAS_H
    seed, deadline, shapes, (CANVAS_W, CANVAS_H) = args   # fresh process: set sheet
    variants = make_variants(shapes)
    rng = random.Random(seed)
    best = None
    while time.time() < deadline:
        if best is None or rng.random() < 0.4:
            genome = random_genome(variants, rng)
        else:
            genome = mutate_genome(best[1], variants, rng)
        pk = build_layout(variants, genome)
        if pk is not None and (best is None or pk.covered > best[0]):
            placed = [(rec["variant"].type_name, poly)
                      for rec in pk.placed for poly in rec["polys"]]
            best = (pk.covered, genome, placed)
    return best[0], best[2]


def run_generic(args):
    """Run one search island per CPU core and keep the best layout."""
    seed = 7 if args.seed is None else args.seed
    deadline = time.time() + args.budget
    jobs = [(seed + 1000 * i, deadline, SHAPES, (CANVAS_W, CANVAS_H))
            for i in range(args.workers)]
    print(f"searching on {args.workers} cores for {args.budget:.0f}s ...")
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        results = list(ex.map(search_island, jobs))
    for i, (cov, _) in enumerate(results):
        print(f"  core {i}: covered {cov:.1f} ({100 * cov / (CANVAS_W * CANVAS_H):.2f}%)")
    return max(results, key=lambda r: r[0])[1]


# ==========================================================================

def load_shapes(path):
    """Replace the sheet and shape table from a JSON file:
    {"sheet": [W, H], "shapes": {"name": [[x, y], ...], ...}}"""
    global CANVAS_W, CANVAS_H, SHAPES
    import json
    with open(path) as f:
        data = json.load(f)
    CANVAS_W, CANVAS_H = map(float, data["sheet"])
    shapes = {}
    for name, verts in data["shapes"].items():
        poly = [tuple(map(float, v)) for v in verts]
        if len(poly) < 3 or not is_convex(poly):
            raise SystemExit(f"shape {name!r} is not a convex polygon")
        if poly_area(poly) <= 0:
            raise SystemExit(f"shape {name!r} has no area")
        shapes[name] = poly
    SHAPES = shapes
    derive_specs()
    print(f"loaded {len(SHAPES)} shapes on a {CANVAS_W:g} x {CANVAS_H:g} sheet from {path}")


def main():
    ap = argparse.ArgumentParser(description="polynest - convex polygon nesting on a rectangular sheet.")
    ap.add_argument("--generic", "--advanced", dest="generic", action="store_true",
                    help="use the generic solver (works for any convex shapes)")
    ap.add_argument("--shapes", default=None,
                    help="JSON file with your own sheet and shapes (implies --generic)")
    ap.add_argument("--json", default=None,
                    help="also write the layout as JSON (name + vertices per piece)")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--out", default=None)
    # structured knobs (defaults reproduce 98.51 %)
    ap.add_argument("--restarts", type=int, default=16)
    ap.add_argument("--pairs", type=int, default=10)
    ap.add_argument("--trials", type=int, default=15)
    ap.add_argument("--deep", type=int, default=4000)
    ap.add_argument("--post", type=int, default=80000)
    # generic knobs
    ap.add_argument("--budget", type=float, default=520.0)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    if args.shapes:
        load_shapes(args.shapes)
    if set(SHAPES) != BUNDLED and not args.generic:
        print("custom shapes: the structured solver only knows the bundled "
              "ten shapes, so the generic solver is used")
        args.generic = True

    t0 = time.time()
    if args.generic:
        placed = run_generic(args)
        out = args.out or "result_generic.png"
    else:
        placed = run_structured(args)
        out = args.out or "result.png"

    counts = validate(placed)
    covered = sum(AREAS[n] for n, _ in placed)
    waste = CANVAS_W * CANVAS_H - covered
    print("\n=== RESULT ===")
    for n in sorted(counts):
        print(f"  {n:24s} x {counts[n]}")
    print(f"  pieces      : {len(placed)}")
    print(f"  covered     : {covered:.1f}  ({100 * covered / (CANVAS_W * CANVAS_H):.2f}%)")
    print(f"  wasted      : {waste:.1f}  ({100 * waste / (CANVAS_W * CANVAS_H):.2f}%)")
    print(f"  sheet:waste : {CANVAS_W * CANVAS_H / waste:.1f} : 1")
    print(f"  runtime     : {time.time() - t0:.1f}s")
    plot(placed, counts, covered, out)
    if args.json:
        import json
        with open(args.json, "w") as f:
            json.dump({"sheet": [CANVAS_W, CANVAS_H], "covered": covered,
                       "pieces": [{"name": n, "vertices": [list(v) for v in p]}
                                  for n, p in placed]}, f)
        print(f"layout saved to {args.json}")


if __name__ == "__main__":
    main()
