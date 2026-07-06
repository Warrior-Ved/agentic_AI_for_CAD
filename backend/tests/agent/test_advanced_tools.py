"""The multi-surface / generator / ingest tools exercised through the registry —
the same path the planner, executor and MCP server use."""
from __future__ import annotations

import pytest

from agentic_cad.agent.planner import tool_catalog
from agentic_cad.tools.cad_tools import registry

NEW_TOOLS = [
    "revolve", "loft", "sweep", "add_helix", "polar_array", "rotate", "add_torus",
    "sketch_polygon", "sketch_ellipse", "add_involute_gear", "add_fan_rotor",
    "add_spring", "import_step", "import_stl", "open_document",
]


def test_new_tools_registered():
    names = registry.names()
    for tool in NEW_TOOLS:
        assert tool in names, f"{tool} missing from registry"


def test_planner_catalog_advertises_generators():
    catalog = tool_catalog(registry)
    for tool in ("add_involute_gear", "add_fan_rotor", "add_spring", "revolve", "loft"):
        assert tool in catalog


def test_gear_via_registry():
    registry.run("new_document", {"name": "GearDoc"})
    out = registry.run("add_involute_gear",
                       {"module_mm": 2, "teeth": 16, "thickness": 6, "bore_diameter": 6})
    assert out["solid_count"] == 1
    assert out["volume_mm3"] > 0


def test_fan_rotor_via_registry():
    registry.run("new_document", {"name": "RotorDoc"})
    out = registry.run("add_fan_rotor",
                       {"hub_radius": 8, "hub_height": 16, "blade_count": 5,
                        "blade_length": 30, "chord": 10})
    assert out["solid_count"] == 1


def test_polar_array_via_registry():
    registry.run("new_document", {"name": "ArrayDoc"})
    registry.run("add_box", {"length": 4, "width": 4, "height": 4, "x": 15, "name": "Pin"})
    out = registry.run("polar_array", {"base": "Pin", "count": 4})
    assert out["volume_mm3"] == pytest.approx(4 * 64, rel=1e-6)


def test_revolve_and_spring_via_registry():
    registry.run("new_document", {"name": "MiscDoc"})
    registry.run("new_sketch", {"plane": "XZ", "name": "Half"})
    registry.run("sketch_rectangle", {"sketch": "Half", "width": 5, "height": 12})
    rev = registry.run("revolve", {"sketch": "Half"})
    assert rev["solid_count"] == 1
    spring = registry.run("add_spring",
                          {"coil_radius": 8, "wire_radius": 1, "pitch": 4, "turns": 5, "x": 40})
    assert spring["solid_count"] == 1


def test_gear_arg_validation_rejects_bad_teeth():
    with pytest.raises(Exception):
        registry.get("add_involute_gear").args_model(module_mm=2, teeth=3, thickness=5)
