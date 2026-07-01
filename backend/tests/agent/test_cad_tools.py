"""Tests for the CAD tool registry — deterministic, no LLM involved."""
from __future__ import annotations

import math

import pytest

from agentic_cad.tools.cad_tools import registry


def test_registry_exposes_full_toolset():
    names = registry.names()
    assert len(names) >= 20
    for required in ("add_box", "add_cylinder", "boolean_cut", "extrude",
                     "set_property", "get_feature_tree", "export_step"):
        assert required in names


def test_ollama_specs_are_well_formed():
    for spec in registry.to_ollama_specs():
        assert spec["type"] == "function"
        fn = spec["function"]
        assert fn["name"] and isinstance(fn["description"], str)
        assert fn["parameters"]["type"] == "object"


def test_add_box_coerces_string_arguments():
    # Local models frequently emit numbers as strings — pydantic must coerce them.
    registry.run("new_document", {"name": "Coerce"})
    out = registry.run("add_box", {"length": "40", "width": "20", "height": "10"})
    assert out["volume_mm3"] == pytest.approx(8000)


def test_build_holed_block_via_tool_sequence():
    registry.run("new_document", {"name": "HB"})
    box = registry.run("add_box", {"length": 40, "width": 20, "height": 10, "name": "Block"})
    cyl = registry.run("add_cylinder",
                       {"radius": 5, "height": 10, "name": "Hole", "x": 20, "y": 10})
    cut = registry.run("boolean_cut",
                       {"base": box["name"], "tool": cyl["name"], "name": "HoledBlock"})
    expected = 40 * 20 * 10 - math.pi * 25 * 10
    assert cut["volume_mm3"] == pytest.approx(expected, rel=1e-4)
    assert cut["solid_count"] == 1

    names = [o["name"] for o in registry.run("get_feature_tree", {})["objects"]]
    assert cut["name"] in names


def test_parametric_set_property_via_tool():
    registry.run("new_document", {"name": "Param"})
    box = registry.run("add_box", {"length": 10, "width": 10, "height": 10})
    out = registry.run("set_property", {"name": box["name"], "property": "Length", "value": 20})
    assert out["volume_mm3"] == pytest.approx(2000)


def test_sketch_extrude_via_tools():
    registry.run("new_document", {"name": "Sketch"})
    sk = registry.run("new_sketch", {"plane": "XY", "name": "Profile"})
    registry.run("sketch_rectangle", {"sketch": sk["name"], "width": 30, "height": 20})
    pad = registry.run("extrude", {"sketch": sk["name"], "length": 5, "name": "Pad"})
    assert pad["volume_mm3"] == pytest.approx(30 * 20 * 5)


def test_missing_required_argument_is_validation_error():
    registry.run("new_document", {"name": "Bad"})
    with pytest.raises(Exception):  # pydantic ValidationError (length missing)
        registry.run("add_box", {"width": 10, "height": 10})


def test_unknown_object_name_raises():
    registry.run("new_document", {"name": "Ghost"})
    with pytest.raises(ValueError):
        registry.run("boolean_cut", {"base": "DoesNotExist", "tool": "Nope"})
