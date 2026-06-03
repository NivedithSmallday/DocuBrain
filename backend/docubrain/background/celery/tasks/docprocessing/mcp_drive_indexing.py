"""Celery task for periodic MCP Drive indexing."""

from celery import shared_task
from celery import Task

from docubrain.configs.constants import DocubrainCeleryTask
from docubrain.utils.logger import setup_logger

logger = setup_logger()

JOB_TIMEOUT = 60 * 30  # 30 minutes


@shared_task(
    name=DocubrainCeleryTask.CHECK_FOR_MCP_DRIVE_INDEXING,
    ignore_result=True,
    soft_time_limit=JOB_TIMEOUT,
    trail=False,
    bind=True,
)
def check_for_mcp_drive_indexing(self: Task, *, tenant_id: str) -> None:
    """Periodic task: for each user with a connected Google Drive MCP
    credential, run MCP Drive indexing if not already in progress."""
    from sqlalchemy import select

    from docubrain.configs.constants import DocumentSource
    from docubrain.db.engine.sql_engine import get_session_with_current_tenant
    from docubrain.db.models import Credential
    from docubrain.db.models import User

    logger.info("check_for_mcp_drive_indexing: starting for tenant=%s", tenant_id)

    with get_session_with_current_tenant(tenant_id) as db_session:
        # Find users with Google Drive credentials
        user_ids = db_session.execute(
            select(Credential.user_id).where(
                Credential.source == DocumentSource.GOOGLE_DRIVE,
                Credential.user_id.isnot(None),
            ).distinct()
        ).scalars().all()

        if not user_ids:
            logger.info("check_for_mcp_drive_indexing: no Drive credentials found")
            return

        for user_id in user_ids:
            user = db_session.execute(
                select(User).where(User.id == user_id)
            ).scalar_one_or_none()
            if user is None:
                continue

            try:
                from docubrain.mcp.mcp_drive_indexer import run_mcp_drive_indexing

                result = run_mcp_drive_indexing(
                    db_session=db_session,
                    user=user,
                    tenant_id=tenant_id,
                )
                logger.info(
                    "check_for_mcp_drive_indexing: user=%s "
                    "discovered=%d new=%d skipped=%d failures=%d",
                    user_id,
                    result.total_discovered,
                    result.new_docs_indexed,
                    result.skipped_unchanged,
                    len(result.failures),
                )
            except Exception as e:
                logger.error(
                    "check_for_mcp_drive_indexing: failed for user=%s: %s",
                    user_id, e, exc_info=True,
                )

    logger.info("check_for_mcp_drive_indexing: completed for tenant=%s", tenant_id)
