from __future__ import annotations
import os
from pathlib import Path

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
# backend/src/agentic_cad/config.py -> project root is three parents up from src
THIS = Path(__file__).resolve()
SRC_DIR = THIS.parents[1]                      # backend/src
BACKEND_DIR = THIS.parents[2]                  # backend
PROJECT_ROOT = THIS.parents[3]                 # repo root

# Local, per-machine runtime state (never leaves the device, per privacy design).
VAR_DIR = Path(os.environ.get("AGENTIC_CAD_VAR", BACKEND_DIR / "var"))
RUNTIME_DOC_DIR = VAR_DIR / "documents"         # working .FCStd files
EXPORT_DIR = VAR_DIR / "exports"                # STEP/STL/IGES outputs
MEMORY_DIR = VAR_DIR / "memory"                 # SQLite episodic log + Chroma
LOG_DIR = VAR_DIR / "logs"


def ensure_dirs() -> None:
    """Create the local runtime directories if they do not yet exist."""
    for d in (VAR_DIR, RUNTIME_DOC_DIR, EXPORT_DIR, MEMORY_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- #
# FreeCAD location (Windows default; override with FREECAD_HOME)
# --------------------------------------------------------------------------- #
def _detect_freecad_home() -> Path | None:
    env = os.environ.get("FREECAD_HOME")
    if env:
        return Path(env)
    candidates = [Path(r"C:\Program Files\FreeCAD 1.0"), Path(r"C:\Program Files\FreeCAD 1.1"),
                  Path(r"C:\Program Files\FreeCAD")]
    for c in candidates:
        if (c / "bin" / "freecadcmd.exe").exists():
            return c
    return None


FREECAD_HOME = _detect_freecad_home()
FREECAD_PYTHON = (FREECAD_HOME / "bin" / "python.exe") if FREECAD_HOME else None
FREECAD_CMD = (FREECAD_HOME / "bin" / "freecadcmd.exe") if FREECAD_HOME else None

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")

MODEL_ROUTER = os.environ.get("AGENTIC_CAD_ROUTER_MODEL", "llama3.2:3b")
MODEL_PLANNER = os.environ.get("AGENTIC_CAD_PLANNER_MODEL", "qwen2.5-coder:7b")
MODEL_CODER = os.environ.get("AGENTIC_CAD_CODER_MODEL", "qwen2.5-coder:7b")
MODEL_VISION = os.environ.get("AGENTIC_CAD_VISION_MODEL", "qwen3-vl:4b")
MODEL_EMBED = os.environ.get(
    "AGENTIC_CAD_EMBED_MODEL", "locusai/all-minilm-l6-v2:latest"
)

# Privacy: cloud escalation is OFF by default and must be explicitly enabled.
ALLOW_CLOUD = os.environ.get("AGENTIC_CAD_ALLOW_CLOUD", "0") == "1"

# --------------------------------------------------------------------------- #
# Geometry defaults
# --------------------------------------------------------------------------- #
DEFAULT_LINEAR_TOL = 1e-6  
STL_LINEAR_DEFLECTION = 0.1  
