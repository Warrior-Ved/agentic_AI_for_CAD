"""Fixtures for agent/tool tests. Auto-closes any documents a test creates so
the active-document state never leaks between tests."""
from __future__ import annotations

import pytest

from agentic_cad.cad import bootstrap

bootstrap.ensure_freecad_importable()
import FreeCAD as App  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_documents():
    before = set(App.listDocuments().keys())
    yield
    for name in list(App.listDocuments().keys()):
        if name not in before:
            App.closeDocument(name)
