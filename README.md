# Agentic AI Assistant for Engineering Parametric CAD

A privacy-preserving, human-in-the-loop agent that **creates, edits and physically
validates parametric 3D CAD models** in FreeCAD through natural-language
conversation. Every model is proposed as an editable, step-by-step plan the user
approves before any geometry changes, and **all reasoning and data stay on the
local machine**.

> MSc Major Project — Ved Ravindra Prakash. See [`docs/`](docs/) for the full
> proposal, supervisor deck and literature review.

---

## Architecture at a glance

The system is four layers inside one local privacy boundary:

| Layer            | What it does                                              | Where it runs |
| ---------------- | -------------------------------------------------------- | ------------- |
| **Reasoning**    | Intent router + planner (local LLMs via Ollama)          | venv (3.11)   |
| **MCP tools**    | CAD ops, simulation, file ingest, toolroom               | venv (3.11)   |
| **Foundation**   | FreeCAD kernel, episodic + vector memory, Ollama serving | system + venv |
| **Verification** | Plan → Confirm → Execute, per-step kernel validation     | venv (3.11)   |

### Key integration decision: one venv, FreeCAD imported in-process

FreeCAD 1.0 ships its own Python (3.11.10) with compiled modules. Because the
project venv is **Python 3.11**, it can `import FreeCAD` directly — we just add
FreeCAD's `bin`/`lib` to `sys.path` and register its DLL directory. That bootstrap
lives in [`agentic_cad.cad.bootstrap`](backend/src/agentic_cad/cad/bootstrap.py),
so there is **no separate interpreter to manage** — `pytest`, the demo and the
(future) MCP servers all run in the single venv.

> The venv **must be Python 3.11** to match FreeCAD's ABI. 3.12+ cannot load
> FreeCAD's compiled modules. Ideally match the patch version (3.11.10) too.

---

## Prerequisites

| Tool         | Version           | Notes                                                |
| ------------ | ----------------- | ---------------------------------------------------- |
| FreeCAD      | 1.0 (FEM included) | Default path `C:\Program Files\FreeCAD 1.0` (auto-detected; override with `FREECAD_HOME`) |
| Python       | 3.11.x            | The venv interpreter — **must be 3.11**              |
| Ollama       | latest            | Local model serving (router / planner / vision)      |
| GPU          | ≥ 6 GB VRAM       | Models are sized to fit (≤ 7B quantised)             |

**Local model roster** (configured in
[`config.py`](backend/src/agentic_cad/config.py), overridable via env vars):

| Role     | Model                          |
| -------- | ------------------------------ |
| Router   | `llama3.2:3b`                  |
| Planner  | `qwen2.5-coder:7b`             |
| Coder    | `qwen2.5-coder:7b`             |
| Vision   | `qwen3-vl:4b`                  |
| Embeddings | `locusai/all-minilm-l6-v2`   |

---

## Build / setup

```bash
cd backend

# 1. (Re)create the venv with Python 3.11 if needed — it must be 3.11.
#    py -3.11 -m venv .venv

# 2. Install dev tooling (pytest, ruff)
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt

# 3. Install runtime deps incrementally as you reach each phase
#    (Phase 1 / foundation needs none beyond FreeCAD itself)
.venv/Scripts/python.exe -m pip install -r requirements.txt
```

No `import FreeCAD` setup is needed — `agentic_cad.cad.bootstrap` handles it,
auto-detecting the install (or reading `FREECAD_HOME`).

## Test

```bash
cd backend
.venv/Scripts/python.exe -m pytest          # full suite
.venv/Scripts/python.exe -m pytest -v       # verbose
```

The CAD tests assert **analytical geometry** (volumes, bounding boxes, mass
properties) so a regression in the kernel wrapper is caught, not just a crash.

## Run the demos

