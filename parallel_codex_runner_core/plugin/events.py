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
            event_id = index_path.stat().st_size // _OFFSET.size
            event = dict(payload)
            event["event_id"] = event_id
            event.setdefault("timestamp", utc_now())
            encoded = (
                json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
            ).encode("utf-8")
            with event_path.open("ab", buffering=0) as events:
                offset = events.tell()
                events.write(encoded)
            with index_path.open("ab", buffering=0) as index:
                index.write(_OFFSET.pack(offset))
            return event_id

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
    ) -> Dict[str, Any]:
        cursor = max(0, int(cursor))
        limit = max(1, int(limit))
        with self._locked(run_id):
            self._ensure_index_locked(run_id)
            event_path = self.event_path(run_id)
            index_path = self.index_path(run_id)
            total = index_path.stat().st_size // _OFFSET.size
            cursor = min(cursor, total)
            events: list[Dict[str, Any]] = []
            next_cursor = cursor
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
                    events.append(value)
        return {
            "run_id": run_id,
            "cursor": cursor,
            "next_cursor": next_cursor,
            "has_more": next_cursor < total,
            "events": events,
            "total": total,
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
