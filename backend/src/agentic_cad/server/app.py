"""FastAPI app exposing the agentic CAD pipeline to the browser.

Flow the frontend drives:
    POST /api/clarify    -> geometry-critical questions (or "no clarification needed")
    POST /api/plan       -> resolved-spec plan + a throwaway PREVIEW (live model untouched)
    POST /api/execute    -> commit the approved plan into the live model (single undo)
    POST /api/replan     -> reject-with-feedback, get a fresh plan+preview
    GET  /api/model.stl  -> current geometry for the 3-D viewer
    GET  /api/sim/faces  -> per-face tessellation + metadata (viewer face picking)
    POST /api/sim/run    -> start a CalculiX FEA in the background
    GET  /api/sim/status -> idle | running | done | error (+ summary when done)
    GET  /api/sim/result -> full per-node field payload for the result viewer

All FreeCAD access is serialised through the session lock; the server is intended
to run single-worker for one local user.
"""
from __future__ import annotations

import threading
from functools import lru_cache

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agentic_cad import config, privacy
from agentic_cad.agent import validation
from agentic_cad.agent.clarify import answers_to_context, make_clarification, make_clarify_fn
from agentic_cad.agent.execute import execute_plan
from agentic_cad.agent.planner import make_ollama_plan_fn, make_ollama_repair_fn, make_plan
from agentic_cad.agent.router import answer_question, route_intent
from agentic_cad.cad import simulation
from agentic_cad.server import session as sess
from agentic_cad.tools.cad_tools import registry

FRONTEND_DIR = config.PROJECT_ROOT / "frontend"

app = FastAPI(title="Agentic CAD")
SESSION = sess.Session()

# Privacy: local-only by default — any non-local egress from this process
# raises, so the offline claim is enforced, not assumed.
if not config.ALLOW_CLOUD:
    privacy.install_guard()

# Simulation job state. Single job at a time; written by the worker thread,
# read lock-free by the status/result endpoints (GIL-atomic dict swaps).
SIM: dict = {"state": "idle", "error": None, "summary": None, "result": None, "token": 0}


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
    intent: str = "build"          # "edit" plans against the current model


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


def _edit_context() -> str | None:
    """Current-model grounding for edit-by-description plans."""
    doc = SESSION.ensure_doc()
    if not doc.Objects:
        return None
    tree = sess.geometry_summary(doc).get("objects", [])
    lines = [f"- {o['name']} ({o['type']}): {o.get('parameters', {})}" for o in tree]
    return ("You are EDITING the existing model — do NOT recreate it. Change it with "
            "set_property / set_datum / translate / rotate / boolean ops on these "
            "existing objects (use their exact names):\n" + "\n".join(lines))


def _plan_and_preview(instruction: str, context: str | None, intent: str = "build",
                      *, revise_of=None, feedback: str | None = None) -> dict:
    model = _model()
    trace: list = []                 # the agent's visible thought process
    lessons = None
    try:  # retrieval-augmented planning — strictly best-effort
        from agentic_cad.memory import reflect
        lessons = reflect.lessons_context(instruction, k=3,
                                          embed_fn=reflect.make_ollama_embed_fn())
    except Exception:
        pass
    if lessons:
        trace.append({"stage": "memory", "text": "recalled hints from past episodes:\n"
                      + "\n".join(lessons.splitlines()[1:])})
    else:
        trace.append({"stage": "memory", "text": "no relevant lessons in memory"})
    edit_ctx = _edit_context() if intent == "edit" else None
    if edit_ctx:
        trace.append({"stage": "context", "text":
                      "editing the existing model — feature tree injected as context"})
    context = "\n\n".join(c for c in (edit_ctx, lessons, context) if c) or None
    base = SESSION.ensure_doc() if intent == "edit" else None

    # Adaptive planning: every candidate plan is dry-run previewed and
    # semantically cross-checked INSIDE the planner conversation, so the model
    # revises its own plan against kernel + semantic feedback before the user
    # ever sees a broken one.
    cache: dict = {"attempts": 0}

    def verify(candidate) -> list[str]:
        cache["attempts"] += 1
        cache["plan"] = candidate
        report = sess.preview_to_summary(candidate, base_doc=base)
        cache["preview"] = report
        if not report["success"]:
            bad = next((s for s in report["steps"] if not s["ok"]), None)
            detail = (f"step {bad['step']} ({bad['tool']}) FAILED its dry-run preview: {bad['error']}"
                      if bad else report.get("message") or "the preview produced no valid geometry")
            return [detail]
        vol = report.get("final_volume")
        trace.append({"stage": "preview", "text":
                      f"dry-run preview built cleanly — volume {vol:,.1f} mm³" if vol
                      else "dry-run preview built cleanly"})
        issue = validation.semantic_issue(instruction, report.get("geometry"))
        if issue:
            return [issue]
        if any(w in instruction.lower() for w in ("center", "centre", "centred",
                                                  "centered", "middle")):
            trace.append({"stage": "check", "text":
                          "semantic check passed: centre of mass coincides with the "
                          "bounding-box centre (feature is truly centred)"})
        return []

    try:
        plan = make_plan(instruction, registry, chat_fn=_plan_fn(model), model=model,
                         context=context, max_retries=3, verify_fn=verify, trace=trace,
                         revise_of=revise_of, feedback=feedback)
    except ValueError:
        if "plan" not in cache:      # never even produced a schema-valid plan
            raise
        plan = cache["plan"]         # best effort: show the user what failed and why

    preview = cache["preview"]
    attempts = cache["attempts"]
    SESSION.plan = plan
    if preview.get("views", {}).get("stl"):
        SESSION.bump_view()
    return {
        "plan": {"summary": plan.summary, "steps": [s.model_dump() for s in plan.steps]},
        "preview": preview,
        "view_token": SESSION.view_token,
        "lessons_used": lessons,
        "intent": intent,
        "plan_attempts": attempts,
        "thinking": trace,
    }


