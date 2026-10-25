from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import AccessClaims
from .config import Settings
from .db import (
    DeviceRecord,
    FeedbackActivityRecord,
    FeedbackAttachmentRecord,
    FeedbackNoteRecord,
    FeedbackRecord,
    new_id,
    utc_now,
)


class FeedbackServiceError(RuntimeError):
    pass


class FeedbackNotFound(FeedbackServiceError):
    pass


class FeedbackAccessDenied(FeedbackServiceError):
    pass


class FeedbackInvalid(FeedbackServiceError):
    pass


class FeedbackQuotaExceeded(FeedbackServiceError):
    pass


@dataclass(frozen=True)
class FeedbackAttachmentDownload:
    content_type: str
    content: bytes
    sha256: str


class FeedbackService:
    ALLOWED_STATUSES = {
        "submitted",
        "confirmed",
        "in_progress",
        "fixed",
        "declined",
        "withdrawn",
    }
    CLOSED_STATUSES = {"fixed", "declined", "withdrawn"}

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def submit(
        self,
        session: AsyncSession,
        claims: AccessClaims,
        *,
        client_feedback_id: str,
        kind: str,
        description: str,
        diagnostics: dict[str, object],
        related_device_id: str | None,
    ) -> dict[str, Any]:
        normalized_description = description.strip()
        normalized_diagnostics = self._diagnostics(diagnostics)
        normalized_related = (related_device_id or "").strip() or None
        existing = await session.scalar(
            select(FeedbackRecord).where(
                FeedbackRecord.user_id == claims.user_id,
                FeedbackRecord.client_feedback_id == client_feedback_id,
            )
        )
        if existing is not None:
            if (
                existing.kind != kind
                or existing.description != normalized_description
                or existing.diagnostics != normalized_diagnostics
                or existing.related_device_id != normalized_related
            ):
                raise FeedbackInvalid("This feedback identifier was already used.")
            return await self._one(session, existing)

        if normalized_related is not None:
            related = await session.get(DeviceRecord, normalized_related)
            if related is None or related.user_id != claims.user_id:
                raise FeedbackAccessDenied("The related device is not available.")

        recent_since = utc_now() - dt.timedelta(hours=1)
        recent_count = await session.scalar(
            select(func.count(FeedbackRecord.id)).where(
                FeedbackRecord.user_id == claims.user_id,
                FeedbackRecord.created_at >= recent_since,
            )
        )
        if int(recent_count or 0) >= self.settings.feedback_submissions_per_hour:
            raise FeedbackQuotaExceeded("Too many feedback submissions. Try again later.")
        active_count = await session.scalar(
            select(func.count(FeedbackRecord.id)).where(
                FeedbackRecord.user_id == claims.user_id,
                FeedbackRecord.status.not_in(self.CLOSED_STATUSES),
            )
        )
        if int(active_count or 0) >= self.settings.feedback_max_active_per_user:
            raise FeedbackQuotaExceeded("The feedback inbox limit was reached.")

        now = utc_now()
        item = FeedbackRecord(
            id=new_id("feedback"),
            user_id=claims.user_id,
            submitter_device_id=claims.device_id,
            related_device_id=normalized_related,
            client_feedback_id=client_feedback_id,
            kind=kind,
            description=normalized_description,
            diagnostics=normalized_diagnostics,
            status="submitted",
            created_at=now,
            updated_at=now,
        )
        session.add(item)
        try:
            # FeedbackActivityRecord has an immediate foreign key to the parent.
            # Flush explicitly so PostgreSQL never receives the child first.
            await session.flush()
            session.add(
                FeedbackActivityRecord(
                    id=new_id("activity"),
                    feedback_id=item.id,
                    event_type="status",
                    actor="user",
                    status="submitted",
                    created_at=now,
                )
            )
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
        return await self._one(session, item)

    async def list_for_user(
        self,
        session: AsyncSession,
        claims: AccessClaims,
        *,
        cursor: str,
        limit: int,
    ) -> dict[str, Any]:
        statement = select(FeedbackRecord).where(
            FeedbackRecord.user_id == claims.user_id
        )
        boundary = self._decode_cursor(cursor) if cursor else None
        if boundary is not None:
            created_at, feedback_id = boundary
            statement = statement.where(
                or_(
                    FeedbackRecord.created_at < created_at,
                    and_(
                        FeedbackRecord.created_at == created_at,
                        FeedbackRecord.id < feedback_id,
                    ),
                )
            )
        records = list(
            (
                await session.scalars(
                    statement.order_by(
                        FeedbackRecord.created_at.desc(),
                        FeedbackRecord.id.desc(),
                    ).limit(limit + 1)
                )
            ).all()
        )
        has_more = len(records) > limit
        page = records[:limit]
        items = await self._many(session, page)
        next_cursor = ""
        if has_more and page:
            last = page[-1]
            next_cursor = self._encode_cursor(last.created_at, last.id)
        return {
            "items": items,
            "next_cursor": next_cursor,
            "has_more": has_more,
        }

    async def get(
        self,
        session: AsyncSession,
        claims: AccessClaims,
        feedback_id: str,
    ) -> dict[str, Any]:
        return await self._one(session, await self._owned(session, claims, feedback_id))

    async def add_note(
        self,
        session: AsyncSession,
        claims: AccessClaims,
        feedback_id: str,
        *,
        client_note_id: str,
        text: str,
    ) -> dict[str, Any]:
        item = await self._owned(session, claims, feedback_id)
        if item.status == "withdrawn":
            raise FeedbackInvalid("Withdrawn feedback cannot be updated.")
        existing = await session.scalar(
            select(FeedbackNoteRecord).where(
                FeedbackNoteRecord.feedback_id == item.id,
                FeedbackNoteRecord.client_note_id == client_note_id,
            )
        )
        normalized = text.strip()
        if existing is not None:
            if existing.text != normalized:
                raise FeedbackInvalid("This note identifier was already used.")
            return await self._one(session, item)
        now = utc_now()
        session.add(
            FeedbackNoteRecord(
                id=new_id("note"),
                feedback_id=item.id,
                author="user",
                author_device_id=claims.device_id,
                client_note_id=client_note_id,
                text=normalized,
                created_at=now,
            )
        )
        session.add(
            FeedbackActivityRecord(
                id=new_id("activity"),
                feedback_id=item.id,
                event_type="note",
                actor="user",
                text=normalized,
                created_at=now,
            )
        )
        item.updated_at = now
        await session.commit()
        return await self._one(session, item)

    async def add_attachment(
        self,
        session: AsyncSession,
        claims: AccessClaims,
        feedback_id: str,
        *,
        content_type: str,
        encoded: str,
    ) -> dict[str, Any]:
        item = await self._owned(session, claims, feedback_id)
        if item.status == "withdrawn":
            raise FeedbackInvalid("Withdrawn feedback cannot be updated.")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise FeedbackInvalid("The screenshot is not valid base64 data.") from exc
        if not content or len(content) > self.settings.feedback_max_attachment_bytes:
            raise FeedbackInvalid("The screenshot is too large.")
        if not self._matches_image_type(content_type, content):
            raise FeedbackInvalid("The screenshot content does not match its file type.")
        digest = hashlib.sha256(content).hexdigest()
        existing = await session.scalar(
            select(FeedbackAttachmentRecord).where(
                FeedbackAttachmentRecord.feedback_id == item.id,
                FeedbackAttachmentRecord.sha256 == digest,
            )
        )
        if existing is not None:
            return await self._one(session, item)
        existing_rows = list(
            (
                await session.scalars(
                    select(FeedbackAttachmentRecord).where(
                        FeedbackAttachmentRecord.feedback_id == item.id
                    )
                )
            ).all()
        )
        if len(existing_rows) >= self.settings.feedback_max_attachments:
            raise FeedbackQuotaExceeded("This feedback has too many screenshots.")
        total = sum(row.size_bytes for row in existing_rows) + len(content)
        if total > self.settings.feedback_max_total_attachment_bytes:
            raise FeedbackQuotaExceeded("The feedback screenshot limit was reached.")
        now = utc_now()
        session.add(
            FeedbackAttachmentRecord(
                id=new_id("attachment"),
                feedback_id=item.id,
                content_type=content_type,
                size_bytes=len(content),
                sha256=digest,
                content=content,
                created_at=now,
            )
        )
        item.updated_at = now
        await session.commit()
        return await self._one(session, item)

    async def remove_attachment(
        self,
        session: AsyncSession,
        claims: AccessClaims,
        feedback_id: str,
        attachment_id: str,
    ) -> dict[str, Any]:
        item = await self._owned(session, claims, feedback_id)
        if item.status == "withdrawn":
            raise FeedbackInvalid("Withdrawn feedback cannot be updated.")
        attachment = await session.scalar(
            select(FeedbackAttachmentRecord).where(
                FeedbackAttachmentRecord.feedback_id == item.id,
                FeedbackAttachmentRecord.id == attachment_id,
            )
        )
        if attachment is None:
            raise FeedbackNotFound("Feedback attachment was not found.")
        await session.delete(attachment)
        item.updated_at = utc_now()
        await session.commit()
        return await self._one(session, item)

    async def attachment(
        self,
        session: AsyncSession,
        claims: AccessClaims,
        feedback_id: str,
        attachment_id: str,
    ) -> FeedbackAttachmentDownload:
        item = await self._owned(session, claims, feedback_id)
        attachment = await session.scalar(
            select(FeedbackAttachmentRecord).where(
                FeedbackAttachmentRecord.feedback_id == item.id,
                FeedbackAttachmentRecord.id == attachment_id,
            )
        )
        if attachment is None:
            raise FeedbackNotFound("Feedback attachment was not found.")
        return FeedbackAttachmentDownload(
            content_type=attachment.content_type,
            content=attachment.content,
            sha256=attachment.sha256,
        )

    async def withdraw(
        self,
        session: AsyncSession,
        claims: AccessClaims,
        feedback_id: str,
    ) -> dict[str, Any]:
        item = await self._owned(session, claims, feedback_id)
        if item.status == "withdrawn":
            return await self._one(session, item)
        if item.status != "submitted":
            raise FeedbackInvalid("Only unreviewed feedback can be withdrawn.")
        now = utc_now()
        item.status = "withdrawn"
        item.withdrawn_at = now
        item.updated_at = now
        session.add(
            FeedbackActivityRecord(
                id=new_id("activity"),
                feedback_id=item.id,
                event_type="status",
                actor="user",
                status="withdrawn",
                created_at=now,
            )
        )
        await session.commit()
        return await self._one(session, item)

    async def admin_list(
        self,
        session: AsyncSession,
        *,
        status: str = "",
        version: str = "",
        device_id: str = "",
        workspace: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        statement = select(FeedbackRecord)
        if status:
            statement = statement.where(FeedbackRecord.status == status)
        if version:
            statement = statement.where(FeedbackRecord.fixed_version == version)
        if device_id:
            statement = statement.where(FeedbackRecord.related_device_id == device_id)
        records = list(
            (
                await session.scalars(
                    statement.order_by(
                        FeedbackRecord.updated_at.desc(),
                        FeedbackRecord.id.desc(),
                    ).limit(limit)
                )
            ).all()
        )
        if workspace:
            records = [
                item
                for item in records
                if workspace in str((item.diagnostics or {}).get("workspace_id", ""))
                or workspace in str((item.diagnostics or {}).get("workspace_name", ""))
            ]
        return await self._many(session, records)

    async def admin_get(
        self,
        session: AsyncSession,
        feedback_id: str,
    ) -> dict[str, Any]:
        item = await session.get(FeedbackRecord, feedback_id)
        if item is None:
            raise FeedbackNotFound("Feedback was not found.")
        return await self._one(session, item)

    async def admin_update(
        self,
        session: AsyncSession,
        feedback_id: str,
        *,
        status: str,
        developer_reply: str | None,
        fixed_version: str | None,
    ) -> dict[str, Any]:
        item = await session.get(FeedbackRecord, feedback_id)
        if item is None:
            raise FeedbackNotFound("Feedback was not found.")
        if status and status not in self.ALLOWED_STATUSES - {"withdrawn"}:
            raise FeedbackInvalid("Unsupported feedback status.")
        next_status = status or item.status
        next_version = item.fixed_version if fixed_version is None else fixed_version.strip()
        if next_status == "fixed" and not next_version:
            raise FeedbackInvalid("Fixed feedback requires a release version.")
        now = utc_now()
        if status and status != item.status:
            item.status = status
            session.add(
                FeedbackActivityRecord(
                    id=new_id("activity"),
                    feedback_id=item.id,
                    event_type="status",
                    actor="developer",
                    status=status,
                    version=next_version if status == "fixed" else "",
                    created_at=now,
                )
            )
        if developer_reply is not None and developer_reply.strip() != item.developer_reply:
            item.developer_reply = developer_reply.strip()
            session.add(
                FeedbackActivityRecord(
                    id=new_id("activity"),
                    feedback_id=item.id,
                    event_type="reply",
                    actor="developer",
                    text=item.developer_reply,
                    created_at=now,
                )
            )
        item.fixed_version = next_version
        item.updated_at = now
        await session.commit()
        return await self._one(session, item)

    async def admin_attachment(
        self,
        session: AsyncSession,
        feedback_id: str,
        attachment_id: str,
    ) -> FeedbackAttachmentDownload:
        item = await session.get(FeedbackRecord, feedback_id)
        if item is None:
            raise FeedbackNotFound("Feedback was not found.")
        attachment = await session.scalar(
            select(FeedbackAttachmentRecord).where(
                FeedbackAttachmentRecord.feedback_id == item.id,
                FeedbackAttachmentRecord.id == attachment_id,
            )
        )
        if attachment is None:
            raise FeedbackNotFound("Feedback attachment was not found.")
        return FeedbackAttachmentDownload(
            content_type=attachment.content_type,
            content=attachment.content,
            sha256=attachment.sha256,
        )

    async def _owned(
        self,
        session: AsyncSession,
        claims: AccessClaims,
        feedback_id: str,
    ) -> FeedbackRecord:
        item = await session.get(FeedbackRecord, feedback_id)
        if item is None or item.user_id != claims.user_id:
            raise FeedbackNotFound("Feedback was not found.")
        return item

    async def _one(
        self,
        session: AsyncSession,
        item: FeedbackRecord,
    ) -> dict[str, Any]:
        return (await self._many(session, [item]))[0]

    async def _many(
        self,
        session: AsyncSession,
        items: list[FeedbackRecord],
    ) -> list[dict[str, Any]]:
        if not items:
            return []
        ids = [item.id for item in items]
        attachments: dict[str, list[FeedbackAttachmentRecord]] = defaultdict(list)
        notes: dict[str, list[FeedbackNoteRecord]] = defaultdict(list)
        activity: dict[str, list[FeedbackActivityRecord]] = defaultdict(list)
        for row in (
            await session.scalars(
                select(FeedbackAttachmentRecord)
                .where(FeedbackAttachmentRecord.feedback_id.in_(ids))
                .order_by(FeedbackAttachmentRecord.created_at, FeedbackAttachmentRecord.id)
            )
        ).all():
            attachments[row.feedback_id].append(row)
        for row in (
            await session.scalars(
                select(FeedbackNoteRecord)
                .where(FeedbackNoteRecord.feedback_id.in_(ids))
                .order_by(FeedbackNoteRecord.created_at, FeedbackNoteRecord.id)
            )
        ).all():
            notes[row.feedback_id].append(row)
        for row in (
            await session.scalars(
                select(FeedbackActivityRecord)
                .where(FeedbackActivityRecord.feedback_id.in_(ids))
                .order_by(FeedbackActivityRecord.created_at, FeedbackActivityRecord.id)
            )
        ).all():
            activity[row.feedback_id].append(row)
        return [
            {
                "feedback_id": item.id,
                "kind": item.kind,
                "description": item.description,
                "status": item.status,
                "diagnostics": item.diagnostics or {},
                "related_device_id": item.related_device_id or "",
                "attachments": [
                    {
                        "attachment_id": row.id,
                        "content_type": row.content_type,
                        "size_bytes": row.size_bytes,
                        "sha256": row.sha256,
                    }
                    for row in attachments[item.id]
                ],
                "notes": [
                    {
                        "note_id": row.id,
                        "author": row.author,
                        "text": row.text,
                        "created_at": self._iso(row.created_at),
                    }
                    for row in notes[item.id]
                ],
                "activity": [
                    {
                        "activity_id": row.id,
                        "event_type": row.event_type,
                        "actor": row.actor,
                        "status": row.status,
                        "text": row.text,
                        "version": row.version,
                        "created_at": self._iso(row.created_at),
                    }
                    for row in activity[item.id]
                ],
                "developer_reply": item.developer_reply,
                "fixed_version": item.fixed_version,
                "created_at": self._iso(item.created_at),
                "updated_at": self._iso(item.updated_at),
                "withdrawn_at": self._iso(item.withdrawn_at),
            }
            for item in items
        ]

    @staticmethod
    def _diagnostics(values: dict[str, object]) -> dict[str, object]:
        if len(values) > 32:
            raise FeedbackInvalid("Feedback diagnostics contain too many fields.")
        normalized: dict[str, object] = {}
        for raw_key, raw_value in values.items():
            key = str(raw_key).strip()
            if not key or len(key) > 64:
                raise FeedbackInvalid("A feedback diagnostic name is invalid.")
            if not isinstance(raw_value, (str, int, float, bool)):
                raise FeedbackInvalid("A feedback diagnostic value is invalid.")
            value: object = raw_value.strip() if isinstance(raw_value, str) else raw_value
            if len(str(value)) > 1_000:
                raise FeedbackInvalid("A feedback diagnostic value is too long.")
            normalized[key] = value
        encoded = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 16 * 1024:
            raise FeedbackInvalid("Feedback diagnostics are too large.")
        return normalized

    @staticmethod
    def _matches_image_type(content_type: str, content: bytes) -> bool:
        if content_type == "image/jpeg":
            return content.startswith(b"\xff\xd8\xff")
        if content_type == "image/png":
            return content.startswith(b"\x89PNG\r\n\x1a\n")
        if content_type == "image/webp":
            return (
                len(content) >= 12
                and content.startswith(b"RIFF")
                and content[8:12] == b"WEBP"
            )
        return False

    @staticmethod
    def _iso(value: dt.datetime | None) -> str:
        if value is None:
            return ""
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        return value.astimezone(dt.timezone.utc).isoformat()

    @classmethod
    def _encode_cursor(cls, created_at: dt.datetime, feedback_id: str) -> str:
        raw = json.dumps(
            {"created_at": cls._iso(created_at), "feedback_id": feedback_id},
            separators=(",", ":"),
        ).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_cursor(value: str) -> tuple[dt.datetime, str]:
        try:
            padded = value + "=" * (-len(value) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
            created_at = dt.datetime.fromisoformat(str(payload["created_at"]))
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=dt.timezone.utc)
            feedback_id = str(payload["feedback_id"])
            if not feedback_id.startswith("feedback-"):
                raise ValueError
            return created_at, feedback_id
        except (ValueError, KeyError, TypeError, json.JSONDecodeError, binascii.Error) as exc:
            raise FeedbackInvalid("The feedback cursor is invalid.") from exc
