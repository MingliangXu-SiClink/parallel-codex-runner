from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Mapping

from ..plugin.artifacts import write_json_atomic


FEEDBACK_KINDS = {"bug", "feature", "experience"}
FEEDBACK_STATUSES = {"submitted", "confirmed", "in_progress", "fixed", "declined"}
USER_WITHDRAWABLE_STATUSES = {"submitted"}
MAX_DESCRIPTION_CHARS = 8_000
MAX_NOTE_CHARS = 4_000
MAX_DIAGNOSTIC_CHARS = 16_000
MAX_ATTACHMENTS = 3
MAX_ATTACHMENT_BYTES = 512 * 1024
ALLOWED_ATTACHMENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
MAX_FEEDBACK_PER_CLIENT = 500
MAX_FEEDBACK_PER_CLIENT_HOUR = 30
MAX_CLIENT_ATTACHMENT_BYTES = 64 * 1024 * 1024
ALLOWED_DIAGNOSTIC_KEYS = {
    "app_version",
    "app_build",
    "app_platform",
    "os_version",
    "device_model",
    "daemon_version",
    "protocol_version",
    "pcr_version",
    "codex_version",
    "mac_device_id",
    "mac_name",
    "workspace_id",
    "workspace_name",
    "run_id",
    "agent",
    "error_code",
    "error_summary",
    "recent_events",
}


