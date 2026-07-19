"""Toolroom test fixtures: doc cleanup + an isolated toolroom directory."""
from __future__ import annotations

import pytest

from agentic_cad.cad import bootstrap

bootstrap.ensure_freecad_importable()
import FreeCAD as App  # noqa: E402

from agentic_cad.toolroom import store  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_documents():
    before = set(App.listDocuments().keys())
    yield
    for name in list(App.listDocuments().keys()):
        if name not in before:
            App.closeDocument(name)


@pytest.fixture
def toolroom_dir(tmp_path, monkeypatch):
    """Point the store at a throwaway directory so tests never touch var/."""
    monkeypatch.setattr(store, "TOOLROOM_DIR", tmp_path)
    monkeypatch.setattr(store, "MANIFEST", tmp_path / "manifest.json")
    return tmp_path
