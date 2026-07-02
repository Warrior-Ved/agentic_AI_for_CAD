"""Live Phase 3 demo: Clarify -> Plan -> Confirm -> Execute with a local model.

    .venv/Scripts/python.exe scripts/demo_plan_confirm.py
    .venv/Scripts/python.exe scripts/demo_plan_confirm.py "a 50mm cube with a 12mm hole"
    .venv/Scripts/python.exe scripts/demo_plan_confirm.py --interactive "..."
    .venv/Scripts/python.exe scripts/demo_plan_confirm.py --no-clarify "..."

The agent first asks any GEOMETRY-CRITICAL clarifying questions (where the hole
is, through vs blind, depth, axis) — at the terminal you answer them (blank
accepts the suggested default). Those answers become a resolved spec that grounds
the planner. The planner then emits a schema-constrained JSON plan, it is
previewed in a throwaway document, and only the approved plan is executed into
the live model with per-step validation, bounded repair and single-undo.

By default the confirm step auto-approves so it runs unattended; pass
--interactive to review/approve/reject each plan yourself. Pass --no-clarify to
skip the clarification stage entirely.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentic_cad import config
from agentic_cad.agent.clarify import make_clarify_fn, prompt_answers_console
from agentic_cad.agent.execute import approve, plan_confirm_execute, reject
from agentic_cad.agent.planner import make_ollama_plan_fn, make_ollama_repair_fn
from agentic_cad.cad import document
from agentic_cad.tools.cad_tools import registry

DEFAULT = "Create a 40x20x10 mm block with a 10 mm diameter hole through the centre."


def main() -> None:
    args = sys.argv[1:]
    interactive = "--interactive" in args
    clarify = "--no-clarify" not in args
    args = [a for a in args if a not in ("--interactive", "--no-clarify")]
    instruction = args[0] if args else DEFAULT
    model = config.MODEL_PLANNER

    print(f"Model      : {model}")
    print(f"Instruction: {instruction}")
    print(f"Clarify    : {'on' if clarify else 'off'}\n")

    def confirm_fn(plan, preview):
        print("=" * 70)
        print(plan.render())
        print("-" * 70)
        print(f"PREVIEW (throwaway doc): success={preview.success}"
              f"  volume={preview.final_volume}")
        for s in preview.steps:
            flag = "ok" if s.ok else f"FAIL: {s.error}"
            print(f"   step {s.step} {s.tool} -> {flag}")
        print("=" * 70)
        if not interactive:
            print(">> auto-approving (pass --interactive to review)\n")
            return approve()
        ans = input("Approve this plan? [y]es / [n]o+feedback: ").strip()
        if ans.lower().startswith("y"):
            return approve()
        return reject(input("What should change? ").strip() or "please revise")

    live = document.new_document("Session")
    result = plan_confirm_execute(
        instruction, registry, live,
        confirm_fn=confirm_fn,
        planner_chat_fn=make_ollama_plan_fn(model),
        repair_fn=make_ollama_repair_fn(model),
        clarify_fn=prompt_answers_console if clarify else None,
        clarify_chat_fn=make_clarify_fn(model) if clarify else None,
    )

    print("\n--- EXECUTION ---")
    print(f"success={result.success} aborted={result.aborted}  {result.message}")
    for s in result.steps:
        flag = "ok" if s.ok else f"FAIL: {s.error}"
        rep = f" (repairs={s.repairs})" if s.repairs else ""
        print(f"   step {s.step} {s.tool}({s.args}) -> {flag}{rep}")
    if result.success:
        print(f"\nFinal volume: {result.final_volume:.2f} mm^3")
        config.ensure_dirs()
        obj = result.summary.get("final_object")
        if obj:
            out = registry.run("export_step",
                               {"name": obj, "filename": str(config.EXPORT_DIR / "pce_part.step")})
            print(f"Exported: {out['exported']}")


if __name__ == "__main__":
    main()
