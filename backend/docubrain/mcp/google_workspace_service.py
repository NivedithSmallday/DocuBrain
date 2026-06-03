import asyncio
from datetime import datetime
from datetime import timedelta
from datetime import timezone
import json
from dataclasses import dataclass
from typing import Any
from typing import Protocol

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials as OAuthCredentials

from docubrain.configs.app_configs import GOOGLE_WORKSPACE_CLIENT_ID
from docubrain.configs.app_configs import GOOGLE_WORKSPACE_CLIENT_SECRET
from docubrain.configs.app_configs import GOOGLE_WORKSPACE_SCOPES
from docubrain.configs.app_configs import OAUTH_GOOGLE_DRIVE_CLIENT_ID
from docubrain.configs.app_configs import OAUTH_GOOGLE_DRIVE_CLIENT_SECRET
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
from docubrain.db.google_workspace_mcp import get_user_google_workspace_credential
from docubrain.db.google_workspace_mcp import update_user_google_workspace_credential_json
from docubrain.error_handling.error_codes import DocubrainErrorCode
from docubrain.error_handling.exceptions import DocubrainError
from docubrain.mcp.providers.base import _optional_string
from docubrain.mcp.schemas import GoogleWorkspaceCredentialHealth


@dataclass(frozen=True)
class GoogleWorkspaceCredentialRef:
    credential_id: int
    credential_json: dict[str, Any]


@dataclass(frozen=True)
class GoogleWorkspaceScopeValidation:
    scopes_valid: bool
    missing_scopes: list[str]


@dataclass(frozen=True)
class GoogleWorkspaceTokenState:
    access_token: str
    refresh_token: str | None
    expired: bool
    scopes: list[str]
    serialized_token_json: str
    source: DocumentSource | None = None

    async def refresh(self) -> "GoogleWorkspaceTokenState":
        if self.source is None:
            raise NotImplementedError("No Google token refresher configured")
        return await asyncio.to_thread(
            _refresh_google_oauth_token_state,
            self.serialized_token_json,
            self.source,
        )


class GoogleWorkspaceCredentialStore(Protocol):
    def get_credential_ref(
        self,
        *,
        db_session: Any,
        user: Any,
        source: DocumentSource,
    ) -> GoogleWorkspaceCredentialRef | None: ...

    def update_credential_json(
        self,
        *,
        db_session: Any,
        credential_id: int,
        user: Any,
        credential_json: dict[str, Any],
    ) -> bool: ...


class DefaultGoogleWorkspaceCredentialStore:
    def get_credential_ref(
        self,
        *,
        db_session: Any,
        user: Any,
        source: DocumentSource,
    ) -> GoogleWorkspaceCredentialRef | None:
        credential = get_user_google_workspace_credential(
            db_session=db_session,
            user=user,
            source=source,
        )
        if credential is None or credential.credential_json is None:
            return None
        return GoogleWorkspaceCredentialRef(
            credential_id=credential.id,
            credential_json=credential.credential_json.get_value(apply_mask=False),
        )

    def update_credential_json(
        self,
        *,
        db_session: Any,
        credential_id: int,
        user: Any,
        credential_json: dict[str, Any],
    ) -> bool:
        return update_user_google_workspace_credential_json(
            db_session=db_session,
            credential_id=credential_id,
            user=user,
            credential_json=credential_json,
        )


_refresh_locks: dict[tuple[str, str, int], asyncio.Lock] = {}
_refresh_results: dict[tuple[str, str, int], GoogleWorkspaceTokenState] = {}
_MAX_REFRESH_CACHE_ENTRIES = 500


def _evict_expired_refresh_cache() -> None:
    """Remove expired entries and their locks to prevent unbounded growth."""
    expired_keys = [k for k, v in _refresh_results.items() if v.expired]
    for k in expired_keys:
        _refresh_results.pop(k, None)
        _refresh_locks.pop(k, None)
    # If still over limit after evicting expired, remove oldest entries
    while len(_refresh_results) >= _MAX_REFRESH_CACHE_ENTRIES:
        oldest_key = next(iter(_refresh_results))
        _refresh_results.pop(oldest_key, None)
        _refresh_locks.pop(oldest_key, None)
    # Clean up orphaned locks (locks with no corresponding result)
    orphaned_lock_keys = set(_refresh_locks) - set(_refresh_results)
    for k in orphaned_lock_keys:
        _refresh_locks.pop(k, None)


