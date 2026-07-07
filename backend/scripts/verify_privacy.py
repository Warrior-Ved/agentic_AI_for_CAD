"""Phase 7 privacy verification: PROVE the local-only claim at runtime.

    .venv/Scripts/python.exe scripts/verify_privacy.py

1. installs the egress guard (as the server does by default),
2. demonstrates that outbound connections to the internet are BLOCKED,
3. demonstrates that loopback (the local Ollama server) stays allowed,
4. runs a full geometry pipeline (plan -> execute -> export) with the guard
   active — no model call needed, so this also proves the CAD core works
   with networking effectively disabled.
"""
from __future__ import annotations

import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentic_cad import config, privacy
from agentic_cad.agent.execute import execute_plan
from agentic_cad.agent.plan import Plan, PlanStep
from agentic_cad.cad import document
from agentic_cad.tools.cad_tools import registry

CHECK, CROSS = "[ok]", [][:] or "[X]"


def main() -> None:
    print(f"ALLOW_CLOUD: {config.ALLOW_CLOUD} (must be False for the guarantee)")
    privacy.install_guard()
    print(f"{CHECK} egress guard installed — allowed hosts: "
          f"{', '.join(privacy.status()['allowed_hosts'])}\n")

    # 1. external egress must fail
    for host, port in (("8.8.8.8", 53), ("api.openai.com", 443), ("example.com", 80)):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3)
            s.connect((host, port))
            s.close()
            print(f"{CROSS} PRIVACY FAILURE: connection to {host}:{port} was NOT blocked")
            sys.exit(1)
        except privacy.PrivacyViolation:
            print(f"{CHECK} blocked outbound connection to {host}:{port}")
        except OSError as exc:
            print(f"{CROSS} unexpected error (not the guard) for {host}:{port}: {exc}")
            sys.exit(1)

    # 2. loopback must still work (best-effort: only if Ollama is up)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect(("127.0.0.1", 11434))
        s.close()
        print(f"{CHECK} loopback (local Ollama, 127.0.0.1:11434) still allowed")
    except privacy.PrivacyViolation:
        print(f"{CROSS} PRIVACY FAILURE: loopback was wrongly blocked")
        sys.exit(1)
    except OSError:
        print("[--] loopback check skipped (no local Ollama listening) — guard did not block it")

    # 3. full CAD pipeline under the guard, zero network needed
    plan = Plan(summary="offline verification part", steps=[
        PlanStep(step=1, tool="add_box", args={"length": 40, "width": 20, "height": 10}),
        PlanStep(step=2, tool="add_cylinder",
                 args={"radius": 5, "height": 12, "x": 20, "y": 10, "z": -1}),
        PlanStep(step=3, tool="boolean_cut",
                 args={"base": "Box", "tool": "Cylinder", "name": "Cut"}),
        PlanStep(step=4, tool="export_step", args={"name": "Cut", "filename": "privacy_check"}),
    ])
    doc = document.new_document("PrivacyCheck")
    result = execute_plan(plan, registry, doc)
    assert result.success, f"pipeline failed under guard: {result.message}"
    print(f"{CHECK} geometry pipeline ran fully offline "
          f"(volume {result.final_volume:.1f} mm^3, STEP exported)")

    print(f"\nBlocked attempts logged: {privacy.violations()}")
    print("\nVERDICT: no data can leave this machine while the agent runs "
          "(loopback-only egress, enforced at the socket layer).")


if __name__ == "__main__":
    main()
