"""Authenticated Google Workspace MCP API endpoints.

These endpoints are called by MCP server tools (which forward the caller's
DocuBrain bearer token) or directly by any authenticated DocuBrain client.
All operations resolve the current user, load their encrypted Google
credential, and return strict normalized schemas.

Raises ``DocubrainError``, never ``HTTPException``.
Does NOT use ``response_model``.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Request
from sqlalchemy.orm import Session

from docubrain.auth.permissions import get_effective_permissions
from docubrain.auth.users import double_check_user
from docubrain.auth.users import optional_user
from docubrain.configs.constants import DocumentSource
from docubrain.db.engine.sql_engine import get_session
from docubrain.db.enums import Permission
from docubrain.db.models import User
from docubrain.error_handling.error_codes import DocubrainErrorCode
from docubrain.error_handling.exceptions import DocubrainError
from docubrain.mcp.internal_auth import verify_internal_mcp_token
from docubrain.mcp.google_workspace_service import (
    execute_google_workspace_operation,
)
from docubrain.mcp.google_workspace_service import (
    get_google_workspace_credential_health,
)
from docubrain.mcp.observability import get_or_create_request_id
from docubrain.mcp.response_limits import validate_id
from docubrain.mcp.response_limits import validate_page_token
from docubrain.mcp.schemas import GmailRetrievalCapability
from docubrain.mcp.schemas import ProviderCapability
from docubrain.server.features.mcp.google_workspace_api_models import (
    GoogleWorkspaceCredentialHealthAPIResponse,
    GoogleWorkspaceMessageAPIResponse,
    GoogleWorkspaceSearchAPIRequest,
    GoogleWorkspaceSearchAPIResponse,
    GoogleWorkspaceThreadAPIResponse,
    GoogleWorkspaceTruncationMetadata,
)
from docubrain.utils.logger import setup_logger

logger = setup_logger()

router = APIRouter(prefix="/mcp/google-workspace")


# ---------------------------------------------------------------------------
# Request-ID extraction
# ---------------------------------------------------------------------------


def _extract_request_id(request: Request) -> str:
    """Extract or generate a request ID from the incoming request headers."""
    raw = request.headers.get("X-Request-ID")
    return get_or_create_request_id(raw)


def _extract_bearer_token(request: Request) -> str | None:
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    token = auth_header[len("Bearer ") :].strip()
    return token or None


def _require_basic_access(user: User) -> User:
    effective_permissions = get_effective_permissions(user)
    if (
        Permission.FULL_ADMIN_PANEL_ACCESS in effective_permissions
        or Permission.BASIC_ACCESS in effective_permissions
    ):
        return user
    raise DocubrainError(
        DocubrainErrorCode.INSUFFICIENT_PERMISSIONS,
        "You do not have the required permissions for this action.",
    )


def _get_internal_mcp_user(
    *,
    request: Request,
    db_session: Session,
) -> User | None:
    token = _extract_bearer_token(request)
    if token is None:
        return None

    internal_claims = verify_internal_mcp_token(token)
    if internal_claims is None:
        return None

    try:
        user_id = UUID(internal_claims.user_id)
    except ValueError as exc:
        raise DocubrainError(
            DocubrainErrorCode.INVALID_TOKEN,
            "Invalid internal MCP token",
        ) from exc

    user = db_session.get(User, user_id)
    if user is None or not user.is_active:
        raise DocubrainError(
            DocubrainErrorCode.UNAUTHENTICATED,
            "Invalid internal MCP token",
        )
    return _require_basic_access(user)


async def current_google_workspace_user(
    request: Request,
    db_session: Session = Depends(get_session),
    session_user: User | None = Depends(optional_user),
) -> User:
    internal_user = _get_internal_mcp_user(
        request=request,
        db_session=db_session,
    )
    if internal_user is not None:
        return internal_user

    return _require_basic_access(await double_check_user(session_user))


current_google_workspace_user._is_require_permission = True  # type: ignore[attr-defined]


def _build_truncation_metadata(
    raw: dict[str, Any] | None,
) -> GoogleWorkspaceTruncationMetadata | None:
    """Convert raw truncation dict from service layer to typed model."""
    if not raw:
        return None
    return GoogleWorkspaceTruncationMetadata(
        body_text_truncated=raw.get("body_text_truncated", False),
        body_text_original_size=raw.get("body_text_original_size"),
        body_text_returned_size=raw.get("body_text_returned_size"),
        body_html_truncated=raw.get("body_html_truncated", False),
        body_html_original_size=raw.get("body_html_original_size"),
        body_html_returned_size=raw.get("body_html_returned_size"),
        thread_messages_capped=raw.get("thread_messages_capped", False),
        thread_messages_original_count=raw.get("thread_messages_original_count"),
        thread_messages_returned_count=raw.get("thread_messages_returned_count"),
    )


# ---------------------------------------------------------------------------
# Gmail endpoints
# ---------------------------------------------------------------------------


@router.post("/gmail/search")
async def search_gmail(
    body: GoogleWorkspaceSearchAPIRequest,
    request: Request,
    db_session: Session = Depends(get_session),
    user: User = Depends(current_google_workspace_user),
) -> GoogleWorkspaceSearchAPIResponse:
    """Search Gmail messages for the authenticated user."""
    request_id = _extract_request_id(request)
    page_token = validate_page_token(body.page_token)

    result = await execute_google_workspace_operation(
        db_session=db_session,
        user=user,
        source=DocumentSource.GMAIL,
        capability=ProviderCapability.SEARCH_EMAILS,
        arguments={
            "query": body.query,
            "limit": body.limit,
            "page_token": page_token,
        },
        request_id=request_id,
    )

    return GoogleWorkspaceSearchAPIResponse(
        provider=result.provider,
        results=result.records,
        next_page_token=result.next_page_token,
        request_id=request_id,
        truncation=_build_truncation_metadata(result.truncation_metadata),
    )


@router.get("/gmail/messages/{message_id}")
async def get_gmail_message(
    message_id: str,
    request: Request,
    db_session: Session = Depends(get_session),
    user: User = Depends(current_google_workspace_user),
) -> GoogleWorkspaceMessageAPIResponse:
    """Get a single Gmail message by ID for the authenticated user."""
    request_id = _extract_request_id(request)
    validated_id = validate_id(message_id, "message_id")

    result = await execute_google_workspace_operation(
        db_session=db_session,
        user=user,
        source=DocumentSource.GMAIL,
        capability=GmailRetrievalCapability.GET_GMAIL_MESSAGE,
        arguments={"message_id": validated_id},
        request_id=request_id,
    )

    if not result.records:
        raise DocubrainError(
            DocubrainErrorCode.NOT_FOUND,
            f"Gmail message {validated_id} not found",
        )

    return GoogleWorkspaceMessageAPIResponse(
        provider=result.provider,
        message=result.records[0],
        request_id=request_id,
        truncation=_build_truncation_metadata(result.truncation_metadata),
    )


@router.get("/gmail/threads/{thread_id}")
async def get_gmail_thread(
    thread_id: str,
    request: Request,
    page_token: str | None = None,
    db_session: Session = Depends(get_session),
    user: User = Depends(current_google_workspace_user),
) -> GoogleWorkspaceThreadAPIResponse:
    """Get Gmail thread messages by thread ID for the authenticated user."""
    request_id = _extract_request_id(request)
    validated_id = validate_id(thread_id, "thread_id")
    validated_page_token = validate_page_token(page_token)

    result = await execute_google_workspace_operation(
        db_session=db_session,
        user=user,
        source=DocumentSource.GMAIL,
        capability=GmailRetrievalCapability.GET_GMAIL_THREAD,
        arguments={
            "thread_id": validated_id,
            "page_token": validated_page_token,
        },
        request_id=request_id,
    )

    return GoogleWorkspaceThreadAPIResponse(
        provider=result.provider,
        messages=result.records,
        next_page_token=result.next_page_token,
        request_id=request_id,
        truncation=_build_truncation_metadata(result.truncation_metadata),
    )


@router.post("/gmail/threads/search")
async def list_gmail_threads(
    body: GoogleWorkspaceSearchAPIRequest,
    request: Request,
    db_session: Session = Depends(get_session),
    user: User = Depends(current_google_workspace_user),
) -> GoogleWorkspaceSearchAPIResponse:
    """List Gmail threads matching a Gmail query."""
    request_id = _extract_request_id(request)
    result = await execute_google_workspace_operation(
        db_session=db_session,
        user=user,
        source=DocumentSource.GMAIL,
        capability=GmailRetrievalCapability.LIST_GMAIL_THREADS,
        arguments={
            "query": body.query,
            "limit": body.limit,
            "page_token": validate_page_token(body.page_token),
        },
        request_id=request_id,
    )
    return GoogleWorkspaceSearchAPIResponse(
        provider=result.provider,
        results=result.records,
        next_page_token=result.next_page_token,
        request_id=request_id,
        truncation=_build_truncation_metadata(result.truncation_metadata),
    )


@router.get("/gmail/threads/{thread_id}/summary")
async def summarize_gmail_thread(
    thread_id: str,
    request: Request,
    page_token: str | None = None,
    db_session: Session = Depends(get_session),
    user: User = Depends(current_google_workspace_user),
) -> GoogleWorkspaceSearchAPIResponse:
    """Return a compact, model-friendly summary record for a Gmail thread."""
    request_id = _extract_request_id(request)
    result = await execute_google_workspace_operation(
        db_session=db_session,
        user=user,
        source=DocumentSource.GMAIL,
        capability=GmailRetrievalCapability.SUMMARIZE_EMAIL_THREAD,
        arguments={
            "thread_id": validate_id(thread_id, "thread_id"),
            "page_token": validate_page_token(page_token),
        },
        request_id=request_id,
    )
    return GoogleWorkspaceSearchAPIResponse(
        provider=result.provider,
        results=result.records,
        next_page_token=result.next_page_token,
        request_id=request_id,
        truncation=_build_truncation_metadata(result.truncation_metadata),
    )


@router.post("/gmail/attachments/search")
async def search_gmail_attachments(
    body: GoogleWorkspaceSearchAPIRequest,
    request: Request,
    db_session: Session = Depends(get_session),
    user: User = Depends(current_google_workspace_user),
) -> GoogleWorkspaceSearchAPIResponse:
    """Search Gmail messages with attachments and return safe attachment metadata."""
    request_id = _extract_request_id(request)
    result = await execute_google_workspace_operation(
        db_session=db_session,
        user=user,
        source=DocumentSource.GMAIL,
        capability=GmailRetrievalCapability.SEARCH_GMAIL_ATTACHMENTS,
        arguments={
            "query": body.query,
            "limit": body.limit,
            "page_token": validate_page_token(body.page_token),
        },
        request_id=request_id,
    )
    return GoogleWorkspaceSearchAPIResponse(
        provider=result.provider,
        results=result.records,
        next_page_token=result.next_page_token,
        request_id=request_id,
        truncation=_build_truncation_metadata(result.truncation_metadata),
    )


# ---------------------------------------------------------------------------
# Drive endpoints
# ---------------------------------------------------------------------------


@router.post("/drive/search")
async def search_drive(
    body: GoogleWorkspaceSearchAPIRequest,
    request: Request,
    db_session: Session = Depends(get_session),
    user: User = Depends(current_google_workspace_user),
) -> GoogleWorkspaceSearchAPIResponse:
    """Search Google Drive files for the authenticated user."""
    request_id = _extract_request_id(request)
    page_token = validate_page_token(body.page_token)

    result = await execute_google_workspace_operation(
        db_session=db_session,
        user=user,
        source=DocumentSource.GOOGLE_DRIVE,
        capability=ProviderCapability.SEARCH_DOCS,
        arguments={
            "query": body.query,
            "limit": body.limit,
            "page_token": page_token,
        },
        request_id=request_id,
    )

    return GoogleWorkspaceSearchAPIResponse(
        provider=result.provider,
        results=result.records,
        next_page_token=result.next_page_token,
        request_id=request_id,
        truncation=_build_truncation_metadata(result.truncation_metadata),
    )


@router.post("/drive/content/search")
async def search_drive_content(
    body: GoogleWorkspaceSearchAPIRequest,
    request: Request,
    db_session: Session = Depends(get_session),
    user: User = Depends(current_google_workspace_user),
) -> GoogleWorkspaceSearchAPIResponse:
    """Search Google Drive full text content for the authenticated user."""
    request_id = _extract_request_id(request)
    result = await execute_google_workspace_operation(
        db_session=db_session,
        user=user,
        source=DocumentSource.GOOGLE_DRIVE,
        capability=ProviderCapability.SEARCH_DRIVE_CONTENT,
        arguments={
            "query": body.query,
            "limit": body.limit,
            "page_token": validate_page_token(body.page_token),
        },
        request_id=request_id,
    )
    return GoogleWorkspaceSearchAPIResponse(
        provider=result.provider,
        results=result.records,
        next_page_token=result.next_page_token,
        request_id=request_id,
        truncation=_build_truncation_metadata(result.truncation_metadata),
    )


@router.get("/drive/files/{file_id}")
async def get_drive_file_metadata(
    file_id: str,
    request: Request,
    db_session: Session = Depends(get_session),
    user: User = Depends(current_google_workspace_user),
) -> GoogleWorkspaceMessageAPIResponse:
    """Get Google Drive file metadata."""
    request_id = _extract_request_id(request)
    result = await execute_google_workspace_operation(
        db_session=db_session,
        user=user,
        source=DocumentSource.GOOGLE_DRIVE,
        capability=ProviderCapability.GET_DRIVE_FILE_METADATA,
        arguments={"file_id": validate_id(file_id, "file_id")},
        request_id=request_id,
    )
    return GoogleWorkspaceMessageAPIResponse(
        provider=result.provider,
        message=result.records[0] if result.records else {},
        request_id=request_id,
        truncation=_build_truncation_metadata(result.truncation_metadata),
    )


ALLOWED_EXPORT_MIME_TYPES: frozenset[str] = frozenset(
    {
        "text/plain",
        "text/html",
        "text/csv",
        "application/pdf",
        "application/rtf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }
)


@router.get("/drive/docs/{file_id}/export")
async def export_google_doc(
    file_id: str,
    request: Request,
    mime_type: str = "text/plain",
    gid: str | None = None,
    db_session: Session = Depends(get_session),
    user: User = Depends(current_google_workspace_user),
) -> GoogleWorkspaceMessageAPIResponse:
    """Export a Google Doc through Drive's export API."""
    if mime_type not in ALLOWED_EXPORT_MIME_TYPES:
        raise DocubrainError(
            DocubrainErrorCode.INVALID_INPUT,
            f"Unsupported export MIME type: {mime_type}",
        )
    request_id = _extract_request_id(request)
    arguments: dict[str, str] = {
        "file_id": validate_id(file_id, "file_id"),
        "mime_type": mime_type,
    }
    if gid:
        arguments["gid"] = gid
    result = await execute_google_workspace_operation(
        db_session=db_session,
        user=user,
        source=DocumentSource.GOOGLE_DRIVE,
        capability=ProviderCapability.EXPORT_GOOGLE_DOC,
        arguments=arguments,
        request_id=request_id,
    )
    return GoogleWorkspaceMessageAPIResponse(
        provider=result.provider,
        message=result.records[0] if result.records else {},
        request_id=request_id,
        truncation=_build_truncation_metadata(result.truncation_metadata),
    )


