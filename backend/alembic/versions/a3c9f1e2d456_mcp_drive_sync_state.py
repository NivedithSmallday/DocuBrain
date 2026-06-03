"""mcp_drive_sync_state_and_doc_tracking

Revision ID: a3c9f1e2d456
Revises: 2f7e8b9d1c33
Create Date: 2026-05-28 12:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "a3c9f1e2d456"
down_revision = "2f7e8b9d1c33"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_drive_sync_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(), server_default="", nullable=False),
        sa.Column(
            "last_sync_started_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "last_sync_completed_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "last_successful_sync_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("last_change_token", sa.String(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "total_docs_indexed",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "docs_skipped", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column(
            "status", sa.String(), server_default="idle", nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "tenant_id", name="uq_mcp_drive_sync_user_tenant"
        ),
    )

    op.create_table(
        "mcp_drive_doc_tracking",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.String(), nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(), server_default="", nullable=False),
        sa.Column("drive_file_id", sa.String(), nullable=False),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("content_hash", sa.String(), nullable=True),
        sa.Column(
            "is_stale", sa.Boolean(), server_default="false", nullable=False
        ),
        sa.Column(
            "consecutive_misses",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "drive_file_id",
            "user_id",
            "tenant_id",
            name="uq_mcp_drive_doc_file_user_tenant",
        ),
    )
    op.create_index(
        "ix_mcp_drive_doc_tracking_document_id",
        "mcp_drive_doc_tracking",
        ["document_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_mcp_drive_doc_tracking_document_id",
        table_name="mcp_drive_doc_tracking",
    )
    op.drop_table("mcp_drive_doc_tracking")
    op.drop_table("mcp_drive_sync_state")
