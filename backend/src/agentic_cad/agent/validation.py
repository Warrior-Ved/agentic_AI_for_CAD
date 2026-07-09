
from __future__ import annotations
from agentic_cad.cad import bootstrap
bootstrap.ensure_freecad_importable()

_BAD_STATES = {"Invalid", "Error"}


def validate_object(obj) -> tuple[bool, str]:
    """Validate a single object after recompute. Returns (ok, message)."""
    state = list(getattr(obj, "State", []))
    bad = [s for s in state if s in _BAD_STATES]
    if bad:
        return False, f"{obj.Name}: feature state {bad}"
    # Objects that carry geometry must have a usable, valid shape.
    try:
        shape = obj.Shape
    except Exception as exc:
        return False, f"{obj.Name}: shape error — {type(exc).__name__}: {exc}"
    if shape is not None:
        try:
            if shape.isNull():
                # A sketch is legitimately empty (null shape) until geometry is
                # added to it — mid-plan that is normal, not a defect.
                if obj.isDerivedFrom("Sketcher::SketchObject"):
                    return True, "ok (empty sketch)"
                return False, f"{obj.Name}: null shape"
            if not shape.isValid():
                return False, f"{obj.Name}: invalid shape"
        except Exception as exc:
            return False, f"{obj.Name}: shape check failed — {exc}"
    return True, "ok"


def validate_document(doc) -> tuple[bool, list[str]]:
    """Recompute then validate every object. Returns (ok, list_of_problems)."""
    doc.recompute()
    problems: list[str] = []
    for obj in doc.Objects:
        ok, msg = validate_object(obj)
        if not ok:
            problems.append(msg)
    return (not problems), problems


# --------------------------------------------------------------------------- #
# Semantic cross-checks: does the previewed geometry match the WORDS of the
# request? The kernel can't judge intent, but some intent is measurable.
# --------------------------------------------------------------------------- #
_CENTER_WORDS = ("center", "centre", "centred", "centered", "middle")


def semantic_issue(instruction: str, geometry: dict | None) -> str | None:
    """Return a human-readable problem when the previewed part contradicts the
    request, or None. Currently checks: a request for a CENTRED feature must
    yield a symmetric part — an off-centre cut measurably shifts the centre of
    mass away from the bounding-box centre."""
    if not geometry:
        return None
    if not any(w in instruction.lower() for w in _CENTER_WORDS):
        return None
    mp = geometry.get("mass_properties") or {}
    bb = geometry.get("bbox") or {}
    com = mp.get("center_of_mass")
    if not com or "x_min" not in bb:
        return None
    center = [(bb["x_min"] + bb["x_max"]) / 2, (bb["y_min"] + bb["y_max"]) / 2,
              (bb["z_min"] + bb["z_max"]) / 2]
    diag = (bb["x_len"] ** 2 + bb["y_len"] ** 2 + bb["z_len"] ** 2) ** 0.5 or 1.0
    offset = sum((a - b) ** 2 for a, b in zip(com, center)) ** 0.5
    # 0.5% of the diagonal: a truly centred part sits at ~1e-6, while even a
    # small off-centre cavity shifts the COM well past this.
    if offset > 0.005 * diag:
        return (f"the request asks for a CENTRED feature, but the part came out "
                f"asymmetric: its centre of mass {[round(c, 2) for c in com]} is "
                f"{offset:.2f} mm away from its bounding-box centre "
                f"{[round(c, 2) for c in center]} — the cutter is off-centre. Place the "
                f"cutter with centered=true at the SAME centre as the base solid.")
    return None