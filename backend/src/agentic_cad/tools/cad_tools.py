from __future__ import annotations
from pydantic import BaseModel, Field
from agentic_cad.cad import document, features, inspect
from agentic_cad.cad import geometry as g
from agentic_cad.tools.registry import ToolRegistry

registry = ToolRegistry()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _doc():
    try:
        return document.active_document()
    except RuntimeError:
        return document.new_document("Model")


def _obj(name: str):
    doc = _doc()
    obj = doc.getObject(name)
    if obj is None:
        raise ValueError(f"No object named {name!r}. Call get_feature_tree to list valid names.")
    return obj


def _summary(obj) -> dict:
    """Compact result returned after creating/modifying an object."""
    out = {"name": obj.Name, "label": obj.Label, "type": obj.TypeId}
    try:
        shape = obj.Shape
        out["volume_mm3"] = round(shape.Volume, 4)
        out["solid_count"] = len(shape.Solids)
    except Exception:
        pass
    return out


# --------------------------------------------------------------------------- #
# Document
# --------------------------------------------------------------------------- #
class NewDocumentArgs(BaseModel):
    name: str = Field("Model", description="Name for the new document")


@registry.tool("new_document", "Create and activate a new, empty CAD document.",
               NewDocumentArgs)
def new_document(a: NewDocumentArgs) -> dict:
    doc = document.new_document(a.name)
    return {"document": doc.Name}


class SaveDocumentArgs(BaseModel):
    filename: str | None = Field(None, description="Optional .FCStd path; defaults to local runtime dir")


@registry.tool("save_document", "Save the active document to disk (.FCStd).", SaveDocumentArgs)
def save_document(a: SaveDocumentArgs) -> dict:
    path = document.save_document(_doc(), a.filename)
    return {"saved_to": str(path)}


# --------------------------------------------------------------------------- #
# Primitives
# --------------------------------------------------------------------------- #
class BoxArgs(BaseModel):
    length: float = Field(..., gt=0, description="X size in mm")
    width: float = Field(..., gt=0, description="Y size in mm")
    height: float = Field(..., gt=0, description="Z size in mm")
    name: str = Field("Box", description="Name hint for the object")
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


@registry.tool("add_box", "Create a rectangular box (cuboid) solid.", BoxArgs)
def add_box(a: BoxArgs) -> dict:
    obj = g.add_box(_doc(), a.length, a.width, a.height, a.name, (a.x, a.y, a.z))
    return _summary(obj)


class CylinderArgs(BaseModel):
    radius: float = Field(..., gt=0, description="Radius in mm")
    height: float = Field(..., gt=0, description="Height in mm")
    name: str = Field("Cylinder", description="Name hint")
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


@registry.tool("add_cylinder", "Create a cylinder solid (axis along +Z by default).", CylinderArgs)
def add_cylinder(a: CylinderArgs) -> dict:
    obj = g.add_cylinder(_doc(), a.radius, a.height, a.name, (a.x, a.y, a.z))
    return _summary(obj)


class SphereArgs(BaseModel):
    radius: float = Field(..., gt=0)
    name: str = "Sphere"
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


@registry.tool("add_sphere", "Create a sphere solid.", SphereArgs)
def add_sphere(a: SphereArgs) -> dict:
    obj = g.add_sphere(_doc(), a.radius, a.name, (a.x, a.y, a.z))
    return _summary(obj)


class ConeArgs(BaseModel):
    radius1: float = Field(..., ge=0, description="Bottom radius mm")
    radius2: float = Field(..., ge=0, description="Top radius mm")
    height: float = Field(..., gt=0)
    name: str = "Cone"
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


@registry.tool("add_cone", "Create a (truncated) cone solid.", ConeArgs)
def add_cone(a: ConeArgs) -> dict:
    obj = g.add_cone(_doc(), a.radius1, a.radius2, a.height, a.name, (a.x, a.y, a.z))
    return _summary(obj)