@router.get("/drive/docs/{file_id}/read")
async def read_google_doc(
    file_id: str,
    request: Request,
    db_session: Session = Depends(get_session),
    user: User = Depends(current_google_workspace_user),
) -> GoogleWorkspaceMessageAPIResponse:
    """Read a Google Doc as plain text."""
    return await export_google_doc(
        file_id=file_id,
        request=request,
        mime_type="text/plain",
        db_session=db_session,
        user=user,
    )


@router.get("/drive/shared-drives")
async def list_shared_drives(
    request: Request,
    limit: int = 10,
    page_token: str | None = None,
    db_session: Session = Depends(get_session),
    user: User = Depends(current_google_workspace_user),
) -> GoogleWorkspaceSearchAPIResponse:
    """List shared drives visible to the authenticated user."""
    from docubrain.mcp.response_limits import validate_limit

    request_id = _extract_request_id(request)
    result = await execute_google_workspace_operation(
        db_session=db_session,
        user=user,
        source=DocumentSource.GOOGLE_DRIVE,
        capability=ProviderCapability.LIST_SHARED_DRIVES,
        arguments={
            "limit": validate_limit(limit),
            "page_token": validate_page_token(page_token),
        },
        request_id=request_id,
    )
    return GoogleWorkspaceSearchAPIResponse(
        provider=result.provider,
        results=result.records,
        next_page_token=result.next_page_token,
        request_id=request_id,
        truncation=_build_truncation_metadata(result.truncation_metadata),
    )


