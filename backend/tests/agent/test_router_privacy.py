"""Phase 7: intent router, read-only answer loop, and the privacy guard."""
from __future__ import annotations

import json
import socket

import pytest

from agentic_cad import privacy
from agentic_cad.agent import router
from agentic_cad.tools.cad_tools import registry

# --------------------------------------------------------------------------- #
# Router
# --------------------------------------------------------------------------- #


def _route_reply(intent, reason="because"):
    return lambda messages, schema: json.dumps({"intent": intent, "reason": reason})


def test_route_parses_intent():
    r = router.route_intent("make a cube", has_model=False, chat_fn=_route_reply("build"))
    assert r.intent == "build"


def test_route_falls_back_to_build_on_garbage():
    r = router.route_intent("make a cube", chat_fn=lambda m, s: "not json at all")
    assert r.intent == "build"
    assert "unavailable" in r.reason


def test_route_rejects_edit_without_model():
    r = router.route_intent("make the hole bigger", has_model=False,
                            chat_fn=_route_reply("edit"))
    assert r.intent == "build"          # downgraded: nothing to edit yet
    assert "no model" in r.reason


def test_route_prompt_tells_model_state():
    seen = {}

    def chat_fn(messages, schema):
        seen["system"] = messages[0]["content"]
        return json.dumps({"intent": "edit", "reason": ""})

    router.route_intent("make it bigger", has_model=True, chat_fn=chat_fn)
    assert "ALREADY EXISTS" in seen["system"]


# --------------------------------------------------------------------------- #
# Read-only answer loop
# --------------------------------------------------------------------------- #
def test_read_only_registry_excludes_mutators():
    sub = router.read_only_registry(registry)
    names = set(sub.names())
    assert "mass_properties" in names and "get_feature_tree" in names
    for mutator in ("add_box", "boolean_cut", "extrude", "set_property", "forge_tool"):
        assert mutator not in names


def test_answer_question_uses_tools_then_answers(doc):
    import FreeCAD as App

    box = doc.addObject("Part::Box", "Box")
    box.Length = box.Width = box.Height = 10
    doc.recompute()
    App.setActiveDocument(doc.Name)

    calls = {"n": 0}

    def chat_fn(messages, tools):
        calls["n"] += 1
        if calls["n"] == 1:   # first turn: ask for the mass properties
            return {"message": {"content": "", "tool_calls": [
                {"function": {"name": "mass_properties", "arguments": {"name": "Box"}}}]}}
        tool_result = json.loads(messages[-1]["content"])
        vol = round(tool_result["result"]["volume_mm3"])
        return {"message": {"content": f"The part's volume is {vol} mm^3."}}

    result = router.answer_question("what is the volume?", registry, chat_fn=chat_fn)
    assert result.success
    assert "1000" in result.final_text
    assert result.tool_names == ["mass_properties"]


# --------------------------------------------------------------------------- #
# Privacy guard
# --------------------------------------------------------------------------- #
@pytest.fixture
def guard():
    privacy.install_guard()
    try:
        yield
    finally:
        privacy.uninstall_guard()


def test_guard_blocks_external_connect(guard):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with pytest.raises(privacy.PrivacyViolation):
        s.connect(("8.8.8.8", 53))
    s.close()
    assert any("8.8.8.8" in v for v in privacy.violations())


def test_guard_blocks_hostnames(guard):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with pytest.raises(privacy.PrivacyViolation):
        s.connect(("api.openai.com", 443))
    s.close()


def test_guard_allows_loopback(guard):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.settimeout(3)
    client.connect(("127.0.0.1", port))   # must NOT raise
    client.close()
    server.close()


def test_guard_uninstall_restores():
    privacy.install_guard()
    privacy.uninstall_guard()
    assert not privacy.guard_active()
    # after uninstall, connect is the original (loopback connect still works)
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.settimeout(3)
    client.connect(("127.0.0.1", server.getsockname()[1]))
    client.close()
    server.close()


def test_status_reports():
    privacy.install_guard()
    try:
        st = privacy.status()
        assert st["guard_active"] is True
        assert "127.0.0.1" in st["allowed_hosts"]
    finally:
        privacy.uninstall_guard()
