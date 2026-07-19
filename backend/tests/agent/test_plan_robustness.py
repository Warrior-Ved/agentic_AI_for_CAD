"""Regressions from real user sessions: empty-sketch validation, hallucinated
tool arguments, and self-repair of plans that fail their dry-run preview."""
from __future__ import annotations

import json

import pytest

from agentic_cad.agent.execute import approve, plan_confirm_execute, preview_plan
from agentic_cad.agent.plan import Plan, PlanStep
from agentic_cad.agent.validation import validate_document
from agentic_cad.tools.cad_tools import registry

# --------------------------------------------------------------------------- #
# 1. Empty sketches are legal mid-plan (they have a null shape by design)
# --------------------------------------------------------------------------- #


def test_fresh_sketch_passes_document_validation(doc):
    doc.addObject("Sketcher::SketchObject", "Empty")
    doc.recompute()
    ok, problems = validate_document(doc)
    assert ok, problems


def test_sketch_extrude_plan_previews_cleanly():
    plan = Plan(summary="sketch route", steps=[
        PlanStep(step=1, tool="new_sketch", args={"plane": "XY", "name": "Profile"}),
        PlanStep(step=2, tool="sketch_rectangle",
                 args={"sketch": "Profile", "width": 15, "height": 15}),
        PlanStep(step=3, tool="extrude",
                 args={"sketch": "Profile", "length": 22, "name": "Cutter"}),
    ])
    report = preview_plan(plan, registry)
    assert report.success, report.message
    assert report.final_volume == pytest.approx(15 * 15 * 22)


# --------------------------------------------------------------------------- #
# 2. Hallucinated arguments are plan-time errors, not silently ignored
# --------------------------------------------------------------------------- #
def test_extra_args_rejected_at_plan_validation():
    plan = Plan(summary="bad args", steps=[
        PlanStep(step=1, tool="extrude",
                 args={"sketch": "S", "length": 10, "base": "Cube"}),  # no 'base' param
    ])
    problems = plan.validate_against_registry(registry)
    assert problems and "base" in problems[0]


def test_valid_args_still_pass():
    plan = Plan(summary="fine", steps=[
        PlanStep(step=1, tool="add_box", args={"length": 5, "width": 5, "height": 5}),
    ])
    assert plan.validate_against_registry(registry) == []


# --------------------------------------------------------------------------- #
# 3. A failed dry-run preview triggers an automatic revision (kernel feedback)
# --------------------------------------------------------------------------- #
def _plan_json(steps, summary="test part"):
    return json.dumps({"summary": summary, "steps": steps})


BAD_PLAN = _plan_json([  # boolean_cut on names that will not exist -> preview fails
    {"step": 1, "tool": "boolean_cut",
     "args": {"base": "Ghost", "tool": "Phantom"}, "rationale": ""}])
GOOD_PLAN = _plan_json([
    {"step": 1, "tool": "add_box",
     "args": {"length": 20, "width": 20, "height": 20, "name": "Cube"}, "rationale": ""}])


def test_plan_confirm_execute_self_repairs_failed_preview(doc):
    seen: list[str] = []

    def chat_fn(messages, schema):
        seen.append(messages[-1]["content"])
        return BAD_PLAN if len(seen) == 1 else GOOD_PLAN

    result = plan_confirm_execute("a 20 mm cube", registry, doc,
                                  confirm_fn=lambda plan, preview: approve(),
                                  planner_chat_fn=chat_fn, use_memory=False)
    assert result.success
    assert len(seen) == 2                       # one automatic revision
    assert "FAILED its dry-run preview" in seen[1]
    assert "Ghost" in seen[1]                   # the kernel error was fed back


def _geometry(com, mins, maxs):
    return {"mass_properties": {"center_of_mass": com},
            "bbox": {"x_min": mins[0], "y_min": mins[1], "z_min": mins[2],
                     "x_max": maxs[0], "y_max": maxs[1], "z_max": maxs[2],
                     "x_len": maxs[0] - mins[0], "y_len": maxs[1] - mins[1],
                     "z_len": maxs[2] - mins[2]}}


def test_semantic_check_flags_offcentre_hole():
    from agentic_cad.agent.validation import semantic_issue

    # the real corner-hole failure: COM (13.21, 13.21, 10) in a 0..20 cube
    geo = _geometry([13.21, 13.21, 10.0], (0, 0, 0), (20, 20, 20))
    issue = semantic_issue("a cube with a square hole through the center", geo)
    assert issue and "off-centre" in issue

    # a genuinely centred part passes
    geo_ok = _geometry([10.0, 10.0, 10.0], (0, 0, 0), (20, 20, 20))
    assert semantic_issue("a cube with a square hole through the center", geo_ok) is None

    # requests that never mention centring are not judged
    assert semantic_issue("a bracket with a corner notch", geo) is None


