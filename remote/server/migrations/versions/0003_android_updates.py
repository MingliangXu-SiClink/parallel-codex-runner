"""Add private Android releases and one-use download tickets."""

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0003_android_updates"
down_revision: str | Sequence[str] | None = "0002_pairing_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "android_releases",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("package_name", sa.String(255), nullable=False),
        sa.Column("version_code", sa.BigInteger(), nullable=False),
        sa.Column("version_name", sa.String(128), nullable=False),
        sa.Column("release_notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("signing_cert_sha256", sa.String(64), nullable=True),
        sa.Column("artifact_name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint(
            "package_name",
            "version_code",
            name="uq_android_release_package_version",
        ),
        sa.UniqueConstraint("artifact_name", name="uq_android_release_artifact"),
    )
    op.create_index(
        "ix_android_releases_package_name",
        "android_releases",
        ["package_name"],
    )
    op.create_index(
        "ix_android_releases_revoked",
        "android_releases",
        ["revoked"],
    )

    op.create_table(
        "android_update_tickets",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("token_digest", sa.String(64), nullable=False),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "android_device_id",
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
            sa.ForeignKey("android_releases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("release_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("token_digest", name="uq_android_update_ticket_digest"),
    )
    for column in (
        "token_digest",
        "user_id",
        "android_device_id",
        "mac_device_id",
        "release_id",
        "expires_at",
        "used_at",
    ):
        op.create_index(
            f"ix_android_update_tickets_{column}",
            "android_update_tickets",
            [column],
        )


def downgrade() -> None:
    op.drop_table("android_update_tickets")
    op.drop_table("android_releases")
