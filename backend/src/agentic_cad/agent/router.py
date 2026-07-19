"""Intent routing: a small, fast local model (llama3.2:3b) classifies each user
message so the right pipeline handles it, instead of treating everything as a
fresh build:

    build    -> clarify -> plan -> confirm -> execute (new geometry)
    edit     -> plan against the CURRENT model (edit-by-description)
    analyze  -> read-only agent loop: inspection + simulation tools
    question -> read-only agent loop: answer about the model / capabilities

Routing is schema-constrained JSON and degrades gracefully: any failure means
"build", which is the previous behaviour.
"""
from __future__ import annotations

from typing import Callable, Literal

from pydantic import BaseModel, Field

from agentic_cad import config
from agentic_cad.tools.registry import ToolRegistry

RouteChatFn = Callable[[list[dict], dict], str]

INTENTS = ("build", "edit", "analyze", "question")


class Route(BaseModel):
    intent: Literal["build", "edit", "analyze", "question"] = Field(
        ..., description="which pipeline should handle the message")
    reason: str = Field("", description="one short line explaining the choice")


ROUTER_SYSTEM = """\
You route user messages for a CAD assistant. Classify the message into exactly
one intent:

- "build": create NEW geometry / a new part from scratch.
- "edit": change something about the EXISTING model (resize, move, add or
  remove a feature on it). Only possible when a model exists.
- "analyze": measure, inspect or physically test the existing model (volume,
  mass, dimensions, stress, strength, load, deflection, temperature, heat).
- "question": anything else — capabilities, how-to, general questions,
  greetings. No geometry is created or changed.

Examples:
- "make a 30 mm cube with a 10 mm hole" -> build
- "actually make the hole 12 mm" -> edit
- "move it up by 5 mm" -> edit
- "what is the volume of the part?" -> analyze
- "will it survive 500 N pressing on the top face?" -> analyze
- "what kinds of parts can you build?" -> question

{model_state}

Return ONLY the JSON object matching the schema."""


def make_ollama_route_fn(model: str = config.MODEL_ROUTER, host: str = config.OLLAMA_HOST) -> RouteChatFn:
    import ollama

    client = ollama.Client(host=host)

    def chat_fn(messages: list[dict], schema: dict) -> str:
        resp = client.chat(model=model, messages=messages, format=schema,
                           options={"temperature": 0})
        return resp["message"]["content"]

    return chat_fn


def route_intent(message: str, *, has_model: bool = False,
                 chat_fn: RouteChatFn | None = None,
                 model: str = config.MODEL_ROUTER) -> Route:
    """Classify a message. Never raises: any failure falls back to "build"
    (or "question" when the message can't touch geometry that doesn't exist)."""
    state = ("A model ALREADY EXISTS in the session." if has_model else
             "NO model exists yet — 'edit' and 'analyze' are not possible; "
             "prefer 'build' or 'question'.")
    try:
        chat_fn = chat_fn or make_ollama_route_fn(model)
        raw = chat_fn([{"role": "system", "content": ROUTER_SYSTEM.format(model_state=state)},
                       {"role": "user", "content": message}],
                      Route.model_json_schema())
        route = Route.model_validate_json(raw)
    except Exception:
        return Route(intent="build", reason="router unavailable — defaulting to build")
    if not has_model and route.intent in ("edit", "analyze"):
        return Route(intent="build", reason=f"no model yet (router said {route.intent})")
    return route


# --------------------------------------------------------------------------- #
# Read-only answer loop (analyze / question intents)
# --------------------------------------------------------------------------- #
# Tools that inspect or simulate but never mutate the live model. The simulate
# tools run in isolated throwaway documents, so they are safe here too.
READ_ONLY_TOOLS = ("get_feature_tree", "describe_object", "mass_properties",
                   "list_faces", "simulate_static", "simulate_thermal")

ANSWER_SYSTEM = """\
You are a CAD assistant answering a question about the current FreeCAD model.
Use the provided READ-ONLY tools to ground your answer in the real geometry:
get_feature_tree first (for object names), then describe/measure/simulate as
needed. You cannot create or modify geometry — if asked to, say the user
should phrase it as a build/edit request instead.
Dimensions are millimetres, volumes mm^3, masses depend on material.
When you have what you need, stop calling tools and answer in 1-3 short
sentences with concrete numbers."""


def read_only_registry(registry: ToolRegistry) -> ToolRegistry:
    """A sub-registry with only inspection/simulation tools."""
    sub = ToolRegistry()
    for name in READ_ONLY_TOOLS:
        if name in registry:
            sub.register(registry.get(name))
    return sub


def answer_question(message: str, registry: ToolRegistry, *, chat_fn=None,
                    model: str = config.MODEL_PLANNER, max_iters: int = 8):
    """Answer an analyze/question message with the read-only agent loop.
    Returns the AgentResult from :func:`agentic_cad.agent.loop.run_agent`."""
    from agentic_cad.agent.loop import run_agent

    return run_agent(message, registry=read_only_registry(registry), chat_fn=chat_fn,
                     model=model, system_prompt=ANSWER_SYSTEM, max_iters=max_iters)