# --------------------------------------------------------------------------- #
# Boolean operations
# --------------------------------------------------------------------------- #
class BooleanArgs(BaseModel):
    base: str = Field(..., description="Name of the base object")
    tool: str = Field(..., description="Name of the tool object")
    name: str = Field("Boolean", description="Name hint for the result")


@registry.tool("boolean_cut", "Subtract the tool object from the base (base - tool).", BooleanArgs)
def boolean_cut(a: BooleanArgs) -> dict:
    return _summary(g.boolean_cut(_doc(), _obj(a.base), _obj(a.tool), a.name))


@registry.tool("boolean_fuse", "Union (fuse) the base and tool objects.", BooleanArgs)
def boolean_fuse(a: BooleanArgs) -> dict:
    return _summary(g.boolean_fuse(_doc(), _obj(a.base), _obj(a.tool), a.name))


@registry.tool("boolean_common", "Intersection (common volume) of base and tool.", BooleanArgs)
def boolean_common(a: BooleanArgs) -> dict:
    return _summary(g.boolean_common(_doc(), _obj(a.base), _obj(a.tool), a.name))


# --------------------------------------------------------------------------- #
# Dress-up
# --------------------------------------------------------------------------- #
class FilletArgs(BaseModel):
    base: str = Field(..., description="Object whose edges to round")
    radius: float = Field(..., gt=0, description="Fillet radius mm")
    name: str = "Fillet"


@registry.tool("fillet_all_edges", "Round ALL edges of an object with a given radius.", FilletArgs)
def fillet_all_edges(a: FilletArgs) -> dict:
    return _summary(g.fillet_edges(_doc(), _obj(a.base), a.radius, None, a.name))


class ChamferArgs(BaseModel):
    base: str = Field(..., description="Object whose edges to chamfer")
    size: float = Field(..., gt=0, description="Chamfer size mm")
    name: str = "Chamfer"


@registry.tool("chamfer_all_edges", "Chamfer ALL edges of an object with a given size.", ChamferArgs)
def chamfer_all_edges(a: ChamferArgs) -> dict:
    return _summary(g.chamfer_edges(_doc(), _obj(a.base), a.size, None, a.name))


# --------------------------------------------------------------------------- #
# Parametric edit + transform
# --------------------------------------------------------------------------- #
class SetPropertyArgs(BaseModel):
    name: str = Field(..., description="Object name")
    property: str = Field(..., description="Property to change, e.g. Length, Radius")
    value: float = Field(..., description="New numeric value (mm or deg)")


@registry.tool("set_property",
               "Change a numeric property of an object and recompute "
               "(the edit-by-description primitive, e.g. Length, Radius).", SetPropertyArgs)
def set_property(a: SetPropertyArgs) -> dict:
    return _summary(g.set_property(_obj(a.name), a.property, a.value))


class TranslateArgs(BaseModel):
    name: str = Field(..., description="Object name")
    dx: float = 0.0
    dy: float = 0.0
    dz: float = 0.0


@registry.tool("translate", "Move an object by a relative (dx, dy, dz) vector in mm.", TranslateArgs)
def translate(a: TranslateArgs) -> dict:
    return _summary(g.translate(_obj(a.name), (a.dx, a.dy, a.dz)))


# --------------------------------------------------------------------------- #
# Parametric sketch spine
# --------------------------------------------------------------------------- #
class NewSketchArgs(BaseModel):
    plane: str = Field("XY", description="Datum plane: XY, XZ or YZ")
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    name: str = "Sketch"


@registry.tool("new_sketch", "Create a sketch on a datum plane (XY/XZ/YZ).", NewSketchArgs)
def new_sketch(a: NewSketchArgs) -> dict:
    sk = features.new_sketch(_doc(), a.plane, (a.x, a.y, a.z), a.name)
    return {"name": sk.Name, "plane": a.plane}


