"""Multi-surface ops (revolve/loft/sweep/helix/array) verified analytically."""
from __future__ import annotations

import math

import pytest

from agentic_cad.cad import features as f
from agentic_cad.cad import geometry as g
from agentic_cad.cad import surfaces as s


def test_revolve_rectangle_makes_cylinder(doc):
    # Rectangle x:0..10, z:0..30 on the XZ plane, revolved 360 deg about Z.
    sk = f.new_sketch(doc, "XZ", name="Half")
    f.sketch_rectangle(sk, 10, 30)
    rev = s.revolve(doc, sk, 360)
    assert len(rev.Shape.Solids) == 1
    assert rev.Shape.Volume == pytest.approx(math.pi * 10**2 * 30, rel=1e-4)


def test_partial_revolve_quarter(doc):
    sk = f.new_sketch(doc, "XZ", name="Half")
    f.sketch_rectangle(sk, 10, 30)
    rev = s.revolve(doc, sk, 90)
    assert rev.Shape.Volume == pytest.approx(math.pi * 10**2 * 30 / 4, rel=1e-4)


def test_loft_same_sections_makes_prism(doc):
    a = f.new_sketch(doc, "XY", name="A")
    f.sketch_rectangle(a, 20, 20)
    b = f.new_sketch(doc, "XY", (0, 0, 15), name="B")
    f.sketch_rectangle(b, 20, 20)
    lf = s.loft(doc, [a, b])
    assert len(lf.Shape.Solids) == 1
    assert lf.Shape.Volume == pytest.approx(20 * 20 * 15, rel=1e-3)


def test_loft_needs_two_sections(doc):
    a = f.new_sketch(doc, "XY", name="A")
    f.sketch_rectangle(a, 20, 20)
    with pytest.raises(ValueError):
        s.loft(doc, [a])


def test_sweep_circle_along_line(doc):
    prof = f.new_sketch(doc, "XY", name="Prof")
    f.sketch_circle(prof, (0, 0), 3)
    spine = f.new_sketch(doc, "XZ", name="Spine")
    import FreeCAD as App  # noqa: PLC0415
    import Part  # noqa: PLC0415
    spine.addGeometry(Part.LineSegment(App.Vector(0, 0, 0), App.Vector(0, 50, 0)), False)
    sw = s.sweep(doc, prof, spine, frenet=False)
    assert sw.Shape.Volume == pytest.approx(math.pi * 9 * 50, rel=1e-3)


def test_helix_wire_length(doc):
    hx = s.add_helix(doc, radius=10, pitch=5, height=25)  # 5 turns
    expected = 5 * math.sqrt((2 * math.pi * 10) ** 2 + 5**2)
    assert hx.Shape.Length == pytest.approx(expected, rel=1e-3)


def test_polar_array_fuses_disjoint_copies(doc):
    box = g.add_box(doc, 5, 5, 5, position=(20, -2.5, 0))
    arr = s.polar_array(doc, box, 4)
    assert len(arr.Shape.Solids) == 4
    assert arr.Shape.Volume == pytest.approx(4 * 125, rel=1e-6)


def test_torus_volume(doc):
    t = g.add_torus(doc, 20, 5)
    assert t.Shape.Volume == pytest.approx(2 * math.pi**2 * 20 * 5**2, rel=1e-4)


def test_rotate_moves_bbox(doc):
    box = g.add_box(doc, 10, 4, 2)
    g.rotate(box, (0, 0, 1), 90)
    bb = box.Shape.BoundBox
    assert bb.XMin == pytest.approx(-4, abs=1e-6)
    assert bb.YMax == pytest.approx(10, abs=1e-6)


def test_cylinder_axis_orientation(doc):
    cyl = g.add_cylinder(doc, 3, 20, axis=(1, 0, 0))
    bb = cyl.Shape.BoundBox
    assert bb.XLength == pytest.approx(20, rel=1e-6)
    assert bb.ZLength == pytest.approx(6, rel=1e-6)


def test_polygon_pad_volume(doc):
    sk = f.new_sketch(doc, "XY", name="Hex")
    f.sketch_polygon(sk, 6, 10)
    pad = f.extrude(doc, sk, 5, name="HexPad")
    hex_area = 3 * math.sqrt(3) / 2 * 10**2
    assert pad.Shape.Volume == pytest.approx(hex_area * 5, rel=1e-6)


def test_ellipse_pad_volume(doc):
    sk = f.new_sketch(doc, "XY", name="Ell")
    f.sketch_ellipse(sk, (0, 0), 10, 4)
    pad = f.extrude(doc, sk, 5, name="EllPad")
    assert pad.Shape.Volume == pytest.approx(math.pi * 10 * 4 * 5, rel=1e-4)
