from __future__ import annotations
from agentic_cad.cad import bootstrap

# Ensure FreeCAD is importable; inspection only touches obj.Shape, so we don't bind the module itself here.
bootstrap.ensure_freecad_importable()


def bbox(obj) -> dict:
    """Axis-aligned bounding box of an object's shape, in mm."""
    bb = obj.Shape.BoundBox
    return {"x_min": bb.XMin, "y_min": bb.YMin, "z_min": bb.ZMin, "x_max": bb.XMax, "y_max": bb.YMax, 
            "z_max": bb.ZMax, "x_len": bb.XLength, "y_len": bb.YLength, "z_len": bb.ZLength}


def _center_of_mass(shape):
    """Centre of mass, robust to Compounds (which lack ``CenterOfMass``)."""
    try:
        com = shape.CenterOfMass
        return [com.x, com.y, com.z]
    except (AttributeError, Exception):
        solids = shape.Solids
        if not solids:
            return None
        total = sum(s.Volume for s in solids) or 1.0
        cx = sum(s.CenterOfMass.x * s.Volume for s in solids) / total
        cy = sum(s.CenterOfMass.y * s.Volume for s in solids) / total
        cz = sum(s.CenterOfMass.z * s.Volume for s in solids) / total
        return [cx, cy, cz]


def mass_properties(obj) -> dict:
    """Volume, surface area and centre of mass of an object's shape."""
    shape = obj.Shape
    return {"volume_mm3": shape.Volume, "area_mm2": shape.Area, "center_of_mass": _center_of_mass(shape),
            "solid_count": len(shape.Solids), "face_count": len(shape.Faces), "edge_count": len(shape.Edges),
            "is_closed": bool(shape.Solids) and all(s.isClosed() for s in shape.Solids)}


def object_summary(obj) -> dict:
    """Type + the editable numeric/parametric properties of one object."""
    params = {}
    for prop in obj.PropertiesList:
        group = obj.getGroupOfProperty(prop)
        if group in ("Box", "Cylinder", "Sphere", "Cone", "Attachment", ""):
            try:
                value = getattr(obj, prop)
            except Exception:
                continue
            # Quantities (mm, deg) -> float; keep simple scalars only.
            if hasattr(value, "Value"):
                params[prop] = value.Value
            elif isinstance(value, (int, float, bool, str)):
                params[prop] = value
    return {"name": obj.Name, "label": obj.Label, "type": obj.TypeId, "visible": getattr(obj, "Visibility", None), 
            "parameters": params}


def feature_tree(doc) -> list[dict]:
    """Summarise every object in the document (the feature tree the agent reads)."""
    return [object_summary(o) for o in doc.Objects]


def describe(obj) -> dict:
    """Full picture of one object: parameters + geometry + mass properties."""
    out = object_summary(obj)
    try:
        out["bbox"] = bbox(obj)
        out["mass_properties"] = mass_properties(obj)
    except Exception as exc: 
        out["geometry_error"] = str(exc)
    return out