def required_google_workspace_scopes(source: DocumentSource) -> list[str]:
    _ = source
    return list(GOOGLE_WORKSPACE_SCOPES)


def validate_google_workspace_scopes(
    scopes: list[str],
    source: DocumentSource,
) -> GoogleWorkspaceScopeValidation:
    scope_set = set(scopes)
    missing_scopes = [
        scope
        for scope in required_google_workspace_scopes(source)
        if scope not in scope_set
    ]
    return GoogleWorkspaceScopeValidation(
        scopes_valid=not missing_scopes,
        missing_scopes=missing_scopes,
    )


def _verify_google_token_live(access_token: str, source: DocumentSource) -> bool:
    """Make a lightweight Google API call to verify the token is accepted.

    Returns True if the token is valid, False otherwise.
    """
    import httpx

    if source is DocumentSource.GMAIL:
        url = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
    else:
        url = "https://www.googleapis.com/drive/v3/about?fields=user"

    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(
                url, headers={"Authorization": f"Bearer {access_token}"}
            )
            return 200 <= response.status_code < 300
    except Exception:
        return False


def get_google_workspace_credential_health(
    *,
    db_session: Any,
    user: Any,
    source: DocumentSource,
    request_id: str,
    credential_store: GoogleWorkspaceCredentialStore | None = None,
) -> GoogleWorkspaceCredentialHealth:
    provider = _provider_for_source(source)
    store = credential_store or DefaultGoogleWorkspaceCredentialStore()
    credential_ref = store.get_credential_ref(
        db_session=db_session,
        user=user,
        source=source,
    )
    if credential_ref is None:
        return GoogleWorkspaceCredentialHealth(
            provider=provider,
            connected=False,
            token_expired=False,
            refresh_available=False,
            scopes_valid=False,
            missing_scopes=required_google_workspace_scopes(source),
            request_id=request_id,
        )

    token_info = _token_info_from_credential_json(credential_ref.credential_json)
    scopes = _scopes_from_token_info(token_info)
    scope_validation = validate_google_workspace_scopes(scopes, source)
    token_expired = _token_expired(token_info)

    # If the token doesn't appear expired locally, verify it's still
    # accepted by Google (catches revoked tokens).
    token_revoked = False
    if not token_expired:
        access_token = token_info.get("token")
        if isinstance(access_token, str) and access_token:
            token_valid = _verify_google_token_live(access_token, source)
            if not token_valid:
                token_revoked = True
                token_expired = True

    return GoogleWorkspaceCredentialHealth(
        provider=provider,
        connected=True and not token_revoked,
        token_expired=token_expired,
        refresh_available=isinstance(token_info.get("refresh_token"), str),
        scopes_valid=scope_validation.scopes_valid,
        missing_scopes=scope_validation.missing_scopes,
        request_id=request_id,
    )


async def get_valid_google_workspace_token_state(
    *,
    db_session: Any,
    user: Any,
    source: DocumentSource,
    credential_store: GoogleWorkspaceCredentialStore | None = None,
    token_state_factory: Any | None = None,
) -> GoogleWorkspaceTokenState:
    store = credential_store or DefaultGoogleWorkspaceCredentialStore()
    credential_ref = store.get_credential_ref(
        db_session=db_session,
        user=user,
        source=source,
    )
    if credential_ref is None:
        raise DocubrainError(
            DocubrainErrorCode.CREDENTIAL_NOT_FOUND,
            f"{_provider_for_source(source)} is not connected for this user",
        )

    token_json = _token_json_from_credential_json(credential_ref.credential_json)
    token_state = await _build_token_state(token_json, source, token_state_factory)
    _raise_if_scopes_invalid(token_state.scopes, source)

    if not token_state.expired:
        return token_state

    if not token_state.refresh_token:
        raise DocubrainError(
            DocubrainErrorCode.CREDENTIAL_EXPIRED,
            f"{_provider_for_source(source)} credential is expired and cannot refresh",
        )

    lock_key = (str(user.id), source.value, credential_ref.credential_id)
    cached_result = _refresh_results.get(lock_key)
    if cached_result and not cached_result.expired:
        return cached_result

    lock = _refresh_locks.setdefault(lock_key, asyncio.Lock())
    async with lock:
        cached_result = _refresh_results.get(lock_key)
        if cached_result and not cached_result.expired:
            return cached_result

        refreshed_state = await token_state.refresh()
        _raise_if_scopes_invalid(refreshed_state.scopes, source)
        _persist_refreshed_token(
            store=store,
            db_session=db_session,
            user=user,
            credential_ref=credential_ref,
            refreshed_state=refreshed_state,
        )
        if len(_refresh_results) >= _MAX_REFRESH_CACHE_ENTRIES:
            _evict_expired_refresh_cache()
        _refresh_results[lock_key] = refreshed_state
        return refreshed_state


