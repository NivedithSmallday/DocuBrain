from datetime import datetime
from datetime import timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from docubrain.connectors.google_drive.incremental_sync import (
    DriveChangeClassification,
)
from docubrain.connectors.google_drive.incremental_sync import DriveFileFingerprint
from docubrain.connectors.google_drive.incremental_sync import build_drive_file_fingerprint
from docubrain.connectors.google_drive.models import GoogleDriveFileType
from docubrain.db.enums import GoogleDriveSyncScopeType
from docubrain.db.models import GoogleDriveIndexedFileState
from docubrain.db.models import GoogleDriveSyncState


def get_google_drive_sync_state(
    *,
    db_session: Session,
    cc_pair_id: int,
    scope_type: GoogleDriveSyncScopeType = GoogleDriveSyncScopeType.USER,
    drive_id: str | None = None,
) -> GoogleDriveSyncState | None:
    return db_session.execute(
        select(GoogleDriveSyncState).where(
            GoogleDriveSyncState.connector_credential_pair_id == cc_pair_id,
            GoogleDriveSyncState.scope_type == scope_type,
            GoogleDriveSyncState.drive_id == _normalized_drive_id(drive_id),
        )
    ).scalar_one_or_none()


def upsert_google_drive_sync_state(
    *,
    db_session: Session,
    cc_pair_id: int,
    page_token: str,
    scope_type: GoogleDriveSyncScopeType = GoogleDriveSyncScopeType.USER,
    drive_id: str | None = None,
    full_sync_required: bool,
) -> GoogleDriveSyncState:
    sync_state = get_google_drive_sync_state(
        db_session=db_session,
        cc_pair_id=cc_pair_id,
        scope_type=scope_type,
        drive_id=drive_id,
    )
    if sync_state is None:
        sync_state = GoogleDriveSyncState(
            connector_credential_pair_id=cc_pair_id,
            scope_type=scope_type,
            drive_id=_normalized_drive_id(drive_id),
        )
        db_session.add(sync_state)

    sync_state.page_token = page_token
    sync_state.full_sync_required = full_sync_required
    sync_state.token_invalid = False
    sync_state.error_message = None
    sync_state.last_sync_time = datetime.now(timezone.utc)
    if not full_sync_required:
        sync_state.last_successful_sync_time = sync_state.last_sync_time
    db_session.flush()
    return sync_state


def mark_google_drive_sync_token_invalid(
    *,
    db_session: Session,
    sync_state: GoogleDriveSyncState,
    error_message: str,
) -> None:
    sync_state.token_invalid = True
    sync_state.full_sync_required = True
    sync_state.error_message = error_message[:2048]
    sync_state.last_sync_time = datetime.now(timezone.utc)
    db_session.flush()


def get_google_drive_indexed_file_state(
    *,
    db_session: Session,
    cc_pair_id: int,
    file_id: str,
) -> GoogleDriveIndexedFileState | None:
    return db_session.execute(
        select(GoogleDriveIndexedFileState).where(
            GoogleDriveIndexedFileState.connector_credential_pair_id == cc_pair_id,
            GoogleDriveIndexedFileState.file_id == file_id,
        )
    ).scalar_one_or_none()


def previous_content_fingerprint_for_file(
    *,
    db_session: Session,
    cc_pair_id: int,
    file_id: str,
) -> str | None:
    state = get_google_drive_indexed_file_state(
        db_session=db_session,
        cc_pair_id=cc_pair_id,
        file_id=file_id,
    )
    return state.content_fingerprint if state else None


def upsert_google_drive_indexed_file_state(
    *,
    db_session: Session,
    cc_pair_id: int,
    file: GoogleDriveFileType,
    document_id: str | None,
    changed_at: datetime | None,
    fingerprint: DriveFileFingerprint | None = None,
) -> GoogleDriveIndexedFileState:
    file_id = _file_id(file)
    state = get_google_drive_indexed_file_state(
        db_session=db_session,
        cc_pair_id=cc_pair_id,
        file_id=file_id,
    )
    if state is None:
        state = GoogleDriveIndexedFileState(
            connector_credential_pair_id=cc_pair_id,
            file_id=file_id,
        )
        db_session.add(state)

    fingerprint = fingerprint or build_drive_file_fingerprint(file)
    state.drive_id = _optional_str(file.get("driveId"))
    state.document_id = document_id
    state.content_fingerprint = fingerprint.content_fingerprint
    state.acl_fingerprint = fingerprint.acl_fingerprint
    state.name = _optional_str(file.get("name"))
    state.mime_type = _optional_str(file.get("mimeType"))
    state.parents = [p for p in file.get("parents", []) if isinstance(p, str)]
    state.modified_time = _parse_time(file.get("modifiedTime"))
    state.last_seen_change_time = changed_at
    state.last_indexed_at = datetime.now(timezone.utc)
    state.deleted_at = None
    state.raw_metadata = _safe_metadata(file)
    db_session.flush()
    return state


