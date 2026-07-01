"""Live end-to-end demo: the agent builds a part from a chat instruction using a
local Ollama model (Deliverable-1 acceptance).

    .venv/Scripts/python.exe scripts/demo_agent.py
    .venv/Scripts/python.exe scripts/demo_agent.py "make a 30mm cube with a 8mm hole"
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentic_cad import config
from agentic_cad.agent.loop import make_ollama_chat_fn, run_agent
from agentic_cad.tools.cad_tools import registry

DEFAULT = ("Create a rectangular block 40 mm long, 20 mm wide and 10 mm tall, "
           "with a 10 mm diameter hole through the centre. Then report its volume.")


def main() -> None:
    instruction = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
    model = config.MODEL_PLANNER

    print(f"Model      : {model}")
    print(f"Instruction: {instruction}\n")

    result = run_agent(instruction, chat_fn=make_ollama_chat_fn(model), max_iters=16)

    print("--- Tool calls ---")
    for i, step in enumerate(result.steps, 1):
        ok = step["result"].get("ok")
        detail = step["result"].get("result") or step["result"].get("error")
        print(f"{i:2}. {step['tool']}({step['args']}) -> ok={ok} {detail}")

    print("\n--- Final ---")
    print(f"success    : {result.success} (iterations: {result.iterations})")
    print(f"assistant  : {result.final_text}")

    # Ground-truth check on whatever object was created last.
    try:
        tree = registry.run("get_feature_tree", {})["objects"]
        if tree:
            last = tree[-1]["name"]
            mp = registry.run("mass_properties", {"name": last})
            print(f"\nFinal object {last}: volume={mp['volume_mm3']:.2f} mm^3, "
                  f"solids={mp['solid_count']}")
            config.ensure_dirs()
            out = registry.run("export_step",
                               {"name": last, "filename": str(config.EXPORT_DIR / "agent_part.step")})
            print(f"Exported: {out['exported']}")
    except Exception as exc:
        print("inspection failed:", exc)


if __name__ == "__main__":
    main()
