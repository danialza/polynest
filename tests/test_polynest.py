import json
import math
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import polynest as pn  # noqa: E402


# ---- geometry ------------------------------------------------------------

def test_sat_detects_overlap():
    assert pn.sat_overlap(pn.rect(10, 10), pn.translate(pn.rect(10, 10), 5, 5))


def test_sat_touching_edges_are_not_overlap():
    assert not pn.sat_overlap(pn.rect(10, 10), pn.translate(pn.rect(10, 10), 10, 0))


def test_sat_separated_triangles():
    tri = pn.equilateral(10)
    assert not pn.sat_overlap(tri, pn.translate(tri, 12, 0))


def test_triangle_and_rotated_copy_form_parallelogram():
    a, b = pn.pair_with_rotated_copy(pn.equilateral(10))
    assert not pn.sat_overlap(a, b)
    assert pn.side_lengths(a) == pytest.approx(pn.side_lengths(b))


def test_shape_checks():
    assert pn.is_rectangle(pn.rect(30, 15))
    assert not pn.is_rectangle(pn.trapezium(20, 30, 20))
    assert pn.is_convex(pn.isosceles(20, 25))
    assert not pn.is_convex([(0, 0), (10, 0), (10, 10), (5, 3), (0, 10)])


# ---- the key insight -----------------------------------------------------

@pytest.mark.parametrize("side, cells", [(30, 36), (40, 64)])
def test_big_triangles_are_exactly_k_squared_lattice_cells(side, cells):
    lattice = pn.lattice_cells(14, 8)
    for anchor in pn.substitution_anchors(side, 14, 8):
        assert len(pn.covered_cells(anchor, lattice)) == cells
        assert pn.poly_area(anchor) == pytest.approx(
            cells * pn.poly_area(pn.equilateral(5)))


# ---- solvers -------------------------------------------------------------

def test_structured_solver_reproduces_the_record():
    placed = pn.build_structured(3, 10, 15, 4000, 80000)
    counts = pn.validate(placed)
    covered = sum(pn.AREAS[n] for n, _ in placed)
    assert len(placed) == 79
    assert set(counts) == set(pn.SHAPES)
    assert covered == pytest.approx(19701.5, abs=0.05)      # 98.51 %


def test_generic_solver_produces_a_valid_dense_layout():
    placed = pn.run_generic(Namespace(seed=7, budget=3, workers=2))
    pn.validate(placed)
    covered = sum(pn.AREAS[n] for n, _ in placed)
    assert covered / (pn.CANVAS_W * pn.CANVAS_H) > 0.85


# ---- validation catches mistakes ----------------------------------------

def test_validate_rejects_overlapping_pieces():
    stacked = [(name, poly) for name, poly in pn.SHAPES.items()]   # all at origin
    with pytest.raises(AssertionError):
        pn.validate(stacked)


def test_validate_rejects_a_missing_shape():
    placed = [p for p in pn.build_structured(3, 10, 15, 4000, 80000)
              if not p[0].startswith("trapezium")]
    with pytest.raises(AssertionError):
        pn.validate(placed)


# ---- command line --------------------------------------------------------

def run_cli(*args, cwd):
    return subprocess.run([sys.executable, str(ROOT / "polynest.py"), *args],
                          cwd=cwd, capture_output=True, text=True, timeout=180)


def test_custom_shapes_file_end_to_end(tmp_path):
    out = tmp_path / "layout.json"
    r = run_cli("--shapes", str(ROOT / "examples" / "custom_shapes.json"),
                "--budget", "3", "--workers", "2", "--json", str(out), cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    data = json.loads(out.read_text())
    names = {p["name"] for p in data["pieces"]}
    expected = set(json.loads((ROOT / "examples" / "custom_shapes.json").read_text())["shapes"])
    assert names == expected                 # every custom shape placed at least once
    w, h = data["sheet"]
    for p in data["pieces"]:
        for x, y in p["vertices"]:
            assert -1e-6 <= x <= w + 1e-6 and -1e-6 <= y <= h + 1e-6


def test_non_convex_shape_is_rejected(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"sheet": [50, 50], "shapes": {
        "L": [[0, 0], [10, 0], [10, 4], [4, 4], [4, 10], [0, 10]]}}))
    r = run_cli("--shapes", str(bad), "--budget", "1", "--workers", "1", cwd=tmp_path)
    assert r.returncode != 0
    assert "not a convex polygon" in r.stdout + r.stderr
