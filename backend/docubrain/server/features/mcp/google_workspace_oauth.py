"""Google Workspace OAuth onboarding for DocuBrain's internal MCP tools.

Google does not host MCP servers. This module performs application-level
Google OAuth, stores encrypted per-user Google credentials, and ensures the
internal DocuBrain Google Workspace MCP server is registered with real tools.
"""

from __future__ import annotations

import datetime
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import APIRouter
from fastapi import Depends
from fastapi import Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from docubrain.auth.permissions import require_permission
from docubrain.cache.factory import get_cache_backend
from docubrain.cache.interface import CacheBackend
from docubrain.configs.app_configs import GOOGLE_WORKSPACE_CLIENT_ID
from docubrain.configs.app_configs import GOOGLE_WORKSPACE_CLIENT_SECRET
from docubrain.configs.app_configs import GOOGLE_WORKSPACE_MCP_ENABLED
from docubrain.configs.app_configs import GOOGLE_WORKSPACE_REDIRECT_PATH
from docubrain.configs.app_configs import GOOGLE_WORKSPACE_SCOPES
from docubrain.configs.app_configs import WEB_DOMAIN
from docubrain.configs.constants import DocumentSource
from docubrain.connectors.google_utils.shared_constants import (
    DB_CREDENTIALS_AUTHENTICATION_METHOD,
)
from docubrain.connectors.google_utils.shared_constants import (
    DB_CREDENTIALS_DICT_TOKEN_KEY,
)
from docubrain.connectors.google_utils.shared_constants import (
    DB_CREDENTIALS_PRIMARY_ADMIN_KEY,
)
from docubrain.connectors.google_utils.shared_constants import (
    GoogleOAuthAuthenticationMethod,
)
from docubrain.db.engine.sql_engine import get_session
from docubrain.db.enums import MCPServerStatus
from docubrain.db.enums import Permission
from docubrain.db.google_workspace_mcp import delete_user_google_workspace_credential
from docubrain.db.google_workspace_mcp import get_user_google_workspace_credential
from docubrain.db.google_workspace_mcp import upsert_user_google_workspace_credential_json
from docubrain.db.mcp import get_all_mcp_servers
from docubrain.db.mcp import get_all_mcp_tools_for_server
from docubrain.db.mcp import update_mcp_server__no_commit
from docubrain.db.models import User
from docubrain.error_handling.error_codes import DocubrainErrorCode
from docubrain.error_handling.exceptions import DocubrainError
from docubrain.server.features.mcp.bootstrap import (
    DEFAULT_DOCUBRAIN_MCP_SERVER_NAME,
)
from docubrain.server.features.mcp.bootstrap import bootstrap_default_mcp_servers_for_admin
from docubrain.utils.logger import setup_logger
from pydantic import BaseModel

logger = setup_logger()

router = APIRouter(prefix="/google-workspace-oauth")

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"

IDENTITY_SCOPES = ["openid", "email", "profile"]
OAUTH_STATE_TTL_SECONDS = 600
OAUTH_STATE_CACHE_KEY_PREFIX = "google_workspace_oauth_state"

GMAIL_TOOL_NAMES = {
    "search_gmail",
    "search_emails",
    "get_recent_emails",
    "get_gmail_message",
    "get_email",
    "get_gmail_thread",
    "list_threads",
    "summarize_email_thread",
    "search_attachments",
    "gmail_credential_health",
}
DRIVE_TOOL_NAMES = {
    "search_google_drive",
    "search_drive_files",
    "search_drive_content",
    "get_drive_file_metadata",
    "read_google_doc",
    "export_google_doc",
    "list_shared_drives",
    "list_recent_files",
    "get_file_permissions",
    "list_folder_contents",
    "google_drive_credential_health",
}


@dataclass(frozen=True)
class GoogleWorkspaceOAuthState:
    user_id: str
    user_email: str
    created_at: float


def _redirect_uri() -> str:
    return f"{WEB_DOMAIN.rstrip('/')}{GOOGLE_WORKSPACE_REDIRECT_PATH}"


def _all_scopes() -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for scope in IDENTITY_SCOPES + GOOGLE_WORKSPACE_SCOPES:
        if scope not in seen:
            seen.add(scope)
            result.append(scope)
    return result


def google_workspace_oauth_state_cache_key(state: str) -> str:
    return f"{OAUTH_STATE_CACHE_KEY_PREFIX}:{state}"