class FeedbackError(ValueError):
    pass


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _trimmed(value: object, maximum: int, field: str, *, required: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise FeedbackError(f"{field} must not be empty")
    if len(text) > maximum:
        raise FeedbackError(f"{field} is too long")
    return text


class FeedbackStore:
    """Private, structured feedback inbox owned by one machine installation."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        self.state_path = self.root / "feedback.json"
        self.attachments_root = self.root / "attachments"
        self._lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.root.chmod(0o700)
        except OSError:
            pass

    def submit(
        self,
        *,
        client_id: str,
        client_feedback_id: str,
        kind: str,
        description: str,
        diagnostics: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        client_id = _trimmed(client_id, 128, "client_id", required=True)
        client_feedback_id = _trimmed(
            client_feedback_id,
            128,
            "client_feedback_id",
            required=True,
        )
        kind = _trimmed(kind, 32, "kind", required=True).lower()
        if kind not in FEEDBACK_KINDS:
            raise FeedbackError("kind must be bug, feature, or experience")
        description = _trimmed(
            description,
            MAX_DESCRIPTION_CHARS,
            "description",
            required=True,
        )
        safe_diagnostics = self._diagnostics(diagnostics or {})
        with self._lock:
            state = self._load()
            existing = next(
                (
                    item
                    for item in state["items"]
                    if item.get("client_id") == client_id
                    and item.get("client_feedback_id") == client_feedback_id
                ),
                None,
            )
            if existing is not None:
                if (
                    existing.get("kind") != kind
                    or existing.get("description") != description
                ):
                    raise FeedbackError("client_feedback_id was already used")
                return self._public(existing)
            if sum(
                item.get("client_id") == client_id for item in state["items"]
            ) >= MAX_FEEDBACK_PER_CLIENT:
                raise FeedbackError("feedback storage limit was reached")
            one_hour_ago = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)
            recent_count = sum(
                item.get("client_id") == client_id
                and self._timestamp(item.get("created_at")) >= one_hour_ago
                for item in state["items"]
            )
            if recent_count >= MAX_FEEDBACK_PER_CLIENT_HOUR:
                raise FeedbackError("too many feedback submissions; try again later")
            now = _utc_now()
            item: dict[str, Any] = {
                "feedback_id": f"feedback-{uuid.uuid4().hex}",
                "client_id": client_id,
                "client_feedback_id": client_feedback_id,
                "kind": kind,
                "description": description,
                "status": "submitted",
                "diagnostics": safe_diagnostics,
                "attachments": [],
                "notes": [],
                "developer_reply": "",
                "fixed_version": "",
                "created_at": now,
                "updated_at": now,
                "withdrawn_at": "",
            }
            state["items"].append(item)
            self._save(state)
            return self._public(item)

    def list(
        self,
        *,
        client_id: str | None = None,
        cursor: int = 0,
        limit: int = 20,
        status: str = "",
        app_version: str = "",
        device_id: str = "",
        workspace_id: str = "",
    ) -> dict[str, Any]:
        cursor = max(0, int(cursor))
        limit = min(max(1, int(limit)), 50)
        status = _trimmed(status, 32, "status").lower()
        app_version = _trimmed(app_version, 64, "app_version")
        device_id = _trimmed(device_id, 128, "device_id")
        workspace_id = _trimmed(workspace_id, 128, "workspace_id")
        if status and status not in FEEDBACK_STATUSES | {"withdrawn"}:
            raise FeedbackError("status filter is invalid")
        with self._lock:
            items = self._load()["items"]
            if client_id is not None:
                items = [
                    item for item in items if item.get("client_id") == client_id
                ]
            if status:
                items = [item for item in items if item.get("status") == status]
            if device_id:
                items = [
                    item
                    for item in items
                    if item.get("client_id") == device_id
                    or (item.get("diagnostics") or {}).get("mac_device_id") == device_id
                ]
            if app_version:
                items = [
                    item
                    for item in items
                    if (item.get("diagnostics") or {}).get("app_version") == app_version
                ]
            if workspace_id:
                items = [
                    item
                    for item in items
                    if (item.get("diagnostics") or {}).get("workspace_id") == workspace_id
                ]
            items = sorted(
                items,
                key=lambda item: str(item.get("updated_at") or ""),
                reverse=True,
            )
            page = items[cursor : cursor + limit]
            next_cursor = cursor + len(page)
            return {
                "items": [self._public(item) for item in page],
                "cursor": cursor,
                "next_cursor": next_cursor,
                "has_more": next_cursor < len(items),
                "total": len(items),
            }

    def get(
        self,
        feedback_id: str,
        *,
        client_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            return self._public(
                self._require(self._load(), feedback_id, client_id=client_id)
            )

    def add_note(
        self,
        feedback_id: str,
        text: str,
        *,
        client_id: str | None = None,
    ) -> dict[str, Any]:
        text = _trimmed(text, MAX_NOTE_CHARS, "text", required=True)
        with self._lock:
            state = self._load()
            item = self._require(state, feedback_id, client_id=client_id)
            if item.get("withdrawn_at"):
                raise FeedbackError("withdrawn feedback cannot be updated")
            item.setdefault("notes", []).append(
                {
                    "note_id": f"note-{uuid.uuid4().hex}",
                    "author": "user",
                    "text": text,
                    "created_at": _utc_now(),
                }
            )
            item["updated_at"] = _utc_now()
            self._save(state)
            return self._public(item)

    def add_attachment(
        self,
        feedback_id: str,
        *,
        client_id: str | None = None,
        content_type: str,
        encoded: str,
    ) -> dict[str, Any]:
        content_type = _trimmed(content_type, 64, "content_type", required=True).lower()
        extension = ALLOWED_ATTACHMENT_TYPES.get(content_type)
        if extension is None:
            raise FeedbackError("attachment must be a JPEG, PNG, or WebP image")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise FeedbackError("attachment is not valid base64") from exc
        if not content or len(content) > MAX_ATTACHMENT_BYTES:
            raise FeedbackError("attachment must be between 1 byte and 512 KiB")
        if not self._matches_content_type(content_type, content):
            raise FeedbackError("attachment content does not match its image type")
        with self._lock:
            state = self._load()
            item = self._require(state, feedback_id, client_id=client_id)
            if item.get("withdrawn_at"):
                raise FeedbackError("withdrawn feedback cannot be updated")
            attachments = item.setdefault("attachments", [])
            digest = hashlib.sha256(content).hexdigest()
            existing = next(
                (value for value in attachments if value.get("sha256") == digest),
                None,
            )
            if existing is not None:
                return self._public(item)
            if len(attachments) >= MAX_ATTACHMENTS:
                raise FeedbackError(f"feedback supports at most {MAX_ATTACHMENTS} screenshots")
            owner = str(item.get("client_id") or "")
            owner_bytes = sum(
                int(attachment.get("size_bytes") or 0)
                for existing_item in state["items"]
                if existing_item.get("client_id") == owner
                for attachment in existing_item.get("attachments", [])
                if isinstance(attachment, Mapping)
            )
            if owner_bytes + len(content) > MAX_CLIENT_ATTACHMENT_BYTES:
                raise FeedbackError("feedback screenshot storage limit was reached")
            attachment_id = f"attachment-{uuid.uuid4().hex}"
            directory = self.attachments_root / str(item["feedback_id"])
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = directory / f"{attachment_id}{extension}"
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
            except BaseException:
                try:
                    path.unlink()
                except OSError:
                    pass
                raise
            attachments.append(
                {
                    "attachment_id": attachment_id,
                    "content_type": content_type,
                    "size_bytes": len(content),
                    "sha256": digest,
                    "file_name": path.name,
                }
            )
            item["updated_at"] = _utc_now()
            try:
                self._save(state)
            except BaseException:
                try:
                    path.unlink()
                except OSError:
                    pass
                raise
            return self._public(item)

    def attachment(
        self,
        feedback_id: str,
        attachment_id: str,
        *,
        client_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            item = self._require(
                self._load(), feedback_id, client_id=client_id
            )
            metadata = next(
                (
                    value
                    for value in item.get("attachments", [])
                    if value.get("attachment_id") == attachment_id
                ),
                None,
            )
            if metadata is None:
                raise FeedbackError("feedback attachment was not found")
            path = self._attachment_path(item, metadata)
            try:
                content = path.read_bytes()
            except OSError as exc:
                raise FeedbackError("feedback attachment is unavailable") from exc
            if hashlib.sha256(content).hexdigest() != metadata.get("sha256"):
                raise FeedbackError("feedback attachment failed its integrity check")
            return {
                **{key: value for key, value in metadata.items() if key != "file_name"},
                "data": base64.b64encode(content).decode("ascii"),
            }

    def remove_attachment(
        self,
        feedback_id: str,
        attachment_id: str,
        *,
        client_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            state = self._load()
            item = self._require(state, feedback_id, client_id=client_id)
            if item.get("withdrawn_at"):
                raise FeedbackError("withdrawn feedback cannot be updated")
            attachments = item.get("attachments", [])
            metadata = next(
                (
                    value
                    for value in attachments
                    if value.get("attachment_id") == attachment_id
                ),
                None,
            )
            if metadata is None:
                raise FeedbackError("feedback attachment was not found")
            path = self._attachment_path(item, metadata)
            item["attachments"] = [
                value
                for value in attachments
                if value.get("attachment_id") != attachment_id
            ]
            item["updated_at"] = _utc_now()
            self._save(state)
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            return self._public(item)

    def withdraw(
        self,
        feedback_id: str,
        *,
        client_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            state = self._load()
            item = self._require(state, feedback_id, client_id=client_id)
            if item.get("status") not in USER_WITHDRAWABLE_STATUSES:
                raise FeedbackError("only newly submitted feedback can be withdrawn")
            now = _utc_now()
            item["status"] = "withdrawn"
            item["withdrawn_at"] = now
            item["updated_at"] = now
            self._save(state)
            return self._public(item)

    def admin_update(
        self,
        feedback_id: str,
        *,
        status: str,
        developer_reply: str = "",
        fixed_version: str = "",
    ) -> dict[str, Any]:
        status = _trimmed(status, 32, "status", required=True).lower()
        if status not in FEEDBACK_STATUSES:
            raise FeedbackError("status is invalid")
        developer_reply = _trimmed(developer_reply, MAX_NOTE_CHARS, "developer_reply")
        fixed_version = _trimmed(fixed_version, 64, "fixed_version")
        if status == "fixed" and not fixed_version:
            raise FeedbackError("fixed_version is required for fixed feedback")
        with self._lock:
            state = self._load()
            item = self._require(state, feedback_id)
            item["status"] = status
            item["developer_reply"] = developer_reply
            item["fixed_version"] = fixed_version
            item["updated_at"] = _utc_now()
            self._save(state)
            return self._public(item)

    def _load(self) -> dict[str, Any]:
        if not self.state_path.is_file():
            return {"version": 1, "items": []}
        try:
            value = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise FeedbackError("feedback inbox is unreadable") from exc
        if not isinstance(value, dict) or not isinstance(value.get("items"), list):
            raise FeedbackError("feedback inbox is invalid")
        return value

    def _save(self, state: Mapping[str, Any]) -> None:
        write_json_atomic(self.state_path, dict(state))
        try:
            self.state_path.chmod(0o600)
        except OSError:
            pass

    @staticmethod
    def _require(
        state: Mapping[str, Any],
        feedback_id: str,
        *,
        client_id: str | None = None,
    ) -> dict[str, Any]:
        feedback_id = _trimmed(feedback_id, 128, "feedback_id", required=True)
        for item in state.get("items", []):
            if (
                isinstance(item, dict)
                and item.get("feedback_id") == feedback_id
                and (client_id is None or item.get("client_id") == client_id)
            ):
                return item
        raise FeedbackError("feedback was not found")

    @staticmethod
    def _matches_content_type(content_type: str, content: bytes) -> bool:
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
    def _timestamp(value: object) -> dt.datetime:
        try:
            parsed = dt.datetime.fromisoformat(str(value or ""))
        except ValueError:
            return dt.datetime.min.replace(tzinfo=dt.timezone.utc)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)

    @staticmethod
    def _diagnostics(values: Mapping[str, Any]) -> dict[str, Any]:
        safe: dict[str, Any] = {}
        for key in ALLOWED_DIAGNOSTIC_KEYS:
            if key not in values:
                continue
            value = values[key]
            if isinstance(value, (str, int, float, bool)) or value is None:
                safe[key] = value
            elif key == "recent_events" and isinstance(value, list):
                safe[key] = [str(item)[:500] for item in value[:20]]
        encoded = json.dumps(safe, ensure_ascii=False, separators=(",", ":"))
        if len(encoded) > MAX_DIAGNOSTIC_CHARS:
            raise FeedbackError("diagnostics are too large")
        return safe

    def _attachment_path(
        self,
        item: Mapping[str, Any],
        metadata: Mapping[str, Any],
    ) -> Path:
        name = str(metadata.get("file_name") or "")
        path = (
            self.attachments_root
            / str(item.get("feedback_id") or "")
            / name
        ).resolve()
        expected = (
            self.attachments_root / str(item.get("feedback_id") or "")
        ).resolve()
        if path.parent != expected or path.is_symlink():
            raise FeedbackError("feedback attachment path is invalid")
        return path

    @staticmethod
    def _public(item: Mapping[str, Any]) -> dict[str, Any]:
        result = {
            key: value
            for key, value in item.items()
            if key not in {"client_id", "client_feedback_id"}
        }
        result["attachments"] = [
            {
                key: value
                for key, value in attachment.items()
                if key != "file_name"
            }
            for attachment in item.get("attachments", [])
            if isinstance(attachment, Mapping)
        ]
        return result
