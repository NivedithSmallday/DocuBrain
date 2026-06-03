from datetime import datetime
from datetime import timezone
from typing import Any

from docubrain.mcp.clients.base import BaseMCPClient
from docubrain.error_handling.error_codes import DocubrainErrorCode
from docubrain.error_handling.exceptions import DocubrainError
from docubrain.mcp.schemas import LiveRetrievalResult
from docubrain.mcp.schemas import ProviderCapability
from docubrain.mcp.schemas import ToolExecutionPreview
from docubrain.mcp.schemas import ToolExecutionRequest

# Default timezone for retrieval timestamps. Use UTC for server-side records;
# presentation-layer formatting should handle user-local conversion.
_DEFAULT_RETRIEVAL_TZ = timezone.utc


class BaseWorkspaceProvider:
    provider_id: str
    provider_name: str
    capabilities: set[ProviderCapability]

    def __init__(self, client: BaseMCPClient | None = None) -> None:
        self._client = client

    def build_retrieval_result(
        self,
        *,
        title: str,
        content: str,
        source_id: str,
        retrieved_at: datetime | None = None,
        last_modified: datetime | None = None,
        confidence: float = 1.0,
        source_url: str | None = None,
    ) -> LiveRetrievalResult:
        return LiveRetrievalResult(
            title=title,
            content=content,
            provider=self.provider_id,
            source_id=source_id,
            source_url=source_url,
            retrieved_at=retrieved_at or datetime.now(_DEFAULT_RETRIEVAL_TZ),
            last_modified=last_modified,
            confidence=confidence,
        )

    def preview_tool_execution(
        self,
        request: ToolExecutionRequest,
    ) -> ToolExecutionPreview:
        raise DocubrainError(
            DocubrainErrorCode.INVALID_INPUT,
            f"{self.provider_name} does not support preview for {request.capability.value}",
        )

    async def _execute_client_tool(
        self,
        capability: ProviderCapability,
        arguments: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if self._client is None:
            raise DocubrainError(
                DocubrainErrorCode.SERVICE_UNAVAILABLE,
                f"{self.provider_name} live retrieval client is not configured",
            )
        return await self._client.execute_tool(capability, arguments)

    def _normalize_client_records(
        self,
        records: list[dict[str, Any]],
        *,
        default_title: str,
    ) -> list[LiveRetrievalResult]:
        return [
            self.build_retrieval_result(
                title=_string_or_default(record.get("title"), default_title),
                content=_string_or_default(record.get("content"), ""),
                source_id=_string_or_default(record.get("source_id"), ""),
                source_url=_optional_string(record.get("source_url")),
                last_modified=_parse_datetime(record.get("last_modified")),
                confidence=_confidence_or_default(record.get("confidence")),
            )
            for record in records
        ]


def _string_or_default(value: Any, default: str) -> str:
    return value if isinstance(value, str) else default


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _confidence_or_default(value: Any) -> float:
    if isinstance(value, int | float):
        return max(0.0, min(float(value), 1.0))
    return 1.0
