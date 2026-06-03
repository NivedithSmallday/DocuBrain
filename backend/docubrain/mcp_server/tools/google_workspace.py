"""Google Workspace MCP tools for FastMCP server.

Each tool forwards the caller's DocuBrain bearer token to the backend
API endpoints under ``/mcp/google-workspace/``. No direct DB access,
no credential store interaction, no raw Google tokens.

Tool schemas expose ONLY user-facing arguments — never ``access_token``,
``refresh_token``, ``client_secret``, or ``credential_json``.
"""

import re
from typing import Any

from docubrain.mcp.observability import get_or_create_request_id
from docubrain.mcp_server.api import mcp_server
from docubrain.mcp_server.tools.search import _extract_error_detail
from docubrain.mcp_server.utils import get_http_client
from docubrain.mcp_server.utils import require_access_token
from docubrain.utils.logger import setup_logger
from docubrain.utils.variable_functionality import (
    build_api_server_url_for_http_requests,
)

logger = setup_logger()

import os

MCP_TOOL_TIMEOUT_SECONDS: float = float(
    os.environ.get("MCP_TOOL_TIMEOUT_SECONDS", "30.0")
)

# Regex to extract a Google Drive file ID from a full URL or bare ID.
_DRIVE_FILE_ID_RE = re.compile(
    r"(?:https?://docs\.google\.com/(?:document|spreadsheets|presentation|forms)/d/"
    r"|https?://drive\.google\.com/(?:file/d/|open\?id=))"
    r"([a-zA-Z0-9_-]+)",
)


def _extract_file_id(file_id_or_url: str) -> str:
    """Return the bare Drive file ID, extracting it from a URL if needed."""
    file_id_or_url = file_id_or_url.strip()
    m = _DRIVE_FILE_ID_RE.search(file_id_or_url)
    if m:
        return m.group(1)
    # Already a bare ID (or unrecognised format — pass through).
    return file_id_or_url.split("/")[0].split("?")[0]


_GID_RE = re.compile(r"[?&#]gid=(\d+)")


def _extract_gid(url_or_id: str) -> str | None:
    """Extract the sheet tab gid from a Google Sheets URL, if present."""
    m = _GID_RE.search(url_or_id)
    return m.group(1) if m else None


def _base_url() -> str:
    return build_api_server_url_for_http_requests(respect_env_override_if_set=True)


def _auth_headers(token: str, request_id: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Request-ID": request_id,
    }


_AUTH_ERROR_KEYWORDS = frozenset({
    "authentication", "authenticated", "auth", "credential",
    "expired", "revoked", "401", "unauthorized", "reconnect",
    "invalid_grant", "token has been expired",
    "multiple rows were found",
})


def _is_auth_error(error_msg: str, status_code: int | None = None) -> bool:
    """Detect if an error is an authentication/credential issue."""
    if status_code == 401:
        return True
    lower = error_msg.lower()
    return any(kw in lower for kw in _AUTH_ERROR_KEYWORDS)


_REAUTH_ACTION = (
    "ACTION REQUIRED: Your Google credentials have expired or been revoked. "
    "Please go to Settings → Connectors → Google Drive and reconnect your Google account. "
    "Do NOT retry this request — it will fail again until re-authentication is complete."
)


def _safe_error_response(
    *,
    tool: str,
    request_id: str,
    error: str,
    status_code: int | None = None,
) -> dict[str, Any]:
    """Build a safe error dict — never includes token or credential data."""
    resp: dict[str, Any] = {
        "error": error,
        "tool": tool,
        "request_id": request_id,
    }
    if _is_auth_error(error, status_code):
        resp["auth_error"] = True
        resp["user_action"] = _REAUTH_ACTION
    return resp


