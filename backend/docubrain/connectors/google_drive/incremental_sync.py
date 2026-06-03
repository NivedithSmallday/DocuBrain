"""Google Drive incremental sync helpers.

This module is intentionally connector-local: it upgrades the existing Google
Drive ingestion path with Drive Changes API semantics without creating a second
MCP-only indexing system. The connector still owns extraction, hierarchy
creation, permission mapping, chunking, embedding, and Vespa writes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from enum import Enum
from typing import Any
from typing import Iterator

from docubrain.connectors.google_drive.models import GoogleDriveFileType


class DriveChangeAction(str, Enum):
    """What the indexing pipeline should do for a Drive change."""

    UPSERT = "upsert"
    PERMISSION_UPDATE = "permission_update"
    DELETE = "delete"
    SKIP = "skip"


@dataclass(frozen=True)
class GoogleDriveChangeListPage:
    changes: list[dict[str, Any]]
    next_page_token: str | None
    new_start_page_token: str | None


@dataclass(frozen=True)
class DriveFileFingerprint:
    content_fingerprint: str
    acl_fingerprint: str


@dataclass(frozen=True)
class DriveChangeClassification:
    action: DriveChangeAction
    file_id: str
    file: GoogleDriveFileType | None
    changed_at: datetime | None
    content_fingerprint: str | None
    acl_fingerprint: str | None


class GoogleDriveChangesClient:
    """Thin typed wrapper around the Drive Changes API.

    The official API returns `nextPageToken` until the current change stream page
    is exhausted, then returns `newStartPageToken` for future incremental syncs.
    The connector persists only the last fully acknowledged token so retries can
    safely replay pages without losing changes.
    """

    CHANGES_LIST_FIELDS = (
        "nextPageToken,newStartPageToken,"
        "changes(fileId,removed,time,driveId,changeType,"
        "file(id,name,mimeType,modifiedTime,webViewLink,driveId,parents,"
        "shortcutDetails,owners(emailAddress,displayName),size,trashed,"
        "md5Checksum,version,headRevisionId,permissionIds,"
        "permissions(id,emailAddress,type,domain,allowFileDiscovery,permissionDetails)))"
    )

    def __init__(self, drive_service: Any) -> None:
        self._drive_service = drive_service

    def get_start_page_token(self, drive_id: str | None = None) -> str:
        kwargs: dict[str, Any] = {"supportsAllDrives": True}
        if drive_id:
            kwargs["driveId"] = drive_id
        payload = self._drive_service.changes().getStartPageToken(**kwargs).execute()
        token = payload.get("startPageToken")
        if not isinstance(token, str) or not token:
            raise RuntimeError("Google Drive changes.startPageToken returned no token")
        return token

    def iter_change_pages(
        self,
        *,
        page_token: str,
        drive_id: str | None = None,
        page_size: int = 100,
    ) -> Iterator[GoogleDriveChangeListPage]:
        current_token = page_token
        while True:
            kwargs: dict[str, Any] = {
                "pageToken": current_token,
                "supportsAllDrives": True,
                "includeItemsFromAllDrives": True,
                "includeRemoved": True,
                "includeCorpusRemovals": True,
                "pageSize": page_size,
                "fields": self.CHANGES_LIST_FIELDS,
            }
            if drive_id:
                kwargs["driveId"] = drive_id

            payload = self._drive_service.changes().list(**kwargs).execute()
            page = GoogleDriveChangeListPage(
                changes=[
                    change
                    for change in payload.get("changes", [])
                    if isinstance(change, dict)
                ],
                next_page_token=_optional_str(payload.get("nextPageToken")),
                new_start_page_token=_optional_str(payload.get("newStartPageToken")),
            )
            yield page

            if page.next_page_token is None:
                return
            current_token = page.next_page_token


def build_drive_file_fingerprint(file: GoogleDriveFileType) -> DriveFileFingerprint:
    """Build separate content and ACL fingerprints for idempotent sync.

    Permission changes in Drive often appear as file changes even when content is
    identical. Keeping the hashes separate lets us update ACL metadata and Vespa
    ACL fields without re-exporting, rechunking, or re-embedding unchanged file
    contents.
    """

    content_parts = {
        "id": file.get("id"),
        "mimeType": file.get("mimeType"),
        "modifiedTime": file.get("modifiedTime"),
        "md5Checksum": file.get("md5Checksum"),
        "version": file.get("version"),
        "headRevisionId": file.get("headRevisionId"),
        "name": file.get("name"),
        "parents": sorted(_str_list(file.get("parents"))),
        "trashed": file.get("trashed"),
    }
    acl_parts = {
        "permissionIds": sorted(_str_list(file.get("permissionIds"))),
        "permissions": _stable_permissions(file.get("permissions")),
        "driveId": file.get("driveId"),
    }
    return DriveFileFingerprint(
        content_fingerprint=_hash_json(content_parts),
        acl_fingerprint=_hash_json(acl_parts),
    )


def classify_drive_change(
    change: dict[str, Any],
    *,
    previous_content_fingerprint: str | None,
) -> DriveChangeClassification:
    file_id = _optional_str(change.get("fileId")) or _optional_str(
        change.get("file", {}).get("id") if isinstance(change.get("file"), dict) else None
    )
    if file_id is None:
        return DriveChangeClassification(
            action=DriveChangeAction.SKIP,
            file_id="",
            file=None,
            changed_at=_parse_change_time(change.get("time")),
            content_fingerprint=None,
            acl_fingerprint=None,
        )

    if change.get("removed") is True:
        return DriveChangeClassification(
            action=DriveChangeAction.DELETE,
            file_id=file_id,
            file=None,
            changed_at=_parse_change_time(change.get("time")),
            content_fingerprint=None,
            acl_fingerprint=None,
        )

    file = change.get("file")
    if not isinstance(file, dict):
        return DriveChangeClassification(
            action=DriveChangeAction.SKIP,
            file_id=file_id,
            file=None,
            changed_at=_parse_change_time(change.get("time")),
            content_fingerprint=None,
            acl_fingerprint=None,
        )

    fingerprint = build_drive_file_fingerprint(file)
    action = (
        DriveChangeAction.PERMISSION_UPDATE
        if previous_content_fingerprint == fingerprint.content_fingerprint
        else DriveChangeAction.UPSERT
    )
    return DriveChangeClassification(
        action=action,
        file_id=file_id,
        file=file,
        changed_at=_parse_change_time(change.get("time")),
        content_fingerprint=fingerprint.content_fingerprint,
        acl_fingerprint=fingerprint.acl_fingerprint,
    )


def _stable_permissions(value: Any) -> list[dict[str, Any]]:
    permissions = [perm for perm in value or [] if isinstance(perm, dict)]
    stable: list[dict[str, Any]] = []
    for permission in permissions:
        stable.append(
            {
                "id": permission.get("id"),
                "emailAddress": permission.get("emailAddress"),
                "type": permission.get("type"),
                "domain": permission.get("domain"),
                "allowFileDiscovery": permission.get("allowFileDiscovery"),
                "permissionDetails": permission.get("permissionDetails"),
            }
        )
    return sorted(stable, key=lambda item: json.dumps(item, sort_keys=True))


def _hash_json(value: dict[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _parse_change_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)

