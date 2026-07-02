from __future__ import annotations
from pathlib import Path
from typing import Sequence
from agentic_cad import config
from agentic_cad.cad import bootstrap
import FreeCAD as FCad 
import Part  

bootstrap.ensure_freecad_importable()
Vec = FCad.Vector

# --------------------------------------------------------------------------- #
# Placement helpers
# --------------------------------------------------------------------------- #
def placement(position: Sequence[float] = (0, 0, 0), axis: Sequence[float] = (0, 0, 1),
              angle_deg: float = 0.0) -> "FCad.Placement":
    """Build a Placement from a position and an axis-angle rotation."""
    return FCad.Placement(Vec(*position), FCad.Rotation(Vec(*axis), angle_deg))


# --------------------------------------------------------------------------- #
# Primitives
# --------------------------------------------------------------------------- #
def add_box(doc: "FCad.Document", length: float, width: float, height: float, name: str = "Box", 
            position: Sequence[float] = (0, 0, 0)) -> "FCad.DocumentObject":
    """Function to create and add a cuboidal object."""
    box = doc.addObject("Part::Box", name)
    box.Length, box.Width, box.Height = length, width, height
    box.Placement = placement(position)
    doc.recompute()
    return box


def add_cylinder(doc: "FCad.Document", radius: float, height: float, name: str = "Cylinder",
                 position: Sequence[float] = (0, 0, 0), axis: Sequence[float] = (0, 0, 1)) -> "FCad.DocumentObject":
    """Function to create and add a cylindrical object."""
    cyl = doc.addObject("Part::Cylinder", name)
    cyl.Radius, cyl.Height = radius, height
    cyl.Placement = placement(position, axis, 0.0)
    doc.recompute()
    return cyl


def add_sphere(doc: "FCad.Document", radius: float, name: str = "Sphere", 
               position: Sequence[float] = (0, 0, 0)) -> "FCad.DocumentObject":
    """Function to create and add a spherical object."""
    sph = doc.addObject("Part::Sphere", name)
    sph.Radius = radius
    sph.Placement = placement(position)
    doc.recompute()
    return sph


def add_cone(doc: "FCad.Document", r1: float, r2: float, height: float, name: str = "Cone", 
             position: Sequence[float] = (0, 0, 0)) -> "FCad.DocumentObject":
    """Function to create and add a conical object."""
    cone = doc.addObject("Part::Cone", name)
    cone.Radius1 = r1
    cone.Radius2 = r2
    cone.Height = height
    cone.Placement = placement(position)
    doc.recompute()
    return cone


# --------------------------------------------------------------------------- #
# Boolean operations
# --------------------------------------------------------------------------- #
def _boolean(doc, kind: str, base, tool, name: str):
    obj = doc.addObject(f"Part::{kind}", name)
    obj.Base = base
    obj.Tool = tool
    base.Visibility = False
    tool.Visibility = False
    doc.recompute()
    return obj


def boolean_cut(doc, base, tool, name: str = "Cut"):
    """Subtract ``tool`` from ``base`` (base - tool)."""
    return _boolean(doc, "Cut", base, tool, name)


def boolean_fuse(doc, base, tool, name: str = "Fusion"):
    """Union of ``base`` and ``tool``."""
    return _boolean(doc, "Fuse", base, tool, name)


def boolean_common(doc, base, tool, name: str = "Common"):
    """Intersection of ``base`` and ``tool``."""
    return _boolean(doc, "Common", base, tool, name)


# --------------------------------------------------------------------------- #
# Dress-up features
# --------------------------------------------------------------------------- #
def fillet_edges(doc, base, radius: float, edge_indices: Sequence[int] | None = None, name: str = "Fillet"):
    """Round edges of ``base``. ``edge_indices`` are 1-based; None = all edges."""
    fillet = doc.addObject("Part::Fillet", name)
    fillet.Base = base
    if edge_indices is None:
        edge_indices = range(1, len(base.Shape.Edges) + 1)
    fillet.Edges = [(i, radius, radius) for i in edge_indices]
    base.Visibility = False
    doc.recompute()
    return fillet


def chamfer_edges(doc, base, size: float, edge_indices: Sequence[int] | None = None, name: str = "Chamfer"):
    """Chamfer edges of ``base``. ``edge_indices`` are 1-based; None = all edges."""
    chamfer = doc.addObject("Part::Chamfer", name)
    chamfer.Base = base
    if edge_indices is None:
        edge_indices = range(1, len(base.Shape.Edges) + 1)
    chamfer.Edges = [(i, size, size) for i in edge_indices]
    base.Visibility = False
    doc.recompute()
    return chamfer


# --------------------------------------------------------------------------- #
# Parametric edit + transforms
# --------------------------------------------------------------------------- #
def set_property(obj, name: str, value, doc=None):
    """Set a live property (e.g. Length) and recompute — the edit-by-description primitive. 
       Raise Error if the property does not exist."""
    if not hasattr(obj, name):
        raise AttributeError(f"{obj.Name} has no property {name!r}")
    setattr(obj, name, value)
    (doc or obj.Document).recompute()
    return obj


def translate(obj, vector: Sequence[float], doc=None):
    """Move an object by a vector (relative to its current placement)."""
    obj.Placement = FCad.Placement(Vec(*vector), FCad.Rotation()).multiply(obj.Placement)
    (doc or obj.Document).recompute()
    return obj


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #
def export_step(objs, path: str | Path) -> Path:
    """Export one or more objects to a STEP file (exact B-rep geometry)."""
    objs = objs if isinstance(objs, (list, tuple)) else [objs]
    path = _prep_export_path(path, ".step")
    Part.export(list(objs), str(path))
    return path


def export_iges(objs, path: str | Path) -> Path:
    """Export one or more objects to an IGES file."""
    objs = objs if isinstance(objs, (list, tuple)) else [objs]
    path = _prep_export_path(path, ".iges")
    Part.export(list(objs), str(path))
    return path


def export_stl(obj, path: str | Path, deflection: float = config.STL_LINEAR_DEFLECTION) -> Path:
    """Export a single object's tessellated shape to STL (for meshing/printing)."""
    path = _prep_export_path(path, ".stl")
    obj.Shape.exportStl(str(path), deflection)
    return path


def _prep_export_path(path: str | Path, default_suffix: str) -> Path:
    path = Path(path)
    if path.suffix == "":
        path = path.with_suffix(default_suffix)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
