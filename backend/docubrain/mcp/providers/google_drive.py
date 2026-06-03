from docubrain.mcp.providers.base import BaseWorkspaceProvider
from docubrain.mcp.schemas import LiveRetrievalResult
from docubrain.mcp.schemas import ProviderCapability


class GoogleDriveProvider(BaseWorkspaceProvider):
    provider_id = "drive"
    provider_name = "Google Drive"
    capabilities = {ProviderCapability.SEARCH_DOCS}

    async def search_docs(
        self,
        query: str,
        *,
        limit: int = 10,
    ) -> list[LiveRetrievalResult]:
        records = await self._execute_client_tool(
            ProviderCapability.SEARCH_DOCS,
            {"query": query, "limit": limit},
        )
        return self._normalize_client_records(
            records,
            default_title="Untitled Google Drive result",
        )
