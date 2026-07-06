"""File ingest (Phase 4): STEP / STL round-trips through export -> import."""
from __future__ import annotations

import pytest

from agentic_cad.cad import geometry as g
from agentic_cad.cad import ingest


def test_step_roundtrip_exact(doc, tmp_path):
    box = g.add_box(doc, 20, 20, 20)
    path = g.export_step(box, tmp_path / "box.step")
    obj = ingest.import_step(doc, path, "BoxIn")
    assert obj.Shape.Volume == pytest.approx(8000, rel=1e-6)


def test_stl_roundtrip_solidifies(doc, tmp_path):
    box = g.add_box(doc, 20, 20, 20)
    path = g.export_stl(box, tmp_path / "box.stl")
    obj = ingest.import_stl(doc, path, "MeshIn")
    assert len(obj.Shape.Solids) == 1  # watertight mesh -> converted to a solid
    assert obj.Shape.Volume == pytest.approx(8000, rel=0.01)


def test_missing_file_raises(doc):
    with pytest.raises(FileNotFoundError):
        ingest.import_step(doc, "does_not_exist.step")


def test_wrong_format_raises(doc, tmp_path):
    bad = tmp_path / "model.txt"
    bad.write_text("not geometry")
    with pytest.raises(ValueError):
        ingest.import_step(doc, bad)
