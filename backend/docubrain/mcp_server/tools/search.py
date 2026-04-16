"""Search tools for MCP server."""

from datetime import datetime
from typing import Any

import httpx

from docubrain.configs.constants import DocumentSource
from docubrain.mcp_server.api import mcp_server
from docubrain.mcp_server.utils import get_http_client
from docubrain.mcp_server.utils import get_indexed_sources
from docubrain.mcp_server.utils import require_access_token
from docubrain.utils.logger import setup_logger
from docubrain.utils.variable_functionality import build_api_server_url_for_http_requests
from docubrain.utils.variable_functionality import global_version

logger = setup_logger()


def _extract_error_detail(response: httpx.Response) -> str:
    """Extract a human-readable error message from a failed backend response.

    The backend returns DocubrainError responses as
    ``{"error_code": "...", "detail": "..."}``.
    """
    try:
        body = response.json()
        if detail := body.get("detail"):
            return str(detail)
    except Exception:
        pass
    return f"Request failed with status {response.status_code}"


@mcp_server.tool()
async def search_indexed_documents(
    query: str,
    source_types: list[str] | None = None,
    time_cutoff: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """
    Search the user's knowledge base indexed in DocuBrain.
    Use this tool for information that is not public knowledge and specific to the user,
    their team, their work, or their organization/company.

    Note: In CE mode, this tool uses the chat endpoint internally which invokes an LLM
    on every call, consuming tokens and adding latency.
    Additionally, CE callers receive a truncated snippet (blurb) instead of a full document chunk,
    but this should still be sufficient for most use cases. CE mode functionality should be swapped
    when a dedicated CE search endpoint is implemented.

    In EE mode, the dedicated search endpoint is used instead.

    To find a list of available sources, use the `indexed_sources` resource.
    Returns chunks of text as search results with snippets, scores, and metadata.

    Example usage:
    ```
    {
        "query": "What is the latest status of the deployment docs?",
        "source_types": ["google_drive", "github"],
        "time_cutoff": "2025-11-24T00:00:00Z",
        "limit": 10,
    }
    ```
    """
    logger.info(
        f"DocuBrain MCP Server: document search: query='{query}', sources={source_types}, limit={limit}"
    )

    # Parse time_cutoff string to datetime if provided
    time_cutoff_dt: datetime | None = None
    if time_cutoff:
        try:
            time_cutoff_dt = datetime.fromisoformat(time_cutoff.replace("Z", "+00:00"))
        except ValueError as e:
            logger.warning(
                f"DocuBrain MCP Server: Invalid time_cutoff format '{time_cutoff}': {e}. Continuing without time filter."
            )
            # Continue with no time_cutoff instead of returning an error
            time_cutoff_dt = None

    # Initialize source_type_enums early to avoid UnboundLocalError
    source_type_enums: list[DocumentSource] | None = None

    # Get authenticated user from FastMCP's access token
    access_token = require_access_token()

    try:
        sources = await get_indexed_sources(access_token)
    except Exception as e:
        # Error fetching sources (network error, API failure, etc.)
        logger.error(
            "DocuBrain MCP Server: Error checking indexed sources: %s",
            e,
            exc_info=True,
        )
        return {
            "documents": [],
            "total_results": 0,
            "query": query,
            "error": (f"Failed to check indexed sources: {str(e)}. "),
        }

    if not sources:
        logger.info("DocuBrain MCP Server: No indexed sources available for tenant")
        return {
            "documents": [],
            "total_results": 0,
            "query": query,
            "message": (
                "No document sources are indexed yet. Add connectors or upload data "
                "through DocuBrain before calling docubrain_search_documents."
            ),
        }

    # Convert source_types strings to DocumentSource enums if provided
    # Invalid values will be handled by the API server
    if source_types is not None:
        source_type_enums = []
        for src in source_types:
            try:
                source_type_enums.append(DocumentSource(src.lower()))
            except ValueError:
                logger.warning(
                    f"DocuBrain MCP Server: Invalid source type '{src}' - will be ignored by server"
                )

    # Build filters dict only with non-None values
    filters: dict[str, Any] | None = None
    if source_type_enums or time_cutoff_dt:
        filters = {}
        if source_type_enums:
            filters["source_type"] = [src.value for src in source_type_enums]
        if time_cutoff_dt:
            filters["time_cutoff"] = time_cutoff_dt.isoformat()

    is_ee = global_version.is_ee_version()
    base_url = build_api_server_url_for_http_requests(respect_env_override_if_set=True)
    auth_headers = {"Authorization": f"Bearer {access_token.token}"}

    search_request: dict[str, Any]
    if is_ee:
        # EE: use the dedicated search endpoint (no LLM invocation)
        search_request = {
            "search_query": query,
            "filters": filters,
            "num_docs_fed_to_llm_selection": limit,
            "run_query_expansion": False,
            "include_content": True,
            "stream": False,
        }
        endpoint = f"{base_url}/search/send-search-message"
        error_key = "error"
        docs_key = "search_docs"
        content_field = "content"
    else:
        # CE: fall back to the chat endpoint (invokes LLM, consumes tokens)
        search_request = {
            "message": query,
            "stream": False,
            "chat_session_info": {},
        }
        if filters:
            search_request["internal_search_filters"] = filters
        endpoint = f"{base_url}/chat/send-chat-message"
        error_key = "error_msg"
        docs_key = "top_documents"
        content_field = "blurb"

    try:
        response = await get_http_client().post(
            endpoint,
            json=search_request,
            headers=auth_headers,
        )
        if not response.is_success:
            error_detail = _extract_error_detail(response)
            return {
                "documents": [],
                "total_results": 0,
                "query": query,
                "error": error_detail,
            }
        result = response.json()

        # Check for error in response
        if result.get(error_key):
            return {
                "documents": [],
                "total_results": 0,
                "query": query,
                "error": result.get(error_key),
            }

        documents = [
            {
                "semantic_identifier": doc.get("semantic_identifier"),
                "content": doc.get(content_field),
                "source_type": doc.get("source_type"),
                "link": doc.get("link"),
                "score": doc.get("score"),
            }
            for doc in result.get(docs_key, [])
        ]

        # NOTE: search depth is controlled by the backend persona defaults, not `limit`.
        # `limit` only caps the returned list; fewer results may be returned if the
        # backend retrieves fewer documents than requested.
        documents = documents[:limit]

        logger.info(
            f"DocuBrain MCP Server: Internal search returned {len(documents)} results"
        )
        return {
            "documents": documents,
            "total_results": len(documents),
            "query": query,
        }
    except Exception as e:
        logger.error(f"DocuBrain MCP Server: Document search error: {e}", exc_info=True)
        return {
            "error": f"Document search failed: {str(e)}",
            "documents": [],
            "query": query,
        }
