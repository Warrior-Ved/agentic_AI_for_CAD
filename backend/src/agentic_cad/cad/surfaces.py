"""Multi-surface modelling ops: revolve, loft, sweep, helix and polar arrays.

These are the Part-workbench features that unlock parts a plain
sketch -> extrude -> boolean pipeline cannot express: bodies of revolution
(pulleys, flanges), blended sections (lofted blades), helical sweeps
(springs, threads) and features repeated around an axis (bolt circles).
"""
from __future__ import annotations
from typing import Sequence
from agentic_cad.cad import bootstrap
import FreeCAD as FCad

bootstrap.ensure_freecad_importable()
Vec = FCad.Vector


def revolve(doc, sketch, angle_deg: float = 360.0, axis: Sequence[float] = (0, 0, 1),
            base: Sequence[float] = (0, 0, 0), solid: bool = True, name: str = "Revolve"):
    """Revolve a closed sketch about an axis through ``base`` into a solid."""
    rev = doc.addObject("Part::Revolution", name)
    rev.Source = sketch
    rev.Axis = Vec(*axis)
    rev.Base = Vec(*base)
    rev.Angle = angle_deg
    rev.Solid = solid
    sketch.Visibility = False
    doc.recompute()
    if solid and len(rev.Shape.Solids) == 0:
        raise RuntimeError(f"Revolution {name!r} produced no solid — is the sketch a closed profile?")
    return rev


def loft(doc, sections: Sequence, solid: bool = True, ruled: bool = False, name: str = "Loft"):
    """Loft (blend) through two or more closed profiles, in the order given."""
    if len(sections) < 2:
        raise ValueError("loft needs at least two section profiles")
    lf = doc.addObject("Part::Loft", name)
    lf.Sections = list(sections)
    lf.Solid = solid
    lf.Ruled = ruled
    lf.Closed = False
    for s in sections:
        s.Visibility = False
    doc.recompute()
    if solid and len(lf.Shape.Solids) == 0:
        raise RuntimeError(f"Loft {name!r} produced no solid — are all profiles closed?")
    return lf


def sweep(doc, profile, spine, solid: bool = True, frenet: bool = True, name: str = "Sweep"):
    """Sweep a closed profile along a spine path (a sketch, wire or helix)."""
    sw = doc.addObject("Part::Sweep", name)
    sw.Sections = [profile]
    sw.Spine = (spine, [])
    sw.Solid = solid
    sw.Frenet = frenet
    profile.Visibility = False
    spine.Visibility = False
    doc.recompute()
    if solid and len(sw.Shape.Solids) == 0:
        raise RuntimeError(f"Sweep {name!r} produced no solid — closed profile and connected spine?")
    return sw


def add_helix(doc, radius: float, pitch: float, height: float, name: str = "Helix",
              position: Sequence[float] = (0, 0, 0), left_handed: bool = False):
    """Helical wire around +Z (a sweep spine for springs/threads — not a solid)."""
    hx = doc.addObject("Part::Helix", name)
    hx.Radius, hx.Pitch, hx.Height = radius, pitch, height
    hx.LocalCoord = 1 if left_handed else 0
    hx.Placement = FCad.Placement(Vec(*position), FCad.Rotation())
    doc.recompute()
    return hx


def polar_array(doc, obj, count: int, axis: Sequence[float] = (0, 0, 1),
                center: Sequence[float] = (0, 0, 0), total_angle_deg: float = 360.0,
                name: str = "PolarArray"):
    """``count`` copies of ``obj`` spread about an axis, fused into one feature.

    A full circle spaces copies by 360/count; a partial ``total_angle_deg``
    places the first copy at 0 deg and the last at the full angle.
    """
    if count < 2:
        raise ValueError("polar_array needs count >= 2")
    step = total_angle_deg / count if total_angle_deg >= 360.0 else total_angle_deg / (count - 1)
    copies = []
    for i in range(count):
        s = obj.Shape.copy()
        s.rotate(Vec(*center), Vec(*axis), i * step)
        copies.append(s)
    shape = copies[0].multiFuse(copies[1:])
    try:
        shape = shape.removeSplitter()
    except Exception:
        pass  # refinement is cosmetic; the unrefined fuse is still valid
    arr = doc.addObject("Part::Feature", name)
    arr.Shape = shape
    obj.Visibility = False
    doc.recompute()
    return arr