async def _backend_request(
    *,
    method: str,
    tool: str,
    path: str,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request_id = get_or_create_request_id(None)
    access_token = require_access_token()
    logger.info(
        "DocuBrain MCP Server: %s: request_id=%s user=%s",
        tool,
        request_id,
        getattr(access_token, "user_email", "unknown"),
    )
    try:
        client = get_http_client()
        url = f"{_base_url()}{path}"
        headers = _auth_headers(access_token.token, request_id)
        if method == "GET":
            response = await client.get(
                url, headers=headers, params=params, timeout=MCP_TOOL_TIMEOUT_SECONDS,
            )
        else:
            response = await client.post(
                url, json=json_body, headers=headers, timeout=MCP_TOOL_TIMEOUT_SECONDS,
            )
        if not response.is_success:
            return _safe_error_response(
                tool=tool,
                request_id=request_id,
                error=_extract_error_detail(response),
                status_code=response.status_code,
            )
        return response.json()  # type: ignore[no-any-return]
    except Exception as e:
        logger.error("DocuBrain MCP Server: %s error: %s", tool, e, exc_info=True)
        error_msg = f"{tool} failed: {type(e).__name__}"
        return _safe_error_response(
            tool=tool,
            request_id=request_id,
            error=error_msg,
        )


async def _backend_get(
    *, tool: str, path: str, params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await _backend_request(method="GET", tool=tool, path=path, params=params)


async def _backend_post(
    *, tool: str, path: str, json_body: dict[str, Any],
) -> dict[str, Any]:
    return await _backend_request(method="POST", tool=tool, path=path, json_body=json_body)


# ---------------------------------------------------------------------------
# Gmail tools
# ---------------------------------------------------------------------------


@mcp_server.tool()
async def search_gmail(
    query: str,
    limit: int = 10,
    page_token: str | None = None,
) -> dict[str, Any]:
    """Search and retrieve emails from the user's Gmail inbox.

    ALWAYS use this tool when the user asks about:
    - Emails, mails, inbox, messages they received or sent
    - Any query containing words like: "mail", "email", "inbox",
      "received", "sent me", "I got", "newsletter", "notification"
    - Filtering emails by topic, sender, subject, or date

    DO NOT use internal_search for any email-related query. Use this tool instead.

    Example triggers:
    - "KT related mails" → search_gmail(query="KT knowledge transfer")
    - "emails from GitHub" → search_gmail(query="from:github.com")
    - "mails about AWS" → search_gmail(query="AWS")

    Args:
        query: Gmail search query (same syntax as Gmail search bar).
              Supports operators like from:, to:, subject:, OR, etc.
              Must be a single string, NOT a Python list.
        limit: Maximum number of results to return (1-25, default 10).
        page_token: Optional pagination token from a previous response.

    Returns:
        Normalized search results with pagination metadata.
    """
    return await _backend_post(
        tool="search_gmail",
        path="/mcp/google-workspace/gmail/search",
        json_body={"query": query, "limit": limit, "page_token": page_token},
    )


@mcp_server.tool(name="get_recent_emails")
async def get_recent_emails(max_results: int = 10) -> dict[str, Any]:
    """Get recent Gmail messages from the user's inbox."""
    return await search_gmail(query="in:inbox", limit=max_results)


@mcp_server.tool()
async def get_gmail_message(
    message_id: str,
) -> dict[str, Any]:
    """Get a single Gmail message with full body content.

    Retrieves the complete message including subject, sender, full body
    text, and HTML content (when available).

    Args:
        message_id: The Gmail message ID to retrieve.

    Returns:
        Normalized message with full body content and truncation metadata.
    """
    return await _backend_get(
        tool="get_gmail_message",
        path=f"/mcp/google-workspace/gmail/messages/{message_id}",
    )


@mcp_server.tool()
async def get_gmail_thread(
    thread_id: str,
    page_token: str | None = None,
) -> dict[str, Any]:
    """Get all messages in a Gmail thread.

    Retrieves the full conversation thread with all messages, each
    including full body content.

    Args:
        thread_id: The Gmail thread ID to retrieve.
        page_token: Optional pagination token for large threads.

    Returns:
        List of normalized messages with pagination and truncation metadata.
    """
    params = {"page_token": page_token} if page_token else None
    return await _backend_get(
        tool="get_gmail_thread",
        path=f"/mcp/google-workspace/gmail/threads/{thread_id}",
        params=params,
    )


@mcp_server.tool()
async def search_emails(
    query: str,
    limit: int = 10,
    page_token: str | None = None,
) -> dict[str, Any]:
    """Search Gmail messages. Alias for search_gmail with enterprise-friendly naming."""
    return await _backend_post(
        tool="search_emails",
        path="/mcp/google-workspace/gmail/search",
        json_body={"query": query, "limit": limit, "page_token": page_token},
    )


@mcp_server.tool()
async def get_email(message_id: str) -> dict[str, Any]:
    """Get a Gmail message by ID. Alias for get_gmail_message."""
    return await _backend_get(
        tool="get_email",
        path=f"/mcp/google-workspace/gmail/messages/{message_id}",
    )


@mcp_server.tool()
async def list_threads(
    query: str,
    limit: int = 10,
    page_token: str | None = None,
) -> dict[str, Any]:
    """List Gmail threads matching a Gmail search query."""
    return await _backend_post(
        tool="list_threads",
        path="/mcp/google-workspace/gmail/threads/search",
        json_body={"query": query, "limit": limit, "page_token": page_token},
    )


@mcp_server.tool()
async def summarize_email_thread(
    thread_id: str,
    page_token: str | None = None,
) -> dict[str, Any]:
    """Return a compact summary record for a Gmail thread."""
    params = {"page_token": page_token} if page_token else None
    return await _backend_get(
        tool="summarize_email_thread",
        path=f"/mcp/google-workspace/gmail/threads/{thread_id}/summary",
        params=params,
    )


@mcp_server.tool()
async def search_attachments(
    query: str,
    limit: int = 10,
    page_token: str | None = None,
) -> dict[str, Any]:
    """Search Gmail attachments and return safe attachment metadata."""
    return await _backend_post(
        tool="search_attachments",
        path="/mcp/google-workspace/gmail/attachments/search",
        json_body={"query": query, "limit": limit, "page_token": page_token},
    )


# ---------------------------------------------------------------------------
# Drive tools
# ---------------------------------------------------------------------------


@mcp_server.tool()
async def search_google_drive(
    query: str,
    limit: int = 10,
    page_token: str | None = None,
) -> dict[str, Any]:
    """Search the user's Google Drive files.

    Searches for files matching the query string. Returns normalized
    results with file name, type, URL, and last modified timestamp.

    Args:
        query: Drive search query.
        limit: Maximum number of results to return (1-25, default 10).
        page_token: Optional pagination token from a previous response.

    Returns:
        Normalized search results with pagination metadata.
    """
    return await _backend_post(
        tool="search_google_drive",
        path="/mcp/google-workspace/drive/search",
        json_body={"query": query, "limit": limit, "page_token": page_token},
    )


@mcp_server.tool()
async def search_drive_files(
    query: str,
    limit: int = 10,
    page_token: str | None = None,
) -> dict[str, Any]:
    """Search Google Drive file metadata/content for files matching a query."""
    return await _backend_post(
        tool="search_drive_files",
        path="/mcp/google-workspace/drive/search",
        json_body={"query": query, "limit": limit, "page_token": page_token},
    )


@mcp_server.tool()
async def search_drive_content(
    query: str,
    limit: int = 10,
    page_token: str | None = None,
) -> dict[str, Any]:
    """Search Google Drive full text content."""
    return await _backend_post(
        tool="search_drive_content",
        path="/mcp/google-workspace/drive/content/search",
        json_body={"query": query, "limit": limit, "page_token": page_token},
    )


@mcp_server.tool()
async def get_drive_file_metadata(file_id: str) -> dict[str, Any]:
    """Get Google Drive file metadata including owner, shared drive, and parent data."""
    return await _backend_get(
        tool="get_drive_file_metadata",
        path=f"/mcp/google-workspace/drive/files/{file_id}",
    )


@mcp_server.tool()
async def read_google_doc(file_id: str) -> dict[str, Any]:
    """Read a Google Workspace document (Doc, Sheet, or Slide) as plain text.

    Args:
        file_id: The Google Drive file ID (e.g. '1w2kdJQH9PMny...').
                 A full Google Docs/Sheets/Slides URL is also accepted and the
                 file ID will be extracted automatically.
                 For Sheets URLs with a gid parameter (e.g. ...edit?gid=123),
                 only that specific tab is exported.

    Supports Google Docs, Sheets (exported as CSV), and Slides (exported as
    plain text).  For other export formats use ``export_google_doc``.
    """
    clean_id = _extract_file_id(file_id)
    gid = _extract_gid(file_id)
    # Try the dedicated read endpoint first (works for Docs).
    result = await _backend_get(
        tool="read_google_doc",
        path=f"/mcp/google-workspace/drive/docs/{clean_id}/read",
    )
    # If 404 / error, fall back to export (handles Sheets, Slides, etc.).
    if isinstance(result, dict) and result.get("error"):
        logger.info(
            "read_google_doc: read endpoint failed for %s, "
            "falling back to export as text/csv (gid=%s)",
            clean_id, gid,
        )
        params: dict[str, str] = {"mime_type": "text/csv"}
        if gid:
            params["gid"] = gid
        return await _backend_get(
            tool="export_google_doc",
            path=f"/mcp/google-workspace/drive/docs/{clean_id}/export",
            params=params,
        )
    return result


_ALLOWED_EXPORT_MIMES = frozenset(
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


@mcp_server.tool()
async def export_google_doc(
    file_id: str,
    mime_type: str = "text/plain",
    gid: str | None = None,
) -> dict[str, Any]:
    """Export a Google Workspace document in a supported MIME type.

    Args:
        file_id: The Google Drive file ID or full URL.
        mime_type: Target format (default text/plain). Use text/csv for Sheets.
        gid: Optional sheet tab ID for Google Sheets (found in URL as gid=...).
             When provided, exports only that specific sheet tab.
    """
    if mime_type not in _ALLOWED_EXPORT_MIMES:
        request_id = get_or_create_request_id(None)
        return {
            "error": f"Unsupported export MIME type: {mime_type}",
            "allowed": sorted(_ALLOWED_EXPORT_MIMES),
            "request_id": request_id,
        }
    clean_id = _extract_file_id(file_id)
    params: dict[str, str] = {"mime_type": mime_type}
    if gid:
        params["gid"] = gid
    return await _backend_get(
        tool="export_google_doc",
        path=f"/mcp/google-workspace/drive/docs/{clean_id}/export",
        params=params,
    )


@mcp_server.tool()
async def list_shared_drives(
    limit: int = 10,
    page_token: str | None = None,
) -> dict[str, Any]:
    """List shared drives visible to the authenticated user."""
    params: dict[str, Any] = {"limit": limit}
    if page_token:
        params["page_token"] = page_token
    return await _backend_get(
        tool="list_shared_drives",
        path="/mcp/google-workspace/drive/shared-drives",
        params=params,
    )


@mcp_server.tool()
async def list_recent_files(
    limit: int = 10,
    page_token: str | None = None,
) -> dict[str, Any]:
    """List recently modified Google Drive files."""
    params: dict[str, Any] = {"limit": limit}
    if page_token:
        params["page_token"] = page_token
    return await _backend_get(
        tool="list_recent_files",
        path="/mcp/google-workspace/drive/recent",
        params=params,
    )


@mcp_server.tool()
async def get_file_permissions(file_id: str) -> dict[str, Any]:
    """List Google Drive file permissions for audit/debugging workflows."""
    return await _backend_get(
        tool="get_file_permissions",
        path=f"/mcp/google-workspace/drive/files/{file_id}/permissions",
    )


@mcp_server.tool()
async def list_folder_contents(
    folder_id: str,
    limit: int = 10,
    page_token: str | None = None,
) -> dict[str, Any]:
    """List files and folders directly under a Google Drive folder."""
    params: dict[str, Any] = {"limit": limit}
    if page_token:
        params["page_token"] = page_token
    return await _backend_get(
        tool="list_folder_contents",
        path=f"/mcp/google-workspace/drive/folders/{folder_id}/children",
        params=params,
    )


# ---------------------------------------------------------------------------
# Health check tools
# ---------------------------------------------------------------------------


@mcp_server.tool()
async def gmail_credential_health() -> dict[str, Any]:
    """Check Gmail credential health for the authenticated user.

    Returns the connection status, token expiry state, refresh availability,
    and scope validation results. Use this to diagnose authentication issues
    before performing Gmail operations.

    Returns:
        Credential health status including connection, expiry, and scope info.
    """
    request_id = get_or_create_request_id(None)
    access_token = require_access_token()
    headers = _auth_headers(access_token.token, request_id)

    logger.info(
        "DocuBrain MCP Server: gmail_credential_health: request_id=%s",
        request_id,
    )

    try:
        response = await get_http_client().get(
            f"{_base_url()}/mcp/google-workspace/gmail/credential-health",
            headers=headers,
            timeout=MCP_TOOL_TIMEOUT_SECONDS,
        )
        if not response.is_success:
            return _safe_error_response(
                tool="gmail_credential_health",
                request_id=request_id,
                error=_extract_error_detail(response),
            )
        return response.json()  # type: ignore[no-any-return]
    except Exception as e:
        logger.error(
            "DocuBrain MCP Server: gmail_credential_health error: %s", e, exc_info=True
        )
        return _safe_error_response(
            tool="gmail_credential_health",
            request_id=request_id,
            error=f"Gmail health check failed: {type(e).__name__}",
        )


@mcp_server.tool()
async def google_drive_credential_health() -> dict[str, Any]:
    """Check Google Drive credential health for the authenticated user.

    Returns the connection status, token expiry state, refresh availability,
    and scope validation results. Use this to diagnose authentication issues
    before performing Drive operations.

    Returns:
        Credential health status including connection, expiry, and scope info.
    """
    request_id = get_or_create_request_id(None)
    access_token = require_access_token()
    headers = _auth_headers(access_token.token, request_id)

    logger.info(
        "DocuBrain MCP Server: google_drive_credential_health: request_id=%s",
        request_id,
    )

    try:
        response = await get_http_client().get(
            f"{_base_url()}/mcp/google-workspace/drive/credential-health",
            headers=headers,
            timeout=MCP_TOOL_TIMEOUT_SECONDS,
        )
        if not response.is_success:
            return _safe_error_response(
                tool="google_drive_credential_health",
                request_id=request_id,
                error=_extract_error_detail(response),
            )
        return response.json()  # type: ignore[no-any-return]
    except Exception as e:
        logger.error(
            "DocuBrain MCP Server: google_drive_credential_health error: %s",
            e,
            exc_info=True,
        )
        return _safe_error_response(
            tool="google_drive_credential_health",
            request_id=request_id,
            error=f"Drive health check failed: {type(e).__name__}",
        )