class SketchRectArgs(BaseModel):
    sketch: str = Field(..., description="Name of the sketch to draw in")
    width: float = Field(..., gt=0)
    height: float = Field(..., gt=0)
    corner_x: float = 0.0
    corner_y: float = 0.0


@registry.tool("sketch_rectangle", "Add a closed rectangle to a sketch.", SketchRectArgs)
def sketch_rectangle(a: SketchRectArgs) -> dict:
    ids = features.sketch_rectangle(_obj(a.sketch), a.width, a.height, (a.corner_x, a.corner_y))
    return {"sketch": a.sketch, "geometry_ids": ids}


class SketchCircleArgs(BaseModel):
    sketch: str = Field(..., description="Name of the sketch to draw in")
    center_x: float = 0.0
    center_y: float = 0.0
    radius: float = Field(..., gt=0)


@registry.tool("sketch_circle",
               "Add a circle (with a driving radius constraint) to a sketch. "
               "Returns radius_constraint to use with set_datum.", SketchCircleArgs)
def sketch_circle(a: SketchCircleArgs) -> dict:
    info = features.sketch_circle(_obj(a.sketch), (a.center_x, a.center_y), a.radius)
    return {"sketch": a.sketch, **info}


class SetDatumArgs(BaseModel):
    sketch: str = Field(..., description="Sketch name")
    constraint_id: int = Field(..., description="Constraint index (e.g. radius_constraint)")
    value: float = Field(..., description="New dimension value in mm")


@registry.tool("set_datum",
               "Change a dimensional constraint in a sketch (parametric edit).", SetDatumArgs)
def set_datum(a: SetDatumArgs) -> dict:
    features.set_datum(_obj(a.sketch), a.constraint_id, a.value)
    return {"sketch": a.sketch, "constraint_id": a.constraint_id, "value": a.value}


class ExtrudeArgs(BaseModel):
    sketch: str = Field(..., description="Sketch (closed profile) to extrude")
    length: float = Field(..., gt=0, description="Extrusion length mm")
    solid: bool = True
    name: str = "Pad"


@registry.tool("extrude", "Extrude a closed sketch into a solid along its normal.",
               ExtrudeArgs)
def extrude(a: ExtrudeArgs) -> dict:
    return _summary(features.extrude(_doc(), _obj(a.sketch), a.length, a.solid, name=a.name))


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #
class ExportArgs(BaseModel):
    name: str = Field(..., description="Object to export")
    filename: str = Field(..., description="Output path/filename")


@registry.tool("export_step", "Export an object to a STEP file (exact geometry).", ExportArgs)
def export_step(a: ExportArgs) -> dict:
    path = g.export_step(_obj(a.name), a.filename)
    return {"exported": str(path)}


@registry.tool("export_stl", "Export an object to an STL mesh file.", ExportArgs)
def export_stl(a: ExportArgs) -> dict:
    path = g.export_stl(_obj(a.name), a.filename)
    return {"exported": str(path)}


# --------------------------------------------------------------------------- #
# Inspection (read-only grounding)
# --------------------------------------------------------------------------- #
class NoArgs(BaseModel):
    pass


@registry.tool("get_feature_tree",
               "List every object in the document with its parameters "
               "(use this to ground reasoning and get valid object names).", NoArgs)
def get_feature_tree(a: NoArgs) -> dict:
    return {"objects": inspect.feature_tree(_doc())}


class DescribeArgs(BaseModel):
    name: str = Field(..., description="Object name to describe")


@registry.tool("describe_object",
               "Full report on one object: parameters, bounding box, mass properties.", DescribeArgs)
def describe_object(a: DescribeArgs) -> dict:
    return inspect.describe(_obj(a.name))


@registry.tool("mass_properties",
               "Volume, surface area, centre of mass and solid count of an object.", DescribeArgs)
def mass_properties(a: DescribeArgs) -> dict:
    return inspect.mass_properties(_obj(a.name))