def create_google_workspace_oauth_state(
    user: User,
    *,
    cache_backend: CacheBackend | None = None,
) -> str:
    state = secrets.token_urlsafe(32)
    cache = cache_backend or get_cache_backend()
    cache.set(
        google_workspace_oauth_state_cache_key(state),
        json.dumps(
            {
                "user_id": str(user.id),
                "user_email": user.email,
                "created_at": time.time(),
            }
        ),
        ex=OAUTH_STATE_TTL_SECONDS,
    )
    return state


def pop_validated_google_workspace_oauth_state(
    state: str,
    user: User,
    *,
    cache_backend: CacheBackend | None = None,
) -> GoogleWorkspaceOAuthState:
    cache = cache_backend or get_cache_backend()
    key = google_workspace_oauth_state_cache_key(state)
    state_bytes = cache.get(key)
    cache.delete(key)
    if state_bytes is None:
        raise DocubrainError(
            DocubrainErrorCode.VALIDATION_ERROR,
            "Invalid or expired OAuth state",
        )

    try:
        state_data = json.loads(state_bytes.decode("utf-8"))
        oauth_state = GoogleWorkspaceOAuthState(
            user_id=str(state_data["user_id"]),
            user_email=str(state_data["user_email"]),
            created_at=float(state_data["created_at"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DocubrainError(
            DocubrainErrorCode.VALIDATION_ERROR,
            "Invalid or expired OAuth state",
        ) from exc

    if oauth_state.user_id != str(user.id) or oauth_state.user_email != user.email:
        raise DocubrainError(
            DocubrainErrorCode.VALIDATION_ERROR,
            "Invalid or expired OAuth state",
        )
    return oauth_state


def _google_token_json(
    *,
    token_data: dict[str, Any],
    refresh_token: str | None,
    scopes: list[str],
) -> str:
    expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        seconds=int(token_data.get("expires_in", 3600))
    )
    return json.dumps(
        {
            "token": token_data["access_token"],
            "refresh_token": refresh_token,
            "expiry": expires_at.isoformat().replace("+00:00", "Z"),
            "scopes": scopes,
        }
    )


def _google_workspace_credential_json(
    *,
    token_json: str,
    google_email: str,
) -> dict[str, Any]:
    return {
        DB_CREDENTIALS_DICT_TOKEN_KEY: token_json,
        DB_CREDENTIALS_PRIMARY_ADMIN_KEY: google_email,
        DB_CREDENTIALS_AUTHENTICATION_METHOD: (
            GoogleOAuthAuthenticationMethod.OAUTH_INTERACTIVE.value
        ),
    }


def _store_google_workspace_credentials(
    *,
    db_session: Session,
    user: User,
    token_data: dict[str, Any],
    refresh_token: str | None,
    scopes: list[str],
    google_email: str,
) -> None:
    token_json = _google_token_json(
        token_data=token_data,
        refresh_token=refresh_token,
        scopes=scopes,
    )
    credential_json = _google_workspace_credential_json(
        token_json=token_json,
        google_email=google_email,
    )
    for source, name in (
        (DocumentSource.GMAIL, "Google Workspace Gmail OAuth"),
        (DocumentSource.GOOGLE_DRIVE, "Google Workspace Drive OAuth"),
    ):
        upsert_user_google_workspace_credential_json(
            db_session=db_session,
            user=user,
            source=source,
            credential_json=credential_json,
            name=name,
        )


def _existing_refresh_token(
    *,
    db_session: Session,
    user: User,
) -> str | None:
    credential = get_user_google_workspace_credential(
        db_session=db_session,
        user=user,
        source=DocumentSource.GMAIL,
    ) or get_user_google_workspace_credential(
        db_session=db_session,
        user=user,
        source=DocumentSource.GOOGLE_DRIVE,
    )
    if credential is None or credential.credential_json is None:
        return None
    credential_json = credential.credential_json.get_value(apply_mask=False)
    token_json = credential_json.get(DB_CREDENTIALS_DICT_TOKEN_KEY)
    if not isinstance(token_json, str):
        return None
    try:
        token_info = json.loads(token_json)
    except json.JSONDecodeError:
        return None
    refresh_token = token_info.get("refresh_token")
    return refresh_token if isinstance(refresh_token, str) and refresh_token else None


def _find_internal_server(servers: list[Any]) -> Any | None:
    for server in servers:
        if server.name == DEFAULT_DOCUBRAIN_MCP_SERVER_NAME:
            return server
    return None


def _credential_connected(
    *,
    db_session: Session,
    user: User,
    source: DocumentSource,
) -> bool:
    credential = get_user_google_workspace_credential(
        db_session=db_session,
        user=user,
        source=source,
    )
    return credential is not None and credential.credential_json is not None


def _tool_counts_for_internal_server(
    *,
    db_session: Session,
    server: Any | None,
) -> tuple[int, int]:
    if server is None:
        return 0, 0
    tools = get_all_mcp_tools_for_server(server.id, db_session)
    names = {tool.name for tool in tools}
    gmail_count = len(names.intersection(GMAIL_TOOL_NAMES))
    drive_count = len(names.intersection(DRIVE_TOOL_NAMES))
    return gmail_count, drive_count


def _drive_docs_indexed_for_user(
    *,
    db_session: Session,
    user: Any,
) -> int:
    """Count total documents indexed via Google Drive connector CCPs
    associated with this user's credential."""
    from sqlalchemy import func
    from sqlalchemy import select as _sa_select

    from docubrain.db.models import Credential
    from docubrain.db.models import DocumentByConnectorCredentialPair

    # Find the user's Google Drive credential ID
    credential_id = db_session.execute(
        _sa_select(Credential.id).where(
            Credential.user_id == user.id,
            Credential.source == DocumentSource.GOOGLE_DRIVE,
        )
    ).scalar_one_or_none()

    if credential_id is None:
        return 0

    # Count unique documents indexed with this credential
    count = db_session.execute(
        _sa_select(func.count(func.distinct(DocumentByConnectorCredentialPair.id)))
        .where(
            DocumentByConnectorCredentialPair.credential_id == credential_id,
            DocumentByConnectorCredentialPair.has_been_indexed.is_(True),
        )
    ).scalar_one()

    return count or 0


def _extract_google_email(
    *,
    access_token: str,
    fallback_email: str,
) -> str:
    try:
        with httpx.Client(timeout=10.0) as client:
            userinfo_response = client.get(
                GOOGLE_USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if userinfo_response.status_code == 200:
                google_email = userinfo_response.json().get("email")
                if isinstance(google_email, str) and google_email:
                    return google_email
    except Exception:
        logger.warning("Failed to fetch Google userinfo", exc_info=True)
    return fallback_email


@router.get("/connect")
def google_workspace_connect(
    user: User = Depends(require_permission(Permission.MANAGE_ACTIONS)),
) -> RedirectResponse:
    if not GOOGLE_WORKSPACE_MCP_ENABLED:
        raise DocubrainError(
            DocubrainErrorCode.ENV_VAR_GATED,
            "Google Workspace MCP is not enabled",
        )
    if not GOOGLE_WORKSPACE_CLIENT_ID or not GOOGLE_WORKSPACE_CLIENT_SECRET:
        raise DocubrainError(
            DocubrainErrorCode.VALIDATION_ERROR,
            "Google Workspace OAuth credentials are not configured",
        )

    state = create_google_workspace_oauth_state(user)
    params = {
        "client_id": GOOGLE_WORKSPACE_CLIENT_ID,
        "redirect_uri": _redirect_uri(),
        "response_type": "code",
        "scope": " ".join(_all_scopes()),
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": state,
        "login_hint": user.email,
    }
    return RedirectResponse(url=f"{GOOGLE_AUTH_URL}?{httpx.QueryParams(params)}")


@router.get("/callback")
def google_workspace_callback(
    code: str | None = Query(None),
    state: str = Query(...),
    error: str | None = Query(None),
    db: Session = Depends(get_session),
    user: User = Depends(require_permission(Permission.MANAGE_ACTIONS)),
) -> RedirectResponse:
    if error:
        # Check error BEFORE consuming the state token so that an attacker
        # cannot burn a valid state by replaying it with error=access_denied.
        pop_validated_google_workspace_oauth_state(state, user)
        logger.warning(
            "Google Workspace OAuth authorization failed for user_id=%s: %s",
            user.id,
            error,
        )
        return RedirectResponse(
            url=(
                f"{WEB_DOMAIN.rstrip('/')}/admin/actions/mcp"
                "?google_error=authorization_failed"
            ),
            status_code=302,
        )
    pop_validated_google_workspace_oauth_state(state, user)

    if not code:
        logger.warning(
            "Google Workspace OAuth callback missing authorization code for user_id=%s",
            user.id,
        )
        return RedirectResponse(
            url=f"{WEB_DOMAIN.rstrip('/')}/admin/actions/mcp?google_error=missing_code",
            status_code=302,
        )

    try:
        with httpx.Client(timeout=15.0) as client:
            token_response = client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "client_id": GOOGLE_WORKSPACE_CLIENT_ID,
                    "client_secret": GOOGLE_WORKSPACE_CLIENT_SECRET,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": _redirect_uri(),
                },
            )
            token_response.raise_for_status()
            token_data = token_response.json()
    except httpx.HTTPStatusError as e:
        logger.error("Google token exchange failed: status=%s", e.response.status_code)
        raise DocubrainError(
            DocubrainErrorCode.BAD_GATEWAY,
            "Failed to exchange authorization code with Google",
        ) from e
    except Exception as e:
        logger.error("Google token exchange error: %s", e, exc_info=True)
        raise DocubrainError(
            DocubrainErrorCode.BAD_GATEWAY,
            "Failed to communicate with Google OAuth",
        ) from e

    access_token = str(token_data["access_token"])
    refresh_token = token_data.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        refresh_token = _existing_refresh_token(db_session=db, user=user)
        if not refresh_token:
            logger.warning(
                "No refresh token obtained for user_id=%s; "
                "token refresh will fail when access token expires (~1 hour)",
                user.id,
            )
    google_email = _extract_google_email(
        access_token=access_token,
        fallback_email=user.email,
    )
    scopes = token_data.get("scope", "").split() or _all_scopes()

    _store_google_workspace_credentials(
        db_session=db,
        user=user,
        token_data=token_data,
        refresh_token=refresh_token,
        scopes=scopes,
        google_email=google_email,
    )
    bootstrap_result = bootstrap_default_mcp_servers_for_admin(
        db,
        user,
        force=True,
    )
    db.commit()

    logger.info(
        "Google Workspace OAuth connected for user_id=%s gmail_drive_credentials=true "
        "mcp_created_servers=%s mcp_created_tools=%s mcp_updated_tools=%s",
        user.id,
        bootstrap_result.created_servers,
        bootstrap_result.created_tools,
        bootstrap_result.updated_tools,
    )

    return RedirectResponse(
        url=f"{WEB_DOMAIN.rstrip('/')}/admin/actions/mcp?google_connected=true",
        status_code=302,
    )


class GoogleWorkspaceOAuthStatus(BaseModel):
    enabled: bool
    connected: bool
    auth_connected: bool = False
    tools_ready: bool = False
    google_email: str | None = None
    mcp_server_id: int | None = None
    gmail_server_id: int | None = None
    drive_server_id: int | None = None
    gmail_tool_count: int = 0
    drive_tool_count: int = 0
    drive_docs_indexed: int = 0
    tool_registration_error: str | None = None
    scopes: list[str] = []
    token_expired: bool = False
    refresh_available: bool = False
    mcp_drive_docs_indexed: int = 0
    mcp_drive_last_indexed: str | None = None
    mcp_drive_indexing_status: str | None = None


class MCPDriveIndexingStatusResponse(BaseModel):
    mcp_drive_docs_indexed: int = 0
    last_indexing_status: str | None = None
    last_indexing_time: str | None = None
    last_error: str | None = None
    new_docs_indexed: int = 0
    skipped_unchanged: int = 0
    total_chunks: int = 0


@router.get("/status")
def google_workspace_status(
    db: Session = Depends(get_session),
    user: User = Depends(require_permission(Permission.BASIC_ACCESS)),
) -> GoogleWorkspaceOAuthStatus:
    if not GOOGLE_WORKSPACE_MCP_ENABLED:
        return GoogleWorkspaceOAuthStatus(enabled=False, connected=False)

    servers = get_all_mcp_servers(db)
    internal_server = _find_internal_server(servers)
    gmail_connected = _credential_connected(
        db_session=db,
        user=user,
        source=DocumentSource.GMAIL,
    )
    drive_connected = _credential_connected(
        db_session=db,
        user=user,
        source=DocumentSource.GOOGLE_DRIVE,
    )
    gmail_tool_count, drive_tool_count = _tool_counts_for_internal_server(
        db_session=db,
        server=internal_server,
    )
    auth_connected = gmail_connected or drive_connected
    tools_ready = gmail_tool_count > 0 and drive_tool_count > 0
    tool_registration_error = None
    if auth_connected and not tools_ready:
        tool_registration_error = (
            "Google OAuth is connected, but internal MCP tools are not registered."
        )

    google_email = None
    scopes: list[str] = []
    token_expired = False
    refresh_available = False
    credential = get_user_google_workspace_credential(
        db_session=db,
        user=user,
        source=DocumentSource.GMAIL,
    ) or get_user_google_workspace_credential(
        db_session=db,
        user=user,
        source=DocumentSource.GOOGLE_DRIVE,
    )
    if credential is not None and credential.credential_json is not None:
        credential_json = credential.credential_json.get_value(apply_mask=False)
        google_email = credential_json.get(DB_CREDENTIALS_PRIMARY_ADMIN_KEY)
        token_json = credential_json.get(DB_CREDENTIALS_DICT_TOKEN_KEY)
        if isinstance(token_json, str):
            try:
                token_info = json.loads(token_json)
                raw_scopes = token_info.get("scopes", [])
                if isinstance(raw_scopes, list):
                    scopes = [str(scope) for scope in raw_scopes]
                # Check token expiry and refresh availability
                from docubrain.mcp.google_workspace_service import _token_expired
                token_expired = _token_expired(token_info)
                refresh_available = isinstance(
                    token_info.get("refresh_token"), str
                ) and bool(token_info.get("refresh_token"))
            except json.JSONDecodeError:
                logger.warning("Invalid Google Workspace token JSON in credential")

    drive_docs_indexed = _drive_docs_indexed_for_user(db_session=db, user=user)

    # MCP Drive indexing state
    mcp_drive_docs_indexed = 0
    mcp_drive_last_indexed: str | None = None
    mcp_drive_indexing_status: str | None = None
    try:
        from docubrain.db.mcp_drive_sync_state import get_sync_state_for_user

        sync_state = get_sync_state_for_user(db, user.id, "")
        if sync_state is not None:
            mcp_drive_docs_indexed = sync_state.total_docs_indexed
            mcp_drive_indexing_status = sync_state.status
            if sync_state.last_successful_sync_at:
                mcp_drive_last_indexed = (
                    sync_state.last_successful_sync_at.isoformat()
                )
    except Exception:
        logger.debug("Could not fetch MCP Drive sync state", exc_info=True)

    return GoogleWorkspaceOAuthStatus(
        enabled=True,
        connected=auth_connected and tools_ready,
        auth_connected=auth_connected,
        tools_ready=tools_ready,
        google_email=google_email,
        mcp_server_id=internal_server.id if internal_server else None,
        gmail_server_id=internal_server.id if internal_server else None,
        drive_server_id=internal_server.id if internal_server else None,
        gmail_tool_count=gmail_tool_count,
        drive_tool_count=drive_tool_count,
        drive_docs_indexed=drive_docs_indexed,
        tool_registration_error=tool_registration_error,
        scopes=scopes,
        token_expired=token_expired,
        refresh_available=refresh_available,
        mcp_drive_docs_indexed=mcp_drive_docs_indexed,
        mcp_drive_last_indexed=mcp_drive_last_indexed,
        mcp_drive_indexing_status=mcp_drive_indexing_status,
    )


@router.post("/drive-index")
def trigger_drive_mcp_indexing(
    db: Session = Depends(get_session),
    user: User = Depends(require_permission(Permission.MANAGE_ACTIONS)),
) -> MCPDriveIndexingStatusResponse:
    """Trigger MCP Drive indexing for the authenticated user.

    Sets sync state to in_progress before dispatching to avoid UI race,
    then runs indexing synchronously (for now — Celery dispatch in beat task).
    """
    from docubrain.db.mcp_drive_sync_state import get_sync_state_for_user
    from docubrain.db.mcp_drive_sync_state import update_sync_state as _update_sync
    from docubrain.mcp.mcp_drive_indexer import run_mcp_drive_indexing

    try:
        result = run_mcp_drive_indexing(
            db_session=db, user=user, tenant_id="",
        )
    except Exception as e:
        logger.error("MCP Drive indexing trigger failed: %s", e, exc_info=True)
        _update_sync(
            db, user.id, "",
            status="failed",
            last_error=str(e)[:2048],
        )
        db.commit()
        return MCPDriveIndexingStatusResponse(
            last_indexing_status="failed",
            last_error=str(e)[:512],
        )

    sync_state = get_sync_state_for_user(db, user.id, "")
    return MCPDriveIndexingStatusResponse(
        mcp_drive_docs_indexed=sync_state.total_docs_indexed if sync_state else 0,
        last_indexing_status=sync_state.status if sync_state else None,
        last_indexing_time=(
            sync_state.last_successful_sync_at.isoformat()
            if sync_state and sync_state.last_successful_sync_at
            else None
        ),
        last_error=sync_state.last_error if sync_state else None,
        new_docs_indexed=result.new_docs_indexed,
        skipped_unchanged=result.skipped_unchanged,
        total_chunks=result.total_chunks,
    )


@router.get("/drive-index/status")
def get_drive_mcp_indexing_status(
    db: Session = Depends(get_session),
    user: User = Depends(require_permission(Permission.BASIC_ACCESS)),
) -> MCPDriveIndexingStatusResponse:
    """Return the latest MCP Drive indexing status and doc count."""
    from docubrain.db.mcp_drive_sync_state import get_sync_state_for_user

    sync_state = get_sync_state_for_user(db, user.id, "")
    if sync_state is None:
        return MCPDriveIndexingStatusResponse()

    return MCPDriveIndexingStatusResponse(
        mcp_drive_docs_indexed=sync_state.total_docs_indexed,
        last_indexing_status=sync_state.status,
        last_indexing_time=(
            sync_state.last_successful_sync_at.isoformat()
            if sync_state.last_successful_sync_at
            else None
        ),
        last_error=sync_state.last_error,
    )


@router.post("/disconnect")
def google_workspace_disconnect(
    db: Session = Depends(get_session),
    user: User = Depends(require_permission(Permission.MANAGE_ACTIONS)),
) -> dict[str, str]:
    # --- Best-effort Google token revocation ---
    # CRITICAL: Use the Core-SQL-only variant so that the Credential ORM model
    # is NEVER loaded into the session identity map.  Loading it would activate
    # cascade="all, delete-orphan" on Credential.connectors, which cascades
    # NULL into IndexAttempt.connector_credential_pair_id and causes a
    # NotNullViolation on the next autoflush / commit.
    from docubrain.db.google_workspace_mcp import get_user_google_workspace_credential_json_core

    credential_json = get_user_google_workspace_credential_json_core(
        db_session=db,
        user=user,
        source=DocumentSource.GMAIL,
    ) or get_user_google_workspace_credential_json_core(
        db_session=db,
        user=user,
        source=DocumentSource.GOOGLE_DRIVE,
    )
    if credential_json is not None:
        token_json = credential_json.get(DB_CREDENTIALS_DICT_TOKEN_KEY)
        if isinstance(token_json, str):
            try:
                token_info = json.loads(token_json)
                tokens_to_revoke = _tokens_for_revocation(token_info)
                with httpx.Client(timeout=5.0) as client:
                    for token in tokens_to_revoke:
                        client.post(GOOGLE_REVOKE_URL, params={"token": token})
            except Exception:
                logger.warning("Best-effort Google token revocation failed", exc_info=True)

    for source in (DocumentSource.GMAIL, DocumentSource.GOOGLE_DRIVE):
        delete_user_google_workspace_credential(
            db_session=db,
            user=user,
            source=source,
        )

    # Keep the internal Google Workspace MCP server row (so reconnect is fast),
    # but mark it disconnected for UI consistency. Tool definitions are left in
    # place; without valid credentials they simply fail at invocation time and
    # the bootstrap on reconnect re-syncs them.
    internal_server = _find_internal_server(get_all_mcp_servers(db))
    if internal_server is not None:
        update_mcp_server__no_commit(
            server_id=internal_server.id,
            db_session=db,
            status=MCPServerStatus.DISCONNECTED,
        )

    db.commit()
    logger.info("Google Workspace OAuth disconnected for user_id=%s", user.id)
    return {"status": "disconnected"}


def _tokens_for_revocation(token_info: dict[str, Any]) -> list[str]:
    tokens: list[str] = []
    for key in ("refresh_token", "token"):
        token = token_info.get(key)
        if isinstance(token, str) and token and token not in tokens:
            tokens.append(token)
    return tokens
