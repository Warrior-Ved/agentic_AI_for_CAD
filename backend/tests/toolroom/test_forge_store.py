"""Forge (offline, fake code model) + store persistence round-trips."""
from __future__ import annotations

import pytest

from test_sandbox import GOOD_TOOL

from agentic_cad.toolroom import forge, store
from agentic_cad.tools.registry import ToolRegistry


def fenced(src: str) -> str:
    return f"Here is the tool:\n```python\n{src}```\n"


def test_extract_code_from_fence():
    assert forge.extract_code(fenced("x = 1\n")).strip() == "x = 1"
    assert forge.extract_code("y = 2").strip() == "y = 2"  # no fence -> raw


def test_forge_accepts_good_tool_first_try(toolroom_dir):
    registry = ToolRegistry()
    result = forge.forge_tool("a flat washer", registry,
                              chat_fn=lambda messages: fenced(GOOD_TOOL))
    assert result.ok, result.error
    assert result.name == "test_washer"
    assert result.attempts == 1
    assert "test_washer" in registry
    assert (toolroom_dir / "test_washer.py").exists()
    assert "test_washer" in store.list_tools()


def test_forge_retries_with_feedback_then_succeeds(toolroom_dir):
    replies = [fenced("import os\n" + GOOD_TOOL), fenced(GOOD_TOOL)]
    seen: list[list[dict]] = []

    def chat_fn(messages):
        seen.append(list(messages))
        return replies[len(seen) - 1]

    registry = ToolRegistry()
    result = forge.forge_tool("a flat washer", registry, chat_fn=chat_fn)
    assert result.ok
    assert result.attempts == 2
    # the retry prompt must carry the rejection reason back to the model
    assert "rejected" in seen[1][-1]["content"]
    assert "'os'" in seen[1][-1]["content"]


def test_forge_gives_up_after_max_attempts(toolroom_dir):
    registry = ToolRegistry()
    result = forge.forge_tool("anything", registry,
                              chat_fn=lambda m: fenced("import os\nx = 1\n"),
                              max_attempts=2)
    assert not result.ok
    assert result.attempts == 2
    assert "os" in result.error


def test_forge_rejects_name_collision(toolroom_dir):
    registry = ToolRegistry()
    forge.forge_tool("a washer", registry, chat_fn=lambda m: fenced(GOOD_TOOL))
    result = forge.forge_tool("another washer", registry,
                              chat_fn=lambda m: fenced(GOOD_TOOL), max_attempts=1)
    assert not result.ok
    assert "already exists" in result.error


def test_forged_tool_runs_through_registry(toolroom_dir):
    import math

    registry = ToolRegistry()
    forge.forge_tool("a washer", registry, chat_fn=lambda m: fenced(GOOD_TOOL))
    registry.run("new_document", None) if "new_document" in registry else None
    out = registry.run("test_washer",
                       {"outer_diameter": 30, "inner_diameter": 12, "thickness": 4})
    expected = math.pi * (15**2 - 6**2) * 4
    assert out["volume_mm3"] == pytest.approx(expected, rel=1e-4)


def test_store_load_all_restores_tools_in_new_session(toolroom_dir):
    registry_a = ToolRegistry()
    forge.forge_tool("a washer", registry_a, chat_fn=lambda m: fenced(GOOD_TOOL))

    registry_b = ToolRegistry()          # simulates a fresh process
    loaded = store.load_all(registry_b)
    assert loaded == ["test_washer"]
    assert "test_washer" in registry_b
    assert "(forged tool)" in registry_b.get("test_washer").description


def test_store_load_all_skips_tampered_file(toolroom_dir):
    registry_a = ToolRegistry()
    forge.forge_tool("a washer", registry_a, chat_fn=lambda m: fenced(GOOD_TOOL))
    # tamper: inject a forbidden import into the stored file
    path = toolroom_dir / "test_washer.py"
    path.write_text("import os\n" + path.read_text(encoding="utf-8"), encoding="utf-8")

    registry_b = ToolRegistry()
    assert store.load_all(registry_b) == []   # re-gate at load time catches it
    assert "test_washer" not in registry_b


def test_store_remove(toolroom_dir):
    registry = ToolRegistry()
    forge.forge_tool("a washer", registry, chat_fn=lambda m: fenced(GOOD_TOOL))
    assert store.remove("test_washer")
    assert store.list_tools() == {}
    assert not (toolroom_dir / "test_washer.py").exists()
