"""Headless FEM simulation (Phase 5): static structural and steady-state
thermal analyses via FreeCAD's FEM stack — bundled Gmsh mesher + CalculiX ccx.

Design notes
------------
* Every analysis runs in an ISOLATED throwaway document holding a copy of the
  target shape, so the live model never accumulates analysis objects. Face
  names ("Face1", ...) are preserved by ``Shape.copy()``.
* Results come back in two shapes: a compact numeric ``summary`` (for the
  agent / chat log) and a full per-node ``field`` payload with the boundary
  surface of the FEM mesh (for the browser's colour-mapped result viewer).
* Force direction: CalculiX's writer takes a direction from a geometric
  reference, so axis-aligned directions are realised with a hidden Part::Line.
"""
from __future__ import annotations

import time
from typing import Sequence

from agentic_cad import config
from agentic_cad.cad import bootstrap

bootstrap.ensure_freecad_importable()
import FreeCAD as FCad  # noqa: E402

Vec = FCad.Vector

# --------------------------------------------------------------------------- #
# Material cards (mechanical + thermal, mm/N/s unit system: stress in MPa)
# --------------------------------------------------------------------------- #
MATERIALS: dict[str, dict] = {
    "steel": {
        "label": "Steel (S235)",
        "YoungsModulus": "210000 MPa", "PoissonRatio": "0.30",
        "Density": "7900 kg/m^3", "ThermalConductivity": "43 W/m/K",
        "ThermalExpansionCoefficient": "12 um/m/K", "SpecificHeat": "500 J/kg/K",
        "yield_mpa": 235.0,
    },
    "aluminum": {
        "label": "Aluminium (6061)",
        "YoungsModulus": "69000 MPa", "PoissonRatio": "0.33",
        "Density": "2700 kg/m^3", "ThermalConductivity": "167 W/m/K",
        "ThermalExpansionCoefficient": "23 um/m/K", "SpecificHeat": "896 J/kg/K",
        "yield_mpa": 240.0,
    },
    "titanium": {
        "label": "Titanium (Ti-6Al-4V)",
        "YoungsModulus": "114000 MPa", "PoissonRatio": "0.34",
        "Density": "4430 kg/m^3", "ThermalConductivity": "6.7 W/m/K",
        "ThermalExpansionCoefficient": "8.6 um/m/K", "SpecificHeat": "526 J/kg/K",
        "yield_mpa": 880.0,
    },
    "abs": {
        "label": "ABS plastic",
        "YoungsModulus": "2300 MPa", "PoissonRatio": "0.37",
        "Density": "1060 kg/m^3", "ThermalConductivity": "0.17 W/m/K",
        "ThermalExpansionCoefficient": "90 um/m/K", "SpecificHeat": "1400 J/kg/K",
        "yield_mpa": 40.0,
    },
}

_AXES = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1),
         "-x": (-1, 0, 0), "-y": (0, -1, 0), "-z": (0, 0, -1)}


# --------------------------------------------------------------------------- #
# Face metadata + tessellation (agent reasoning and UI picking)
# --------------------------------------------------------------------------- #
def face_info(obj) -> list[dict]:
    """Compact per-face metadata so an agent (or UI) can identify faces:
    name, surface type, area, centre and outward normal (planar faces)."""
    faces = []
    for i, face in enumerate(obj.Shape.Faces, 1):
        entry: dict = {
            "name": f"Face{i}",
            "type": type(face.Surface).__name__,      # Plane, Cylinder, ...
            "area_mm2": round(face.Area, 2),
        }
        c = face.CenterOfMass
        entry["center"] = [round(c.x, 2), round(c.y, 2), round(c.z, 2)]
        try:
            u0, u1, v0, v1 = face.ParameterRange
            n = face.normalAt((u0 + u1) / 2, (v0 + v1) / 2)
            entry["normal"] = [round(n.x, 3), round(n.y, 3), round(n.z, 3)]
        except Exception:
            pass
        faces.append(entry)
    return faces