async def _build_token_state(
    token_json: str,
    source: DocumentSource,
    token_state_factory: Any | None,
) -> GoogleWorkspaceTokenState:
    if token_state_factory is not None:
        return await token_state_factory(token_json, source)

    token_info = json.loads(token_json)
    return GoogleWorkspaceTokenState(
        access_token=str(token_info.get("token", "")),
        refresh_token=_optional_string(token_info.get("refresh_token")),
        expired=_token_expired(token_info),
        scopes=_scopes_from_token_info(token_info),
        serialized_token_json=token_json,
        source=source,
    )


def _persist_refreshed_token(
    *,
    store: GoogleWorkspaceCredentialStore,
    db_session: Any,
    user: Any,
    credential_ref: GoogleWorkspaceCredentialRef,
    refreshed_state: GoogleWorkspaceTokenState,
) -> None:
    next_credential_json = dict(credential_ref.credential_json)
    next_credential_json[DB_CREDENTIALS_DICT_TOKEN_KEY] = (
        refreshed_state.serialized_token_json
    )
    for key in (
        DB_CREDENTIALS_PRIMARY_ADMIN_KEY,
        DB_CREDENTIALS_AUTHENTICATION_METHOD,
    ):
        if key in credential_ref.credential_json:
            next_credential_json[key] = credential_ref.credential_json[key]
    store.update_credential_json(
        db_session=db_session,
        credential_id=credential_ref.credential_id,
        user=user,
        credential_json=next_credential_json,
    )


def _raise_if_scopes_invalid(scopes: list[str], source: DocumentSource) -> None:
    scope_validation = validate_google_workspace_scopes(scopes, source)
    if not scope_validation.scopes_valid:
        raise DocubrainError(
            DocubrainErrorCode.CREDENTIAL_INVALID,
            (
                f"{_provider_for_source(source)} credential is missing required scopes: "
                + ", ".join(scope_validation.missing_scopes)
            ),
        )


def _token_json_from_credential_json(credential_json: dict[str, Any]) -> str:
    token_json = credential_json.get(DB_CREDENTIALS_DICT_TOKEN_KEY)
    if not isinstance(token_json, str):
        raise DocubrainError(
            DocubrainErrorCode.CREDENTIAL_INVALID,
            "Google Workspace credential is missing OAuth token data",
        )
    return token_json


