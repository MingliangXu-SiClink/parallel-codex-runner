"""Lossless operation identities and conservative, display-independent summaries."""

from __future__ import annotations

import json
import hashlib
from datetime import datetime, timedelta, timezone
import re
import shlex
import time
import uuid
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from .conversation_protocol import (
    WorkFileChangeKind,
    WorkOperationKind,
    WorkOperationRecord,
    WorkOperationStatus,
    WorkOperationTarget,
)


def tool_item(payload: object) -> Mapping[str, Any] | None:
    if not isinstance(payload, Mapping):
        return None
    item = payload.get("item")
    if isinstance(item, Mapping):
        return item
    nested = payload.get("payload")
    if isinstance(nested, Mapping):
        return tool_item(nested) or nested
    return payload


def _number(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def wait_deadline(started_at: str | None, milliseconds: object) -> str | None:
    if not started_at or type(milliseconds) is not int or milliseconds < 0:
        return None
    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        if start.tzinfo is None:
            return None
        return (start + timedelta(milliseconds=milliseconds)).astimezone(timezone.utc).isoformat()
    except (ValueError, OverflowError):
        return None


def operation_output_name(identity: str) -> str:
    return "operation-output/" + hashlib.sha256(identity.encode("utf-8")).hexdigest() + ".log"


class OperationOutputStore:
    """Append streamed output once; event metadata identifies an immutable prefix."""

    def __init__(self, directory: Path, sizes: dict[str, int], streams: set[str] | None = None) -> None:
        self.directory, self.sizes = directory, sizes
        self.streams = streams if streams is not None else set()

    def observe(self, item: object, operation: dict[str, Any]) -> int | None:
        if not isinstance(item, Mapping):
            return None
        identity = operation["id"]
        delta = item.get("output_delta")
        snapshot = item.get("aggregated_output")
        if not isinstance(delta, str) and not isinstance(snapshot, str) and identity not in self.sizes:
            return None
        path = self.directory / operation_output_name(identity)
        if path.parent.is_symlink() or path.is_symlink():
            raise OSError("Operation output was replaced by a symlink")
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        size = self.sizes.get(identity, 0)
        if identity in self.sizes and path.stat().st_size != size:
            raise OSError("Operation output no longer matches its published prefix")
        if isinstance(delta, str):
            if size and identity not in self.streams:
                # Native deltas have no offset: a late chunk cannot safely be
                # concatenated with a completion summary that may cover it.
                raise OSError("Output stream coverage is unknown after a completion snapshot")
            data = delta.encode("utf-8")
            self.streams.add(identity)
        else:
            data = b""
            if isinstance(snapshot, str) and identity not in self.streams:
                encoded = snapshot.encode("utf-8")
                # Summaries are independently retained in stdout.log. Once
                # deltas exist, do not guess where a summary overlaps them.
                if len(encoded) > size:
                    if identity not in self.sizes:
                        data = encoded
                    else:
                        with path.open("rb") as source:
                            offset = 0
                            while offset < size:
                                chunk = source.read(min(64 * 1024, size - offset))
                                if not chunk or chunk != encoded[offset:offset + len(chunk)]:
                                    break
                                offset += len(chunk)
                            if offset == size:
                                data = encoded[size:]
        with path.open("ab" if identity in self.sizes else "wb") as target:
            target.write(data)
        self.sizes[identity] = size + len(data)
        return self.sizes[identity]


def command_string(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(part, str) for part in value):
        return shlex.join(value)
    return ""


def command_arguments(command: str) -> list[str] | None:
    try:
        tokens = shlex.split(command)
        if len(tokens) == 3 and PurePosixPath(tokens[0]).name in {"sh", "bash", "zsh"} and tokens[1] in {"-c", "-lc"}:
            command = tokens[2]
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>()")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return None
    # Shell expansion, substitution and pipelines are deliberately not classified.
    if any(char in command for char in ("$", "`", "\n")) or any(
        token and all(char in ";&|<>()" for char in token) for token in tokens
    ):
        return None
    return tokens or None


def classify_command(command: str) -> tuple[WorkOperationKind, list[str], str | None]:
    fallback = (WorkOperationKind.COMMAND, [], None)
    args = command_arguments(command)
    if not args:
        return fallback
    executable = PurePosixPath(args[0]).name
    options = args[1:]
    if executable == "cat":
        if any(arg.startswith("-") and arg != "--" for arg in options):
            return fallback
        paths = [arg for arg in options if arg != "--"]
        return (WorkOperationKind.FILE_READ, paths, None) if paths else fallback
    if executable in {"head", "tail"}:
        paths: list[str] = []
        index = 0
        while index < len(options):
            arg = options[index]
            if arg in {"-n", "-c"}:
                index += 1
                if index >= len(options) or not re.fullmatch(r"[+-]?\d+", options[index]):
                    return fallback
            elif re.fullmatch(r"-\d+", arg) or arg in {"--", "-q", "-v"}:
                pass
            elif arg.startswith("-"):
                return fallback
            else:
                paths.append(arg)
            index += 1
        return (WorkOperationKind.FILE_READ, paths, None) if paths else fallback
    if executable == "sed":
        if len(options) >= 3 and options[0] == "-n" and re.fullmatch(r"\d+(?:,\d+|,\$)?p", options[1]):
            paths = options[2:]
            if all(not path.startswith("-") for path in paths):
                return WorkOperationKind.FILE_READ, paths, None
        return fallback
    if executable in {"ls", "pwd"}:
        if any(arg.startswith("-") and not re.fullmatch(r"-[alhAFd1]+|--", arg) for arg in options):
            return fallback
        return WorkOperationKind.DIRECTORY_LIST, [arg for arg in options if not arg.startswith("-")] or ["."], None
    if executable in {"rg", "grep"}:
        paths = []
        query: str | None = None
        listing = False
        positional = False
        index = 0
        while index < len(options):
            arg = options[index]
            if arg == "--" and not positional:
                positional = True
            elif not positional and arg == "--files" and executable == "rg":
                listing = True
            elif not positional and arg in {"-e", "--regexp"}:
                index += 1
                if query is not None or index >= len(options):
                    return fallback
                query = options[index]
            elif not positional and arg in {"-g", "--glob", "-t", "--type", "-m", "--max-count", "-A", "-B", "-C"}:
                index += 1
                if index >= len(options):
                    return fallback
            elif not positional and (re.fullmatch(r"-[nirlwvsFEoHha]+", arg) or arg in {"--hidden", "--no-ignore", "--line-number", "--ignore-case", "--fixed-strings"}):
                pass
            elif not positional and arg.startswith("-"):
                return fallback
            elif query is None and not listing:
                query = arg
            else:
                paths.append(arg)
            index += 1
        if listing:
            return WorkOperationKind.DIRECTORY_LIST, paths or ["."], None
        if query is not None:
            return WorkOperationKind.FILE_SEARCH, paths or ["."], query
    return fallback


def operation_from_payload(payload: object, *, identity: str, event_id: int = 0) -> WorkOperationRecord | None:
    item = tool_item(payload)
    if item is None:
        return None
    item_type = item.get("type")
    command = None
    query = None
    targets: list[WorkOperationTarget] = []
    details_available = True
    planned_duration = None
    if item_type == "sleep":
        kind = WorkOperationKind.UNKNOWN
        command = "sleep"
        details_available = False
        planned_duration = _number(item.get("durationMs"))
        if planned_duration is not None and planned_duration < 0:
            planned_duration = None
    elif item_type == "command_execution":
        command = command_string(item.get("command"))
        if not command:
            return None
        kind, paths, query = classify_command(command)
        targets = [WorkOperationTarget(path=path) for path in paths]
    elif item_type == "file_change":
        kind = WorkOperationKind.FILE_CHANGE
        details_available = False
        for change in item.get("changes", []):
            if not isinstance(change, Mapping) or not isinstance(change.get("path"), str):
                continue
            raw_kind = str(change.get("kind") or "unknown")
            change_kind = WorkFileChangeKind.from_value({"add": "create", "modify": "update", "remove": "delete"}.get(raw_kind, raw_kind))
            added, deleted = _number(change.get("added")), _number(change.get("deleted"))
            diff = change.get("diff")
            if isinstance(diff, str) and diff:
                details_available = True
                if "@@" in diff:
                    added = deleted = 0
                    in_hunk = False
                    for line in diff.splitlines():
                        if line.startswith("@@"):
                            in_hunk = True
                        elif in_hunk:
                            added += line.startswith("+")
                            deleted += line.startswith("-")
            targets.append(WorkOperationTarget(path=change["path"], change=change_kind,
                added=added if added is not None and added >= 0 else None,
                deleted=deleted if deleted is not None and deleted >= 0 else None))
    elif item_type == "web_search":
        kind = WorkOperationKind.WEB_SEARCH
        action = item.get("action")
        queries = action.get("queries") if isinstance(action, Mapping) else None
        query = " · ".join(str(value) for value in queries) if isinstance(queries, list) else str(item.get("query") or "")
    elif item_type == "tool_call":
        kind = WorkOperationKind.UNKNOWN
        command = str(item.get("tool") or "")
    else:
        return None
    raw_status = str(item.get("status") or "").lower()
    event_type = str(payload.get("type") or "") if isinstance(payload, Mapping) else ""
    exit_code = _number(item.get("exit_code"))
    if raw_status in {"cancelled", "canceled", "interrupted"}:
        status = WorkOperationStatus.CANCELLED
    elif kind == WorkOperationKind.FILE_SEARCH and exit_code == 1:
        status = WorkOperationStatus.COMPLETED
    elif raw_status in {"failed", "error", "declined"} or (exit_code is not None and exit_code != 0):
        status = WorkOperationStatus.FAILED
    elif raw_status in {"completed", "success"} or event_type == "item.completed":
        status = WorkOperationStatus.COMPLETED
    elif raw_status in {"running", "in_progress"} or event_type in {"item.started", "item.updated"}:
        status = WorkOperationStatus.RUNNING
    else:
        status = WorkOperationStatus.UNKNOWN
    duration = _number(item.get("duration_ms"))
    duration = duration if duration is not None and duration >= 0 else None
    return WorkOperationRecord(id=identity, event_id=event_id, kind=kind, status=status,
        command=command, targets=targets, query=query, duration_ms=duration,
        planned_duration_ms=planned_duration,
        duration_source="upstream" if duration is not None else None,
        exit_code=exit_code, details_available=details_available)


def operation_for_event(event: Mapping[str, Any]) -> dict[str, Any] | None:
    event_id = _number(event.get("event_id"))
    if event_id is None:
        return None
    saved = event.get("pcr_work_operation")
    if isinstance(saved, Mapping):
        record = WorkOperationRecord.from_mapping(saved)
        record.event_id = event_id
        return record.to_mapping()
    # Preserve the legacy representation when an invocation identity was never
    # recorded. Guessing one from text merges retries or duplicates start/end.
    return None


class WorkOperationTracker:
    def __init__(self) -> None:
        self.execution_id = uuid.uuid4().hex
        self.started: dict[str, float] = {}
        self.finished: dict[str, tuple[int | None, str | None]] = {}
        self.terminal_statuses: dict[str, WorkOperationStatus] = {}
        self.pending: dict[str, WorkOperationRecord] = {}
        self.wait_deadlines: dict[str, str | None] = {}

    def observe(self, payload: object, now: float | None = None) -> dict[str, Any] | None:
        item = tool_item(payload)
        if item is None:
            return None
        source_id = item.get("id")
        if not isinstance(source_id, str) or not source_id:
            return None
        record = operation_from_payload(payload, identity=f"{self.execution_id}:{source_id}")
        if record is None:
            return None
        if source_id in self.terminal_statuses and record.status == WorkOperationStatus.RUNNING:
            record.status = self.terminal_statuses[source_id]
        now = time.monotonic() if now is None else now
        if record.status == WorkOperationStatus.RUNNING:
            self.started.setdefault(source_id, now)
            if item.get("type") == "sleep":
                self.wait_deadlines.setdefault(source_id, wait_deadline(
                    datetime.now(timezone.utc).isoformat(), record.planned_duration_ms))
            if source_id not in self.finished:
                self.pending[source_id] = record
        elif record.status != WorkOperationStatus.UNKNOWN and source_id in self.started and record.duration_ms is None:
            record.duration_ms = max(0, round((now - self.started.pop(source_id)) * 1000))
            record.duration_source = "observed"
        if record.status in {WorkOperationStatus.COMPLETED, WorkOperationStatus.FAILED, WorkOperationStatus.CANCELLED}:
            if record.duration_ms is None and source_id in self.finished:
                record.duration_ms, record.duration_source = self.finished[source_id]
            self.started.pop(source_id, None)
            self.pending.pop(source_id, None)
            self.finished[source_id] = (record.duration_ms, record.duration_source)
            self.terminal_statuses[source_id] = record.status
        record.wait_until = self.wait_deadlines.get(source_id)
        # Oversized commands still retain their original event and full log.
        if len(json.dumps(record.to_mapping(), ensure_ascii=False).encode("utf-8")) > 16 * 1024:
            self.pending.pop(source_id, None)
            return None
        return record.to_mapping()

    def finish(self, agent_status: str) -> list[dict[str, Any]]:
        """End incomplete tools without inventing a successful tool completion."""
        records = []
        for record in self.pending.values():
            record.status = (
                WorkOperationStatus.CANCELLED if agent_status in {"cancelled", "killed"}
                else WorkOperationStatus.UNKNOWN
            )
            record.duration_ms = None
            record.duration_source = None
            record.details_available = False
            records.append(record.to_mapping())
        self.pending.clear()
        self.started.clear()
        return records
