from __future__ import annotations

import datetime as dt
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from anyio import CancelScope
from sqlalchemy import (
    Boolean,
    BigInteger,
    JSON,
    LargeBinary,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    delete,
    event,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


class Base(DeclarativeBase):
    pass


class UserRecord(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    wechat_subject: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class DeviceRecord(Base):
    __tablename__ = "devices"
    __table_args__ = (
        UniqueConstraint("kind", "public_key", name="uq_device_kind_public_key"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(128))
    public_key: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    authorization_generation: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    last_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class PairingRecord(Base):
    __tablename__ = "pairings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    mac_device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), index=True
    )
    pairing_commitment: Mapped[str] = mapped_column(String(64))
    approval_commitment: Mapped[str] = mapped_column(String(64))
    poll_digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    approved_by_device_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    claimed_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    pairing_proof: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class RefreshTokenRecord(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), index=True
    )
    token_digest: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class DeviceLoginChallengeRecord(Base):
    __tablename__ = "device_login_challenges"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    public_key: Mapped[str] = mapped_column(String(128))
    authorization_generation: Mapped[int] = mapped_column(Integer)
    proof_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class EnvelopeRecord(Base):
    __tablename__ = "envelopes"
    __table_args__ = (
        UniqueConstraint("sender_device_id", "sequence", name="uq_sender_sequence"),
        Index("ix_target_pending", "target_device_id", "acknowledged", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    sender_device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), index=True
    )
    target_device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(BigInteger)
    ciphertext: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)


class AndroidReleaseRecord(Base):
    __tablename__ = "android_releases"
    __table_args__ = (
        UniqueConstraint(
            "package_name",
            "version_code",
            name="uq_android_release_package_version",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    package_name: Mapped[str] = mapped_column(String(255), index=True)
    version_code: Mapped[int] = mapped_column(BigInteger)
    version_name: Mapped[str] = mapped_column(String(128))
    release_notes: Mapped[str] = mapped_column(Text, default="")
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    signing_cert_sha256: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )
    artifact_name: Mapped[str] = mapped_column(String(255), unique=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class AndroidUpdateTicketRecord(Base):
    __tablename__ = "android_update_tickets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    token_digest: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    android_device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), index=True
    )
    mac_device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), index=True
    )
    release_id: Mapped[str] = mapped_column(
        ForeignKey("android_releases.id", ondelete="CASCADE"), index=True
    )
    release_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )


class MacOSReleaseRecord(Base):
    __tablename__ = "macos_releases"
    __table_args__ = (
        UniqueConstraint(
            "bundle_id",
            "build_number",
            name="uq_macos_release_bundle_build",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    bundle_id: Mapped[str] = mapped_column(String(255), index=True)
    build_number: Mapped[int] = mapped_column(BigInteger)
    version_name: Mapped[str] = mapped_column(String(128))
    release_notes: Mapped[str] = mapped_column(Text, default="")
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    designated_requirement: Mapped[str] = mapped_column(Text)
    signing_authority: Mapped[str] = mapped_column(String(256))
    artifact_name: Mapped[str] = mapped_column(String(255), unique=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class MacOSUpdateTicketRecord(Base):
    __tablename__ = "macos_update_tickets"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    token_digest: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    controller_device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), index=True
    )
    mac_device_id: Mapped[str] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), index=True
    )
    release_id: Mapped[str] = mapped_column(
        ForeignKey("macos_releases.id", ondelete="CASCADE"), index=True
    )
    release_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )


