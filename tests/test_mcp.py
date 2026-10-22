"""Starts the MCP server over stdio as a real client would and calls the keyless tools."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


async def _session_calls():
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "alert2playbook.mcp_server"], cwd=str(ROOT), env={"PINECONE_API_KEY": ""}
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {t.name for t in (await session.list_tools()).tools}
            looked_up = await session.call_tool("lookup_technique", {"technique_id": "T1490"})
            searched = await session.call_tool("search_techniques", {"query": "modify PLC setpoint parameters", "k": 5})
            return tools, looked_up, searched


def _payload(result):
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


def test_mcp_tools_over_stdio():
    tools, looked_up, searched = anyio.run(_session_calls)
    assert tools == {"lookup_technique", "search_techniques", "alert_to_playbook"}
    assert _payload(looked_up)["name"] == "Inhibit System Recovery"
    found = json.dumps(_payload(searched))
    assert "T0836" in found  # ICS: Modify Parameter