def _token_info_from_credential_json(credential_json: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(_token_json_from_credential_json(credential_json))
    except json.JSONDecodeError as exc:
        raise DocubrainError(
            DocubrainErrorCode.CREDENTIAL_INVALID,
            "Google Workspace credential token data is invalid",
        ) from exc


def _scopes_from_token_info(token_info: dict[str, Any]) -> list[str]:
    scopes = token_info.get("scopes")
    if isinstance(scopes, list):
        return [str(scope) for scope in scopes]
    if isinstance(scopes, str):
        return [scope for scope in scopes.split() if scope]
    return []


def _token_expired(token_info: dict[str, Any]) -> bool:
    expiry = token_info.get("expiry")
    if not isinstance(expiry, str) or not expiry:
        return True
    try:
        expiry_at = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
    except ValueError:
        return True
    if expiry_at.tzinfo is None or expiry_at.utcoffset() is None:
        expiry_at = expiry_at.replace(tzinfo=timezone.utc)
    return expiry_at <= datetime.now(timezone.utc) + timedelta(seconds=60)


def _refresh_google_oauth_token_state(
    token_json: str,
    source: DocumentSource,
) -> GoogleWorkspaceTokenState:
    token_info = json.loads(token_json)
    authorized_user_info = dict(token_info)
    client_id = (
        _optional_string(authorized_user_info.get("client_id"))
        or GOOGLE_WORKSPACE_CLIENT_ID
        or OAUTH_GOOGLE_DRIVE_CLIENT_ID
    )
    client_secret = (
        _optional_string(authorized_user_info.get("client_secret"))
        or GOOGLE_WORKSPACE_CLIENT_SECRET
        or OAUTH_GOOGLE_DRIVE_CLIENT_SECRET
    )
    if client_id:
        authorized_user_info["client_id"] = client_id
    if client_secret:
        authorized_user_info["client_secret"] = client_secret

    creds = OAuthCredentials.from_authorized_user_info(
        info=authorized_user_info,
        scopes=required_google_workspace_scopes(source),
    )
    try:
        creds.refresh(Request())
    except Exception as exc:
        raise DocubrainError(
            DocubrainErrorCode.BAD_GATEWAY,
            f"Failed to refresh {_provider_for_source(source)} OAuth token",
        ) from exc

    if not creds.valid or not creds.token:
        raise DocubrainError(
            DocubrainErrorCode.CREDENTIAL_EXPIRED,
            f"{_provider_for_source(source)} credential could not be refreshed",
        )

    refreshed_token_info = json.loads(creds.to_json())
    if creds.refresh_token is None and token_info.get("refresh_token"):
        refreshed_token_info["refresh_token"] = token_info["refresh_token"]
    refreshed_token_info.pop("client_id", None)
    refreshed_token_info.pop("client_secret", None)
    refreshed_token_info["scopes"] = _scopes_from_token_info(token_info)
    serialized_token_json = json.dumps(refreshed_token_info)
    return GoogleWorkspaceTokenState(
        access_token=creds.token,
        refresh_token=_optional_string(refreshed_token_info.get("refresh_token")),
        expired=_token_expired(refreshed_token_info),
        scopes=_scopes_from_token_info(refreshed_token_info),
        serialized_token_json=serialized_token_json,
        source=source,
    )


def _provider_for_source(source: DocumentSource) -> str:
    if source is DocumentSource.GMAIL:
        return "gmail"
    if source is DocumentSource.GOOGLE_DRIVE:
        return "drive"
    return source.value



# ---------------------------------------------------------------------------
# httpx-based JSON transport
# ---------------------------------------------------------------------------

GOOGLE_API_TIMEOUT_SECONDS: float = 25.0


class HttpxJSONTransport:
    """HTTP transport that implements the HTTPJSONTransport protocol using httpx."""

    def __init__(
        self,
        *,
        timeout: float = GOOGLE_API_TIMEOUT_SECONDS,
    ) -> None:
        self._timeout = timeout
        self.last_status_code: int | None = None

    async def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        import httpx

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json_body,
            )
            self.last_status_code = response.status_code
            try:
                return response.json()  # type: ignore[no-any-return]
            except Exception:
                return {"error": {"message": f"Non-JSON response: {response.status_code}"}}

    async def request_text(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
    ) -> str:
        import httpx

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.request(
                method,
                url,
                headers=headers,
                params=params,
            )
            self.last_status_code = response.status_code
            return response.text


# ---------------------------------------------------------------------------
# Full orchestration: credential -> client -> operation -> truncation -> audit
# ---------------------------------------------------------------------------


