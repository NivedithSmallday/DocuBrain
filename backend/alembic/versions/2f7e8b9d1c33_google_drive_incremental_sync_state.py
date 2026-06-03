"""google_drive_incremental_sync_state

Revision ID: 2f7e8b9d1c33
Revises: 503883791c39
Create Date: 2026-05-26 15:05:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from docubrain.db.enums import GoogleDriveSyncScopeType
from docubrain.db.enums import SyncStatus


# revision identifiers, used by Alembic.
revision = "2f7e8b9d1c33"
down_revision = "503883791c39"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "google_drive_sync_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("connector_credential_pair_id", sa.Integer(), nullable=False),
        sa.Column(
            "scope_type",
            sa.Enum(GoogleDriveSyncScopeType, native_enum=False),
            server_default=GoogleDriveSyncScopeType.USER.value,
            nullable=False,
        ),
        sa.Column("drive_id", sa.String(), server_default="", nullable=False),
        sa.Column("page_token", sa.String(), nullable=True),
        sa.Column("last_sync_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_successful_sync_time", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "full_sync_required", sa.Boolean(), server_default="true", nullable=False
        ),
        sa.Column(
            "token_invalid", sa.Boolean(), server_default="false", nullable=False
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["connector_credential_pair_id"],
            ["connector_credential_pair.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "connector_credential_pair_id",
            "scope_type",
            "drive_id",
            name="uq_google_drive_sync_state_scope",
        ),
    )
    op.create_index(
        "ix_google_drive_sync_state_cc_pair",
        "google_drive_sync_state",
        ["connector_credential_pair_id"],
    )

    op.create_table(
        "google_drive_indexed_file_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("connector_credential_pair_id", sa.Integer(), nullable=False),
        sa.Column("file_id", sa.String(), nullable=False),
        sa.Column("drive_id", sa.String(), nullable=True),
        sa.Column("document_id", sa.String(), nullable=True),
        sa.Column("content_fingerprint", sa.String(), nullable=True),
        sa.Column("acl_fingerprint", sa.String(), nullable=True),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("mime_type", sa.String(), nullable=True),
        sa.Column("parents", postgresql.JSONB(), nullable=True),
        sa.Column("modified_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_change_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("raw_metadata", postgresql.JSONB(), nullable=True),
        sa.ForeignKeyConstraint(
            ["connector_credential_pair_id"],
            ["connector_credential_pair.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["document_id"], ["document.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "connector_credential_pair_id",
            "file_id",
            name="uq_google_drive_indexed_file_state_file",
        ),
    )
    op.create_index(
        "ix_google_drive_indexed_file_state_cc_pair",
        "google_drive_indexed_file_state",
        ["connector_credential_pair_id"],
    )
    op.create_index(
        "ix_google_drive_indexed_file_state_document",
        "google_drive_indexed_file_state",
        ["document_id"],
    )

    op.create_table(
        "google_drive_sync_job",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("connector_credential_pair_id", sa.Integer(), nullable=False),
        sa.Column("sync_state_id", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(SyncStatus, native_enum=False),
            server_default=SyncStatus.IN_PROGRESS.value,
            nullable=False,
        ),
        sa.Column("page_token_started", sa.String(), nullable=True),
        sa.Column("page_token_finished", sa.String(), nullable=True),
        sa.Column(
            "changed_files_seen", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("files_queued", sa.Integer(), server_default="0", nullable=False),
        sa.Column("files_deleted", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "permission_updates", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "time_created",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.Column("time_started", sa.DateTime(timezone=True), nullable=True),
        sa.Column("time_finished", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["connector_credential_pair_id"],
            ["connector_credential_pair.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["sync_state_id"], ["google_drive_sync_state.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_google_drive_sync_job_cc_pair_time",
        "google_drive_sync_job",
        ["connector_credential_pair_id", "time_created"],
    )
    op.create_index("ix_google_drive_sync_job_status", "google_drive_sync_job", ["status"])


def downgrade() -> None:
    op.drop_index("ix_google_drive_sync_job_status", table_name="google_drive_sync_job")
    op.drop_index(
        "ix_google_drive_sync_job_cc_pair_time", table_name="google_drive_sync_job"
    )
    op.drop_table("google_drive_sync_job")
    op.drop_index(
        "ix_google_drive_indexed_file_state_document",
        table_name="google_drive_indexed_file_state",
    )
    op.drop_index(
        "ix_google_drive_indexed_file_state_cc_pair",
        table_name="google_drive_indexed_file_state",
    )
    op.drop_table("google_drive_indexed_file_state")
    op.drop_index(
        "ix_google_drive_sync_state_cc_pair", table_name="google_drive_sync_state"
    )
    op.drop_table("google_drive_sync_state")
