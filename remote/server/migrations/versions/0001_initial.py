"""Create the initial PCR relay schema."""

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0001_initial"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("wechat_subject", sa.String(256), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("wechat_subject"),
    )
    op.create_index("ix_users_wechat_subject", "users", ["wechat_subject"])
    op.create_index("ix_users_status", "users", ["status"])
    op.create_table(
        "devices",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("public_key", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("kind", "public_key", name="uq_device_kind_public_key"),
    )
    op.create_index("ix_devices_user_id", "devices", ["user_id"])
    op.create_index("ix_devices_kind", "devices", ["kind"])
    op.create_index("ix_devices_status", "devices", ["status"])
    op.create_table(
        "pairings",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "mac_device_id",
            sa.String(64),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("pairing_commitment", sa.String(64), nullable=False),
        sa.Column("poll_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "approved_by_device_id",
            sa.String(64),
            sa.ForeignKey("devices.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pairing_proof", sa.String(64), nullable=True),
    )
    op.create_index("ix_pairings_mac_device_id", "pairings", ["mac_device_id"])
    op.create_index("ix_pairings_status", "pairings", ["status"])
    op.create_index("ix_pairings_expires_at", "pairings", ["expires_at"])
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "device_id",
            sa.String(64),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_digest", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("token_digest"),
    )
    op.create_index("ix_refresh_tokens_device_id", "refresh_tokens", ["device_id"])
    op.create_index("ix_refresh_tokens_token_digest", "refresh_tokens", ["token_digest"])
    op.create_index("ix_refresh_tokens_expires_at", "refresh_tokens", ["expires_at"])
    op.create_table(
        "envelopes",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "sender_device_id",
            sa.String(64),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_device_id",
            sa.String(64),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("sender_device_id", "sequence", name="uq_sender_sequence"),
    )
    op.create_index("ix_envelopes_user_id", "envelopes", ["user_id"])
    op.create_index("ix_envelopes_sender_device_id", "envelopes", ["sender_device_id"])
    op.create_index("ix_envelopes_target_device_id", "envelopes", ["target_device_id"])
    op.create_index("ix_envelopes_expires_at", "envelopes", ["expires_at"])
    op.create_index(
        "ix_target_pending",
        "envelopes",
        ["target_device_id", "acknowledged", "expires_at"],
    )


def downgrade() -> None:
    op.drop_table("envelopes")
    op.drop_table("refresh_tokens")
    op.drop_table("pairings")
    op.drop_table("devices")
    op.drop_table("users")
