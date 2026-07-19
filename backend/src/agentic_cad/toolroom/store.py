"""Persistence for forged tools: accepted tool modules live in
``var/toolroom/<name>.py`` with a ``manifest.json`` index, and are re-gated and
re-registered into the live registry at every startup — so a tool the agent
forges today is still in its toolbox tomorrow.
"""
from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

from agentic_cad import config
from agentic_cad.toolroom.sandbox import check_source
from agentic_cad.tools.registry import Tool, ToolRegistry

TOOLROOM_DIR = config.VAR_DIR / "toolroom"
MANIFEST = TOOLROOM_DIR / "manifest.json"


def _read_manifest() -> dict:
    if MANIFEST.exists():
        try:
            return json.loads(MANIFEST.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _write_manifest(manifest: dict) -> None:
    TOOLROOM_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def list_tools() -> dict:
    """Manifest of stored tools: {name: {description, created, file, volume}}."""
    return _read_manifest()


def save(name: str, source: str, description: str, volume: float | None = None) -> Path:
    """Persist an accepted tool module and index it in the manifest."""
    TOOLROOM_DIR.mkdir(parents=True, exist_ok=True)
    path = TOOLROOM_DIR / f"{name}.py"
    path.write_text(source, encoding="utf-8")
    manifest = _read_manifest()
    manifest[name] = {"description": description, "file": path.name,
                      "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                      "volume": volume}
    _write_manifest(manifest)
    return path


def remove(name: str) -> bool:
    manifest = _read_manifest()
    entry = manifest.pop(name, None)
    if entry is None:
        return False
    (TOOLROOM_DIR / entry["file"]).unlink(missing_ok=True)
    _write_manifest(manifest)
    return True


def _import_tool_module(path: Path):
    spec = importlib.util.spec_from_file_location(f"forged_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def register_module(module, registry: ToolRegistry) -> str:
    """Wrap a forged module's build() as a registry Tool. Returns the name."""
    def runner(a, _module=module):
        from agentic_cad.tools import cad_tools  # lazy: avoid import cycle

        obj = _module.build(cad_tools._doc(), a)
        return cad_tools._summary(obj)

    registry.register(Tool(module.TOOL_NAME, f"{module.TOOL_DESCRIPTION} (forged tool)",
                           module.Args, runner))
    return module.TOOL_NAME


def load_all(registry: ToolRegistry) -> list[str]:
    """Re-gate + register every stored tool. Bad or colliding entries are
    skipped (never fatal) so a corrupt file can't break the app at startup."""
    loaded: list[str] = []
    for name, entry in _read_manifest().items():
        path = TOOLROOM_DIR / entry["file"]
        try:
            if not path.exists():
                continue
            if name in registry:
                continue
            problems = check_source(path.read_text(encoding="utf-8"))
            if problems:
                continue
            module = _import_tool_module(path)
            if module.TOOL_NAME != name or module.TOOL_NAME in registry:
                continue
            register_module(module, registry)
            loaded.append(name)
        except Exception:
            continue
    return loaded
