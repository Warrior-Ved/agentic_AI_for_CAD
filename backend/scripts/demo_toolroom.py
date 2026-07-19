"""Phase 6 live demo: the agent forges a brand-new tool, the sandbox vets it,
and it becomes a permanent, reusable part of the toolbox.

    .venv/Scripts/python.exe scripts/demo_toolroom.py
    .venv/Scripts/python.exe scripts/demo_toolroom.py "a parametric hollow tube ..."

Requires Ollama (coder model). The forged tool lands in backend/var/toolroom/.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentic_cad.toolroom import forge, store
from agentic_cad.tools.cad_tools import registry
from agentic_cad.tools.registry import ToolRegistry

DEFAULT = ("a parametric hollow tube (pipe segment): outer_diameter, "
           "wall_thickness and length in mm, standing on the XY plane")


def _sample_args(args_model) -> dict:
    """Pick a value inside each required numeric field's declared bounds."""
    from annotated_types import Ge, Gt, Le, Lt

    sample: dict = {}
    for name, f in args_model.model_fields.items():
        if not f.is_required() or f.annotation not in (float, int):
            continue
        lo, hi = 0.0, None
        for m in f.metadata:
            if isinstance(m, Gt):
                lo = float(m.gt)
            elif isinstance(m, Ge):
                lo = float(m.ge)
            elif isinstance(m, Lt):
                hi = float(m.lt)
            elif isinstance(m, Le):
                hi = float(m.le)
        # conservative: a quarter of the way into the allowed range, so we
        # stay clear of the tool's own relationship checks at the boundaries
        sample[name] = lo + (hi - lo) * 0.25 if hi is not None else max(lo * 2, 20.0)
    return sample


def main() -> None:
    request = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
    print(f"Capability request: {request}\n")
    print(f"Tools before: {len(registry.names())}")

    result = forge.forge_tool(request, registry)
    for line in result.log:
        print("  ", line)
    if not result.ok:
        print(f"\nFORGE FAILED after {result.attempts} attempt(s): {result.error}")
        sys.exit(1)

    print(f"\nForged '{result.name}' in {result.seconds}s "
          f"({result.attempts} attempt(s)) — self-test volume {result.test_volume} mm^3")
    print(f"Description: {result.description}")
    print(f"Tools after: {len(registry.names())}\n")

    # Use the new tool immediately, like any other registry tool.
    tool = registry.get(result.name)
    fields = {k: f.description for k, f in tool.args_model.model_fields.items()}
    print("Args:", fields)
    out = registry.run(result.name, _sample_args(tool.args_model))
    print(f"Called {result.name} -> volume {out.get('volume_mm3')} mm^3")

    # Prove persistence: a FRESH registry (new session) re-loads it from disk.
    fresh = ToolRegistry()
    loaded = store.load_all(fresh)
    print(f"\nFresh session loads from toolroom: {loaded}")
    print(f"Stored at: {store.TOOLROOM_DIR / (result.name + '.py')}")


if __name__ == "__main__":
    main()
