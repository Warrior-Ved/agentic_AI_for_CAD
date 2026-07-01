"""Phase 3 — Plan -> Confirm -> Execute, tested without a live LLM via a fake
planner chat function and injected confirm/repair callbacks."""
from __future__ import annotations

import math

import pytest

from agentic_cad.agent.execute import (
    approve,
    edit,
    execute_plan,
    plan_confirm_execute,
    preview_plan,
    reject,
)
from agentic_cad.agent.plan import Plan, PlanStep
from agentic_cad.agent.validation import validate_document
from agentic_cad.cad import document
from agentic_cad.tools.cad_tools import registry

HOLED_BLOCK_VOL = 40 * 20 * 10 - math.pi * 5 ** 2 * 10  # 7214.6


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def make_plan_obj(summary, steps):
    return Plan(summary=summary, steps=[
        PlanStep(step=i + 1, tool=t, args=a, rationale=r)
        for i, (t, a, r) in enumerate(steps)
    ])


def holed_block_plan():
    return make_plan_obj("40x20x10 block with a centred 10mm hole", [
        ("add_box", {"length": 40, "width": 20, "height": 10, "name": "Block"}, "base"),
        ("add_cylinder", {"radius": 5, "height": 10, "x": 20, "y": 10, "name": "Hole"}, "hole"),
        ("boolean_cut", {"base": "Block", "tool": "Hole", "name": "HoledBlock"}, "cut"),
    ])


def fake_plan_fn(*json_strings):
    it = iter(json_strings)

    def chat_fn(messages, schema):
        return next(it)

    return chat_fn


# --------------------------------------------------------------------------- #
# Plan schema validation
# --------------------------------------------------------------------------- #
def test_valid_plan_passes_registry_validation():
    assert holed_block_plan().validate_against_registry(registry) == []


def test_plan_detects_unknown_tool_and_bad_args():
    plan = make_plan_obj("broken", [
        ("teleport", {}, "nope"),
        ("add_box", {"length": 10}, "missing width/height"),
    ])
    problems = plan.validate_against_registry(registry)
    assert any("unknown tool" in p for p in problems)
    assert any("step 2" in p for p in problems)


# --------------------------------------------------------------------------- #
# Kernel validation
# --------------------------------------------------------------------------- #
def test_kernel_validation_passes_on_good_document():
    registry.run("new_document", {"name": "Good"})
    doc = document.active_document()
    registry.run("add_box", {"length": 10, "width": 10, "height": 10})
    ok, problems = validate_document(doc)
    assert ok and problems == []


def test_kernel_validation_detects_broken_feature():
    registry.run("new_document", {"name": "Broken"})
    doc = document.active_document()
    box = registry.run("add_box", {"length": 10, "width": 10, "height": 10})
    # Fillet radius far larger than the part -> invalid feature.
    registry.run("fillet_all_edges", {"base": box["name"], "radius": 50})
    ok, problems = validate_document(doc)
    assert not ok and problems


# --------------------------------------------------------------------------- #
# Preview isolation
# --------------------------------------------------------------------------- #
def test_preview_runs_in_throwaway_and_leaves_live_untouched():
    import FreeCAD as App  # noqa: PLC0415
    live = document.new_document("Live")
    preview = preview_plan(holed_block_plan(), registry)

    assert preview.success
    assert preview.final_volume == pytest.approx(HOLED_BLOCK_VOL, rel=1e-4)
    assert len(live.Objects) == 0                       # live model untouched
    assert "__preview__" not in App.listDocuments()     # throwaway cleaned up


# --------------------------------------------------------------------------- #
# Execute (transactional, single undo)
# --------------------------------------------------------------------------- #
def test_execute_builds_part_and_is_a_single_undo():
    live = document.new_document("Exec")
    res = execute_plan(holed_block_plan(), registry, live, label="holed block")

    assert res.success and not res.aborted
    assert res.final_volume == pytest.approx(HOLED_BLOCK_VOL, rel=1e-4)
    assert len(live.Objects) >= 3

    live.undo()                       # the whole action collapses to one undo
    assert len(live.Objects) == 0


def test_execute_aborts_cleanly_on_unrepairable_failure():
    live = document.new_document("Abort")
    plan = make_plan_obj("box then impossible fillet", [
        ("add_box", {"length": 10, "width": 10, "height": 10, "name": "Block"}, "base"),
        ("fillet_all_edges", {"base": "Block", "radius": 50}, "too big -> fails"),
    ])
    res = execute_plan(plan, registry, live, repair_fn=None)

    assert not res.success and res.aborted
    assert len(live.Objects) == 0     # transaction rolled back; nothing half-built


# --------------------------------------------------------------------------- #
# Bounded repair
# --------------------------------------------------------------------------- #
def test_execute_repairs_a_failing_step():
    live = document.new_document("Repair")
    plan = make_plan_obj("hole, but boolean names a wrong base first", [
        ("add_box", {"length": 10, "width": 10, "height": 10, "name": "Block"}, "base"),
        ("add_cylinder", {"radius": 3, "height": 10, "x": 5, "y": 5, "name": "Hole"}, "hole"),
        ("boolean_cut", {"base": "WRONG", "tool": "Hole", "name": "Cut"}, "cut"),
    ])

    def repair_fn(step, error, args, reg):
        if step.tool == "boolean_cut" and args.get("base") == "WRONG":
            return {**args, "base": "Block"}
        return None

    res = execute_plan(plan, registry, live, repair_fn=repair_fn, max_repair=2)
    assert res.success
    assert res.steps[-1].repairs == 1
    assert res.final_volume == pytest.approx(10 * 10 * 10 - math.pi * 9 * 10, rel=1e-4)


# --------------------------------------------------------------------------- #
# Full orchestration: plan -> confirm -> execute
# --------------------------------------------------------------------------- #
def test_pce_approve_happy_path():
    live = document.new_document("PCEok")
    chat = fake_plan_fn(holed_block_plan().model_dump_json())
    res = plan_confirm_execute("make a holed block", registry, live,
                               confirm_fn=lambda p, pv: approve(), planner_chat_fn=chat)
    assert res.success
    assert res.final_volume == pytest.approx(HOLED_BLOCK_VOL, rel=1e-4)


def test_pce_reject_then_replan_then_approve():
    live = document.new_document("PCEreplan")
    bad = make_plan_obj("just a block", [
        ("add_box", {"length": 40, "width": 20, "height": 10, "name": "Block"}, "base"),
    ]).model_dump_json()
    good = holed_block_plan().model_dump_json()
    chat = fake_plan_fn(bad, good)

    decisions = iter([reject("you forgot the hole"), approve()])
    res = plan_confirm_execute("make a holed block", registry, live,
                               confirm_fn=lambda p, pv: next(decisions),
                               planner_chat_fn=chat)
    assert res.success
    assert res.final_volume == pytest.approx(HOLED_BLOCK_VOL, rel=1e-4)


def test_pce_edit_executes_the_modified_plan():
    live = document.new_document("PCEedit")
    base = make_plan_obj("small block", [
        ("add_box", {"length": 10, "width": 10, "height": 10, "name": "Block"}, "base"),
    ]).model_dump_json()
    chat = fake_plan_fn(base)

    bigger = make_plan_obj("user enlarged it", [
        ("add_box", {"length": 20, "width": 10, "height": 10, "name": "Block"}, "edited"),
    ])
    res = plan_confirm_execute("make a block", registry, live,
                               confirm_fn=lambda p, pv: edit(bigger), planner_chat_fn=chat)
    assert res.success
    assert res.final_volume == pytest.approx(2000)
