"""Edit-by-description previews run against a COPY of the live model."""
from __future__ import annotations

import pytest

from agentic_cad.agent.plan import Plan, PlanStep
from agentic_cad.cad import document
from agentic_cad.server import session as sess


def test_edit_preview_uses_live_copy_and_leaves_live_untouched():
    live = document.new_document("Live")
    box = live.addObject("Part::Box", "Box")
    box.Length = box.Width = box.Height = 10
    live.recompute()

    plan = Plan(summary="grow the box", steps=[
        PlanStep(step=1, tool="set_property",
                 args={"name": "Box", "property": "Length", "value": 20}),
    ])
    report = sess.preview_to_summary(plan, base_doc=live)

    assert report["success"], report
    # the previewed COPY reflects the edit...
    assert report["final_volume"] == pytest.approx(20 * 10 * 10)
    # ...while the live model is untouched
    assert live.getObject("Box").Length.Value == pytest.approx(10)


def test_build_preview_without_base_doc_still_works():
    plan = Plan(summary="a box", steps=[
        PlanStep(step=1, tool="add_box", args={"length": 5, "width": 5, "height": 5}),
    ])
    report = sess.preview_to_summary(plan)
    assert report["success"]
    assert report["final_volume"] == pytest.approx(125)
