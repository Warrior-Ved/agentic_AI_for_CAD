"""Parametric part generators (gear / fan rotor / spring) verified analytically.

Gear volumes are bounded by the root and tip cylinders; the spring volume is
compared to pi * r_wire^2 * helix_length (exact for a swept circular section).
"""
from __future__ import annotations

import math

import pytest

from agentic_cad.cad import parts as p


def test_spur_gear_valid_and_bounded(doc):
    gear = p.involute_gear(doc, module=2, teeth=20, thickness=8)
    shape = gear.Shape
    assert shape.isValid()
    assert len(shape.Solids) == 1
    r_root, r_tip = 20 - 2.5, 20 + 2  # pitch r=20, dedendum 1.25m, addendum 1m
    assert math.pi * r_root**2 * 8 < shape.Volume < math.pi * r_tip**2 * 8


def test_gear_bore_removes_expected_volume(doc):
    solid = p.involute_gear(doc, 2, 20, 8, name="G1")
    bored = p.involute_gear(doc, 2, 20, 8, bore_diameter=10, name="G2")
    assert solid.Shape.Volume - bored.Shape.Volume == pytest.approx(math.pi * 5**2 * 8, rel=1e-3)


def test_helical_gear_valid_and_bounded(doc):
    gear = p.involute_gear(doc, 2, 20, 10, helix_angle=25)
    shape = gear.Shape
    assert shape.isValid()
    assert len(shape.Solids) == 1
    r_root, r_tip = 17.5, 22.0
    assert math.pi * r_root**2 * 10 < shape.Volume < math.pi * r_tip**2 * 10


def test_helical_matches_spur_volume(doc):
    # Twisting the tooth column must not change its cross-section, so the
    # lofted helical gear should stay within a few % of the spur volume.
    spur = p.involute_gear(doc, 2, 16, 10, name="Spur")
    helical = p.involute_gear(doc, 2, 16, 10, helix_angle=20, name="Helical")
    assert helical.Shape.Volume == pytest.approx(spur.Shape.Volume, rel=0.03)


def _tip_vertex_angles(shape, z_at: float, r_tip: float) -> list[float]:
    """Polar angles (deg) of the flank-tip vertices in the section at ``z_at``."""
    angles = []
    for v in shape.Vertexes:
        if abs(v.Z - z_at) < 1e-5 and abs(math.hypot(v.X, v.Y) - r_tip) < 1e-3:
            angles.append(math.degrees(math.atan2(v.Y, v.X)))
    return angles


def test_helical_gear_twist_is_actually_applied(doc):
    # Regression guard: OCC lofts can silently re-align rotated sections and
    # remove the twist. Measure the tooth-tip rotation between bottom and top.
    module, teeth, thickness, beta = 2, 16, 10, 20
    r_pitch = module * teeth / 2
    r_tip = r_pitch + module
    expected = math.degrees(thickness * math.tan(math.radians(beta)) / r_pitch)  # ~13.0 deg
    gear = p.involute_gear(doc, module, teeth, thickness, helix_angle=beta)
    bottom = _tip_vertex_angles(gear.Shape, 0.0, r_tip)
    top = _tip_vertex_angles(gear.Shape, thickness, r_tip)
    assert bottom and top
    pitch_ang = 360.0 / teeth
    # All teeth are equivalent modulo one tooth pitch; some top/bottom vertex
    # pair must be separated by the helix twist.
    candidates = {round((t - b) % pitch_ang, 2) for t in top for b in bottom}
    assert any(abs(c - expected % pitch_ang) < 0.5 for c in candidates), (
        f"no tip-vertex shift near {expected:.2f} deg in {sorted(candidates)}")


def test_degenerate_tooth_raises(doc):
    # 4 teeth at a 30 deg pressure angle: the flanks cross before the tip circle.
    with pytest.raises(ValueError):
        p.involute_gear(doc, module=2, teeth=4, pressure_angle=30, thickness=8)


def test_fan_rotor_single_solid(doc):
    rotor = p.fan_rotor(doc, hub_radius=10, hub_height=20, blade_count=6,
                        blade_length=40, chord=12)
    shape = rotor.Shape
    assert shape.isValid()
    assert len(shape.Solids) == 1
    assert shape.Volume > math.pi * 10**2 * 20  # more than the bare hub
    # Blades at 0/180 deg put the tip-to-tip span at 2 * (hub + blade length).
    assert shape.BoundBox.XLength == pytest.approx(2 * (10 + 40), rel=0.05)


def test_fan_rotor_thickness_guard(doc):
    with pytest.raises(ValueError):
        p.fan_rotor(doc, hub_radius=10, hub_height=20, blade_count=4,
                    blade_length=30, chord=5, blade_thickness=5, taper=0.5)


def test_fan_rotor_survives_aggressive_taper(doc):
    # Thickness scales with chord, so even an extreme taper stays buildable.
    rotor = p.fan_rotor(doc, hub_radius=12, hub_height=25, blade_count=6,
                        blade_length=45, chord=14, blade_thickness=2.8, taper=0.2,
                        root_twist=-10, tip_twist=10)
    assert rotor.Shape.isValid()
    assert len(rotor.Shape.Solids) == 1


def test_spring_volume(doc):
    sp = p.spring(doc, coil_radius=10, wire_radius=1.5, pitch=5, turns=6)
    assert len(sp.Shape.Solids) == 1
    helix_len = 6 * math.sqrt((2 * math.pi * 10) ** 2 + 5**2)
    assert sp.Shape.Volume == pytest.approx(math.pi * 1.5**2 * helix_len, rel=0.05)


def test_spring_self_intersection_guard(doc):
    with pytest.raises(ValueError):
        p.spring(doc, coil_radius=10, wire_radius=3, pitch=5, turns=4)
