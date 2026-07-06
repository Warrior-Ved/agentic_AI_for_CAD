from __future__ import annotations
from typing import Callable
from pydantic import BaseModel, Field
from agentic_cad import config

# A clarify chat function takes (messages, json_schema) -> the model's JSON text.
ClarifyChatFn = Callable[[list[dict], dict], str]


class ClarQuestion(BaseModel):
    id: str = Field(..., description="short snake_case key, e.g. hole_position")
    question: str = Field(..., description="the question to ask the user, in plain language")
    why: str = Field("", description="one short line on why this changes the geometry")
    options: list[str] = Field(default_factory=list, description="2-4 concrete example answers the user can pick from")
    suggested: str = Field("", description="the most sensible default answer")


class Clarification(BaseModel):
    needs_clarification: bool = Field(..., description="true if any geometry-critical detail is ambiguous")
    questions: list[ClarQuestion] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list,
                                   description="assumptions that will be made for anything not asked about")


CLARIFY_SYSTEM = """\
You are a CAD requirements analyst. A user has described a part in natural \
language. Before any geometry is built, find the details that are AMBIGUOUS and \
would materially change the 3-D shape, and turn them into a short list of \
clarifying questions.

Ask ONLY about things that change the geometry, such as:
- WHERE a feature sits (which face, centred vs offset, exact position).
- Whether a hole/pocket is a THROUGH hole or a BLIND hole, and if blind, its depth.
- The AXIS / direction a hole or feature runs along.
- Missing dimensions that have no obvious default.
- Counts and spacing (e.g. how many holes, pitch) when a number is implied but unstated.

Rules:
- Do NOT ask about anything already stated explicitly (e.g. if a size is given, don't ask for it).
- Do NOT ask cosmetic or non-geometric questions (colour, material, units — assume millimetres).
- Ask at most 4 questions; fewer is better. Order them most-important first.
- For every question give a SUGGESTED default (the choice a competent engineer \
would make) and 2-4 concrete example options.
- If the request is already unambiguous enough to build, set needs_clarification \
to false and return an empty questions list.
- Also list, in "assumptions", the sensible defaults you would apply for details \
you did NOT ask about.

Return ONLY the JSON object matching the schema."""


def make_clarify_fn(model: str = config.MODEL_PLANNER, host: str = config.OLLAMA_HOST) -> ClarifyChatFn:
    """Ollama-backed clarify chat function (schema-constrained, temperature 0)."""
    import ollama

    client = ollama.Client(host=host)

    def chat_fn(messages: list[dict], schema: dict) -> str:
        resp = client.chat(
            model=model, messages=messages, format=schema, options={"temperature": 0}
        )
        return resp["message"]["content"]

    return chat_fn


def make_clarification(instruction: str, *, chat_fn: ClarifyChatFn | None = None, model: str = config.MODEL_PLANNER, 
                       max_questions: int = 4) -> Clarification:
    """Ask the model which geometry-critical details are ambiguous.
    Never raises for content reasons: if the model returns something unparseable
    we degrade gracefully to "no clarification needed" so the pipeline still runs.
    """
    chat_fn = chat_fn or make_clarify_fn(model)
    schema = Clarification.model_json_schema()
    messages = [
        {"role": "system", "content": CLARIFY_SYSTEM},
        {"role": "user", "content": instruction},
    ]
    try:
        raw = chat_fn(messages, schema)
        clar = Clarification.model_validate_json(raw)
    except Exception:
        return Clarification(needs_clarification=False, questions=[], assumptions=[])

    # Trim to the cap and keep the invariant between the flag and the list honest.
    clar.questions = clar.questions[:max_questions]
    clar.needs_clarification = bool(clar.questions)
    return clar


def answers_to_context(clar: Clarification, answers: dict[str, str], *, instruction: str | None = None) -> str:
    """Fold the user's answers (and unanswered defaults) into a spec block that
    grounds the planner. ``answers`` maps question id -> the user's answer text;
    a missing/blank answer falls back to that question's suggested default."""
    lines: list[str] = ["Resolved specification (treat as authoritative):"]
    if instruction:
        lines.append(f"- Original request: {instruction}")
    for q in clar.questions:
        ans = (answers.get(q.id) or "").strip() or q.suggested or "(use a sensible default)"
        lines.append(f"- {q.question} -> {ans}")
    for a in clar.assumptions:
        lines.append(f"- Assumption: {a}")
    return "\n".join(lines)


# Console driver (for the CLI demo)
def prompt_answers_console(clar: Clarification) -> dict[str, str]:
    """Ask each question at the terminal; empty input accepts the suggested default."""
    answers: dict[str, str] = {}
    print("\nI need to clarify a few things before building:\n")
    for i, q in enumerate(clar.questions, 1):
        print(f"  {i}. {q.question}")
        if q.why:
            print(f"     ({q.why})")
        if q.options:
            print(f"     options: {', '.join(q.options)}")
        default = q.suggested or "(none)"
        raw = input(f"     your answer [default: {default}]: ").strip()
        answers[q.id] = raw or q.suggested
    print()
    return answers