```bash
cd backend

# Phase 1 — deterministic parametric build (no LLM)
.venv/Scripts/python.exe scripts/demo_holed_block.py

# Phase 2 — the AGENT builds a part from a natural-language instruction (needs Ollama)
.venv/Scripts/python.exe scripts/demo_agent.py
.venv/Scripts/python.exe scripts/demo_agent.py "make a 30mm cube with an 8mm hole"

# Phase 3 — Plan -> Confirm -> Execute (schema-constrained plan, preview, approve, validated execute)
.venv/Scripts/python.exe scripts/demo_plan_confirm.py
.venv/Scripts/python.exe scripts/demo_plan_confirm.py --interactive "a 50mm cube with a 12mm hole"
```

`demo_holed_block.py` builds the Deliverable-1 holed block parametrically and
writes `.FCStd` + `.step` + `.stl` into `backend/var/`. `demo_agent.py` runs the
ReAct loop against a local model, printing each tool call, error recovery, and the
verified final geometry.

## Run the MCP CAD server standalone

```bash
cd backend
.venv/Scripts/python.exe -m agentic_cad.mcp_servers.cad_server   # stdio transport
```

Any MCP client can spawn this and call the 23 CAD tools (see
`tests/agent/test_mcp_server.py` for a client round-trip example).

---

## Project layout

```
backend/
  src/agentic_cad/
    config.py            # shared config (paths, model roster, FreeCAD location)
    cad/                 # FreeCAD-facing layer (runs in venv via bootstrap)
      bootstrap.py       #   makes `import FreeCAD` work in-process
      document.py        #   document lifecycle (new/open/save/recompute)
      geometry.py        #   atomic Part ops: primitives, booleans, fillet, export
      features.py        #   parametric spine: sketch -> extrude -> datum edit
      inspect.py         #   read model state (feature tree, bbox, mass props)
      recipes.py         #   composite parts (holed block = Deliverable 1)
    tools/               # agent-facing tool layer
      registry.py        #   Tool/ToolRegistry: pydantic args -> JSON schema
      cad_tools.py       #   the 23 CAD tools (single source of truth)
    mcp_servers/
      cad_server.py      #   MCP server publishing the registry over stdio
    agent/
      loop.py            #   ReAct loop (Ollama tool-calling + text fallback)
      plan.py            #   Plan/PlanStep schema + validate-against-registry
      planner.py         #   constrained-JSON plan + LLM repair generation
      validation.py      #   per-step kernel validation (State + shape checks)
      execute.py         #   Plan -> Confirm -> Execute engine (preview/transaction/repair)
  tests/cad/             # Phase 1 suite (analytically verified)
  tests/agent/           # Phase 2 + 3 suite (tools, loop, MCP, plan-confirm-execute)
  scripts/               # runnable demos
  var/                   # local runtime artifacts (gitignored)
docs/                    # proposal, supervisor deck, literature review
```

---

## Roadmap (40-day plan)

| Days   | Phase                | Status |
| ------ | -------------------- | ------ |
| 1–4    | **Foundation** — FreeCAD scripting, atomic + parametric ops, tests | ✅ done |
| 5–10   | **MCP CAD core** — 23 tools, MCP server, ReAct agent loop on live geometry | ✅ done |
| 11–16  | **Plan → Confirm → Execute** — constrained-JSON plan, preview, per-step validation, bounded repair, single-undo | ✅ done |
| 17–21  | File ingest (STEP/STL exact; PDF assistive) + edit-by-description | next |
| 22–25  | Simulation tools (FEA + thermal) via CalculiX | |
| 26–30  | Toolroom (sandboxed self-extension) + reflective memory | |
| 31–34  | Intent router + privacy / offline verification | |
| 35–40  | Evaluation, ablation, demo, write-up | |

---

## Privacy

Local by default. Cloud escalation is **off** (`AGENTIC_CAD_ALLOW_CLOUD=0`) and,
when enabled, must show exactly what would be sent. The evaluation is designed to
run with networking disabled to *prove* — not promise — the privacy claim.