def face_tessellation(obj, deflection: float = 0.4) -> list[dict]:
    """Per-face triangle meshes (flat arrays) so the browser can raycast-pick
    individual faces. Kept separate per face — that IS the selection unit."""
    out = []
    for i, face in enumerate(obj.Shape.Faces, 1):
        verts, tris = face.tessellate(deflection)
        out.append({
            "name": f"Face{i}",
            "vertices": [round(c, 3) for v in verts for c in (v.x, v.y, v.z)],
            "triangles": [i for t in tris for i in t],
        })
    return out


# --------------------------------------------------------------------------- #
# Analysis assembly
# --------------------------------------------------------------------------- #
def _isolated_copy(obj):
    """Copy the target solid into a fresh document (face order preserved)."""
    tmp = FCad.newDocument("__sim__")
    feat = tmp.addObject("Part::Feature", "SimPart")
    feat.Shape = obj.Shape.copy()
    tmp.recompute()
    return tmp, feat


def _check_faces(feat, names: Sequence[str], what: str) -> list:
    n_faces = len(feat.Shape.Faces)
    refs = []
    for name in names:
        if not name.startswith("Face") or not name[4:].isdigit() or not (1 <= int(name[4:]) <= n_faces):
            raise ValueError(f"{what}: {name!r} is not a valid face (object has Face1..Face{n_faces})")
        refs.append((feat, name))
    if not refs:
        raise ValueError(f"{what}: at least one face is required")
    return refs


def _mesh_size_default(shape) -> float:
    bb = shape.BoundBox
    return min(max(bb.DiagonalLength / 12.0, 0.5), 25.0)


def _add_common(doc, feat, material: str, thermal: bool):
    """Analysis + CalculiX solver + material. Returns (analysis, solver)."""
    import ObjectsFem

    if material not in MATERIALS:
        raise ValueError(f"unknown material {material!r}; options: {sorted(MATERIALS)}")
    card = MATERIALS[material]

    analysis = ObjectsFem.makeAnalysis(doc, "Analysis")

    solver = ObjectsFem.makeSolverCalculiXCcxTools(doc)
    solver.GeometricalNonlinearity = "linear"
    solver.MatrixSolverType = "default"
    solver.IterationsControlParameterTimeUse = False
    if thermal:
        solver.AnalysisType = "thermomech"
        solver.ThermoMechSteadyState = True
    analysis.addObject(solver)

    mat = ObjectsFem.makeMaterialSolid(doc, "Material")
    m = dict(mat.Material)
    m["Name"] = card["label"]
    for key in ("YoungsModulus", "PoissonRatio", "Density", "ThermalConductivity",
                "ThermalExpansionCoefficient", "SpecificHeat"):
        m[key] = card[key]
    mat.Material = m
    analysis.addObject(mat)
    return analysis, solver


def _add_mesh(doc, feat, analysis, mesh_size: float | None):
    """Second-order Gmsh tet mesh (gmsh.exe ships with FreeCAD)."""
    import ObjectsFem
    from femmesh.gmshtools import GmshTools

    mesh = ObjectsFem.makeMeshGmsh(doc, "Mesh")
    mesh.Shape = feat
    mesh.CharacteristicLengthMax = float(mesh_size or _mesh_size_default(feat.Shape))
    mesh.ElementOrder = "2nd"
    doc.recompute()
    err = GmshTools(mesh).create_mesh()
    if err:
        raise RuntimeError(f"meshing failed: {err}")
    if mesh.FemMesh.NodeCount == 0:
        raise RuntimeError("meshing produced an empty mesh")
    analysis.addObject(mesh)
    return mesh


def _solve(analysis, solver):
    """Write the input deck, run ccx, load results. Returns the result object."""
    from femtools import ccxtools

    workdir = config.VAR_DIR / "sim"
    workdir.mkdir(parents=True, exist_ok=True)
    fea = ccxtools.FemToolsCcx(analysis, solver)
    fea.update_objects()
    fea.setup_working_dir(str(workdir))
    fea.setup_ccx()
    msg = fea.check_prerequisites()
    if msg:
        raise RuntimeError(f"analysis prerequisites not met: {msg}")
    fea.purge_results()
    fea.write_inp_file()
    fea.ccx_run()
    fea.load_results()

    results = [o for o in analysis.Group if o.isDerivedFrom("Fem::FemResultObject")]
    if not results:
        raise RuntimeError("solver produced no results (check ccx output in var/sim)")
    return results[-1]


