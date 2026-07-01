"""FreeCAD-facing CAD operations.

Every module here imports ``FreeCAD`` / ``Part``. That import only works in a
Python 3.11 interpreter that can see FreeCAD's compiled modules. Because the
project venv is Python 3.11, we make it work *in-process* by prepending
FreeCAD's ``bin``/``lib`` to ``sys.path`` (see :mod:`agentic_cad.cad.bootstrap`)
before importing FreeCAD.

Always do this at the top of a CAD module::

    from agentic_cad.cad import bootstrap
    bootstrap.ensure_freecad_importable()
    import FreeCAD as App
    import Part
"""
