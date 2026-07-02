"""FastAPI app exposing the agentic CAD pipeline to the browser.

Flow the frontend drives:
    POST /api/clarify   -> geometry-critical questions (or "no clarification needed")
    POST /api/plan      -> resolved-spec plan + a throwaway PREVIEW (live model untouched)
    POST /api/execute   -> commit the approved plan into the live model (single undo)
    POST /api/replan    -> reject-with-feedback, get a fresh plan+preview
    GET  /api/model.stl -> current geometry for the 3-D viewer

All FreeCAD access is serialised through the session lock; the server is intended
to run single-worker for one local user.
"""
from __future__ import annotations

from functools import lru_cache

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agentic_cad import config
from agentic_cad.agent.clarify import answers_to_context, make_clarification, make_clarify_fn
from agentic_cad.agent.execute import execute_plan
from agentic_cad.agent.planner import make_ollama_plan_fn, make_ollama_repair_fn, make_plan
from agentic_cad.server import session as sess
from agentic_cad.tools.cad_tools import registry

FRONTEND_DIR = config.PROJECT_ROOT / "frontend"

app = FastAPI(title="Agentic CAD")
SESSION = sess.Session()


# --------------------------------------------------------------------------- #
# Lazily-built, per-model Ollama functions (creating an ollama client is cheap,
# but we cache so repeated calls reuse the same one).
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=8)
def _plan_fn(model: str):
    return make_ollama_plan_fn(model)


@lru_cache(maxsize=8)
def _repair_fn(model: str):
    return make_ollama_repair_fn(model)


@lru_cache(maxsize=8)
def _clarify_fn(model: str):
    return make_clarify_fn(model)


def _model() -> str:
    return config.MODEL_PLANNER


# --------------------------------------------------------------------------- #
# Request bodies
# --------------------------------------------------------------------------- #
class InstructionBody(BaseModel):
    instruction: str


class PlanBody(BaseModel):
    instruction: str | None = None
    answers: dict[str, str] = {}


class ReplanBody(BaseModel):
    feedback: str = "please revise"


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
def _resolved_context() -> str | None:
    """Build the resolved-spec context from the stored clarification + answers."""
    clar = SESSION.clarification
    if clar is None:
        return None
    return answers_to_context(clar, SESSION.answers, instruction=SESSION.instruction)


def _plan_and_preview(instruction: str, context: str | None) -> dict:
    model = _model()
    plan = make_plan(instruction, registry, chat_fn=_plan_fn(model), model=model, context=context)
    SESSION.plan = plan
    preview = sess.preview_to_summary(plan)
    if preview.get("views", {}).get("stl"):
        SESSION.bump_view()
    return {
        "plan": {"summary": plan.summary, "steps": [s.model_dump() for s in plan.steps]},
        "preview": preview,
        "view_token": SESSION.view_token,
    }


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
@app.get("/api/health")
def health():
    info = {"freecad": None, "ollama": False, "models": [], "planner_model": _model(),
            "planner_available": False}
    try:
        import FreeCAD as App
        info["freecad"] = ".".join(App.Version()[:2])
    except Exception:
        pass
    try:
        import ollama
        models = [m.get("model") or m.get("name") for m in ollama.Client(host=config.OLLAMA_HOST).list()["models"]]
        info["ollama"] = True
        info["models"] = sorted(m for m in models if m)
        info["planner_available"] = any((m or "").startswith(_model().split(":")[0]) for m in models)
    except Exception as exc:
        info["ollama_error"] = str(exc)
    return info


@app.post("/api/clarify")
def clarify(body: InstructionBody):
    instruction = body.instruction.strip()
    if not instruction:
        return JSONResponse({"error": "empty instruction"}, status_code=400)
    with SESSION.lock:
        SESSION.instruction = instruction
        SESSION.answers = {}
        clar = make_clarification(instruction, chat_fn=_clarify_fn(_model()), model=_model())
        SESSION.clarification = clar
        return {
            "needs_clarification": clar.needs_clarification,
            "questions": [q.model_dump() for q in clar.questions],
            "assumptions": clar.assumptions,
        }


@app.post("/api/plan")
def plan(body: PlanBody):
    with SESSION.lock:
        if body.instruction:
            SESSION.instruction = body.instruction.strip()
        SESSION.answers = dict(body.answers or {})
        if not SESSION.instruction:
            return JSONResponse({"error": "no instruction"}, status_code=400)
        return _plan_and_preview(SESSION.instruction, _resolved_context())


@app.post("/api/replan")
def replan(body: ReplanBody):
    with SESSION.lock:
        if not SESSION.instruction:
            return JSONResponse({"error": "no instruction to replan"}, status_code=400)
        instr = f"{SESSION.instruction}\n\nUser rejected the previous plan: {body.feedback}"
        return _plan_and_preview(instr, _resolved_context())


@app.post("/api/execute")
def execute():
    with SESSION.lock:
        if SESSION.plan is None:
            return JSONResponse({"error": "no plan to execute — call /api/plan first"}, status_code=400)
        doc = SESSION.ensure_doc()
        model = _model()
        result = execute_plan(SESSION.plan, registry, doc, repair_fn=_repair_fn(model), max_repair=2,
                              label=SESSION.instruction[:60])
        payload = {
            "success": result.success,
            "aborted": result.aborted,
            "message": result.message,
            "steps": [
                {"step": s.step, "tool": s.tool, "args": s.args, "ok": s.ok,
                 "error": s.error, "repairs": s.repairs}
                for s in result.steps
            ],
        }
        if result.success:
            sess.export_views(doc)
            SESSION.bump_view()
            payload["geometry"] = sess.geometry_summary(doc)
            payload["view_token"] = SESSION.view_token
            SESSION.history.append({"instruction": SESSION.instruction,
                                    "volume": result.final_volume})
        return payload


@app.post("/api/reset")
def reset():
    with SESSION.lock:
        SESSION.reset()
        return {"ok": True}


@app.get("/api/model.stl")
def model_stl():
    path = sess.stl_view_path()
    if not path.exists():
        return JSONResponse({"error": "no model yet"}, status_code=404)
    return FileResponse(path, media_type="model/stl", headers={"Cache-Control": "no-store"})


@app.get("/api/model.step")
def model_step():
    path = sess.step_view_path()
    if not path.exists():
        return JSONResponse({"error": "no model yet"}, status_code=404)
    return FileResponse(path, media_type="application/step", filename="model.step")


# --------------------------------------------------------------------------- #
# Static frontend (mounted last so /api/* wins)
# --------------------------------------------------------------------------- #
@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