def _remember_execution(instruction: str, plan, result) -> None:
    """Log the episode, then reflect + consolidate in the background so the
    HTTP response is never delayed by the reflection model call."""
    try:
        from agentic_cad.memory import episodic
        failed = next((s for s in result.steps if not s.ok), None)
        episodic.log_episode(instruction,
                             plan=plan.model_dump() if plan is not None else None,
                             success=result.success,
                             error=failed.error if failed else None,
                             repairs=sum(s.repairs for s in result.steps),
                             volume_mm3=result.final_volume)
    except Exception:
        return

    def reflect_worker():
        try:
            from agentic_cad.memory import episodic, reflect

            def chat_fn(messages):  # plain (schema-free) chat on the planner model
                import ollama
                return ollama.Client(host=config.OLLAMA_HOST).chat(
                    model=_model(), messages=messages,
                    options={"temperature": 0})["message"]["content"]

            embed = reflect.make_ollama_embed_fn()
            # failures only: kernel errors are objective lessons; a "successful"
            # episode may still be semantically wrong and would poison planning
            for ep in episodic.unreflected(limit=2, failures_only=True):
                reflect.reflect_on_episode(ep, chat_fn=chat_fn, embed_fn=embed)
            reflect.consolidate()
        except Exception:
            pass

    threading.Thread(target=reflect_worker, daemon=True).start()


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
@app.get("/api/health")
def health():
    info = {"freecad": None, "ollama": False, "models": [], "planner_model": _model(),
            "planner_available": False, "privacy": privacy.status()}
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
        SESSION.intent = body.intent if body.intent in ("build", "edit") else "build"
        if not SESSION.instruction:
            return JSONResponse({"error": "no instruction"}, status_code=400)
        return _plan_and_preview(SESSION.instruction, _resolved_context(), SESSION.intent)


@app.post("/api/replan")
def replan(body: ReplanBody):
    with SESSION.lock:
        if not SESSION.instruction:
            return JSONResponse({"error": "no instruction to replan"}, status_code=400)
        # Revise the plan currently on screen (which already carries every earlier
        # edit) — do NOT re-derive from the original request, or accumulated edits
        # like a changed dimension get reverted when the next edit is applied.
        if SESSION.plan is not None:
            return _plan_and_preview(SESSION.instruction, _resolved_context(), SESSION.intent,
                                     revise_of=SESSION.plan, feedback=body.feedback)
        # No prior plan to revise (shouldn't normally happen): keep the feedback
        # in the request so it is not silently dropped.
        instr = f"{SESSION.instruction}\n\nUser rejected the previous plan: {body.feedback}"
        return _plan_and_preview(instr, _resolved_context(), SESSION.intent)


# --------------------------------------------------------------------------- #
# Intent routing + read-only answers (Phase 7)
# --------------------------------------------------------------------------- #
class RouteBody(BaseModel):
    instruction: str


@app.post("/api/route")
def route(body: RouteBody):
    instruction = body.instruction.strip()
    if not instruction:
        return JSONResponse({"error": "empty instruction"}, status_code=400)
    with SESSION.lock:
        doc = SESSION.ensure_doc()
        has_model = sess._final_solid(doc) is not None
    r = route_intent(instruction, has_model=has_model)
    return {"intent": r.intent, "reason": r.reason, "has_model": has_model}


@app.post("/api/answer")
def answer(body: RouteBody):
    instruction = body.instruction.strip()
    if not instruction:
        return JSONResponse({"error": "empty instruction"}, status_code=400)
    with SESSION.lock:
        result = answer_question(instruction, registry, model=_model())
        return {"answer": result.final_text,
                "success": result.success,
                "tools_used": [{"tool": s["tool"], "args": s["args"]} for s in result.steps]}


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
        _remember_execution(SESSION.instruction, SESSION.plan, result)
        return payload


@app.post("/api/reset")
def reset():
    with SESSION.lock:
        SESSION.reset()
        return {"ok": True}


