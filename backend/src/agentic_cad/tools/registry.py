from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable
from pydantic import BaseModel


@dataclass
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    func: Callable[[BaseModel], dict]

    def json_schema(self) -> dict:
        """JSON Schema for the arguments (drops pydantic's noisy ``title`` keys)."""
        schema = self.args_model.model_json_schema()
        schema.pop("title", None)
        for prop in schema.get("properties", {}).values():
            prop.pop("title", None)
        return schema

    def to_ollama_spec(self) -> dict:
        """The tool spec shape Ollama's chat API expects."""
        return {"type": "function",
                "function": {"name": self.name, "description": self.description,
                             "parameters": self.json_schema()}}

    def run(self, args: dict[str, Any] | None = None) -> dict:
        """Validate/coerce args, execute, return a JSON-serialisable dict."""
        model = self.args_model(**(args or {}))
        return self.func(model)


@dataclass
class ToolRegistry:
    _tools: dict[str, Tool] = field(default_factory=dict)

    def register(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool name: {tool.name}")
        self._tools[tool.name] = tool
        return tool

    def tool(self, name: str, description: str, args_model: type[BaseModel]):
        """Decorator: register the wrapped function as a tool."""
        def decorator(func: Callable[[BaseModel], dict]):
            self.register(Tool(name, description, args_model, func))
            return func
        return decorator

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"unknown tool: {name}")
        return self._tools[name]

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return list(self._tools)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def to_ollama_specs(self) -> list[dict]:
        return [t.to_ollama_spec() for t in self._tools.values()]

    def run(self, name: str, args: dict[str, Any] | None = None) -> dict:
        return self.get(name).run(args)