"""Add timezone to backup groups.

Revision ID: 202606250001
Revises: 202606240004
Create Date: 2026-06-25 00:01:00
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "202606250001"
down_revision: str | None = "202606240004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.add_column(
        "backup_groups",
        sa.Column(
            "timezone",
            sa.String(length=128),
            nullable=False,
            server_default="UTC",
        ),
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_column("backup_groups", "timezone")