def is_tombstone_newer_than_change(
    *,
    db_session: Session,
    cc_pair_id: int,
    file_id: str,
    change_timestamp: datetime | None,
) -> bool:
    """Return True only if a tombstone exists AND is newer than the change.

    This prevents two failure modes:

    * **Resurrection race** — a concurrent worker already processed a DELETE
      that is *newer* than the current UPSERT → skip indexing.
    * **Permanent suppression** — the file was restored from trash *after*
      the DELETE → the UPSERT carries a newer timestamp → allow re-indexing.

    When ``change_timestamp`` is None (rare: the Changes API almost always
    supplies a ``time`` field), the guard conservatively allows the UPSERT
    through so restored files are never permanently invisible.
    """
    state = get_google_drive_indexed_file_state(
        db_session=db_session,
        cc_pair_id=cc_pair_id,
        file_id=file_id,
    )
    if state is None or state.deleted_at is None:
        return False

    # If the UPSERT has no timestamp, allow it through — conservative
    # choice to avoid permanent suppression.
    if change_timestamp is None:
        return False

    return state.deleted_at >= change_timestamp


def mark_google_drive_file_deleted(
    *,
    db_session: Session,
    cc_pair_id: int,
    file_id: str,
    changed_at: datetime | None,
) -> GoogleDriveIndexedFileState:
    state = get_google_drive_indexed_file_state(
        db_session=db_session,
        cc_pair_id=cc_pair_id,
        file_id=file_id,
    )
    if state is None:
        state = GoogleDriveIndexedFileState(
            connector_credential_pair_id=cc_pair_id,
            file_id=file_id,
        )
        db_session.add(state)
    state.deleted_at = changed_at or datetime.now(timezone.utc)
    state.last_seen_change_time = changed_at
    db_session.flush()
    return state


def apply_drive_change_to_file_state(
    *,
    db_session: Session,
    cc_pair_id: int,
    classification: DriveChangeClassification,
    document_id: str | None,
) -> None:
    if classification.file is None:
        mark_google_drive_file_deleted(
            db_session=db_session,
            cc_pair_id=cc_pair_id,
            file_id=classification.file_id,
            changed_at=classification.changed_at,
        )
        return

    fingerprint = (
        DriveFileFingerprint(
            content_fingerprint=classification.content_fingerprint,
            acl_fingerprint=classification.acl_fingerprint,
        )
        if classification.content_fingerprint and classification.acl_fingerprint
        else None
    )
    upsert_google_drive_indexed_file_state(
        db_session=db_session,
        cc_pair_id=cc_pair_id,
        file=classification.file,
        document_id=document_id,
        changed_at=classification.changed_at,
        fingerprint=fingerprint,
    )


def invalidate_google_drive_sync_states_for_cc_pair(
    *,
    db_session: Session,
    cc_pair_id: int,
    reason: str,
) -> None:
    states = db_session.execute(
        select(GoogleDriveSyncState).where(
            GoogleDriveSyncState.connector_credential_pair_id == cc_pair_id
        )
    ).scalars()
    for state in states:
        state.full_sync_required = True
        state.token_invalid = True
        state.error_message = reason[:2048]
        state.updated_at = datetime.now(timezone.utc)
    db_session.flush()


def _normalized_drive_id(drive_id: str | None) -> str:
    return drive_id or ""


def _file_id(file: GoogleDriveFileType) -> str:
    file_id = file.get("id")
    if not isinstance(file_id, str) or not file_id:
        raise ValueError("Google Drive file is missing id")
    return file_id


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _safe_metadata(file: GoogleDriveFileType) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for key in (
        "id",
        "name",
        "mimeType",
        "modifiedTime",
        "webViewLink",
        "driveId",
        "parents",
        "permissionIds",
        "owners",
        "trashed",
    ):
        if key in file:
            metadata[key] = file[key]
    return metadata

