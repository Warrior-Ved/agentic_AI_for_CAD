from __future__ import annotations
from pydantic import BaseModel, ConfigDict, Field, model_validator
from agentic_cad.cad import document, features, ingest, inspect, parts, simulation, surfaces
from agentic_cad.cad import geometry as g
from agentic_cad.tools.registry import ToolRegistry

registry = ToolRegistry()


class ToolArgs(BaseModel):
    """Base for every tool's argument model: unknown arguments are ERRORS
    (extra="forbid"), so a planner that hallucinates a parameter is corrected
    by schema feedback at PLAN time instead of being silently ignored."""

    model_config = ConfigDict(extra="forbid")


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


def _target(name: str = ""):
    """Named object, or the last visible solid — 'the part' — when name is empty."""
    if name:
        return _obj(name)
    for obj in reversed(_doc().Objects):
        try:
            if obj.Visibility and len(obj.Shape.Solids) >= 1:
                return obj
        except Exception:
            continue
    raise ValueError("no solid in the document to analyse — build a part first")


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
class NewDocumentArgs(ToolArgs):
    name: str = Field("Model", description="Name for the new document")


@registry.tool("new_document", "Create and activate a new, empty CAD document.",
               NewDocumentArgs)
def new_document(a: NewDocumentArgs) -> dict:
    doc = document.new_document(a.name)
    return {"document": doc.Name}


class SaveDocumentArgs(ToolArgs):
    filename: str | None = Field(None, description="Optional .FCStd path; defaults to local runtime dir")


@registry.tool("save_document", "Save the active document to disk (.FCStd).", SaveDocumentArgs)
def save_document(a: SaveDocumentArgs) -> dict:
    path = document.save_document(_doc(), a.filename)
    return {"saved_to": str(path)}


# --------------------------------------------------------------------------- #
# Primitives
# --------------------------------------------------------------------------- #
class BoxArgs(ToolArgs):
    length: float = Field(..., gt=0, description="X size in mm")
    width: float = Field(..., gt=0, description="Y size in mm")
    height: float = Field(..., gt=0, description="Z size in mm")
    name: str = Field("Box", description="Name hint for the object")
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    centered: bool = Field(False, description="True: (x,y,z) is the box CENTRE "
                                              "(like a cylinder); False: its min corner")


@registry.tool("add_box",
               "Create a rectangular box (cuboid) solid. Set centered=true to place it "
               "by its centre — best for cutters and centred features.", BoxArgs)
def add_box(a: BoxArgs) -> dict:
    obj = g.add_box(_doc(), a.length, a.width, a.height, a.name, (a.x, a.y, a.z), a.centered)
    return _summary(obj)


class CylinderArgs(ToolArgs):
    radius: float = Field(..., gt=0, description="Radius in mm")
    height: float = Field(..., gt=0, description="Height in mm")
    name: str = Field("Cylinder", description="Name hint")
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    axis_x: float = Field(0.0, description="Axis direction (default 0,0,1 = +Z)")
    axis_y: float = 0.0
    axis_z: float = 1.0
    centered: bool = Field(False, description="True: (x,y,z) is the cylinder's MID-HEIGHT "
                                              "centre; False: its base-circle centre")


@registry.tool("add_cylinder",
               "Create a cylinder solid (axis +Z by default; tilt with axis_x/axis_y/axis_z; "
               "set centered=true to place it by its mid-height centre — best for cutters).",
               CylinderArgs)
def add_cylinder(a: CylinderArgs) -> dict:
    obj = g.add_cylinder(_doc(), a.radius, a.height, a.name, (a.x, a.y, a.z),
                         (a.axis_x, a.axis_y, a.axis_z), a.centered)
    return _summary(obj)


class SphereArgs(ToolArgs):
    radius: float = Field(..., gt=0)
    name: str = "Sphere"
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


