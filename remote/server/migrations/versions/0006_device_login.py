"""Keep device trust independent of short-lived login credentials."""

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0006_device_login"
down_revision: str | Sequence[str] | None = "0005_macos_updates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "devices",
        sa.Column("authorization_generation", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "device_login_challenges",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("device_id", sa.String(64), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("public_key", sa.String(128), nullable=False),
        sa.Column("authorization_generation", sa.Integer(), nullable=False),
        sa.Column("proof_digest", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_device_login_challenges_device_id", "device_login_challenges", ["device_id"])
    op.create_index("ix_device_login_challenges_expires_at", "device_login_challenges", ["expires_at"])


def downgrade() -> None:
    op.drop_table("device_login_challenges")
    op.drop_column("devices", "authorization_generation")
