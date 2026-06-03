from collections.abc import Sequence

from docubrain.mcp.providers.base import BaseWorkspaceProvider
from docubrain.mcp.schemas import ProviderCapability


EMAIL_TERMS = (
    "email",
    "emails",
    "mail",
    "gmail",
    "inbox",
    "sent",
    "received",
    "reply",
    "replied",
)

DRIVE_TERMS = (
    "drive",
    "doc",
    "docs",
    "document",
    "documents",
    "file",
    "files",
    "proposal",
    "spreadsheet",
    "presentation",
)

DRAFT_TERMS = (
    "draft",
    "draft a reply",
    "write a reply",
    "compose a reply",
    "create a reply",
)


class GoogleWorkspaceRouter:
    def __init__(self, providers: Sequence[BaseWorkspaceProvider]) -> None:
        self._providers = list(providers)

    def infer_capabilities(self, query: str) -> list[ProviderCapability]:
        normalized_query = query.lower()

        if any(term in normalized_query for term in DRAFT_TERMS):
            return [ProviderCapability.CREATE_DRAFT]
        if any(term in normalized_query for term in EMAIL_TERMS):
            return [ProviderCapability.SEARCH_EMAILS]
        if any(term in normalized_query for term in DRIVE_TERMS):
            return [ProviderCapability.SEARCH_DOCS]

        return []

    def providers_for_capability(
        self,
        capability: ProviderCapability,
    ) -> list[BaseWorkspaceProvider]:
        return [
            provider
            for provider in self._providers
            if capability in provider.capabilities
        ]