@registry.tool("add_sphere", "Create a sphere solid.", SphereArgs)
def add_sphere(a: SphereArgs) -> dict:
    obj = g.add_sphere(_doc(), a.radius, a.name, (a.x, a.y, a.z))
    return _summary(obj)


class ConeArgs(ToolArgs):
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


class TorusArgs(ToolArgs):
    radius1: float = Field(..., gt=0, description="Ring (major) radius mm")
    radius2: float = Field(..., gt=0, description="Tube (minor) radius mm")
    name: str = "Torus"
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


@registry.tool("add_torus", "Create a torus (ring / donut) solid around +Z.", TorusArgs)
def add_torus(a: TorusArgs) -> dict:
    obj = g.add_torus(_doc(), a.radius1, a.radius2, a.name, (a.x, a.y, a.z))
    return _summary(obj)


# --------------------------------------------------------------------------- #
# Boolean operations
# --------------------------------------------------------------------------- #
class BooleanArgs(ToolArgs):
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
class FilletArgs(ToolArgs):
    base: str = Field(..., description="Object whose edges to round")
    radius: float = Field(..., gt=0, description="Fillet radius mm")
    name: str = "Fillet"


@registry.tool("fillet_all_edges", "Round ALL edges of an object with a given radius.", FilletArgs)
def fillet_all_edges(a: FilletArgs) -> dict:
    return _summary(g.fillet_edges(_doc(), _obj(a.base), a.radius, None, a.name))


class ChamferArgs(ToolArgs):
    base: str = Field(..., description="Object whose edges to chamfer")
    size: float = Field(..., gt=0, description="Chamfer size mm")
    name: str = "Chamfer"


@registry.tool("chamfer_all_edges", "Chamfer ALL edges of an object with a given size.", ChamferArgs)
def chamfer_all_edges(a: ChamferArgs) -> dict:
    return _summary(g.chamfer_edges(_doc(), _obj(a.base), a.size, None, a.name))


# --------------------------------------------------------------------------- #
# Parametric edit + transform
# --------------------------------------------------------------------------- #
class SetPropertyArgs(ToolArgs):
    name: str = Field(..., description="Object name")
    property: str = Field(..., description="Property to change, e.g. Length, Radius")
    value: float = Field(..., description="New numeric value (mm or deg)")


@registry.tool("set_property",
               "Change a numeric property of an object and recompute "
               "(the edit-by-description primitive, e.g. Length, Radius).", SetPropertyArgs)
def set_property(a: SetPropertyArgs) -> dict:
    return _summary(g.set_property(_obj(a.name), a.property, a.value))


class TranslateArgs(ToolArgs):
    name: str = Field(..., description="Object name")
    dx: float = 0.0
    dy: float = 0.0
    dz: float = 0.0


@registry.tool("translate", "Move an object by a relative (dx, dy, dz) vector in mm.", TranslateArgs)
def translate(a: TranslateArgs) -> dict:
    return _summary(g.translate(_obj(a.name), (a.dx, a.dy, a.dz)))


class RotateArgs(ToolArgs):
    name: str = Field(..., description="Object name")
    angle: float = Field(..., description="Rotation angle in degrees")
    axis_x: float = Field(0.0, description="Rotation axis direction (default 0,0,1 = +Z)")
    axis_y: float = 0.0
    axis_z: float = 1.0
    center_x: float = Field(0.0, description="Point the axis passes through")
    center_y: float = 0.0
    center_z: float = 0.0


@registry.tool("rotate", "Rotate an object about an axis through a centre point (relative, degrees).",
               RotateArgs)
def rotate(a: RotateArgs) -> dict:
    return _summary(g.rotate(_obj(a.name), (a.axis_x, a.axis_y, a.axis_z), a.angle,
                             (a.center_x, a.center_y, a.center_z)))


# --------------------------------------------------------------------------- #
# Parametric sketch spine
# --------------------------------------------------------------------------- #
class NewSketchArgs(ToolArgs):
    plane: str = Field("XY", description="Datum plane: XY, XZ or YZ")
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    name: str = "Sketch"