class FeedbackRecord(Base):
    __tablename__ = "feedback"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "client_feedback_id",
            name="uq_feedback_user_client_id",
        ),
        Index("ix_feedback_user_created", "user_id", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    submitter_device_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL"), nullable=True, index=True
    )
    related_device_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL"), nullable=True, index=True
    )
    client_feedback_id: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(32), index=True)
    description: Mapped[str] = mapped_column(Text)
    diagnostics: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="submitted", index=True)
    developer_reply: Mapped[str] = mapped_column(Text, default="")
    fixed_version: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )
    withdrawn_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class FeedbackNoteRecord(Base):
    __tablename__ = "feedback_notes"
    __table_args__ = (
        UniqueConstraint(
            "feedback_id",
            "client_note_id",
            name="uq_feedback_note_client_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    feedback_id: Mapped[str] = mapped_column(
        ForeignKey("feedback.id", ondelete="CASCADE"), index=True
    )
    author: Mapped[str] = mapped_column(String(32))
    author_device_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL"), nullable=True
    )
    client_note_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class FeedbackAttachmentRecord(Base):
    __tablename__ = "feedback_attachments"
    __table_args__ = (
        UniqueConstraint(
            "feedback_id",
            "sha256",
            name="uq_feedback_attachment_digest",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    feedback_id: Mapped[str] = mapped_column(
        ForeignKey("feedback.id", ondelete="CASCADE"), index=True
    )
    content_type: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    content: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class FeedbackActivityRecord(Base):
    __tablename__ = "feedback_activity"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    feedback_id: Mapped[str] = mapped_column(
        ForeignKey("feedback.id", ondelete="CASCADE"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )


class Database:
    def __init__(self, url: str) -> None:
        if url.startswith("postgresql://"):
            url = "postgresql+asyncpg://" + url.removeprefix("postgresql://")
        self.engine: AsyncEngine = create_async_engine(url, pool_pre_ping=True)
        if self.engine.url.get_backend_name() == "sqlite":
            @event.listens_for(self.engine.sync_engine, "connect")
            def enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
                cursor = dbapi_connection.cursor()
                try:
                    cursor.execute("PRAGMA foreign_keys=ON")
                finally:
                    cursor.close()
        self.sessions = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def create_schema(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def verify_schema(self) -> None:
        def has_required_tables(connection) -> bool:
            from sqlalchemy import inspect

            inspector = inspect(connection)
            return all(
                inspector.has_table(name)
                for name in (
                    "users",
                    "devices",
                    "device_login_challenges",
                    "pairings",
                    "envelopes",
                    "android_releases",
                    "android_update_tickets",
                    "macos_releases",
                    "macos_update_tickets",
                    "feedback",
                    "feedback_notes",
                    "feedback_attachments",
                    "feedback_activity",
                )
            ) and "authorization_generation" in {
                column["name"] for column in inspector.get_columns("devices")
            }

        async with self.engine.connect() as connection:
            valid = await connection.run_sync(has_required_tables)
        if not valid:
            raise RuntimeError(
                "PCR relay database is not migrated; run `alembic upgrade head`"
            )

    @asynccontextmanager
    async def session_scope(self) -> AsyncIterator[AsyncSession]:
        session = self.sessions()
        try:
            yield session
        finally:
            # WebSocket disconnects cancel their route immediately. Letting
            # that cancellation interrupt aiosqlite rollback leaves a detached
            # close task racing database disposal during shutdown.
            with CancelScope(shield=True):
                await session.close()

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_scope() as session:
            yield session

    async def cleanup_expired(self) -> None:
        now = utc_now()
        async with self.session_scope() as session:
            await session.execute(
                delete(EnvelopeRecord).where(
                    (EnvelopeRecord.expires_at <= now)
                    | (EnvelopeRecord.acknowledged.is_(True))
                )
            )
            await session.execute(
                delete(PairingRecord).where(PairingRecord.expires_at <= now)
            )
            await session.execute(
                delete(DeviceLoginChallengeRecord).where(
                    DeviceLoginChallengeRecord.expires_at <= now
                )
            )
            await session.execute(
                delete(RefreshTokenRecord).where(
                    (RefreshTokenRecord.expires_at <= now)
                    | (RefreshTokenRecord.revoked.is_(True))
                )
            )
            await session.execute(
                delete(AndroidUpdateTicketRecord).where(
                    (AndroidUpdateTicketRecord.expires_at <= now)
                    | AndroidUpdateTicketRecord.used_at.is_not(None)
                )
            )
            await session.execute(
                delete(MacOSUpdateTicketRecord).where(
                    (MacOSUpdateTicketRecord.expires_at <= now)
                    | MacOSUpdateTicketRecord.used_at.is_not(None)
                )
            )
            await session.commit()

    async def close(self) -> None:
        await self.engine.dispose()
