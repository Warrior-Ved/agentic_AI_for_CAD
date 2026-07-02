"""Launch the Agentic CAD web app (FastAPI + the browser frontend).

    .venv/Scripts/python.exe scripts/serve.py
    .venv/Scripts/python.exe scripts/serve.py --port 8000

Then open http://127.0.0.1:8000 in a browser. FreeCAD is imported in-process and
Ollama must be running locally (see the health banner in the UI). The server is
single-worker on purpose: the FreeCAD kernel is stateful and single-threaded.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the Agentic CAD web app")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="auto-reload on code changes (dev)")
    args = parser.parse_args()

    import uvicorn

    print(f"Agentic CAD -> http://{args.host}:{args.port}")
    uvicorn.run("agentic_cad.server.app:app", host=args.host, port=args.port,
                reload=args.reload, workers=1, log_level="info")


if __name__ == "__main__":
    main()