@registry.tool("new_sketch", "Create a sketch on a datum plane (XY/XZ/YZ).", NewSketchArgs)
def new_sketch(a: NewSketchArgs) -> dict:
    sk = features.new_sketch(_doc(), a.plane, (a.x, a.y, a.z), a.name)
    return {"name": sk.Name, "plane": a.plane}


class SketchRectArgs(ToolArgs):
    sketch: str = Field(..., description="Name of the sketch to draw in")
    width: float = Field(..., gt=0)
    height: float = Field(..., gt=0)
    corner_x: float = 0.0
    corner_y: float = 0.0


@registry.tool("sketch_rectangle", "Add a closed rectangle to a sketch.", SketchRectArgs)
def sketch_rectangle(a: SketchRectArgs) -> dict:
    ids = features.sketch_rectangle(_obj(a.sketch), a.width, a.height, (a.corner_x, a.corner_y))
    return {"sketch": a.sketch, "geometry_ids": ids}


class SketchCircleArgs(ToolArgs):
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


class SketchPolygonArgs(ToolArgs):
    sketch: str = Field(..., description="Name of the sketch to draw in")
    sides: int = Field(..., ge=3, le=64, description="Number of sides (6 = hexagon)")
    radius: float = Field(..., gt=0, description="Centre-to-vertex radius mm")
    center_x: float = 0.0
    center_y: float = 0.0
    rotation: float = Field(0.0, description="Rotation of the polygon in degrees")


@registry.tool("sketch_polygon",
               "Add a closed regular polygon to a sketch (e.g. hexagon for nuts / bolt heads).",
               SketchPolygonArgs)
def sketch_polygon(a: SketchPolygonArgs) -> dict:
    ids = features.sketch_polygon(_obj(a.sketch), a.sides, a.radius,
                                  (a.center_x, a.center_y), a.rotation)
    return {"sketch": a.sketch, "geometry_ids": ids}


class SketchEllipseArgs(ToolArgs):
    sketch: str = Field(..., description="Name of the sketch to draw in")
    major_radius: float = Field(..., gt=0, description="Half-length along X, mm")
    minor_radius: float = Field(..., gt=0, description="Half-width along Y, mm (<= major)")
    center_x: float = 0.0
    center_y: float = 0.0


@registry.tool("sketch_ellipse",
               "Add an ellipse to a sketch (blade sections, oval bosses/slots).", SketchEllipseArgs)
def sketch_ellipse(a: SketchEllipseArgs) -> dict:
    geo_id = features.sketch_ellipse(_obj(a.sketch), (a.center_x, a.center_y),
                                     a.major_radius, a.minor_radius)
    return {"sketch": a.sketch, "geo_id": geo_id}


class SetDatumArgs(ToolArgs):
    sketch: str = Field(..., description="Sketch name")
    constraint_id: int = Field(..., description="Constraint index (e.g. radius_constraint)")
    value: float = Field(..., description="New dimension value in mm")


@registry.tool("set_datum",
               "Change a dimensional constraint in a sketch (parametric edit).", SetDatumArgs)
def set_datum(a: SetDatumArgs) -> dict:
    features.set_datum(_obj(a.sketch), a.constraint_id, a.value)
    return {"sketch": a.sketch, "constraint_id": a.constraint_id, "value": a.value}


class ExtrudeArgs(ToolArgs):
    sketch: str = Field(..., description="Sketch (closed profile) to extrude")
    length: float = Field(..., gt=0, description="Extrusion length mm")
    solid: bool = True
    name: str = "Pad"


@registry.tool("extrude", "Extrude a closed sketch into a solid along its normal.",
               ExtrudeArgs)
def extrude(a: ExtrudeArgs) -> dict:
    return _summary(features.extrude(_doc(), _obj(a.sketch), a.length, a.solid, name=a.name))