def test_self_repair_uses_semantic_feedback(doc):
    offcentre = _plan_json([
        {"step": 1, "tool": "add_box",
         "args": {"length": 20, "width": 20, "height": 20, "name": "Base"}, "rationale": ""},
        {"step": 2, "tool": "add_box",
         "args": {"length": 15, "width": 15, "height": 22, "name": "Cutter", "z": -1},
         "rationale": ""},
        {"step": 3, "tool": "boolean_cut",
         "args": {"base": "Base", "tool": "Cutter", "name": "Part"}, "rationale": ""}])
    centred = _plan_json([
        {"step": 1, "tool": "add_box",
         "args": {"length": 20, "width": 20, "height": 20, "name": "Base", "centered": True},
         "rationale": ""},
        {"step": 2, "tool": "add_box",
         "args": {"length": 15, "width": 15, "height": 22, "name": "Cutter", "centered": True},
         "rationale": ""},
        {"step": 3, "tool": "boolean_cut",
         "args": {"base": "Base", "tool": "Cutter", "name": "Part"}, "rationale": ""}])
    seen: list[str] = []

    def chat_fn(messages, schema):
        seen.append(messages[-1]["content"])
        return offcentre if len(seen) == 1 else centred

    result = plan_confirm_execute(
        "a 20 mm cube with a 15 mm square hole through the center", registry, doc,
        confirm_fn=lambda plan, preview: approve(),
        planner_chat_fn=chat_fn, use_memory=False)
    assert result.success
    assert len(seen) == 2
    assert "off-centre" in seen[1]          # semantic feedback drove the retry
    assert result.final_volume == pytest.approx(20**3 - 15 * 15 * 20)


# --------------------------------------------------------------------------- #
# 4. Revising a plan keeps every earlier edit (does not revert to defaults)
# --------------------------------------------------------------------------- #
def test_make_plan_revision_seeds_prior_plan_and_feedback():
    from agentic_cad.agent.planner import make_plan

    prior = Plan(summary="a 30x40x20 block", steps=[
        PlanStep(step=1, tool="add_box",
                 args={"length": 30, "width": 40, "height": 20, "name": "Base", "centered": True}),
    ])
    revised = _plan_json([
        {"step": 1, "tool": "add_box",
         "args": {"length": 30, "width": 40, "height": 20, "name": "Base", "centered": True},
         "rationale": ""},
        {"step": 2, "tool": "add_cylinder",
         "args": {"radius": 4, "height": 22, "name": "Cutter", "centered": True}, "rationale": ""},
        {"step": 3, "tool": "boolean_cut",
         "args": {"base": "Base", "tool": "Cutter", "name": "Part"}, "rationale": ""}])

    seen: list[list] = []

    def chat_fn(messages, schema):
        seen.append(list(messages))
        return revised

    plan = make_plan("a 30x40x20 block", registry, chat_fn=chat_fn,
                     revise_of=prior, feedback="add an 8 mm through hole")

    # the previous plan was seeded as the model's own answer, and the feedback
    # as the only requested change
    roles = [m["role"] for m in seen[0]]
    assert roles == ["system", "user", "assistant", "user"]
    assert '"length": 30' in seen[0][2]["content"]      # prior dimensions in context
    assert "8 mm through hole" in seen[0][3]["content"]
    # the revised plan keeps the edited block dimensions rather than reverting
    assert plan.steps[0].args["length"] == 30
    assert plan.steps[0].args["width"] == 40
    assert len(plan.steps) == 3


def test_replan_endpoint_retains_prior_edits(monkeypatch):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from agentic_cad.server import app as server_app

    block = _plan_json([
        {"step": 1, "tool": "add_box",
         "args": {"length": 30, "width": 40, "height": 20, "name": "Base", "centered": True},
         "rationale": ""}])
    block_with_hole = _plan_json([
        {"step": 1, "tool": "add_box",
         "args": {"length": 30, "width": 40, "height": 20, "name": "Base", "centered": True},
         "rationale": ""},
        {"step": 2, "tool": "add_cylinder",
         "args": {"radius": 4, "height": 22, "name": "Cutter", "centered": True}, "rationale": ""},
        {"step": 3, "tool": "boolean_cut",
         "args": {"base": "Base", "tool": "Cutter", "name": "Part"}, "rationale": ""}])

    calls = {"n": 0}
    captured: dict = {}

    def fake_plan_fn(model):
        def chat_fn(messages, schema):
            calls["n"] += 1
            if calls["n"] == 1:
                return block
            captured["messages"] = list(messages)
            return block_with_hole
        return chat_fn

    monkeypatch.setattr(server_app, "_plan_fn", fake_plan_fn)
    client = TestClient(server_app.app)
    try:
        r1 = client.post("/api/plan", json={"instruction": "a 30x40x20 block"})
        assert r1.status_code == 200, r1.json()
        r2 = client.post("/api/replan", json={"feedback": "add an 8 mm through hole in the centre"})
        data = r2.json()
        assert r2.status_code == 200, data
        # the replan conversation was SEEDED with the previously-shown plan and
        # only the new feedback — not a rebuild from the original request
        msgs = captured["messages"]
        assert any(m["role"] == "assistant" and "add_box" in m["content"] for m in msgs)
        assert any(m["role"] == "user" and "8 mm through hole" in m["content"] for m in msgs)
        # the earlier dimension edit survives the second edit
        assert data["preview"]["success"], data["preview"]
        assert data["plan"]["steps"][0]["args"]["length"] == 30
        assert data["plan"]["steps"][0]["args"]["width"] == 40
    finally:
        with server_app.SESSION.lock:
            server_app.SESSION.reset()


def test_server_plan_endpoint_self_repairs(monkeypatch):
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from agentic_cad.server import app as server_app

    replies = iter([BAD_PLAN, GOOD_PLAN])
    monkeypatch.setattr(server_app, "_plan_fn",
                        lambda model: (lambda messages, schema: next(replies)))
    client = TestClient(server_app.app)
    try:
        r = client.post("/api/plan", json={"instruction": "a 20 mm cube"})
        data = r.json()
        assert r.status_code == 200, data
        assert data["preview"]["success"]
        assert data["plan_attempts"] == 2
        assert data["plan"]["steps"][0]["tool"] == "add_box"
    finally:
        with server_app.SESSION.lock:
            server_app.SESSION.reset()
