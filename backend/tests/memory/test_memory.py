"""Reflective memory: episodic log, lessons, retrieval, consolidation, and the
integration into the plan->confirm->execute loop. All offline (fake models)."""
from __future__ import annotations

import json

from agentic_cad.memory import episodic, reflect

# --------------------------------------------------------------------------- #
# Episodic log
# --------------------------------------------------------------------------- #


def test_log_and_recall_episode():
    eid = episodic.log_episode("make a cube", plan={"steps": []}, success=True,
                               volume_mm3=27000.0)
    ep = episodic.get_episode(eid)
    assert ep["instruction"] == "make a cube"
    assert ep["success"] == 1
    assert episodic.stats() == {"episodes": 1, "successes": 1, "failures": 0}


def test_unreflected_prioritises_failures():
    episodic.log_episode("ok part", success=True)
    fid = episodic.log_episode("bad part", success=False, error="boom")
    queue = episodic.unreflected(limit=5)
    assert queue[0]["id"] == fid                  # failure first
    episodic.mark_reflected(fid)
    assert all(e["id"] != fid for e in episodic.unreflected())


# --------------------------------------------------------------------------- #
# Lessons: retrieval (keyword fallback + embeddings), reflection, pruning
# --------------------------------------------------------------------------- #
def _fake_embed(mapping):
    """Embed fn from a keyword->vector table (first matching keyword wins)."""
    def embed(text):
        for key, vec in mapping.items():
            if key in text.lower():
                return vec
        return [0.0, 0.0, 1.0]
    return embed


def test_keyword_fallback_retrieval():
    reflect.add_lesson("For gears always use add_involute_gear with module and teeth.")
    reflect.add_lesson("Center through-holes need the cylinder longer than the block.")
    got = reflect.relevant_lessons("make a gear with 20 teeth", k=2)
    assert len(got) == 1
    assert "involute" in got[0]


def test_embedding_retrieval_ranks_by_cosine():
    emb = _fake_embed({"gear": [1.0, 0.0, 0.0], "hole": [0.0, 1.0, 0.0]})
    reflect.add_lesson("gear lesson text", embed_fn=emb)
    reflect.add_lesson("hole lesson text", embed_fn=emb)
    got = reflect.relevant_lessons("please build a gear", k=1, embed_fn=emb)
    assert got == ["gear lesson text"]


def test_lessons_context_block():
    reflect.add_lesson("For gears always use add_involute_gear.")
    ctx = reflect.lessons_context("a gears request")
    assert ctx.startswith("Hints from past attempts")
    assert "- For gears" in ctx
    assert reflect.lessons_context("totally unrelated zzz") is None


def test_reflect_on_episode_stores_lesson_and_marks():
    eid = episodic.log_episode("make a fan", success=False, error="blade too thin")
    ep = episodic.get_episode(eid)
    lesson = reflect.reflect_on_episode(
        ep, chat_fn=lambda messages: "Fans need blade_thickness below chord*taper.")
    assert "blade_thickness" in lesson
    assert episodic.get_episode(eid)["reflected"] == 1
    assert any("blade_thickness" in le["text"] for le in reflect.all_lessons())


def test_consolidate_dedupes_and_caps():
    emb = _fake_embed({"duplicate": [1.0, 0.0, 0.0]})
    a = reflect.add_lesson("duplicate lesson one", embed_fn=emb)
    b = reflect.add_lesson("duplicate lesson two", embed_fn=emb)  # cosine 1.0 with a
    removed = reflect.consolidate(similarity_threshold=0.95, max_lessons=60)
    assert removed == 1
    remaining = {le["id"] for le in reflect.all_lessons()}
    assert b in remaining and a not in remaining   # newest survives

    for i in range(10):
        reflect.add_lesson(f"unique lesson {i}")
    reflect.consolidate(max_lessons=5)
    assert len(reflect.all_lessons()) == 5


# --------------------------------------------------------------------------- #
# Integration: plan_confirm_execute logs an episode
# --------------------------------------------------------------------------- #
def test_plan_confirm_execute_logs_episode(doc):
    from agentic_cad.agent.execute import approve, plan_confirm_execute
    from agentic_cad.tools.cad_tools import registry

    plan_json = json.dumps({
        "summary": "one box",
        "steps": [{"step": 1, "tool": "add_box",
                   "args": {"length": 10, "width": 10, "height": 10}, "rationale": ""}],
    })
    result = plan_confirm_execute(
        "a 10 mm cube", registry, doc,
        confirm_fn=lambda plan, preview: approve(),
        planner_chat_fn=lambda messages, schema: plan_json)
    assert result.success

    episodes = episodic.recent(1)
    assert episodes and episodes[0]["instruction"] == "a 10 mm cube"
    assert episodes[0]["success"] == 1
    assert abs(episodes[0]["volume_mm3"] - 1000.0) < 1e-6
