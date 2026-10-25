"""Add the private macOS update catalog."""

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0005_macos_updates"
down_revision: str | Sequence[str] | None = "0004_feedback_inbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "macos_releases",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("bundle_id", sa.String(255), nullable=False),
        sa.Column("build_number", sa.BigInteger(), nullable=False),
        sa.Column("version_name", sa.String(128), nullable=False),
        sa.Column("release_notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("designated_requirement", sa.Text(), nullable=False),
        sa.Column("signing_authority", sa.String(256), nullable=False),
        sa.Column("artifact_name", sa.String(255), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint(
            "bundle_id",
            "build_number",
            name="uq_macos_release_bundle_build",
        ),
    )
    op.create_index("ix_macos_releases_bundle_id", "macos_releases", ["bundle_id"])
    op.create_index("ix_macos_releases_revoked", "macos_releases", ["revoked"])

    op.create_table(
        "macos_update_tickets",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("token_digest", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "controller_device_id",
            sa.String(64),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "mac_device_id",
            sa.String(64),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "release_id",
            sa.String(64),
            sa.ForeignKey("macos_releases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("release_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )
    for column in (
        "token_digest",
        "user_id",
        "controller_device_id",
        "mac_device_id",
        "release_id",
        "expires_at",
        "used_at",
    ):
        op.create_index(
            f"ix_macos_update_tickets_{column}",
            "macos_update_tickets",
            [column],
        )


def downgrade() -> None:
    op.drop_table("macos_update_tickets")
    op.drop_table("macos_releases")
