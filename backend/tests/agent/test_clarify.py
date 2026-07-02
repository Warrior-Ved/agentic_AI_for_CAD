"""Clarification stage tests. These use a fake chat function (no Ollama), so they
exercise the schema parsing, capping and context-folding deterministically."""
from __future__ import annotations

import json

from agentic_cad.agent.clarify import (
    Clarification, answers_to_context, make_clarification)
from agentic_cad.agent.execute import _resolve_spec


def _fake_chat(payload: dict):
    """A clarify chat_fn that always returns the given payload as JSON."""
    def chat_fn(messages, schema):
        return json.dumps(payload)
    return chat_fn


def test_ambiguous_request_yields_questions():
    payload = {
        "needs_clarification": True,
        "questions": [
            {"id": "hole_axis", "question": "Which axis?", "why": "changes orientation",
             "options": ["Z", "X"], "suggested": "Z"},
            {"id": "through", "question": "Through or blind?", "why": "changes depth",
             "options": ["through", "blind"], "suggested": "through"},
        ],
        "assumptions": ["cube centred at origin"],
    }
    clar = make_clarification("a cube with a hole", chat_fn=_fake_chat(payload))
    assert clar.needs_clarification is True
    assert [q.id for q in clar.questions] == ["hole_axis", "through"]


def test_questions_are_capped_and_flag_follows_list():
    payload = {
        "needs_clarification": True,
        "questions": [
            {"id": f"q{i}", "question": f"Q{i}?", "suggested": "x"} for i in range(6)
        ],
        "assumptions": [],
    }
    clar = make_clarification("x", chat_fn=_fake_chat(payload), max_questions=4)
    assert len(clar.questions) == 4
    assert clar.needs_clarification is True


def test_fully_specified_request_needs_no_clarification():
    payload = {"needs_clarification": False, "questions": [], "assumptions": ["mm"]}
    clar = make_clarification("a 40x20x10 block", chat_fn=_fake_chat(payload))
    assert clar.needs_clarification is False
    assert clar.questions == []


def test_unparseable_model_output_degrades_gracefully():
    def bad_chat(messages, schema):
        return "not json at all"

    clar = make_clarification("anything", chat_fn=bad_chat)
    assert clar.needs_clarification is False


def test_answers_fold_into_context_with_defaults():
    clar = Clarification(
        needs_clarification=True,
        questions=[
            {"id": "hole_axis", "question": "Which axis?", "suggested": "Z"},
            {"id": "through", "question": "Through or blind?", "suggested": "through"},
        ],
        assumptions=["centred at origin"],
    )
    # answer one, leave the other blank -> the blank falls back to the suggestion
    ctx = answers_to_context(clar, {"hole_axis": "X"}, instruction="a cube with a hole")
    assert "Original request: a cube with a hole" in ctx
    assert "Which axis? -> X" in ctx
    assert "Through or blind? -> through" in ctx
    assert "Assumption: centred at origin" in ctx


def test_resolve_spec_disabled_when_no_callbacks():
    assert _resolve_spec("x", None, None, None) is None


def test_resolve_spec_runs_callback_and_builds_context():
    payload = {
        "needs_clarification": True,
        "questions": [{"id": "through", "question": "Through or blind?", "suggested": "through"}],
        "assumptions": [],
    }
    captured = {}

    def clarify_fn(clar):
        captured["asked"] = [q.id for q in clar.questions]
        return {"through": "blind, 5mm deep"}

    ctx = _resolve_spec("a cube with a hole", clarify_fn, _fake_chat(payload), None)
    assert captured["asked"] == ["through"]
    assert "Through or blind? -> blind, 5mm deep" in ctx
