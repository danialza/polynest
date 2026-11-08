"""
Render the README figures from layouts exported by polynest.py.

    python polynest.py --json docs/layouts/structured.json
    python polynest.py --generic --json docs/layouts/generic.json
    python docs/make_figures.py
"""

import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation, PillowWriter
from matplotlib.patches import Polygon, FancyArrowPatch

HERE = Path(__file__).parent
OUT = HERE / "img"
OUT.mkdir(exist_ok=True)

INK, SOFT, PAPER, LINE = "#1d2633", "#5d6b7c", "#ffffff", "#d9e0e8"
PALETTE = {                      # shape families share a hue
    "square 20x20": "#3d6fa8", "square 25x25": "#7aa5d6",
    "rectangle 30x15": "#2f8f83", "rectangle 35x25": "#79c2b2",
    "eq. triangle s=5": "#f2c75c", "eq. triangle s=30": "#ec9a45",
    "eq. triangle s=40": "#d4632a",
    "iso. triangle 20/25": "#c25b7a", "iso. triangle 30/35": "#8e4f8f",
    "trapezium 20/30 h20": "#7d8a9c",
}
EXTRA = ["#3d6fa8", "#ec9a45", "#2f8f83", "#c25b7a", "#8e4f8f", "#f2c75c",
         "#7d8a9c", "#79c2b2", "#d4632a", "#7aa5d6"]


def colour(name, order=[]):
    """Fixed colour for the bundled shapes, a stable palette for any others."""
    if name in PALETTE:
        return PALETTE[name]
    if name not in order:
        order.append(name)
    return EXTRA[order.index(name) % len(EXTRA)]


plt.rcParams.update({"font.family": "DejaVu Sans", "text.color": INK,
                     "axes.edgecolor": LINE})


def load(name):
    d = json.loads((HERE / "layouts" / f"{name}.json").read_text())
    return d["sheet"], d["covered"], [(p["name"], p["vertices"]) for p in d["pieces"]]


def draw_layout(ax, sheet, pieces, edge=0.45):
    w, h = sheet
    ax.add_patch(Polygon([(0, 0), (w, 0), (w, h), (0, h)], closed=True,
                         facecolor="#f3f5f8", edgecolor=INK, linewidth=1.6, zorder=0))
    for name, verts in pieces:
        ax.add_patch(Polygon(verts, closed=True, facecolor=colour(name),
                             edgecolor="white", linewidth=edge, zorder=2))
    ax.set_xlim(-2, w + 2)
    ax.set_ylim(-2, h + 2)
    ax.set_aspect("equal")
    ax.axis("off")


def legend(fig, pieces, y):
    counts = {}
    for n, _ in pieces:
        counts[n] = counts.get(n, 0) + 1
    names = [n for n in PALETTE if n in counts] + [n for n in counts if n not in PALETTE]
    handles = [plt.Rectangle((0, 0), 1, 1, fc=colour(n)) for n in names]
    labels = [f"{n}  ×{counts[n]}" for n in names]
    fig.legend(handles, labels, loc="lower center", ncol=5, frameon=False,
               fontsize=9, bbox_to_anchor=(0.5, y), handlelength=1.1,
               columnspacing=1.4, labelcolor=SOFT)


# ---- 1. hero: the 98.51 % layout -----------------------------------------
sheet, covered, pieces = load("structured")
area = sheet[0] * sheet[1]
fig = plt.figure(figsize=(11, 6.6), facecolor=PAPER)
ax = fig.add_axes([0.03, 0.17, 0.94, 0.72])
draw_layout(ax, sheet, pieces)
fig.text(0.03, 0.94, f"{100 * covered / area:.2f}% of the sheet covered",
         fontsize=20, fontweight="bold")
fig.text(0.03, 0.905, f"{len(pieces)} pieces · {area - covered:.1f} units² of waste · "
         f"sheet : waste = {area / (area - covered):.0f} : 1 · no overlaps",
         fontsize=11, color=SOFT)
legend(fig, pieces, 0.02)
fig.savefig(OUT / "hero.png", dpi=170, facecolor=PAPER)
plt.close(fig)

