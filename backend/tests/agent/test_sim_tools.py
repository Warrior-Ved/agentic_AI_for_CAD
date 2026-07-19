"""Simulation tools through the registry + the FastAPI sim endpoints."""
from __future__ import annotations

import time

import pytest

from agentic_cad.tools.cad_tools import registry


def test_sim_tools_registered():
    for tool in ("list_faces", "simulate_static", "simulate_thermal"):
        assert tool in registry.names()


def test_list_faces_and_static_via_registry():
    registry.run("new_document", {"name": "SimDoc"})
    registry.run("add_box", {"length": 60, "width": 15, "height": 8, "name": "Beam"})
    faces = registry.run("list_faces", {})
    assert faces["object"] == "Beam"
    assert len(faces["faces"]) == 6

    out = registry.run("simulate_static",
                       {"fixed_faces": ["Face1"], "load_faces": ["Face2"],
                        "force_n": 200, "direction": "-z", "mesh_size": 6})
    assert out["sim_type"] == "static"
    assert out["max_von_mises_mpa"] > 0
    assert out["safety_factor"] is not None


def test_sim_arg_validation_rejects_bad_input():
    args = registry.get("simulate_static").args_model
    with pytest.raises(Exception):
        args(fixed_faces=["Face1"], load_faces=["Face2"], force_n=100, direction="up")
    targs = registry.get("simulate_thermal").args_model
    with pytest.raises(Exception):
        targs(hot_faces=["Face1"], hot_temp_c=20, cold_faces=["Face2"], cold_temp_c=90)


def test_sim_api_endpoints():
    httpx = pytest.importorskip("httpx")  # noqa: F841 — TestClient needs it
    from fastapi.testclient import TestClient

    from agentic_cad.server import app as server_app

    client = TestClient(server_app.app)

    # build a part in the server's live document
    with server_app.SESSION.lock:
        doc = server_app.SESSION.ensure_doc()
        box = doc.addObject("Part::Box", "Beam")
        box.Length, box.Width, box.Height = 60, 15, 8
        doc.recompute()

    try:
        faces = client.get("/api/sim/faces").json()
        assert faces["object"] == "Beam"
        assert len(faces["faces"]) == 6
        assert "vertices" in faces["faces"][0] and "area_mm2" in faces["faces"][0]

        r = client.post("/api/sim/run", json={
            "sim_type": "static", "material": "steel", "mesh_size": 6,
            "fixed_faces": ["Face1"], "load_faces": ["Face2"],
            "force_n": 200, "direction": "-z",
        })
        assert r.status_code == 200, r.text

        deadline = time.time() + 120
        state = "running"
        while time.time() < deadline:
            st = client.get("/api/sim/status").json()
            state = st["state"]
            if state in ("done", "error"):
                break
            time.sleep(0.5)
        assert state == "done", client.get("/api/sim/status").json()

        result = client.get("/api/sim/result").json()
        n_nodes = len(result["nodes"]) // 3
        assert n_nodes > 0
        assert len(result["fields"]["von_mises"]["values"]) == n_nodes
        assert result["summary"]["max_von_mises_mpa"] > 0
    finally:
        with server_app.SESSION.lock:
            server_app.SESSION.reset()
        server_app.SIM.update(state="idle", error=None, summary=None, result=None)
