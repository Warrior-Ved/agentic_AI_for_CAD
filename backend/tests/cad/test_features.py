"""Parametric sketch -> extrude -> edit tests, verified against analytical volume."""
from __future__ import annotations

import math

import pytest

from agentic_cad.cad import features as f


def test_rectangle_pad_volume(doc):
    sk = f.new_sketch(doc, "XY", name="Rect")
    f.sketch_rectangle(sk, 40, 20)
    pad = f.extrude(doc, sk, 10, name="Pad")
    assert len(pad.Shape.Solids) == 1
    assert pad.Shape.Volume == pytest.approx(40 * 20 * 10)


def test_circle_pad_volume(doc):
    sk = f.new_sketch(doc, "XY", name="Circ")
    f.sketch_circle(sk, (0, 0), radius=5)
    pad = f.extrude(doc, sk, 10, name="Pad")
    assert pad.Shape.Volume == pytest.approx(math.pi * 25 * 10, rel=1e-4)


def test_open_profile_raises(doc):
    # A single line segment is not a closed profile -> no solid -> clear error.
    sk = f.new_sketch(doc, "XY", name="Open")
    import Part  # noqa: PLC0415
    import FreeCAD as App  # noqa: PLC0415
    sk.addGeometry(Part.LineSegment(App.Vector(0, 0, 0), App.Vector(10, 0, 0)), False)
    with pytest.raises(RuntimeError):
        f.extrude(doc, sk, 5, name="Bad")


def test_parametric_datum_edit_changes_volume(doc):
    sk = f.new_sketch(doc, "XY", name="Circ")
    info = f.sketch_circle(sk, (0, 0), radius=5)
    pad = f.extrude(doc, sk, 10, name="Pad")
    assert pad.Shape.Volume == pytest.approx(math.pi * 25 * 10, rel=1e-4)

    # "Make it bigger" -> change the radius constraint, recompute.
    f.set_datum(sk, info["radius_constraint"], 8)
    assert pad.Shape.Volume == pytest.approx(math.pi * 64 * 10, rel=1e-4)
