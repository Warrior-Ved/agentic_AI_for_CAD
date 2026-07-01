"""Atomic Part-operation tests, verified against analytical geometry."""
from __future__ import annotations

import math

import pytest

from agentic_cad.cad import geometry as g
from agentic_cad.cad import inspect


def test_box_volume_and_bbox(doc):
    box = g.add_box(doc, 20, 10, 5)
    assert box.Shape.Volume == pytest.approx(20 * 10 * 5)
    bb = inspect.bbox(box)
    assert (bb["x_len"], bb["y_len"], bb["z_len"]) == pytest.approx((20, 10, 5))


def test_cylinder_volume(doc):
    cyl = g.add_cylinder(doc, radius=5, height=10)
    assert cyl.Shape.Volume == pytest.approx(math.pi * 25 * 10, rel=1e-4)


def test_boolean_cut_subtracts_volume(doc):
    box = g.add_box(doc, 40, 20, 10)
    cyl = g.add_cylinder(doc, radius=5, height=10, position=(20, 10, 0))
    cut = g.boolean_cut(doc, box, cyl)
    expected = 40 * 20 * 10 - math.pi * 25 * 10
    assert cut.Shape.Volume == pytest.approx(expected, rel=1e-4)
    assert len(cut.Shape.Solids) == 1


def test_boolean_fuse_overlap(doc):
    a = g.add_box(doc, 10, 10, 10)
    b = g.add_box(doc, 10, 10, 10, position=(5, 0, 0))
    fused = g.boolean_fuse(doc, a, b)
    # Union of two 1000 mm^3 boxes overlapping in a 500 mm^3 slab.
    assert fused.Shape.Volume == pytest.approx(1000 + 1000 - 500)


def test_boolean_common_overlap(doc):
    a = g.add_box(doc, 10, 10, 10)
    b = g.add_box(doc, 10, 10, 10, position=(5, 0, 0))
    common = g.boolean_common(doc, a, b)
    assert common.Shape.Volume == pytest.approx(500)


def test_fillet_produces_valid_solid_with_less_volume(doc):
    box = g.add_box(doc, 20, 20, 20)
    v0 = box.Shape.Volume
    fillet = g.fillet_edges(doc, box, radius=2)
    assert len(fillet.Shape.Solids) == 1
    assert fillet.Shape.isClosed()
    assert fillet.Shape.Volume < v0  # rounding removes material at the corners


def test_chamfer_produces_valid_solid(doc):
    box = g.add_box(doc, 20, 20, 20)
    chamfer = g.chamfer_edges(doc, box, size=2)
    assert len(chamfer.Shape.Solids) == 1
    assert chamfer.Shape.Volume < 20 ** 3


def test_set_property_is_parametric(doc):
    box = g.add_box(doc, 10, 10, 10)
    assert box.Shape.Volume == pytest.approx(1000)
    g.set_property(box, "Length", 20)
    assert box.Shape.Volume == pytest.approx(2000)  # edit-by-property + recompute


def test_translate_moves_bbox(doc):
    box = g.add_box(doc, 10, 10, 10)
    g.translate(box, (5, 0, 0))
    assert inspect.bbox(box)["x_min"] == pytest.approx(5)


def test_export_step_and_stl(doc, tmp_path):
    box = g.add_box(doc, 10, 10, 10)
    step = g.export_step(box, tmp_path / "box.step")
    stl = g.export_stl(box, tmp_path / "box.stl")
    assert step.exists() and step.stat().st_size > 0
    assert stl.exists() and stl.stat().st_size > 0


def test_mass_properties(doc):
    box = g.add_box(doc, 2, 3, 4)
    mp = inspect.mass_properties(box)
    assert mp["volume_mm3"] == pytest.approx(24)
    assert mp["center_of_mass"] == pytest.approx([1, 1.5, 2])
    assert mp["solid_count"] == 1
