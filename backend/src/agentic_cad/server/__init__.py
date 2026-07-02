"""HTTP server that exposes the Clarify -> Plan -> Confirm -> Execute pipeline to
a browser frontend. Runs in the same Python 3.11 venv as everything else, so
FreeCAD is imported in-process (no extra interpreter)."""
