from __future__ import annotations
from pathlib import Path
from agentic_cad import config
from agentic_cad.cad import bootstrap
import FreeCAD as FCad 

bootstrap.ensure_freecad_importable()


def new_document(name: str = "Unnamed") -> "FCad.Document":
    """Create and activate a new, empty document."""
    doc = FCad.newDocument(name)
    FCad.setActiveDocument(doc.Name)
    return doc


def active_document() -> "FCad.Document":
    """Return the active document, raising if none is open."""
    doc = FCad.ActiveDocument
    if doc is None:
        raise RuntimeError("No active FreeCAD document. Call new_document() first.")
    return doc


def get_document(name: str | None = None) -> "FCad.Document":
    """Return a document by name, or the active one if name is None."""
    if name is None:
        return active_document()
    doc = FCad.getDocument(name)
    if doc is None:
        raise RuntimeError(f"No document named {name!r}.")
    return doc


def recompute(doc: "FCad.Document | None" = None) -> int:
    """Recompute a document and return the number of objects touched."""
    doc = doc or active_document()
    return doc.recompute()


def save_document(doc: "FCad.Document | None" = None, path: str | Path | None = None) -> Path:
    """Save the document. Defaults to the local runtime document directory."""
    doc = doc or active_document()
    if path is None:
        config.ensure_dirs()
        path = config.RUNTIME_DOC_DIR / f"{doc.Name}.FCStd"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveAs(str(path))
    return path


def open_document(path: str | Path) -> "FCad.Document":
    """Open an existing .FCStd document and make it active."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    doc = FCad.openDocument(str(path))
    FCad.setActiveDocument(doc.Name)
    return doc


def close_document(doc: "FCad.Document | None" = None) -> None:
    """Close a document without saving."""
    doc = doc or active_document()
    FCad.closeDocument(doc.Name)
