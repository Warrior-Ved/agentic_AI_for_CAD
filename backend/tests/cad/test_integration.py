"""End-to-end Phase 1 integration scenarios that mirror real agent workflows.

These exercise the foundation the way the agent layer (Phase 2+) will: build a
multi-feature part, persist it, exchange it as STEP, edit it across several
"turns", and read its state back. Each asserts analytical geometry, so a real
regression — not just a crash — is caught.
"""
from __future__ import annotations

import math

import pytest

from agentic_cad.cad import document, features, inspect, recipes
from agentic_cad.cad import geometry as g


def test_bracket_plate_with_four_holes():
    """Build a 4-hole mounting plate via repeated boolean cuts (a real part)."""
    doc = document.new_document("Bracket")
    try:
        L, W, H, r, inset = 80.0, 60.0, 8.0, 4.0, 12.0
        current = g.add_box(doc, L, W, H, name="Plate")
        centers = [(inset, inset), (L - inset, inset),
                   (L - inset, W - inset), (inset, W - inset)]
        for i, (cx, cy) in enumerate(centers):
            hole = g.add_cylinder(doc, r, H, name=f"Hole{i}", position=(cx, cy, 0))
            current = g.boolean_cut(doc, current, hole, name=f"Cut{i}")

        expected = L * W * H - 4 * math.pi * r * r * H
        assert current.Shape.Volume == pytest.approx(expected, rel=1e-4)
        assert len(current.Shape.Solids) == 1
        assert current.Shape.isClosed()
    finally:
        document.close_document(doc)


def test_save_close_reopen_preserves_model(tmp_path):
    """A model survives a save -> close -> reopen round-trip (.FCStd persistence)."""
    out = recipes.holed_block(40, 20, 10, 10)
    v0 = out["result"].Shape.Volume
    path = document.save_document(out["doc"], tmp_path / "hb.FCStd")
    document.close_document(out["doc"])

    doc2 = document.open_document(path)
    try:
        obj = doc2.getObject("HoledBlock")
        assert obj is not None, "feature tree not restored on reopen"
        assert obj.Shape.Volume == pytest.approx(v0, rel=1e-6)
    finally:
        document.close_document(doc2)


def test_step_export_import_preserves_volume(tmp_path):
    """Exact geometry survives a STEP export -> re-import (the ingest contract)."""
    doc = document.new_document("StepSrc")
    try:
        box = g.add_box(doc, 30, 20, 10)
        cyl = g.add_cylinder(doc, 4, 10, position=(15, 10, 0))
        part = g.boolean_cut(doc, box, cyl)
        v0 = part.Shape.Volume
        step = g.export_step(part, tmp_path / "part.step")
    finally:
        document.close_document(doc)

    import Part  # noqa: PLC0415
    doc2 = document.new_document("StepDst")
    try:
        feat = doc2.addObject("Part::Feature", "Imported")
        feat.Shape = Part.read(str(step))
        doc2.recompute()
        assert feat.Shape.Volume == pytest.approx(v0, rel=1e-4)
        assert len(feat.Shape.Solids) == 1
    finally:
        document.close_document(doc2)


def test_parametric_edit_conversation():
    """Simulate a multi-turn 'make the hole bigger/smaller' conversation.

    Each edit is a single datum change + recompute, and every result must be a
    valid single solid with the analytically-correct volume.
    """
    out = recipes.holed_block(40, 20, 10, hole_diameter=10)
    doc, result, sk = out["doc"], out["result"], out["hole_sketch"]
    con = out["hole_radius_constraint"]
    try:
        for diameter in (12, 8, 6, 14):
            features.set_datum(sk, con, diameter / 2)
            expected = 40 * 20 * 10 - math.pi * (diameter / 2) ** 2 * 10
            assert result.Shape.Volume == pytest.approx(expected, rel=1e-4), \
                f"wrong volume after resizing hole to d={diameter}"
            assert len(result.Shape.Solids) == 1, f"not a solid at d={diameter}"
    finally:
        document.close_document(doc)


def test_feature_tree_inspection_grounding():
    """The agent can read back accurate parameters + geometry (no hallucination)."""
    doc = document.new_document("Inspect")
    try:
        box = g.add_box(doc, 10, 20, 30, name="Plate")
        g.add_cylinder(doc, 5, 30, name="Pin")

        tree = {t["name"]: t for t in inspect.feature_tree(doc)}
        assert {"Plate", "Pin"} <= set(tree)
        assert tree["Plate"]["parameters"]["Length"] == pytest.approx(10)
        assert tree["Plate"]["parameters"]["Width"] == pytest.approx(20)
        assert tree["Plate"]["parameters"]["Height"] == pytest.approx(30)
        assert tree["Pin"]["parameters"]["Radius"] == pytest.approx(5)

        described = inspect.describe(box)
        assert described["mass_properties"]["volume_mm3"] == pytest.approx(6000)
        assert described["bbox"]["z_len"] == pytest.approx(30)
    finally:
        document.close_document(doc)