# --------------------------------------------------------------------------- #
# Multi-surface features (revolve / loft / sweep / helix / arrays)
# --------------------------------------------------------------------------- #
class RevolveArgs(ToolArgs):
    sketch: str = Field(..., description="Closed-profile sketch to revolve")
    angle: float = Field(360.0, gt=0, le=360, description="Sweep angle in degrees")
    axis_x: float = Field(0.0, description="Rotation axis direction (default 0,0,1 = +Z)")
    axis_y: float = 0.0
    axis_z: float = 1.0
    base_x: float = Field(0.0, description="Point the axis passes through")
    base_y: float = 0.0
    base_z: float = 0.0
    name: str = "Revolve"


@registry.tool("revolve",
               "Revolve a closed sketch about an axis into a solid of revolution "
               "(pulleys, flanges, domes, vases, bottles).", RevolveArgs)
def revolve(a: RevolveArgs) -> dict:
    return _summary(surfaces.revolve(_doc(), _obj(a.sketch), a.angle,
                                     (a.axis_x, a.axis_y, a.axis_z),
                                     (a.base_x, a.base_y, a.base_z), name=a.name))


class LoftArgs(ToolArgs):
    sections: list[str] = Field(..., min_length=2,
                                description="Names of 2+ closed sketches to blend through, in order")
    ruled: bool = Field(False, description="True = straight (ruled) transitions between sections")
    name: str = "Loft"


@registry.tool("loft",
               "Loft (blend) a solid through two or more closed sketch profiles at different "
               "positions — for transitions and blade-like shapes.", LoftArgs)
def loft(a: LoftArgs) -> dict:
    return _summary(surfaces.loft(_doc(), [_obj(n) for n in a.sections], True, a.ruled, a.name))


class SweepArgs(ToolArgs):
    profile: str = Field(..., description="Closed-profile sketch to sweep")
    spine: str = Field(..., description="Path object: a sketch or a helix")
    frenet: bool = Field(True, description="Rotate the profile with the path (needed on helical spines)")
    name: str = "Sweep"


@registry.tool("sweep",
               "Sweep a closed profile along a spine path (use an add_helix spine for "
               "springs / threads; a sketch path for pipes and rails).", SweepArgs)
def sweep(a: SweepArgs) -> dict:
    return _summary(surfaces.sweep(_doc(), _obj(a.profile), _obj(a.spine),
                                   frenet=a.frenet, name=a.name))


class HelixArgs(ToolArgs):
    radius: float = Field(..., gt=0, description="Helix radius mm")
    pitch: float = Field(..., gt=0, description="Height gained per turn, mm")
    height: float = Field(..., gt=0, description="Total height, mm")
    left_handed: bool = False
    name: str = "Helix"
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


@registry.tool("add_helix",
               "Create a helical wire around +Z — a path for sweep, not a solid by itself.",
               HelixArgs)
def add_helix(a: HelixArgs) -> dict:
    hx = surfaces.add_helix(_doc(), a.radius, a.pitch, a.height, a.name,
                            (a.x, a.y, a.z), a.left_handed)
    return {"name": hx.Name, "label": hx.Label, "type": hx.TypeId}


class PolarArrayArgs(ToolArgs):
    base: str = Field(..., description="Object to repeat")
    count: int = Field(..., ge=2, le=100, description="Total number of copies")
    total_angle: float = Field(360.0, gt=0, le=360, description="Arc to spread over, degrees")
    center_x: float = Field(0.0, description="Centre of rotation (axis is +Z)")
    center_y: float = 0.0
    name: str = "PolarArray"


@registry.tool("polar_array",
               "Repeat an object N times around the Z axis and fuse into one solid "
               "(bolt-hole circles, spokes, blades).", PolarArrayArgs)
def polar_array(a: PolarArrayArgs) -> dict:
    return _summary(surfaces.polar_array(_doc(), _obj(a.base), a.count, (0, 0, 1),
                                         (a.center_x, a.center_y, 0), a.total_angle, a.name))


