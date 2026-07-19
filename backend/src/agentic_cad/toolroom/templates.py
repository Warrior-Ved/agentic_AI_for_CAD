"""The toolroom contract: what a forged tool's source file must look like, and
the code-generation prompt that asks the local coder model to write one."""
from __future__ import annotations

# Modules a forged tool is allowed to import (enforced by the AST gate).
ALLOWED_IMPORTS = {
    "math", "typing", "pydantic",
    "FreeCAD", "Part", "Sketcher",
    "agentic_cad", "agentic_cad.cad",
    "agentic_cad.cad.geometry", "agentic_cad.cad.features",
    "agentic_cad.cad.surfaces", "agentic_cad.cad.parts",
}

# Names that must never appear anywhere in forged code (defense in depth on
# top of the import whitelist).
FORBIDDEN_NAMES = {
    "os", "sys", "subprocess", "socket", "shutil", "pathlib", "importlib",
    "open", "eval", "exec", "compile", "__import__", "globals", "locals",
    "input", "breakpoint", "exit", "quit", "getattr", "setattr", "delattr",
    "vars", "memoryview", "ctypes",
}

# Symbols the module must define to satisfy the contract.
REQUIRED_SYMBOLS = ("TOOL_NAME", "TOOL_DESCRIPTION", "Args", "build", "self_test")

CONTRACT = '''\
from pydantic import BaseModel, Field
import math
import FreeCAD as FCad
import Part

TOOL_NAME = "snake_case_tool_name"
TOOL_DESCRIPTION = "One line: what solid this tool creates."


class Args(BaseModel):
    # every argument MUST have a sensible default or be required with gt/ge bounds
    outer_diameter: float = Field(..., gt=0, description="... in mm")
    name: str = "Part"


def build(doc, a: Args):
    """Create the solid in ``doc`` and return the created DocumentObject."""
    shape = ...  # build with Part primitives / booleans, dimensions in mm
    obj = doc.addObject("Part::Feature", a.name)
    obj.Shape = shape
    doc.recompute()
    return obj


def self_test(doc):
    """Build a sample part and ASSERT its geometry analytically
    (volume / bounding box / solid count). Return the created object."""
    obj = build(doc, Args(outer_diameter=20))
    assert len(obj.Shape.Solids) == 1
    expected = 123.0  # exact analytic volume of the sample
    assert abs(obj.Shape.Volume - expected) < 0.01 * expected
    return obj
'''

CODEGEN_SYSTEM = """\
You write ONE new parametric CAD tool for a FreeCAD agent, as a single Python \
module. Reply with ONLY a fenced python code block, nothing else.

The module MUST follow this exact contract:
{contract}

Hard rules:
- Allowed imports ONLY: math, pydantic, FreeCAD, Part, Sketcher. \
No os/sys/subprocess/open/eval/exec — the module is rejected otherwise.
- All dimensions in millimetres. Build the shape with the Part module \
(Part.makeBox/makeCylinder/makeCone/makeSphere/makeTorus, .cut/.fuse/.common, \
Part.Face/Part.Wire/extrude/revolve) and attach it to a Part::Feature.
- TOOL_NAME must be snake_case and must NOT be one of the existing tools: \
{existing}
- Args: pydantic v2, numeric fields with gt=0 bounds, descriptions with units.
- Validate argument RELATIONSHIPS at the top of build() and raise ValueError \
with a clear message (e.g. inner diameter must be smaller than outer).
- self_test(doc) must build one sample and assert its EXACT analytic volume \
(derive the formula yourself) within 1%, plus solid count. No prints.
- Fields are ONLY read from an Args INSTANCE: call build(doc, Args(outer_diameter=20, \
...)) with explicit numbers. NEVER use Args.some_field on the class — that is a \
pydantic FieldInfo object, not a number, and it will crash.
- Keep it under 80 lines. No comments needed except where genuinely subtle.
"""

CODEGEN_USER = """\
Write the tool module for this capability request:

{request}
"""
