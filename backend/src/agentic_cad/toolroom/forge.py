"""The forge: controlled self-extension. Given a capability request the local
coder model writes a new tool module; it must survive the AST gate and the
subprocess sandbox (self-test with analytic geometry assertions) before it is
persisted to the toolroom and registered — with bounded retries that feed the
failure reason back to the model.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Callable

from agentic_cad import config
from agentic_cad.toolroom import store
from agentic_cad.toolroom.sandbox import check_source, run_sandboxed
from agentic_cad.toolroom.templates import CODEGEN_SYSTEM, CODEGEN_USER, CONTRACT
from agentic_cad.tools.registry import ToolRegistry

# A code chat function: (messages) -> the model's raw text reply.
CodeChatFn = Callable[[list[dict]], str]


@dataclass
class ForgeResult:
    ok: bool
    name: str = ""
    description: str = ""
    error: str = ""
    attempts: int = 0
    test_volume: float | None = None
    source: str = ""
    seconds: float = 0.0
    log: list[str] = field(default_factory=list)


def make_ollama_code_fn(model: str = config.MODEL_CODER, host: str = config.OLLAMA_HOST) -> CodeChatFn:
    import ollama

    client = ollama.Client(host=host)

    def chat_fn(messages: list[dict]) -> str:
        resp = client.chat(model=model, messages=messages, options={"temperature": 0})
        return resp["message"]["content"]

    return chat_fn


def extract_code(reply: str) -> str:
    """The fenced python block from a model reply (or the raw reply)."""
    m = re.search(r"```(?:python)?\s*\n(.*?)```", reply, re.DOTALL)
    return (m.group(1) if m else reply).strip() + "\n"


def forge_tool(request: str, registry: ToolRegistry, *, chat_fn: CodeChatFn | None = None,
               model: str = config.MODEL_CODER, max_attempts: int = 3) -> ForgeResult:
    """Generate -> gate -> sandbox -> persist -> register. Never raises for
    content reasons; the result carries the failure story instead."""
    t0 = time.time()
    result = ForgeResult(ok=False)
    chat_fn = chat_fn or make_ollama_code_fn(model)

    system = CODEGEN_SYSTEM.format(contract=CONTRACT, existing=", ".join(registry.names()))
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": CODEGEN_USER.format(request=request)}]

    candidate = store.TOOLROOM_DIR / "_candidate.py"
    for attempt in range(1, max_attempts + 1):
        result.attempts = attempt
        try:
            source = extract_code(chat_fn(messages))
        except Exception as exc:
            result.error = f"code model unavailable: {exc}"
            break
        result.source = source

        problems = check_source(source)
        if not problems:
            store.TOOLROOM_DIR.mkdir(parents=True, exist_ok=True)
            candidate.write_text(source, encoding="utf-8")
            verdict = run_sandboxed(candidate)
            if verdict.get("ok"):
                name = verdict["tool_name"]
                if name in registry:
                    problems = [f"TOOL_NAME {name!r} already exists — choose a different name"]
                else:
                    store.save(name, source, verdict["description"], verdict.get("volume"))
                    module = store._import_tool_module(store.TOOLROOM_DIR / f"{name}.py")
                    store.register_module(module, registry)
                    candidate.unlink(missing_ok=True)
                    result.ok = True
                    result.name = name
                    result.description = verdict["description"]
                    result.test_volume = verdict.get("volume")
                    result.seconds = round(time.time() - t0, 1)
                    result.log.append(f"attempt {attempt}: accepted")
                    return result
            else:
                problems = [verdict.get("error") or "sandbox rejected the tool"]

        result.error = "; ".join(problems)
        result.log.append(f"attempt {attempt}: {result.error}")
        messages.append({"role": "assistant", "content": f"```python\n{source}```"})
        messages.append({"role": "user", "content":
                         f"That module was rejected: {result.error}\n"
                         "Fix the problem and return the FULL corrected module "
                         "as a single fenced python block."})

    candidate.unlink(missing_ok=True)
    result.seconds = round(time.time() - t0, 1)
    return result
