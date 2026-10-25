"""Materialize paginated Codex history without changing its source rollouts."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_ROLLOUT_NAME = re.compile(rf"rollout-(\d{{4}}-\d{{2}}-\d{{2}}T\d{{2}}-\d{{2}}-\d{{2}})-({_UUID})(?:_({_UUID}))?\.jsonl")


def canonical_rollout_identity(name: str) -> tuple[str, str, str] | None:
    match = _ROLLOUT_NAME.fullmatch(name)
    if match is None:
        return None
    try:
        datetime.strptime(match[1], "%Y-%m-%dT%H-%M-%S")
    except ValueError:
        return None
    return match[1], match[2], match[3] or match[2]


@dataclass(frozen=True)
class RolloutSegment:
    path: Path
    end: int
    metadata: dict[str, Any]
    metadata_line: int

    def destination_name(self, rewritten_session: str | None = None) -> str:
        """Preserve native physical names; repair only unambiguous PCR imports."""
        canonical = canonical_rollout_identity(self.path.name)
        if canonical is not None and rewritten_session is None:
            return self.path.name
        payload = self.metadata.get("payload", {})
        if rewritten_session is None:
            short = re.fullmatch(rf"rollout-({_UUID})\.jsonl", self.path.name)
            if (short is None or payload.get("id") != short[1]
                    or payload.get("rollout_id") not in (None, short[1])):
                raise ValueError(f"Cannot safely name Codex rollout with ambiguous physical identity: {self.path}")
            session = short[1]
        else:
            session = rewritten_session
        if not isinstance(session, str) or not re.fullmatch(_UUID, session):
            raise ValueError(f"Cannot safely name Codex rollout with invalid session identity: {self.path}")
        if canonical is not None:
            timestamp = canonical[0]
        else:
            value = payload.get("timestamp") or self.metadata.get("timestamp")
            try:
                stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    raise ValueError("missing timezone")
                timestamp = stamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
            except (AttributeError, TypeError, ValueError) as error:
                raise ValueError(f"Cannot safely name Codex rollout without its creation timestamp: {self.path}") from error
        return f"rollout-{timestamp}-{session}.jsonl"


@dataclass(frozen=True)
class ResumeHistory:
    segments: tuple[RolloutSegment, ...]

    @property
    def paginated(self) -> bool:
        return any(segment.metadata.get("payload", {}).get("history_mode") == "paginated" for segment in self.segments)

    @property
    def size_bytes(self) -> int:
        return sum(segment.end for segment in self.segments)

    @property
    def workspaces(self) -> set[str]:
        return {str(segment.metadata["payload"]["cwd"]) for segment in self.segments if segment.metadata.get("payload", {}).get("cwd")}

    @property
    def physical_ids(self) -> tuple[str, ...]:
        identifiers = []
        for index, segment in enumerate(self.segments):
            child = (self.segments[index + 1].metadata.get("payload", {}).get("history_base", {})
                     if index + 1 < len(self.segments) else {})
            canonical = canonical_rollout_identity(segment.destination_name())
            # Native revert keeps metadata.id stable; the physical id lives in
            # the canonical filename, not in a session_meta.rollout_id field.
            identifier = canonical[2]
            if child.get("thread_id") and identifier != child["thread_id"]:
                raise ValueError("Codex history points to a different physical rollout")
            if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
                raise ValueError("Codex history has no safe physical rollout identity")
            identifiers.append(identifier)
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Codex history contains duplicate physical rollout identities")
        return tuple(identifiers)

    def lines(self, session_id: str | None = None) -> Iterator[str]:
        # A portable legacy copy contains the exact inherited prefixes and
        # current branch. It no longer needs global thread rows or byte offsets
        # into files that PCR will subsequently rebind or clean up.
        if self.paginated:
            metadata = dict(self.segments[-1].metadata)
            payload = dict(metadata["payload"])
            if session_id is not None:
                payload["id"] = session_id
                payload.pop("rollout_id", None)
            payload["history_mode"] = "legacy"
            payload.pop("history_base", None)
            metadata["payload"] = payload
            metadata.pop("ordinal", None)
            yield json.dumps(metadata, ensure_ascii=False, separators=(",", ":")) + "\n"
        for segment in self.segments:
            if segment.end == 0:
                continue
            consumed = 0
            with segment.path.open("rb") as source:
                for number, raw in enumerate(source):
                    consumed += len(raw)
                    if consumed > segment.end:
                        raise ValueError(f"Codex history changed while preparing its copy: {segment.path}")
                    if not self.paginated or number != segment.metadata_line:
                        yield raw.decode("utf-8", errors="replace")
                    if consumed == segment.end:
                        break
            if consumed != segment.end:
                raise ValueError(f"Codex history was truncated while preparing its copy: {segment.path}")


def _segment(path: Path, end: int | None = None) -> RolloutSegment:
    size = path.stat().st_size
    if end is not None and (end < 0 or end > size):
        raise ValueError(f"Codex history cutoff is outside its source rollout: {path}")
    with path.open("rb") as source:
        for number in range(20):
            raw = source.readline()
            if not raw:
                break
            try:
                record = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(record, dict) and record.get("type") == "session_meta" and isinstance(record.get("payload"), dict):
                return RolloutSegment(path.resolve(), size if end is None else end, record, number)
    return RolloutSegment(path.resolve(), size if end is None else end, {}, -1)


def _cutoff_matches(path: Path, end: int, ordinal: int) -> bool:
    if end == 0:
        return ordinal == 0
    if end > path.stat().st_size:
        return False
    with path.open("rb") as source:
        source.seek(end - 1)
        if source.read(1) != b"\n":
            return False
        position = end - 1
        chunks = []
        while position:
            size = min(position, 65536)
            position -= size
            source.seek(position)
            chunk = source.read(size)
            boundary = chunk.rfind(b"\n")
            chunks.append(chunk[boundary + 1:] if boundary >= 0 else chunk)
            if boundary >= 0:
                break
        try:
            record = json.loads(b"".join(reversed(chunks)))
        except (ValueError, UnicodeDecodeError):
            return False
    return isinstance(record, dict) and record.get("ordinal") == ordinal - 1


def resolve_resume_history(codex_home: Path, rollout: Path) -> ResumeHistory:
    leaf = _segment(rollout)
    segments = [leaf]
    seen = {leaf.path}
    while True:
        base = segments[-1].metadata.get("payload", {}).get("history_base")
        if base is None:
            break
        if not isinstance(base, dict):
            raise ValueError("Codex paginated history has invalid source metadata")
        thread = base.get("thread_id")
        end = base.get("end_byte_offset")
        ordinal = base.get("end_ordinal_exclusive")
        if not isinstance(thread, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", thread) or type(end) is not int or type(ordinal) is not int or end < 0 or ordinal < 0:
            raise ValueError("Codex paginated history has an invalid source or cutoff")

        paths = set((codex_home / "sessions").rglob(f"*{thread}.jsonl"))
        paths.update((codex_home / "archived_sessions").rglob(f"*{thread}.jsonl"))
        canonical = None
        database = codex_home / "state_5.sqlite"
        if database.exists():
            connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
            try:
                row = connection.execute("SELECT rollout_path FROM threads WHERE id = ?", (thread,)).fetchone()
                if row and row[0]:
                    canonical = Path(row[0]).expanduser().resolve()
                    paths.add(canonical)
            except sqlite3.Error:
                pass
            finally:
                connection.close()
        candidates = [path.resolve() for path in paths if path.is_file() and path.resolve() not in seen and _cutoff_matches(path, end, ordinal)]
        candidates = list(dict.fromkeys(candidates))
        if canonical in candidates:
            candidates = [canonical]
        elif len(candidates) > 1:
            # Adopted closures live together. Prefer their own source only
            # when unique, rather than a duplicate in another import directory.
            local = [path for path in candidates if path.parent == segments[-1].path.parent]
            if len(local) == 1:
                candidates = local
        if len(candidates) != 1:
            raise ValueError(f"Cannot safely restore Codex history source {thread}: expected one rollout with the recorded byte and ordinal cutoff, found {len(candidates)}. Original history was not changed.")
        parent = _segment(candidates[0], end)
        if not parent.metadata:
            raise ValueError(f"Codex history source has no session metadata: {parent.path}")
        seen.add(parent.path)
        segments.append(parent)
    return ResumeHistory(tuple(reversed(segments)))
