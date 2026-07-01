"""Smoke demo: build the Deliverable-1 holed block, save it, export STEP + STL.

Run from the backend/ folder with the project venv:

    .venv/Scripts/python.exe scripts/demo_holed_block.py

Outputs land in backend/var/ (local only — nothing leaves the machine).
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make the package importable when run as a plain script (no install needed).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentic_cad import config
from agentic_cad.cad import document, geometry, inspect, recipes


def main() -> None:
    config.ensure_dirs()

    out = recipes.holed_block(length=40, width=20, height=10, hole_diameter=10)
    result = out["result"]

    mp = inspect.mass_properties(result)
    print("Built holed block:")
    print(f"  volume      : {mp['volume_mm3']:.2f} mm^3")
    print(f"  surface area: {mp['area_mm2']:.2f} mm^2")
    print(f"  solids      : {mp['solid_count']}  (closed: {mp['is_closed']})")

    fcstd = document.save_document(out["doc"])
    step = geometry.export_step(result, config.EXPORT_DIR / "holed_block.step")
    stl = geometry.export_stl(result, config.EXPORT_DIR / "holed_block.stl")

    print("\nSaved artifacts:")
    for p in (fcstd, step, stl):
        print(f"  {p}  ({p.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