# --------------------------------------------------------------------------- #
# Parametric part generators (complex multi-surface parts in one call)
# --------------------------------------------------------------------------- #
class GearArgs(ToolArgs):
    module_mm: float = Field(..., gt=0,
                             description="Gear module in mm (tooth size; pitch diameter = module * teeth)")
    teeth: int = Field(..., ge=6, le=200, description="Number of teeth")
    thickness: float = Field(..., gt=0, description="Face width (extrusion depth) mm")
    pressure_angle: float = Field(20.0, ge=14, le=30, description="Pressure angle, degrees (20 standard)")
    helix_angle: float = Field(0.0, ge=-45, le=45,
                               description="0 = spur gear; non-zero = helical gear, degrees")
    bore_diameter: float = Field(0.0, ge=0, description="Central shaft hole diameter mm (0 = none)")
    name: str = "Gear"
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    @model_validator(mode="after")
    def _bore_fits_inside_root_circle(self):
        root_diameter = self.module_mm * self.teeth - 2.5 * self.module_mm
        if self.bore_diameter > 0 and self.bore_diameter >= root_diameter:
            raise ValueError(f"bore_diameter must be below the tooth root diameter "
                             f"({root_diameter:.1f} mm for this module/teeth)")
        return self


@registry.tool("add_involute_gear",
               "Create a parametric involute gear in ONE call (spur, or helical when "
               "helix_angle != 0). Pitch diameter = module_mm * teeth.", GearArgs)
def add_involute_gear(a: GearArgs) -> dict:
    return _summary(parts.involute_gear(_doc(), a.module_mm, a.teeth, a.thickness,
                                        a.pressure_angle, a.helix_angle, a.bore_diameter,
                                        a.name, (a.x, a.y, a.z)))


class FanRotorArgs(ToolArgs):
    hub_radius: float = Field(..., gt=0, description="Hub cylinder radius mm")
    hub_height: float = Field(..., gt=0, description="Hub cylinder height mm")
    blade_count: int = Field(..., ge=2, le=24, description="Number of blades")
    blade_length: float = Field(..., gt=0, description="Radial blade length beyond the hub, mm")
    chord: float = Field(..., gt=0, description="Blade width at the root, mm")
    blade_thickness: float = Field(2.0, gt=0,
                                   description="Blade thickness at the root, mm (thins toward the tip)")
    root_twist: float = Field(35.0, ge=-80, le=80, description="Blade pitch angle at the hub, degrees")
    tip_twist: float = Field(15.0, ge=-80, le=80, description="Blade pitch angle at the tip, degrees")
    taper: float = Field(0.7, gt=0, le=1, description="Tip chord / root chord ratio (0.5-1 typical)")
    name: str = "FanRotor"
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    @model_validator(mode="after")
    def _blade_section_sane(self):
        if self.blade_thickness > 0.8 * self.chord:
            raise ValueError(f"blade_thickness ({self.blade_thickness} mm) must be at most "
                             f"0.8 x chord ({0.8 * self.chord:.1f} mm)")
        return self


@registry.tool("add_fan_rotor",
               "Create a fan / propeller / impeller in ONE call: cylindrical hub + N helically "
               "twisted, tapered, lofted blades fused into a single solid.", FanRotorArgs)
def add_fan_rotor(a: FanRotorArgs) -> dict:
    return _summary(parts.fan_rotor(_doc(), a.hub_radius, a.hub_height, a.blade_count,
                                    a.blade_length, a.chord, a.blade_thickness,
                                    a.root_twist, a.tip_twist, a.taper,
                                    name=a.name, position=(a.x, a.y, a.z)))


