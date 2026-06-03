"""Incremental delete cleanup dispatcher for Google Drive.

When the Drive Changes API reports a file deletion, this module bridges the
event into the existing ``document_by_cc_pair_cleanup_task`` so that Vespa
chunks, hierarchy references, ACL entries, and DB records are removed
immediately — not deferred to the next pruning cycle.

Design constraints
------------------
* **Reuse existing cleanup infrastructure** — the same Celery task used by
  connector deletion and pruning handles Vespa deletion, KG cleanup,
  multi-cc_pair access updates, and DB record removal.
* **No new deletion subsystem** — this module is a *producer* of cleanup
  events; the existing shared task is the *executor*.
* **Idempotent** — duplicate Drive DELETE events (retries, out-of-order
  delivery) result in safe no-ops because the cleanup task checks
  ``get_document_connector_count`` before acting.
* **Fire-and-forget dispatch** — individual incremental deletes do not
  require Redis fencing or taskset tracking because they are independent,
  single-document operations (unlike batch connector deletion which must
  track completion of an entire cc_pair).
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from docubrain.configs.constants import DocubrainCeleryPriority
from docubrain.configs.constants import DocubrainCeleryQueues
from docubrain.configs.constants import DocubrainCeleryTask
from docubrain.db.connector_credential_pair import (
    get_connector_credential_pair_from_id,
)
from docubrain.db.google_drive_sync import get_google_drive_indexed_file_state
from docubrain.server.metrics.incremental_sync_metrics import (
    inc_delete_enqueued,
    inc_delete_skipped_no_cc_pair,
    inc_delete_skipped_no_document,
)

logger = logging.getLogger(__name__)

# Distinct prefix so these tasks don't collide with full connector deletion
# tracking in the task_postrun handler.
INCREMENTAL_DELETE_TASK_PREFIX = "incrementaldelete"


def _get_celery_app() -> Any:
    """Lazy accessor for the Celery client app.

    Isolated into its own function so tests can patch it without triggering
    Celery/broker initialisation at import time.
    """
    from docubrain.background.celery.apps.client import celery_app

    return celery_app


def _generate_task_id(cc_pair_id: int) -> str:
    return f"{INCREMENTAL_DELETE_TASK_PREFIX}_{cc_pair_id}_{uuid4()}"


def enqueue_drive_delete_cleanup(
    *,
    db_session: Session,
    cc_pair_id: int,
    file_id: str,
    tenant_id: str,
) -> bool:
    """Dispatch an immediate cleanup task for a deleted Drive file.

    Returns True if a cleanup task was enqueued, False if the file could not
    be resolved to a DocuBrain document (e.g. never indexed, or already
    cleaned up).
    """
    # ------------------------------------------------------------------
    # 1. Resolve the Drive file_id to a DocuBrain document_id via the
    #    indexed file state table.  This avoids an expensive scan and
    #    leverages the mapping already maintained by the sync pipeline.
    # ------------------------------------------------------------------
    file_state = get_google_drive_indexed_file_state(
        db_session=db_session,
        cc_pair_id=cc_pair_id,
        file_id=file_id,
    )

    if file_state is None or file_state.document_id is None:
        logger.info(
            "Incremental delete skipped: no indexed document for "
            "file_id=%s cc_pair=%d (file was never indexed or already cleaned up)",
            file_id,
            cc_pair_id,
        )
        inc_delete_skipped_no_document()
        return False

    document_id: str = file_state.document_id

    # ------------------------------------------------------------------
    # 2. Look up connector_id and credential_id from the cc_pair.
    # ------------------------------------------------------------------
    cc_pair = get_connector_credential_pair_from_id(
        db_session=db_session,
        cc_pair_id=cc_pair_id,
    )
    if cc_pair is None:
        logger.warning(
            "Incremental delete skipped: cc_pair=%d not found "
            "(may have been fully deleted already)",
            cc_pair_id,
        )
        inc_delete_skipped_no_cc_pair()
        return False

    # ------------------------------------------------------------------
    # 3. Dispatch the existing cleanup task.
    # ------------------------------------------------------------------
    task_id = _generate_task_id(cc_pair_id)
    celery_app = _get_celery_app()

    celery_app.send_task(
        DocubrainCeleryTask.DOCUMENT_BY_CC_PAIR_CLEANUP_TASK,
        kwargs=dict(
            document_id=document_id,
            connector_id=cc_pair.connector_id,
            credential_id=cc_pair.credential_id,
            tenant_id=tenant_id,
        ),
        queue=DocubrainCeleryQueues.CONNECTOR_DELETION,
        task_id=task_id,
        priority=DocubrainCeleryPriority.HIGH,  # higher than batch deletion
        ignore_result=True,
    )

    logger.info(
        "Incremental delete cleanup enqueued: "
        "file_id=%s document_id=%s cc_pair=%d task_id=%s",
        file_id,
        document_id,
        cc_pair_id,
        task_id,
    )
    inc_delete_enqueued()
    return True
