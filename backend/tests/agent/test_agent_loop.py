"""Agent-loop tests using a deterministic scripted 'LLM' (no Ollama required).

This proves the loop's mechanics — tool dispatch, result feedback, completion,
error recovery and the iteration guard — independently of any real model.
"""
from __future__ import annotations

import math

import pytest

from agentic_cad.agent.loop import run_agent


def scripted_chat(turns):
    """Build a chat_fn that replays `turns` on successive calls.

    Each turn is either:
      * a list of (tool_name, args) -> emit those tool calls, or
      * a str -> final assistant message with no tool calls.
    """
    it = iter(turns)

    def chat_fn(messages, tools):
        try:
            turn = next(it)
        except StopIteration:
            return {"message": {"content": "done", "tool_calls": []}}
        if isinstance(turn, str):
            return {"message": {"content": turn, "tool_calls": []}}
        tool_calls = [{"function": {"name": n, "arguments": a}} for n, a in turn]
        return {"message": {"content": "", "tool_calls": tool_calls}}

    return chat_fn


def test_agent_builds_holed_block_end_to_end():
    turns = [
        [("new_document", {"name": "HB"})],
        [("add_box", {"length": 40, "width": 20, "height": 10, "name": "Block"})],
        [("add_cylinder", {"radius": 5, "height": 10, "name": "Hole", "x": 20, "y": 10})],
        [("boolean_cut", {"base": "Block", "tool": "Hole", "name": "HoledBlock"})],
        "Built a 40x20x10 mm block with a central 10 mm through-hole.",
    ]
    res = run_agent("Create a holed block", chat_fn=scripted_chat(turns))

    assert res.success
    assert res.tool_names == ["new_document", "add_box", "add_cylinder", "boolean_cut"]
    final = res.steps[-1]["result"]
    assert final["ok"] is True
    expected = 40 * 20 * 10 - math.pi * 25 * 10
    assert final["result"]["volume_mm3"] == pytest.approx(expected, rel=1e-4)
    assert "block" in res.final_text.lower()


def test_agent_recovers_from_a_tool_error():
    turns = [
        [("boolean_cut", {"base": "Ghost", "tool": "Ghost2"})],   # fails: no objects
        [("add_box", {"length": 10, "width": 10, "height": 10, "name": "B"})],  # recovers
        "Recovered and built a 10 mm cube.",
    ]
    res = run_agent("do something", chat_fn=scripted_chat(turns))

    assert res.success
    assert res.steps[0]["result"]["ok"] is False
    assert "error" in res.steps[0]["result"]
    assert res.steps[1]["result"]["ok"] is True


def test_agent_reports_unknown_tool_without_crashing():
    turns = [
        [("teleport_part", {"to": "moon"})],   # not a real tool
        "Could not do that.",
    ]
    res = run_agent("teleport it", chat_fn=scripted_chat(turns))
    assert res.success
    assert res.steps[0]["result"]["ok"] is False
    assert "unknown tool" in res.steps[0]["result"]["error"]


def test_agent_parses_text_emitted_tool_calls():
    """Local models often print tool calls as JSON text instead of using the
    structured API; the loop must still extract and run them."""
    text = ('I will build it.\n'
            '{"name": "add_box", "arguments": {"length": 10, "width": 10, "height": 10}}')
    turns = iter([
        {"message": {"content": text, "tool_calls": []}},  # text-only tool call
        {"message": {"content": "Built a 10mm cube.", "tool_calls": []}},
    ])

    def chat_fn(messages, tools):
        return next(turns)

    res = run_agent("make a cube", chat_fn=chat_fn)
    assert res.tool_names == ["add_box"]
    assert res.steps[0]["result"]["ok"] is True
    assert res.steps[0]["result"]["result"]["volume_mm3"] == pytest.approx(1000)


def test_text_fallback_ignores_non_tool_json():
    """JSON in the text that does not name a real tool must NOT be executed."""
    turns = iter([
        {"message": {"content": '{"note": "just some data", "value": 5}', "tool_calls": []}},
    ])

    def chat_fn(messages, tools):
        return next(turns)

    res = run_agent("chat", chat_fn=chat_fn)
    assert res.steps == []          # nothing executed
    assert res.success is True


def test_agent_stops_at_max_iters():
    def always_calls(messages, tools):
        return {"message": {"content": "",
                            "tool_calls": [{"function": {"name": "get_feature_tree",
                                                         "arguments": {}}}]}}
    res = run_agent("loop forever", chat_fn=always_calls, max_iters=3)
    assert res.success is False
    assert res.iterations == 3
    assert len(res.steps) == 3