@router.get("/drive/recent")
async def list_recent_files(
    request: Request,
    limit: int = 10,
    page_token: str | None = None,
    db_session: Session = Depends(get_session),
    user: User = Depends(current_google_workspace_user),
) -> GoogleWorkspaceSearchAPIResponse:
    """List recently modified Drive files visible to the authenticated user."""
    from docubrain.mcp.response_limits import validate_limit

    request_id = _extract_request_id(request)
    result = await execute_google_workspace_operation(
        db_session=db_session,
        user=user,
        source=DocumentSource.GOOGLE_DRIVE,
        capability=ProviderCapability.LIST_RECENT_FILES,
        arguments={
            "limit": validate_limit(limit),
            "page_token": validate_page_token(page_token),
        },
        request_id=request_id,
    )
    return GoogleWorkspaceSearchAPIResponse(
        provider=result.provider,
        results=result.records,
        next_page_token=result.next_page_token,
        request_id=request_id,
        truncation=_build_truncation_metadata(result.truncation_metadata),
    )


@router.get("/drive/files/{file_id}/permissions")
async def get_file_permissions(
    file_id: str,
    request: Request,
    db_session: Session = Depends(get_session),
    user: User = Depends(current_google_workspace_user),
) -> GoogleWorkspaceSearchAPIResponse:
    """List permissions for a Drive file."""
    request_id = _extract_request_id(request)
    result = await execute_google_workspace_operation(
        db_session=db_session,
        user=user,
        source=DocumentSource.GOOGLE_DRIVE,
        capability=ProviderCapability.GET_FILE_PERMISSIONS,
        arguments={"file_id": validate_id(file_id, "file_id")},
        request_id=request_id,
    )
    return GoogleWorkspaceSearchAPIResponse(
        provider=result.provider,
        results=result.records,
        next_page_token=result.next_page_token,
        request_id=request_id,
        truncation=_build_truncation_metadata(result.truncation_metadata),
    )


