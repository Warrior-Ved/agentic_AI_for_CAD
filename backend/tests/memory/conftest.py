"""Memory test fixtures: isolated SQLite file + FreeCAD doc cleanup."""
from __future__ import annotations

import pytest

from agentic_cad.cad import bootstrap

bootstrap.ensure_freecad_importable()
import FreeCAD as App  # noqa: E402

from agentic_cad.memory import episodic  # noqa: E402


@pytest.fixture(autouse=True)
def memory_db(tmp_path, monkeypatch):
    """Every test gets its own memory store; var/ is never touched."""
    monkeypatch.setattr(episodic, "DB_PATH", tmp_path / "memory.sqlite")
    yield tmp_path / "memory.sqlite"


@pytest.fixture(autouse=True)
def _clean_documents():
    before = set(App.listDocuments().keys())
    yield
    for name in list(App.listDocuments().keys()):
        if name not in before:
            App.closeDocument(name)
