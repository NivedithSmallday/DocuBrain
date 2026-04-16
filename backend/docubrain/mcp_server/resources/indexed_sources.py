"""Resources that expose metadata for the DocuBrain MCP server."""

from __future__ import annotations

from typing import Any

from docubrain.mcp_server.api import mcp_server
from docubrain.mcp_server.utils import get_indexed_sources
from docubrain.mcp_server.utils import require_access_token
from docubrain.utils.logger import setup_logger

logger = setup_logger()


@mcp_server.resource(
    "resource://indexed_sources",
    name="indexed_sources",
    description=(
        "Enumerate the user's document sources that are currently indexed in DocuBrain."
        "This can be used to discover filters for the `search_indexed_documents` tool."
    ),
    mime_type="application/json",
)
async def indexed_sources_resource() -> dict[str, Any]:
    """Return the list of indexed source types for search filtering."""

    access_token = require_access_token()

    sources = await get_indexed_sources(access_token)

    logger.info(
        "DocuBrain MCP Server: indexed_sources resource returning %s entries",
        len(sources),
    )

    return {
        "indexed_sources": sorted(sources),
    }