# ---- 2. structured vs generic --------------------------------------------
fig, axes = plt.subplots(1, 2, figsize=(13, 4.4), facecolor=PAPER)
for ax, name, title in ((axes[0], "structured", "Structured solver"),
                        (axes[1], "generic", "Generic solver")):
    s, cov, pcs = load(name)
    draw_layout(ax, s, pcs, edge=0.35)
    a = s[0] * s[1]
    ax.set_title(f"{title}  —  {100 * cov / a:.2f}%  ({len(pcs)} pieces)",
                 fontsize=12.5, color=INK, pad=8, loc="left")
fig.tight_layout()
fig.savefig(OUT / "comparison.png", dpi=150, facecolor=PAPER)
plt.close(fig)

# ---- 3. the key insight: lattice substitution ----------------------------
H5 = 5 * math.sqrt(3) / 2
cols, rows = 14, 8
fig = plt.figure(figsize=(11, 4.6), facecolor=PAPER)
ax = fig.add_axes([0.02, 0.05, 0.62, 0.86])
for j in range(rows):                       # the side-5 lattice
    for i in range(-1, cols + 1):
        x0 = 5 * i + 2.5 * (j % 2)
        up = [(x0, H5 * j), (x0 + 5, H5 * j), (x0 + 2.5, H5 * (j + 1))]
        dn = [(x0 + 2.5, H5 * (j + 1)), (x0 + 7.5, H5 * (j + 1)), (x0 + 5, H5 * j)]
        for t in (up, dn):
            if all(-1e-9 <= x <= 70 + 1e-9 for x, _ in t):
                ax.add_patch(Polygon(t, closed=True, facecolor="#fbf3dc",
                                     edgecolor="#e2d3a6", linewidth=0.6))
big = [(10, 8 * H5), (50, 8 * H5), (30, 0)]          # side 40, point down
mid = [(0, 0), (30, 0), (15, 6 * H5)]                 # side 30, point up
ax.add_patch(Polygon(big, closed=True, facecolor="#d4632a", alpha=0.82,
                     edgecolor="white", linewidth=1.4))
ax.add_patch(Polygon(mid, closed=True, facecolor="#ec9a45", alpha=0.85,
                     edgecolor="white", linewidth=1.4))
ax.text(30, 6.2 * H5, "side 40\n= 64 cells", ha="center", va="center",
        color="white", fontsize=11, fontweight="bold")
ax.text(15, 2.2 * H5, "side 30\n= 36 cells", ha="center", va="center",
        color="white", fontsize=10.5, fontweight="bold")
ax.set_xlim(-2, 72)
ax.set_ylim(-2, 8 * H5 + 2)
ax.set_aspect("equal")
ax.axis("off")
fig.text(0.67, 0.80, "The key insight", fontsize=17, fontweight="bold")
fig.text(0.67, 0.28,
         "Every size in the instance is a multiple of 5.\n\n"
         "An equilateral triangle of side 5k is made of\n"
         "exactly k² side-5 triangles, so the two big\n"
         "triangles drop onto a side-5 lattice with\n"
         "zero local waste — and every rectangle tiles\n"
         "a 5-unit grid exactly.\n\n"
         "The hard part is reduced to fitting two\n"
         "isosceles triangles into the lattice.",
         fontsize=10.8, color=SOFT, linespacing=1.45)
fig.savefig(OUT / "insight.png", dpi=170, facecolor=PAPER)
plt.close(fig)

# ---- 4. the separating axis theorem --------------------------------------
fig, ax = plt.subplots(figsize=(8.5, 4.4), facecolor=PAPER)
A = [(1, 1), (5, 1.6), (3.6, 4.6)]
B = [(6.6, 2.6), (9.6, 2.2), (9.2, 5.2), (6.9, 5.4)]
ax.add_patch(Polygon(A, closed=True, facecolor="#7aa5d6", edgecolor=INK, lw=1.2))
ax.add_patch(Polygon(B, closed=True, facecolor="#79c2b2", edgecolor=INK, lw=1.2))
ax.plot([0, 11], [-0.6, -0.6], color=SOFT, lw=1)          # projection axis
for poly, col in ((A, "#3d6fa8"), (B, "#2f8f83")):
    xs = [p[0] for p in poly]
    ax.plot([min(xs), max(xs)], [-0.6, -0.6], color=col, lw=6, solid_capstyle="butt")
    for x in (min(xs), max(xs)):
        ax.plot([x, x], [-0.6, min(p[1] for p in poly if abs(p[0] - x) < 1e-9)],
                color=col, lw=0.8, ls=":")