# --------------------------------------------------------------------------- #
# Result-field extraction (boundary surface of the volume mesh + node scalars)
# --------------------------------------------------------------------------- #
def _boundary_surface(femmesh) -> tuple[list, list, dict]:
    """Boundary triangles of a tet mesh: faces referenced by exactly one
    element. Corner nodes only (linear facets are fine for display).

    Returns (node_xyz_flat, tri_indices_flat, node_id -> local index map).
    """
    face_count: dict[tuple, tuple] = {}
    for vid in femmesh.Volumes:
        nodes = femmesh.getElementNodes(vid)
        corners = nodes[:4]                       # tet4 & tet10: corners first
        for tri in ((0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3)):
            face = tuple(corners[i] for i in tri)
            key = tuple(sorted(face))
            if key in face_count:
                face_count[key] = None            # interior (shared) face
            else:
                face_count[key] = face
    boundary = [f for f in face_count.values() if f is not None]

    node_map: dict[int, int] = {}
    coords: list[float] = []
    tris: list[int] = []
    for face in boundary:
        for nid in face:
            if nid not in node_map:
                node_map[nid] = len(node_map)
                p = femmesh.Nodes[nid]
                coords.extend((round(p.x, 4), round(p.y, 4), round(p.z, 4)))
            tris.append(node_map[nid])
    return coords, tris, node_map


def _extract_field(result, femmesh, sim_type: str, material: str) -> tuple[dict, dict]:
    """Build (summary, field_payload) from a FreeCAD FEM result object."""
    coords, tris, node_map = _boundary_surface(femmesh)
    order = {nid: i for i, nid in enumerate(result.NodeNumbers)}

    def per_node(values):
        """Re-index a result array (aligned with NodeNumbers) to surface nodes."""
        out = [0.0] * len(node_map)
        for nid, local in node_map.items():
            out[local] = values[order[nid]]
        return out

    disp = result.DisplacementVectors
    disp_flat: list[float] = []
    for nid in node_map:  # dict preserves insertion order == local index order
        v = disp[order[nid]]
        disp_flat.extend((round(v.x, 5), round(v.y, 5), round(v.z, 5)))
    disp_mag = [d.Length for d in disp]

    fields: dict[str, dict] = {}
    summary: dict = {
        "sim_type": sim_type,
        "material": MATERIALS[material]["label"],
        "nodes": femmesh.NodeCount,
        "elements": femmesh.VolumeCount,
        "max_displacement_mm": round(max(disp_mag), 5),
    }

    if sim_type == "static":
        vm = list(result.vonMises)
        yield_mpa = MATERIALS[material]["yield_mpa"]
        max_vm = max(vm)
        summary.update({
            "max_von_mises_mpa": round(max_vm, 2),
            "yield_strength_mpa": yield_mpa,
            "safety_factor": round(yield_mpa / max_vm, 2) if max_vm > 1e-9 else None,
        })
        fields["von_mises"] = {"label": "von Mises stress", "unit": "MPa",
                               "values": [round(v, 3) for v in per_node(vm)]}
    else:
        temp_c = [t - 273.15 for t in result.Temperature]
        summary.update({
            "max_temp_c": round(max(temp_c), 2),
            "min_temp_c": round(min(temp_c), 2),
        })
        fields["temperature"] = {"label": "Temperature", "unit": "°C",
                                 "values": [round(v, 2) for v in per_node(temp_c)]}

    fields["displacement"] = {"label": "Displacement", "unit": "mm",
                              "values": [round(v, 5) for v in per_node(disp_mag)]}

    field_payload = {
        "sim_type": sim_type,
        "nodes": coords,
        "triangles": tris,
        "displacements": disp_flat,
        "fields": fields,
        "summary": summary,
    }
    return summary, field_payload


