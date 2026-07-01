from __future__ import annotations
import json
import anyio
import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server
from agentic_cad.tools.cad_tools import registry

server: Server = Server("agentic-cad-cad")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [types.Tool(name=t.name, description=t.description, inputSchema=t.json_schema()) for t in registry.all()]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    try:
        result = registry.run(name, arguments)
        payload = {"ok": True, "result": result}
    except Exception as exc:  # surface errors to the agent as tool output, not crashes
        payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return [types.TextContent(type="text", text=json.dumps(payload, default=str))]


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    anyio.run(main)
