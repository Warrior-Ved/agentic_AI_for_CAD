"""End-to-end MCP test: spawn the CAD server as a subprocess and drive it with a
real MCP client over stdio. Proves the protocol surface actually works."""
from __future__ import annotations

import json
import os
import sys

import anyio

from agentic_cad import config


def _server_params():
    from mcp import StdioServerParameters

    env = os.environ.copy()
    env["PYTHONPATH"] = str(config.SRC_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "agentic_cad.mcp_servers.cad_server"],
        env=env,
    )


async def _roundtrip():
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    async with stdio_client(_server_params()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            names = [t.name for t in tools]

            await session.call_tool("new_document", {"name": "MCP"})
            res = await session.call_tool(
                "add_box", {"length": 40, "width": 20, "height": 10})
            payload = json.loads(res.content[0].text)
            return names, payload


def test_mcp_server_lists_and_calls_tools():
    names, payload = anyio.run(_roundtrip)

    assert len(names) >= 20
    assert "add_box" in names and "boolean_cut" in names and "get_feature_tree" in names

    assert payload["ok"] is True
    assert payload["result"]["volume_mm3"] == 8000
