"""FEM simulation (Phase 5) verified against analytic solutions.

These run the real Gmsh + CalculiX pipeline headless (both ship with FreeCAD),
on a coarse mesh so each solve stays around a second.
"""
from __future__ import annotations


import pytest

from agentic_cad.cad import simulation as sim


@pytest.fixture
def beam(doc):
    box = doc.addObject("Part::Box", "Beam")
    box.Length, box.Width, box.Height = 100, 20, 10
    doc.recompute()
    return box


def test_face_info_box(beam):
    faces = sim.face_info(beam)
    assert len(faces) == 6
    assert {f["name"] for f in faces} == {f"Face{i}" for i in range(1, 7)}
    areas = sorted(f["area_mm2"] for f in faces)
    assert areas == [200.0, 200.0, 1000.0, 1000.0, 2000.0, 2000.0]
    f1 = next(f for f in faces if f["name"] == "Face1")
    assert f1["normal"] == [-1.0, -0.0, 0.0]


def test_face_tessellation_box(beam):
    tess = sim.face_tessellation(beam)
    assert len(tess) == 6
    for f in tess:
        assert len(f["vertices"]) % 3 == 0 and len(f["vertices"]) >= 9
        assert len(f["triangles"]) % 3 == 0 and len(f["triangles"]) >= 3
        assert max(f["triangles"]) < len(f["vertices"]) // 3


def test_static_cantilever_matches_beam_theory(beam):
    summary, field = sim.run_static(beam, ["Face1"], ["Face2"], 500,
                                    direction="-z", material="steel", mesh_size=8)
    # analytic tip deflection: F L^3 / (3 E I),  I = b h^3 / 12
    expected = 500 * 100**3 / (3 * 210000 * (20 * 10**3 / 12))
    assert summary["max_displacement_mm"] == pytest.approx(expected, rel=0.05)
    assert summary["max_von_mises_mpa"] > 0
    assert summary["safety_factor"] == pytest.approx(
        235.0 / summary["max_von_mises_mpa"], rel=1e-3)
    # field payload consistency
    n_nodes = len(field["nodes"]) // 3
    assert len(field["displacements"]) == 3 * n_nodes
    assert max(field["triangles"]) < n_nodes
    for f in field["fields"].values():
        assert len(f["values"]) == n_nodes
    assert set(field["fields"]) == {"von_mises", "displacement"}


def test_thermal_gradient_bounds(beam):
    summary, field = sim.run_thermal(beam, ["Face1"], 100.0, ["Face2"], 20.0,
                                     material="steel", mesh_size=8)
    assert summary["max_temp_c"] == pytest.approx(100.0, abs=0.5)
    assert summary["min_temp_c"] == pytest.approx(20.0, abs=0.5)
    assert summary["max_displacement_mm"] > 0          # thermal expansion
    temps = field["fields"]["temperature"]["values"]
    assert min(temps) >= 19.5 and max(temps) <= 100.5  # bounded by the BCs


def test_bad_face_name_rejected(beam):
    with pytest.raises(ValueError, match="Face99"):
        sim.run_static(beam, ["Face99"], ["Face2"], 100)


def test_bad_direction_rejected(beam):
    with pytest.raises(ValueError, match="direction"):
        sim.run_static(beam, ["Face1"], ["Face2"], 100, direction="diagonal")


def test_inverted_gradient_rejected(beam):
    with pytest.raises(ValueError, match="hot_temp_c"):
        sim.run_thermal(beam, ["Face1"], 20.0, ["Face2"], 100.0)


def test_unknown_material_rejected(beam):
    with pytest.raises(ValueError, match="material"):
        sim.run_static(beam, ["Face1"], ["Face2"], 100, material="wood")


def test_isolation_leaves_live_doc_clean(doc, beam):
    before = {o.Name for o in doc.Objects}
    sim.run_static(beam, ["Face1"], ["Face2"], 100, mesh_size=10)
    after = {o.Name for o in doc.Objects}
    assert before == after  # no analysis objects leaked into the live document
