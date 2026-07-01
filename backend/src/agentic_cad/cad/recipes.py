from __future__ import annotations
from agentic_cad.cad import document, features


def holed_block(length: float = 2.0, width: float = 2.0, height: float = 1.0, hole_diameter: float = 1.0, doc=None) -> dict:
    """Build a rectangular block with a single through-hole at its centre.

    Returns handles to the key objects plus the radius-constraint id, so callers
    can demonstrate a parametric edit (resize the hole) without rebuilding.
    """
    doc = doc or document.new_document("HoledBlock")

    base_sk = features.new_sketch(doc, "XY", name="BaseProfile")
    features.sketch_rectangle(base_sk, length, width)
    block = features.extrude(doc, base_sk, height, name="Block")

    hole_sk = features.new_sketch(doc, "XY", name="HoleProfile")
    hole = features.sketch_circle(hole_sk, (length / 2, width / 2), hole_diameter / 2)
    hole_solid = features.extrude(doc, hole_sk, height, name="HoleTool")

    result = doc.addObject("Part::Cut", "HoledBlock")
    result.Base = block
    result.Tool = hole_solid
    block.Visibility = False
    hole_solid.Visibility = False
    doc.recompute()

    return {"doc": doc, "result": result, "hole_sketch": hole_sk, 
            "hole_radius_constraint": hole["radius_constraint"]}
