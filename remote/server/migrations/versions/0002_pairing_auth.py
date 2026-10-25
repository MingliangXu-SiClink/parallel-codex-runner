"""Add one-time Android pairing authentication."""

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0002_pairing_auth"
down_revision: str | Sequence[str] | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "pairings",
        sa.Column(
            "approval_commitment",
            sa.String(64),
            nullable=False,
            server_default="",
        ),
    )
    with op.batch_alter_table("pairings") as batch:
        batch.alter_column("approval_commitment", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("pairings") as batch:
        batch.drop_column("approval_commitment")