ax.add_patch(FancyArrowPatch((5, -1.25), (6.6, -1.25), arrowstyle="<->",
                             mutation_scale=12, color="#d4632a", lw=1.4))
ax.text(5.8, -1.85, "gap on this axis  →  no overlap", ha="center",
        color="#d4632a", fontsize=10.5, fontweight="bold")
ax.text(0, 6.1, "Separating Axis Theorem", fontsize=14, fontweight="bold")
ax.text(0, 5.65, "Two convex shapes are disjoint iff their projections are separated "
        "on some edge normal.", fontsize=9.6, color=SOFT)
ax.set_xlim(-0.3, 11)
ax.set_ylim(-2.3, 6.5)
ax.set_aspect("equal")
ax.axis("off")
fig.savefig(OUT / "sat.png", dpi=170, facecolor=PAPER, bbox_inches="tight")
plt.close(fig)

# ---- 5. your own shapes: the generic solver on a custom file -------------
sheet, covered, pieces = load("custom")
area = sheet[0] * sheet[1]
fig = plt.figure(figsize=(8.6, 6.4), facecolor=PAPER)
ax = fig.add_axes([0.04, 0.16, 0.92, 0.72])
draw_layout(ax, sheet, pieces, edge=0.6)
fig.text(0.04, 0.935, "examples/custom_shapes.json", fontsize=14, fontweight="bold",
         family="DejaVu Sans Mono")
fig.text(0.04, 0.895, f"{sheet[0]:g} × {sheet[1]:g} sheet · {len(pieces)} pieces · "
         f"{100 * covered / area:.1f}% covered · generic solver", fontsize=10.5, color=SOFT)
legend(fig, pieces, 0.02)
fig.savefig(OUT / "custom.png", dpi=150, facecolor=PAPER)
plt.close(fig)

# ---- 6. animation: the record layout, built stage by stage ---------------
sheet, covered, pieces = load("structured")
area = sheet[0] * sheet[1]
frames, small = [], []
for i, (name, _) in enumerate(pieces):        # one frame per piece, small
    if name == "eq. triangle s=5":            # triangles a few at a time
        small.append(i)
        if len(small) == 5:
            frames.append(small)
            small = []
    else:
        if small:
            frames.append(small)
            small = []
        frames.append([i])
if small:
    frames.append(small)
hold = 18
fig = plt.figure(figsize=(8, 4.75), facecolor=PAPER)
ax = fig.add_axes([0.02, 0.02, 0.96, 0.84])
draw_layout(ax, sheet, [])
patches = []
for name, verts in pieces:
    patch = Polygon(verts, closed=True, facecolor=colour(name), edgecolor="white",
                    linewidth=0.45, zorder=2, visible=False)
    ax.add_patch(patch)
    patches.append(patch)
label = fig.text(0.03, 0.91, "", fontsize=15, fontweight="bold")
sub = fig.text(0.97, 0.915, "", fontsize=10.5, color=SOFT, ha="right")
done = [0.0, 0]


def step(k):
    if k < len(frames):
        for i in frames[k]:
            patches[i].set_visible(True)
            done[0] += abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in
                               zip(pieces[i][1], pieces[i][1][1:] + pieces[i][1][:1]))) / 2
            done[1] += 1
    label.set_text(f"{100 * done[0] / area:5.2f}% covered")
    sub.set_text(f"{done[1]} / {len(pieces)} pieces")
    return patches + [label, sub]


anim = FuncAnimation(fig, step, frames=len(frames) + hold, blit=False)
anim.save(OUT / "build.gif", writer=PillowWriter(fps=8), dpi=80)
plt.close(fig)

print("figures written to", OUT)
