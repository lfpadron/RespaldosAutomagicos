"""Create initial schema.

Revision ID: 202606240001
Revises:
Create Date: 2026-06-24 00:01:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "202606240001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the initial schema migration."""
    op.create_table(
        "backup_groups",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("root_directory", sa.String(length=1024), nullable=False),
        sa.Column("destination_directory", sa.String(length=1024), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("scan_interval_minutes", sa.Integer(), nullable=False),
        sa.Column("stabilization_minutes", sa.Integer(), nullable=False),
        sa.Column("backups_to_keep", sa.Integer(), nullable=False),
        sa.Column("days_to_keep", sa.Integer(), nullable=True),
        sa.Column("compression_level", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "watched_directories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("relative_path", sa.String(length=1024), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("pending_backup", sa.Boolean(), nullable=False),
        sa.Column("backup_running", sa.Boolean(), nullable=False),
        sa.Column("last_change_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_backup_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_content_hash", sa.String(length=128), nullable=True),
        sa.Column("rolling_counter", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["backup_groups.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "group_id",
            "relative_path",
            name="uq_watched_directory_path",
        ),
    )
    op.create_index(
        op.f("ix_watched_directories_group_id"),
        "watched_directories",
        ["group_id"],
        unique=False,
    )
    op.create_table(
        "backup_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=False),
        sa.Column("watched_directory_id", sa.Integer(), nullable=True),
        sa.Column("backup_name", sa.String(length=255), nullable=False),
        sa.Column("backup_path", sa.String(length=1024), nullable=False),
        sa.Column("backup_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("backup_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("file_count", sa.Integer(), nullable=True),
        sa.Column("content_hash", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["backup_groups.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["watched_directory_id"],
            ["watched_directories.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_backup_history_backup_time"),
        "backup_history",
        ["backup_time"],
        unique=False,
    )
    op.create_index(
        op.f("ix_backup_history_group_id"),
        "backup_history",
        ["group_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_backup_history_watched_directory_id"),
        "backup_history",
        ["watched_directory_id"],
        unique=False,
    )
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("group_id", sa.Integer(), nullable=True),
        sa.Column("watched_directory_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("result", sa.String(length=64), nullable=False),
        sa.Column("details", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["group_id"], ["backup_groups.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["watched_directory_id"],
            ["watched_directories.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_audit_logs_group_id"),
        "audit_logs",
        ["group_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_audit_logs_timestamp"),
        "audit_logs",
        ["timestamp"],
        unique=False,
    )
    op.create_index(
        op.f("ix_audit_logs_watched_directory_id"),
        "audit_logs",
        ["watched_directory_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the initial schema."""
    op.drop_index(op.f("ix_audit_logs_watched_directory_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_timestamp"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_group_id"), table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index(
        op.f("ix_backup_history_watched_directory_id"),
        table_name="backup_history",
    )
    op.drop_index(op.f("ix_backup_history_group_id"), table_name="backup_history")
    op.drop_index(op.f("ix_backup_history_backup_time"), table_name="backup_history")
    op.drop_table("backup_history")
    op.drop_index(
        op.f("ix_watched_directories_group_id"),
        table_name="watched_directories",
    )
    op.drop_table("watched_directories")
    op.drop_table("backup_groups")
