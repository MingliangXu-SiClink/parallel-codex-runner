"""Disposable raw conversation projection; the native rollout remains authoritative."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
from pathlib import Path

from .models import CodexHistoryEntry


HistoryCandidate = tuple[int, str, str, CodexHistoryEntry]
_MAX_CACHE_BYTES = 8 * 1024 * 1024


class HistoryReadCache:
    def __init__(self, home: Path, source: Path, session: str, max_lines: int | None) -> None:
        self.source = source
        self.session = session
        self.max_lines = max(0, int(max_lines)) if max_lines is not None else None
        self.identity = [str(source.absolute()), session, self.max_lines]
        digest = hashlib.sha256(json.dumps(self.identity).encode()).hexdigest()
        self.path = home / "pcr" / "conversation-cache" / f"{digest}.json"

    def fingerprint(self) -> list[int]:
        stat = self.source.stat()
        return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]

    def load(self, fingerprint: list[int]) -> list[HistoryCandidate] | None:
        try:
            with self.path.open("rb") as stream:
                data = stream.read(_MAX_CACHE_BYTES + 1)
            if len(data) > _MAX_CACHE_BYTES:
                return None
            value = json.loads(data)
            if (not isinstance(value, dict) or value.get("version") != 2
                    or value.get("identity") != self.identity
                    or value.get("fingerprint") != fingerprint
                    or not isinstance(value.get("entries"), list)):
                return None
            result = []
            previous_line = -1
            for item in value["entries"]:
                if (not isinstance(item, list) or len(item) != 6
                        or type(item[0]) is not int or item[0] <= previous_line
                        or (self.max_lines is not None and item[0] >= self.max_lines)
                        or item[1] not in {"event", "response"}
                        or not isinstance(item[2], str)
                        or item[3] not in {"user", "output", "thought"}
                        or not isinstance(item[4], str) or item[5] not in {"", "pcr_recovery"}):
                    return None
                line, source, turn, category, text, origin = item
                result.append((line, source, turn, CodexHistoryEntry(category, text,
                    id=f"history:{self.session}:{line}", turn_id=turn, input_origin=origin)))
                previous_line = line
            return result if self.fingerprint() == fingerprint else None
        except (OSError, ValueError, TypeError):
            return None

    def store(self, fingerprint: list[int], entries: list[HistoryCandidate]) -> None:
        temporary = None
        try:
            value = {"version": 2, "identity": self.identity, "fingerprint": fingerprint,
                     "entries": [[line, source, turn, entry.category, entry.text, entry.input_origin]
                                 for line, source, turn, entry in entries]}
            data = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
            if len(data) > _MAX_CACHE_BYTES or self.fingerprint() != fingerprint:
                return
            self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            self.path.parent.chmod(0o700)
            descriptor, name = tempfile.mkstemp(prefix=".history-", dir=self.path.parent)
            temporary = Path(name)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
            os.replace(temporary, self.path)
        except (OSError, UnicodeError):
            # A missing or unwritable read cache must never block the original.
            pass
        finally:
            if temporary is not None:
                with contextlib.suppress(OSError):
                    temporary.unlink(missing_ok=True)