# --------------------------------------------------------------------------- #
# Public entry points
# --------------------------------------------------------------------------- #
def run_static(obj, fixed_faces: Sequence[str], load_faces: Sequence[str],
               force_n: float, direction: str = "normal", material: str = "steel",
               mesh_size: float | None = None) -> tuple[dict, dict]:
    """Static structural: fix ``fixed_faces``, apply ``force_n`` newtons over
    ``load_faces`` along ``direction`` ('normal' presses into the surface;
    or 'x','-x','y','-y','z','-z'). Returns (summary, field_payload)."""
    import ObjectsFem

    direction = (direction or "normal").lower()
    if direction != "normal" and direction not in _AXES:
        raise ValueError(f"direction must be 'normal' or one of {sorted(_AXES)}")
    if force_n <= 0:
        raise ValueError("force_n must be positive (use direction to flip sense)")

    t0 = time.time()
    tmp, feat = _isolated_copy(obj)
    try:
        analysis, solver = _add_common(tmp, feat, material, thermal=False)

        fixed = ObjectsFem.makeConstraintFixed(tmp, "Fixed")
        fixed.References = _check_faces(feat, fixed_faces, "fixed_faces")
        analysis.addObject(fixed)

        force = ObjectsFem.makeConstraintForce(tmp, "Load")
        force.References = _check_faces(feat, load_faces, "load_faces")
        force.Force = f"{float(force_n)} N"
        if direction == "normal":
            force.Reversed = True                 # press INTO the surface
        else:
            dx, dy, dz = _AXES[direction]
            line = tmp.addObject("Part::Line", "LoadDir")
            line.X2, line.Y2, line.Z2 = dx * 10.0, dy * 10.0, dz * 10.0
            tmp.recompute()
            force.Direction = (line, ["Edge1"])
        analysis.addObject(force)

        mesh = _add_mesh(tmp, feat, analysis, mesh_size)
        result = _solve(analysis, solver)
        summary, field = _extract_field(result, mesh.FemMesh, "static", material)
        summary["solve_seconds"] = round(time.time() - t0, 1)
        summary.update({"force_n": force_n, "direction": direction,
                        "fixed_faces": list(fixed_faces), "load_faces": list(load_faces)})
        field["summary"] = summary
        return summary, field
    finally:
        FCad.closeDocument(tmp.Name)


def run_thermal(obj, hot_faces: Sequence[str], hot_temp_c: float,
                cold_faces: Sequence[str], cold_temp_c: float,
                material: str = "steel", mesh_size: float | None = None) -> tuple[dict, dict]:
    """Steady-state heat conduction: hold ``hot_faces`` at ``hot_temp_c`` and
    ``cold_faces`` at ``cold_temp_c`` (deg C). The hot faces are also fixed
    mechanically, so the displacement field shows thermal expansion."""
    import ObjectsFem

    if hot_temp_c <= cold_temp_c:
        raise ValueError("hot_temp_c must be greater than cold_temp_c")

    t0 = time.time()
    tmp, feat = _isolated_copy(obj)
    try:
        analysis, solver = _add_common(tmp, feat, material, thermal=True)

        init = ObjectsFem.makeConstraintInitialTemperature(tmp, "InitialTemp")
        init.initialTemperature = cold_temp_c + 273.15
        analysis.addObject(init)

        hot = ObjectsFem.makeConstraintTemperature(tmp, "Hot")
        hot.References = _check_faces(feat, hot_faces, "hot_faces")
        hot.Temperature = f"{hot_temp_c + 273.15} K"
        analysis.addObject(hot)

        cold = ObjectsFem.makeConstraintTemperature(tmp, "Cold")
        cold.References = _check_faces(feat, cold_faces, "cold_faces")
        cold.Temperature = f"{cold_temp_c + 273.15} K"
        analysis.addObject(cold)

        fixed = ObjectsFem.makeConstraintFixed(tmp, "Fixed")   # anchor rigid body
        fixed.References = _check_faces(feat, hot_faces, "hot_faces")
        analysis.addObject(fixed)

        mesh = _add_mesh(tmp, feat, analysis, mesh_size)
        result = _solve(analysis, solver)
        summary, field = _extract_field(result, mesh.FemMesh, "thermal", material)
        summary["solve_seconds"] = round(time.time() - t0, 1)
        summary.update({"hot_faces": list(hot_faces), "hot_temp_c": hot_temp_c,
                        "cold_faces": list(cold_faces), "cold_temp_c": cold_temp_c})
        field["summary"] = summary
        return summary, field
    finally:
        FCad.closeDocument(tmp.Name)
