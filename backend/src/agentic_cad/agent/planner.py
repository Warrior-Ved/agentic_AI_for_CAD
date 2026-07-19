from __future__ import annotations
import json
from typing import Callable
from agentic_cad import config
from agentic_cad.agent.plan import Plan
from agentic_cad.tools.registry import ToolRegistry

# Plan chat function takes (messages, json_schema) and returns the model's JSON text.
PlanChatFn = Callable[[list[dict], dict], str]

PLANNER_SYSTEM = """\
You are a CAD planning agent. Given a request, output a JSON plan: a numbered \
list of tool calls that build the requested part in FreeCAD, in millimetres.

Requirements:
- Use ONLY the tools listed below, with correct argument names.
- Make every dimension explicit and numeric.
- If a "Resolved specification" is provided, it is AUTHORITATIVE — honour every \
line of it exactly (hole position, through vs blind, depth, axis). Do not \
override it with your own assumptions.
- Give each step a one-line rationale.
- Output ONLY the JSON object, matching the required schema.
- When REVISING after a rejection or a failed preview, change ONLY what the \
feedback asks for — keep every other dimension and detail from the original \
request exactly as it was.

Referring to objects across steps (this is the most common cause of failure):
- A later step can only reference an object that an EARLIER step created.
- Refer to it by the EXACT string you passed as that step's `name` argument — \
same spelling, same capitalisation, and NO spaces (use "HoleTool", never \
"Hole Tool" or "hole"). If a step has no `name`, do not invent one.
- Always give a `name` to any object you will reference later (a sketch you will \
extrude, a solid you will cut or fuse), and reuse that identical string.
- Prefer the fewest steps: if one generator tool (gear, fan, spring) or one \
primitive builds the part, do NOT decompose it into sketches and booleans.

Placement rules (millimetres):
- add_box and add_cylinder accept centered=true, which makes (x, y, z) the \
solid's CENTRE. Without it a box is placed by its min corner and a cylinder by \
its base-circle centre (axis +Z).
- CONVENTION: create the base part with centered=true at the origin (0,0,0). A \
feature "through the centre" then needs a cutter that is ALSO centered=true at \
(0,0,0) — identical centres, no offset arithmetic to get wrong. Worked example, \
"20 mm cube with a 15 mm square hole through the centre":
    1. add_box(length=20, width=20, height=20, name="Base", centered=true)
    2. add_box(length=15, width=15, height=22, name="Cutter", centered=true)
    3. boolean_cut(base="Base", tool="Cutter", name="Part")
  (no translate step is needed — both solids share the origin as their centre)
- Holes, pockets, slots and cut-outs are SUBTRACTION: build a cutter solid in \
the exact SHAPE of the cavity — ROUND hole -> add_cylinder (radius = \
diameter/2); SQUARE or RECTANGULAR hole/slot -> add_box with the hole's side \
lengths; NEVER substitute one shape for the other — then \
boolean_cut(base=main solid, tool=cutter).
- A THROUGH cutter must be 2 mm LONGER than the material it crosses, so it \
protrudes past both faces (e.g. through a 20 mm cube: cutter length 22).
- A BLIND hole of depth D from a face: the cutter starts at that face and \
reaches D deep (top face at z=T: cylinder centered=false, z = T - D, height = D + 1).
- A hole along the X or Y axis: tilt a cylinder cutter with axis_x/axis_y/axis_z \
(e.g. axis_x=1, axis_z=0 goes along +X), or size a box cutter along that axis.
- Prefer primitives + boolean_cut; use new_sketch -> sketch shapes -> extrude \
ONLY for profiles no primitive can make (an extrusion is solid MATERIAL — \
boolean_cut it afterwards to remove material).

Complex multi-surface parts — prefer ONE generator call over many primitive steps:
- Gear: add_involute_gear(module_mm, teeth, thickness, ...). Pitch diameter = \
module_mm * teeth; set helix_angle for a helical gear; bore_diameter for the shaft hole.
- Fan / propeller / impeller: add_fan_rotor(hub_radius, hub_height, blade_count, \
blade_length, chord, ...) builds the hub AND the twisted blades in one step — never \
build blades from boxes.
- Coil spring: add_spring(coil_radius, wire_radius, pitch, turns).
- Body of revolution (vase, pulley, dome, bottle): sketch the half profile, then revolve.
- Blended/tapered sections: 2+ sketches at different heights (use the sketch z offset), \
then loft with their sketch names in order.
- Helical/threaded shapes: add_helix for the path, a closed profile sketch, then sweep.
- Features repeated around an axis (bolt circles, spokes): build one, then polar_array.

Simulation (only when the user asks to analyse/test a part that already exists):
- Call list_faces first to see face names, areas, centres and normals, then pick faces \
by their geometry (e.g. the face with normal [0,0,1] is the top).
- Strength/deflection: simulate_static(fixed_faces, load_faces, force_n, direction).
- Heat flow: simulate_thermal(hot_faces, hot_temp_c, cold_faces, cold_temp_c).

Available tools:
{catalog}
"""


def tool_catalog(registry: ToolRegistry) -> str:
    lines = []
    for tool in registry.all():
        fields = tool.args_model.model_fields
        arg_names = ", ".join(fields.keys())
        lines.append(f"- {tool.name}({arg_names}): {tool.description}")
    return "\n".join(lines)


