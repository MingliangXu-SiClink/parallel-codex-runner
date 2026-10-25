"""Add the product-level feedback inbox."""

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0004_feedback_inbox"
down_revision: str | Sequence[str] | None = "0003_android_updates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "feedback",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "submitter_device_id",
            sa.String(64),
            sa.ForeignKey("devices.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "related_device_id",
            sa.String(64),
            sa.ForeignKey("devices.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("client_feedback_id", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("diagnostics", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("developer_reply", sa.Text(), nullable=False, server_default=""),
        sa.Column("fixed_version", sa.String(128), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "user_id",
            "client_feedback_id",
            name="uq_feedback_user_client_id",
        ),
    )
    for column in (
        "user_id",
        "submitter_device_id",
        "related_device_id",
        "kind",
        "status",
        "updated_at",
    ):
        op.create_index(f"ix_feedback_{column}", "feedback", [column])
    op.create_index(
        "ix_feedback_user_created",
        "feedback",
        ["user_id", "created_at", "id"],
    )

    op.create_table(
        "feedback_notes",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "feedback_id",
            sa.String(64),
            sa.ForeignKey("feedback.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("author", sa.String(32), nullable=False),
        sa.Column(
            "author_device_id",
            sa.String(64),
            sa.ForeignKey("devices.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("client_note_id", sa.String(128), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "feedback_id",
            "client_note_id",
            name="uq_feedback_note_client_id",
        ),
    )
    op.create_index("ix_feedback_notes_feedback_id", "feedback_notes", ["feedback_id"])

    op.create_table(
        "feedback_attachments",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "feedback_id",
            sa.String(64),
            sa.ForeignKey("feedback.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content_type", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "feedback_id",
            "sha256",
            name="uq_feedback_attachment_digest",
        ),
    )
    op.create_index(
        "ix_feedback_attachments_feedback_id",
        "feedback_attachments",
        ["feedback_id"],
    )

    op.create_table(
        "feedback_activity",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "feedback_id",
            sa.String(64),
            sa.ForeignKey("feedback.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("actor", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default=""),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("version", sa.String(128), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_feedback_activity_feedback_id",
        "feedback_activity",
        ["feedback_id"],
    )
    op.create_index(
        "ix_feedback_activity_created_at",
        "feedback_activity",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_table("feedback_activity")
    op.drop_table("feedback_attachments")
    op.drop_table("feedback_notes")
    op.drop_table("feedback")
