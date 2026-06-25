"""Add restore metadata to backup history.

Revision ID: 202606240004
Revises: 202606240003
Create Date: 2026-06-24 00:04:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "202606240004"
down_revision: str | None = "202606240003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.add_column(
        "backup_history",
        sa.Column("last_restored_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "backup_history",
        sa.Column("restore_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_column("backup_history", "restore_count")
    op.drop_column("backup_history", "last_restored_at")