class SpringArgs(ToolArgs):
    coil_radius: float = Field(..., gt=0, description="Coil (helix) radius mm")
    wire_radius: float = Field(..., gt=0, description="Wire radius mm")
    pitch: float = Field(..., gt=0, description="Height per turn mm (must exceed wire diameter)")
    turns: float = Field(..., gt=0, description="Number of coils")
    name: str = "Spring"
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    @model_validator(mode="after")
    def _coils_do_not_touch(self):
        if self.pitch <= 2 * self.wire_radius:
            raise ValueError(f"pitch ({self.pitch} mm) must exceed the wire diameter "
                             f"({2 * self.wire_radius} mm) or the coils self-intersect")
        if self.wire_radius >= self.coil_radius:
            raise ValueError("wire_radius must be smaller than coil_radius")
        return self


@registry.tool("add_spring",
               "Create a helical coil spring in ONE call (round wire swept along a helix).",
               SpringArgs)
def add_spring(a: SpringArgs) -> dict:
    return _summary(parts.spring(_doc(), a.coil_radius, a.wire_radius, a.pitch, a.turns,
                                 a.name, (a.x, a.y, a.z)))


# --------------------------------------------------------------------------- #
# File ingest (Phase 4)
# --------------------------------------------------------------------------- #
class ImportArgs(ToolArgs):
    filename: str = Field(..., description="Path to the file to import")
    name: str = Field("Imported", description="Name hint for the imported object")


@registry.tool("import_step",
               "Import a STEP / IGES / BREP file as exact geometry into the active document.",
               ImportArgs)
def import_step(a: ImportArgs) -> dict:
    return _summary(ingest.import_step(_doc(), a.filename, a.name))


@registry.tool("import_stl",
               "Import an STL mesh file, sewing it into a solid when watertight.", ImportArgs)
def import_stl(a: ImportArgs) -> dict:
    return _summary(ingest.import_stl(_doc(), a.filename, a.name))


class OpenDocumentArgs(ToolArgs):
    filename: str = Field(..., description="Path to a .FCStd FreeCAD document")


@registry.tool("open_document", "Open an existing .FCStd document and make it active.",
               OpenDocumentArgs)
def open_document(a: OpenDocumentArgs) -> dict:
    doc = document.open_document(a.filename)
    return {"document": doc.Name, "objects": [o.Name for o in doc.Objects]}


# --------------------------------------------------------------------------- #
# Simulation (Phase 5: CalculiX FEA — static structural + steady-state thermal)
# --------------------------------------------------------------------------- #
class ListFacesArgs(ToolArgs):
    name: str = Field("", description="Object to inspect (empty = the final solid)")


@registry.tool("list_faces",
               "List every face of an object with its area, centre and normal — "
               "use this to choose faces for simulation loads and constraints.", ListFacesArgs)
def list_faces(a: ListFacesArgs) -> dict:
    obj = _target(a.name)
    return {"object": obj.Name, "faces": simulation.face_info(obj)}


_DIRECTIONS = {"normal", "x", "-x", "y", "-y", "z", "-z"}


class SimulateStaticArgs(ToolArgs):
    fixed_faces: list[str] = Field(..., min_length=1,
                                   description='Faces held rigid, e.g. ["Face1"] (see list_faces)')
    load_faces: list[str] = Field(..., min_length=1, description="Faces the force acts on")
    force_n: float = Field(..., gt=0, description="Total force in newtons")
    direction: str = Field("normal", description="'normal' presses into the surface, "
                                                 "or a global axis: x, -x, y, -y, z, -z")
    material: str = Field("steel", description="steel | aluminum | titanium | abs")
    mesh_size: float | None = Field(None, gt=0, description="FEM element size mm (default auto)")
    name: str = Field("", description="Object to analyse (empty = the final solid)")

    @model_validator(mode="after")
    def _direction_known(self):
        if self.direction.lower() not in _DIRECTIONS:
            raise ValueError(f"direction must be one of {sorted(_DIRECTIONS)}")
        return self


@registry.tool("simulate_static",
               "Run a static structural FEA (CalculiX): fix faces, apply a force to other "
               "faces, and get max von Mises stress, deflection and safety factor.",
               SimulateStaticArgs)
