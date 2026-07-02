"""Server-side session state and the geometry helpers the API needs.

FreeCAD's Python API is single-threaded and stateful, so a single live document
is held here and every mutation is serialised by ``Session.lock``. The server is
meant to run single-worker for one local user, so this is sufficient and keeps
the FreeCAD kernel happy.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

from agentic_cad import config
from agentic_cad.agent.clarify import Clarification
from agentic_cad.agent.execute import run_steps
from agentic_cad.agent.plan import Plan
from agentic_cad.cad import bootstrap, document, inspect
from agentic_cad.tools.cad_tools import registry

bootstrap.ensure_freecad_importable()
import FreeCAD as App  # noqa: E402

_DOC_MGMT_TOOLS = {"new_document", "save_document"}
_VIEW_STL = "_view.stl"          # current model, tessellated for the 3-D viewer
_VIEW_STEP = "_view.step"        # exact geometry, for download


def _final_solid(doc):
    """The last visible object that is a real solid — the 'result' of the build."""
    for obj in reversed(doc.Objects):
        try:
            if obj.Visibility and len(obj.Shape.Solids) >= 1:
                return obj
        except Exception:
            continue
    return None


def geometry_summary(doc) -> dict:
    """Feature tree + mass properties + bounding box of the current final solid."""
    out: dict = {"objects": inspect.feature_tree(doc)}
    final = _final_solid(doc)
    if final is not None:
        out["final_object"] = final.Name
        try:
            out["mass_properties"] = inspect.mass_properties(final)
            out["bbox"] = inspect.bbox(final)
        except Exception as exc:  # never let a report crash the request
            out["geometry_error"] = str(exc)
    return out


def export_views(doc) -> dict:
    """Write the current final solid to the shared STL/STEP view files.

    Returns which formats were produced so the API can advertise them.
    """
    config.ensure_dirs()
    final = _final_solid(doc)
    produced = {"stl": False, "step": False}
    if final is None:
        return produced
    from agentic_cad.cad import geometry as g

    try:
        g.export_stl(final, config.EXPORT_DIR / _VIEW_STL)
        produced["stl"] = True
    except Exception:
        pass
    try:
        g.export_step(final, config.EXPORT_DIR / _VIEW_STEP)
        produced["step"] = True
    except Exception:
        pass
    return produced


def stl_view_path():
    return config.EXPORT_DIR / _VIEW_STL


def step_view_path():
    return config.EXPORT_DIR / _VIEW_STEP


def preview_to_summary(plan: Plan) -> dict:
    """Run a plan in a throwaway document, export its STL for the viewer, and
    return a full preview report — WITHOUT touching the live model."""
    doc = document.new_document("__preview__")
    try:
        result = run_steps(plan, registry, doc, repair_fn=None, max_repair=0, skip=_DOC_MGMT_TOOLS)
        report = {
            "success": result.success,
            "message": result.message,
            "final_volume": result.final_volume,
            "steps": [
                {"step": s.step, "tool": s.tool, "ok": s.ok, "error": s.error}
                for s in result.steps
            ],
        }
        if result.success:
            report["geometry"] = geometry_summary(doc)
            report["views"] = export_views(doc)
        return report
    finally:
        App.closeDocument(doc.Name)


@dataclass
class Session:
    """Everything the browser session needs to remember between HTTP calls."""

    lock: threading.RLock = field(default_factory=threading.RLock)
    doc: object | None = None
    instruction: str = ""
    clarification: Clarification | None = None
    answers: dict = field(default_factory=dict)   # question id -> user's answer
    plan: Plan | None = None
    view_token: int = 0            # bumped whenever the view STL changes (cache-bust)
    history: list[dict] = field(default_factory=list)

    def ensure_doc(self):
        """Return the live document, creating one on first use."""
        if self.doc is None or self.doc.Name not in App.listDocuments():
            self.doc = document.new_document("Session")
        return self.doc

    def reset(self):
        """Drop the live model and all remembered state."""
        for name in list(App.listDocuments().keys()):
            try:
                App.closeDocument(name)
            except Exception:
                pass
        self.doc = None
        self.instruction = ""
        self.clarification = None
        self.answers = {}
        self.plan = None
        self.view_token = 0
        self.history = []

    def bump_view(self):
        self.view_token += 1
        return self.view_token
