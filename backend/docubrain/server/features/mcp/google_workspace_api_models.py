"""Pydantic request/response models for Google Workspace MCP API endpoints.

These models define the strict API contract. No raw Google payload fields
are exposed. Truncation metadata is always included when truncation occurs.
"""

from typing import Any

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator

from docubrain.mcp.response_limits import MAX_QUERY_LENGTH
from docubrain.mcp.response_limits import validate_limit


class GoogleWorkspaceSearchAPIRequest(BaseModel):
    """Validated search request for Gmail or Drive."""

    query: str
    limit: int = 10
    page_token: str | None = None

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("query must be non-empty")
        if len(stripped) > MAX_QUERY_LENGTH:
            raise ValueError(
                f"query exceeds maximum length of {MAX_QUERY_LENGTH} characters"
            )
        return stripped

    @field_validator("limit")
    @classmethod
    def bound_limit(cls, value: int) -> int:
        return validate_limit(value)


class GoogleWorkspaceTruncationMetadata(BaseModel):
    """Metadata about response truncation."""

    model_config = ConfigDict(frozen=True)

    body_text_truncated: bool = False
    body_text_original_size: int | None = None
    body_text_returned_size: int | None = None
    body_html_truncated: bool = False
    body_html_original_size: int | None = None
    body_html_returned_size: int | None = None
    thread_messages_capped: bool = False
    thread_messages_original_count: int | None = None
    thread_messages_returned_count: int | None = None


class GoogleWorkspaceSearchAPIResponse(BaseModel):
    """Normalized search response with pagination and truncation metadata."""

    model_config = ConfigDict(frozen=True)

    provider: str
    results: list[dict[str, Any]] = Field(default_factory=list)
    next_page_token: str | None = None
    request_id: str
    truncation: GoogleWorkspaceTruncationMetadata | None = None


class GoogleWorkspaceMessageAPIResponse(BaseModel):
    """Normalized single message response."""

    model_config = ConfigDict(frozen=True)

    provider: str
    message: dict[str, Any]
    request_id: str
    truncation: GoogleWorkspaceTruncationMetadata | None = None


class GoogleWorkspaceThreadAPIResponse(BaseModel):
    """Normalized thread response with message list."""

    model_config = ConfigDict(frozen=True)

    provider: str
    messages: list[dict[str, Any]] = Field(default_factory=list)
    next_page_token: str | None = None
    request_id: str
    truncation: GoogleWorkspaceTruncationMetadata | None = None


class GoogleWorkspaceCredentialHealthAPIResponse(BaseModel):
    """Credential health check response."""

    model_config = ConfigDict(frozen=True)

    provider: str
    connected: bool
    token_expired: bool
    refresh_available: bool
    scopes_valid: bool
    missing_scopes: list[str] = Field(default_factory=list)
    request_id: str