def simulate_static(a: SimulateStaticArgs) -> dict:
    summary, _ = simulation.run_static(_target(a.name), a.fixed_faces, a.load_faces,
                                       a.force_n, a.direction, a.material, a.mesh_size)
    return summary


class SimulateThermalArgs(ToolArgs):
    hot_faces: list[str] = Field(..., min_length=1, description="Faces held at the hot temperature")
    hot_temp_c: float = Field(..., description="Hot temperature, deg C")
    cold_faces: list[str] = Field(..., min_length=1, description="Faces held at the cold temperature")
    cold_temp_c: float = Field(..., description="Cold temperature, deg C")
    material: str = Field("steel", description="steel | aluminum | titanium | abs")
    mesh_size: float | None = Field(None, gt=0, description="FEM element size mm (default auto)")
    name: str = Field("", description="Object to analyse (empty = the final solid)")

    @model_validator(mode="after")
    def _gradient_exists(self):
        if self.hot_temp_c <= self.cold_temp_c:
            raise ValueError("hot_temp_c must be greater than cold_temp_c")
        return self


@registry.tool("simulate_thermal",
               "Run a steady-state thermal FEA (CalculiX): hold face sets at hot/cold "
               "temperatures and get the temperature field plus thermal expansion.",
               SimulateThermalArgs)
def simulate_thermal(a: SimulateThermalArgs) -> dict:
    summary, _ = simulation.run_thermal(_target(a.name), a.hot_faces, a.hot_temp_c,
                                        a.cold_faces, a.cold_temp_c, a.material, a.mesh_size)
    return summary


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #
class ExportArgs(ToolArgs):
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
class NoArgs(ToolArgs):
    pass


@registry.tool("get_feature_tree",
               "List every object in the document with its parameters "
               "(use this to ground reasoning and get valid object names).", NoArgs)
def get_feature_tree(a: NoArgs) -> dict:
    return {"objects": inspect.feature_tree(_doc())}


class DescribeArgs(ToolArgs):
    name: str = Field(..., description="Object name to describe")


@registry.tool("describe_object",
               "Full report on one object: parameters, bounding box, mass properties.", DescribeArgs)
def describe_object(a: DescribeArgs) -> dict:
    return inspect.describe(_obj(a.name))


@registry.tool("mass_properties",
               "Volume, surface area, centre of mass and solid count of an object.", DescribeArgs)
def mass_properties(a: DescribeArgs) -> dict:
    return inspect.mass_properties(_obj(a.name))


# --------------------------------------------------------------------------- #
# Toolroom (Phase 6: sandboxed self-extension)
# --------------------------------------------------------------------------- #
class ForgeArgs(ToolArgs):
    request: str = Field(..., min_length=10,
                         description="Plain-language spec of the NEW parametric tool to create, "
                                     "with its parameters and units, e.g. 'a hollow tube given "
                                     "outer diameter, wall thickness and length in mm'")


@registry.tool("forge_tool",
               "LAST RESORT — only when NO existing tool or combination of tools can build the "
               "requested shape: the coder model writes a brand-new parametric tool, it is "
               "safety-gated and sandbox-tested, then permanently added to the toolbox.",
               ForgeArgs)
def forge_tool(a: ForgeArgs) -> dict:
    from agentic_cad.toolroom import forge  # lazy: needs the code model only here

    result = forge.forge_tool(a.request, registry)
    out = {"ok": result.ok, "attempts": result.attempts, "seconds": result.seconds}
    if result.ok:
        out.update({"new_tool": result.name, "description": result.description,
                    "self_test_volume_mm3": result.test_volume})
    else:
        out["error"] = result.error
    return out


# Re-register previously forged tools so self-extension persists across
# sessions. Never fatal: a corrupt stored tool is skipped, not raised.
try:
    from agentic_cad.toolroom import store as _toolroom_store

    _toolroom_store.load_all(registry)
except Exception:
    pass
