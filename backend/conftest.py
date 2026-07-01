"""Shared pytest fixtures for the CAD test suite.

Tests run in the project venv (Python 3.11), which can import FreeCAD in-process
via agentic_cad.cad.bootstrap. ``pythonpath = ["src"]`` in pyproject.toml makes
the ``agentic_cad`` package importable.
"""
from __future__ import annotations

import pytest

from agentic_cad.cad import bootstrap

bootstrap.ensure_freecad_importable()
import FreeCAD as App  # noqa: E402


@pytest.fixture
def doc():
    """A fresh, isolated FreeCAD document, closed automatically after the test."""
    document = App.newDocument("test")
    try:
        yield document
    finally:
        App.closeDocument(document.Name)