def make_ollama_plan_fn(model: str = config.MODEL_PLANNER, host: str = config.OLLAMA_HOST) -> PlanChatFn:
    import ollama 

    client = ollama.Client(host=host)

    def chat_fn(messages: list[dict], schema: dict) -> str:
        resp = client.chat(model=model, messages=messages, format=schema,
                           options={"temperature": 0})
        return resp["message"]["content"]

    return chat_fn


def make_ollama_repair_fn(model: str = config.MODEL_PLANNER, host: str = config.OLLAMA_HOST):
    """A repair function (for the execute loop) that asks the model to fix the
    arguments of a single failing step, constrained to that tool's schema."""
    import ollama  # noqa: PLC0415

    client = ollama.Client(host=host)

    def repair_fn(step, error, args, registry):
        tool = registry.get(step.tool)
        schema = tool.args_model.model_json_schema()
        messages = [
            {"role": "system", "content": (
                "You fix ONE failing CAD tool call. Return only corrected JSON "
                "arguments that satisfy the schema and resolve the error.")},
            {"role": "user", "content": (
                f"Tool: {step.tool}\nDescription: {tool.description}\n"
                f"Failed arguments: {json.dumps(args)}\nError: {error}\n"
                "Return corrected arguments as JSON.")},
        ]
        try:
            raw = client.chat(model=model, messages=messages, format=schema,
                             options={"temperature": 0})["message"]["content"]
            fixed = json.loads(raw)
            return fixed if isinstance(fixed, dict) else None
        except Exception:
            return None

    return repair_fn


def make_plan(instruction: str, registry: ToolRegistry, *, chat_fn: PlanChatFn | None = None,
              model: str = config.MODEL_PLANNER, context: str | None = None, max_retries: int = 2,
              verify_fn=None, trace: list | None = None,
              revise_of: Plan | None = None, feedback: str | None = None) -> Plan:
    """Produce a registry-valid Plan, retrying with feedback on failure.

    ``verify_fn(plan) -> list[str]`` can add deeper checks (dry-run previews,
    semantic cross-checks); its problems are fed back INSIDE this conversation,
    so the model sees its own previous plan next to the complaint — far more
    effective than restarting with a longer instruction.

    ``revise_of`` + ``feedback`` put the planner in REVISION mode: the plan being
    revised is seeded into the conversation as the model's own previous answer and
    the feedback as the only requested change. The model edits THAT plan — keeping
    every earlier change baked into it — instead of re-deriving from the original
    request and silently dropping accumulated edits.

    ``trace`` (a list) collects human-readable events about every draft — what
    was rejected and why — so a UI can show the agent's thought process.

    Raises ``ValueError`` if a valid plan cannot be produced after the retries.
    """
    chat_fn = chat_fn or make_ollama_plan_fn(model)
    schema = Plan.model_json_schema()

    def note(stage: str, text: str) -> None:
        if trace is not None:
            trace.append({"stage": stage, "text": text})

    system = PLANNER_SYSTEM.format(catalog=tool_catalog(registry))
    user = instruction if not context else f"{instruction}\n\nCurrent model:\n{context}"
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]

    # Revision mode: hand the model its own previous plan and ask for ONLY the
    # requested change. The prior plan already carries every earlier edit, so
    # this is how incremental edits accumulate instead of reverting to defaults.
    if revise_of is not None and feedback:
        messages.append({"role": "assistant", "content": json.dumps(revise_of.model_dump())})
        messages.append({"role": "user", "content": (
            f"Apply ONLY this change to the plan above: {feedback}\n"
            "This change takes priority over any earlier specification if they "
            "conflict. Keep every other step, dimension, object name and detail "
            "EXACTLY as in the plan above — do not revert anything to a default. "
            "Return the full corrected JSON plan.")})
        note("planner", f"revising the previous {len(revise_of.steps)}-step plan — "
                        f"applying only: {feedback}")

    last_error = ""
    for attempt in range(1, max_retries + 2):
        raw = chat_fn(messages, schema)
        try:
            plan = Plan.model_validate_json(raw)
        except Exception as exc:
            last_error = f"plan was not valid JSON for the schema: {exc}"
            note("planner", f"draft {attempt} rejected: not valid JSON for the plan schema")
            messages.append({"role": "user", "content": last_error + " Try again."})
            continue

        note("planner", f"draft {attempt}: {len(plan.steps)} step(s) — "
                        + ", ".join(s.tool for s in plan.steps))
        problems = plan.validate_against_registry(registry)
        if problems:
            note("planner", f"draft {attempt} rejected by schema validation: "
                            + "; ".join(problems))
        elif verify_fn is not None:
            problems = list(verify_fn(plan) or [])
            if problems:
                note("planner", f"draft {attempt} rejected by verification: "
                                + "; ".join(problems))
        if not problems:
            note("planner", f"draft {attempt} accepted")
            return plan

        last_error = "the plan had these problems:\n" + "\n".join(problems)
        messages.append({
            "role": "assistant",
            "content": json.dumps(plan.model_dump()),
        })
        messages.append({
            "role": "user",
            "content": last_error + "\nFix EXACTLY these problems and return the corrected "
                       "JSON plan, keeping everything else (especially dimensions) unchanged.",
        })

    raise ValueError(f"could not produce a valid plan: {last_error}")