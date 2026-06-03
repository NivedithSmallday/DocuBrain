import asyncio
from collections.abc import Callable
from datetime import UTC
from datetime import datetime
from dataclasses import dataclass
from typing import Any
from typing import Protocol

from docubrain.error_handling.error_codes import DocubrainErrorCode
from docubrain.error_handling.exceptions import DocubrainError
from docubrain.utils.logger import setup_logger

logger = setup_logger()
from docubrain.mcp.clients.gmail_mime import extract_gmail_message_body
from docubrain.mcp.providers.base import _optional_string
from docubrain.mcp.schemas import GmailRetrievalCapability
from docubrain.mcp.schemas import ProviderCapability

GMAIL_MESSAGES_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
GMAIL_THREADS_URL = "https://gmail.googleapis.com/gmail/v1/users/me/threads"
DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
DRIVE_DRIVES_URL = "https://www.googleapis.com/drive/v3/drives"
RETRYABLE_GOOGLE_STATUS_CODES = {429, 500, 502, 503, 504}


class HTTPJSONTransport(Protocol):
    async def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return a decoded JSON response for an HTTP request."""

    async def request_text(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
    ) -> str:
        """Return a text response for export endpoints."""


@dataclass(frozen=True)
class GoogleWorkspaceRetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 0.25


@dataclass(frozen=True)
class GoogleWorkspaceClientResult:
    records: list[dict[str, Any]]
    next_page_token: str | None = None
    upstream_status_code: int | None = None

    def __iter__(self) -> Any:
        return iter(self.records)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, list):
            return self.records == other
        return super().__eq__(other)


class GoogleWorkspaceRESTClient:
    def __init__(
        self,
        *,
        access_token: str,
        transport: HTTPJSONTransport,
        request_id: str | None = None,
        retry_policy: GoogleWorkspaceRetryPolicy | None = None,
        sleep: Callable[[float], Any] | None = None,
    ) -> None:
        self._access_token = access_token
        self._transport = transport
        self._request_id = request_id
        self._retry_policy = retry_policy or GoogleWorkspaceRetryPolicy()
        self._sleep = sleep

    async def execute_tool(
        self,
        capability: ProviderCapability,
        arguments: dict[str, Any],
    ) -> GoogleWorkspaceClientResult:
        if capability is ProviderCapability.SEARCH_EMAILS:
            return await self._search_emails(arguments)
        if capability is ProviderCapability.SEARCH_DOCS:
            return await self._search_docs(arguments)
        if capability is GmailRetrievalCapability.GET_GMAIL_MESSAGE:
            return await self._get_gmail_message(arguments)
        if capability is GmailRetrievalCapability.GET_GMAIL_THREAD:
            return await self._get_gmail_thread(arguments)
        if capability is GmailRetrievalCapability.LIST_GMAIL_THREADS:
            return await self._list_gmail_threads(arguments)
        if capability is GmailRetrievalCapability.SUMMARIZE_EMAIL_THREAD:
            return await self._summarize_email_thread(arguments)
        if capability is GmailRetrievalCapability.SEARCH_GMAIL_ATTACHMENTS:
            return await self._search_gmail_attachments(arguments)
        if capability is ProviderCapability.GET_DRIVE_FILE_METADATA:
            return await self._get_drive_file_metadata(arguments)
        if capability in (
            ProviderCapability.READ_GOOGLE_DOC,
            ProviderCapability.EXPORT_GOOGLE_DOC,
        ):
            return await self._export_google_doc(arguments)
        if capability is ProviderCapability.LIST_SHARED_DRIVES:
            return await self._list_shared_drives(arguments)
        if capability is ProviderCapability.SEARCH_DRIVE_CONTENT:
            return await self._search_docs(arguments)
        if capability is ProviderCapability.LIST_RECENT_FILES:
            return await self._list_recent_files(arguments)
        if capability is ProviderCapability.GET_FILE_PERMISSIONS:
            return await self._get_file_permissions(arguments)
        if capability is ProviderCapability.LIST_FOLDER_CONTENTS:
            return await self._list_folder_contents(arguments)

        raise DocubrainError(
            DocubrainErrorCode.INVALID_INPUT,
            f"Unsupported Google Workspace capability: {capability.value}",
        )

    async def _search_emails(
        self,
        arguments: dict[str, Any],
    ) -> GoogleWorkspaceClientResult:
        query = _required_string(arguments, "query")
        limit = _limit(arguments)
        page_token = _optional_string(arguments.get("page_token"))

        params: dict[str, Any] = {"q": query, "maxResults": limit}
        if page_token:
            params["pageToken"] = page_token

        list_response = await self._request_json_with_retries(
            "GET",
            GMAIL_MESSAGES_URL,
            headers=self._auth_headers(),
            params=params,
        )

        # Gmail's list endpoint only returns message IDs, so each message
        # requires a separate GET.  We fetch them concurrently to reduce
        # total latency (previously sequential N+1).
        message_refs = list_response.payload.get("messages", [])
        message_ids: list[str] = []
        for message_ref in message_refs[:limit]:
            if not isinstance(message_ref, dict):
                continue
            message_id = message_ref.get("id")
            if isinstance(message_id, str):
                message_ids.append(message_id)

        async def _fetch_message(mid: str) -> dict[str, Any]:
            resp = await self._request_json_with_retries(
                "GET",
                f"{GMAIL_MESSAGES_URL}/{mid}",
                headers=self._auth_headers(),
                params={"format": "metadata", "metadataHeaders": "Subject"},
            )
            return _gmail_message_to_record(resp.payload)

        results = await asyncio.gather(*[_fetch_message(mid) for mid in message_ids])

        return GoogleWorkspaceClientResult(
            records=list(results),
            next_page_token=_optional_string(list_response.payload.get("nextPageToken")),
            upstream_status_code=list_response.status_code,
        )

    async def _search_docs(
        self,
        arguments: dict[str, Any],
    ) -> GoogleWorkspaceClientResult:
        query = _required_string(arguments, "query")
        limit = _limit(arguments)
        page_token = _optional_string(arguments.get("page_token"))

        params: dict[str, Any] = {
            "q": f"fullText contains '{_escape_drive_query(query)}' and trashed = false",
            "pageSize": limit,
            "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,webViewLink)",
            "orderBy": "modifiedTime desc",
        }
        if page_token:
            params["pageToken"] = page_token

        response = await self._request_json_with_retries(
            "GET",
            DRIVE_FILES_URL,
            headers=self._auth_headers(),
            params=params,
        )

        return GoogleWorkspaceClientResult(
            records=[
                _drive_file_to_record(file)
                for file in response.payload.get("files", [])
                if isinstance(file, dict)
            ],
            next_page_token=_optional_string(response.payload.get("nextPageToken")),
            upstream_status_code=response.status_code,
        )

    async def _get_gmail_message(
        self,
        arguments: dict[str, Any],
    ) -> GoogleWorkspaceClientResult:
        message_id = _required_string(arguments, "message_id")
        response = await self._request_json_with_retries(
            "GET",
            f"{GMAIL_MESSAGES_URL}/{message_id}",
            headers=self._auth_headers(),
            params={"format": "full"},
        )
        return GoogleWorkspaceClientResult(
            records=[_gmail_full_message_to_record(response.payload)],
            upstream_status_code=response.status_code,
        )

    async def _get_gmail_thread(
        self,
        arguments: dict[str, Any],
    ) -> GoogleWorkspaceClientResult:
        thread_id = _required_string(arguments, "thread_id")
        page_token = _optional_string(arguments.get("page_token"))
        params: dict[str, Any] = {"format": "full"}
        if page_token:
            params["pageToken"] = page_token
        response = await self._request_json_with_retries(
            "GET",
            f"{GMAIL_THREADS_URL}/{thread_id}",
            headers=self._auth_headers(),
            params=params,
        )
        messages = response.payload.get("messages", [])
        return GoogleWorkspaceClientResult(
            records=[
                _gmail_full_message_to_record(message)
                for message in messages
                if isinstance(message, dict)
            ],
            next_page_token=_optional_string(response.payload.get("nextPageToken")),
            upstream_status_code=response.status_code,
        )

    async def _list_gmail_threads(
        self,
        arguments: dict[str, Any],
    ) -> GoogleWorkspaceClientResult:
        query = _optional_string(arguments.get("query"))
        limit = _limit(arguments)
        page_token = _optional_string(arguments.get("page_token"))
        params: dict[str, Any] = {"maxResults": limit}
        if query:
            params["q"] = query
        if page_token:
            params["pageToken"] = page_token
        response = await self._request_json_with_retries(
            "GET",
            GMAIL_THREADS_URL,
            headers=self._auth_headers(),
            params=params,
        )
        return GoogleWorkspaceClientResult(
            records=[
                _gmail_thread_ref_to_record(thread)
                for thread in response.payload.get("threads", [])
                if isinstance(thread, dict)
            ],
            next_page_token=_optional_string(response.payload.get("nextPageToken")),
            upstream_status_code=response.status_code,
        )

    async def _summarize_email_thread(
        self,
        arguments: dict[str, Any],
    ) -> GoogleWorkspaceClientResult:
        thread_result = await self._get_gmail_thread(arguments)
        return GoogleWorkspaceClientResult(
            records=[_summarize_thread_records(thread_result.records)],
            next_page_token=thread_result.next_page_token,
            upstream_status_code=thread_result.upstream_status_code,
        )

    async def _search_gmail_attachments(
        self,
        arguments: dict[str, Any],
    ) -> GoogleWorkspaceClientResult:
        query = _required_string(arguments, "query")
        limit = _limit(arguments)
        page_token = _optional_string(arguments.get("page_token"))
        gmail_query = query if "has:attachment" in query else f"{query} has:attachment"
        params: dict[str, Any] = {"q": gmail_query, "maxResults": limit}
        if page_token:
            params["pageToken"] = page_token
        list_response = await self._request_json_with_retries(
            "GET",
            GMAIL_MESSAGES_URL,
            headers=self._auth_headers(),
            params=params,
        )
        message_ids: list[str] = []
        for message_ref in list_response.payload.get("messages", []):
            if not isinstance(message_ref, dict):
                continue
            message_id = message_ref.get("id")
            if isinstance(message_id, str):
                message_ids.append(message_id)

        async def _fetch_attachments(mid: str) -> list[dict[str, Any]]:
            resp = await self._request_json_with_retries(
                "GET",
                f"{GMAIL_MESSAGES_URL}/{mid}",
                headers=self._auth_headers(),
                params={"format": "full"},
            )
            return _gmail_attachment_records(resp.payload)

        attachment_lists = await asyncio.gather(
            *[_fetch_attachments(mid) for mid in message_ids]
        )
        records: list[dict[str, Any]] = []
        for attachment_list in attachment_lists:
            records.extend(attachment_list)
        return GoogleWorkspaceClientResult(
            records=records,
            next_page_token=_optional_string(list_response.payload.get("nextPageToken")),
            upstream_status_code=list_response.status_code,
        )

    async def _get_drive_file_metadata(
        self,
        arguments: dict[str, Any],
    ) -> GoogleWorkspaceClientResult:
        file_id = _required_string(arguments, "file_id")
        response = await self._request_json_with_retries(
            "GET",
            f"{DRIVE_FILES_URL}/{file_id}",
            headers=self._auth_headers(),
            params={
                "fields": (
                    "id,name,mimeType,description,modifiedTime,createdTime,webViewLink,"
                    "size,owners,driveId,parents,shortcutDetails,shared,trashed"
                ),
                "supportsAllDrives": True,
            },
        )
        return GoogleWorkspaceClientResult(
            records=[_drive_metadata_to_record(response.payload)],
            upstream_status_code=response.status_code,
        )

    async def _export_google_doc(
        self,
        arguments: dict[str, Any],
    ) -> GoogleWorkspaceClientResult:
        file_id = _required_string(arguments, "file_id")
        mime_type = _optional_string(arguments.get("mime_type")) or "text/plain"
        params: dict[str, str] = {"mimeType": mime_type}
        # Support exporting a specific sheet tab via gid
        gid = _optional_string(arguments.get("gid")) or _optional_string(
            arguments.get("sheet_id")
        )
        if gid:
            params["gid"] = gid
        text = await self._request_text_with_retries(
            "GET",
            f"{DRIVE_FILES_URL}/{file_id}/export",
            headers=self._auth_headers(),
            params=params,
        )
        source_url = f"https://drive.google.com/file/d/{file_id}/view"
        if gid:
            source_url = f"https://docs.google.com/spreadsheets/d/{file_id}/edit?gid={gid}#gid={gid}"
        return GoogleWorkspaceClientResult(
            records=[
                {
                    "file_id": file_id,
                    "mime_type": mime_type,
                    "content": text,
                    "source_url": source_url,
                }
            ],
            upstream_status_code=_transport_status_code(self._transport),
        )

    async def _get_spreadsheet_sheet_ids(
        self,
        file_id: str,
    ) -> list[dict[str, Any]]:
        """Fetch all sheet tabs (name + gid) from Google Sheets API.

        Returns a list of dicts with ``title`` and ``sheetId`` keys.
        Falls back to an empty list on error (caller should export without gid).
        """
        url = f"https://sheets.googleapis.com/v4/spreadsheets/{file_id}"
        try:
            response = await self._request_json_with_retries(
                "GET",
                url,
                headers=self._auth_headers(),
                params={"fields": "sheets.properties(sheetId,title)"},
            )
            sheets_data = response.payload.get("sheets", [])
            return [
                {
                    "title": s.get("properties", {}).get("title", f"Sheet{i}"),
                    "sheetId": str(s.get("properties", {}).get("sheetId", "0")),
                }
                for i, s in enumerate(sheets_data)
            ]
        except Exception as e:
            logger.warning(
                "Failed to list spreadsheet tabs for %s: %s", file_id, e
            )
            return []

    async def _export_all_sheets_as_csv(
        self,
        file_id: str,
    ) -> list[dict[str, Any]]:
        """Export all tabs of a Google Sheet as separate CSV texts.

        Returns a list of dicts with ``sheet_name``, ``gid``, and ``content``.
        """
        tabs = await self._get_spreadsheet_sheet_ids(file_id)
        if not tabs:
            # Fallback: export without gid (first sheet only)
            text = await self._request_text_with_retries(
                "GET",
                f"{DRIVE_FILES_URL}/{file_id}/export",
                headers=self._auth_headers(),
                params={"mimeType": "text/csv"},
            )
            return [{"sheet_name": "Sheet1", "gid": "0", "content": text}]

        results: list[dict[str, Any]] = []
        for tab in tabs:
            try:
                text = await self._request_text_with_retries(
                    "GET",
                    f"{DRIVE_FILES_URL}/{file_id}/export",
                    headers=self._auth_headers(),
                    params={"mimeType": "text/csv", "gid": tab["sheetId"]},
                )
                if text and text.strip():
                    results.append({
                        "sheet_name": tab["title"],
                        "gid": tab["sheetId"],
                        "content": text,
                    })
            except Exception as e:
                logger.warning(
                    "Failed to export tab %s (gid=%s) of %s: %s",
                    tab["title"], tab["sheetId"], file_id, e,
                )
        return results

    async def _list_shared_drives(
        self,
        arguments: dict[str, Any],
    ) -> GoogleWorkspaceClientResult:
        limit = _limit(arguments)
        page_token = _optional_string(arguments.get("page_token"))
        params: dict[str, Any] = {
            "pageSize": limit,
            "fields": "nextPageToken,drives(id,name,createdTime,hidden,restrictions)",
        }
        if page_token:
            params["pageToken"] = page_token
        response = await self._request_json_with_retries(
            "GET",
            DRIVE_DRIVES_URL,
            headers=self._auth_headers(),
            params=params,
        )
        return GoogleWorkspaceClientResult(
            records=[
                _shared_drive_to_record(drive)
                for drive in response.payload.get("drives", [])
                if isinstance(drive, dict)
            ],
            next_page_token=_optional_string(response.payload.get("nextPageToken")),
            upstream_status_code=response.status_code,
        )

    async def _list_recent_files(
        self,
        arguments: dict[str, Any],
    ) -> GoogleWorkspaceClientResult:
        return await self._drive_file_query(
            q="trashed = false",
            limit=_limit(arguments),
            page_token=_optional_string(arguments.get("page_token")),
            order_by="modifiedTime desc",
        )

    async def _list_folder_contents(
        self,
        arguments: dict[str, Any],
    ) -> GoogleWorkspaceClientResult:
        folder_id = _required_string(arguments, "folder_id")
        return await self._drive_file_query(
            q=f"'{_escape_drive_query(folder_id)}' in parents and trashed = false",
            limit=_limit(arguments),
            page_token=_optional_string(arguments.get("page_token")),
            order_by="folder,name",
        )

    async def _get_file_permissions(
        self,
        arguments: dict[str, Any],
    ) -> GoogleWorkspaceClientResult:
        file_id = _required_string(arguments, "file_id")
        response = await self._request_json_with_retries(
            "GET",
            f"{DRIVE_FILES_URL}/{file_id}/permissions",
            headers=self._auth_headers(),
            params={
                "fields": (
                    "permissions(id,type,role,emailAddress,domain,displayName,"
                    "allowFileDiscovery,expirationTime),nextPageToken"
                ),
                "supportsAllDrives": True,
            },
        )
        return GoogleWorkspaceClientResult(
            records=[
                permission
                for permission in response.payload.get("permissions", [])
                if isinstance(permission, dict)
            ],
            next_page_token=_optional_string(response.payload.get("nextPageToken")),
            upstream_status_code=response.status_code,
        )

    async def _drive_file_query(
        self,
        *,
        q: str,
        limit: int,
        page_token: str | None,
        order_by: str,
    ) -> GoogleWorkspaceClientResult:
        params: dict[str, Any] = {
            "q": q,
            "pageSize": limit,
            "fields": (
                "nextPageToken,files(id,name,mimeType,modifiedTime,webViewLink,"
                "size,driveId,parents)"
            ),
            "orderBy": order_by,
            "includeItemsFromAllDrives": True,
            "supportsAllDrives": True,
        }
        if page_token:
            params["pageToken"] = page_token
        response = await self._request_json_with_retries(
            "GET",
            DRIVE_FILES_URL,
            headers=self._auth_headers(),
            params=params,
        )
        return GoogleWorkspaceClientResult(
            records=[
                _drive_file_to_record(file)
                for file in response.payload.get("files", [])
                if isinstance(file, dict)
            ],
            next_page_token=_optional_string(response.payload.get("nextPageToken")),
            upstream_status_code=response.status_code,
        )

    async def _request_json_with_retries(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> "_GoogleResponse":
        attempt = 0
        while True:
            attempt += 1
            payload = await self._transport.request_json(
                method,
                url,
                headers=headers,
                params=params,
                json_body=json_body,
            )
            status_code = _transport_status_code(self._transport)
            if status_code is None or 200 <= status_code < 300:
                return _GoogleResponse(payload=payload, status_code=status_code)
            if (
                status_code not in RETRYABLE_GOOGLE_STATUS_CODES
                or attempt >= self._retry_policy.max_attempts
            ):
                raise DocubrainError(
                    _error_code_for_google_status(status_code),
                    _google_error_detail(payload, status_code),
                    status_code_override=status_code,
                )
            await self._sleep_for_attempt(attempt)

    async def _request_text_with_retries(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, Any] | None = None,
    ) -> str:
        attempt = 0
        while True:
            attempt += 1
            request_text = getattr(self._transport, "request_text", None)
            if request_text is None:
                raise DocubrainError(
                    DocubrainErrorCode.BAD_GATEWAY,
                    "Google Workspace transport does not support text export",
                )
            text = await request_text(
                method,
                url,
                headers=headers,
                params=params,
            )
            status_code = _transport_status_code(self._transport)
            if status_code is None or 200 <= status_code < 300:
                return text
            if (
                status_code not in RETRYABLE_GOOGLE_STATUS_CODES
                or attempt >= self._retry_policy.max_attempts
            ):
                error_msg = (
                    "Google authentication expired or revoked. "
                    "Please reconnect your Google account in Settings → Connectors → Google Drive."
                    if status_code == 401
                    else f"Google Workspace export failed with status {status_code}"
                )
                raise DocubrainError(
                    _error_code_for_google_status(status_code),
                    error_msg,
                    status_code_override=status_code,
                )
            await self._sleep_for_attempt(attempt)

    async def _sleep_for_attempt(self, attempt: int) -> None:
        delay = self._retry_policy.base_delay_seconds * (2 ** (attempt - 1))
        if self._sleep is None:
            await asyncio.sleep(delay)
            return
        maybe_awaitable = self._sleep(delay)
        if hasattr(maybe_awaitable, "__await__"):
            await maybe_awaitable

    def _auth_headers(self) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._access_token}"}
        if self._request_id:
            headers["X-Request-ID"] = self._request_id
        return headers


@dataclass(frozen=True)
class _GoogleResponse:
    payload: dict[str, Any]
    status_code: int | None


def _required_string(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DocubrainError(
            DocubrainErrorCode.INVALID_INPUT,
            f"Google Workspace request requires a non-empty {key}",
        )
    return value


def _limit(arguments: dict[str, Any]) -> int:
    value = arguments.get("limit", 10)
    if not isinstance(value, int):
        return 10
    return max(1, min(value, 25))


def _escape_drive_query(query: str) -> str:
    """Escape special characters for Google Drive query strings.

    Drive query language uses single quotes for string literals.
    Backslashes and single quotes must be escaped within those literals.
    """
    return query.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"')


def _gmail_message_to_record(message: dict[str, Any]) -> dict[str, Any]:
    message_id = _string_or_empty(message.get("id"))
    thread_id = _string_or_empty(message.get("threadId")) or message_id
    snippet = _string_or_empty(message.get("snippet"))
    subject = _gmail_subject(message) or "Untitled Gmail result"

    return {
        "title": subject,
        "content": snippet,
        "source_id": thread_id,
        "source_url": f"https://mail.google.com/mail/u/0/#all/{thread_id}",
        "last_modified": _gmail_internal_date_to_iso(message.get("internalDate")),
        "confidence": 1.0,
    }


def _gmail_full_message_to_record(message: dict[str, Any]) -> dict[str, Any]:
    message_id = _string_or_empty(message.get("id"))
    thread_id = _string_or_empty(message.get("threadId")) or message_id
    payload = message.get("payload")
    headers = payload.get("headers") if isinstance(payload, dict) else []
    body = extract_gmail_message_body(payload if isinstance(payload, dict) else {})
    return {
        "message_id": message_id,
        "thread_id": thread_id,
        "subject": _header_value(headers, "subject"),
        "from": _header_value(headers, "from"),
        "body_text": body.body_text,
        "body_html": body.body_html,
        "snippet": _string_or_empty(message.get("snippet")),
        "source_url": f"https://mail.google.com/mail/u/0/#all/{thread_id}",
        "last_modified": _gmail_internal_date_to_iso(message.get("internalDate")),
    }


def _gmail_thread_ref_to_record(thread: dict[str, Any]) -> dict[str, Any]:
    thread_id = _string_or_empty(thread.get("id"))
    return {
        "thread_id": thread_id,
        "snippet": _string_or_empty(thread.get("snippet")),
    }


def _summarize_thread_records(messages: list[dict[str, Any]]) -> dict[str, Any]:
    senders = []
    subjects = []
    snippets = []
    for message in messages:
        sender = _string_or_empty(message.get("from"))
        subject = _string_or_empty(message.get("subject"))
        snippet = _string_or_empty(message.get("snippet"))
        if sender and sender not in senders:
            senders.append(sender)
        if subject and subject not in subjects:
            subjects.append(subject)
        if snippet:
            snippets.append(snippet)
    return {
        "message_count": len(messages),
        "participants": senders,
        "subjects": subjects,
        "summary": "\n".join(snippets[:10]),
        "messages": messages,
    }


def _gmail_attachment_records(message: dict[str, Any]) -> list[dict[str, Any]]:
    message_id = _string_or_empty(message.get("id"))
    thread_id = _string_or_empty(message.get("threadId")) or message_id
    payload = message.get("payload")
    headers = payload.get("headers") if isinstance(payload, dict) else []
    records: list[dict[str, Any]] = []
    for part in _walk_gmail_parts(payload if isinstance(payload, dict) else {}):
        filename = _string_or_empty(part.get("filename"))
        body = part.get("body")
        if not filename or not isinstance(body, dict):
            continue
        records.append(
            {
                "message_id": message_id,
                "thread_id": thread_id,
                "subject": _header_value(headers, "subject"),
                "filename": filename,
                "mime_type": _string_or_empty(part.get("mimeType")),
                "size": body.get("size", 0),
                "attachment_id": _string_or_empty(body.get("attachmentId")),
                "source_url": f"https://mail.google.com/mail/u/0/#all/{thread_id}",
                "last_modified": _gmail_internal_date_to_iso(message.get("internalDate")),
            }
        )
    return records


def _walk_gmail_parts(part: dict[str, Any]) -> list[dict[str, Any]]:
    parts = [part]
    for child in part.get("parts", []):
        if isinstance(child, dict):
            parts.extend(_walk_gmail_parts(child))
    return parts


def _gmail_subject(message: dict[str, Any]) -> str:
    payload = message.get("payload")
    if not isinstance(payload, dict):
        return ""
    headers = payload.get("headers")
    if not isinstance(headers, list):
        return ""

    for header in headers:
        if not isinstance(header, dict):
            continue
        if str(header.get("name", "")).lower() == "subject":
            return _string_or_empty(header.get("value"))
    return ""


def _header_value(headers: Any, name: str) -> str:
    if not isinstance(headers, list):
        return ""
    for header in headers:
        if not isinstance(header, dict):
            continue
        if str(header.get("name", "")).lower() == name:
            return _string_or_empty(header.get("value"))
    return ""


def _gmail_internal_date_to_iso(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        milliseconds = int(value)
    except ValueError:
        return None
    return datetime.fromtimestamp(milliseconds / 1000, UTC).isoformat()


def _drive_file_to_record(file: dict[str, Any]) -> dict[str, Any]:
    file_name = _string_or_empty(file.get("name")) or "Untitled Google Drive result"
    return {
        "title": file_name,
        "content": f"Google Drive file: {file_name}",
        "source_id": _string_or_empty(file.get("id")),
        "source_url": _optional_string(file.get("webViewLink")),
        "last_modified": _optional_string(file.get("modifiedTime")),
        "confidence": 1.0,
    }


def _drive_metadata_to_record(file: dict[str, Any]) -> dict[str, Any]:
    return {
        "file_id": _string_or_empty(file.get("id")),
        "name": _string_or_empty(file.get("name")),
        "mime_type": _string_or_empty(file.get("mimeType")),
        "description": _optional_string(file.get("description")),
        "source_url": _optional_string(file.get("webViewLink")),
        "created_time": _optional_string(file.get("createdTime")),
        "modified_time": _optional_string(file.get("modifiedTime")),
        "size": _optional_string(file.get("size")),
        "owners": file.get("owners", []),
        "drive_id": _optional_string(file.get("driveId")),
        "parents": file.get("parents", []),
        "shared": bool(file.get("shared", False)),
        "trashed": bool(file.get("trashed", False)),
    }


def _shared_drive_to_record(drive: dict[str, Any]) -> dict[str, Any]:
    return {
        "drive_id": _string_or_empty(drive.get("id")),
        "name": _string_or_empty(drive.get("name")),
        **(
            {"created_time": drive["createdTime"]}
            if isinstance(drive.get("createdTime"), str)
            else {}
        ),
        **({"hidden": drive["hidden"]} if isinstance(drive.get("hidden"), bool) else {}),
        **(
            {"restrictions": drive["restrictions"]}
            if isinstance(drive.get("restrictions"), dict)
            else {}
        ),
    }


def _string_or_empty(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _transport_status_code(transport: HTTPJSONTransport) -> int | None:
    status_code = getattr(transport, "last_status_code", None)
    return status_code if isinstance(status_code, int) else None


def _error_code_for_google_status(status_code: int) -> DocubrainErrorCode:
    """Map a Google API HTTP status to the appropriate DocuBrain error code."""
    if status_code == 404:
        return DocubrainErrorCode.NOT_FOUND
    if status_code == 401:
        return DocubrainErrorCode.CREDENTIAL_EXPIRED
    if status_code == 403:
        return DocubrainErrorCode.INSUFFICIENT_PERMISSIONS
    if status_code == 429:
        return DocubrainErrorCode.RATE_LIMITED
    return DocubrainErrorCode.BAD_GATEWAY


def _google_error_detail(payload: dict[str, Any], status_code: int) -> str:
    if status_code == 401:
        return (
            "Google authentication expired or revoked. "
            "Please reconnect your Google account in Settings → Connectors → Google Drive."
        )
    error = payload.get("error")
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"]
    return f"Google Workspace request failed with status {status_code}"
