"""MCP-based Google Drive incremental indexer.

Discovers recently modified Drive files via the existing MCP REST client,
converts them to Document objects with ACL (ExternalAccess), and feeds
them into the standard indexing pipeline (chunking → embedding → Vespa).

This is a *lightweight recent-activity indexer*, not a full sync engine.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from docubrain.access.models import ExternalAccess
from docubrain.configs.app_configs import MAX_MCP_EXPORT_BYTES
from docubrain.configs.app_configs import MCP_DRIVE_INDEXING_BATCH_SIZE
from docubrain.configs.app_configs import MCP_DRIVE_INDEXING_MAX_PAGES
from docubrain.configs.app_configs import MCP_DRIVE_STALE_THRESHOLD_SYNCS
from docubrain.configs.constants import DocumentSource
from docubrain.connectors.models import BasicExpertInfo
from docubrain.connectors.models import Document
from docubrain.connectors.models import IndexAttemptMetadata
from docubrain.connectors.models import InputType
from docubrain.connectors.models import TextSection
from docubrain.file_processing.extract_file_text import csv_text_to_row_records
from docubrain.db.models import Connector
from docubrain.db.models import ConnectorCredentialPair
from docubrain.db.models import Credential
from docubrain.db.enums import ConnectorCredentialPairStatus
from docubrain.db.mcp_drive_sync_state import acquire_sync_lock
from docubrain.db.mcp_drive_sync_state import get_content_hash
from docubrain.db.mcp_drive_sync_state import mark_unseen_docs_stale
from docubrain.db.mcp_drive_sync_state import update_sync_state
from docubrain.db.mcp_drive_sync_state import upsert_doc_tracking
from docubrain.db.search_settings import get_active_search_settings
from docubrain.document_index.factory import get_all_document_indices
from docubrain.indexing.adapters.document_indexing_adapter import (
    DocumentIndexingBatchAdapter,
)
from docubrain.indexing.indexing_pipeline import run_indexing_pipeline
from docubrain.mcp.clients.google_workspace import GoogleWorkspaceRESTClient
from docubrain.mcp.google_workspace_service import HttpxJSONTransport
from docubrain.mcp.google_workspace_service import get_valid_google_workspace_token_state
from docubrain.utils.logger import setup_logger

logger = setup_logger()

MCP_DRIVE_CONNECTOR_NAME = "Google Drive MCP Indexer"
MCP_DRIVE_DOC_ID_PREFIX = "mcp_google_drive__"

SUPPORTED_MCP_EXPORTS: dict[str, str] = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
    "application/vnd.google-apps.presentation": "text/plain",
}


@dataclass
class MCPDriveIndexingResult:
    total_discovered: int = 0
    new_docs_indexed: int = 0
    skipped_unchanged: int = 0
    total_docs_processed: int = 0
    total_chunks: int = 0
    failures: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_mcp_drive_indexing(
    *,
    db_session: Session,
    user: Any,
    tenant_id: str,
    max_pages: int | None = None,
) -> MCPDriveIndexingResult:
    """Top-level synchronous entry point for MCP Drive indexing.

    1. Acquires sync lock (skips if locked or already in_progress)
    2. Gets valid OAuth token
    3. Discovers recent files via MCP client
    4. Builds Document objects with ACL
    5. Feeds batches into run_indexing_pipeline()
    """
    max_pages = max_pages or MCP_DRIVE_INDEXING_MAX_PAGES
    result = MCPDriveIndexingResult()

    # Acquire lock
    locked_state = acquire_sync_lock(db_session, user.id, tenant_id)
    if locked_state is None:
        logger.info("MCP Drive indexing: skipped — already in progress for user=%s", user.id)
        return result

    now = datetime.now(timezone.utc)
    update_sync_state(
        db_session, user.id, tenant_id,
        status="in_progress",
        last_sync_started_at=now,
    )
    db_session.commit()

    try:
        result = _run_indexing_inner(
            db_session=db_session,
            user=user,
            tenant_id=tenant_id,
            max_pages=max_pages,
        )
        update_sync_state(
            db_session, user.id, tenant_id,
            status="success",
            last_sync_completed_at=datetime.now(timezone.utc),
            last_successful_sync_at=datetime.now(timezone.utc),
            last_error=None,
            total_docs_indexed=result.total_docs_processed,
            docs_skipped=result.skipped_unchanged,
        )
        db_session.commit()
    except Exception as e:
        logger.error("MCP Drive indexing failed: %s", e, exc_info=True)
        result.failures.append(str(e))
        db_session.rollback()
        update_sync_state(
            db_session, user.id, tenant_id,
            status="failed",
            last_sync_completed_at=datetime.now(timezone.utc),
            last_error=str(e)[:2048],
        )
        db_session.commit()

    return result


# ---------------------------------------------------------------------------
# Internal orchestration
# ---------------------------------------------------------------------------


def _run_indexing_inner(
    *,
    db_session: Session,
    user: Any,
    tenant_id: str,
    max_pages: int,
) -> MCPDriveIndexingResult:
    result = MCPDriveIndexingResult()

    # Get valid token
    token_state = asyncio.run(
        get_valid_google_workspace_token_state(
            db_session=db_session,
            user=user,
            source=DocumentSource.GOOGLE_DRIVE,
        )
    )

    transport = HttpxJSONTransport()
    client = GoogleWorkspaceRESTClient(
        access_token=token_state.access_token,
        transport=transport,
    )

    # Discover recent files
    file_records = asyncio.run(
        _discover_recent_drive_files(client, max_pages)
    )
    result.total_discovered = len(file_records)
    logger.info(
        "MCP Drive indexing: discovered %d recent files for user=%s",
        result.total_discovered, user.id,
    )

    if not file_records:
        return result

    # Build Document objects (with content hash dedup)
    documents: list[Document] = []
    seen_file_ids: set[str] = set()

    for file_record in file_records:
        file_id = file_record.get("source_id", "")
        if not file_id:
            continue
        seen_file_ids.add(file_id)

        mime_type = file_record.get("mime_type", "")
        if mime_type not in SUPPORTED_MCP_EXPORTS:
            continue

        # Check content hash for dedup
        doc = asyncio.run(
            _build_document_from_drive_file(
                client=client,
                file_record=file_record,
                user_id=user.id,
                tenant_id=tenant_id,
                db_session=db_session,
            )
        )
        if doc is None:
            result.skipped_unchanged += 1
            continue

        documents.append(doc)

    # Mark stale docs
    if seen_file_ids:
        newly_stale = mark_unseen_docs_stale(
            db_session,
            seen_file_ids=seen_file_ids,
            user_id=user.id,
            tenant_id=tenant_id,
            stale_threshold=MCP_DRIVE_STALE_THRESHOLD_SYNCS,
        )
        if newly_stale > 0:
            logger.info(
                "MCP Drive indexing: marked %d docs as stale for user=%s",
                newly_stale, user.id,
            )

    if not documents:
        logger.info("MCP Drive indexing: no new/changed docs to index")
        return result

    # Get or create the MCP connector + cc_pair
    connector_id, credential_id = get_or_create_mcp_drive_cc_pair(
        db_session, user
    )

    # Get search settings and document indices
    active_settings = get_active_search_settings(db_session)
    document_indices = get_all_document_indices(
        active_settings.primary, active_settings.secondary,
    )

    # Batch and index
    batch_size = MCP_DRIVE_INDEXING_BATCH_SIZE
    for i in range(0, len(documents), batch_size):
        batch = documents[i : i + batch_size]
        try:
            adapter = DocumentIndexingBatchAdapter(
                db_session=db_session,
                connector_id=connector_id,
                credential_id=credential_id,
                tenant_id=tenant_id,
                index_attempt_metadata=IndexAttemptMetadata(
                    connector_id=connector_id,
                    credential_id=credential_id,
                ),
            )
            pipeline_result = run_indexing_pipeline(
                document_batch=batch,
                request_id=None,
                embedder=None,  # run_indexing_pipeline creates one from settings
                document_indices=document_indices,
                db_session=db_session,
                tenant_id=tenant_id,
                adapter=adapter,
                ignore_time_skip=False,
            )
            result.new_docs_indexed += pipeline_result.new_docs
            result.total_docs_processed += pipeline_result.total_docs
            result.total_chunks += pipeline_result.total_chunks
            for failure in pipeline_result.failures:
                result.failures.append(str(failure))

            # Commit after each batch — partial success is persisted
            db_session.commit()
        except Exception as e:
            logger.error(
                "MCP Drive indexing: batch %d failed: %s",
                i // batch_size, e, exc_info=True,
            )
            result.failures.append(f"Batch {i // batch_size}: {e}")
            db_session.rollback()

    logger.info(
        "MCP Drive indexing complete: discovered=%d, new=%d, skipped=%d, "
        "chunks=%d, failures=%d",
        result.total_discovered, result.new_docs_indexed,
        result.skipped_unchanged, result.total_chunks, len(result.failures),
    )
    return result


# ---------------------------------------------------------------------------
# Async helpers
# ---------------------------------------------------------------------------


async def _discover_recent_drive_files(
    client: GoogleWorkspaceRESTClient,
    max_pages: int,
) -> list[dict[str, Any]]:
    """Paginate through list_recent_files, returning file metadata dicts."""
    all_files: list[dict[str, Any]] = []
    page_token: str | None = None

    for _ in range(max_pages):
        result = await client._list_recent_files(
            {"limit": 25, "page_token": page_token}
        )
        all_files.extend(result.records)

        page_token = result.next_page_token
        if not page_token:
            break

    return all_files


async def _build_document_from_drive_file(
    *,
    client: GoogleWorkspaceRESTClient,
    file_record: dict[str, Any],
    user_id: UUID,
    tenant_id: str,
    db_session: Session,
) -> Document | None:
    """Fetch content + permissions, build a Document.

    Returns None if:
    - Content is empty or too large
    - Content hash unchanged (dedup)
    - Export fails
    """
    file_id = file_record.get("source_id", "")
    file_name = file_record.get("title", "Untitled")
    source_url = file_record.get("source_url", "")
    modified_time = file_record.get("last_modified")
    mime_type = file_record.get("mime_type", "")

    export_mime = SUPPORTED_MCP_EXPORTS.get(mime_type)
    if not export_mime:
        return None

    document_id = f"{MCP_DRIVE_DOC_ID_PREFIX}{file_id}"
    start_time = time.monotonic()

    try:
        # For spreadsheets, export ALL tabs individually; for others, single export.
        is_spreadsheet = mime_type == "application/vnd.google-apps.spreadsheet"
        sheets_result: list[dict[str, Any]] = []

        if is_spreadsheet:
            # Fetch all sheet tabs + permissions + metadata concurrently
            all_results = await asyncio.gather(
                client._export_all_sheets_as_csv(file_id),
                client._get_file_permissions({"file_id": file_id}),
                client._get_drive_file_metadata({"file_id": file_id}),
                return_exceptions=True,
            )
            export_duration = time.monotonic() - start_time
            raw_sheets = all_results[0]
            permissions_result = all_results[1]
            metadata_result = all_results[2]

            if isinstance(raw_sheets, BaseException) or not isinstance(raw_sheets, list) or not raw_sheets:
                logger.warning(
                    "MCP Drive indexing: all-tabs export failed for file=%s: %s",
                    file_id, raw_sheets if isinstance(raw_sheets, BaseException) else "empty",
                )
                return None

            sheets_result: list[dict[str, Any]] = raw_sheets
            # Combine all tab CSVs into one content string for hashing
            content_text = "\n".join(s["content"] for s in sheets_result)
        else:
            all_results = await asyncio.gather(
                client._export_google_doc(
                    {"file_id": file_id, "mime_type": export_mime}
                ),
                client._get_file_permissions({"file_id": file_id}),
                client._get_drive_file_metadata({"file_id": file_id}),
                return_exceptions=True,
            )
            export_duration = time.monotonic() - start_time
            raw_content = all_results[0]
            permissions_result = all_results[1]
            metadata_result = all_results[2]

            if isinstance(raw_content, BaseException):
                logger.warning(
                    "MCP Drive indexing: export failed for file=%s: %s",
                    file_id, raw_content,
                )
                return None

            content_text = ""
            if hasattr(raw_content, "records") and raw_content.records:
                content_text = raw_content.records[0].get("content", "")

        if not content_text:
            logger.debug("MCP Drive indexing: empty content for file=%s", file_id)
            return None

        # Size limit
        content_bytes = len(content_text.encode("utf-8"))
        if content_bytes > MAX_MCP_EXPORT_BYTES:
            logger.info(
                "MCP Drive indexing: skipping oversized file=%s (%d bytes)",
                file_id, content_bytes,
            )
            return None

        # Content hash dedup
        content_hash = hashlib.sha256(content_text.encode("utf-8")).hexdigest()
        existing_hash = get_content_hash(
            db_session, drive_file_id=file_id, user_id=user_id, tenant_id=tenant_id,
        )
        if existing_hash == content_hash:
            # Content unchanged — still update tracking (last_seen_at)
            upsert_doc_tracking(
                db_session,
                document_id=document_id,
                drive_file_id=file_id,
                user_id=user_id,
                tenant_id=tenant_id,
                content_hash=content_hash,
            )
            logger.debug(
                "MCP Drive indexing: content unchanged for file=%s (%.2fs export)",
                file_id, export_duration,
            )
            return None

        # Build ExternalAccess from permissions
        external_access = ExternalAccess.empty()
        perm_records = (
            getattr(permissions_result, "records", None)
            if not isinstance(permissions_result, BaseException)
            else None
        )
        if perm_records:
            external_access = _permissions_to_external_access(perm_records)

        # Extract owners from metadata
        primary_owners: list[BasicExpertInfo] | None = None
        doc_metadata: dict[str, Any] = {
            "source_type": "mcp",
            "mime_type": mime_type,
        }
        meta_records = (
            getattr(metadata_result, "records", None)
            if not isinstance(metadata_result, BaseException)
            else None
        )
        if meta_records:
            meta = meta_records[0]
            owners = meta.get("owners", [])
            if owners:
                primary_owners = [
                    BasicExpertInfo(
                        display_name=o.get("displayName", ""),
                        email=o.get("emailAddress", ""),
                    )
                    for o in owners
                    if isinstance(o, dict)
                ]
            # Preserve permission roles in metadata for future use
            if perm_records:
                doc_metadata["drive_permissions"] = [
                    {"email": p.get("emailAddress", ""), "role": p.get("role", "")}
                    for p in perm_records
                    if isinstance(p, dict)
                ]

        # Parse modified time
        doc_updated_at = None
        if isinstance(modified_time, str) and modified_time:
            try:
                doc_updated_at = datetime.fromisoformat(
                    modified_time.replace("Z", "+00:00")
                )
            except ValueError:
                pass

        # Update doc tracking
        upsert_doc_tracking(
            db_session,
            document_id=document_id,
            drive_file_id=file_id,
            user_id=user_id,
            tenant_id=tenant_id,
            content_hash=content_hash,
        )

        logger.info(
            "MCP Drive indexing: built document file=%s name=%r (%.2fs, %d bytes)",
            file_id, file_name, export_duration, content_bytes,
        )

        # For spreadsheets, emit one TextSection per row per tab so that
        # each record is chunked independently with full header context.
        if is_spreadsheet and isinstance(sheets_result, list):
            all_row_records: list[dict[str, Any]] = []
            for tab in sheets_result:
                tab_rows = csv_text_to_row_records(
                    tab["content"],
                    file_name=file_name,
                    sheet_name=tab.get("sheet_name", "Sheet1"),
                )
                all_row_records.extend(tab_rows)
            if all_row_records:
                sections = [
                    TextSection(text=rec["text"], link=source_url)
                    for rec in all_row_records
                ]
                doc_metadata["record_type"] = "structured_spreadsheet"
                doc_metadata["row_count"] = len(all_row_records)
                doc_metadata["sheet_count"] = len(sheets_result)
            else:
                sections = [TextSection(text=content_text, link=source_url)]
        else:
            sections = [TextSection(text=content_text, link=source_url)]

        return Document(
            id=document_id,
            source=DocumentSource.GOOGLE_DRIVE,
            semantic_identifier=file_name,
            title=file_name,
            sections=sections,
            metadata={},
            doc_updated_at=doc_updated_at,
            primary_owners=primary_owners,
            external_access=external_access,
            doc_metadata=doc_metadata,
        )

    except Exception as e:
        logger.error(
            "MCP Drive indexing: failed to build document for file=%s: %s",
            file_id, e, exc_info=True,
        )
        return None


# ---------------------------------------------------------------------------
# ACL mapping
# ---------------------------------------------------------------------------


def _permissions_to_external_access(
    permissions: list[dict[str, Any]],
) -> ExternalAccess:
    """Map Google Drive permissions to ExternalAccess.

    - type=user → external_user_emails
    - type=group → external_user_group_ids (prefixed with google_group:)
    - type=domain/anyone → is_public=True
    """
    user_emails: set[str] = set()
    group_ids: set[str] = set()
    is_public = False

    for perm in permissions:
        if not isinstance(perm, dict):
            continue

        perm_type = perm.get("type", "")
        email = perm.get("emailAddress", "")

        if perm_type == "user" and email:
            user_emails.add(email.lower())
        elif perm_type == "group" and email:
            group_ids.add(f"google_group:{email.lower()}")
        elif perm_type in ("domain", "anyone"):
            is_public = True

    return ExternalAccess(
        external_user_emails=user_emails,
        external_user_group_ids=group_ids,
        is_public=is_public,
    )


# ---------------------------------------------------------------------------
# Connector / CC-pair bootstrap
# ---------------------------------------------------------------------------


def get_or_create_mcp_drive_cc_pair(
    db_session: Session,
    user: Any,
) -> tuple[int, int]:
    """Find or create the virtual Connector + ConnectorCredentialPair
    for MCP Drive indexing.

    Returns (connector_id, credential_id).
    """
    # Find existing MCP connector
    connector = db_session.execute(
        select(Connector).where(
            Connector.name == MCP_DRIVE_CONNECTOR_NAME,
            Connector.source == DocumentSource.GOOGLE_DRIVE,
        )
    ).scalar_one_or_none()

    if connector is None:
        connector = Connector(
            name=MCP_DRIVE_CONNECTOR_NAME,
            source=DocumentSource.GOOGLE_DRIVE,
            input_type=InputType.POLL,
            connector_specific_config={"mcp_indexer": True},
        )
        db_session.add(connector)
        db_session.flush()

    # Find user's Drive credential
    credential = db_session.execute(
        select(Credential).where(
            Credential.user_id == user.id,
            Credential.source == DocumentSource.GOOGLE_DRIVE,
        )
    ).scalar_one_or_none()

    if credential is None:
        raise ValueError(
            "No Google Drive credential found for user. "
            "Connect Google Workspace first."
        )

    # Find or create cc_pair
    cc_pair = db_session.execute(
        select(ConnectorCredentialPair).where(
            ConnectorCredentialPair.connector_id == connector.id,
            ConnectorCredentialPair.credential_id == credential.id,
        )
    ).scalar_one_or_none()

    if cc_pair is None:
        cc_pair = ConnectorCredentialPair(
            connector_id=connector.id,
            credential_id=credential.id,
            name=MCP_DRIVE_CONNECTOR_NAME,
            status=ConnectorCredentialPairStatus.ACTIVE,
        )
        db_session.add(cc_pair)
        db_session.flush()

    return connector.id, credential.id
