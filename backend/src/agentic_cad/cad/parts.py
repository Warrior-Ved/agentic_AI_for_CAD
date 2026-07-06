"""Parametric generators for complex multi-surface parts: gears, rotors, springs.

Each generator computes the finished shape in memory with the Part kernel
(involute maths, twisted lofts, helical sweeps) and attaches it to a single
Part::Feature, so the feature tree stays readable and the agent can treat the
part as one object. Edits are done by re-generating with new parameters.
"""
from __future__ import annotations
import math
from typing import Sequence
from agentic_cad.cad import bootstrap
import FreeCAD as FCad
import Part

bootstrap.ensure_freecad_importable()
Vec = FCad.Vector


# --------------------------------------------------------------------------- #
# Involute gear
# --------------------------------------------------------------------------- #
def _involute_pt(r_base: float, t: float) -> tuple[float, float]:
    """Point on the involute of a circle of radius ``r_base`` at roll angle t."""
    return (r_base * (math.cos(t) + t * math.sin(t)),
            r_base * (math.sin(t) - t * math.cos(t)))


def _rot2d(pts, delta: float):
    c, s = math.cos(delta), math.sin(delta)
    return [(x * c - y * s, x * s + y * c) for x, y in pts]


def _polar(r: float, ang: float) -> tuple[float, float]:
    return (r * math.cos(ang), r * math.sin(ang))


def _gear_profile_wire(module: float, teeth: int, pressure_angle_deg: float,
                       z_pos: float = 0.0, twist_rad: float = 0.0,
                       points_per_flank: int = 12) -> "Part.Wire":
    """Closed involute gear outline at height ``z_pos``, rotated by ``twist_rad``.

    Per tooth: (radial root line) -> involute flank -> tip arc -> mirrored
    flank -> (radial root line) -> root arc to the next tooth.
    """
    m, zt = float(module), int(teeth)
    alpha = math.radians(pressure_angle_deg)
    r_pitch = m * zt / 2.0
    r_base = r_pitch * math.cos(alpha)
    r_tip = r_pitch + m                # standard addendum = 1 module
    r_root = r_pitch - 1.25 * m        # standard dedendum = 1.25 modules
    if r_root <= 0:
        raise ValueError(f"gear with module {m} and {zt} teeth has no root circle")

    inv_alpha = math.tan(alpha) - alpha
    half_tooth = math.pi / (2 * zt)    # half tooth-thickness angle at the pitch circle
    t_tip = math.sqrt((r_tip / r_base) ** 2 - 1.0)
    inv_tip = t_tip - math.atan(t_tip)
    if inv_tip >= half_tooth + inv_alpha:
        raise ValueError(
            f"tooth tip degenerates: {zt} teeth is too few for a "
            f"{pressure_angle_deg} deg pressure angle — add teeth or lower the angle")

    # Flank from the base circle (or the root circle when it lies outside).
    t_start = 0.0 if r_root < r_base else math.sqrt((r_root / r_base) ** 2 - 1.0)
    ts = [t_start + (t_tip - t_start) * i / (points_per_flank - 1) for i in range(points_per_flank)]
    flank = [_involute_pt(r_base, t) for t in ts]

    # Centre the tooth on +X: the involute crosses the pitch circle at polar
    # angle inv(alpha); the lower flank must cross it at -half_tooth.
    lower = _rot2d(flank, -(half_tooth + inv_alpha))
    upper = [(x, -y) for x, y in lower]
    ang_lo = math.atan2(lower[0][1], lower[0][0])   # polar angle where the flank starts
    tooth_pitch = 2 * math.pi / zt

    def V(p) -> "FCad.Vector":
        return Vec(p[0], p[1], z_pos)

    edges = []
    for k in range(zt):
        rot = k * tooth_pitch + twist_rad
        lo = _rot2d(lower, rot)
        up = _rot2d(upper, rot)
        if r_root < r_base - 1e-9:
            edges.append(Part.LineSegment(V(_polar(r_root, ang_lo + rot)), V(lo[0])).toShape())
        bs = Part.BSplineCurve()
        bs.interpolate([V(p) for p in lo])
        edges.append(bs.toShape())
        edges.append(Part.Arc(V(lo[-1]), V(_polar(r_tip, rot)), V(up[-1])).toShape())
        bs2 = Part.BSplineCurve()
        bs2.interpolate([V(p) for p in reversed(up)])
        edges.append(bs2.toShape())
        if r_root < r_base - 1e-9:
            edges.append(Part.LineSegment(V(up[0]), V(_polar(r_root, -ang_lo + rot))).toShape())
            gap_start = -ang_lo + rot
        else:
            gap_start = math.atan2(up[0][1], up[0][0])
        gap_end = ang_lo + rot + tooth_pitch
        mid = (gap_start + gap_end) / 2.0
        edges.append(Part.Arc(V(_polar(r_root, gap_start)), V(_polar(r_root, mid)),
                              V(_polar(r_root, gap_end))).toShape())
    return Part.Wire(edges)