# --------------------------------------------------------------------------- #
# Simulation (Phase 5)
# --------------------------------------------------------------------------- #
class SimRunBody(BaseModel):
    object: str | None = None                # None = the final solid
    sim_type: str = "static"                 # static | thermal
    material: str = "steel"
    mesh_size: float | None = None
    # static
    fixed_faces: list[str] = []
    load_faces: list[str] = []
    force_n: float = 100.0
    direction: str = "normal"
    # thermal
    hot_faces: list[str] = []
    hot_temp_c: float = 100.0
    cold_faces: list[str] = []
    cold_temp_c: float = 20.0


def _sim_target(name: str | None):
    doc = SESSION.ensure_doc()
    if name:
        obj = doc.getObject(name)
        if obj is None:
            raise ValueError(f"no object named {name!r}")
        return obj
    obj = sess._final_solid(doc)
    if obj is None:
        raise ValueError("no solid in the model yet — build a part first")
    return obj


@app.get("/api/sim/faces")
def sim_faces(object: str | None = None):
    with SESSION.lock:
        try:
            target = _sim_target(object)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        info = simulation.face_info(target)
        tess = simulation.face_tessellation(target)
        faces = [{**i, **t} for i, t in zip(info, tess)]
        return {"object": target.Name, "faces": faces,
                "materials": {k: v["label"] for k, v in simulation.MATERIALS.items()}}


@app.post("/api/sim/run")
def sim_run(body: SimRunBody):
    if SIM["state"] == "running":
        return JSONResponse({"error": "a simulation is already running"}, status_code=409)
    if body.sim_type not in ("static", "thermal"):
        return JSONResponse({"error": "sim_type must be 'static' or 'thermal'"}, status_code=400)
    with SESSION.lock:  # validate the target up front for a fast, clear error
        try:
            target_name = _sim_target(body.object).Name
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    SIM.update(state="running", error=None, summary=None)

    def work():
        try:
            with SESSION.lock:
                target = _sim_target(target_name)
                if body.sim_type == "static":
                    summary, field = simulation.run_static(
                        target, body.fixed_faces, body.load_faces, body.force_n,
                        body.direction, body.material, body.mesh_size)
                else:
                    summary, field = simulation.run_thermal(
                        target, body.hot_faces, body.hot_temp_c,
                        body.cold_faces, body.cold_temp_c, body.material, body.mesh_size)
            SIM.update(state="done", summary=summary, result=field, token=SIM["token"] + 1)
        except Exception as exc:
            SIM.update(state="error", error=f"{type(exc).__name__}: {exc}")

    threading.Thread(target=work, daemon=True).start()
    return {"started": True}


@app.get("/api/sim/status")
def sim_status():
    return {"state": SIM["state"], "error": SIM["error"], "summary": SIM["summary"],
            "token": SIM["token"]}


@app.get("/api/sim/result")
def sim_result():
    if SIM["result"] is None:
        return JSONResponse({"error": "no simulation result yet"}, status_code=404)
    return SIM["result"]


# --------------------------------------------------------------------------- #
# Toolroom + reflective memory (Phase 6) — inspection endpoints
# --------------------------------------------------------------------------- #
@app.get("/api/toolroom")
def toolroom_list():
    from agentic_cad.toolroom import store
    return {"forged_tools": store.list_tools(), "registry_size": len(registry.names())}


@app.get("/api/memory")
def memory_state():
    try:
        from agentic_cad.memory import episodic, reflect
        return {"stats": episodic.stats(),
                "recent_episodes": [
                    {k: e[k] for k in ("id", "ts", "instruction", "success", "error")}
                    for e in episodic.recent(8)],
                "lessons": [{k: le[k] for k in ("id", "ts", "text")}
                            for le in reflect.all_lessons()[:12]]}
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


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
class _FreshStaticFiles(StaticFiles):
    """Serve the frontend with ``Cache-Control: no-cache`` so the browser always
    revalidates. Without it, a heuristically-cached ``app.js``/``styles.css`` can
    mask a code fix until a manual hard refresh — the browser keeps running the
    stale script. This is a local, single-user tool, so always-fresh beats caching.
    """

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers.setdefault("Cache-Control", "no-cache")
        return response


def _asset_version() -> int:
    """Newest mtime across the frontend files — changes whenever any asset does."""
    latest = 0
    try:
        for f in FRONTEND_DIR.rglob("*"):
            if f.is_file():
                latest = max(latest, int(f.stat().st_mtime))
    except OSError:
        pass
    return latest


@app.get("/")
def index():
    # Stamp every local asset URL with a version derived from the file mtimes.
    # A changed app.js/styles.css therefore gets a NEW url the browser has never
    # cached — so a fix can never be masked by a stale cached copy, even without
    # a hard refresh. index.html itself is served no-cache, so it always revalidates.
    import re

    html = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    version = _asset_version()
    html = re.sub(r'(src|href)="(/[^":?]+)"', rf'\1="\2?v={version}"', html)
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


if FRONTEND_DIR.exists():
    app.mount("/", _FreshStaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
