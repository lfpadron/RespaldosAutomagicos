"""Add retention metadata to backup history.

Revision ID: 202606240003
Revises: 202606240002
Create Date: 2026-06-24 00:03:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "202606240003"
down_revision: str | None = "202606240002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.add_column(
        "backup_history",
        sa.Column("retained", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "backup_history",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "backup_history",
        sa.Column("deletion_reason", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_column("backup_history", "deletion_reason")
    op.drop_column("backup_history", "deleted_at")
    op.drop_column("backup_history", "retained")
