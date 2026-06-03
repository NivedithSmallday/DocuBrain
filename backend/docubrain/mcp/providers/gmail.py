from typing import Any

from docubrain.error_handling.error_codes import DocubrainErrorCode
from docubrain.error_handling.exceptions import DocubrainError
from docubrain.mcp.providers.base import BaseWorkspaceProvider
from docubrain.mcp.schemas import ProviderCapability
from docubrain.mcp.schemas import ToolExecutionPreview
from docubrain.mcp.schemas import ToolExecutionRequest
from docubrain.mcp.schemas import LiveRetrievalResult


class GmailProvider(BaseWorkspaceProvider):
    provider_id = "gmail"
    provider_name = "Gmail"
    capabilities = {
        ProviderCapability.SEARCH_EMAILS,
        ProviderCapability.CREATE_DRAFT,
    }

    async def search_emails(
        self,
        query: str,
        *,
        limit: int = 10,
    ) -> list[LiveRetrievalResult]:
        records = await self._execute_client_tool(
            ProviderCapability.SEARCH_EMAILS,
            {"query": query, "limit": limit},
        )
        return self._normalize_client_records(
            records,
            default_title="Untitled Gmail result",
        )

    def preview_tool_execution(
        self,
        request: ToolExecutionRequest,
    ) -> ToolExecutionPreview:
        if request.capability is not ProviderCapability.CREATE_DRAFT:
            return super().preview_tool_execution(request)

        preview = _draft_preview_from_arguments(request.arguments)
        return ToolExecutionPreview(
            provider=self.provider_id,
            capability=ProviderCapability.CREATE_DRAFT,
            preview=preview,
            requires_approval=True,
            executed=False,
        )


def _draft_preview_from_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    to = arguments.get("to")
    subject = arguments.get("subject")
    body = arguments.get("body")

    if not isinstance(to, list) or not all(isinstance(item, str) for item in to):
        raise DocubrainError(
            DocubrainErrorCode.INVALID_INPUT,
            "Gmail draft preview requires a list of recipient email addresses",
        )
    if not isinstance(subject, str) or not subject.strip():
        raise DocubrainError(
            DocubrainErrorCode.INVALID_INPUT,
            "Gmail draft preview requires a subject",
        )
    if not isinstance(body, str) or not body.strip():
        raise DocubrainError(
            DocubrainErrorCode.INVALID_INPUT,
            "Gmail draft preview requires a body",
        )

    return {
        "to": to,
        "subject": subject,
        "body": body,
    }
