"""The toolroom sandbox: static AST gate + isolated subprocess validation."""
from __future__ import annotations

from agentic_cad.toolroom.sandbox import check_source, run_sandboxed

GOOD_TOOL = '''\
from pydantic import BaseModel, Field
import math
import Part


TOOL_NAME = "test_washer"
TOOL_DESCRIPTION = "A flat washer (annulus) from outer/inner diameter and thickness."


class Args(BaseModel):
    outer_diameter: float = Field(..., gt=0, description="Outer diameter mm")
    inner_diameter: float = Field(..., gt=0, description="Inner (hole) diameter mm")
    thickness: float = Field(..., gt=0, description="Thickness mm")
    name: str = "Washer"


def build(doc, a: Args):
    if a.inner_diameter >= a.outer_diameter:
        raise ValueError("inner_diameter must be smaller than outer_diameter")
    ring = Part.makeCylinder(a.outer_diameter / 2, a.thickness).cut(
        Part.makeCylinder(a.inner_diameter / 2, a.thickness))
    obj = doc.addObject("Part::Feature", a.name)
    obj.Shape = ring
    doc.recompute()
    return obj


def self_test(doc):
    obj = build(doc, Args(outer_diameter=20, inner_diameter=10, thickness=3))
    assert len(obj.Shape.Solids) == 1
    expected = math.pi * (10 ** 2 - 5 ** 2) * 3
    assert abs(obj.Shape.Volume - expected) < 0.01 * expected
    return obj
'''


def test_gate_accepts_good_tool():
    assert check_source(GOOD_TOOL) == []


def test_gate_rejects_forbidden_import():
    problems = check_source("import os\n" + GOOD_TOOL)
    assert any("'os'" in p for p in problems)


def test_gate_rejects_network_and_files():
    for line in ("import socket", "from urllib import request",
                 "data = open('x').read()", "exec('1')", "__import__('os')"):
        assert check_source(GOOD_TOOL + "\n" + line), line


def test_gate_rejects_missing_contract():
    src = GOOD_TOOL.replace("def self_test", "def other_name")
    assert any("self_test" in p for p in check_source(src))


def test_gate_rejects_dunder_access():
    assert check_source(GOOD_TOOL + "\nx = Part.__dict__\n")


def test_sandbox_accepts_good_tool(tmp_path):
    path = tmp_path / "candidate.py"
    path.write_text(GOOD_TOOL, encoding="utf-8")
    verdict = run_sandboxed(path)
    assert verdict["ok"], verdict
    assert verdict["tool_name"] == "test_washer"
    assert verdict["volume"] > 0


def test_sandbox_rejects_failing_self_test(tmp_path):
    bad = GOOD_TOOL.replace("expected = math.pi * (10 ** 2 - 5 ** 2) * 3",
                            "expected = 99999.0")
    path = tmp_path / "candidate.py"
    path.write_text(bad, encoding="utf-8")
    verdict = run_sandboxed(path)
    assert not verdict["ok"]
    assert "assert" in (verdict["error"] or "").lower()
