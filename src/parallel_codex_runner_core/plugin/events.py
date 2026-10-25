from __future__ import annotations

import json
import os
import struct
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator

from .state import utc_now

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback is process-local
    fcntl = None


_OFFSET = struct.Struct(">Q")
_MAX_EVENT_RECORD_BYTES = 256 * 1024
_TRANSCRIPT_FRAGMENT_BYTES = 128 * 1024


def _encoded_event_size(payload: Dict[str, Any]) -> int:
    return len(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    )


def _utf8_chunks(value: str, max_bytes: int) -> list[str]:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return [value]
    chunks: list[str] = []
    start = 0
    while start < len(encoded):
        end = min(len(encoded), start + max_bytes)
        while end < len(encoded) and end > start and encoded[end] & 0xC0 == 0x80:
            end -= 1
        if end == start:
            raise ValueError("Cannot split UTF-8 Agent output")
        if end < len(encoded):
            newline = encoded.rfind(b"\n", start + max_bytes // 2, end)
            if newline >= 0:
                end = newline + 1
        chunks.append(encoded[start:end].decode("utf-8"))
        start = end
    return chunks


def _bounded_payloads(payload: Dict[str, Any]) -> list[Dict[str, Any]]:
    if _encoded_event_size(payload) <= _MAX_EVENT_RECORD_BYTES:
        return [payload]
    if payload.get("type") != "agent_line":
        return [payload]
    raw = payload.get("text") or payload.get("message")
    if not isinstance(raw, str) or not raw:
        return [payload]

    display_kind = "activity"
    display_text = raw
    display_status = ""
    operation_id = ""
    activity_type = ""
    try:
        from ..remote.transcript import transcript_item

        item = transcript_item({**payload, "event_id": 0})
    except (ImportError, ValueError):
        item = None
    if isinstance(item, dict):
        display_kind = str(item.get("kind") or display_kind)
        display_text = str(item.get("text") or display_text)
        display_status = str(item.get("status") or "")
        operation_id = str(item.get("operation_id") or "")
        activity_type = str(item.get("activity_type") or "")

    chunks = _utf8_chunks(display_text, _TRANSCRIPT_FRAGMENT_BYTES)
    if len(chunks) <= 1:
        return [payload]
    fragment_group = os.urandom(12).hex()
    fragments: list[Dict[str, Any]] = []
    for index, chunk in enumerate(chunks):
        fragment = {
            key: value
            for key, value in payload.items()
            if key not in {"text", "message"}
        }
        fragment.update(
            {
                "pcr_transcript_kind": display_kind,
                "pcr_transcript_text": chunk,
                "pcr_fragment_group": fragment_group,
                "pcr_fragment_index": index,
                "pcr_fragment_count": len(chunks),
            }
        )
        if display_status:
            fragment["pcr_transcript_status"] = display_status
        if operation_id:
            fragment["pcr_operation_id"] = operation_id
        if activity_type:
            fragment["pcr_activity_type"] = activity_type
        fragments.append(fragment)
    return fragments


class EventLog:
    """Append-only JSONL events with a compact, lazily rebuilt offset index."""

    def __init__(self, events_dir: Path) -> None:
        self.events_dir = events_dir
        self.events_dir.mkdir(parents=True, exist_ok=True)
        self._thread_lock = threading.RLock()

    def event_path(self, run_id: str) -> Path:
        return self.events_dir / f"{run_id}.jsonl"

    def index_path(self, run_id: str) -> Path:
        return self.events_dir / f"{run_id}.idx"

    def lock_path(self, run_id: str) -> Path:
        return self.events_dir / f"{run_id}.lock"

    @contextmanager
    def _locked(self, run_id: str) -> Iterator[None]:
        with self._thread_lock:
            handle = self.lock_path(run_id).open("a+b")
            try:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                yield
            finally:
                if fcntl is not None:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass
                handle.close()

    def _index_is_valid(self, run_id: str) -> bool:
        index = self.index_path(run_id)
        try:
            return index.stat().st_size % _OFFSET.size == 0
        except OSError:
            return False

    def _rebuild_index_locked(self, run_id: str) -> None:
        event_path = self.event_path(run_id)
        index_path = self.index_path(run_id)
        event_path.touch(exist_ok=True)
        offsets: list[int] = []
        complete_end = 0
        with event_path.open("r+b") as source:
            while True:
                offset = source.tell()
                line = source.readline()
                if not line:
                    break
                if not line.endswith(b"\n"):
                    source.truncate(complete_end)
                    break
                offsets.append(offset)
                complete_end = source.tell()
        temporary = index_path.with_name(f".{index_path.name}.{os.getpid()}.tmp")
        with temporary.open("wb") as target:
            for offset in offsets:
                target.write(_OFFSET.pack(offset))
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, index_path)

    def _ensure_index_locked(self, run_id: str) -> None:
        if not self._index_is_valid(run_id):
            self._rebuild_index_locked(run_id)
            return
        event_path = self.event_path(run_id)
        index_path = self.index_path(run_id)
        event_path.touch(exist_ok=True)
        index_path.touch(exist_ok=True)
        count = index_path.stat().st_size // _OFFSET.size
        if count == 0:
            if event_path.stat().st_size:
                self._rebuild_index_locked(run_id)
            return
        with index_path.open("rb") as index:
            index.seek(-_OFFSET.size, os.SEEK_END)
            last_offset = _OFFSET.unpack(index.read(_OFFSET.size))[0]
        with event_path.open("rb") as events:
            events.seek(last_offset)
            last_line = events.readline()
            indexed_end = events.tell()
        if not last_line.endswith(b"\n") or indexed_end != event_path.stat().st_size:
            self._rebuild_index_locked(run_id)

    def append(self, run_id: str, payload: Dict[str, Any]) -> int:
        with self._locked(run_id):
            self._ensure_index_locked(run_id)
            event_path = self.event_path(run_id)
            index_path = self.index_path(run_id)
            first_event_id = index_path.stat().st_size // _OFFSET.size
            timestamp = str(payload.get("timestamp") or utc_now())
            with (
                event_path.open("ab", buffering=0) as events,
                index_path.open("ab", buffering=0) as index,
            ):
                for value in _bounded_payloads(dict(payload)):
                    event_id = index_path.stat().st_size // _OFFSET.size
                    event = dict(value)
                    event["event_id"] = event_id
                    event.setdefault("timestamp", timestamp)
                    encoded = (
                        json.dumps(
                            event,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                        + "\n"
                    ).encode("utf-8")
                    offset = events.tell()
                    events.write(encoded)
                    index.write(_OFFSET.pack(offset))
            return first_event_id

    def count(self, run_id: str) -> int:
        with self._locked(run_id):
            self._ensure_index_locked(run_id)
            return self.index_path(run_id).stat().st_size // _OFFSET.size

    def read(
        self,
        run_id: str,
        *,
        cursor: int = 0,
        limit: int = 100,
        agent: int | None = None,
        max_bytes: int = 512 * 1024,
        transcript_kinds: set[str] | None = None,
    ) -> Dict[str, Any]:
        cursor = max(0, int(cursor))
        limit = max(1, int(limit))
        max_bytes = max(4 * 1024, int(max_bytes))
        with self._locked(run_id):
            self._ensure_index_locked(run_id)
            event_path = self.event_path(run_id)
            index_path = self.index_path(run_id)
            total = index_path.stat().st_size // _OFFSET.size
            cursor = min(cursor, total)
            events: list[Dict[str, Any]] = []
            next_cursor = cursor
            serialized_bytes = 2
            oversized_event = False
            with index_path.open("rb") as index, event_path.open("rb") as source:
                index.seek(cursor * _OFFSET.size)
                position = cursor
                while position < total and len(events) < limit:
                    raw_offset = index.read(_OFFSET.size)
                    if len(raw_offset) != _OFFSET.size:
                        break
                    offset = _OFFSET.unpack(raw_offset)[0]
                    source.seek(offset)
                    raw = source.readline()
                    event_cursor = position
                    position += 1
                    next_cursor = position
                    try:
                        value = json.loads(raw)
                    except (UnicodeDecodeError, ValueError):
                        continue
                    if not isinstance(value, dict):
                        continue
                    if agent is not None:
                        try:
                            event_agent = int(value.get("idx") or 0)
                        except (TypeError, ValueError):
                            event_agent = 0
                        if event_agent != int(agent):
                            continue
                    if transcript_kinds is not None:
                        try:
                            from ..remote.transcript import transcript_item

                            item = transcript_item(value)
                        except (ImportError, TypeError, ValueError):
                            item = None
                        if not isinstance(item, dict) or str(
                            item.get("kind") or ""
                        ) not in transcript_kinds:
                            continue
                    encoded_size = len(
                        json.dumps(
                            value,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    )
                    separator = 1 if events else 0
                    if (
                        events
                        and serialized_bytes + separator + encoded_size > max_bytes
                    ):
                        next_cursor = event_cursor
                        break
                    if not events and serialized_bytes + encoded_size > max_bytes:
                        oversized_event = True
                    events.append(value)
                    serialized_bytes += separator + encoded_size
                    if oversized_event:
                        break
        return {
            "run_id": run_id,
            "cursor": cursor,
            "next_cursor": next_cursor,
            "has_more": next_cursor < total,
            "events": events,
            "total": total,
            "serialized_bytes": serialized_bytes,
            "byte_limit": max_bytes,
            "oversized_event": oversized_event,
        }

    def delete(self, run_id: str) -> None:
        with self._locked(run_id):
            for path in (self.event_path(run_id), self.index_path(run_id)):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
        try:
            self.lock_path(run_id).unlink()
        except FileNotFoundError:
            pass
