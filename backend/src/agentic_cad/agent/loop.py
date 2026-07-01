from __future__ import annotations
import json
from dataclasses import dataclass, field
from typing import Any, Callable
from agentic_cad import config
from agentic_cad.tools.cad_tools import registry as default_registry
from agentic_cad.tools.registry import ToolRegistry

ChatFn = Callable[[list[dict], list[dict]], dict]

SYSTEM_PROMPT = """\
You are a CAD modelling agent driving FreeCAD through tools. Build exactly what \
the user asks, using millimetres.

Rules:
- Use the provided tools to create and modify geometry; do not invent geometry in text.
- Each creation tool returns the object's actual `name`. Always use that returned \
name in later tool calls (FreeCAD may rename, e.g. Box -> Box001).
- To make a hole, create the negative shape (e.g. a cylinder) then call boolean_cut \
with the solid as base and the negative as tool.
- Call get_feature_tree or describe_object to check the current model when unsure.
- Call ONE tool per step and wait for its result before the next, so you always \
use the real object names returned to you.
- When the model matches the request, stop calling tools and reply with one short \
sentence summarising what you built and its key dimensions.
"""


@dataclass
class AgentResult:
    final_text: str
    steps: list[dict] = field(default_factory=list)
    success: bool = True
    iterations: int = 0

    @property
    def tool_names(self) -> list[str]:
        return [s["tool"] for s in self.steps]


# Response parsing (tolerant of both plain dicts and Ollama's objects)
def _get(obj: Any, key: str, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _scan_json_objects(text: str):
    """Yield every top-level JSON object embedded in free text."""
    decoder = json.JSONDecoder()
    i, n = 0, len(text)
    while i < n:
        if text[i] == "{":
            try:
                obj, end = decoder.raw_decode(text, i)
                yield obj
                i = end
                continue
            except json.JSONDecodeError:
                pass
        i += 1


def _text_tool_calls(content: str, valid_names) -> list[tuple[str, dict]]:
    """Fallback for local models that print tool calls as text instead of using
    the structured tool-calls API. Only accepts objects naming a real tool."""
    calls: list[tuple[str, dict]] = []
    for obj in _scan_json_objects(content):
        if not isinstance(obj, dict):
            continue
        name = obj.get("name")
        if name in valid_names:
            args = obj.get("arguments", obj.get("parameters", {}))
            if isinstance(args, str):
                args = json.loads(args) if args.strip() else {}
            calls.append((name, dict(args) if isinstance(args, dict) else {}))
    return calls


def _extract(response: Any, valid_names=()) -> tuple[str, list[tuple[str, dict]]]:
    message = _get(response, "message", {})
    content = _get(message, "content", "") or ""
    raw_calls = _get(message, "tool_calls", None) or []
    calls: list[tuple[str, dict]] = []
    for call in raw_calls:
        fn = _get(call, "function", {})
        name = _get(fn, "name")
        args = _get(fn, "arguments", {})
        if isinstance(args, str):
            args = json.loads(args) if args.strip() else {}
        calls.append((name, dict(args)))
    # Fallback: some local models emit tool calls as JSON inside the text body.
    if not calls and content:
        calls = _text_tool_calls(content, set(valid_names))
    return content, calls


def _assistant_message(content: str, calls: list[tuple[str, dict]]) -> dict:
    msg: dict[str, Any] = {"role": "assistant", "content": content}
    if calls:
        msg["tool_calls"] = [{"type": "function", "function": {"name": n, "arguments": a}}
                             for n, a in calls]
    return msg


# Default Ollama chat function
def make_ollama_chat_fn(model: str = config.MODEL_PLANNER, host: str = config.OLLAMA_HOST) -> ChatFn:
    import ollama 

    client = ollama.Client(host=host)

    def chat_fn(messages: list[dict], tools: list[dict]) -> dict:
        return client.chat(model=model, messages=messages, tools=tools, options={"temperature": 0})

    return chat_fn


# The loop
def run_agent(instruction: str, *, registry: ToolRegistry | None = None, chat_fn: ChatFn | None = None, 
              model: str = config.MODEL_PLANNER, system_prompt: str = SYSTEM_PROMPT,
              max_iters: int = 16) -> AgentResult:
    registry = registry or default_registry
    chat_fn = chat_fn or make_ollama_chat_fn(model)
    tools = registry.to_ollama_specs()

    messages: list[dict] = [{"role": "system", "content": system_prompt},
                            {"role": "user", "content": instruction}]
    steps: list[dict] = []

    valid_names = registry.names()
    for iteration in range(1, max_iters + 1):
        response = chat_fn(messages, tools)
        content, calls = _extract(response, valid_names)
        messages.append(_assistant_message(content, calls))

        if not calls:
            return AgentResult(final_text=content, steps=steps, success=True, iterations=iteration)

        for name, args in calls:
            result = _run_tool(registry, name, args)
            steps.append({"tool": name, "args": args, "result": result})
            messages.append({"role": "tool", "tool_name": name,
                             "content": json.dumps(result, default=str)})

    return AgentResult(final_text="Stopped: reached max iterations without finishing.",
                       steps=steps, success=False, iterations=max_iters)


def _run_tool(registry: ToolRegistry, name: str, args: dict) -> dict:
    if name not in registry:
        return {"ok": False, "error": f"unknown tool: {name}"}
    try:
        return {"ok": True, "result": registry.run(name, args)}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
