from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field
from agentic_cad.tools.registry import ToolRegistry


class PlanStep(BaseModel):
    step: int = Field(..., description="1-based step number")
    tool: str = Field(..., description="Name of the tool to call")
    args: dict[str, Any] = Field(default_factory=dict, description="Tool arguments")
    rationale: str = Field("", description="One short line on why this step exists")


class Plan(BaseModel):
    summary: str = Field(..., description="One sentence describing the whole part")
    steps: list[PlanStep] = Field(default_factory=list)

    def validate_against_registry(self, registry: ToolRegistry) -> list[str]:
        """Return a list of human-readable problems; empty means executable."""
        problems: list[str] = []
        if not self.steps:
            problems.append("plan has no steps")
        for s in self.steps:
            if s.tool not in registry:
                problems.append(f"step {s.step}: unknown tool {s.tool!r}")
                continue
            try:
                registry.get(s.tool).args_model(**s.args)
            except Exception as exc:
                problems.append(f"step {s.step} ({s.tool}): bad args — {exc}")
        return problems

    def render(self) -> str:
        """A compact, human-readable rendering for the confirm step / logs."""
        lines = [f"Plan: {self.summary}"]
        for s in self.steps:
            arg_str = ", ".join(f"{k}={v}" for k, v in s.args.items())
            line = f"  {s.step}. {s.tool}({arg_str})"
            if s.rationale:
                line += f"  — {s.rationale}"
            lines.append(line)
        return "\n".join(lines)