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

Geometry rules for boxes and holes (a box spans x:0..L, y:0..W, z:0..H):
- A box is created with its near-bottom corner at its (x, y, z), so it occupies \
[x, x+length] x [y, y+width] x [z, z+height].
- To CENTRE a hole on the top face of a box of length L and width W, place the \
cutting cylinder at x=L/2, y=W/2 (NOT at a corner).
- A cylinder is created with its base circle centred on its (x, y, z) and its \
axis along +Z, extending upward by its height.
- For a THROUGH hole along Z, make the cutting cylinder longer than the block and \
start it below the block: set the cylinder z = -1 and its height = H + 2, so it \
passes cleanly all the way through. Its radius is (hole diameter)/2.
- For a BLIND hole of depth D from the top face, set the cylinder height = D + 1 \
and z = H - D (so it starts at the surface and stops D deep).
- To cut a hole: add the box, add the cutting cylinder, then boolean_cut with the \
box as base and the cylinder as tool.

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
              model: str = config.MODEL_PLANNER, context: str | None = None, max_retries: int = 2) -> Plan:
    """Produce a registry-valid Plan, retrying with feedback on failure.

    Raises ``ValueError`` if a valid plan cannot be produced after the retry.
    """
    chat_fn = chat_fn or make_ollama_plan_fn(model)
    schema = Plan.model_json_schema()

    system = PLANNER_SYSTEM.format(catalog=tool_catalog(registry))
    user = instruction if not context else f"{instruction}\n\nCurrent model:\n{context}"
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]

    last_error = ""
    for _ in range(max_retries + 1):
        raw = chat_fn(messages, schema)
        try:
            plan = Plan.model_validate_json(raw)
        except Exception as exc:
            last_error = f"plan was not valid JSON for the schema: {exc}"
            messages.append({"role": "user", "content": last_error + " Try again."})
            continue

        problems = plan.validate_against_registry(registry)
        if not problems:
            return plan

        last_error = "the plan had these problems:\n" + "\n".join(problems)
        messages.append({
            "role": "assistant",
            "content": json.dumps(plan.model_dump()),
        })
        messages.append({
            "role": "user",
            "content": last_error + "\nFix them and return the corrected JSON plan.",
        })

    raise ValueError(f"could not produce a valid plan: {last_error}")