@router.get("/drive/folders/{folder_id}/children")
async def list_folder_contents(
    folder_id: str,
    request: Request,
    limit: int = 10,
    page_token: str | None = None,
    db_session: Session = Depends(get_session),
    user: User = Depends(current_google_workspace_user),
) -> GoogleWorkspaceSearchAPIResponse:
    """List child files/folders for a Drive folder."""
    from docubrain.mcp.response_limits import validate_limit

    request_id = _extract_request_id(request)
    result = await execute_google_workspace_operation(
        db_session=db_session,
        user=user,
        source=DocumentSource.GOOGLE_DRIVE,
        capability=ProviderCapability.LIST_FOLDER_CONTENTS,
        arguments={
            "folder_id": validate_id(folder_id, "folder_id"),
            "limit": validate_limit(limit),
            "page_token": validate_page_token(page_token),
        },
        request_id=request_id,
    )
    return GoogleWorkspaceSearchAPIResponse(
        provider=result.provider,
        results=result.records,
        next_page_token=result.next_page_token,
        request_id=request_id,
        truncation=_build_truncation_metadata(result.truncation_metadata),
    )


# ---------------------------------------------------------------------------
# Credential health endpoints
# ---------------------------------------------------------------------------


@router.get("/gmail/credential-health")
def gmail_credential_health(
    request: Request,
    db_session: Session = Depends(get_session),
    user: User = Depends(current_google_workspace_user),
) -> GoogleWorkspaceCredentialHealthAPIResponse:
    """Check Gmail credential health for the authenticated user."""
    request_id = _extract_request_id(request)

    health = get_google_workspace_credential_health(
        db_session=db_session,
        user=user,
        source=DocumentSource.GMAIL,
        request_id=request_id,
    )

    return GoogleWorkspaceCredentialHealthAPIResponse(
        provider=health.provider,
        connected=health.connected,
        token_expired=health.token_expired,
        refresh_available=health.refresh_available,
        scopes_valid=health.scopes_valid,
        missing_scopes=health.missing_scopes,
        request_id=health.request_id,
    )


@router.get("/drive/credential-health")
def drive_credential_health(
    request: Request,
    db_session: Session = Depends(get_session),
    user: User = Depends(current_google_workspace_user),
) -> GoogleWorkspaceCredentialHealthAPIResponse:
    """Check Google Drive credential health for the authenticated user."""
    request_id = _extract_request_id(request)

    health = get_google_workspace_credential_health(
        db_session=db_session,
        user=user,
        source=DocumentSource.GOOGLE_DRIVE,
        request_id=request_id,
    )

    return GoogleWorkspaceCredentialHealthAPIResponse(
        provider=health.provider,
        connected=health.connected,
        token_expired=health.token_expired,
        refresh_available=health.refresh_available,
        scopes_valid=health.scopes_valid,
        missing_scopes=health.missing_scopes,
        request_id=health.request_id,
    )
