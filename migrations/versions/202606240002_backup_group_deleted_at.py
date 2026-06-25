"""Add logical delete marker to backup groups.

Revision ID: 202606240002
Revises: 202606240001
Create Date: 2026-06-24 00:02:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "202606240002"
down_revision: str | None = "202606240001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.add_column(
        "backup_groups",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_column("backup_groups", "deleted_at")
