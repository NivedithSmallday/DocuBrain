"""Database helpers for MCP Drive sync state and document tracking."""

from datetime import datetime
from datetime import timedelta
from datetime import timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy import update
from sqlalchemy.orm import Session

from docubrain.configs.app_configs import MCP_DRIVE_STALE_TASK_TIMEOUT_HOURS
from docubrain.db.models import MCPDriveDocTracking
from docubrain.db.models import MCPDriveSyncState


def get_or_create_sync_state(
    db_session: Session,
    user_id: UUID,
    tenant_id: str,
) -> MCPDriveSyncState:
    state = db_session.execute(
        select(MCPDriveSyncState).where(
            MCPDriveSyncState.user_id == user_id,
            MCPDriveSyncState.tenant_id == tenant_id,
        )
    ).scalar_one_or_none()
    if state is None:
        state = MCPDriveSyncState(
            user_id=user_id,
            tenant_id=tenant_id,
        )
        db_session.add(state)
        db_session.flush()
    return state


def acquire_sync_lock(
    db_session: Session,
    user_id: UUID,
    tenant_id: str,
) -> MCPDriveSyncState | None:
    """Attempt to acquire a row-level lock on the sync state.

    Returns the locked row if acquired, None if already locked by another
    worker or currently in_progress.  Stale in_progress states (older than
    the configured timeout) are auto-reset to 'failed' before the lock
    attempt.
    """
    _auto_reset_stale_tasks(db_session)

    state = get_or_create_sync_state(db_session, user_id, tenant_id)

    if state.status == "in_progress":
        return None

    # Attempt row-level lock (SKIP LOCKED avoids blocking)
    locked = db_session.execute(
        select(MCPDriveSyncState)
        .where(MCPDriveSyncState.id == state.id)
        .with_for_update(skip_locked=True)
    ).scalar_one_or_none()

    return locked


def update_sync_state(
    db_session: Session,
    user_id: UUID,
    tenant_id: str,
    *,
    status: str | None = None,
    last_sync_started_at: datetime | None = None,
    last_sync_completed_at: datetime | None = None,
    last_successful_sync_at: datetime | None = None,
    last_error: str | None = None,
    total_docs_indexed: int | None = None,
    docs_skipped: int | None = None,
) -> None:
    state = get_or_create_sync_state(db_session, user_id, tenant_id)
    if status is not None:
        state.status = status
    if last_sync_started_at is not None:
        state.last_sync_started_at = last_sync_started_at
    if last_sync_completed_at is not None:
        state.last_sync_completed_at = last_sync_completed_at
    if last_successful_sync_at is not None:
        state.last_successful_sync_at = last_successful_sync_at
    if last_error is not None:
        state.last_error = last_error[:2048] if last_error else None
    if total_docs_indexed is not None:
        state.total_docs_indexed = total_docs_indexed
    if docs_skipped is not None:
        state.docs_skipped = docs_skipped
    db_session.flush()


def get_sync_state_for_user(
    db_session: Session,
    user_id: UUID,
    tenant_id: str,
) -> MCPDriveSyncState | None:
    return db_session.execute(
        select(MCPDriveSyncState).where(
            MCPDriveSyncState.user_id == user_id,
            MCPDriveSyncState.tenant_id == tenant_id,
        )
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Document tracking
# ---------------------------------------------------------------------------


def upsert_doc_tracking(
    db_session: Session,
    *,
    document_id: str,
    drive_file_id: str,
    user_id: UUID,
    tenant_id: str,
    content_hash: str | None,
) -> MCPDriveDocTracking:
    tracking = db_session.execute(
        select(MCPDriveDocTracking).where(
            MCPDriveDocTracking.drive_file_id == drive_file_id,
            MCPDriveDocTracking.user_id == user_id,
            MCPDriveDocTracking.tenant_id == tenant_id,
        )
    ).scalar_one_or_none()

    now = datetime.now(timezone.utc)

    if tracking is None:
        tracking = MCPDriveDocTracking(
            document_id=document_id,
            drive_file_id=drive_file_id,
            user_id=user_id,
            tenant_id=tenant_id,
            content_hash=content_hash,
            last_seen_at=now,
            is_stale=False,
            consecutive_misses=0,
        )
        db_session.add(tracking)
    else:
        tracking.document_id = document_id
        tracking.content_hash = content_hash
        tracking.last_seen_at = now
        tracking.consecutive_misses = 0
        if tracking.is_stale:
            tracking.is_stale = False  # auto-revive

    db_session.flush()
    return tracking


def get_content_hash(
    db_session: Session,
    *,
    drive_file_id: str,
    user_id: UUID,
    tenant_id: str,
) -> str | None:
    tracking = db_session.execute(
        select(MCPDriveDocTracking.content_hash).where(
            MCPDriveDocTracking.drive_file_id == drive_file_id,
            MCPDriveDocTracking.user_id == user_id,
            MCPDriveDocTracking.tenant_id == tenant_id,
        )
    ).scalar_one_or_none()
    return tracking


def mark_unseen_docs_stale(
    db_session: Session,
    *,
    seen_file_ids: set[str],
    user_id: UUID,
    tenant_id: str,
    stale_threshold: int,
) -> int:
    """Increment consecutive_misses for unseen docs and mark as stale
    if threshold exceeded. Returns count of newly stale docs."""
    all_tracked = db_session.execute(
        select(MCPDriveDocTracking).where(
            MCPDriveDocTracking.user_id == user_id,
            MCPDriveDocTracking.tenant_id == tenant_id,
            MCPDriveDocTracking.is_stale.is_(False),
        )
    ).scalars().all()

    newly_stale = 0
    for tracking in all_tracked:
        if tracking.drive_file_id not in seen_file_ids:
            tracking.consecutive_misses += 1
            if tracking.consecutive_misses >= stale_threshold:
                tracking.is_stale = True
                newly_stale += 1

    db_session.flush()
    return newly_stale


def get_stale_doc_ids(
    db_session: Session,
    *,
    user_id: UUID,
    tenant_id: str,
) -> set[str]:
    """Return document IDs for stale MCP Drive docs (for search filtering)."""
    rows = db_session.execute(
        select(MCPDriveDocTracking.document_id).where(
            MCPDriveDocTracking.user_id == user_id,
            MCPDriveDocTracking.tenant_id == tenant_id,
            MCPDriveDocTracking.is_stale.is_(True),
        )
    ).scalars().all()
    return set(rows)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _auto_reset_stale_tasks(db_session: Session) -> None:
    """Reset in_progress states that are older than the timeout threshold
    or have a NULL start timestamp (crashed before recording start time)."""
    from sqlalchemy import or_

    cutoff = datetime.now(timezone.utc) - timedelta(
        hours=MCP_DRIVE_STALE_TASK_TIMEOUT_HOURS
    )
    db_session.execute(
        update(MCPDriveSyncState)
        .where(
            MCPDriveSyncState.status == "in_progress",
            or_(
                MCPDriveSyncState.last_sync_started_at < cutoff,
                MCPDriveSyncState.last_sync_started_at.is_(None),
            ),
        )
        .values(
            status="failed",
            last_error="Auto-reset: task exceeded timeout or had no start timestamp",
        )
    )
    db_session.flush()
