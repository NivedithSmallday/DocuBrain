from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator


class ProviderCapability(str, Enum):
    SEARCH_EMAILS = "search_emails"
    SEARCH_DOCS = "search_docs"
    GET_DRIVE_FILE_METADATA = "get_drive_file_metadata"
    READ_GOOGLE_DOC = "read_google_doc"
    EXPORT_GOOGLE_DOC = "export_google_doc"
    LIST_SHARED_DRIVES = "list_shared_drives"
    SEARCH_DRIVE_CONTENT = "search_drive_content"
    LIST_RECENT_FILES = "list_recent_files"
    GET_FILE_PERMISSIONS = "get_file_permissions"
    LIST_FOLDER_CONTENTS = "list_folder_contents"
    CREATE_DRAFT = "create_draft"


class GmailRetrievalCapability(str, Enum):
    GET_GMAIL_MESSAGE = "get_gmail_message"
    GET_GMAIL_THREAD = "get_gmail_thread"
    LIST_GMAIL_THREADS = "list_gmail_threads"
    SUMMARIZE_EMAIL_THREAD = "summarize_email_thread"
    SEARCH_GMAIL_ATTACHMENTS = "search_gmail_attachments"


class RetrievalSourceType(str, Enum):
    INDEXED = "indexed"
    LIVE_WORKSPACE = "live_workspace"


class LiveRetrievalResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: str
    content: str
    source_type: RetrievalSourceType = RetrievalSourceType.LIVE_WORKSPACE
    retrieved_at: datetime
    last_modified: datetime | None = None
    provider: str
    confidence: float = Field(ge=0.0, le=1.0)
    source_id: str
    source_url: str | None = None


class ToolExecutionRequest(BaseModel):
    capability: ProviderCapability
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolExecutionPreview(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    capability: ProviderCapability
    preview: dict[str, Any]
    requires_approval: bool = True
    executed: bool = False


class SanitizedContent(BaseModel):
    model_config = ConfigDict(frozen=True)

    content: str
    is_suspicious: bool
    detected_patterns: list[str] = Field(default_factory=list)


class GoogleWorkspaceSearchRequest(BaseModel):
    query: str
    limit: int = 10
    page_token: str | None = None

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("query must be non-empty")
        return stripped

    @field_validator("limit")
    @classmethod
    def bound_limit(cls, value: int) -> int:
        return max(1, min(value, 25))


class GoogleWorkspaceSearchResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    results: list[LiveRetrievalResult] = Field(default_factory=list)
    next_page_token: str | None = None
    request_id: str


class GoogleWorkspaceCredentialHealth(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    connected: bool
    token_expired: bool
    refresh_available: bool
    scopes_valid: bool
    missing_scopes: list[str] = Field(default_factory=list)
    request_id: str
