
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