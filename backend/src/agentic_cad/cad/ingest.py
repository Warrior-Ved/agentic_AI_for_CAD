"""File ingest (Phase 4): bring external geometry into the live document.

STEP/IGES/BREP come in as exact B-rep geometry; STL comes in as a mesh that is
sewn into a shell and — when watertight — converted to a solid, so the usual
inspection / boolean / export tools work on imported parts too.
"""
from __future__ import annotations
from pathlib import Path
from agentic_cad.cad import bootstrap
import Part

bootstrap.ensure_freecad_importable()

_EXACT_SUFFIXES = {".step", ".stp", ".iges", ".igs", ".brep", ".brp"}


def import_step(doc, path: str | Path, name: str = "Imported"):
    """Import a STEP/IGES/BREP file as one exact-geometry object."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix.lower() not in _EXACT_SUFFIXES:
        raise ValueError(f"unsupported exact-geometry format: {path.suffix!r} "
                         f"(expected one of {sorted(_EXACT_SUFFIXES)})")
    shape = Part.Shape()
    shape.read(str(path))
    if shape.isNull():
        raise RuntimeError(f"no geometry found in {path.name}")
    obj = doc.addObject("Part::Feature", name)
    obj.Shape = shape
    doc.recompute()
    return obj


def import_stl(doc, path: str | Path, name: str = "ImportedMesh", tolerance: float = 0.05):
    """Import an STL mesh; sew it and convert to a solid when watertight."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    import Mesh  # FreeCAD's compiled mesh module (on sys.path via bootstrap)
    mesh = Mesh.Mesh(str(path))
    shape = Part.Shape()
    shape.makeShapeFromMesh(mesh.Topology, tolerance)
    try:
        solid = Part.makeSolid(shape)
        if solid.isValid() and solid.Volume > 1e-9:
            shape = solid
    except Exception:
        pass  # not watertight — keep the shell; inspection still works
    obj = doc.addObject("Part::Feature", name)
    obj.Shape = shape
    doc.recompute()
    return obj
