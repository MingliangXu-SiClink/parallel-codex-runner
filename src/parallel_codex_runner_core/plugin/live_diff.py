from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict
from typing import Callable

from .artifacts import ArtifactError


class LiveDiffSnapshots:
    """Bounded, immutable pages. Live previews never become final artifacts."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snapshots: OrderedDict[str, tuple[tuple, float, bytes]] = OrderedDict()
        self.max_bytes = 64 * 1024 * 1024
        self.max_entries = 16
        self.ttl = 300.0
        self.refresh_interval = 5.0

    def read(self, key: tuple, snapshot_id: str, build: Callable[[], str]) -> tuple[str, bytes]:
        # Serialize scans separately from the run manager: status, stop, and
        # queue requests must never wait for a large workspace diff.
        with self._lock:
            now = time.monotonic()
            for identifier, (_, created, _) in list(self._snapshots.items()):
                if now - created > self.ttl:
                    del self._snapshots[identifier]
            if snapshot_id:
                value = self._snapshots.get(snapshot_id)
                if value is None or value[0] != key:
                    raise ArtifactError("This live file preview has expired. Refresh to load the current changes.")
                return snapshot_id, value[2]
            for identifier, (stored_key, created, data) in reversed(self._snapshots.items()):
                if stored_key == key and now - created < self.refresh_interval:
                    return identifier, data
            data = build().encode("utf-8")
            if len(data) > self.max_bytes:
                raise ArtifactError("These live changes are too large to preview. Review them after the Agent finishes.")
            size = sum(len(value[2]) for value in self._snapshots.values())
            while self._snapshots and (size + len(data) > self.max_bytes or len(self._snapshots) >= self.max_entries):
                _, (_, _, removed) = self._snapshots.popitem(last=False)
                size -= len(removed)
            identifier = uuid.uuid4().hex
            self._snapshots[identifier] = (key, time.monotonic(), data)
            return identifier, data


def live_diff_page(data: bytes, cursor: int, limit: int) -> tuple[str, int]:
    cursor = min(cursor, len(data))
    end = min(cursor + limit, len(data))
    while end < len(data) and data[end] & 0xC0 == 0x80:
        end += 1
    return data[cursor:end].decode("utf-8", errors="replace"), end
