from __future__ import annotations
from typing import Sequence
from agentic_cad.cad import bootstrap
import FreeCAD as FCad  
import Part  
import Sketcher  

bootstrap.ensure_freecad_importable()
Vec = FCad.Vector

# Standard datum-plane placements (origin, normal along the named convention).
_PLANE_PLACEMENTS = {"XY": FCad.Placement(Vec(0, 0, 0), FCad.Rotation()),
                     "XZ": FCad.Placement(Vec(0, 0, 0), FCad.Rotation(Vec(1, 0, 0), 90)),
                     "YZ": FCad.Placement(Vec(0, 0, 0), FCad.Rotation(Vec(0, 1, 0), -90))}


def new_sketch(doc, plane: str = "XY", position: Sequence[float] = (0, 0, 0), name: str = "Sketch"):
    """Create a sketch on a named datum plane (XY/XZ/YZ), offset by ``position``."""
    if plane not in _PLANE_PLACEMENTS:
        raise ValueError(f"plane must be one of {list(_PLANE_PLACEMENTS)}")
    sk = doc.addObject("Sketcher::SketchObject", name)
    base = _PLANE_PLACEMENTS[plane]
    sk.Placement = FCad.Placement(Vec(*position), FCad.Rotation()).multiply(base)
    doc.recompute()
    return sk


def sketch_rectangle(sketch, width: float, height: float, corner: Sequence[float] = (0, 0)) -> list[int]:
    """Add a closed, coincidence-constrained rectangle. Returns the line geo ids."""
    x0, y0 = corner
    pts = [(x0, y0), (x0 + width, y0), (x0 + width, y0 + height), (x0, y0 + height)]
    geo = [Part.LineSegment(Vec(*pts[i], 0), Vec(*pts[(i + 1) % 4], 0)) for i in range(4)]
    ids = sketch.addGeometry(geo, False)
    for i in range(4):
        sketch.addConstraint(Sketcher.Constraint("Coincident", ids[i], 2, ids[(i + 1) % 4], 1))
    sketch.Document.recompute()
    return list(ids)


def sketch_circle(sketch, center: Sequence[float], radius: float) -> dict:
    """Add a circle with a driving radius constraint.

    Returns ``{"geo_id", "radius_constraint"}`` — pass ``radius_constraint`` to
    :func:`set_datum` to change the radius parametrically later.
    """
    cx, cy = center
    geo_id = sketch.addGeometry(Part.Circle(Vec(cx, cy, 0), Vec(0, 0, 1), radius), False)
    con_id = sketch.addConstraint(Sketcher.Constraint("Radius", geo_id, radius))
    sketch.Document.recompute()
    return {"geo_id": geo_id, "radius_constraint": con_id}


def set_datum(sketch, constraint_id: int, value: float, doc=None):
    """Change a dimensional constraint (the parametric edit primitive)."""
    sketch.setDatum(constraint_id, FCad.Units.Quantity(f"{value} mm"))
    (doc or sketch.Document).recompute()
    return sketch


def extrude(doc, sketch, length: float, solid: bool = True, reversed: bool = False, name: str = "Pad"):
    """Extrude a (closed) sketch normal to its plane into a solid."""
    ext = doc.addObject("Part::Extrusion", name)
    ext.Base = sketch
    ext.DirMode = "Normal"
    ext.LengthFwd = length
    ext.Solid = solid
    ext.Reversed = reversed
    sketch.Visibility = False
    doc.recompute()
    if solid and len(ext.Shape.Solids) == 0:
        raise RuntimeError(f"Extrusion {name!r} produced no solid — is the sketch a closed profile?")
    return ext
