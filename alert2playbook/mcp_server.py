"""MCP server (stdio) exposing the knowledge graph and the full pipeline as tools,
so any MCP client (Claude Code, Claude Desktop, Cursor) can call them.

    claude mcp add alert2playbook -- python -m alert2playbook.mcp_server

`lookup_technique` and `search_techniques` need no API keys. `alert_to_playbook`
needs GROQ_API_KEY.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from . import __version__
from .config import Settings
from .kb import get_kb
from .retrieval import HybridRetriever

server = MCPServer(
    name="alert2playbook",
    version=__version__,
    instructions=(
        "MITRE ATT&CK (Enterprise + ICS) and D3FEND knowledge graph tools, plus a multi-agent pipeline "
        "that turns a security alert into a grounded incident response playbook."
    ),
)


@server.tool()
def lookup_technique(technique_id: str) -> dict:
    """Return an ATT&CK technique with its tactics, mitigations, detection strategies
    (analytics and log sources) and D3FEND countermeasures. Revoked IDs resolve to
    their replacement. Example IDs: T1490, T1053.005, T0836."""
    n = get_kb().neighborhood(technique_id)
    if n is None:
        return {"error": f"{technique_id} is not an active ATT&CK Enterprise or ICS technique"}
    return n


@server.tool()
def search_techniques(query: str, k: int = 8) -> list[dict]:
    """Find ATT&CK techniques matching a behaviour description or log fragment,
    using hybrid retrieval (Pinecone dense + BM25 when Pinecone is configured)."""
    kb = get_kb()
    retriever = HybridRetriever(kb, Settings.from_env())
    return [
        {"id": c.id, "name": c.name, "tactics": c.tactics, "matched_by": c.sources}
        for c in retriever.search([query], k=max(1, min(k, 20)))
    ]


@server.tool()
def alert_to_playbook(alert: str) -> str:
    """Run the full multi-agent pipeline on a raw security alert and return the
    reviewed response playbook as Markdown (ATT&CK mapping, NIST SP 800-61 phases,
    hunt leads, with every reference linked)."""
    from .graph import new_state, stream_run
    from .render import playbook_markdown
    from .service import get_service

    service = get_service()
    final = None
    for _, _, merged in stream_run(service.graph, new_state(alert[: service.settings.max_alert_chars]), source="mcp"):
        final = merged
    return playbook_markdown(final, service.kb)


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
