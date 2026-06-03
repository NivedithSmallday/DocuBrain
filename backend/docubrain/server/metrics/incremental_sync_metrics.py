"""Incremental sync reconciliation Prometheus metrics.

Tracks decision outcomes during Google Drive incremental sync processing:

- DELETE events dispatched vs skipped
- Tombstone-blocked UPSERTs (stale change superseded by newer DELETE)
- Restoration events (file restored from trash, tombstone cleared)
- Cleanup task outcomes

All counters use ``connector_type`` as the sole label to avoid unbounded
cardinality from per-file or per-cc_pair labels. Per-event details
(file_id, cc_pair_id, timestamps) are captured in structured log lines
emitted alongside each metric increment.

Usage:
    from docubrain.server.metrics.incremental_sync_metrics import (
        inc_delete_enqueued,
        inc_delete_skipped_no_document,
        inc_delete_skipped_no_cc_pair,
        inc_tombstone_blocked_upsert,
        inc_restoration_event,
    )
"""

from prometheus_client import Counter

from docubrain.utils.logger import setup_logger

logger = setup_logger()

_CONNECTOR_TYPE = "google_drive"

DELETE_ENQUEUED = Counter(
    "docubrain_incremental_delete_enqueued_total",
    "Drive DELETE events that dispatched a cleanup task",
    ["connector_type"],
)

DELETE_SKIPPED_NO_DOCUMENT = Counter(
    "docubrain_incremental_delete_skipped_no_document_total",
    "Drive DELETE events skipped because the file was never indexed or already cleaned up",
    ["connector_type"],
)

DELETE_SKIPPED_NO_CC_PAIR = Counter(
    "docubrain_incremental_delete_skipped_no_cc_pair_total",
    "Drive DELETE events skipped because the connector credential pair no longer exists",
    ["connector_type"],
)

TOMBSTONE_BLOCKED_UPSERT = Counter(
    "docubrain_incremental_tombstone_blocked_upserts_total",
    "UPSERT events blocked because a newer DELETE tombstone takes precedence",
    ["connector_type"],
)

RESTORATION_EVENT = Counter(
    "docubrain_incremental_restoration_events_total",
    "UPSERT events that cleared a tombstone (file restored from trash or re-shared)",
    ["connector_type"],
)


def inc_delete_enqueued() -> None:
    try:
        DELETE_ENQUEUED.labels(connector_type=_CONNECTOR_TYPE).inc()
    except Exception:
        logger.debug("Failed to record incremental delete enqueued metric", exc_info=True)


def inc_delete_skipped_no_document() -> None:
    try:
        DELETE_SKIPPED_NO_DOCUMENT.labels(connector_type=_CONNECTOR_TYPE).inc()
    except Exception:
        logger.debug("Failed to record delete skipped metric", exc_info=True)


def inc_delete_skipped_no_cc_pair() -> None:
    try:
        DELETE_SKIPPED_NO_CC_PAIR.labels(connector_type=_CONNECTOR_TYPE).inc()
    except Exception:
        logger.debug("Failed to record delete skipped metric", exc_info=True)


def inc_tombstone_blocked_upsert() -> None:
    try:
        TOMBSTONE_BLOCKED_UPSERT.labels(connector_type=_CONNECTOR_TYPE).inc()
    except Exception:
        logger.debug("Failed to record tombstone blocked metric", exc_info=True)


def inc_restoration_event() -> None:
    try:
        RESTORATION_EVENT.labels(connector_type=_CONNECTOR_TYPE).inc()
    except Exception:
        logger.debug("Failed to record restoration event metric", exc_info=True)