async def execute_google_workspace_operation(
    *,
    db_session: Any,
    user: Any,
    source: DocumentSource,
    capability: Any,
    arguments: dict[str, Any],
    request_id: str,
    credential_store: GoogleWorkspaceCredentialStore | None = None,
    token_state_factory: Any | None = None,
    transport_factory: Any | None = None,
) -> "GoogleWorkspaceOperationResult":
    """End-to-end orchestration for a Google Workspace MCP operation.

    1. Load and validate user credential
    2. Refresh token under lock if expired
    3. Create Google REST client with httpx transport
    4. Execute the requested capability
    5. Apply response truncation
    6. Emit audit event and record metrics
    7. Return normalized result with truncation metadata
    """
    import time

    from docubrain.mcp.clients.google_workspace import GoogleWorkspaceRESTClient
    from docubrain.mcp.observability import build_safe_audit_event
    from docubrain.mcp.observability import emit_audit_log
    from docubrain.mcp.observability import record_google_workspace_latency
    from docubrain.mcp.observability import record_google_workspace_metric
    from docubrain.mcp.response_limits import cap_thread_messages
    from docubrain.mcp.response_limits import truncate_body

    provider = _provider_for_source(source)
    start_time = time.monotonic()
    upstream_status: int | None = None
    success = False

    try:
        token_state = await get_valid_google_workspace_token_state(
            db_session=db_session,
            user=user,
            source=source,
            credential_store=credential_store,
            token_state_factory=token_state_factory,
        )

        if transport_factory is not None:
            transport = transport_factory()
        else:
            transport = HttpxJSONTransport()

        client = GoogleWorkspaceRESTClient(
            access_token=token_state.access_token,
            transport=transport,
            request_id=request_id,
        )

        result = await client.execute_tool(capability, arguments)
        upstream_status = result.upstream_status_code

        # Apply truncation to message body fields
        truncation_metadata: dict[str, Any] = {}
        truncated_records: list[dict[str, Any]] = []
        for record in result.records:
            record_copy = dict(record)
            if "body_text" in record_copy:
                tr = truncate_body(record_copy.get("body_text"))  # type: ignore[arg-type]
                record_copy["body_text"] = tr.text
                if tr.is_truncated:
                    truncation_metadata["body_text_truncated"] = True
                    truncation_metadata["body_text_original_size"] = tr.original_size
                    truncation_metadata["body_text_returned_size"] = tr.returned_size
            if "body_html" in record_copy:
                tr = truncate_body(record_copy.get("body_html"))  # type: ignore[arg-type]
                record_copy["body_html"] = tr.text
                if tr.is_truncated:
                    truncation_metadata["body_html_truncated"] = True
                    truncation_metadata["body_html_original_size"] = tr.original_size
                    truncation_metadata["body_html_returned_size"] = tr.returned_size
            record_copy = _add_attachment_placeholders(record_copy)
            truncated_records.append(record_copy)

        # Cap thread messages
        capped_records, was_capped, original_count = cap_thread_messages(
            truncated_records
        )
        if was_capped:
            truncation_metadata["thread_messages_capped"] = True
            truncation_metadata["thread_messages_original_count"] = original_count
            truncation_metadata["thread_messages_returned_count"] = len(capped_records)

        success = True
        return GoogleWorkspaceOperationResult(
            records=capped_records,
            next_page_token=result.next_page_token,
            upstream_status_code=upstream_status,
            request_id=request_id,
            provider=provider,
            truncation_metadata=truncation_metadata if truncation_metadata else None,
        )

    finally:
        duration = time.monotonic() - start_time
        record_google_workspace_metric(
            provider=provider,
            operation=str(capability),
            success=success,
            duration_seconds=duration,
        )
        record_google_workspace_latency(
            provider=provider,
            operation=str(capability),
            duration_seconds=duration,
            request_id=request_id,
        )
        audit_event = build_safe_audit_event(
            user_id=str(getattr(user, "id", "unknown")),
            provider=provider,
            tool=str(capability),
            request_id=request_id,
            success=success,
            upstream_status_code=upstream_status,
        )
        emit_audit_log(audit_event)


@dataclass(frozen=True)
class GoogleWorkspaceOperationResult:
    """Result container with truncation metadata for API responses."""

    records: list[dict[str, Any]]
    next_page_token: str | None = None
    upstream_status_code: int | None = None
    request_id: str = ""
    provider: str = ""
    truncation_metadata: dict[str, Any] | None = None


def _add_attachment_placeholders(record: dict[str, Any]) -> dict[str, Any]:
    """Add attachment existence metadata without including attachment content.

    Even before download support, this preserves attachment metadata so callers
    know attachments exist. Attachment content is never included.
    """
    if "attachments" not in record and "attachment_count" not in record:
        return record
    attachments = record.get("attachments")
    if isinstance(attachments, list):
        safe_attachments = []
        for att in attachments:
            if isinstance(att, dict):
                safe_attachments.append({
                    "filename": att.get("filename", ""),
                    "mime_type": att.get("mime_type", att.get("mimeType", "")),
                    "size": att.get("size", 0),
                    "download_available": False,
                })
        record["attachments"] = safe_attachments
        record["attachment_count"] = len(safe_attachments)
    return record
