"""The toolroom's isolated testing space.

Two independent layers of defence before a forged tool touches the live app:

1. **AST gate** (``check_source``) — static: parse the candidate, allow only a
   whitelist of imports, refuse forbidden names (os/sys/open/eval/...), and
   require the tool contract symbols.
2. **Subprocess sandbox** (``run_sandboxed``) — dynamic: import the module and
   run its ``self_test`` in a FRESH FreeCAD instance in a separate process, so
   a kernel crash, hang or bad geometry can never take down the agent. The
   verdict comes back as one JSON line on stdout.

Run the dynamic stage directly with:
    python -m agentic_cad.toolroom.sandbox <candidate.py>
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

from agentic_cad import config
from agentic_cad.toolroom.templates import ALLOWED_IMPORTS, FORBIDDEN_NAMES, REQUIRED_SYMBOLS


# --------------------------------------------------------------------------- #
# Layer 1: static AST gate
# --------------------------------------------------------------------------- #
def check_source(source: str) -> list[str]:
    """Return a list of problems; empty means the source passes the gate."""
    problems: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"syntax error: {exc}"]

    defined: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in ALLOWED_IMPORTS:
                    problems.append(f"import of {alias.name!r} is not allowed")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod not in ALLOWED_IMPORTS:
                problems.append(f"import from {mod!r} is not allowed")
        elif isinstance(node, ast.Name):
            if node.id in FORBIDDEN_NAMES:
                problems.append(f"forbidden name {node.id!r}")
            if isinstance(node.ctx, ast.Store):
                defined.add(node.id)
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            problems.append(f"dunder attribute access {node.attr!r} is not allowed")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)

    for symbol in REQUIRED_SYMBOLS:
        if symbol not in defined:
            problems.append(f"contract symbol {symbol!r} is missing")
    return sorted(set(problems))


# --------------------------------------------------------------------------- #
# Layer 2: isolated dynamic validation
# --------------------------------------------------------------------------- #
def run_sandboxed(path: str | Path, timeout: float = 120.0) -> dict:
    """Validate a candidate tool file in a separate process.

    Returns a verdict dict: {"ok": bool, "error": str|None, "tool_name": ...,
    "description": ..., "volume": ...}.
    """
    cmd = [sys.executable, "-m", "agentic_cad.toolroom.sandbox", str(path)]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            cwd=str(config.BACKEND_DIR),
            env={**__import__("os").environ, "PYTHONPATH": str(config.SRC_DIR)},
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"sandbox timed out after {timeout}s"}

    for line in reversed(proc.stdout.strip().splitlines() or [""]):
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                break
    tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-6:]
    return {"ok": False,
            "error": f"sandbox process exited {proc.returncode} without a verdict: "
                     + " | ".join(tail)}


def _validate_in_this_process(path: Path) -> dict:
    """The sandbox payload — only ever run inside the subprocess."""
    import importlib.util

    source = path.read_text(encoding="utf-8")
    problems = check_source(source)          # re-gate inside the sandbox too
    if problems:
        return {"ok": False, "error": "; ".join(problems)}

    from agentic_cad.cad import bootstrap

    bootstrap.ensure_freecad_importable()
    import FreeCAD  # noqa: F401 — MUST be loaded before the candidate's `import Part`,
    #                 or the compiled module access-violates the whole process

    spec = importlib.util.spec_from_file_location("forged_candidate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    from pydantic import BaseModel

    if not (isinstance(module.TOOL_NAME, str) and module.TOOL_NAME.isidentifier()):
        return {"ok": False, "error": "TOOL_NAME must be a python identifier"}
    if not issubclass(module.Args, BaseModel):
        return {"ok": False, "error": "Args must be a pydantic BaseModel"}

    from agentic_cad.agent.validation import validate_document

    import FreeCAD as FCad

    doc = FCad.newDocument("__sandbox__")
    obj = module.self_test(doc)
    if obj is None or not hasattr(obj, "Shape"):
        return {"ok": False, "error": "self_test must return the created DocumentObject"}
    shape = obj.Shape
    if not shape.isValid() or len(shape.Solids) < 1 or shape.Volume <= 0:
        return {"ok": False, "error": "self_test produced invalid or empty geometry"}
    ok, doc_problems = validate_document(doc)
    if not ok:
        return {"ok": False, "error": "kernel validation failed: " + "; ".join(doc_problems)}

    return {"ok": True, "error": None, "tool_name": module.TOOL_NAME,
            "description": str(module.TOOL_DESCRIPTION), "volume": round(shape.Volume, 3)}


if __name__ == "__main__":
    try:
        verdict = _validate_in_this_process(Path(sys.argv[1]))
    except AssertionError as exc:
        verdict = {"ok": False, "error": f"self_test assertion failed: {exc or 'geometry check'}"}
    except Exception as exc:
        import traceback
        where = ""
        for frame in reversed(traceback.extract_tb(exc.__traceback__)):
            if frame.filename.endswith(".py") and "forged" in frame.filename or frame.line:
                where = f" (at line {frame.lineno}: {frame.line})"
                break
        verdict = {"ok": False, "error": f"{type(exc).__name__}: {exc}{where}"}
    sys.stdout.write(json.dumps(verdict) + "\n")
    sys.stdout.flush()
    # Hard-exit: FreeCAD's C++ teardown can access-violate on Windows, which
    # would discard buffered output — the verdict is already flushed above.
    import os
    os._exit(0)
