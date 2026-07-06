from __future__ import annotations
import os
import sys
from agentic_cad import config

_INITIALIZED = False


def ensure_freecad_importable() -> None:
    """Configure ``sys.path`` + DLL search dirs so ``import FreeCAD`` works. 
       Raises a clear error if FreeCAD cannot be located, so callers fail fast with an actionable message."""
    global _INITIALIZED
    if _INITIALIZED:
        return

    home = config.FREECAD_HOME
    if home is None or not home.exists():
        raise RuntimeError(
            "FreeCAD installation not found. Set the FREECAD_HOME environment "
            "variable to your FreeCAD root (the folder containing bin/ and lib/)."
            )

    bin_dir = home / "bin"
    lib_dir = home / "lib"
    # bin/Lib/site-packages holds FreeCAD's bundled pure-Python deps (numpy,
    # needed by the FEM stack). Appended AFTER the venv paths so venv packages win.
    site_dir = bin_dir / "Lib" / "site-packages"
    for path in (str(bin_dir), str(lib_dir), str(site_dir)):
        if path not in sys.path:
            sys.path.append(path)

    if hasattr(os, "add_dll_directory") and bin_dir.is_dir():
        try:
            os.add_dll_directory(str(bin_dir))
        except OSError:
            pass

    _INITIALIZED = True


def import_freecad():
    """Convenience: ensure the path is set up and return the ``FreeCAD`` module."""
    ensure_freecad_importable()
    import FreeCAD  

    return FreeCAD
