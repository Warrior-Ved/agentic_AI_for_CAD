"""End-to-end recipe test: the Deliverable-1 holed block, parametric + exportable."""
from __future__ import annotations

import math

import pytest

from agentic_cad.cad import features, recipes
from agentic_cad.cad import geometry as g


def test_holed_block_volume_matches_analytical():
    L, W, H, d = 40.0, 20.0, 10.0, 10.0
    out = recipes.holed_block(L, W, H, d)
    try:
        result = out["result"]
        expected = L * W * H - math.pi * (d / 2) ** 2 * H
        assert result.Shape.Volume == pytest.approx(expected, rel=1e-4)
        assert len(result.Shape.Solids) == 1
        assert result.Shape.isClosed()
    finally:
        import FreeCAD as App  # noqa: PLC0415
        App.closeDocument(out["doc"].Name)


def test_holed_block_hole_is_resizable():
    out = recipes.holed_block(40, 20, 10, hole_diameter=10)
    try:
        result, sk = out["result"], out["hole_sketch"]
        v0 = result.Shape.Volume
        # Enlarge the hole to diameter 16 (radius 8) — a parametric edit.
        features.set_datum(sk, out["hole_radius_constraint"], 8)
        v1 = result.Shape.Volume
        assert v1 < v0  # bigger hole removes more material
        expected = 40 * 20 * 10 - math.pi * 8 ** 2 * 10
        assert v1 == pytest.approx(expected, rel=1e-4)
    finally:
        import FreeCAD as App  # noqa: PLC0415
        App.closeDocument(out["doc"].Name)


def test_holed_block_exports_step(tmp_path):
    out = recipes.holed_block(40, 20, 10, 10)
    try:
        path = g.export_step(out["result"], tmp_path / "holed_block.step")
        assert path.exists() and path.stat().st_size > 0
    finally:
        import FreeCAD as App  # noqa: PLC0415
        App.closeDocument(out["doc"].Name)