def involute_gear(doc, module: float, teeth: int, thickness: float,
                  pressure_angle: float = 20.0, helix_angle: float = 0.0,
                  bore_diameter: float = 0.0, name: str = "Gear",
                  position: Sequence[float] = (0, 0, 0)):
    """Involute gear solid: spur, or helical when ``helix_angle`` is non-zero.

    Pitch diameter = module * teeth. A helical gear is built as a solid loft
    through profile sections progressively rotated by the helix twist.
    """
    r_pitch = module * teeth / 2.0
    if helix_angle == 0.0:
        wire = _gear_profile_wire(module, teeth, pressure_angle)
        shape = Part.Face(wire).extrude(Vec(0, 0, thickness))
    else:
        twist_total = thickness * math.tan(math.radians(helix_angle)) / r_pitch
        n = max(3, int(abs(math.degrees(twist_total)) // 8) + 2)
        wires = [_gear_profile_wire(module, teeth, pressure_angle,
                                    z_pos=thickness * i / (n - 1),
                                    twist_rad=twist_total * i / (n - 1))
                 for i in range(n)]
        shape = Part.makeLoft(wires, True)
    if bore_diameter > 0:
        if bore_diameter / 2.0 >= r_pitch - 1.25 * module:
            raise ValueError("bore_diameter reaches into the tooth roots")
        shape = shape.cut(Part.makeCylinder(bore_diameter / 2.0, thickness + 2.0, Vec(0, 0, -1)))
    obj = doc.addObject("Part::Feature", name)
    obj.Shape = shape
    obj.Placement = FCad.Placement(Vec(*position), FCad.Rotation())
    doc.recompute()
    return obj


# --------------------------------------------------------------------------- #
# Fan / propeller rotor
# --------------------------------------------------------------------------- #
def fan_rotor(doc, hub_radius: float, hub_height: float, blade_count: int,
              blade_length: float, chord: float, blade_thickness: float = 2.0,
              root_twist: float = 35.0, tip_twist: float = 15.0, taper: float = 0.7,
              sections: int = 5, name: str = "FanRotor",
              position: Sequence[float] = (0, 0, 0)):
    """Axial fan / propeller: a cylindrical hub + ``blade_count`` twisted blades.

    Each blade is a solid loft through elliptical cross-sections that taper
    (``taper`` = tip chord / root chord) and twist from ``root_twist`` to
    ``tip_twist`` degrees along the radius — the helical pitch of the blade.
    ``blade_thickness`` is the ROOT thickness; sections keep a constant
    thickness-to-chord ratio, thinning toward the tip like a real blade (this
    also means no taper value can degenerate the tip section). The blades are
    arrayed about +Z and fused with the hub into one solid.
    """
    if blade_count < 2:
        raise ValueError("a rotor needs at least 2 blades")
    if blade_thickness > 0.8 * chord:
        raise ValueError("blade_thickness must be at most 0.8 x chord at the root")
    x0 = hub_radius * 0.6              # embed the root inside the hub for a clean fuse
    x1 = hub_radius + blade_length
    z_mid = hub_height / 2.0

    wires = []
    for i in range(sections):
        frac = i / (sections - 1)
        scale = 1.0 - (1.0 - taper) * frac
        chord_i = chord * scale
        twist_i = root_twist + (tip_twist - root_twist) * frac
        e = Part.Ellipse(Vec(0, 0, 0), chord_i / 2.0, blade_thickness * scale / 2.0)
        # Split the periodic ellipse into two explicit arcs: the loft matches
        # section VERTICES, so without them OCC re-aligns the closed curves to
        # minimise twist — silently removing the blade pitch.
        w = Part.Wire([Part.ArcOfEllipse(e, 0.0, math.pi).toShape(),
                       Part.ArcOfEllipse(e, math.pi, 2.0 * math.pi).toShape()])
        w.rotate(Vec(0, 0, 0), Vec(0, 1, 0), 90)             # section plane normal -> +X (radial)
        w.rotate(Vec(0, 0, 0), Vec(1, 0, 0), 90 + twist_i)   # chord along Y, then blade pitch
        w.translate(Vec(x0 + (x1 - x0) * frac, 0, z_mid))
        wires.append(w)
    blade = Part.makeLoft(wires, True)

    blades = []
    for k in range(blade_count):
        b = blade.copy()
        b.rotate(Vec(0, 0, 0), Vec(0, 0, 1), k * 360.0 / blade_count)
        blades.append(b)
    shape = Part.makeCylinder(hub_radius, hub_height).multiFuse(blades)
    try:
        shape = shape.removeSplitter()
    except Exception:
        pass  # refinement is cosmetic
    obj = doc.addObject("Part::Feature", name)
    obj.Shape = shape
    obj.Placement = FCad.Placement(Vec(*position), FCad.Rotation())
    doc.recompute()
    return obj


# --------------------------------------------------------------------------- #
# Coil spring
# --------------------------------------------------------------------------- #
def spring(doc, coil_radius: float, wire_radius: float, pitch: float, turns: float,
           name: str = "Spring", position: Sequence[float] = (0, 0, 0)):
    """Helical coil spring: a round wire swept along a helix (axis +Z)."""
    if pitch <= 2 * wire_radius:
        raise ValueError("pitch must exceed the wire diameter or the coils self-intersect")
    if wire_radius >= coil_radius:
        raise ValueError("wire_radius must be smaller than coil_radius")
    helix = Part.makeHelix(pitch, pitch * turns, coil_radius)
    tangent = Vec(0.0, 2 * math.pi * coil_radius, pitch)
    tangent.normalize()
    profile = Part.Wire([Part.Circle(Vec(coil_radius, 0, 0), tangent, wire_radius).toShape()])
    shape = Part.Wire(helix.Edges).makePipeShell([profile], True, True)
    obj = doc.addObject("Part::Feature", name)
    obj.Shape = shape
    obj.Placement = FCad.Placement(Vec(*position), FCad.Rotation())
    doc.recompute()
    return obj
