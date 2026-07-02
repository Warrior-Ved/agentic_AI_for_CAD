from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable
from agentic_cad.agent.clarify import (
    Clarification, ClarifyChatFn, answers_to_context, make_clarification)
from agentic_cad.agent.plan import Plan, PlanStep
from agentic_cad.agent.planner import make_plan
from agentic_cad.agent.validation import validate_document
from agentic_cad.cad import bootstrap, document, inspect
from agentic_cad.tools.registry import ToolRegistry
bootstrap.ensure_freecad_importable()
import FreeCAD as App  # noqa: E402

# repair_fn(step, error, current_args, registry) -> corrected args or None.
RepairFn = Callable[[PlanStep, str, dict, ToolRegistry], dict | None]
# confirm_fn(plan, preview) -> Decision.
ConfirmFn = Callable[["Plan", "ExecutionResult"], "Decision"]
# clarify_fn(clarification) -> {question_id: answer}. Returning {} accepts the defaults.
ClarifyFn = Callable[["Clarification"], dict]

_DOC_MGMT_TOOLS = {"new_document", "save_document"}

# Result + decision types
@dataclass
class StepResult:
    step: int
    tool: str
    args: dict
    ok: bool
    result: dict | None = None
    error: str | None = None
    repairs: int = 0


@dataclass
class ExecutionResult:
    success: bool
    steps: list[StepResult] = field(default_factory=list)
    summary: dict | None = None
    aborted: bool = False
    message: str = ""

    @property
    def final_volume(self) -> float | None:
        if self.summary and "mass_properties" in self.summary:
            return self.summary["mass_properties"].get("volume_mm3")
        return None


@dataclass
class Decision:
    action: str  # "approve" | "edit" | "reject"
    plan: Plan | None = None
    feedback: str = ""


def approve() -> Decision:
    return Decision("approve")


def edit(plan: Plan) -> Decision:
    return Decision("edit", plan=plan)


def reject(feedback: str) -> Decision:
    return Decision("reject", feedback=feedback)


# Core step engine
def execute_step(step: PlanStep, registry: ToolRegistry, doc, repair_fn, max_repair):
    args = dict(step.args)
    attempts = 0
    result = None
    while True:
        before = {o.Name for o in doc.Objects}
        try:
            result = registry.run(step.tool, args)
            error = None
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"

        if error is None:
            ok, problems = validate_document(doc)
            if ok:
                return StepResult(step.step, step.tool, args, True, result, None, attempts)
            error = "kernel validation failed: " + "; ".join(problems)

        # Roll back anything this attempt added, so a repair starts clean.
        for name in {o.Name for o in doc.Objects} - before:
            try:
                doc.removeObject(name)
            except Exception:
                pass
        doc.recompute()

        if repair_fn is None or attempts >= max_repair:
            return StepResult(step.step, step.tool, args, False, result, error, attempts)

        fixed = repair_fn(step, error, args, registry)
        if not fixed:
            return StepResult(step.step, step.tool, args, False, result, error, attempts)
        args = dict(fixed)
        attempts += 1


def run_steps(plan: Plan, registry: ToolRegistry, doc, *, repair_fn, max_repair, skip: set[str]) -> ExecutionResult:
    App.setActiveDocument(doc.Name)
    results: list[StepResult] = []
    for step in plan.steps:
        if step.tool in skip:
            continue
        sr = execute_step(step, registry, doc, repair_fn, max_repair)
        results.append(sr)
        if not sr.ok:
            return ExecutionResult(False, results, None,
                                   message=f"step {step.step} ({step.tool}) failed: {sr.error}")
    return ExecutionResult(True, results, summarize(doc))


def summarize(doc) -> dict:
    summary = {"objects": [{"name": o["name"], "type": o["type"]} for o in inspect.feature_tree(doc)]}
    final = None
    for obj in reversed(doc.Objects):
        try:
            if obj.Visibility and len(obj.Shape.Solids) >= 1:
                final = obj
                break
        except Exception:
            continue
    if final is not None:
        summary["final_object"] = final.Name
        summary["mass_properties"] = inspect.mass_properties(final)
    return summary


# Preview (throwaway doc) + Execute (live doc, transactional)
def preview_plan(plan: Plan, registry: ToolRegistry) -> ExecutionResult:
    """Run the plan in a throwaway document; the live model is untouched."""
    doc = document.new_document("__preview__")
    try:
        return run_steps(plan, registry, doc, repair_fn=None, max_repair=0, skip=_DOC_MGMT_TOOLS)
    finally:
        App.closeDocument(doc.Name)


def execute_plan(plan: Plan, registry: ToolRegistry, doc, *,
                 repair_fn: RepairFn | None = None, max_repair: int = 2,
                 label: str = "agent action") -> ExecutionResult:
    """Execute into the live doc inside one transaction (one undo), with per-step
    validation and bounded repair. Aborts cleanly if a step can't be repaired."""
    doc.UndoMode = 1
    doc.openTransaction(label)
    res = run_steps(plan, registry, doc, repair_fn=repair_fn, max_repair=max_repair,
                     skip={"new_document"})
    if res.success:
        doc.commitTransaction()
    else:
        doc.abortTransaction()
        doc.recompute()
        res.aborted = True
    return res


# Main Loop: clarify -> plan -> confirm -> execute (with replanning on reject)
def plan_confirm_execute(instruction: str, registry: ToolRegistry, doc, *, confirm_fn: ConfirmFn,
                         planner_chat_fn=None, repair_fn: RepairFn | None = None, model: str | None = None,
                         context: str | None = None, max_rounds: int = 3, max_repair: int = 2,
                         clarify_fn: ClarifyFn | None = None,
                         clarify_chat_fn: ClarifyChatFn | None = None) -> ExecutionResult:
    """Full human-in-the-loop build.

    If ``clarify_fn`` and ``clarify_chat_fn`` are given, the loop first asks the
    model which geometry-critical details are ambiguous; ``clarify_fn`` collects
    the user's answers, which are folded into the planner's context as a resolved
    spec. Then it plans, previews, confirms and executes as before.
    """
    spec = _resolve_spec(instruction, clarify_fn, clarify_chat_fn, model)
    base_context = "\n\n".join(c for c in (spec, context) if c) or None

    feedback: str | None = None
    for _ in range(max_rounds):
        instr = instruction
        if feedback:
            instr = f"{instruction}\n\nUser rejected the previous plan: {feedback}"

        plan_kwargs = {"chat_fn": planner_chat_fn, "context": base_context}
        if model:
            plan_kwargs["model"] = model
        plan = make_plan(instr, registry, **plan_kwargs)

        preview = preview_plan(plan, registry)
        decision = confirm_fn(plan, preview)

        if decision.action in ("approve", "edit"):
            chosen = decision.plan if decision.action == "edit" else plan
            return execute_plan(chosen, registry, doc, repair_fn=repair_fn, max_repair=max_repair,
                                label=instruction[:60])
        if decision.action == "reject":
            feedback = decision.feedback
            continue

    return ExecutionResult(False, message="rejected: reached max planning rounds")


def _resolve_spec(instruction, clarify_fn, clarify_chat_fn, model) -> str | None:
    """Run the clarification sub-step, returning a resolved-spec context block
    (or None if clarification is disabled or nothing was ambiguous)."""
    if not (clarify_fn and clarify_chat_fn):
        return None
    kwargs = {"chat_fn": clarify_chat_fn}
    if model:
        kwargs["model"] = model
    clar = make_clarification(instruction, **kwargs)
    if not clar.needs_clarification:
        return None
    answers = clarify_fn(clar) or {}
    return answers_to_context(clar, answers, instruction=instruction)