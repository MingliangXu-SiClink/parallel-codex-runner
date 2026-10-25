from __future__ import annotations

import argparse
import base64
import binascii
import datetime as dt
import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from copy import deepcopy
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence

from platformdirs import user_state_dir

from .app import (
    LARGE_RUN_STORAGE_WARNING_BYTES,
    available_storage_bytes,
    copy_codex_support_entries,
    estimate_run_storage,
    estimate_staged_run_storage,
    find_rollout_path_for_session,
    format_storage_bytes,
    get_codex_home,
    load_codex_session_history,
    list_resume_sessions,
    normalize_recommend_by,
    promote_best_codex_session_to_workspace,
    retained_resume_preparation,
    retained_execution_preparation,
    agent_execution_workspace,
    run_additional_agents,
    run_once,
    run_synthesis_refresh,
    scrub_agent_codex_homes,
    select_best_result,
    subagent_resume_error,
    validate_args,
)
from .codex_models import CodexModelRegistry
from .codex_model_cache import CodexModelCacheManager
from .codex_cli import format_fast_mode
from .agent_questions import QuestionError, QuestionStore
from .work_operations import tool_item, operation_output_name
from .finalization import FinalizationStageError, finalize_candidate_workspace
from .limits import MAX_AGENT_COUNT, validate_agent_count
from .models import (
    AGENT_ROLE_CANDIDATE,
    AGENT_ROLE_SYNTHESIS,
    AgentResult,
    DEFAULT_FOLLOW_UP_DELAY_SECONDS,
    DEFAULT_NUM_AGENTS,
    DEFAULT_SUBAGENTS_LIMIT,
    normalize_agent_role,
)
from .paths import choose_run_base, is_relative_to
from .synthesis import (
    failed_synthesis_preparation_fingerprint,
    normalize_synthesis_effort,
    normalize_synthesis_fast,
    normalize_synthesis_model,
)
from .workspace import (
    WorkspaceSyncPreflightError,
    capture_sync_receipt,
    cleanup_workspace_copies,
    estimate_path_storage_bytes,
    execution_workspace_mode,
    source_workspace_lease,
    sync_best_workspace_back,
    verify_sync_receipt,
)
from .plugin.artifacts import (
    ArtifactError,
    ArtifactStore,
    write_json_atomic,
    write_text_atomic,
)
from .plugin.cache import LruCache, file_fingerprint
from .plugin.live_diff import LiveDiffSnapshots, live_diff_page
from .plugin.events import EventLog
from .plugin.progress import RunProgressProjection, result_fingerprint
from .plugin.lifecycle import (
    DEFAULT_ARTIFACT_RETENTION_SECONDS,
    DEFAULT_RUN_TTL_SECONDS,
    DEFAULT_STORAGE_QUOTA_BYTES,
    FileSignal,
    RunOperationBusyError,
    RunOperationLock,
    WORKER_PROTOCOL_VERSION,
    configured_positive_int,
    candidate_requests_accepting,
    deadline_timestamp,
    enqueue_candidate_requests,
    enqueue_worker_operation,
    initialize_candidate_requests,
    new_worker_operation_id,
    locked_synthesis_config,
    pid_is_alive,
    read_json,
    spawn_worker,
    utc_deadline,
    worker_candidate_requests_path,
    worker_response_path,
    worker_synthesis_config_path,
    worker_status_path,
)
from .plugin.state import (
    MAX_READABLE_STATE_VERSION,
    STATE_VERSION,
    JsonStateStore,
    ManagedRun,
    int_key_dict as _int_key_dict,
    positive_ints as _positive_ints,
    safe_int as _safe_int,
    utc_now as _utc_now,
)
from .run_statuses import TERMINAL_AGENT_STATUSES
from .visible_paths import WorkspacePathMapping, rewrite_workspace_references


ACTIVE_RUN_STATES = {"preparing", "running", "stopping"}
FINAL_RUN_STATES = {"finalized", "discarded"}
MAX_EVENT_PAGE = 250
DEFAULT_EVENT_PAGE_BYTES = 512 * 1024
MAX_EVENT_PAGE_BYTES = 640 * 1024
DEFAULT_RUN_LIST_PAGE_BYTES = 512 * 1024
MAX_RUN_LIST_PAGE_BYTES = 640 * 1024
DEFAULT_HISTORY_PAGE_BYTES = 512 * 1024
MAX_HISTORY_PAGE_BYTES = 640 * 1024
MAX_HISTORY_ENTRY_TEXT_BYTES = 256 * 1024
DEFAULT_FINAL_MESSAGE_PAGE_BYTES = 192 * 1024
MAX_FINAL_MESSAGE_PAGE_BYTES = 256 * 1024
FINAL_MESSAGE_READ_ATTEMPTS = 3
MAX_DIFF_PAGE_CHARS = 100_000
MAX_STORAGE_SUMMARY_RUNS = 100
UNMANAGED_STORAGE_CACHE_SECONDS = 30.0
CANDIDATE_CLEANUP_RETRY_SECONDS = 60.0
RUN_ID_PATTERN = re.compile(r"^pcr-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")
RESUME_CACHE_SIZE = 8
RESUME_COMPLETE_CACHE_SECONDS = 30.0
RESUME_INCOMPLETE_CACHE_SECONDS = 2.0


class PluginRunError(RuntimeError):
    """Raised when a plugin operation cannot be completed safely."""

    def __init__(
        self,
        message: str = "",
        *,
        error_code: str | None = None,
        user_message: str | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.user_message = user_message


class PluginRunPendingError(PluginRunError):
    """Raised when a safe retry can continue an operation already in progress."""


class PluginRunNotAppliedError(PluginRunError):
    """A preflight rejection that provably did not apply the submitted turn."""


def _read_utf8_text_page(
    path: Path | None,
    *,
    cursor: int,
    max_bytes: int,
    transform: Callable[[str], str] | None = None,
) -> Dict[str, Any]:
    """Read one stable UTF-8 page without splitting a code point."""

    cursor = max(0, int(cursor))
    max_bytes = min(
        max(4 * 1024, int(max_bytes)),
        MAX_FINAL_MESSAGE_PAGE_BYTES,
    )
    if path is None or not path.is_file():
        return _empty_utf8_text_page()

    if transform is not None:
        return _read_transformed_utf8_text_page(
            path,
            cursor=cursor,
            max_bytes=max_bytes,
            transform=transform,
        )

    requested_cursor = cursor
    for _ in range(FINAL_MESSAGE_READ_ATTEMPTS):
        try:
            with path.open("rb") as stream:
                before = os.fstat(stream.fileno())
                cursor = requested_cursor if requested_cursor <= before.st_size else 0
                identity = ":".join(
                    str(value)
                    for value in (
                        before.st_dev,
                        before.st_ino,
                        before.st_size,
                        before.st_mtime_ns,
                    )
                )
                message_id = hashlib.sha256(identity.encode("ascii")).hexdigest()
                stream.seek(cursor)
                raw = stream.read(max_bytes + 4)
                # A cursor from an earlier artifact may now split a code point.
                # Restart the versioned page, never skip bytes to realign it.
                if cursor and raw and raw[0] & 0xC0 == 0x80:
                    cursor = 0
                    stream.seek(0)
                    raw = stream.read(max_bytes + 4)
                after = os.fstat(stream.fileno())
        except FileNotFoundError:
            return _empty_utf8_text_page()
        except OSError as exc:
            raise PluginRunError(f"Cannot read the saved Agent response: {exc}") from exc

        if _file_identity(before) == _file_identity(after):
            break
        requested_cursor = 0
    else:
        # Final responses are written while the Agent is finishing and may also
        # disappear during accepted-run cleanup. This is a transient view race,
        # not an Agent failure. Return a retryable empty page; the normal poller
        # will ask again without putting a technical error in the conversation.
        return _empty_utf8_text_page(has_more=True)

    if len(raw) > max_bytes:
        end = max_bytes
        while end > 0 and raw[end] & 0xC0 == 0x80:
            end -= 1
        raw = raw[:end]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PluginRunError("The saved Agent response is not valid UTF-8.") from exc
    next_cursor = cursor + len(raw)
    return {
        "cursor": cursor,
        "next_cursor": next_cursor,
        "has_more": next_cursor < before.st_size,
        "total_bytes": before.st_size,
        "message_id": message_id,
        "text": text,
    }


def _read_transformed_utf8_text_page(
    path: Path,
    *,
    cursor: int,
    max_bytes: int,
    transform: Callable[[str], str],
) -> Dict[str, Any]:
    requested_cursor = cursor
    for _ in range(FINAL_MESSAGE_READ_ATTEMPTS):
        try:
            before = path.stat()
            raw = path.read_bytes()
            after = path.stat()
        except FileNotFoundError:
            return _empty_utf8_text_page()
        except OSError as exc:
            raise PluginRunError(f"Cannot read the saved Agent response: {exc}") from exc
        if _file_identity(before) == _file_identity(after):
            break
        requested_cursor = 0
    else:
        return _empty_utf8_text_page(has_more=True)
    identity = _file_identity(before)
    try:
        visible = transform(raw.decode("utf-8")).encode("utf-8")
    except UnicodeDecodeError as exc:
        raise PluginRunError("The saved Agent response is not valid UTF-8.") from exc
    cursor = requested_cursor if requested_cursor <= len(visible) else 0
    # Adoption can change rendered paths without changing the underlying file.
    # Validate against the rendered bytes, whose identity the client compares.
    if cursor < len(visible) and visible[cursor] & 0xC0 == 0x80:
        cursor = 0
    end = min(len(visible), cursor + max_bytes)
    while end > cursor and end < len(visible) and visible[end] & 0xC0 == 0x80:
        end -= 1
    page = visible[cursor:end]
    message_identity = ":".join(str(value) for value in identity).encode("ascii")
    message_id = hashlib.sha256(
        message_identity + b":" + hashlib.sha256(visible).digest()
    ).hexdigest()
    return {
        "cursor": cursor,
        "next_cursor": end,
        "has_more": end < len(visible),
        "total_bytes": len(visible),
        "message_id": message_id,
        "text": page.decode("utf-8"),
    }


def _file_identity(stat_result: os.stat_result) -> tuple[int, int, int, int]:
    return (
        stat_result.st_dev,
        stat_result.st_ino,
        stat_result.st_size,
        stat_result.st_mtime_ns,
    )


def _empty_utf8_text_page(*, has_more: bool = False) -> Dict[str, Any]:
    return {
        "cursor": 0,
        "next_cursor": 0,
        "has_more": has_more,
        "total_bytes": 0,
        "message_id": "",
        "text": "",
    }


def _history_payloads(
    entries: Sequence[Any],
    *,
    path_mappings: Sequence[WorkspacePathMapping] = (),
) -> list[Dict[str, Any]]:
    """Split only protocol-sized history entries on UTF-8 boundaries."""
    payloads: list[Dict[str, Any]] = []
    for entry_index, entry in enumerate(entries):
        payload = asdict(entry)
        entry_id = str(payload.get("id") or "").strip()
        if not entry_id:
            payload.pop("id", None)
        if not str(payload.get("turn_id") or "").strip():
            payload.pop("turn_id", None)
        text = str(payload.get("text") or "")
        category = str(payload.get("category") or "").strip().casefold()
        if category in {"output", "assistant", "agent_message"}:
            text = rewrite_workspace_references(text, path_mappings)
            payload["text"] = text
        encoded = text.encode("utf-8")
        if len(encoded) <= MAX_HISTORY_ENTRY_TEXT_BYTES:
            payloads.append(payload)
            continue
        start = 0
        while start < len(encoded):
            end = min(len(encoded), start + MAX_HISTORY_ENTRY_TEXT_BYTES)
            while end < len(encoded) and end > start and encoded[end] & 0xC0 == 0x80:
                end -= 1
            if end == start:
                end = min(len(encoded), start + MAX_HISTORY_ENTRY_TEXT_BYTES)
                while end < len(encoded) and encoded[end] & 0xC0 == 0x80:
                    end += 1
            fragment = dict(payload)
            fragment["id"] = (
                f"{entry_id}:bytes:{start}"
                if entry_id
                else f"history-fragment:{entry_index}:{start}"
            )
            fragment["text"] = encoded[start:end].decode("utf-8")
            payloads.append(fragment)
            start = end
    return payloads


def _validated_agent_count(value: int, name: str, *, allow_zero: bool = False) -> int:
    try:
        return validate_agent_count(value, name, allow_zero=allow_zero)
    except ValueError as exc:
        raise PluginRunError(str(exc)) from exc


def _validate_run_agent_total(candidate_agents: int, synthesis_agents: int) -> None:
    total = candidate_agents + synthesis_agents
    if total > MAX_AGENT_COUNT:
        raise PluginRunError(
            f"candidate and synthesis Agents total {total}, which exceeds the "
            f"engineering guard of {MAX_AGENT_COUNT}"
        )


def _set_agent_status(run: ManagedRun, idx: int, status: object) -> None:
    incoming = str(status or "unknown").strip().lower() or "unknown"
    current = str(run.agent_statuses.get(idx) or "").strip().lower()
    if current in TERMINAL_AGENT_STATUSES and incoming not in TERMINAL_AGENT_STATUSES:
        return
    run.agent_statuses[idx] = incoming


def _seal_agent_statuses(run: ManagedRun, *, cancelled: bool) -> None:
    fallback = "cancelled" if cancelled else "failed"
    for idx in set(run.agent_statuses) | set(run.results):
        result_status = str((run.results.get(idx) or {}).get("status") or "")
        current = str(run.agent_statuses.get(idx) or "")
        resolved = result_status or current
        if resolved.strip().lower() not in TERMINAL_AGENT_STATUSES:
            resolved = fallback
        resolved = resolved.strip().lower()
        run.agent_statuses[idx] = resolved
        result_status = str((run.results.get(idx) or {}).get("status") or "")
        if idx in run.results and result_status.strip().lower() not in TERMINAL_AGENT_STATUSES:
            run.results[idx] = dict(run.results[idx], status=resolved)


def _run_cursor(created_at: str, run_id: str) -> str:
    raw = json.dumps(
        {"created_at": created_at, "run_id": run_id},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _parse_run_cursor(value: str) -> tuple[str, str]:
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        created_at = str(payload["created_at"]).strip()
        run_id = str(payload["run_id"]).strip()
        if not created_at or not run_id or len(run_id) > 256:
            raise ValueError
        return created_at, run_id
    except (ValueError, KeyError, TypeError, UnicodeDecodeError, binascii.Error) as exc:
        raise PluginRunError("Run list cursor is invalid") from exc


def _resume_rollout_line_count(session_id: Optional[str]) -> Optional[int]:
    if not session_id:
        return 0
    path = find_rollout_path_for_session(get_codex_home(), session_id)
    if path is None or not path.is_file():
        return None
    try:
        with path.open("rb") as stream:
            return sum(1 for _line in stream)
    except OSError:
        return None


def _storage_filesystem_id(path: Path) -> int:
    """Return the filesystem device that would contain ``path``."""
    candidate = path.expanduser().resolve()
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    if candidate.is_file():
        candidate = candidate.parent
    return int(candidate.stat().st_dev)


def _storage_free_space_summary(paths: set[Path]) -> tuple[int, int]:
    """Return conservative free space and distinct volume count for run bases."""
    free_by_filesystem: dict[int, int] = {}
    for path in sorted(paths, key=str):
        try:
            filesystem_id = _storage_filesystem_id(path)
            free_bytes = available_storage_bytes(path)
        except OSError:
            continue
        previous = free_by_filesystem.get(filesystem_id)
        free_by_filesystem[filesystem_id] = (
            free_bytes if previous is None else min(previous, free_bytes)
        )
    return min(free_by_filesystem.values(), default=0), len(free_by_filesystem)


def default_plugin_state_dir() -> Path:
    configured = os.environ.get("PCR_PLUGIN_DATA", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (
        Path(user_state_dir("parallel-codex-runner", "parallel-codex-runner"))
        / "plugin"
    ).resolve()


@dataclass
class _RunContext:
    cancel_event: threading.Event
    agent_cancel_events: Dict[int, threading.Event]
    thread: Optional[threading.Thread] = None


@dataclass(frozen=True)
class _StorageMeasurement:
    updated_at: str
    root_signature: tuple[tuple[int, int, int, int], ...]
    actual_bytes: int
    workspace_bytes: int
    codex_home_bytes: int


class PluginRunManager:
    """Thread-safe headless PCR controller used by the Codex plugin."""

    def __init__(
        self,
        state_dir: Optional[Path] = None,
        *,
        detached_workers: bool | None = None,
        enforce_storage_quota: bool = True,
        retain_history: bool = False,
        run_maintenance: bool = True,
    ) -> None:
        self.state_dir = (state_dir or default_plugin_state_dir()).expanduser().resolve()
        self.events_dir = self.state_dir / "events"
        self.state_path = self.state_dir / "runs.json"
        self.locks_dir = self.state_dir / "locks"
        self.control_dir = self.state_dir / "control"
        self._state_store = JsonStateStore(self.state_dir)
        self._events = EventLog(self.events_dir)
        self._progress_cache: LruCache[str, RunProgressProjection] = LruCache(256)
        self._artifacts = ArtifactStore()
        self._live_diffs = LiveDiffSnapshots()
        self._model_catalog: CodexModelCacheManager | None = None
        self._detached_workers = (
            state_dir is None
            if detached_workers is None
            else bool(detached_workers)
        )
        self._lock = threading.RLock()
        self._runs: Dict[str, ManagedRun] = {}
        self._contexts: Dict[str, _RunContext] = {}
        self._worker_processes: Dict[str, Any] = {}
        self._resume_cache: LruCache[tuple[Any, ...], tuple[Any, list[Any]]] = (
            LruCache(RESUME_CACHE_SIZE)
        )
        self._resume_condition = threading.Condition(threading.RLock())
        self._resume_inflight: set[tuple[Any, ...]] = set()
        self._storage_condition = threading.Condition(threading.RLock())
        self._storage_measurements: dict[str, _StorageMeasurement] = {}
        self._storage_inflight: set[
            tuple[str, str, tuple[tuple[int, int, int, int], ...]]
        ] = set()
        self._unmanaged_storage_measurements: dict[
            str, tuple[tuple[int, int, int, int], float, int]
        ] = {}
        self._startup_warnings: list[str] = []
        self._run_operations: Dict[str, str] = {}
        self._async_answer_dispatching: set[str] = set()
        self._async_answer_retry_after: dict[str, float] = {}
        # Reads (health, runs.list, turns.get) previously re-parsed runs.json
        # on every call while holding self._lock, serializing all concurrent
        # clients. Cache the file's (mtime_ns, size) so we only re-parse when
        # the file actually changed. This is safe across processes: any writer
        # (worker or peer manager) does an atomic os.replace which always
        # changes mtime. Unlike a time-based throttle, a stale cache is
        # impossible because a write always produces a new file stamp.
        self._state_file_stamp: tuple[int, int] | None = None  # (mtime_ns, size)
        self._closed = False
        self._cleanup_stop = threading.Event()
        self._cleanup_thread: threading.Thread | None = None
        self.run_ttl_seconds = configured_positive_int(
            "PCR_PLUGIN_RUN_TTL_SECONDS", DEFAULT_RUN_TTL_SECONDS
        )
        self.artifact_retention_seconds = configured_positive_int(
            "PCR_PLUGIN_RETENTION_SECONDS", DEFAULT_ARTIFACT_RETENTION_SECONDS
        )
        self.storage_quota_bytes = configured_positive_int(
            "PCR_PLUGIN_STORAGE_QUOTA_BYTES", DEFAULT_STORAGE_QUOTA_BYTES
        )
        self.enforce_storage_quota = bool(enforce_storage_quota)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        # This store is shared with detached workers. Persist the policy outside
        # runs.json so every process, including a newly started worker, sees it.
        self._retain_history_path = self.state_dir / "retain-history"
        if retain_history:
            self._retain_history_path.touch(mode=0o600, exist_ok=True)
        self.events_dir.mkdir(parents=True, exist_ok=True)
        self.locks_dir.mkdir(parents=True, exist_ok=True)
        self.control_dir.mkdir(parents=True, exist_ok=True)
        self._harden_path(self.state_dir, 0o700)
        self._harden_path(self.events_dir, 0o700)
        self._load_state()
        if run_maintenance:
            self._run_maintenance_locked()

    @staticmethod
    def _harden_path(path: Path, mode: int) -> None:
        try:
            path.chmod(mode)
        except OSError:
            pass

    def _preserve_invalid_state(self, reason: str) -> None:
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = self.state_path.with_name(
            f"runs.corrupt-{stamp}-{uuid.uuid4().hex[:6]}.json"
        )
        try:
            os.replace(self.state_path, backup)
            self._startup_warnings.append(f"{reason}; preserved at {backup}")
        except OSError as exc:
            self._startup_warnings.append(
                f"{reason}; the invalid state could not be preserved: {exc}"
            )

    def _load_state(self) -> None:
        try:
            payload = self._state_store.load_payload()
        except FileNotFoundError:
            return
        except OSError as exc:
            self._startup_warnings.append(
                f"Could not read plugin state {self.state_path}: {exc}"
            )
            return
        except ValueError:
            self._preserve_invalid_state("Plugin state is not valid JSON")
            return
        if payload is None:
            return
        if not isinstance(payload, dict):
            self._preserve_invalid_state(
                "Plugin state root is not a JSON object"
            )
            return
        version = _safe_int(payload.get("version"), STATE_VERSION)
        if version > MAX_READABLE_STATE_VERSION:
            raise PluginRunError(
                f"Plugin state version {version} is newer than supported version "
                f"{MAX_READABLE_STATE_VERSION}"
            )
        values = payload.get("runs", [])
        if not isinstance(values, list):
            self._preserve_invalid_state(
                "Plugin state does not contain a run list"
            )
            return
        # Version 4 only introduced additive fields. Rewrite it as the stable
        # version-3 envelope so already-running version-3 workers do not reject
        # the shared state after pcrd itself has been upgraded.
        changed = version != STATE_VERSION
        for value in values:
            if not isinstance(value, dict):
                continue
            run = ManagedRun.from_dict(value)
            if not RUN_ID_PATTERN.fullmatch(run.run_id):
                continue
            self._runs[run.run_id] = run
            if run.status in ACTIVE_RUN_STATES | {"finalizing"} or run.worker_pid:
                if self._refresh_events_locked(run, save=False):
                    changed = True
            if self._detect_worker_failure_locked(run):
                changed = True
            previous_status = run.status
            self._recover_finalization_locked(run)
            if run.status == "finalizing" and run.finalized_agent is not None:
                run.status = "cleanup_failed"
                run.error = (
                    "Legacy state records sync-back as complete; retry cleanup "
                    "without syncing again."
                )
                run.updated_at = _utc_now()
            if run.status != previous_status:
                changed = True
        if changed:
            self._save_state_locked()

    def _save_state_locked(
        self,
        *,
        deleted_run_ids: set[str] | None = None,
    ) -> None:
        deleted = deleted_run_ids or set()
        with self._state_store.locked():
            try:
                payload = self._state_store.load_payload()
            except (OSError, ValueError):
                payload = None
            if isinstance(payload, dict) and isinstance(payload.get("runs"), list):
                for value in payload["runs"]:
                    if not isinstance(value, dict):
                        continue
                    disk_run = ManagedRun.from_dict(value)
                    if disk_run.run_id in deleted:
                        continue
                    local = self._runs.get(disk_run.run_id)
                    if local is None or disk_run.updated_at > local.updated_at:
                        self._runs[disk_run.run_id] = disk_run
            for run_id in deleted:
                self._runs.pop(run_id, None)
            if self.retains_history:
                # Empty expiry is also understood by older workers. Do not
                # rewrite activity timestamps when migrating existing history.
                for run in self._runs.values():
                    run.expires_at = ""
            self._state_store.write_runs(self._runs)
        self._harden_path(self.state_path, 0o600)
        # Record the stamp of the file we just wrote so the in-memory state
        # (already current) is not overwritten by a redundant re-parse on the
        # next read. A peer process's stale stamp still differs and re-loads.
        try:
            st = self.state_path.stat()
            self._state_file_stamp = (st.st_mtime_ns, st.st_size)
        except OSError:
            self._state_file_stamp = None

    def _create_context(
        self,
        run_id: str,
        agent_indices: Sequence[int],
    ) -> _RunContext:
        context = self._contexts.get(run_id)
        if context is not None:
            context.cancel_event = threading.Event()
            context.agent_cancel_events = {
                idx: threading.Event() for idx in agent_indices
            }
            return context
        context = _RunContext(
            cancel_event=threading.Event(),
            agent_cancel_events={idx: threading.Event() for idx in agent_indices},
        )
        self._contexts[run_id] = context
        return context

    def _drop_context_locked(
        self,
        run_id: str,
        *,
        remove_event_file: bool = False,
    ) -> None:
        context = self._contexts.pop(run_id, None)
        if context is None:
            return
        if remove_event_file:
            self._events.delete(run_id)

    def _append_event_locked(
        self,
        run: ManagedRun,
        payload: Dict[str, Any],
    ) -> None:
        self._events.append(run.run_id, payload)
        self._refresh_events_locked(run, save=False)

    def _sync_state_from_disk_locked(self, *, force: bool = False) -> None:
        # Check mtime+size of runs.json before acquiring the cross-process lock
        # and re-parsing JSON. Any writer (worker or peer manager instance) does
        # an atomic os.replace which always updates the mtime. This avoids
        # redundant JSON parsing on rapid read bursts while remaining correct
        # across multiple processes sharing the same state_dir — unlike a pure
        # time-based throttle, a stale cache is impossible because a write always
        # changes the file stamp.
        state_path = self._state_store.path
        try:
            st = state_path.stat()
            current_stamp: tuple[int, int] | None = (st.st_mtime_ns, st.st_size)
        except OSError:
            current_stamp = None
        if not force and current_stamp is not None and current_stamp == self._state_file_stamp:
            return
        try:
            with self._state_store.locked():
                payload = self._state_store.load_payload()
                # Re-stat after acquiring the lock so we capture the stamp of
                # the file we actually read, not a newer concurrent write.
                try:
                    st2 = state_path.stat()
                    self._state_file_stamp = (st2.st_mtime_ns, st2.st_size)
                except OSError:
                    self._state_file_stamp = None
        except (OSError, ValueError):
            return
        if not isinstance(payload, dict) or not isinstance(payload.get("runs"), list):
            return
        disk_run_ids: set[str] = set()
        for value in payload["runs"]:
            if not isinstance(value, dict):
                continue
            disk_run = ManagedRun.from_dict(value)
            if not RUN_ID_PATTERN.fullmatch(disk_run.run_id):
                continue
            disk_run_ids.add(disk_run.run_id)
            local = self._runs.get(disk_run.run_id)
            if local is None or disk_run.updated_at > local.updated_at:
                self._runs[disk_run.run_id] = disk_run
        for run_id, run in list(self._runs.items()):
            if run_id in disk_run_ids or run_id in self._run_operations:
                continue
            if not self._worker_active_locked(run):
                self._runs.pop(run_id, None)

    def _require_run_locked(self, run_id: str) -> ManagedRun:
        self._sync_state_from_disk_locked()
        run = self._runs.get(run_id)
        if run is None:
            raise PluginRunError(f"Unknown PCR run: {run_id}")
        changed = self._refresh_events_locked(run, save=False)
        if self._uses_persistent_worker(run):
            changed = self._detect_worker_failure_locked(run) or changed
        if changed:
            self._save_state_locked()
        return run

    def _active_context_thread_locked(
        self,
    ) -> Optional[tuple[str, threading.Thread]]:
        for run_id, context in self._contexts.items():
            thread = context.thread
            if thread is not None and thread.is_alive():
                return run_id, thread
        return None

    @staticmethod
    def _uses_persistent_worker(run: ManagedRun) -> bool:
        return run.worker_protocol == WORKER_PROTOCOL_VERSION

    def _worker_alive_locked(self, run: ManagedRun) -> bool:
        context = self._contexts.get(run.run_id)
        if context is not None and context.thread is not None:
            return context.thread.is_alive()
        process = self._worker_processes.get(run.run_id)
        if process is not None:
            if process.poll() is not None:
                self._worker_processes.pop(run.run_id, None)
                return False
            return True
        return pid_is_alive(run.worker_pid)

    def _worker_active_locked(self, run: ManagedRun) -> bool:
        context = self._contexts.get(run.run_id)
        if context is not None and context.thread is not None:
            return context.thread.is_alive()
        if self._uses_persistent_worker(run):
            return self._worker_alive_locked(run) and bool(
                run.worker_operation
                or run.worker_state in {"starting", "busy"}
            )
        return self._worker_alive_locked(run)

    def _worker_executing_agents_locked(self, run: ManagedRun) -> bool:
        if not self._worker_active_locked(run):
            return False
        return not self._uses_persistent_worker(run) or run.worker_operation in {
            "initial", "more", "retry"
        }

    def _result_operation_pending_locked(self, run: ManagedRun) -> bool:
        return run.status == "finalizing" or (
            run.worker_operation in {"finalize", "recover_finalization", "discard"}
            and self._worker_active_locked(run)
        )

    def _worker_available_locked(self, run: ManagedRun) -> bool:
        return (
            self._uses_persistent_worker(run)
            and run.worker_state not in {"crashed", "stopped"}
            and self._worker_alive_locked(run)
        )

    def _idle_expiry_recovery_available_locked(self, run: ManagedRun) -> bool:
        """Whether an old worker was retired only while waiting for a decision."""
        if (
            not self._uses_persistent_worker(run)
            or run.status != "completed"
            or run.worker_state != "stopped"
            or self._worker_alive_locked(run)
        ):
            return False
        status = read_json(self.state_dir / "workers" / f"{run.run_id}.json")
        return (
            str(status.get("stop_reason") or "") == "expired"
            and not bool(status.get("active"))
            and not bool(status.get("expired"))
            and int(status.get("exit_code") or 0) == 0
        )

    def _completed_result_recovery_available_locked(self, run: ManagedRun) -> bool:
        """Finalize saved results without recreating the old execution worker."""
        if (
            not self._uses_persistent_worker(run)
            or run.status != "completed"
            or run.finalized_agent is not None
            or self._worker_alive_locked(run)
            or run.worker_operation
            or run.worker_operation_id
            or not run.results
            or any(status not in TERMINAL_AGENT_STATUSES for status in run.agent_statuses.values())
            or any(result.get("status") not in TERMINAL_AGENT_STATUSES for result in run.results.values())
            or not any(result.get("status") == "success" for result in run.results.values())
        ):
            return False
        # A terminal event alone is insufficient: the worker must have durably
        # finished the operation, with no queued mutation or unresolved sync.
        status = read_json(self.state_dir / "workers" / f"{run.run_id}.json")
        checkpoint_complete = (
            status.get("protocol") == WORKER_PROTOCOL_VERSION
            and status.get("run_id") == run.run_id
            and status.get("active") is False
            and not status.get("operation")
            and not status.get("operation_id")
            and bool(status.get("finished_at"))
            and status.get("last_operation") in {"initial", "more", "retry"}
            and status.get("exit_code") == 0
            and not status.get("expired")
            and not status.get("error")
        )
        if not checkpoint_complete and not self._idle_expiry_recovery_available_locked(run):
            return False
        try:
            if any((self.state_dir / "workers" / "requests").glob(f"{run.run_id}-*.json")):
                return False
            candidates = read_json(worker_candidate_requests_path(self.state_dir, run.run_id))
            if candidates.get("requests"):
                return False
            journal_path = self._finalization_journal_path(run)
            if journal_path.exists() and read_json(journal_path).get("phase") != "sync_not_applied":
                return False
        except (OSError, PluginRunError):
            return False
        return True

    def _detect_worker_failure_locked(self, run: ManagedRun) -> bool:
        if not self._uses_persistent_worker(run):
            if run.status not in ACTIVE_RUN_STATES or self._worker_alive_locked(run):
                return False
            run.status = "interrupted"
            run.error = (
                "The PCR worker ended before this legacy run completed. Inspect "
                "retained artifacts or discard the run."
            )
            for idx, status in list(run.agent_statuses.items()):
                if status in {"copying", "queued", "starting", "resuming", "submitting", "waiting_for_output", "waiting_for_models", "running", "stopping"}:
                    run.agent_statuses[idx] = "interrupted"
            run.updated_at = _utc_now()
            return True
        if self._worker_alive_locked(run):
            return False
        # Completion can be committed after our first event read but before the
        # process exits. After observing exit, its event log is stable: consume
        # that terminal tail before classifying or scrubbing an unfinished run.
        changed = self._refresh_events_locked(run, save=False)
        if run.status in FINAL_RUN_STATES or self._completed_result_recovery_available_locked(run):
            worker_changed = bool(
                run.worker_state != "stopped"
                or run.worker_pid is not None
                or run.worker_operation
                or run.worker_operation_id
                or run.worker_error
            )
            if worker_changed:
                if run.worker_error and run.error == run.worker_error:
                    run.error = ""
                run.worker_pid = None
                run.worker_state = "stopped"
                run.worker_operation = ""
                run.worker_operation_id = ""
                run.worker_error = ""
                run.updated_at = _utc_now()
            return worker_changed or changed
        if run.worker_state in {"crashed", "stopped"}:
            return changed
        message = (
            "This run's persistent PCR worker ended unexpectedly. Its code version "
            "was pinned when the run started, so PCR will not replace it with a "
            "worker loaded from different source. The run cannot continue."
        )
        run.worker_pid = None
        run.worker_state = "crashed"
        run.worker_operation = ""
        run.worker_operation_id = ""
        run.worker_error = message
        if run.status in ACTIVE_RUN_STATES | {"finalizing"}:
            run.status = "interrupted"
            run.error = message
            for idx, status in list(run.agent_statuses.items()):
                if status in {"copying", "queued", "starting", "resuming", "submitting", "waiting_for_output", "waiting_for_models", "running", "stopping"}:
                    run.agent_statuses[idx] = "interrupted"
            if run.run_root:
                try:
                    root = self._validated_run_root(run)
                    scrub_agent_codex_homes(root / "meta")
                except Exception as exc:
                    run.error += (
                        " Temporary Codex support files could not be scrubbed "
                        f"automatically: {exc}"
                    )
        run.updated_at = _utc_now()
        return True

    def _require_persistent_worker_locked(self, run: ManagedRun) -> None:
        if self._worker_available_locked(run):
            return
        self._detect_worker_failure_locked(run)
        detail = run.worker_error or (
            "This run does not have the persistent worker that loaded its original "
            "PCR code."
        )
        raise PluginRunError(f"{detail} Inspect or discard the retained run instead.")

    def _claim_run_operation_locked(self, run_id: str, operation: str) -> None:
        active = self._run_operations.get(run_id)
        if active is not None:
            raise PluginRunError(
                f"PCR run {run_id} is already performing {active}"
            )
        self._run_operations[run_id] = operation

    def _release_run_operation_locked(self, run_id: str, operation: str) -> None:
        if self._run_operations.get(run_id) == operation:
            self._run_operations.pop(run_id, None)
        self._finish_shutdown_locked()

    def _require_no_run_operation_locked(self, run_id: str) -> None:
        operation = self._run_operations.get(run_id)
        if operation is not None:
            raise PluginRunError(
                f"PCR run {run_id} is already performing {operation}"
            )

    def _require_no_active_operation_locked(self) -> None:
        if not self._run_operations:
            return
        run_id, operation = next(iter(self._run_operations.items()))
        raise PluginRunError(
            f"PCR run {run_id} is still performing {operation}"
        )

    @contextmanager
    def _run_operation_guard(
        self,
        run_id: str,
        operation: str,
    ) -> Any:
        try:
            with RunOperationLock(self.locks_dir, run_id, operation):
                with self._lock:
                    self._require_run_locked(run_id)
                    self._claim_run_operation_locked(run_id, operation)
                try:
                    yield
                finally:
                    with self._lock:
                        self._release_run_operation_locked(run_id, operation)
        except RuntimeError as exc:
            raise PluginRunError(str(exc)) from exc

    def _finish_background_thread_locked(self, run_id: str) -> None:
        context = self._contexts.get(run_id)
        if context is not None:
            context.thread = None
        run = self._runs.get(run_id)
        if run is not None and not self._uses_persistent_worker(run):
            run.worker_operation = ""
            run.worker_operation_id = ""
        self._finish_shutdown_locked()

    def _finish_shutdown_locked(self) -> None:
        if not self._closed or self._run_operations:
            return
        self._sync_state_from_disk_locked()
        self._save_state_locked()

    def _recommendation_candidates_locked(self, run: ManagedRun) -> list[AgentResult]:
        candidates: list[AgentResult] = []
        rejected = set(run.rejected_agents) | set(run.stale_synthesis_agents)
        for idx, value in run.results.items():
            if idx in rejected or value.get("status") != "success":
                continue
            try:
                candidates.append(AgentResult(**value))
            except (TypeError, ValueError):
                continue
        return candidates

    def _recompute_recommendation_locked(self, run: ManagedRun) -> None:
        best = select_best_result(
            self._recommendation_candidates_locked(run),
            str(run.config.get("recommend_by") or "reasoning_tokens"),
            warn_missing_tokens=False,
        )
        run.recommended_agent = best.idx if best is not None else None

    def _apply_progress_locked(
        self,
        run: ManagedRun,
        payload: Dict[str, Any],
    ) -> None:
        kind = str(payload.get("type") or "")
        idx = _safe_int(payload.get("idx"))
        if kind == "run_prepared":
            rows = payload.get("rows")
            if isinstance(rows, list):
                values: Dict[str, str] = {}
                for row in rows:
                    if not isinstance(row, (list, tuple)) or len(row) != 2:
                        continue
                    key, value = row
                    if isinstance(key, str):
                        values[key] = str(value)
                run.run_root = values.get("RUNS_ROOT", run.run_root)
                if run.run_root:
                    self._write_run_marker_locked(run)
            run.status = "running"
        elif kind == "plugin_worker_started":
            worker_pid = _safe_int(payload.get("pid"))
            if worker_pid > 0:
                run.worker_pid = worker_pid
            protocol = _safe_int(payload.get("protocol"))
            if protocol > 0:
                run.worker_protocol = protocol
                run.worker_state = "busy" if run.worker_operation else "idle"
                run.worker_error = ""
            if run.status == "preparing":
                run.status = "running"
        elif kind == "plugin_operation_started":
            worker_pid = _safe_int(payload.get("pid"))
            if worker_pid > 0:
                run.worker_pid = worker_pid
            run.worker_state = "busy"
            run.worker_operation = str(payload.get("operation") or "")
            run.completion_reason = (
                "finalization" if run.worker_operation in {"finalize", "recover_finalization"}
                else "discard" if run.worker_operation == "discard" else ""
            )
            run.worker_operation_id = str(payload.get("operation_id") or "")
            run.worker_error = ""
        elif kind == "agent_status" and idx > 0:
            if payload.get("role") is not None:
                run.agent_roles[idx] = normalize_agent_role(payload.get("role"))
            _set_agent_status(
                run,
                idx,
                payload.get("status") or run.agent_statuses.get(idx, "queued"),
            )
        elif kind == "agent_process_started" and idx > 0:
            _set_agent_status(run, idx, "starting")
        elif kind == "agent_started" and idx > 0:
            if payload.get("role") is not None:
                run.agent_roles[idx] = normalize_agent_role(payload.get("role"))
            _set_agent_status(run, idx, "running")
        elif kind == "async_answer_started" and idx > 0:
            run.status = "running"
            run.completion_reason = ""
            run.results.pop(idx, None)
            run.agent_statuses[idx] = "queued"
        elif kind == "agent_tokens" and idx > 0:
            value = payload.get("reasoning_tokens")
            if isinstance(value, int):
                run.agent_tokens[idx] = value
        elif kind == "agent_finished" and idx > 0:
            result = payload.get("result")
            if isinstance(result, dict):
                run.results[idx] = dict(result)
                run.agent_roles[idx] = normalize_agent_role(
                    result.get("role", payload.get("role"))
                )
                _set_agent_status(run, idx, result.get("status") or "success")
                token_value = result.get("reasoning_tokens")
                run.agent_tokens[idx] = (
                    int(token_value) if isinstance(token_value, int) else None
                )
                self._recompute_recommendation_locked(run)
        elif kind == "synthesis_started":
            raw_indices = payload.get("indices")
            if isinstance(raw_indices, list):
                for value in raw_indices:
                    synthesis_idx = _safe_int(value)
                    if synthesis_idx > 0:
                        run.agent_roles[synthesis_idx] = AGENT_ROLE_SYNTHESIS
                        # A synthesis refresh reuses Agent ids, not the previous
                        # attempt's terminal result. Keep late-progress guards
                        # within an attempt, but reset at this explicit start.
                        run.agent_statuses[synthesis_idx] = "queued"
                        run.results.pop(synthesis_idx, None)
                        run.agent_tokens[synthesis_idx] = None
                self._recompute_recommendation_locked(run)
        elif kind == "synthesis_finished":
            if not payload.get("error") and not payload.get("skipped"):
                raw_indices = payload.get("indices")
                refreshed = {
                    _safe_int(value)
                    for value in raw_indices
                } if isinstance(raw_indices, list) else set()
                run.stale_synthesis_agents = sorted(
                    set(run.stale_synthesis_agents)
                    - {value for value in refreshed if value > 0}
                )
                self._recompute_recommendation_locked(run)
        elif kind == "synthesis_revalidated":
            run.stale_synthesis_agents = sorted(
                set(run.stale_synthesis_agents) - set(_positive_ints(payload.get("indices")))
            )
            self._recompute_recommendation_locked(run)
        elif kind == "run_finished":
            cancelled = bool(payload.get("cancelled"))
            run.completion_reason = "cancelled" if cancelled else "execution"
            _seal_agent_statuses(run, cancelled=cancelled)
            run.status = "cancelled" if cancelled else "completed"
            root = payload.get("run_root")
            if isinstance(root, str) and root:
                if run.run_root and Path(root).resolve() != Path(run.run_root).resolve():
                    raise PluginRunError("PCR reported a different run root at completion")
                if not run.run_root:
                    run.run_root = root
                    self._write_run_marker_locked(run)
            self._recompute_recommendation_locked(run)
        elif kind == "run_failed":
            run.completion_reason = "execution"
            run.status = "failed"
            run.error = str(payload.get("message") or "PCR run failed")
        elif kind == "batch_finished":
            cancelled = bool(payload.get("cancelled"))
            run.completion_reason = "cancelled" if cancelled else "execution"
            _seal_agent_statuses(run, cancelled=cancelled)
            run.status = "cancelled" if cancelled else "completed"
            self._recompute_recommendation_locked(run)
        elif kind == "batch_failed":
            run.completion_reason = "execution"
            run.status = "failed"
            run.error = str(
                payload.get("message") or "Additional PCR candidates failed"
            )
        elif kind == "agent_selected_for_finalization" and idx > 0:
            run.completion_reason = "finalization"
            run.finalized_agent = idx
            run.synced_back = bool(payload.get("synced_back"))
            promotion = payload.get("session_promotion")
            run.session_promotion = (
                dict(promotion) if isinstance(promotion, dict) else None
            )
            run.status = "finalizing"
            run.error = ""
        elif kind == "agent_finalized" and idx > 0:
            run.completion_reason = "finalization"
            run.finalized_agent = idx
            run.finalizing_agent = None
            run.synced_back = bool(payload.get("synced_back"))
            run.workspaces_deleted = bool(payload.get("workspaces_deleted"))
            run.codex_homes_deleted = bool(payload.get("codex_homes_deleted"))
            run.cleanup_error = str(payload.get("cleanup_error") or "")
            promotion = payload.get("session_promotion")
            run.session_promotion = (
                dict(promotion) if isinstance(promotion, dict) else None
            )
            run.status = "finalized"
            run.error = ""
        elif kind == "workspace_cleanup_failed" and idx > 0:
            run.finalized_agent = idx
            # Older workers report this only after sync and session promotion.
            run.status = "finalized"
            run.cleanup_error = str(payload.get("message") or "Candidate cleanup failed")
            run.error = ""
        elif kind == "workspace_cleanup_updated":
            run.workspaces_deleted = bool(payload.get("workspaces_deleted"))
            run.codex_homes_deleted = bool(payload.get("codex_homes_deleted"))
            run.cleanup_error = str(payload.get("cleanup_error") or "")
        elif kind == "finalize_failed" and idx > 0:
            run.finalized_agent = idx
            run.status = "sync_ambiguous"
            run.error = str(payload.get("message") or "Sync-back failed")
        elif kind == "finalization_metadata_failed":
            run.error = str(
                payload.get("message") or "Could not update finalization metadata"
            )
        elif kind == "finalization_recovered" and idx > 0:
            run.completion_reason = "finalization"
            if bool(payload.get("sync_was_applied")):
                run.finalized_agent = idx
                run.synced_back = True
                run.status = "cleanup_failed"
                run.error = (
                    "Sync-back was confirmed. Call pcr_accept_agent for the same "
                    "Agent to finish cleanup without syncing again."
                )
            else:
                run.finalized_agent = None
                run.synced_back = False
                run.status = "completed"
                run.error = ""
        elif kind == "run_discarded":
            run.completion_reason = "discard"
            run.status = "discarded"
            run.workspaces_deleted = bool(payload.get("workspaces_deleted"))
            run.codex_homes_deleted = bool(payload.get("codex_homes_deleted"))
        elif kind in {"plugin_operation_finished", "plugin_worker_finished"}:
            persistent = kind == "plugin_operation_finished"
            if persistent:
                run.worker_state = "idle"
                run.worker_operation = ""
                run.worker_operation_id = ""
                run.worker_deadline = ""
            else:
                run.worker_pid = None
                run.worker_operation = ""
            if payload.get("expired"):
                run.status = "expired"
                run.error = "PCR worker exceeded its configured runtime limit"
                if "workspaces_deleted" in payload:
                    run.workspaces_deleted = bool(payload.get("workspaces_deleted"))
                if "codex_homes_deleted" in payload:
                    run.codex_homes_deleted = bool(payload.get("codex_homes_deleted"))
                cleanup_error = str(payload.get("cleanup_error") or "").strip()
                if cleanup_error:
                    run.error += f"; artifact cleanup failed: {cleanup_error}"
        elif kind == "plugin_operation_failed":
            run.worker_error = str(
                payload.get("message") or "Persistent PCR worker operation failed"
            )
            operation = str(payload.get("operation") or run.worker_operation)
            if (
                operation == "finalize"
                and run.status == "finalizing"
                and run.finalized_agent is None
            ):
                run.status = "completed"
                run.error = run.worker_error
            elif operation == "discard":
                run.error = run.worker_error
            elif operation in {"initial", "more", "retry"}:
                run.status = "failed"
                run.error = run.worker_error
        elif kind == "plugin_worker_stopped":
            run.worker_pid = None
            run.worker_state = "stopped"
            run.worker_operation = ""
            run.worker_operation_id = ""
        run.updated_at = str(payload.get("timestamp") or _utc_now())

    def _refresh_events_locked(
        self,
        run: ManagedRun,
        *,
        save: bool,
    ) -> bool:
        changed = False
        durable_changed = False
        while True:
            page = self._events.read(
                run.run_id,
                cursor=run.event_count,
                limit=1000,
            )
            for payload in page["events"]:
                self._apply_progress_locked(run, payload)
                if str(payload.get("type") or "") not in {
                    "agent_line",
                    "agent_tokens",
                }:
                    durable_changed = True
            if page["next_cursor"] != run.event_count:
                changed = True
                run.event_count = page["next_cursor"]
            if not page["has_more"]:
                break
        if self._revalidate_unchanged_synthesis_locked(run):
            changed = durable_changed = True
        if changed and save:
            self._save_state_locked()
        return changed and durable_changed

    def _revalidate_unchanged_synthesis_locked(self, run: ManagedRun) -> bool:
        if (not run.stale_synthesis_agents or run.finalized_agent is not None
            or run.status not in {"completed", "cancelled", "failed"}
            or self._worker_active_locked(run)):
            return False
        progress = self._progress_cache.get(run.run_id)
        if progress is None:
            progress = RunProgressProjection()
            self._progress_cache.put(run.run_id, progress)
        progress.refresh(self._events, run.run_id, run.event_count)
        sources = {idx: result_fingerprint(value) for idx, value in run.results.items()
            if value.get("status") == "success"
            and normalize_agent_role(value.get("role", run.agent_roles.get(idx))) == AGENT_ROLE_CANDIDATE}
        if not sources or progress.cursor != run.event_count or sources != progress.candidate_results:
            return False
        restored = [idx for idx in run.stale_synthesis_agents
            if (value := run.results.get(idx)) and value.get("status") == "success"
            and normalize_agent_role(value.get("role", run.agent_roles.get(idx))) == AGENT_ROLE_SYNTHESIS
            and progress.synthesis_sources.get(idx) == sources
            and progress.synthesis_generations.get(idx) == progress.candidate_generation
            and progress.synthesis_results.get(idx) == result_fingerprint(value)]
        if not restored:
            return False
        # A stopped expansion did not change the inputs or replace these results.
        # Preserve the original sessions; only retract the premature invalidation.
        run.stale_synthesis_agents = sorted(set(run.stale_synthesis_agents) - set(restored))
        self._append_event_locked(run, {"type": "synthesis_revalidated", "indices": restored,
            "source_agents": sorted(sources), "reason": "retained_inputs_and_results_unchanged"})
        return True

    def _record_progress(
        self,
        run_id: str,
        payload: Dict[str, Any],
    ) -> None:
        save = str(payload.get("type") or "") not in {"agent_line", "agent_tokens"}
        with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                return
            self._events.append(run_id, payload)
            self._refresh_events_locked(run, save=save)

    @staticmethod
    def _new_run_id() -> str:
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"pcr-{stamp}-{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _resolve_workspace(workspace: str) -> Path:
        path = Path(workspace or os.getcwd()).expanduser().resolve()
        if not path.exists() or not path.is_dir():
            raise PluginRunError(f"Workspace is not a directory: {path}")
        return path

    @staticmethod
    def _run_base(workspace: Path, runs_dir: Optional[str]) -> Path:
        try:
            return choose_run_base(workspace, runs_dir)
        except SystemExit as exc:
            raise PluginRunError(str(exc)) from exc

    def _reserved_storage_locked(self) -> int:
        return sum(
            run.estimated_bytes
            for run in self._runs.values()
            if not (run.workspaces_deleted and run.codex_homes_deleted)
        )

    def _cleanup_run_artifacts_locked(
        self,
        run: ManagedRun,
        *,
        preserve_diffs: bool,
    ) -> None:
        if not run.run_root:
            run.workspaces_deleted = True
            run.codex_homes_deleted = True
            return
        if preserve_diffs:
            try:
                self._artifacts.persist_successful_diffs(run)
            except ArtifactError as exc:
                raise PluginRunError(
                    f"Cannot preserve completed Agent diffs before cleanup: {exc}"
                ) from exc
        workspace = self._workspace_for_run(run)
        workspaces_root = self._workspaces_root_for_run(run)
        if workspaces_root.exists():
            cleanup_workspace_copies(workspace, workspaces_root)
        run.workspaces_deleted = not workspaces_root.exists()
        try:
            run.codex_homes_deleted = self._artifacts.remove_codex_homes(run)
        except ArtifactError as exc:
            raise PluginRunError(str(exc)) from exc

    def _validated_state_parent(self, parent: Path) -> Path:
        if parent.is_symlink():
            raise PluginRunError(f"Plugin state directory was replaced by a symlink: {parent}")
        resolved = parent.resolve()
        if resolved == self.state_dir or not is_relative_to(resolved, self.state_dir):
            raise PluginRunError(f"Refusing to clean an unverified state path: {parent}")
        return resolved

    def _remove_owned_state_path(
        self,
        path: Path,
        parent: Path,
        *,
        directory: bool = False,
    ) -> None:
        verified_parent = self._validated_state_parent(parent)
        if path.parent.resolve() != verified_parent:
            raise PluginRunError(f"Refusing to clean an unverified state path: {path}")
        if path.is_symlink():
            path.unlink()
            return
        if not path.exists():
            return
        if directory:
            if not path.is_dir():
                raise PluginRunError(f"Expected a plugin state directory: {path}")
            shutil.rmtree(path)
            return
        if not path.is_file():
            raise PluginRunError(f"Expected a plugin state file: {path}")
        path.unlink()

    def _purge_retained_artifacts_locked(self, run: ManagedRun) -> None:
        operation = "retained artifact cleanup"
        try:
            with RunOperationLock(self.locks_dir, run.run_id, operation):
                if not self._artifacts.remove_run_root(run):
                    raise PluginRunError(
                        f"Could not remove retained run artifacts for {run.run_id}"
                    )

                self._remove_owned_state_path(
                    self.control_dir / run.run_id,
                    self.control_dir,
                    directory=True,
                )
                workers_dir = self.state_dir / "workers"
                self._remove_owned_state_path(
                    workers_dir / f"{run.run_id}.json",
                    workers_dir,
                )
                requests_dir = workers_dir / "requests"
                self._validated_state_parent(requests_dir)
                if requests_dir.exists():
                    for request in requests_dir.glob(f"{run.run_id}-*.json"):
                        self._remove_owned_state_path(request, requests_dir)
                responses_dir = workers_dir / "responses"
                self._validated_state_parent(responses_dir)
                if responses_dir.exists():
                    for response in responses_dir.glob(f"{run.run_id}-*.json"):
                        self._remove_owned_state_path(response, responses_dir)
                self._events.delete(run.run_id)
        except RuntimeError as exc:
            raise PluginRunError(str(exc)) from exc

        self._remove_owned_state_path(
            self.locks_dir / f"{run.run_id}.lock",
            self.locks_dir,
        )
        self._contexts.pop(run.run_id, None)
        process = self._worker_processes.pop(run.run_id, None)
        if process is not None:
            process.poll()

    @property
    def retains_history(self) -> bool:
        return self._retain_history_path.exists()

    def _next_worker_deadline(self) -> str:
        return utc_deadline(self.run_ttl_seconds) if self.run_ttl_seconds > 0 else ""

    def _run_maintenance_locked(self) -> None:
        now = time.time()
        retain_history = self.retains_history
        changed = False
        removable: list[str] = []
        for run in list(self._runs.values()):
            if run.status in ACTIVE_RUN_STATES | {"finalizing"} or run.worker_pid:
                if self._refresh_events_locked(run, save=False):
                    changed = True
            if self._detect_worker_failure_locked(run):
                changed = True
            active = self._worker_active_locked(run)
            if not active and run.status == "completed":
                self._schedule_async_answers_locked(run)
            worker_alive = self._worker_alive_locked(run)
            deadline = deadline_timestamp(run.worker_deadline)
            if active and deadline is not None and now >= deadline:
                FileSignal(self.control_dir / run.run_id / "stop").set()
            if run.status in ACTIVE_RUN_STATES and not active:
                run.status = "interrupted"
                run.error = "The detached PCR worker is no longer running"
                run.worker_pid = None
                run.updated_at = _utc_now()
                changed = True
            if retain_history:
                if run.expires_at:
                    run.expires_at = ""
                    changed = True
                continue
            expires = deadline_timestamp(run.expires_at)
            if (
                expires is not None
                and now >= expires
                and not active
                and not worker_alive
                and not (run.workspaces_deleted and run.codex_homes_deleted)
            ):
                try:
                    self._cleanup_run_artifacts_locked(run, preserve_diffs=True)
                    if run.status not in FINAL_RUN_STATES:
                        run.status = "expired"
                    run.error = "" if run.status == "finalized" else run.error
                    run.updated_at = _utc_now()
                    changed = True
                except Exception as exc:  # noqa: BLE001
                    run.error = f"Expired artifact cleanup failed: {exc}"
                    changed = True
            if (
                expires is not None
                and now >= expires + self.artifact_retention_seconds
                and not active
                and not worker_alive
                and run.workspaces_deleted
                and run.codex_homes_deleted
                and run.status in FINAL_RUN_STATES | {"expired", "interrupted", "failed"}
            ):
                removable.append(run.run_id)
        removed: set[str] = set()
        for run_id in removable:
            run = self._runs.get(run_id)
            if run is None:
                continue
            try:
                self._purge_retained_artifacts_locked(run)
            except Exception as exc:  # noqa: BLE001
                run.error = f"Retained artifact cleanup failed: {exc}"
                run.updated_at = _utc_now()
                changed = True
                continue
            self._runs.pop(run_id, None)
            removed.add(run_id)
            changed = True
        if changed:
            self._save_state_locked(deleted_run_ids=removed)
        self._start_cleanup_worker_locked()

    def _start_cleanup_worker_locked(self) -> None:
        if (
            self._closed
            or not self._detached_workers
            or self._cleanup_thread is not None
            or not any(run.cleanup_pending for run in self._runs.values())
        ):
            return
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_worker_main,
            name="pcr-candidate-cleanup",
            daemon=True,
        )
        self._cleanup_thread.start()

    def _cleanup_worker_main(self) -> None:
        try:
            while not self._cleanup_stop.wait(CANDIDATE_CLEANUP_RETRY_SECONDS):
                if not self._retry_finalized_cleanups():
                    return
        finally:
            with self._lock:
                self._cleanup_thread = None
                self._start_cleanup_worker_locked()

    def _retry_finalized_cleanups(self) -> bool:
        with self._lock:
            if self._closed:
                return False
            self._sync_state_from_disk_locked()
            pending = [run.run_id for run in self._runs.values() if run.cleanup_pending]
        for run_id in pending:
            if self._cleanup_stop.is_set():
                break
            try:
                with RunOperationLock(self.locks_dir, run_id, "candidate cleanup"):
                    with self._lock:
                        run = self._require_run_locked(run_id)
                        if not run.cleanup_pending or self._worker_active_locked(run):
                            continue
                        snapshot = ManagedRun.from_dict(run.to_dict())
                    # Deleting large trees must not hold the state lock used by
                    # sends, polling and other workspaces. Never sync here.
                    error = ""
                    try:
                        self._cleanup_run_artifacts_locked(snapshot, preserve_diffs=False)
                    except Exception as exc:  # noqa: BLE001
                        error = str(exc)
                    with self._lock:
                        run = self._require_run_locked(run_id)
                        if not run.cleanup_pending:
                            continue
                        values = {
                            "workspaces_deleted": snapshot.workspaces_deleted,
                            "codex_homes_deleted": snapshot.codex_homes_deleted,
                            "cleanup_error": error,
                        }
                        if all(getattr(run, key) == value for key, value in values.items()):
                            continue
                        run.workspaces_deleted = snapshot.workspaces_deleted
                        run.codex_homes_deleted = snapshot.codex_homes_deleted
                        run.cleanup_error = error
                        run.updated_at = _utc_now()
                        self._append_event_locked(
                            run, {"type": "workspace_cleanup_updated", **values}
                        )
                        self._write_finalization_journal(
                            run,
                            phase="complete",
                            agent=run.finalized_agent,
                            synced_back=run.synced_back,
                            **values,
                        )
                        self._save_state_locked()
            except RuntimeError:
                # A foreground operation owns this run; retry on the next pass.
                continue
        with self._lock:
            return any(run.cleanup_pending for run in self._runs.values())

    def cleanup_expired_runs(self) -> Dict[str, Any]:
        with self._lock:
            before = set(self._runs)
            self._run_maintenance_locked()
            after = set(self._runs)
            return {
                "cleaned_records": sorted(before - after),
                "recorded_runs": len(after),
                "reserved_bytes": self._reserved_storage_locked(),
                "reserved": format_storage_bytes(self._reserved_storage_locked()),
                "quota_bytes": self.storage_quota_bytes,
                "quota": format_storage_bytes(self.storage_quota_bytes),
            }

    @staticmethod
    def _measure_run_storage(root: Path) -> tuple[int, int, int]:
        """Measure one run once, while classifying its removable subtrees."""
        try:
            root_stat = root.lstat()
        except OSError:
            return 0, 0, 0

        def allocated(stat_result: os.stat_result) -> int:
            blocks = int(getattr(stat_result, "st_blocks", 0) or 0) * 512
            return max(int(stat_result.st_size), blocks)

        actual = allocated(root_stat)
        workspaces = 0
        codex_homes = 0
        pending: list[tuple[Path, tuple[str, ...]]] = [(root, ())]
        while pending:
            directory, relative = pending.pop()
            try:
                entries = os.scandir(directory)
            except OSError:
                continue
            with entries:
                for entry in entries:
                    try:
                        stat_result = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    parts = (*relative, entry.name)
                    size = allocated(stat_result)
                    actual += size
                    if parts and parts[0] == "workspaces":
                        workspaces += size
                    if (
                        len(parts) >= 3
                        and parts[0] == "meta"
                        and re.fullmatch(r"agent_[0-9]+", parts[1]) is not None
                        and parts[2] == "codex_home"
                    ):
                        codex_homes += size
                    if entry.is_dir(follow_symlinks=False):
                        pending.append((Path(entry.path), parts))
        return actual, workspaces, codex_homes

    @staticmethod
    def _storage_root_signature(
        root: Path,
    ) -> tuple[tuple[int, int, int, int], ...]:
        signature: list[tuple[int, int, int, int]] = []
        for path in (root, root / "workspaces", root / "meta"):
            try:
                value = path.stat(follow_symlinks=False)
            except OSError:
                signature.append((0, 0, 0, 0))
                continue
            signature.append(
                (value.st_dev, value.st_ino, value.st_mtime_ns, value.st_size)
            )
        return tuple(signature)

    def _measure_run_storage_cached(
        self,
        run: ManagedRun,
        root: Path,
    ) -> tuple[int, int, int]:
        if run.status in ACTIVE_RUN_STATES | {"finalizing"}:
            return self._measure_run_storage(root)

        signature = self._storage_root_signature(root)
        key = (run.run_id, run.updated_at, signature)
        with self._storage_condition:
            while key in self._storage_inflight:
                self._storage_condition.wait()
            cached = self._storage_measurements.get(run.run_id)
            if (
                cached is not None
                and cached.updated_at == run.updated_at
                and cached.root_signature == signature
            ):
                return (
                    cached.actual_bytes,
                    cached.workspace_bytes,
                    cached.codex_home_bytes,
                )
            self._storage_inflight.add(key)

        try:
            measured = self._measure_run_storage(root)
            cached = _StorageMeasurement(
                updated_at=run.updated_at,
                root_signature=signature,
                actual_bytes=measured[0],
                workspace_bytes=measured[1],
                codex_home_bytes=measured[2],
            )
            with self._storage_condition:
                self._storage_measurements[run.run_id] = cached
            return measured
        finally:
            with self._storage_condition:
                self._storage_inflight.discard(key)
                self._storage_condition.notify_all()

    def _measure_unmanaged_storage_cached(self, root: Path) -> int:
        signature = self._storage_root_signature(root)[0]
        key = str(root)
        now = time.monotonic()
        with self._storage_condition:
            cached = self._unmanaged_storage_measurements.get(key)
            if (
                cached is not None
                and cached[0] == signature
                and now - cached[1] < UNMANAGED_STORAGE_CACHE_SECONDS
            ):
                return cached[2]
        measured = estimate_path_storage_bytes(root)
        with self._storage_condition:
            self._unmanaged_storage_measurements[key] = (
                signature,
                time.monotonic(),
                measured,
            )
        return measured

    def storage_summary(self, limit: int = 50) -> Dict[str, Any]:
        """Measure PCR-owned run artifacts without trusting arbitrary run paths."""
        limit = max(1, min(int(limit), MAX_STORAGE_SUMMARY_RUNS))
        with self._lock:
            self._run_maintenance_locked()
            snapshots = [
                ManagedRun.from_dict(run.to_dict()) for run in self._runs.values()
            ]

        entries: list[Dict[str, Any]] = []
        managed_roots: set[Path] = set()
        run_bases: set[Path] = set()
        managed_bytes = 0
        reclaimable_bytes = 0
        for run in snapshots:
            configured_base = str(run.config.get("run_base") or "").strip()
            if configured_base:
                run_bases.add(Path(configured_base).expanduser().resolve())
            try:
                root = self._artifacts.run_root(run)
            except ArtifactError:
                continue
            managed_roots.add(root)
            actual_bytes, workspace_bytes, codex_home_bytes = (
                self._measure_run_storage_cached(run, root)
            )
            candidate_bytes = workspace_bytes + codex_home_bytes
            cleanup_action: str | None = None
            if run.status in FINAL_RUN_STATES | {"expired"}:
                cleanup_action = "cleanup" if candidate_bytes > 0 else None
            elif (
                run.status not in ACTIVE_RUN_STATES | {"finalizing", "sync_ambiguous"}
                and run.finalized_agent is None
                and candidate_bytes > 0
            ):
                cleanup_action = "discard"
            entry_reclaimable = candidate_bytes if cleanup_action else 0
            managed_bytes += actual_bytes
            reclaimable_bytes += entry_reclaimable
            entries.append(
                {
                    "run_id": run.run_id,
                    "status": run.status,
                    "workspace": run.workspace,
                    "workspace_name": Path(run.workspace).name or run.workspace,
                    "created_at": run.created_at,
                    "updated_at": run.updated_at,
                    "actual_bytes": actual_bytes,
                    "actual": format_storage_bytes(actual_bytes),
                    "reclaimable_bytes": entry_reclaimable,
                    "reclaimable": format_storage_bytes(entry_reclaimable),
                    "cleanup_action": cleanup_action,
                    "finalized_agent": run.finalized_agent,
                    "keep_workspaces": bool(run.config.get("keep_workspaces", False)),
                }
            )

        unmanaged_bytes = 0
        unmanaged_count = 0
        unmanaged_roots: set[Path] = set()
        for base in run_bases:
            if base.is_symlink() or not base.is_dir():
                continue
            try:
                children = list(base.iterdir())
            except OSError:
                continue
            for child in children:
                if (
                    child in managed_roots
                    or child.is_symlink()
                    or not child.is_dir()
                    or re.fullmatch(r"[0-9]{8}_[0-9]{6}(?:_[0-9]{3})?", child.name)
                    is None
                ):
                    continue
                unmanaged_count += 1
                unmanaged_roots.add(child)
                unmanaged_bytes += self._measure_unmanaged_storage_cached(child)

        entries.sort(
            key=lambda item: (
                int(item["reclaimable_bytes"]),
                int(item["actual_bytes"]),
                str(item["updated_at"]),
            ),
            reverse=True,
        )
        with self._storage_condition:
            current_run_ids = {run.run_id for run in snapshots}
            self._storage_measurements = {
                run_id: value
                for run_id, value in self._storage_measurements.items()
                if run_id in current_run_ids
            }
            current_unmanaged_roots = {str(child) for child in unmanaged_roots}
            self._unmanaged_storage_measurements = {
                path: value
                for path, value in self._unmanaged_storage_measurements.items()
                if path in current_unmanaged_roots
            }
        visible = entries[:limit]
        free_bytes, volume_count = _storage_free_space_summary(run_bases)
        return {
            "managed_bytes": managed_bytes,
            "managed": format_storage_bytes(managed_bytes),
            "reclaimable_bytes": reclaimable_bytes,
            "reclaimable": format_storage_bytes(reclaimable_bytes),
            "unmanaged_bytes": unmanaged_bytes,
            "unmanaged": format_storage_bytes(unmanaged_bytes),
            "unmanaged_count": unmanaged_count,
            "free_bytes": free_bytes,
            "free": format_storage_bytes(free_bytes),
            "volume_count": volume_count,
            "runs": visible,
            "total_runs": len(entries),
            "limit": limit,
        }

    def cleanup_run_storage(self, run_id: str) -> Dict[str, Any]:
        """Remove retained candidates from a finalized or discarded run."""
        operation = "retained storage cleanup"
        try:
            with RunOperationLock(self.locks_dir, run_id, operation):
                with self._lock:
                    run = self._require_run_locked(run_id)
                    self._refresh_events_locked(run, save=False)
                    if self._worker_active_locked(run) or self._worker_alive_locked(run):
                        raise PluginRunError(
                            "The run worker is still active; stop or discard the run first"
                        )
                    if run.status not in FINAL_RUN_STATES | {"expired"}:
                        raise PluginRunError(
                            "Only finalized, discarded, or expired runs can release "
                            "retained storage without discarding their result"
                        )
                    self._claim_run_operation_locked(run_id, operation)
                    try:
                        self._cleanup_run_artifacts_locked(run, preserve_diffs=True)
                        run.updated_at = _utc_now()
                        run.error = ""
                        self._append_event_locked(
                            run,
                            {
                                "type": "retained_storage_cleaned",
                                "workspaces_deleted": run.workspaces_deleted,
                                "codex_homes_deleted": run.codex_homes_deleted,
                            },
                        )
                        self._save_state_locked()
                        return self._run_summary_locked(run)
                    finally:
                        self._release_run_operation_locked(run_id, operation)
        except RuntimeError as exc:
            raise PluginRunError(str(exc)) from exc

    def _finalization_journal_path(self, run: ManagedRun) -> Path:
        return self._validated_run_root(run) / "plugin" / "finalization.json"

    def _write_finalization_journal(
        self,
        run: ManagedRun,
        *,
        phase: str,
        agent: int,
        **values: Any,
    ) -> None:
        path = self._finalization_journal_path(run)
        payload = read_json(path)
        payload.update(
            {
                "version": 1,
                "run_id": run.run_id,
                "agent": int(agent),
                "phase": phase,
                "updated_at": _utc_now(),
                **values,
            }
        )
        write_json_atomic(path, payload)

    def _recover_finalization_locked(self, run: ManagedRun) -> None:
        if not run.run_root:
            return
        try:
            path = self._finalization_journal_path(run)
        except PluginRunError:
            return
        journal = read_json(path)
        if not journal:
            return
        phase = str(journal.get("phase") or "")
        agent = _safe_int(journal.get("agent"))
        if agent <= 0:
            return
        if phase == "sync_started":
            run.finalized_agent = agent
            run.status = "sync_ambiguous"
            run.error = (
                "Sync-back started but its completion was not recorded. Inspect the "
                "workspace, then call pcr_recover_finalization with sync_was_applied."
            )
        elif phase == "sync_complete":
            run.finalized_agent = agent
            run.synced_back = bool(journal.get("synced_back", True))
            run.status = "cleanup_failed"
            run.error = "Sync-back is recorded; retry candidate cleanup without syncing again."
        elif phase == "cleanup_started":
            run.finalized_agent = agent
            run.synced_back = bool(journal.get("synced_back", True))
            run.cleanup_error = run.cleanup_error or run.error
            run.status = "finalized"
            run.error = ""
        elif phase == "complete":
            run.finalized_agent = agent
            run.synced_back = bool(journal.get("synced_back"))
            run.workspaces_deleted = bool(journal.get("workspaces_deleted"))
            run.codex_homes_deleted = bool(journal.get("codex_homes_deleted"))
            run.cleanup_error = str(journal.get("cleanup_error") or "")
            run.status = "finalized"
            run.error = ""

    def _workspace_for_run(self, run: ManagedRun) -> Path:
        try:
            return self._artifacts.workspace(run)
        except ArtifactError as exc:
            raise PluginRunError(str(exc)) from exc

    def _validated_run_root(
        self,
        run: ManagedRun,
        *,
        require_marker: bool = True,
    ) -> Path:
        try:
            return self._artifacts.run_root(run, require_marker=require_marker)
        except ArtifactError as exc:
            raise PluginRunError(str(exc)) from exc

    def _write_run_marker_locked(self, run: ManagedRun) -> None:
        try:
            self._artifacts.write_marker(run)
        except ArtifactError as exc:
            raise PluginRunError(str(exc)) from exc

    def _agent_workspace_for_run(
        self,
        run: ManagedRun,
        agent: int,
        recorded_path: Optional[str] = None,
    ) -> Path:
        try:
            return self._artifacts.agent_workspace(run, agent, recorded_path)
        except ArtifactError as exc:
            raise PluginRunError(str(exc)) from exc

    def _require_agent_workspace_for_action(
        self,
        run: ManagedRun,
        agent: int,
        recorded_path: Optional[str] = None,
    ) -> Path:
        try:
            candidate = self._agent_workspace_for_run(run, agent, recorded_path)
        except PluginRunError as exc:
            raise PluginRunError(
                f"AGENT-{agent:03d} can still be reviewed, but its working copy "
                "is no longer available. Start a new run for the next prompt."
            ) from exc
        if not candidate.is_dir():
            raise PluginRunError(
                f"AGENT-{agent:03d} can still be reviewed, but its working copy "
                "has already been cleaned up. Start a new run for the next prompt."
            )
        return candidate

    def _agent_meta_file(
        self,
        run: ManagedRun,
        agent: int,
        name: str,
    ) -> Path:
        try:
            return self._artifacts.agent_meta_file(run, agent, name)
        except ArtifactError as exc:
            raise PluginRunError(str(exc)) from exc

    def estimate(
        self,
        workspace: str,
        num_agents: int = DEFAULT_NUM_AGENTS,
        runs_dir: Optional[str] = None,
        resume_session_id: Optional[str] = None,
        synthesis_agents: int = 0,
        *,
        resume_codex_home: Optional[Path] = None,
    ) -> Dict[str, Any]:
        source = self._resolve_workspace(workspace)
        _validated_agent_count(num_agents, "num_agents")
        _validated_agent_count(
            synthesis_agents,
            "synthesis_agents",
            allow_zero=True,
        )
        _validate_run_agent_total(num_agents, synthesis_agents)
        if synthesis_agents:
            estimate = estimate_staged_run_storage(
                source,
                num_agents,
                synthesis_agents,
                resume_session_id=resume_session_id,
                codex_home=get_codex_home(),
                resume_codex_home=resume_codex_home,
            )
        else:
            estimate = estimate_run_storage(
                source,
                num_agents,
                resume_session_id=resume_session_id,
                codex_home=get_codex_home(),
                resume_codex_home=resume_codex_home,
            )
        run_base = self._run_base(source, runs_dir)
        free_bytes = available_storage_bytes(run_base)
        return {
            "workspace": str(source),
            "run_base": str(run_base),
            "num_agents": num_agents,
            "synthesis_agents": synthesis_agents,
            "workspace_bytes_per_agent": estimate.workspace_bytes_per_agent,
            "workspace_copies_bytes": estimate.workspace_copies_bytes,
            "metadata_bytes_per_agent": estimate.metadata_bytes_per_agent,
            "metadata_bytes": estimate.metadata_bytes,
            "total_bytes": estimate.total_bytes,
            "total": format_storage_bytes(estimate.total_bytes),
            "free_bytes": free_bytes,
            "free": format_storage_bytes(free_bytes),
            "fits": estimate.total_bytes <= free_bytes,
            "confirmation_required": (
                estimate.total_bytes > LARGE_RUN_STORAGE_WARNING_BYTES
            ),
            "warning_threshold_bytes": LARGE_RUN_STORAGE_WARNING_BYTES,
            "warning_threshold": format_storage_bytes(
                LARGE_RUN_STORAGE_WARNING_BYTES
            ),
        }

    @staticmethod
    def _build_args(
        run: ManagedRun,
        context: _RunContext,
        num_agents: Optional[int] = None,
    ) -> argparse.Namespace:
        config = run.config
        return argparse.Namespace(
            prompt=run.prompt,
            prompt_file=None,
            num_agents=num_agents or int(config["num_agents"]),
            max_parallel=config.get("max_parallel"),
            serial=bool(config.get("serial")),
            recommend_by=str(config.get("recommend_by") or "reasoning_tokens"),
            workspace=run.workspace,
            runs_dir=config.get("runs_dir"),
            codex_bin=str(config.get("codex_bin") or "codex"),
            model=config.get("model"),
            effort=config.get("effort"),
            fast=config.get("fast"),
            synthesis_agents=int(config.get("synthesis_agents") or 0),
            synthesis_model=config.get("synthesis_model"),
            synthesis_effort=config.get("synthesis_effort"),
            synthesis_fast=config.get("synthesis_fast"),
            follow_up_delay=int(
                config.get("follow_up_delay")
                if config.get("follow_up_delay") is not None
                else DEFAULT_FOLLOW_UP_DELAY_SECONDS
            ),
            subagents=bool(config.get("subagents", False)),
            subagents_limit=int(
                config.get("subagents_limit") or DEFAULT_SUBAGENTS_LIMIT
            ),
            resume=False,
            resume_session_id=config.get("resume_session_id"),
            resume_include_non_interactive=False,
            # The plugin owns review, finalization, and cleanup.
            no_sync_back=True,
            keep_workspaces=True,
            workspace_mode=config.get("workspace_mode", "isolated"),
            cancel_event=context.cancel_event,
            agent_cancel_events=context.agent_cancel_events,
            _pcr_explicit_tui_settings=set(),
        )

    def start_run(
        self,
        prompt: str,
        workspace: str = "",
        num_agents: int = DEFAULT_NUM_AGENTS,
        max_parallel: Optional[int] = None,
        serial: bool = False,
        recommend_by: str = "reasoning_tokens",
        model: Optional[str] = None,
        effort: Optional[str] = None,
        resume_session_id: Optional[str] = None,
        runs_dir: Optional[str] = None,
        codex_bin: str = "codex",
        sync_back: bool = True,
        keep_workspaces: bool = False,
        confirm_large_run: bool = False,
        fast: Optional[bool] = None,
        synthesis_agents: int = 0,
        follow_up_delay: int = DEFAULT_FOLLOW_UP_DELAY_SECONDS,
        synthesis_model: Optional[str] = None,
        synthesis_effort: Optional[str] = None,
        synthesis_fast: Optional[bool] = None,
        subagents: bool = False,
        subagents_limit: int = DEFAULT_SUBAGENTS_LIMIT,
        conversation_id: Optional[str] = None,
        turn_id: Optional[str] = None,
        continued_from_run_id: Optional[str] = None,
        continued_from_agent: Optional[int] = None,
        turn_index: int = 0,
        submission_command: Optional[Dict[str, str]] = None,
        supports_agent_questions: bool = False,
    ) -> Dict[str, Any]:
        prompt = prompt.strip()
        if not prompt:
            raise PluginRunError("Prompt must not be empty")
        source = self._resolve_workspace(workspace)
        _validated_agent_count(num_agents, "num_agents")
        if max_parallel is not None and max_parallel <= 0:
            raise PluginRunError("max_parallel must be greater than zero")
        _validated_agent_count(
            synthesis_agents,
            "synthesis_agents",
            allow_zero=True,
        )
        _validate_run_agent_total(num_agents, synthesis_agents)
        if isinstance(follow_up_delay, bool) or follow_up_delay < 0:
            raise PluginRunError("follow_up_delay must not be negative")
        if subagents_limit <= 0:
            raise PluginRunError("subagents_limit must be greater than zero")
        if fast is not None and not isinstance(fast, bool):
            raise PluginRunError("fast must be true, false, or null")
        if synthesis_fast is not None and not isinstance(synthesis_fast, bool):
            raise PluginRunError("synthesis_fast must be true, false, or null")
        if not isinstance(supports_agent_questions, bool):
            raise PluginRunError("supports_agent_questions must be true or false")
        try:
            recommend_by = normalize_recommend_by(recommend_by)
        except (ValueError, argparse.ArgumentTypeError) as exc:
            raise PluginRunError(str(exc)) from exc

        with self._lock:
            if self._closed:
                raise PluginRunError("PCR plugin runtime is shutting down")
            self._sync_state_from_disk_locked()
            self._run_maintenance_locked()

        storage = self.estimate(
            str(source),
            num_agents,
            runs_dir,
            resume_session_id,
            synthesis_agents,
        )
        if not storage["fits"]:
            raise PluginRunError(
                "Insufficient disk space: "
                f"need {storage['total']}, available {storage['free']}"
            )
        if storage["confirmation_required"] and not confirm_large_run:
            return {
                "status": "confirmation_required",
                "message": (
                    f"This run is estimated to use {storage['total']}. "
                    "Ask the user to confirm, then call start again with "
                    "confirm_large_run=true."
                ),
                "storage": storage,
            }
        with self._lock:
            reserved = self._reserved_storage_locked()
            projected = reserved + int(storage["total_bytes"])
            if self.enforce_storage_quota and projected > self.storage_quota_bytes:
                raise PluginRunError(
                    "PCR plugin storage quota exceeded: "
                    f"{format_storage_bytes(projected)} projected, "
                    f"{format_storage_bytes(self.storage_quota_bytes)} allowed. "
                    "Accept, discard, or clean older runs before starting another."
                )

        config: Dict[str, Any] = {
            "num_agents": int(num_agents),
            "workspace_mode": execution_workspace_mode(
                num_agents, synthesis_agents, sync_back, keep_workspaces
            ),
            "max_parallel": int(max_parallel) if max_parallel is not None else None,
            "serial": bool(serial),
            "recommend_by": recommend_by,
            "model": model or None,
            "effort": effort or None,
            "fast": fast,
            "synthesis_agents": int(synthesis_agents),
            "synthesis_model": synthesis_model or None,
            "synthesis_effort": synthesis_effort or None,
            "synthesis_fast": synthesis_fast,
            "follow_up_delay": int(follow_up_delay),
            "subagents": bool(subagents),
            "subagents_limit": int(subagents_limit),
            "resume_session_id": resume_session_id or None,
            "resume_rollout_line_count": _resume_rollout_line_count(
                resume_session_id
            ),
            "runs_dir": (
                str(Path(runs_dir).expanduser().resolve()) if runs_dir else None
            ),
            "codex_bin": codex_bin,
            "sync_back": bool(sync_back),
            "keep_workspaces": bool(keep_workspaces),
            "run_base": str(storage["run_base"]),
        }
        if submission_command is not None:
            config["submission_command"] = dict(submission_command)
        if supports_agent_questions:
            config["supports_agent_questions"] = True
        run_id = self._new_run_id()
        run = ManagedRun(
            run_id=run_id,
            prompt=prompt,
            workspace=str(source),
            config=config,
            turn_id=str(turn_id or f"turn-{uuid.uuid4().hex}"),
            conversation_id=str(conversation_id or resume_session_id or run_id),
            continued_from_run_id=str(continued_from_run_id or ""),
            continued_from_agent=(
                int(continued_from_agent)
                if continued_from_agent is not None
                else None
            ),
            turn_index=max(0, int(turn_index)),
            agent_statuses={idx: "queued" for idx in range(1, num_agents + 1)},
            agent_roles={
                idx: AGENT_ROLE_CANDIDATE for idx in range(1, num_agents + 1)
            },
            agent_tokens={idx: None for idx in range(1, num_agents + 1)},
            artifact_token=uuid.uuid4().hex,
            worker_protocol=(
                WORKER_PROTOCOL_VERSION if self._detached_workers else 0
            ),
            worker_state="starting" if self._detached_workers else "",
            worker_operation="initial",
            worker_operation_id=(
                new_worker_operation_id(run_id) if self._detached_workers else ""
            ),
            worker_started_at=_utc_now(),
            worker_deadline=self._next_worker_deadline(),
            estimated_bytes=int(storage["total_bytes"]),
            expires_at="" if self.retains_history else utc_deadline(self.artifact_retention_seconds),
        )
        with self._lock:
            if self._closed:
                raise PluginRunError("PCR plugin runtime is shutting down")
            if (
                conversation_id is None
                and continued_from_run_id is None
                and resume_session_id
            ):
                lineage = self._resume_lineage_locked(
                    str(source),
                    resume_session_id,
                )
                if lineage:
                    previous, lineage_size = lineage
                    run.conversation_id = (
                        previous.conversation_id
                        or str(
                            previous.config.get("resume_session_id") or ""
                        ).strip()
                        or resume_session_id
                    )
                    run.continued_from_run_id = previous.run_id
                    run.continued_from_agent = previous.finalized_agent
                    run.turn_index = max(
                        max(0, int(previous.turn_index)) + 1,
                        lineage_size,
                    )
            context = _RunContext(
                cancel_event=threading.Event(),
                agent_cancel_events={
                    idx: threading.Event() for idx in range(1, num_agents + 1)
                },
            )
            args = self._build_args(run, context)
            try:
                validate_args(args)
            except SystemExit as exc:
                raise PluginRunError(str(exc)) from exc
            self._runs[run_id] = run
            try:
                self._append_event_locked(
                    run,
                    {
                        "type": "plugin_run_created",
                        "storage": storage,
                    },
                )
                self._save_state_locked()
            except Exception:
                self._runs.pop(run_id, None)
                self._events.delete(run_id)
                raise
            if self._detached_workers:
                try:
                    initialize_candidate_requests(
                        worker_candidate_requests_path(self.state_dir, run_id)
                    )
                    process, operation_id = spawn_worker(
                        self.state_dir,
                        run_id,
                        "initial",
                        {"run": run.to_dict(), "indices": []},
                        operation_id=run.worker_operation_id,
                    )
                except Exception as exc:
                    run.status = "failed"
                    run.worker_state = "crashed"
                    run.worker_operation = ""
                    run.worker_operation_id = ""
                    run.worker_error = f"Could not start persistent PCR worker: {exc}"
                    run.error = run.worker_error
                    run.updated_at = _utc_now()
                    self._save_state_locked()
                    raise PluginRunError(run.worker_error) from exc
                run.worker_pid = process.pid
                run.worker_state = "busy"
                run.worker_operation_id = operation_id
                self._worker_processes[run_id] = process
                self._save_state_locked()
            else:
                self._contexts[run_id] = context

                def target() -> None:
                    try:
                        run_once(
                            args,
                            prompt,
                            progress_callback=lambda payload: self._record_progress(
                                run_id, payload
                            ),
                            print_output=False,
                        )
                    except BaseException as exc:
                        self._record_progress(
                            run_id,
                            {"type": "run_failed", "message": str(exc)},
                        )
                    finally:
                        with self._lock:
                            self._finish_background_thread_locked(run_id)

                thread = threading.Thread(
                    target=target,
                    name=f"pcr-plugin-{run_id}",
                    daemon=True,
                )
                context.thread = thread
                thread.start()
        return {
            "status": "started",
            "run_id": run_id,
            "storage": storage,
            "run": self.get_run(run_id),
        }

    def _resume_lineage_locked(
        self,
        workspace: str,
        session_id: str,
    ) -> Optional[tuple[ManagedRun, int]]:
        normalized_session = str(session_id or "").strip()
        if not normalized_session:
            return None

        matches: list[ManagedRun] = []
        for candidate in self._runs.values():
            if candidate.workspace != workspace:
                continue
            if candidate.finalized_agent is None or not candidate.synced_back:
                continue
            promotion = (
                candidate.session_promotion
                if isinstance(candidate.session_promotion, dict)
                else {}
            )
            promoted_session = str(promotion.get("session_id") or "").strip()
            configured_session = str(
                candidate.config.get("resume_session_id") or ""
            ).strip()
            if normalized_session not in {promoted_session, configured_session}:
                continue
            matches.append(candidate)

        if not matches:
            return None
        matches.sort(key=lambda item: (item.created_at, item.run_id))
        return matches[-1], len(matches)

    def _agent_summary_locked(
        self,
        run: ManagedRun,
        idx: int,
    ) -> Dict[str, Any]:
        result = run.results.get(idx) or {}
        token_value = result.get("reasoning_tokens", run.agent_tokens.get(idx))
        role = normalize_agent_role(
            result.get("role", run.agent_roles.get(idx))
        )
        status = str(
            result.get("status")
            or run.agent_statuses.get(idx)
            or "unknown"
        )
        workspace_available = self._agent_workspace_available_locked(run, idx)
        retry_workspace_available = self._workspaces_root_available_locked(run)
        worker_available = (
            not self._uses_persistent_worker(run)
            or self._worker_available_locked(run)
        )
        idle_expiry_recovery = self._idle_expiry_recovery_available_locked(run)
        candidate_mutations_available = (
            not self._worker_active_locked(run)
            or self._live_candidate_stage_accepting_locked(run)
        )
        run_open = run.finalized_agent is None and run.status not in FINAL_RUN_STATES
        can_accept = (
            status == "success"
            and run_open
            and idx not in run.stale_synthesis_agents
            and workspace_available
            and (worker_available or self._completed_result_recovery_available_locked(run))
        )
        promoted_session = ""
        if isinstance(run.session_promotion, dict):
            promoted_session = str(
                run.session_promotion.get("session_id") or ""
            ).strip()
        can_continue_finalized = (
            status == "success"
            and run.status == "finalized"
            and idx == run.finalized_agent
            and bool(run.synced_back)
            and bool(promoted_session)
            and Path(run.workspace).expanduser().is_dir()
        )
        timing = self._progress_cache.get(run.run_id)
        # Completed results already carry the authoritative measured duration.
        # Only running or incomplete summaries need the event-derived clock.
        if timing is None and (
            status not in TERMINAL_AGENT_STATUSES or result.get("seconds") is None
        ):
            timing = RunProgressProjection()
            self._progress_cache.put(run.run_id, timing)
        if timing is not None:
            timing.refresh(self._events, run.run_id, run.event_count)
        started_at = timing.started.get(idx) if timing is not None else None
        finished_at = timing.finished.get(idx) if timing is not None else None
        if status in {"queued", "copying", "starting", "resuming", "submitting"}:
            started_at = finished_at = None
        activity = timing.activities[idx].snapshot() if timing is not None and idx in timing.activities else None
        if activity is not None and status in TERMINAL_AGENT_STATUSES:
            activity.update(state="finished", wait_until=None, active_operation_count=0)
        questions = self._questions_if_present(run, idx)
        return {
            "agent": idx,
            "pending_question_count": questions.pending_count() if questions is not None and not self._question_stopped_locked(run, idx) else 0,
            "blocking_question_count": questions.blocking_count() if questions is not None and not self._question_stopped_locked(run, idx) else 0,
            "name": f"AGENT-{idx:03d}",
            "role": role,
            "status": status,
            "seconds": result.get("seconds"),
            "phase": timing.phases.get(idx) if timing is not None else None,
            "activity": activity,
            "phase_timings": result.get("phase_timings") or (timing.phase_timings.get(idx, {}) if timing is not None else {}),
            "started_at": started_at,
            "finished_at": finished_at,
            "reasoning_tokens": token_value,
            "reasoning_token_counts": result.get("reasoning_token_counts")
            or (timing.token_counts.get(idx, {}) if timing is not None else {}),
            "codex_thread_id": result.get("codex_thread_id"),
            "error": result.get("error"),
            "rejected": idx in set(run.rejected_agents),
            "stale": idx in set(run.stale_synthesis_agents),
            "recommended": idx == run.recommended_agent,
            "finalized": idx == run.finalized_agent,
            "workspace_available": workspace_available,
            "can_accept": can_accept,
            "can_continue": (
                can_accept or can_continue_finalized
            ) and bool(run.config.get("sync_back", True)),
            "workspace_dir": str(result.get("workspace_dir") or "").strip() or None,
            "idle_expiry_recovery": idle_expiry_recovery,
            "can_retry": (
                status
                in {"failed", "error", "killed", "cancelled", "interrupted"}
                and run_open
                and retry_workspace_available
                and worker_available
                and candidate_mutations_available
            ),
        }

    @staticmethod
    def _run_root_available_locked(run: ManagedRun) -> bool:
        if not run.run_root:
            return False
        root = Path(run.run_root).expanduser()
        return not root.is_symlink() and root.is_dir()

    def _workspaces_root_available_locked(self, run: ManagedRun) -> bool:
        if run.workspaces_deleted or not self._run_root_available_locked(run):
            return False
        root = Path(run.run_root).expanduser() / "workspaces"
        return not root.is_symlink() and root.is_dir()

    def _agent_workspace_available_locked(self, run: ManagedRun, idx: int) -> bool:
        if not self._workspaces_root_available_locked(run):
            return False
        result = run.results.get(idx) or {}
        recorded = str(result.get("workspace_dir") or "").strip()
        candidate = (
            Path(recorded).expanduser()
            if recorded
            else Path(run.run_root).expanduser() / "workspaces" / f"agent_{idx:03d}"
        )
        return not candidate.is_symlink() and candidate.is_dir()

    def _accepted_workspace_mapping_locked(
        self,
        run: ManagedRun,
        *,
        agent: int | None = None,
    ) -> WorkspacePathMapping | None:
        finalized = run.finalized_agent
        if (
            not run.synced_back
            or finalized is None
            or (agent is not None and finalized != agent)
        ):
            return None
        result = run.results.get(finalized) or {}
        recorded = str(result.get("workspace_dir") or "").strip()
        if recorded:
            source = Path(recorded).expanduser()
        elif run.run_root:
            source = (
                Path(run.run_root).expanduser()
                / "workspaces"
                / f"agent_{finalized:03d}"
            )
        else:
            return None
        target = Path(run.workspace).expanduser()
        mapping = WorkspacePathMapping.create(source, target)
        return mapping if mapping.target_root.is_dir() else None

    def _workspace_path_mappings_locked(
        self,
        workspace: Path,
    ) -> tuple[WorkspacePathMapping, ...]:
        target = WorkspacePathMapping.create(workspace, workspace).target_root
        mappings: list[WorkspacePathMapping] = []
        seen_sources: set[Path] = set()
        for run in sorted(
            self._runs.values(),
            key=lambda item: item.updated_at,
            reverse=True,
        ):
            if WorkspacePathMapping.create(run.workspace, run.workspace).target_root != target:
                continue
            mapping = self._accepted_workspace_mapping_locked(run)
            if mapping is None or mapping.source_root in seen_sources:
                continue
            seen_sources.add(mapping.source_root)
            mappings.append(mapping)
        return tuple(mappings)

    def rewrite_agent_visible_text(
        self,
        run_id: str,
        agent: int,
        text: str,
    ) -> str:
        return self.rewrite_agent_visible_texts(run_id, agent, [text])[0]

    def rewrite_agent_visible_texts(
        self,
        run_id: str,
        agent: int,
        texts: Sequence[str],
    ) -> list[str]:
        with self._lock:
            run = self._require_run_locked(run_id)
            mapping = self._accepted_workspace_mapping_locked(run, agent=int(agent))
        mappings = (mapping,) if mapping is not None else ()
        return [rewrite_workspace_references(text, mappings) for text in texts]

    def _live_candidate_stage_accepting_locked(self, run: ManagedRun) -> bool:
        return bool(
            self._detached_workers
            and self._worker_active_locked(run)
            and run.worker_operation == "initial"
            and candidate_requests_accepting(
                worker_candidate_requests_path(self.state_dir, run.run_id)
            )
        )

    def _synthesis_settings_mutable_locked(self, run: ManagedRun) -> bool:
        if run.config.get("workspace_mode") == "source":
            return False
        if not (
            self._detached_workers
            and self._worker_active_locked(run)
            and run.worker_operation == "initial"
            and self._worker_available_locked(run)
        ):
            return False
        path = worker_synthesis_config_path(self.state_dir, run.run_id)
        with locked_synthesis_config(path):
            return path.is_file() and not bool(read_json(path).get("locked"))

    def _run_summary_locked(self, run: ManagedRun) -> Dict[str, Any]:
        indices = sorted(
            set(run.agent_statuses)
            | set(run.results)
            | set(run.agent_tokens)
        )
        artifacts_available = self._run_root_available_locked(run)
        workspaces_available = self._workspaces_root_available_locked(run)
        worker_available = self._worker_available_locked(run)
        idle_expiry_recovery = self._idle_expiry_recovery_available_locked(run)
        worker_active = self._worker_active_locked(run)
        candidate_mutations_available = (
            not worker_active or self._live_candidate_stage_accepting_locked(run)
        )
        can_add_agents = (
            run.finalized_agent is None
            and run.config.get("workspace_mode") != "source"
            and run.status not in (FINAL_RUN_STATES | {"finalizing", "expired"})
            and workspaces_available
            and candidate_mutations_available
            and (
                not self._uses_persistent_worker(run)
                or worker_available
            )
        )
        can_update_synthesis = self._synthesis_settings_mutable_locked(run)
        promotion = run.session_promotion if isinstance(run.session_promotion, dict) else {}
        conversation_id = (
            run.conversation_id
            or str(run.config.get("resume_session_id") or "").strip()
            or str(promotion.get("session_id") or "").strip()
            or run.run_id
        )
        agents = [self._agent_summary_locked(run, idx) for idx in indices]
        eligible = {agent["agent"] for agent in agents if agent.get("can_continue")}
        continuation = select_best_result(
            [candidate for candidate in self._recommendation_candidates_locked(run)
             if candidate.idx in eligible],
            "reasoning_tokens",
            warn_missing_tokens=False,
        )
        timing = self._progress_cache.get(run.run_id)
        return {
            "run_id": run.run_id,
            "status": run.status,
            "workspace": run.workspace,
            "prompt": run.prompt,
            "turn_id": run.turn_id or f"turn-{run.run_id}",
            "conversation_id": conversation_id,
            "continued_from_run_id": run.continued_from_run_id or None,
            "continued_from_agent": run.continued_from_agent,
            "turn_index": max(0, int(run.turn_index)),
            "created_at": run.created_at,
            "updated_at": run.updated_at,
            "run_root": run.run_root or None,
            "config": dict(run.config),
            "recommended_agent": run.recommended_agent,
            "continuation_agent": continuation.idx if continuation is not None else None,
            "finalized_agent": run.finalized_agent,
            "finalizing_agent": run.finalizing_agent,
            "synced_back": run.synced_back,
            "workspaces_deleted": run.workspaces_deleted,
            "codex_homes_deleted": run.codex_homes_deleted,
            "cleanup_pending": run.cleanup_pending,
            "cleanup_error": run.cleanup_error or None,
            "artifacts_available": artifacts_available,
            "candidate_workspaces_available": workspaces_available,
            "can_add_agents": can_add_agents,
            "can_update_synthesis": can_update_synthesis,
            "worker_active": worker_active,
            "worker_available": worker_available,
            "idle_expiry_recovery": idle_expiry_recovery,
            "worker_pid": run.worker_pid,
            "worker_state": run.worker_state or None,
            "worker_operation": run.worker_operation or None,
            "worker_deadline": run.worker_deadline or None,
            "worker_error": run.worker_error or None,
            "session_promotion": run.session_promotion,
            "error": run.error or None,
            "event_count": run.event_count,
            "completion_reason": run.completion_reason or None,
            "agents": agents,
            "phase": timing.run_phase if timing is not None else "",
            "phase_timings": dict(timing.run_phase_timings) if timing is not None else {},
        }

    def _run_overview_locked(self, run: ManagedRun) -> Dict[str, Any]:
        """Return only the lifecycle counts needed by a Workspace list."""
        indices = sorted(
            set(run.agent_statuses)
            | set(run.results)
            | set(run.agent_tokens)
        )
        statuses = [
            str(
                (run.results.get(idx) or {}).get("status")
                or run.agent_statuses.get(idx)
                or "unknown"
            ).strip().lower()
            for idx in indices
        ]
        return {
            "run_id": run.run_id,
            "status": run.status,
            "worker_active": self._worker_active_locked(run),
            "agents_total": len(statuses),
            "agents_finished": sum(
                status in TERMINAL_AGENT_STATUSES for status in statuses
            ),
            "agents_failed": sum(
                status in TERMINAL_AGENT_STATUSES and status != "success"
                for status in statuses
            ),
        }

    def get_run(
        self,
        run_id: str,
        include_events: bool = False,
        cursor: int = 0,
        event_limit: int = 50,
        event_max_bytes: int = DEFAULT_EVENT_PAGE_BYTES,
    ) -> Dict[str, Any]:
        with self._lock:
            run = self._require_run_locked(run_id)
            result = self._run_summary_locked(run)
        if include_events:
            result["event_page"] = self.get_events(
                run_id,
                cursor=cursor,
                limit=event_limit,
                max_bytes=event_max_bytes,
            )
        return result

    def list_runs(
        self,
        limit: int = 20,
        cursor: str = "",
        max_bytes: int = DEFAULT_RUN_LIST_PAGE_BYTES,
        workspace: str = "",
    ) -> Dict[str, Any]:
        limit = min(max(1, int(limit)), 100)
        max_bytes = min(max(4 * 1024, int(max_bytes)), MAX_RUN_LIST_PAGE_BYTES)
        boundary = _parse_run_cursor(cursor) if cursor else None
        with self._lock:
            self._sync_state_from_disk_locked()
            self._run_maintenance_locked()
            values = sorted(
                self._runs.values(),
                key=lambda run: (run.created_at, run.run_id),
                reverse=True,
            )
            if workspace:
                workspace_path = str(Path(workspace).expanduser().resolve())
                values = [run for run in values if run.workspace == workspace_path]
            total = len(values)
            if boundary is not None:
                values = [
                    run
                    for run in values
                    if (run.created_at, run.run_id) < boundary
                ]
            summaries: list[Dict[str, Any]] = []
            serialized_bytes = 2
            oversized_run = False
            for run in values:
                if len(summaries) >= limit:
                    break
                summary = self._run_summary_locked(run)
                encoded_size = len(
                    json.dumps(
                        summary,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                )
                separator = 1 if summaries else 0
                if summaries and serialized_bytes + separator + encoded_size > max_bytes:
                    break
                if not summaries and serialized_bytes + encoded_size > max_bytes:
                    oversized_run = True
                summaries.append(summary)
                serialized_bytes += separator + encoded_size
            has_more = len(summaries) < len(values)
            next_cursor = ""
            if has_more and summaries:
                last = summaries[-1]
                next_cursor = _run_cursor(
                    str(last.get("created_at") or ""),
                    str(last.get("run_id") or ""),
                )
            return {
                "runs": summaries,
                "total": total,
                "cursor": cursor,
                "next_cursor": next_cursor,
                "has_more": has_more,
                "serialized_bytes": serialized_bytes,
                "byte_limit": max_bytes,
                "oversized_run": oversized_run,
            }

    def get_run_overviews(self, run_ids: Sequence[str]) -> Dict[str, Any]:
        normalized = tuple(dict.fromkeys(str(run_id).strip() for run_id in run_ids))
        normalized = tuple(run_id for run_id in normalized if run_id)
        with self._lock:
            self._sync_state_from_disk_locked()
            self._run_maintenance_locked()
            return {
                "runs": [
                    self._run_overview_locked(self._runs[run_id])
                    for run_id in normalized
                    if run_id in self._runs
                ],
                "requested": len(normalized),
            }

    def completion_snapshot(self) -> list[Dict[str, Any]]:
        """Return the lightweight lifecycle state used by background notifiers."""

        with self._lock:
            self._sync_state_from_disk_locked()
            self._run_maintenance_locked()
            records: list[Dict[str, Any]] = []
            for run in self._runs.values():
                statuses = [
                    str(status or "").strip().lower()
                    for status in run.agent_statuses.values()
                ]
                working = (
                    self._worker_active_locked(run)
                    and run.worker_operation not in {"finalize", "discard"}
                ) or run.status in {
                    "preparing",
                    "running",
                    "stopping",
                }
                records.append(
                    {
                        "run_id": run.run_id,
                        "workspace_name": Path(run.workspace).name or run.workspace,
                        "status": run.status,
                        "completion_reason": run.completion_reason or None,
                        "finalized_agent": run.finalized_agent,
                        "finalizing_agent": run.finalizing_agent,
                        "worker_operation": run.worker_operation,
                        "updated_at": run.updated_at,
                        "working": working,
                        "all_agents_terminal": bool(statuses)
                        and all(
                            status in TERMINAL_AGENT_STATUSES
                            for status in statuses
                        ),
                    }
                )
            return records

    def get_events(
        self,
        run_id: str,
        cursor: int = 0,
        limit: int = 100,
        agent: Optional[int] = None,
        max_bytes: int = DEFAULT_EVENT_PAGE_BYTES,
        transcript_kinds: set[str] | None = None,
    ) -> Dict[str, Any]:
        cursor = max(0, int(cursor))
        limit = min(max(1, int(limit)), MAX_EVENT_PAGE)
        max_bytes = min(max(4 * 1024, int(max_bytes)), MAX_EVENT_PAGE_BYTES)
        with self._lock:
            self._require_run_locked(run_id)
        try:
            return self._events.read(
                run_id,
                cursor=cursor,
                limit=limit,
                agent=agent,
                max_bytes=max_bytes,
                transcript_kinds=transcript_kinds,
            )
        except OSError as exc:
            raise PluginRunError(f"Cannot read PCR events: {exc}") from exc

    def get_operation_details(
        self, run_id: str, agent: int, event_id: int,
        *, cursor: int = 0, max_bytes: int = 32 * 1024,
    ) -> Dict[str, Any]:
        page = self.get_events(run_id, cursor=event_id, limit=1)
        events = page.get("events", [])
        if not events or events[0].get("event_id") != event_id or events[0].get("idx") != agent:
            raise PluginRunError("The operation does not belong to this Agent.")
        event = events[0]
        if event.get("pcr_output_unavailable"):
            return {"text": "", "next_cursor": 0, "has_more": False,
                    "total_bytes": 0, "availability": "removed"}
        if "pcr_output_bytes" in event:
            total = event["pcr_output_bytes"]
            operation = event.get("pcr_work_operation") or {}
            identity = operation.get("id") if isinstance(operation, dict) else None
            if type(total) is not int or total < 0 or not isinstance(identity, str) or not identity:
                raise PluginRunError("The operation output reference is invalid.")
            with self._lock:
                run = self._require_run_locked(run_id)
                if agent not in run.agent_statuses and agent not in run.results:
                    raise PluginRunError("The operation Agent is unavailable.")
                path = self._agent_meta_file(run, agent, operation_output_name(identity))
            start = min(max(0, cursor), total)
            try:
                if path.parent.is_symlink() or path.stat().st_size < total:
                    raise OSError("Output prefix is unavailable")
                with path.open("rb") as source:
                    source.seek(start)
                    data = source.read(min(total - start, min(64 * 1024, max(4096, max_bytes)) + 4))
            except OSError:
                return {"text": "", "next_cursor": 0, "has_more": False,
                        "total_bytes": 0, "availability": "removed"}
            if data and data[0] & 0xC0 == 0x80:
                raise PluginRunError("The operation output cursor is invalid.")
            end = min(len(data), min(64 * 1024, max(4096, max_bytes)))
            while end < len(data) and data[end] & 0xC0 == 0x80:
                end -= 1
            return {"text": data[:end].decode("utf-8"), "next_cursor": start + end,
                    "has_more": start + end < total, "total_bytes": total, "availability": "available"}
        raw = str(event.get("text") or "")
        availability = "available"
        if "pcr_log_offset" in event:
            with self._lock:
                run = self._require_run_locked(run_id)
                if agent not in run.agent_statuses and agent not in run.results:
                    raise PluginRunError("The operation Agent is unavailable.")
                stream = event.get("stream")
                if stream not in {"stdout", "stderr"}:
                    raise PluginRunError("The operation log is unavailable.")
                log = self._agent_meta_file(run, agent, f"{stream}.log")
            offset, length = event.get("pcr_log_offset"), event.get("pcr_log_length")
            if not isinstance(offset, int) or not isinstance(length, int) or offset < 0 or not 0 < length <= 32 * 1024 * 1024:
                raise PluginRunError("The recorded operation is too large to preview.")
            try:
                with log.open("rb") as source:
                    source.seek(offset)
                    recorded = source.read(length)
                if len(recorded) != length or hashlib.sha256(recorded).hexdigest() != event.get("pcr_log_sha256"):
                    availability = "removed"
                    raw = ""
                else:
                    raw = recorded.decode("utf-8", errors="replace")
            except OSError:
                raw = ""
                availability = "removed"
        try:
            item = tool_item(json.loads(raw)) or {}
        except (ValueError, TypeError):
            item = {}
        output = None
        for key in ("aggregated_output", "output", "stdout", "stderr"):
            if isinstance(item.get(key), str):
                output = item[key]
                break
        if output is None and isinstance(item.get("changes"), list):
            diffs = [str(change.get("path", "")) + "\n" + change["diff"] for change in item["changes"]
                     if isinstance(change, dict) and isinstance(change.get("diff"), str)]
            if diffs:
                output = "\n\n".join(diffs)
        if output is None and item.get("type") in {"web_search", "tool_call"}:
            parts = {key: item[key] for key in ("query", "arguments", "result", "results", "error")
                     if item.get(key) is not None}
            if parts:
                output = json.dumps(parts, ensure_ascii=False, indent=2)
        if output is None:
            output = ""
        encoded = output.encode("utf-8")
        start = min(max(0, cursor), len(encoded))
        if start < len(encoded) and encoded[start] & 0xC0 == 0x80:
            raise PluginRunError("The operation output cursor is invalid.")
        end = min(len(encoded), start + min(64 * 1024, max(4096, max_bytes)))
        while end < len(encoded) and encoded[end] & 0xC0 == 0x80:
            end -= 1
        return {"text": encoded[start:end].decode("utf-8"), "next_cursor": end,
                "has_more": end < len(encoded), "total_bytes": len(encoded),
                "availability": availability}

    def search_conversation_text(self, *, query: str, workspace: str, cursor: str = "") -> dict[str, Any]:
        from .conversation_search import matching_excerpt, search_file

        query = query.strip()
        if not query or len(query) > 200:
            raise ValueError("Search text must contain 1 to 200 characters")
        page = self.list_runs(limit=10, cursor=cursor, workspace=workspace)
        matches = []
        for summary in page.get("runs", []):
            run_id = str(summary["run_id"])
            excerpt = matching_excerpt(str(summary.get("prompt") or ""), query)
            matched_agent = None
            if excerpt is None:
                with self._lock:
                    run = self._require_run_locked(run_id)
                    paths = []
                    for agent in sorted(set(run.results) | set(run.agent_statuses)):
                        try:
                            paths.append((agent, self._agent_meta_file(run, agent, "final_message.md")))
                        except PluginRunError:
                            continue
                for agent, path in paths:
                    excerpt = search_file(path, query)
                    if excerpt is not None:
                        matched_agent = agent
                        break
            if excerpt is not None:
                matches.append({"run_id": run_id, "agent": matched_agent, "excerpt": excerpt})
        return {"matches": matches, "next_cursor": page.get("next_cursor", ""),
                "has_more": bool(page.get("has_more")), "query": query}

    def _question_store(self, run: ManagedRun, agent: int) -> QuestionStore:
        directory = self._agent_meta_file(run, agent, "agent_questions.sqlite3").parent
        if directory.is_symlink() or directory.parent.is_symlink():
            raise PluginRunError("Agent question directory was replaced by a symbolic link")
        return QuestionStore(directory)

    def _questions_if_present(self, run: ManagedRun, agent: int) -> QuestionStore | None:
        if not run.run_root:
            return None
        directory = Path(run.run_root) / "meta" / f"agent_{agent:03d}"
        if not directory.is_dir():
            return None
        existing = any((directory / name).exists() for name in ("agent_questions.sqlite3", "agent_async_questions.sqlite3"))
        if not existing and self._question_stopped_locked(run, agent):
            return None
        if not existing and run.config.get("supports_agent_questions") is not True:
            return None
        try:
            store = self._question_store(run, agent)
        except PluginRunError:
            if not existing:
                return None
            raise
        if run.config.get("supports_agent_questions") is True:
            store.import_async_output(store.directory / "stdout.log")
        return store if store.path.exists() or store.async_path.exists() else None

    def _question_stopped_locked(self, run: ManagedRun, agent: int) -> bool:
        return (run.finalized_agent is not None or run.finalizing_agent is not None
            or run.status in FINAL_RUN_STATES | {"stopping", "finalizing"}
            or agent in run.rejected_agents or (self.control_dir / run.run_id / "stop").exists()
            or (self.control_dir / run.run_id / f"kill-agent-{agent:03d}").exists()
            or any(other.continued_from_run_id == run.run_id and other.continued_from_agent == agent
                   for other in self._runs.values()))

    def _question_snapshot_locked(self, run: ManagedRun, agent: int, record: dict[str, Any]) -> dict[str, Any]:
        if record["state"] == "open" and self._question_stopped_locked(run, agent):
            return {**record, "state": "invalidated"}
        return record

    def _schedule_async_answers_locked(self, run: ManagedRun) -> None:
        if (not self._detached_workers or run.run_id in self._async_answer_dispatching or self._closed
            or self._async_answer_retry_after.get(run.run_id, 0) > time.monotonic()
            or run.finalized_agent is not None or run.finalizing_agent is not None or run.worker_error
            or (self.control_dir / run.run_id / "stop").exists()):
            return
        for idx in run.agent_statuses:
            if self._question_stopped_locked(run, idx):
                continue
            directory = Path(run.run_root or "/nonexistent") / "meta" / f"agent_{idx:03d}"
            store = QuestionStore(directory)
            if not store.async_path.exists() or not store.queued_async():
                continue
            self._async_answer_dispatching.add(run.run_id)

            def dispatch(run_id=run.run_id, agent=idx):
                try:
                    self._dispatch_idle_async_answer(run_id, agent)
                except (PluginRunError, QuestionError, OSError, ValueError) as error:
                    self._async_answer_retry_after[run_id] = time.monotonic() + 60
                    logging.getLogger(__name__).warning("Async answer for %s is preserved but not delivered: %s", run_id, error)
                finally:
                    with self._lock:
                        self._async_answer_dispatching.discard(run_id)

            threading.Thread(target=dispatch, name="pcr-async-answer", daemon=True).start()
            break

    def _dispatch_idle_async_answer(self, run_id: str, agent: int) -> None:
        with self._run_operation_guard(run_id, "deliver original async answer"):
            with self._lock:
                run = self._require_run_locked(run_id)
                if (run.status != "completed" or self._worker_active_locked(run) or run.worker_operation
                    or self._question_stopped_locked(run, agent) or run.agent_statuses.get(agent) != "success"):
                    raise PluginRunError("The original Agent is not at an unstopped, unchanged idle answer boundary")
                store = self._question_store(run, agent)
                pending = store.queued_async()
                if not pending:
                    return
                record = pending[0]
                recovery = read_json(store.directory / "execution_recovery.json")
                if (recovery.get("stage") != "completed" or recovery.get("thread_id") != record["thread_id"]
                    or type(recovery.get("native_pid")) is not int or pid_is_alive(recovery.get("native_pid"))):
                    raise PluginRunError("The original native execution has not durably completed")
                self._require_persistent_worker_locked(run)
                checkpoint_path = worker_status_path(self.state_dir, run_id)
                checkpoint = read_json(checkpoint_path)
                if (checkpoint.get("pid") != run.worker_pid or checkpoint.get("run_id") != run_id
                    or checkpoint.get("active") is not False or checkpoint.get("alive") is not True
                    or checkpoint.get("operation") or checkpoint.get("operation_id") or not checkpoint.get("finished_at")
                    or checkpoint.get("error") or checkpoint.get("expired")
                    or any((self.state_dir / "workers" / "requests").glob(f"{run_id}-*.json"))
                    or read_json(worker_candidate_requests_path(self.state_dir, run_id)).get("requests")):
                    raise PluginRunError("The existing owner is not durably idle")
                old_pid = run.worker_pid
                shutdown = FileSignal(self.control_dir / run_id / "shutdown")
                shutdown.set()
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    stopped = read_json(checkpoint_path)
                    if stopped.get("pid") == old_pid and stopped.get("alive") is False and stopped.get("active") is False and stopped.get("stop_reason") == "shutdown":
                        self._refresh_events_locked(run, save=False)
                        break
                    time.sleep(0.05)
                else:
                    shutdown.clear()
                    raise PluginRunError("The idle owner did not retire; no reply execution was started")
                previous = run.results.get(agent)
                if not previous or store.get(record["id"])["delivery"] != "queued":
                    raise PluginRunError("The original result or answer changed before dispatch")
                old_process = self._worker_processes.pop(run_id, None)
                if old_process is not None:
                    old_process.wait(timeout=5)
                run.worker_pid = None
                run.worker_state = "busy"
                run.worker_operation = "async_answer"
                run.worker_operation_id = new_worker_operation_id(run_id)
                run.worker_started_at = _utc_now()
                run.worker_deadline = self._next_worker_deadline()
                request = {"run": run.to_dict(), "indices": [agent], "request_id": record["id"],
                    "previous_result": dict(previous), "agent_role": run.agent_roles.get(agent, "candidate")}
                process, _ = spawn_worker(self.state_dir, run_id, "async_answer", request,
                    operation_id=run.worker_operation_id, wait_for_publication=True)
                self._worker_processes[run_id] = process
                run.worker_pid = process.pid
                self._append_event_locked(run, {"type": "async_answer_started", "idx": agent,
                    "request_id": record["id"], "previous_result": previous})
                run.updated_at = _utc_now()
                self._save_state_locked()

    def get_question(self, run_id: str, agent: int, request_id: str) -> Dict[str, Any]:
        with self._lock:
            run = self._require_run_locked(run_id)
            if agent not in run.agent_statuses and agent not in run.results:
                raise PluginRunError(f"AGENT-{agent:03d} does not belong to {run_id}")
            record = self._question_store(run, agent).get(request_id)
            return {"run_id": run_id, "agent": agent, "request": self._question_snapshot_locked(run, agent, record)}

    def answer_question(self, run_id: str, agent: int, request_id: str,
                        connection_id: str, answer_id: str, answers: dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            run = self._require_run_locked(run_id)
            if agent not in run.agent_statuses and agent not in run.results:
                raise PluginRunError(f"AGENT-{agent:03d} does not belong to {run_id}")
            store = self._questions_if_present(run, agent) or self._question_store(run, agent)
            asynchronous = store.get(request_id).get("kind") == "async"
            if (self._question_stopped_locked(run, agent) or run.agent_statuses.get(agent) in TERMINAL_AGENT_STATUSES and not (
                    asynchronous and run.status in {"running", "completed"} and run.agent_statuses.get(agent) == "success")
                or (self.control_dir / run_id / "stop").exists()
                or (self.control_dir / run_id / f"kill-agent-{agent:03d}").exists()):
                raise QuestionError("The Agent has stopped; no answer was sent")
            result = store.answer(request_id, connection_id, answer_id, answers)
            if asynchronous and run.status == "completed":
                self._schedule_async_answers_locked(run)
        return {"run_id": run_id, "agent": agent, "request": result}

    def get_agent(
        self,
        run_id: str,
        agent: int,
        include_diff: bool = False,
        include_events: bool = True,
        include_final_message: bool = True,
        include_final_message_page: bool = False,
        cursor: int = 0,
        event_limit: int = 100,
        event_max_bytes: int = DEFAULT_EVENT_PAGE_BYTES,
        final_message_cursor: int = 0,
        final_message_max_bytes: int = DEFAULT_FINAL_MESSAGE_PAGE_BYTES,
        transcript_kinds: set[str] | None = None,
    ) -> Dict[str, Any]:
        agent = int(agent)
        with self._lock:
            run = self._require_run_locked(run_id)
            if agent not in run.agent_statuses and agent not in run.results:
                raise PluginRunError(
                    f"AGENT-{agent:03d} does not belong to {run_id}"
                )
            result_data = dict(run.results.get(agent) or {})
            response = self._agent_summary_locked(run, agent)
            response["run_id"] = run_id
            questions = self._questions_if_present(run, agent)
            response["input_requests"] = [self._question_snapshot_locked(run, agent, record)
                for record in questions.snapshot()] if questions is not None else []
            response["stdout_tail"] = result_data.get("stdout_tail") or ""
            response["stderr_tail"] = result_data.get("stderr_tail") or ""
            response["workspace_dir"] = None
            response["stdout_log"] = None
            response["stderr_log"] = None
            final_message_path = None
            artifact_errors: list[str] = []
            visible_mapping = self._accepted_workspace_mapping_locked(
                run,
                agent=agent,
            )

            if response["workspace_available"]:
                try:
                    candidate = self._agent_workspace_for_run(
                        run,
                        agent,
                        result_data.get("workspace_dir"),
                    )
                    response["workspace_dir"] = str(candidate)
                except PluginRunError:
                    response["workspace_available"] = False
                    response["can_accept"] = False
                    response["can_continue"] = False
                    artifact_errors.append(
                        "The saved Agent workspace could not be verified."
                    )

            if self._run_root_available_locked(run):
                try:
                    stdout_log = self._agent_meta_file(run, agent, "stdout.log")
                    stderr_log = self._agent_meta_file(run, agent, "stderr.log")
                    final_message_path = self._agent_meta_file(
                        run, agent, "final_message.md"
                    )
                    response["stdout_log"] = (
                        str(stdout_log) if stdout_log.is_file() else None
                    )
                    response["stderr_log"] = (
                        str(stderr_log) if stderr_log.is_file() else None
                    )
                except PluginRunError:
                    artifact_errors.append(
                        "The saved Agent output could not be verified."
                    )
        if include_final_message:
            final_message = ""
            if final_message_path is not None and final_message_path.is_file():
                try:
                    final_message = final_message_path.read_text(
                        encoding="utf-8"
                    ).strip()
                except OSError:
                    final_message = ""
            final_message = rewrite_workspace_references(
                final_message,
                (visible_mapping,) if visible_mapping is not None else (),
            )
            response["final_message"] = final_message
        else:
            response["final_message"] = ""
        if include_final_message_page:
            response["final_message_page"] = _read_utf8_text_page(
                final_message_path,
                cursor=final_message_cursor,
                max_bytes=final_message_max_bytes,
                transform=lambda value: rewrite_workspace_references(
                    value,
                    (visible_mapping,) if visible_mapping is not None else (),
                ),
            )
        response["artifact_error"] = " ".join(dict.fromkeys(artifact_errors)) or None
        if include_events:
            response["event_page"] = self.get_events(
                run_id,
                cursor=cursor,
                limit=event_limit,
                agent=agent,
                max_bytes=event_max_bytes,
                transcript_kinds=transcript_kinds,
            )
        if include_diff:
            if response["artifact_error"]:
                response["diff"] = None
                response["diff_error"] = response["artifact_error"]
            else:
                try:
                    page = self.get_diff(
                        run_id,
                        agent,
                        cursor=0,
                        limit=MAX_DIFF_PAGE_CHARS,
                    )
                    response["diff"] = page["text"]
                    response["diff_next_cursor"] = page["next_cursor"]
                    response["diff_has_more"] = page["has_more"]
                    response["diff_error"] = None
                except (OSError, PluginRunError) as exc:
                    response["diff"] = None
                    response["diff_error"] = str(exc)
        return response

    def get_diff(
        self,
        run_id: str,
        agent: int,
        cursor: int = 0,
        limit: int = 20_000,
        full_context: bool = False,
        snapshot_id: str = "",
    ) -> Dict[str, Any]:
        agent = int(agent)
        cursor = max(0, int(cursor))
        limit = min(max(1, int(limit)), MAX_DIFF_PAGE_CHARS)
        with self._lock:
            run = self._require_run_locked(run_id)
            if agent not in run.agent_statuses and agent not in run.results:
                raise PluginRunError(
                    f"AGENT-{agent:03d} does not belong to {run_id}"
                )
            result_data = dict(run.results.get(agent) or {})
            live = not result_data or run.agent_statuses.get(agent) in {"pending", "running", "starting"}
            captured = deepcopy(run) if live or snapshot_id else None
        if captured is not None:
            try:
                identifier, data = self._live_diffs.read(
                    (run_id, agent, full_context), snapshot_id,
                    lambda: self._artifacts.live_diff(captured, agent, full_context=full_context),
                )
            except (ArtifactError, OSError) as exc:
                raise PluginRunError(str(exc)) from exc
            text, next_cursor = live_diff_page(data, cursor, limit)
            return {"run_id": run_id, "agent": agent, "cursor": min(cursor, len(data)),
                    "next_cursor": next_cursor, "has_more": next_cursor < len(data),
                    "total_chars": len(data), "cursor_unit": "utf8_bytes", "full_context": full_context,
                    "snapshot_id": identifier, "live": True, "text": text}
        with self._lock:
            run = self._require_run_locked(run_id)
            try:
                path = self._artifacts.persist_diff(
                    run,
                    agent,
                    result_data.get("workspace_dir"),
                    full_context=full_context,
                )
            except ArtifactError as exc:
                raise PluginRunError(str(exc)) from exc
        total = path.stat().st_size
        cursor = min(cursor, total)
        with path.open("rb") as handle:
            handle.seek(cursor)
            raw = handle.read(limit)
            while raw:
                try:
                    text = raw.decode("utf-8")
                    break
                except UnicodeDecodeError as exc:
                    if exc.reason != "unexpected end of data" or len(raw) >= limit + 4:
                        text = raw.decode("utf-8", errors="replace")
                        break
                    extra = handle.read(1)
                    if not extra:
                        text = raw.decode("utf-8", errors="replace")
                        break
                    raw += extra
            else:
                text = ""
            next_cursor = handle.tell()
        return {
            "run_id": run_id,
            "agent": agent,
            "cursor": cursor,
            "next_cursor": next_cursor,
            "has_more": next_cursor < total,
            "total_chars": total,
            "cursor_unit": "utf8_bytes",
            "full_context": path.name.endswith(".full.patch"),
            "persisted_path": str(path),
            "text": text,
        }

    def get_file_preview(self, run_id: str, agent: int, path: str, **page: Any) -> Dict[str, Any]:
        with self._lock:
            run = self._require_run_locked(run_id)
            if agent not in run.agent_statuses and agent not in run.results:
                raise PluginRunError(f"AGENT-{agent:03d} does not belong to {run_id}")
            result = dict(run.results.get(agent) or {})
            live = not result or run.agent_statuses.get(agent) in {
                "pending", "copying", "queued", "starting", "resuming", "submitting",
                "waiting_for_output", "waiting_for_models", "running", "stopping",
            }
            captured = deepcopy(run)
        try:
            return self._artifacts.file_preview(captured, agent, path, live=live,
                                               recorded_path=result.get("workspace_dir"), **page)
        except (ArtifactError, OSError, ValueError) as exc:
            raise PluginRunError(str(exc)) from exc

    def reject_agent(
        self,
        run_id: str,
        agent: int,
        rejected: bool = True,
    ) -> Dict[str, Any]:
        with self._run_operation_guard(run_id, "recommendation update"):
            return self._reject_agent(run_id, agent, rejected)

    def _reject_agent(
        self,
        run_id: str,
        agent: int,
        rejected: bool,
    ) -> Dict[str, Any]:
        agent = int(agent)
        with self._lock:
            run = self._require_run_locked(run_id)
            if agent not in run.agent_statuses and agent not in run.results:
                raise PluginRunError(
                    f"AGENT-{agent:03d} does not belong to {run_id}"
                )
            rejected_values = set(run.rejected_agents)
            if rejected:
                rejected_values.add(agent)
            else:
                rejected_values.discard(agent)
            run.rejected_agents = sorted(rejected_values)
            self._recompute_recommendation_locked(run)
            run.updated_at = _utc_now()
            self._append_event_locked(
                run,
                {
                    "type": "agent_rejected" if rejected else "agent_restored",
                    "idx": agent,
                },
            )
            self._save_state_locked()
            return self._run_summary_locked(run)

    def kill_agent(self, run_id: str, agent: int) -> Dict[str, Any]:
        with self._run_operation_guard(run_id, f"stop AGENT-{int(agent):03d}"):
            return self._kill_agent(run_id, agent)

    def _kill_agent(self, run_id: str, agent: int) -> Dict[str, Any]:
        agent = int(agent)
        with self._lock:
            run = self._require_run_locked(run_id)
            context = self._contexts.get(run_id)
            if not self._worker_active_locked(run):
                raise PluginRunError(f"PCR run {run_id} is not active")
            current = run.agent_statuses.get(agent)
            if current in {"success", "failed", "error", "killed", "cancelled"}:
                raise PluginRunError(f"AGENT-{agent:03d} has already finished")
            if current == "queued":
                raise PluginRunError(
                    f"AGENT-{agent:03d} is queued; queued Agents start normally"
                )
            if current == "stopping":
                raise PluginRunError(f"AGENT-{agent:03d} is already stopping")
            if current not in {"running", "starting", "resuming", "submitting", "waiting_for_output", "waiting_for_models"}:
                raise PluginRunError(f"AGENT-{agent:03d} is not running")
            if context is not None:
                cancel_event = context.agent_cancel_events.get(agent)
                if cancel_event is None:
                    raise PluginRunError(
                        f"AGENT-{agent:03d} is not part of the active batch"
                    )
                cancel_event.set()
            else:
                FileSignal(
                    self.control_dir / run_id / f"kill-agent-{agent:03d}"
                ).set()
            run.agent_statuses[agent] = "stopping"
            run.updated_at = _utc_now()
            self._append_event_locked(
                run,
                {"type": "agent_stop_requested", "idx": agent},
            )
            self._save_state_locked()
            return self._agent_summary_locked(run, agent)

    def stop_run(
        self,
        run_id: str,
        wait_seconds: float = 30.0,
    ) -> Dict[str, Any]:
        with self._lock:
            run = self._require_run_locked(run_id)
            if self._result_operation_pending_locked(run):
                raise PluginRunPendingError(
                    "The previous result is being saved. Its worker cannot be stopped as an Agent."
                )
            context = self._contexts.get(run_id)
            thread = context.thread if context is not None else None
            if not self._worker_active_locked(run):
                return self._run_summary_locked(run)
            if context is not None:
                context.cancel_event.set()
            else:
                FileSignal(self.control_dir / run_id / "stop").set()
            run.status = "stopping"
            run.updated_at = _utc_now()
            self._append_event_locked(run, {"type": "run_stop_requested"})
            self._save_state_locked()
        deadline = time.monotonic() + max(0.0, float(wait_seconds))
        if thread is not None:
            thread.join(timeout=max(0.0, float(wait_seconds)))
        else:
            while time.monotonic() < deadline:
                with self._lock:
                    current = self._require_run_locked(run_id)
                    if not self._worker_active_locked(current):
                        break
                time.sleep(0.1)
        with self._lock:
            run = self._require_run_locked(run_id)
            if self._worker_active_locked(run):
                run.status = "stopping"
            return self._run_summary_locked(run)

    def wait_for_run(
        self,
        run_id: str,
        timeout_seconds: float = 30.0,
        cursor: int = 0,
        event_limit: int = 50,
        event_max_bytes: int = DEFAULT_EVENT_PAGE_BYTES,
    ) -> Dict[str, Any]:
        timeout_seconds = min(max(0.0, float(timeout_seconds)), 60.0)
        with self._lock:
            run = self._require_run_locked(run_id)
            context = self._contexts.get(run_id)
            thread = context.thread if context is not None else None
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout_seconds)
        elif self._detached_workers:
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                with self._lock:
                    run = self._require_run_locked(run_id)
                    if not self._worker_active_locked(run):
                        break
                time.sleep(0.1)
        result = self.get_run(
            run_id,
            include_events=True,
            cursor=cursor,
            event_limit=event_limit,
            event_max_bytes=event_max_bytes,
        )
        result["still_running"] = bool(result.get("worker_active"))
        return result

    def _queue_persistent_operation_locked(
        self,
        run: ManagedRun,
        operation: str,
        request: Dict[str, Any],
    ) -> str:
        self._require_persistent_worker_locked(run)
        if self._worker_active_locked(run):
            raise PluginRunError(
                f"PCR run {run.run_id} is still performing "
                f"{run.worker_operation or 'another operation'}"
            )
        previous = (
            run.worker_state,
            run.worker_operation,
            run.worker_operation_id,
            run.worker_started_at,
            run.worker_deadline,
            run.worker_error,
        )
        run.worker_state = "busy"
        run.worker_operation = operation
        run.worker_operation_id = new_worker_operation_id(run.run_id)
        run.worker_started_at = _utc_now()
        run.worker_deadline = self._next_worker_deadline()
        run.worker_error = ""
        run.updated_at = _utc_now()
        self._save_state_locked()
        try:
            operation_id, _request_path = enqueue_worker_operation(
                self.state_dir,
                run.run_id,
                operation,
                {
                    "run": run.to_dict(),
                    "expect_response": True,
                    **request,
                },
                operation_id=run.worker_operation_id,
            )
        except Exception as exc:
            (
                run.worker_state,
                run.worker_operation,
                run.worker_operation_id,
                run.worker_started_at,
                run.worker_deadline,
                run.worker_error,
            ) = previous
            run.updated_at = _utc_now()
            self._save_state_locked()
            raise PluginRunError(
                f"Could not queue {operation} for persistent PCR worker: {exc}"
            ) from exc
        return operation_id

    def _wait_for_persistent_operation(
        self,
        run_id: str,
        operation_id: str,
        timeout_seconds: float,
    ) -> Dict[str, Any] | None:
        response_path = worker_response_path(self.state_dir, operation_id)
        deadline = time.monotonic() + max(0.0, float(timeout_seconds))
        while True:
            response = read_json(response_path)
            if response:
                try:
                    response_path.unlink()
                except OSError:
                    pass
                if bool(response.get("ok")) and str(
                    response.get("operation") or ""
                ) in {"finalize", "discard"}:
                    while time.monotonic() < deadline:
                        with self._lock:
                            terminal_run = self._require_run_locked(run_id)
                            if terminal_run.worker_state in {"stopped", "crashed"}:
                                break
                        time.sleep(0.05)
                with self._lock:
                    run = self._require_run_locked(run_id)
                    self._save_state_locked()
                if not bool(response.get("ok")):
                    raise PluginRunError(
                        str(response.get("error") or "Persistent PCR worker operation failed")
                    )
                return response
            with self._lock:
                run = self._require_run_locked(run_id)
                if run.worker_state == "crashed":
                    self._require_persistent_worker_locked(run)
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.1)

    def _start_additional_batch(
        self,
        run: ManagedRun,
        indices: Sequence[int],
        retry_indices: set[int],
        *,
        agent_role: str = AGENT_ROLE_CANDIDATE,
        retained_startup_fingerprint: str | None = None,
        retained_execution_fingerprints: dict[int, str] | None = None,
        synthesis_preparation_fingerprints: dict[int, str] | None = None,
    ) -> Dict[str, Any]:
        agent_role = normalize_agent_role(agent_role)
        operation = "retry" if retry_indices else "more"
        run_root = self._validated_run_root(run)
        worker_active = self._worker_active_locked(run)
        fresh_owner = bool(retained_execution_fingerprints or synthesis_preparation_fingerprints)
        if synthesis_preparation_fingerprints and (
            not self._detached_workers or worker_active or run.worker_pid is not None
            or run.worker_state != "stopped" or set(synthesis_preparation_fingerprints) != set(indices)
            or retry_indices != set(indices) or agent_role != AGENT_ROLE_SYNTHESIS
            or retained_execution_fingerprints or retained_startup_fingerprint
            or (self.control_dir / run.run_id / "stop").exists()
            or any((self.control_dir / run.run_id / f"kill-agent-{idx:03d}").exists() for idx in indices)
        ):
            raise PluginRunError("Synthesis preparation dispatch requires a retired owner and exact unsubmitted slots")
        if retained_execution_fingerprints and (
            not self._detached_workers or worker_active or run.worker_pid is not None
            or run.worker_state != "stopped" or set(retained_execution_fingerprints) != set(indices)
            or retry_indices != set(indices) or agent_role != AGENT_ROLE_CANDIDATE
            or (self.control_dir / run.run_id / "stop").exists()
            or any((self.control_dir / run.run_id / f"kill-agent-{idx:03d}").exists() for idx in indices)
        ):
            raise PluginRunError("Retained execution dispatch requires a retired owner and exact candidate bindings")
        live_candidate_stage = bool(
            worker_active
            and self._detached_workers
            and run.worker_operation == "initial"
            and candidate_requests_accepting(
                worker_candidate_requests_path(self.state_dir, run.run_id)
            )
        )
        if worker_active and not live_candidate_stage:
            raise PluginRunError(
                f"PCR run {run.run_id} is still active; wait or stop it first"
            )
        if self._detached_workers and not (retained_startup_fingerprint or fresh_owner):
            self._require_persistent_worker_locked(run)
        refresh_synthesis = bool(
            not live_candidate_stage
            and agent_role == AGENT_ROLE_CANDIDATE
            and int(run.config.get("synthesis_agents") or 0) > 0
        )
        context: _RunContext | None = None
        args: argparse.Namespace | None = None
        if not live_candidate_stage:
            context = _RunContext(
                cancel_event=threading.Event(),
                agent_cancel_events={idx: threading.Event() for idx in indices},
            )
            args = self._build_args(run, context, num_agents=len(indices))
            try:
                validate_args(args)
            except SystemExit as exc:
                raise PluginRunError(str(exc)) from exc
        previous_status = run.status
        previous_error = run.error
        previous_rejected = list(run.rejected_agents)
        previous_stale_synthesis = list(run.stale_synthesis_agents)
        previous_worker = (
            run.worker_state,
            run.worker_operation,
            run.worker_operation_id,
            run.worker_started_at,
            run.worker_deadline,
            run.worker_error,
        )
        previous_agents = {
            idx: (
                run.agent_statuses.get(idx),
                run.agent_roles.get(idx),
                run.agent_tokens.get(idx),
                dict(run.results[idx]) if idx in run.results else None,
            )
            for idx in indices
        }
        run.status = "running"
        run.error = ""
        if refresh_synthesis:
            run.stale_synthesis_agents = sorted(
                idx
                for idx, value in run.results.items()
                if normalize_agent_role(
                    value.get("role", run.agent_roles.get(idx))
                ) == AGENT_ROLE_SYNTHESIS
            )
        for idx in indices:
            run.agent_statuses[idx] = "queued"
            run.agent_roles[idx] = agent_role
            run.agent_tokens[idx] = None
            if idx in retry_indices:
                run.results.pop(idx, None)
                if idx in run.rejected_agents:
                    run.rejected_agents.remove(idx)
            if not fresh_owner:
                FileSignal(
                    self.control_dir / run.run_id / f"kill-agent-{idx:03d}"
                ).clear()
        if not live_candidate_stage:
            if not fresh_owner:
                FileSignal(self.control_dir / run.run_id / "stop").clear()
            run.worker_operation = operation
            if self._detached_workers:
                run.worker_state = "busy"
                run.worker_operation_id = new_worker_operation_id(run.run_id)
            run.worker_started_at = _utc_now()
            run.worker_deadline = self._next_worker_deadline()
        self._recompute_recommendation_locked(run)
        run.updated_at = _utc_now()
        self._append_event_locked(
            run,
            {
                "type": "batch_created",
                "indices": list(indices),
                "retry_indices": sorted(retry_indices),
                "role": agent_role,
                "live": live_candidate_stage,
                "refresh_synthesis": refresh_synthesis,
            },
        )
        # A retained execution has no owner until spawn succeeds. Publishing a
        # busy record with a missing PID makes older schedulers declare a crash.
        if not fresh_owner:
            self._save_state_locked()

        def restore_after_queue_failure(message: str) -> None:
            run.status = previous_status
            run.error = previous_error
            run.rejected_agents = previous_rejected
            run.stale_synthesis_agents = previous_stale_synthesis
            (
                run.worker_state,
                run.worker_operation,
                run.worker_operation_id,
                run.worker_started_at,
                run.worker_deadline,
                run.worker_error,
            ) = previous_worker
            for idx, (status, role, tokens, result) in previous_agents.items():
                if status is None:
                    run.agent_statuses.pop(idx, None)
                    run.agent_roles.pop(idx, None)
                    run.agent_tokens.pop(idx, None)
                else:
                    run.agent_statuses[idx] = status
                    if role is None:
                        run.agent_roles.pop(idx, None)
                    else:
                        run.agent_roles[idx] = role
                    run.agent_tokens[idx] = tokens
                if result is None:
                    run.results.pop(idx, None)
                else:
                    run.results[idx] = result
            self._recompute_recommendation_locked(run)
            run.updated_at = _utc_now()
            self._append_event_locked(
                run,
                {
                    "type": "batch_queue_failed",
                    "indices": list(indices),
                    "message": message,
                },
            )
            self._save_state_locked()

        if live_candidate_stage:
            accepted = enqueue_candidate_requests(
                worker_candidate_requests_path(self.state_dir, run.run_id),
                indices,
                sorted(retry_indices),
            )
            if not accepted:
                message = "The first-stage candidate window has already closed"
                restore_after_queue_failure(message)
                raise PluginRunError(message)
            for idx in retry_indices:
                try:
                    self._artifacts.diff_path(run, idx).unlink()
                except (ArtifactError, FileNotFoundError):
                    pass
            return self._run_summary_locked(run)

        if self._detached_workers:
            try:
                request = {
                    "run": run.to_dict(),
                    "indices": list(indices),
                    "retry_indices": sorted(retry_indices),
                    "agent_role": agent_role,
                    "refresh_synthesis": refresh_synthesis,
                }
                if retained_startup_fingerprint:
                    if run.worker_operation != operation or run.worker_state != "busy":
                        raise PluginRunError("The retained startup operation changed before dispatch")
                    request["retained_startup_fingerprint"] = retained_startup_fingerprint
                    process, _operation_id = spawn_worker(
                        self.state_dir, run.run_id, operation, request,
                        operation_id=run.worker_operation_id,
                    )
                    self._worker_processes[run.run_id] = process
                    run.worker_pid = process.pid
                elif fresh_owner:
                    key = "retained_execution_fingerprints" if retained_execution_fingerprints else "synthesis_preparation_fingerprints"
                    request[key] = {str(idx): value for idx, value in (
                        retained_execution_fingerprints or synthesis_preparation_fingerprints).items()}
                    process, _operation_id = spawn_worker(
                        self.state_dir, run.run_id, operation, request,
                        operation_id=run.worker_operation_id,
                        wait_for_publication=True,
                    )
                    self._worker_processes[run.run_id] = process
                    run.worker_pid = process.pid
                else:
                    enqueue_worker_operation(
                        self.state_dir,
                        run.run_id,
                        operation,
                        request,
                        operation_id=run.worker_operation_id,
                    )
            except Exception as exc:
                restore_after_queue_failure(str(exc))
                raise PluginRunError(
                    f"Could not queue persistent worker operation: {exc}"
                ) from exc
            if retained_startup_fingerprint or fresh_owner:
                # Event readers can hold the pre-publication record at the
                # batch event's timestamp. Make the PID commit strictly newer
                # so their delayed saves merge, rather than erase, this owner.
                run.updated_at = _utc_now()
                self._save_state_locked()
            for idx in retry_indices:
                try:
                    self._artifacts.diff_path(run, idx).unlink()
                except (ArtifactError, FileNotFoundError):
                    pass
            return self._run_summary_locked(run)

        assert context is not None and args is not None
        self._contexts[run.run_id] = context

        def target() -> None:
            try:
                progress = lambda payload: self._record_progress(  # noqa: E731
                    run.run_id, payload
                )
                if agent_role == AGENT_ROLE_SYNTHESIS:
                    recorded_results = []
                    for value in run.results.values():
                        try:
                            recorded_results.append(AgentResult(**value))
                        except (TypeError, ValueError):
                            continue
                    run_synthesis_refresh(
                        args=args,
                        prompt=run.prompt,
                        run_root=run_root,
                        workspace=self._workspace_for_run(run),
                        recorded_results=recorded_results,
                        resume_session_id=run.config.get("resume_session_id"),
                        progress_callback=progress,
                        cancel_event=context.cancel_event,
                        agent_cancel_events=context.agent_cancel_events,
                        synthesis_indices=indices,
                        retry_synthesis_indices=retry_indices,
                        recommendation_excluded_indices=set(run.rejected_agents),
                    )
                else:
                    run_additional_agents(
                        args=args,
                        prompt=run.prompt,
                        agent_indices=indices,
                        run_root=run_root,
                        workspace=self._workspace_for_run(run),
                        resume_session_id=run.config.get("resume_session_id"),
                        retry_indices=retry_indices,
                        progress_callback=progress,
                        cancel_event=context.cancel_event,
                        agent_cancel_events=context.agent_cancel_events,
                        recommendation_excluded_indices=(
                            set(run.rejected_agents)
                            | set(run.stale_synthesis_agents)
                        ),
                    )
                    if refresh_synthesis and not context.cancel_event.is_set():
                        recorded_results = []
                        for value in run.results.values():
                            try:
                                recorded_results.append(AgentResult(**value))
                            except (TypeError, ValueError):
                                continue
                        run_synthesis_refresh(
                            args=args,
                            prompt=run.prompt,
                            run_root=run_root,
                            workspace=self._workspace_for_run(run),
                            recorded_results=recorded_results,
                            resume_session_id=run.config.get("resume_session_id"),
                            progress_callback=progress,
                            cancel_event=context.cancel_event,
                            agent_cancel_events=context.agent_cancel_events,
                            recommendation_excluded_indices=set(run.rejected_agents),
                        )
                self._record_progress(
                    run.run_id,
                    {
                        "type": "batch_finished",
                        "cancelled": context.cancel_event.is_set(),
                    },
                )
            except BaseException as exc:
                self._record_progress(
                    run.run_id,
                    {"type": "batch_failed", "message": str(exc)},
                )
            finally:
                with self._lock:
                    self._finish_background_thread_locked(run.run_id)

        thread = threading.Thread(
            target=target,
            name=f"pcr-plugin-batch-{run.run_id}",
            daemon=True,
        )
        context.thread = thread
        thread.start()
        return self._run_summary_locked(run)

    def add_agents(
        self,
        run_id: str,
        count: int,
        confirm_large_run: bool = False,
    ) -> Dict[str, Any]:
        with self._run_operation_guard(run_id, "add Agents"):
            return self._add_agents(run_id, count, confirm_large_run)

    def update_synthesis_settings(
        self,
        run_id: str,
        agents: int,
        model: object = None,
        effort: object = None,
        fast: object = None,
    ) -> Dict[str, Any]:
        agents = int(agents)
        _validated_agent_count(agents, "synthesis_agents", allow_zero=True)
        try:
            normalized_model = normalize_synthesis_model(model)
            normalized_effort = normalize_synthesis_effort(effort)
            normalized_fast = normalize_synthesis_fast(fast)
        except argparse.ArgumentTypeError as exc:
            raise PluginRunError(str(exc)) from exc

        with self._lock:
            run = self._require_run_locked(run_id)
            candidate_count = sum(
                normalize_agent_role(run.agent_roles.get(idx))
                != AGENT_ROLE_SYNTHESIS
                for idx in set(run.agent_statuses) | set(run.results)
            )
            _validate_run_agent_total(candidate_count, agents)
            if run.config.get("workspace_mode") == "source":
                raise PluginRunError("Synthesis is unavailable for a source-folder turn. Configure it before the next turn.")
            if not self._detached_workers or not self._worker_active_locked(run):
                raise PluginRunError(
                    "Synthesis settings can only change while the initial Agents are running"
                )
            if run.worker_operation != "initial":
                raise PluginRunError(
                    "Synthesis settings can no longer change for this run"
                )
            path = worker_synthesis_config_path(self.state_dir, run_id)
            with locked_synthesis_config(path):
                current = read_json(path)
                if bool(current.get("locked")):
                    raise PluginRunError(
                        "Synthesis has already started; its settings are now locked"
                    )
                write_json_atomic(
                    path,
                    {
                        "agents": agents,
                        "model": normalized_model,
                        "effort": normalized_effort,
                        "fast": normalized_fast,
                        "locked": False,
                    },
                )
            run.config.update(
                {
                    "synthesis_agents": agents,
                    "synthesis_model": normalized_model,
                    "synthesis_effort": normalized_effort,
                    "synthesis_fast": normalized_fast,
                }
            )
            run.updated_at = _utc_now()
            self._append_event_locked(
                run,
                {
                    "type": "synthesis_settings_updated",
                    "agents": agents,
                    "model": normalized_model,
                    "effort": normalized_effort,
                    "fast": normalized_fast,
                },
            )
            self._save_state_locked()
            return self._run_summary_locked(run)

    def _add_agents(
        self,
        run_id: str,
        count: int,
        confirm_large_run: bool,
    ) -> Dict[str, Any]:
        count = int(count)
        _validated_agent_count(count, "count")
        with self._lock:
            run = self._require_run_locked(run_id)
            if run.config.get("workspace_mode") == "source":
                raise PluginRunError("This turn runs in the source folder. Start a new turn to use multiple Agents.")
            current_count = len(set(run.agent_statuses) | set(run.results))
            if current_count + count > MAX_AGENT_COUNT:
                raise PluginRunError(
                    f"adding {count} Agents would raise this run to "
                    f"{current_count + count}, above the engineering guard of "
                    f"{MAX_AGENT_COUNT}"
                )
            if run.status in FINAL_RUN_STATES or run.finalized_agent is not None:
                raise PluginRunError(f"PCR run {run_id} is already {run.status}")
            storage = self.estimate(
                run.workspace,
                count,
                run.config.get("runs_dir"),
                run.config.get("resume_session_id"),
            )
            if not storage["fits"]:
                raise PluginRunError(
                    "Insufficient disk space for additional Agents: "
                    f"need {storage['total']}, available {storage['free']}"
                )
            if storage["confirmation_required"] and not confirm_large_run:
                return {
                    "status": "confirmation_required",
                    "message": (
                        f"The additional Agents are estimated to use {storage['total']}. "
                        "Ask the user to confirm, then call pcr_add_agents again with "
                        "confirm_large_run=true."
                    ),
                    "storage": storage,
                    "run": self._run_summary_locked(run),
                }
            projected = self._reserved_storage_locked() + int(storage["total_bytes"])
            if self.enforce_storage_quota and projected > self.storage_quota_bytes:
                raise PluginRunError(
                    "PCR plugin storage quota exceeded by additional Agents: "
                    f"{format_storage_bytes(projected)} projected, "
                    f"{format_storage_bytes(self.storage_quota_bytes)} allowed"
                )
            highest = max(
                set(run.agent_statuses) | set(run.results) | {0}
            )
            indices = list(range(highest + 1, highest + count + 1))
            previous_count = run.config.get("num_agents")
            previous_estimate = run.estimated_bytes
            run.config["num_agents"] = int(previous_count or 0) + count
            run.estimated_bytes += int(storage["total_bytes"])
            try:
                response = self._start_additional_batch(run, indices, set())
                response["additional_storage"] = storage
                return response
            except BaseException:
                run.config["num_agents"] = previous_count
                run.estimated_bytes = previous_estimate
                run.updated_at = _utc_now()
                self._save_state_locked()
                raise

    def retry_agent(
        self, run_id: str, agent: int, *, refresh_startup_runtime: bool = False,
    ) -> Dict[str, Any]:
        with self._run_operation_guard(run_id, f"retry AGENT-{int(agent):03d}"):
            return self._retry_agent(run_id, agent, refresh_startup_runtime=refresh_startup_runtime)

    def resume_agents_in_place(self, run_id: str, agents: Sequence[int]) -> Dict[str, Any]:
        """Explicit maintenance handoff of idle, receipt-proven failed candidates."""
        indices = list(agents)
        if not indices or any(type(idx) is not int or idx <= 0 for idx in indices) or len(set(indices)) != len(indices):
            raise PluginRunError("Recovery requires distinct positive candidate indices")
        with self._run_operation_guard(run_id, "resume retained candidate executions"):
            with self._lock:
                run = self._require_run_locked(run_id)
                if (
                    not self._detached_workers or run.status != "completed" or run.finalized_agent is not None
                    or run.worker_operation or self._worker_active_locked(run)
                    or run.workspaces_deleted or run.codex_homes_deleted
                    or any(other.workspace == run.workspace and other.created_at > run.created_at
                           for other in self._runs.values())
                    or (self.control_dir / run_id / "stop").exists()
                ):
                    raise PluginRunError("Retained execution recovery requires the original latest run to be idle and not stopped")
                self._require_persistent_worker_locked(run)
                run_root = self._validated_run_root(run)
                fingerprints = {}
                for idx in indices:
                    result = run.results.get(idx) or {}
                    if (result.get("status") not in {"failed", "error", "interrupted"}
                        or normalize_agent_role(result.get("role", run.agent_roles.get(idx))) != AGENT_ROLE_CANDIDATE
                        or idx in run.rejected_agents
                        or (self.control_dir / run_id / f"kill-agent-{idx:03d}").exists()):
                        raise PluginRunError(f"AGENT-{idx:03d} is not a recoverable unfinished candidate")
                    workspace = agent_execution_workspace(self._workspace_for_run(run), run_root / "workspaces", idx,
                        str(run.config.get("workspace_mode") or "isolated"))
                    try:
                        command, home, fingerprint = retained_execution_preparation(run_root, idx, workspace, run.prompt)
                    except (OSError, ValueError, TypeError) as exc:
                        raise PluginRunError(f"AGENT-{idx:03d} cannot resume its retained execution: {exc}") from exc
                    if (result.get("command") != command or result.get("codex_home") != str(home)
                        or result.get("workspace_dir") != str(workspace)):
                        raise PluginRunError(f"AGENT-{idx:03d} result and retained execution identity differ")
                    fingerprints[idx] = fingerprint
                context = _RunContext(threading.Event(), {idx: threading.Event() for idx in indices})
                validate_args(self._build_args(run, context, num_agents=len(indices)))
                checkpoint_path = self.state_dir / "workers" / f"{run_id}.json"
                checkpoint = read_json(checkpoint_path)
                if (
                    checkpoint.get("run_id") != run_id or checkpoint.get("pid") != run.worker_pid
                    or checkpoint.get("protocol") != WORKER_PROTOCOL_VERSION
                    or checkpoint.get("alive") is not True or checkpoint.get("active") is not False
                    or checkpoint.get("operation") or checkpoint.get("operation_id")
                    or checkpoint.get("last_operation") not in {"initial", "retry", "more"}
                    or not checkpoint.get("finished_at") or checkpoint.get("error") or checkpoint.get("expired")
                    or any((self.state_dir / "workers" / "requests").glob(f"{run_id}-*.json"))
                    or read_json(worker_candidate_requests_path(self.state_dir, run_id)).get("requests")
                    or self._finalization_journal_path(run).exists()
                ):
                    raise PluginRunError("The original batch worker has not durably reached an idle recovery boundary")
                old_pid = run.worker_pid
                self._append_event_locked(run, {"type": "execution_runtime_refresh_requested", "pid": old_pid,
                    "indices": indices, "fingerprints": fingerprints})
                shutdown = FileSignal(self.control_dir / run_id / "shutdown")
                shutdown.set()
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    stopped = read_json(checkpoint_path)
                    if (stopped.get("pid") == old_pid and stopped.get("alive") is False
                        and stopped.get("active") is False and stopped.get("stop_reason") == "shutdown"):
                        self._refresh_events_locked(run, save=False)
                        run.worker_pid, run.worker_state, run.worker_error = None, "stopped", ""
                        self._worker_processes.pop(run_id, None)
                        run.updated_at = _utc_now()
                        self._save_state_locked()
                        break
                    time.sleep(0.05)
                else:
                    shutdown.clear()
                    raise PluginRunError("The idle worker did not retire; no replacement or Agent was started")
                response = self._start_additional_batch(run, indices, set(indices),
                    retained_execution_fingerprints=fingerprints)
                response["recovery_operation_id"] = run.worker_operation_id
                return response

    def recover_synthesis_preparation(self, run_id: str, agents: Sequence[int], *, owner_retired=None) -> Dict[str, Any]:
        """Replace only an idle owner whose synthesis slots never reached Codex."""
        indices = list(agents)
        if not indices or any(type(idx) is not int or idx <= 0 for idx in indices) or len(set(indices)) != len(indices):
            raise PluginRunError("Recovery requires distinct positive synthesis indices")
        with self._run_operation_guard(run_id, "recover unsubmitted synthesis preparation"):
            with self._lock:
                run = self._require_run_locked(run_id)

                def bindings(current):
                    if (not self._detached_workers or current.status != "completed" or current.finalized_agent is not None
                        or current.finalizing_agent is not None or current.worker_operation or self._worker_active_locked(current)
                        or current.workspaces_deleted or current.codex_homes_deleted
                        or any(other.workspace == current.workspace and other.created_at > current.created_at for other in self._runs.values())
                        or (self.control_dir / run_id / "stop").exists() or self._finalization_journal_path(current).exists()):
                        raise PluginRunError("Synthesis preparation recovery requires the original idle, latest, unstopped run")
                    progress = RunProgressProjection()
                    progress.refresh(self._events, run_id, current.event_count)
                    sources = {idx: result_fingerprint(value) for idx, value in current.results.items()
                        if value.get("role") == AGENT_ROLE_CANDIDATE and value.get("status") == "success"}
                    if not sources or progress.cursor != current.event_count or sources != progress.candidate_results:
                        raise PluginRunError("The original synthesis inputs cannot be proven")
                    root = self._validated_run_root(current)
                    fingerprints = {}
                    for idx in indices:
                        value = current.results.get(idx) or {}
                        if (value.get("role") != AGENT_ROLE_SYNTHESIS or value.get("status") != "error"
                            or value.get("command") != [] or value.get("codex_thread_id")
                            or idx in current.rejected_agents or idx in current.stale_synthesis_agents
                            or (self.control_dir / run_id / f"kill-agent-{idx:03d}").exists()
                            or progress.synthesis_sources.get(idx) != sources
                            or progress.synthesis_generations.get(idx) != progress.candidate_generation
                            or progress.synthesis_results.get(idx) != result_fingerprint(value)):
                            raise PluginRunError(f"AGENT-{idx:03d} is not an unchanged, unsubmitted synthesis failure")
                        fingerprints[idx] = failed_synthesis_preparation_fingerprint(root, idx, current.prompt)
                    return fingerprints

                fingerprints = bindings(run)
                self._require_persistent_worker_locked(run)
                context = _RunContext(threading.Event(), {idx: threading.Event() for idx in indices})
                validate_args(self._build_args(run, context, num_agents=len(indices)))
                checkpoint_path = self.state_dir / "workers" / f"{run_id}.json"
                checkpoint = read_json(checkpoint_path)
                if (checkpoint.get("run_id") != run_id or checkpoint.get("pid") != run.worker_pid
                    or checkpoint.get("protocol") != WORKER_PROTOCOL_VERSION
                    or checkpoint.get("alive") is not True or checkpoint.get("active") is not False
                    or checkpoint.get("operation") or checkpoint.get("operation_id") or not checkpoint.get("finished_at")
                    or checkpoint.get("error") or checkpoint.get("expired")
                    or any((self.state_dir / "workers" / "requests").glob(f"{run_id}-*.json"))
                    or read_json(worker_candidate_requests_path(self.state_dir, run_id)).get("requests")):
                    raise PluginRunError("The original synthesis worker is not durably idle")
                if bindings(run) != fingerprints:
                    raise PluginRunError("Synthesis changed during recovery validation")
                identity = result_fingerprint({key: run.to_dict()[key] for key in (
                    "prompt", "config", "results", "conversation_id", "run_root", "artifact_token")})
                old_pid = run.worker_pid
                self._append_event_locked(run, {"type": "synthesis_preparation_runtime_refresh_requested",
                    "pid": old_pid, "indices": indices, "fingerprints": fingerprints})
                shutdown = FileSignal(self.control_dir / run_id / "shutdown")
                shutdown.set()
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    stopped = read_json(checkpoint_path)
                    if (stopped.get("pid") == old_pid and stopped.get("alive") is False
                        and stopped.get("active") is False and stopped.get("stop_reason") == "shutdown"):
                        self._refresh_events_locked(run, save=False)
                        run.worker_pid, run.worker_state, run.worker_error = None, "stopped", ""
                        self._worker_processes.pop(run_id, None)
                        run.updated_at = _utc_now()
                        self._save_state_locked()
                        break
                    time.sleep(0.05)
                else:
                    shutdown.clear()
                    raise PluginRunError("The idle synthesis owner did not retire; no replacement was started")
                # A daemon predating this handoff can still cache the old Popen.
                # Let it observe the retired owner before publishing the new PID.
                if owner_retired is not None:
                    owner_retired()
                run = self._require_run_locked(run_id)
                if (bindings(run) != fingerprints or identity != result_fingerprint({key: run.to_dict()[key] for key in (
                    "prompt", "config", "results", "conversation_id", "run_root", "artifact_token")})):
                    raise PluginRunError("The original synthesis changed while its owner retired")
                response = self._start_additional_batch(run, indices, set(indices), agent_role=AGENT_ROLE_SYNTHESIS,
                    synthesis_preparation_fingerprints=fingerprints)
                response["recovery_operation_id"] = run.worker_operation_id
                return response

    def resume_interrupted_batch(
        self, run_id: str, agents: Sequence[int], *, old_pid: int, old_operation_id: str,
    ) -> Dict[str, Any]:
        """Replace a verified dead supervisor without rebuilding accepted tasks."""
        indices = list(agents)
        if (type(old_pid) is not int or old_pid <= 0 or not isinstance(old_operation_id, str)
            or not re.fullmatch(re.escape(run_id) + r"-[A-Za-z0-9._-]+", old_operation_id)
            or not indices or any(type(idx) is not int or idx <= 0 for idx in indices)
            or len(set(indices)) != len(indices)):
            raise PluginRunError("Interrupted recovery requires exact owner and candidate identities")
        with self._run_operation_guard(run_id, "recover interrupted batch in place"):
            with self._lock:
                run = self._require_run_locked(run_id)
                checkpoint = read_json(self.state_dir / "workers" / f"{run_id}.json")
                control = self.control_dir / run_id
                if (
                    not self._detached_workers or run.status != "interrupted" or run.worker_state != "crashed"
                    or run.worker_pid is not None or run.worker_operation or run.finalized_agent is not None
                    or run.workspaces_deleted or run.codex_homes_deleted or pid_is_alive(old_pid)
                    or checkpoint.get("pid") != old_pid or checkpoint.get("run_id") != run_id
                    or checkpoint.get("protocol") != WORKER_PROTOCOL_VERSION
                    or checkpoint.get("operation_id") != old_operation_id
                    or checkpoint.get("operation") not in {"initial", "retry", "more"}
                    or checkpoint.get("active") is not True or checkpoint.get("expired")
                    or checkpoint.get("error") or (control / "stop").exists()
                    or any(other.workspace == run.workspace and other.created_at > run.created_at
                           for other in self._runs.values())
                    or read_json(worker_candidate_requests_path(self.state_dir, run_id)).get("requests")
                    or self._finalization_journal_path(run).exists()
                ):
                    raise PluginRunError("Interrupted recovery requires the original dead owner and no newer operation or stop")
                unfinished = {idx for idx, status in run.agent_statuses.items()
                    if normalize_agent_role(run.agent_roles.get(idx)) == AGENT_ROLE_CANDIDATE and status != "success"}
                if unfinished != set(indices):
                    raise PluginRunError("Interrupted recovery must cover exactly the unfinished original candidates")
                run_root = self._validated_run_root(run)
                fingerprints = {}
                for idx in indices:
                    result = run.results.get(idx)
                    if (idx in run.rejected_agents or (control / f"kill-agent-{idx:03d}").exists()
                        or (result is not None and result.get("status") not in {"failed", "error", "interrupted"})):
                        raise PluginRunError(f"AGENT-{idx:03d} is stopped, rejected or already finished")
                    workspace = agent_execution_workspace(self._workspace_for_run(run), run_root / "workspaces", idx,
                        str(run.config.get("workspace_mode") or "isolated"))
                    command, home, fingerprint = retained_execution_preparation(run_root, idx, workspace, run.prompt)
                    if result is not None and (result.get("command") != command
                        or result.get("codex_home") != str(home) or result.get("workspace_dir") != str(workspace)):
                        raise PluginRunError(f"AGENT-{idx:03d} result and original receipt differ")
                    fingerprints[idx] = fingerprint
                requests = list((self.state_dir / "workers" / "requests").glob(f"{run_id}-*.json"))
                if (len(requests) != 1 or requests[0].is_symlink()
                    or requests[0].stem != old_operation_id):
                    raise PluginRunError("The retired supervisor must have exactly its original in-flight request")
                request = read_json(requests[0])
                original = request.get("run") or {}
                if (request.get("protocol") != WORKER_PROTOCOL_VERSION
                    or request.get("operation_id") != old_operation_id
                    or request.get("operation") != checkpoint.get("operation")
                    or original.get("run_root") not in ({"", run.run_root}
                        if request.get("operation") == "initial" else {run.run_root})
                    or any(original.get(key) != run.to_dict().get(key)
                           for key in ("run_id", "workspace", "conversation_id", "prompt", "config", "artifact_token"))):
                    raise PluginRunError("The retired operation no longer belongs to this original task")
                validate_args(self._build_args(run, _RunContext(threading.Event(),
                    {idx: threading.Event() for idx in indices}), num_agents=len(indices)))
                if (control / "stop").exists():
                    raise PluginRunError("The user stopped this run during recovery preflight")
                archive = self.state_dir / "workers" / "retired" / old_operation_id
                archive.mkdir(parents=True, exist_ok=False)
                os.replace(requests[0], archive / requests[0].name)
                self._append_event_locked(run, {"type": "interrupted_execution_runtime_refresh",
                    "pid": old_pid, "operation_id": old_operation_id, "indices": indices,
                    "fingerprints": fingerprints, "retained_request": str(archive)})
                run.worker_state, run.worker_error = "stopped", ""
                self._worker_processes.pop(run_id, None)
                response = self._start_additional_batch(run, indices, set(indices),
                    retained_execution_fingerprints=fingerprints)
                response["recovery_operation_id"] = run.worker_operation_id
                return response

    def _retry_agent(
        self, run_id: str, agent: int, *, refresh_startup_runtime: bool = False,
    ) -> Dict[str, Any]:
        agent = int(agent)
        with self._lock:
            run = self._require_run_locked(run_id)
            if run.status in FINAL_RUN_STATES or run.finalized_agent is not None:
                raise PluginRunError(f"PCR run {run_id} is already {run.status}")
            result = run.results.get(agent)
            status = str(
                (result or {}).get("status")
                or run.agent_statuses.get(agent)
                or ""
            )
            if status not in {
                "failed",
                "error",
                "killed",
                "cancelled",
                "interrupted",
            }:
                raise PluginRunError(
                    f"AGENT-{agent:03d} cannot be retried from status {status or 'unknown'}"
                )
            role = normalize_agent_role(
                (result or {}).get("role", run.agent_roles.get(agent))
            )
            fingerprint = None
            retained_executions = None
            if refresh_startup_runtime:
                fingerprint = self._retire_failed_startup_worker_locked(run, agent)
            elif role == AGENT_ROLE_CANDIDATE and status in {"failed", "error", "interrupted"} and run.run_root:
                root = self._validated_run_root(run)
                receipt_path = root / "meta" / f"agent_{agent:03d}" / "execution_recovery.json"
                receipt = read_json(receipt_path)
                pid = receipt.get("native_pid")
                if type(pid) is int and pid > 0 and pid_is_alive(pid):
                    raise PluginRunError(f"AGENT-{agent:03d} still has a native execution owner; retry would duplicate its input")
                if receipt_path.exists() and receipt.get("stage") != "ready":
                    workspace = agent_execution_workspace(self._workspace_for_run(run), root / "workspaces", agent,
                        str(run.config.get("workspace_mode") or "isolated"))
                    try:
                        _, _, retained = retained_execution_preparation(root, agent, workspace, run.prompt)
                    except (OSError, ValueError, TypeError) as exc:
                        # An accepted task is never silently converted into a
                        # fresh workspace/full-prompt retry when resume is unsafe.
                        raise PluginRunError(f"AGENT-{agent:03d} retained execution cannot safely resume: {exc}") from exc
                    retained_executions = {agent: retained}
            return self._start_additional_batch(
                run,
                [agent],
                {agent},
                agent_role=role,
                retained_startup_fingerprint=fingerprint,
                retained_execution_fingerprints=retained_executions,
            )

    def _retire_failed_startup_worker_locked(self, run: ManagedRun, agent: int) -> str:
        if (
            not self._detached_workers or run.status != "completed"
            or run.worker_operation or self._worker_active_locked(run)
            or set(run.results) != {agent} or set(run.agent_statuses) != {agent}
            or run.config.get("workspace_mode") != "source"
            or not run.config.get("resume_session_id")
            or int(run.config.get("synthesis_agents") or 0)
        ):
            raise PluginRunError("Only a completed, pre-submission source resume can refresh its startup runtime")
        self._require_persistent_worker_locked(run)
        checkpoint_path = self.state_dir / "workers" / f"{run.run_id}.json"
        checkpoint = read_json(checkpoint_path)
        if (
            checkpoint.get("run_id") != run.run_id or checkpoint.get("pid") != run.worker_pid
            or checkpoint.get("protocol") != WORKER_PROTOCOL_VERSION
            or checkpoint.get("alive") is not True or checkpoint.get("active") is not False
            or checkpoint.get("operation") or checkpoint.get("operation_id")
            or checkpoint.get("last_operation") != "initial" or not checkpoint.get("finished_at")
            or checkpoint.get("error") or checkpoint.get("expired")
            or any((self.state_dir / "workers" / "requests").glob(f"{run.run_id}-*.json"))
            or read_json(worker_candidate_requests_path(self.state_dir, run.run_id)).get("requests")
            or self._finalization_journal_path(run).exists()
        ):
            raise PluginRunError("The original worker has not durably reached an idle startup failure")
        try:
            _command, _home, fingerprint = retained_resume_preparation(
                self._validated_run_root(run), agent, self._workspace_for_run(run),
                str(run.config["resume_session_id"]), run.prompt,
            )
            validate_args(self._build_args(run, _RunContext(threading.Event(), {agent: threading.Event()}), num_agents=1))
        except (OSError, ValueError, SystemExit) as exc:
            raise PluginRunError(f"Original startup could not be safely recovered: {exc}") from exc
        old_pid = run.worker_pid
        self._append_event_locked(run, {"type": "startup_runtime_refresh_requested", "idx": agent, "pid": old_pid})
        shutdown = FileSignal(self.control_dir / run.run_id / "shutdown")
        shutdown.set()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            stopped = read_json(checkpoint_path)
            if stopped.get("pid") == old_pid and stopped.get("alive") is False and stopped.get("active") is False:
                if stopped.get("stop_reason") != "shutdown":
                    raise PluginRunError("The idle worker did not acknowledge the requested retirement")
                # Its terminal event is durable before the idle checkpoint.
                # Consume it now, before marking the replacement operation busy.
                self._refresh_events_locked(run, save=False)
                run.worker_pid = None
                run.worker_state = "stopped"
                run.worker_error = ""
                self._worker_processes.pop(run.run_id, None)
                run.updated_at = _utc_now()
                self._save_state_locked()
                return fingerprint
            time.sleep(0.05)
        shutdown.clear()
        raise PluginRunError("The idle worker did not retire; no replacement or Agent was started")

    def _record_finalization(
        self,
        run: ManagedRun,
        result: AgentResult,
    ) -> None:
        if not run.run_root:
            return
        run_root = self._validated_run_root(run)
        summary_path = run_root / "summary.json"
        if summary_path.is_symlink():
            payload = {}
        else:
            try:
                payload = json.loads(summary_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                payload = {}
        if not isinstance(payload, dict):
            payload = {}
        payload["selected_agent"] = f"agent_{result.idx:03d}"
        payload["plugin_finalized_agent"] = f"agent_{result.idx:03d}"
        payload["synced_back_to_workspace"] = (
            run.workspace if run.synced_back else None
        )
        payload["workspaces_deleted"] = run.workspaces_deleted
        payload["codex_homes_deleted"] = run.codex_homes_deleted
        payload["codex_session_promotion"] = run.session_promotion
        write_json_atomic(summary_path, payload)
        write_text_atomic(
            run_root / "SELECTED_AGENT.txt",
            f"agent_{result.idx:03d}\n",
        )
        write_text_atomic(
            run_root / "FINAL_RESULT_WORKSPACE.txt",
            (run.workspace if run.synced_back else "") + "\n",
        )
        if run.session_promotion is not None:
            write_json_atomic(
                run_root / "codex_session_promotion.json",
                run.session_promotion,
            )

    def _workspaces_root_for_run(self, run: ManagedRun) -> Path:
        try:
            return self._artifacts.workspaces_root(run)
        except ArtifactError as exc:
            raise PluginRunError(str(exc)) from exc

    def accept_agent(
        self,
        run_id: str,
        agent: int,
        wait_seconds: float = 45.0,
        *,
        keep_workspaces: Optional[bool] = None,
        sync_back: Optional[bool] = None,
    ) -> Dict[str, Any]:
        if keep_workspaces is not None and not isinstance(keep_workspaces, bool):
            raise PluginRunError("keep_workspaces must be true, false, or null")
        if sync_back is not None and not isinstance(sync_back, bool):
            raise PluginRunError("sync_back must be true, false, or null")
        operation = f"finalization of AGENT-{int(agent):03d}"
        try:
            with RunOperationLock(self.locks_dir, run_id, operation):
                with self._lock:
                    run = self._require_run_locked(run_id)
                    self._claim_run_operation_locked(run_id, operation)
                    if keep_workspaces is not None:
                        run.config["keep_workspaces"] = keep_workspaces
                    if sync_back is not None:
                        run.config["sync_back"] = sync_back
                    if keep_workspaces is not None or sync_back is not None:
                        run.updated_at = _utc_now()
                        self._save_state_locked()
                    cleanup_only = (
                        run.status == "cleanup_failed"
                        and run.finalized_agent == int(agent)
                        and not self._worker_active_locked(run)
                    )
                    recover_completed_result = (
                        self._completed_result_recovery_available_locked(run)
                    )
                try:
                    if (
                        self._uses_persistent_worker(run)
                        and not cleanup_only
                        and not recover_completed_result
                    ):
                        return self._accept_agent_via_worker(
                            run_id,
                            agent,
                            wait_seconds,
                        )
                    return self._accept_agent(
                        run_id,
                        agent,
                        wait_seconds,
                        stop_active=not cleanup_only,
                    )
                finally:
                    with self._lock:
                        self._release_run_operation_locked(run_id, operation)
        except RunOperationBusyError as exc:
            raise PluginRunPendingError(str(exc)) from exc
        except RuntimeError as exc:
            raise PluginRunError(str(exc)) from exc

    def _accept_agent_via_worker(
        self,
        run_id: str,
        agent: int,
        wait_seconds: float,
    ) -> Dict[str, Any]:
        agent = int(agent)
        with self._lock:
            run = self._require_run_locked(run_id)
            if run.status == "sync_ambiguous":
                raise PluginRunError(
                    "Sync-back completion is ambiguous. Inspect the workspace and call "
                    "pcr_recover_finalization before accepting again."
                )
            if run.finalized_agent is not None:
                if run.finalized_agent != agent:
                    raise PluginRunError(
                        f"PCR run {run_id} already finalized "
                        f"AGENT-{run.finalized_agent:03d}"
                    )
                if run.status == "finalized":
                    return self._run_summary_locked(run)
            if self._result_operation_pending_locked(run):
                saving_agent = run.finalizing_agent or run.finalized_agent
                if saving_agent is not None and saving_agent != agent:
                    raise PluginRunNotAppliedError("A different Agent is being saved for this conversation",
                                                   error_code="continuation_source_changed")
                summary = self._run_summary_locked(run)
                summary["operation_pending"] = True
                return summary
            value = run.results.get(agent)
            if not isinstance(value, dict) or value.get("status") != "success":
                raise PluginRunError(
                    f"AGENT-{agent:03d} has not finished successfully"
                )
            self._require_agent_workspace_for_action(
                run,
                agent,
                str(value.get("workspace_dir") or "") or None,
            )
            operation_active = self._worker_executing_agents_locked(run)
        if operation_active:
            self.stop_run(run_id, wait_seconds=wait_seconds)
        with self._lock:
            run = self._require_run_locked(run_id)
            if run.finalized_agent is not None:
                if run.finalized_agent != agent:
                    raise PluginRunError(
                        f"PCR run {run_id} already finalized "
                        f"AGENT-{run.finalized_agent:03d}"
                    )
                if run.status == "finalized":
                    return self._run_summary_locked(run)
            if self._worker_active_locked(run):
                raise PluginRunPendingError(
                    "The previous operation is still finishing. The continuation can be retried after it finishes."
                )
            self._require_persistent_worker_locked(run)
            previous_selection = run.finalizing_agent
            previous_status = run.status
            previous_error = run.error
            # Reserve the chosen result before any diff preservation or sync.
            # A concurrent continuation must join this save, not stop its worker.
            run.finalizing_agent = agent
            run.status = "finalizing"
            run.error = ""
            try:
                operation_id = self._queue_persistent_operation_locked(
                    run, "finalize", {"agent": agent}
                )
            except Exception:
                run.finalizing_agent = previous_selection
                run.status = previous_status
                run.error = previous_error
                self._save_state_locked()
                raise
        response = self._wait_for_persistent_operation(
            run_id,
            operation_id,
            max(0.0, float(wait_seconds)),
        )
        with self._lock:
            run = self._require_run_locked(run_id)
            summary = self._run_summary_locked(run)
        if response is None:
            summary["operation_pending"] = True
        return summary

    def recover_finalization(
        self,
        run_id: str,
        sync_was_applied: bool | None,
    ) -> Dict[str, Any]:
        operation = "finalization recovery"
        try:
            with RunOperationLock(self.locks_dir, run_id, operation), ExitStack() as recovery_leases:
                with self._lock:
                    run = self._require_run_locked(run_id)
                    if run.status != "sync_ambiguous" or run.finalized_agent is None:
                        raise PluginRunError(
                            f"PCR run {run_id} does not have an ambiguous sync-back"
                        )
                    use_worker = (
                        self._uses_persistent_worker(run)
                        and self._worker_available_locked(run)
                    )
                    self._claim_run_operation_locked(run_id, operation)
                try:
                    if sync_was_applied is None:
                        recovery_leases.enter_context(source_workspace_lease(self._workspace_for_run(run)))
                        journal_path = self._finalization_journal_path(run)
                        journal = read_json(journal_path)
                        proof = read_json(journal_path.with_name("sync-proof.json"))
                        if (not journal.get("sync_proof_id")
                                or proof.get("id") != journal.get("sync_proof_id")
                                or proof.get("agent") != run.finalized_agent
                                or proof.get("run_id") != run_id):
                            raise PluginRunError("This interrupted save has no verification record. "
                                                 "Your files were not changed; inspect the result before manual recovery.")
                        try:
                            verify_sync_receipt(proof.get("receipt", {}), self._workspace_for_run(run))
                        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
                            raise PluginRunError(str(exc)) from exc
                        sync_was_applied = True
                    if use_worker:
                        with self._lock:
                            operation_id = self._queue_persistent_operation_locked(
                                run,
                                "recover_finalization",
                                {"sync_was_applied": bool(sync_was_applied)},
                            )
                        response = self._wait_for_persistent_operation(
                            run_id,
                            operation_id,
                            45.0,
                        )
                        with self._lock:
                            run = self._require_run_locked(run_id)
                            summary = self._run_summary_locked(run)
                        if response is None:
                            summary["operation_pending"] = True
                        return summary
                    return self._recover_finalization(run_id, sync_was_applied)
                finally:
                    with self._lock:
                        self._release_run_operation_locked(run_id, operation)
        except RuntimeError as exc:
            raise PluginRunError(str(exc)) from exc

    def _recover_finalization(
        self,
        run_id: str,
        sync_was_applied: bool,
    ) -> Dict[str, Any]:
        with self._lock:
            run = self._require_run_locked(run_id)
            if run.status != "sync_ambiguous" or run.finalized_agent is None:
                raise PluginRunError(
                    f"PCR run {run_id} does not have an ambiguous sync-back"
                )
            agent = run.finalized_agent
            if sync_was_applied:
                self._write_finalization_journal(
                    run,
                    phase="sync_complete",
                    agent=agent,
                    synced_back=True,
                    recovered=True,
                )
                run.synced_back = True
                run.status = "cleanup_failed"
                run.error = (
                    "Sync-back was confirmed. Call pcr_accept_agent for the "
                    "same Agent to finish cleanup without syncing again."
                )
            else:
                self._write_finalization_journal(
                    run,
                    phase="sync_not_applied",
                    agent=agent,
                    synced_back=False,
                    recovered=True,
                )
                run.finalized_agent = None
                run.synced_back = False
                run.status = "completed"
                run.error = ""
            run.updated_at = _utc_now()
            self._append_event_locked(
                run,
                {
                    "type": "finalization_recovered",
                    "idx": agent,
                    "sync_was_applied": bool(sync_was_applied),
                },
            )
            self._save_state_locked()
            return self._run_summary_locked(run)

    def _persist_completed_diffs_locked(self, run: ManagedRun) -> None:
        try:
            self._artifacts.persist_successful_diffs(run)
        except ArtifactError as exc:
            raise PluginRunError(
                f"Cannot preserve completed Agent diffs before cleanup: {exc}"
            ) from exc

    def _accept_agent(
        self,
        run_id: str,
        agent: int,
        wait_seconds: float,
        *,
        stop_active: bool = True,
    ) -> Dict[str, Any]:
        agent = int(agent)
        with self._lock:
            run = self._require_run_locked(run_id)
            if run.status == "sync_ambiguous":
                raise PluginRunError(
                    "Sync-back completion is ambiguous. Inspect the workspace and call "
                    "pcr_recover_finalization before accepting again."
                )
            if run.finalized_agent is not None:
                if run.finalized_agent != agent:
                    raise PluginRunError(
                        f"PCR run {run_id} already finalized "
                        f"AGENT-{run.finalized_agent:03d}"
                    )
                if run.status == "finalized":
                    return self._run_summary_locked(run)
            if stop_active and self._result_operation_pending_locked(run):
                raise PluginRunPendingError(
                    "The previous result is being saved. The continuation can be retried after it finishes."
                )
            value = run.results.get(agent)
            if not isinstance(value, dict) or value.get("status") != "success":
                raise PluginRunError(
                    f"AGENT-{agent:03d} has not finished successfully"
                )
            worker_active = self._worker_executing_agents_locked(run)
        if worker_active and stop_active:
            self.stop_run(run_id, wait_seconds=wait_seconds)
            with self._lock:
                run = self._require_run_locked(run_id)
                if self._worker_active_locked(run):
                    raise PluginRunPendingError(
                        "The previous operation is still finishing. The continuation can be retried after it finishes."
                    )

        with self._lock:
            run = self._require_run_locked(run_id)
            value = run.results.get(agent)
            if not isinstance(value, dict) or value.get("status") != "success":
                raise PluginRunError(
                    f"AGENT-{agent:03d} is no longer available for finalization"
                )
            result = AgentResult(**value)
            sync_back = bool(run.config.get("sync_back", True))
            keep_workspaces = bool(run.config.get("keep_workspaces", False))
            workspace = self._workspace_for_run(run)
            workspaces_root = self._workspaces_root_for_run(run)
            journal = read_json(self._finalization_journal_path(run))
            already_synced = (
                str(journal.get("phase") or "")
                in {"sync_complete", "cleanup_started", "complete"}
                or (
                    run.status == "cleanup_failed"
                    and run.finalized_agent == agent
                    and run.synced_back
                )
            )
            promotion_data = run.session_promotion
            if not already_synced:
                candidate = self._require_agent_workspace_for_action(
                    run,
                    agent,
                    result.workspace_dir,
                )
            else:
                candidate = None
            if not already_synced:
                run.finalizing_agent = agent
                run.completion_reason = "finalization"
                run.status = "finalizing"
                run.error = ""
                run.updated_at = _utc_now()
                self._save_state_locked()
            artifact_snapshot = ManagedRun.from_dict(run.to_dict())

        # Preservation is expensive filesystem work. RunOperationLock still
        # protects adoption; readers and unrelated commands need not wait.
        finalization_timings: Dict[str, float] = {}

        def record_phase(name: str, seconds: Optional[float]) -> None:
            if seconds is not None:
                finalization_timings[name] = seconds
            self._record_progress(run_id, {"type": "run_phase", "phase": name,
                                          "phase_timings": dict(finalization_timings)})

        record_phase("result_preservation", None)
        preservation_started = time.monotonic()
        try:
            self._persist_completed_diffs_locked(artifact_snapshot)
        except PluginRunError as exc:
            with self._lock:
                current = self._require_run_locked(run_id)
                if not already_synced:
                    current.finalized_agent = None
                    current.status = "completed"
                else:
                    current.status = "cleanup_failed"
                current.error = str(exc)
                self._save_state_locked()
            raise
        finally:
            record_phase("result_preservation", time.monotonic() - preservation_started)

        if not already_synced:
            with self._lock:
                run = self._require_run_locked(run_id)
            self._write_finalization_journal(
                run,
                phase="sync_started" if sync_back else "sync_complete",
                agent=agent,
                synced_back=False,
                sync_proof_id=None,
            )
            with self._lock:
                run.finalized_agent = agent
                run.status = "finalizing"
                run.error = ""
                run.updated_at = _utc_now()
                self._save_state_locked()

        def after_sync() -> None:
            self._write_finalization_journal(
                run,
                phase="sync_complete",
                agent=agent,
                synced_back=True,
            )

        def sync_with_receipt(src: Path, dst: Path) -> None:
            proof_id = uuid.uuid4().hex
            try:
                receipt = capture_sync_receipt(src, dst)
                write_json_atomic(self._finalization_journal_path(run).with_name("sync-proof.json"),
                                  {"id": proof_id, "run_id": run_id, "agent": agent, "receipt": receipt})
                self._write_finalization_journal(run, phase="sync_started", agent=agent,
                                                 sync_proof_id=proof_id)
            except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
                raise WorkspaceSyncPreflightError(f"Could not record save verification: {exc}") from exc
            sync_best_workspace_back(src, dst)

        def after_promotion(value: dict[str, Any] | None) -> None:
            nonlocal promotion_data
            promotion_data = value
            with self._lock:
                current = self._require_run_locked(run_id)
                current.finalized_agent = agent
                current.synced_back = sync_back
                current.session_promotion = promotion_data
                current.status = "finalizing"
                current.error = ""
                current.updated_at = _utc_now()
                self._append_event_locked(
                    current,
                    {
                        "type": "agent_selected_for_finalization",
                        "idx": agent,
                        "synced_back": sync_back,
                        "session_promotion": promotion_data,
                    },
                )
                self._save_state_locked()

        def before_cleanup() -> None:
            self._write_finalization_journal(
                run,
                phase="cleanup_started",
                agent=agent,
                synced_back=sync_back,
            )

        try:
            outcome = finalize_candidate_workspace(
                result,
                candidate_workspace=candidate,
                workspace=workspace,
                workspaces_root=workspaces_root,
                promote_session=promote_best_codex_session_to_workspace,
                sync_back=sync_back,
                keep_workspaces=keep_workspaces,
                already_synced=already_synced,
                existing_promotion=promotion_data,
                sync_workspace=sync_with_receipt,
                cleanup_workspaces=cleanup_workspace_copies,
                cleanup_codex_homes=lambda: self._artifacts.remove_codex_homes(run),
                after_sync=after_sync,
                after_promotion=after_promotion,
                before_cleanup=before_cleanup,
                phase_callback=record_phase,
            )
        except FinalizationStageError as failure:
            exc = failure.cause
            if failure.stage == "sync_preflight":
                with self._lock:
                    run = self._require_run_locked(run_id)
                    run.error = (
                        f"Sync-back preflight check failed for AGENT-{agent:03d}: {exc}. "
                        "The workspace was not modified."
                    )
                    run.updated_at = _utc_now()
                    self._append_event_locked(
                        run,
                        {
                            "type": "finalize_failed",
                            "idx": agent,
                            "message": str(exc),
                            "stage": "sync_preflight",
                        },
                    )
                    self._save_state_locked()
                raise PluginRunError(
                    f"AGENT-{agent:03d} sync-back preflight failed (workspace untouched): {exc}",
                    error_code="workspace_sync_preflight_failed",
                ) from exc
            if failure.stage == "sync":
                with self._lock:
                    run = self._require_run_locked(run_id)
                    run.status = "sync_ambiguous"
                    run.error = (
                        f"Sync-back raised an error after it started: {exc}. Inspect "
                        "the workspace and recover the finalization explicitly."
                    )
                    run.updated_at = _utc_now()
                    self._append_event_locked(
                        run,
                        {
                            "type": "finalize_failed",
                            "idx": agent,
                            "message": str(exc),
                        },
                    )
                    self._save_state_locked()
                raise PluginRunError(
                    f"Cannot confirm whether AGENT-{agent:03d} sync-back completed: {exc}"
                ) from exc
            raise PluginRunError(
                f"AGENT-{agent:03d} finalization failed during {failure.stage}: {exc}"
            ) from exc

        promotion_data = outcome.session_promotion
        deleted = outcome.workspaces_deleted
        codex_homes_deleted = outcome.codex_homes_deleted

        with self._lock:
            run = self._require_run_locked(run_id)
            run.finalized_agent = agent
            run.synced_back = sync_back
            run.workspaces_deleted = deleted
            run.codex_homes_deleted = codex_homes_deleted
            run.cleanup_error = outcome.cleanup_error
            run.session_promotion = promotion_data
            run.status = "finalized"
            run.error = ""
            run.updated_at = _utc_now()
            self._append_event_locked(
                run,
                {
                    "type": "agent_finalized",
                    "idx": agent,
                    "synced_back": sync_back,
                    "workspaces_deleted": deleted,
                    "codex_homes_deleted": codex_homes_deleted,
                    "cleanup_error": outcome.cleanup_error,
                    "session_promotion": promotion_data,
                },
            )
            try:
                self._record_finalization(run, result)
            except Exception as exc:
                run.error = f"Finalized, but could not update run summary files: {exc}"
                self._append_event_locked(
                    run,
                    {
                        "type": "finalization_metadata_failed",
                        "idx": agent,
                        "message": str(exc),
                    },
                )
            self._write_finalization_journal(
                run,
                phase="complete",
                agent=agent,
                synced_back=sync_back,
                workspaces_deleted=deleted,
                codex_homes_deleted=codex_homes_deleted,
                cleanup_error=outcome.cleanup_error,
            )
            self._save_state_locked()
            self._start_cleanup_worker_locked()
            return self._run_summary_locked(run)

    def discard_run(
        self,
        run_id: str,
        keep_workspaces: bool = False,
        wait_seconds: float = 30.0,
    ) -> Dict[str, Any]:
        operation = "discard"
        try:
            with RunOperationLock(self.locks_dir, run_id, operation):
                with self._lock:
                    run = self._require_run_locked(run_id)
                    self._claim_run_operation_locked(run_id, operation)
                try:
                    if self._uses_persistent_worker(run) and self._worker_available_locked(run):
                        return self._discard_run_via_worker(
                            run_id,
                            keep_workspaces,
                            wait_seconds,
                        )
                    return self._discard_run(run_id, keep_workspaces, wait_seconds)
                finally:
                    with self._lock:
                        self._release_run_operation_locked(run_id, operation)
        except RuntimeError as exc:
            raise PluginRunError(str(exc)) from exc

    def _discard_run_via_worker(
        self,
        run_id: str,
        keep_workspaces: bool,
        wait_seconds: float,
    ) -> Dict[str, Any]:
        with self._lock:
            run = self._require_run_locked(run_id)
            if run.status == "finalized" or run.finalized_agent is not None:
                raise PluginRunError(
                    f"PCR run {run_id} already selected an Agent; use accept again "
                    "to retry cleanup"
                )
            if run.status == "discarded" and (
                keep_workspaces or run.workspaces_deleted
            ):
                return self._run_summary_locked(run)
            operation_active = self._worker_active_locked(run)
        if operation_active:
            self.stop_run(run_id, wait_seconds=wait_seconds)
        with self._lock:
            run = self._require_run_locked(run_id)
            if run.status == "discarded" and (
                keep_workspaces or run.workspaces_deleted
            ):
                return self._run_summary_locked(run)
            if run.status == "finalized" or run.finalized_agent is not None:
                raise PluginRunError(
                    f"PCR run {run_id} already selected an Agent; use accept again "
                    "to retry cleanup"
                )
            if self._worker_active_locked(run):
                raise PluginRunError(
                    "Run is still stopping; discard it again after all Agents stop"
                )
            operation_id = self._queue_persistent_operation_locked(
                run,
                "discard",
                {"keep_workspaces": bool(keep_workspaces)},
            )
        response = self._wait_for_persistent_operation(
            run_id,
            operation_id,
            max(0.0, float(wait_seconds)),
        )
        with self._lock:
            run = self._require_run_locked(run_id)
            summary = self._run_summary_locked(run)
        if response is None:
            summary["operation_pending"] = True
        return summary

    def _discard_run(
        self,
        run_id: str,
        keep_workspaces: bool,
        wait_seconds: float,
        *,
        stop_active: bool = True,
    ) -> Dict[str, Any]:
        with self._lock:
            existing = self._require_run_locked(run_id)
            if existing.status == "finalized" or existing.finalized_agent is not None:
                raise PluginRunError(
                    f"PCR run {run_id} already selected an Agent; use accept again "
                    "to retry cleanup"
                )
            if existing.status == "discarded" and (
                keep_workspaces or existing.workspaces_deleted
            ):
                return self._run_summary_locked(existing)
        if stop_active:
            self.stop_run(run_id, wait_seconds=wait_seconds)
        with self._lock:
            run = self._require_run_locked(run_id)
            if stop_active and self._worker_active_locked(run):
                raise PluginRunError(
                    "Run is still stopping; discard it again after all Agents stop"
                )
            workspace = self._workspace_for_run(run)
            workspaces_root = (
                self._workspaces_root_for_run(run)
                if run.run_root
                else None
            )
            if run.run_root:
                self._persist_completed_diffs_locked(run)
        deleted = bool(
            not keep_workspaces
            and (workspaces_root is None or not workspaces_root.exists())
        )
        if not keep_workspaces and workspaces_root is not None:
            if workspaces_root.exists():
                try:
                    cleanup_workspace_copies(workspace, workspaces_root)
                    deleted = not workspaces_root.exists()
                except Exception as exc:
                    raise PluginRunError(
                        f"Cannot clean candidate workspaces: {exc}"
                    ) from exc
        codex_homes_deleted = False
        if not keep_workspaces and run.run_root:
            try:
                codex_homes_deleted = self._artifacts.remove_codex_homes(run)
            except ArtifactError as exc:
                raise PluginRunError(str(exc)) from exc
        with self._lock:
            run = self._require_run_locked(run_id)
            run.status = "discarded"
            run.error = ""
            run.worker_error = ""
            run.workspaces_deleted = deleted
            run.codex_homes_deleted = codex_homes_deleted
            run.updated_at = _utc_now()
            self._append_event_locked(
                run,
                {
                    "type": "run_discarded",
                    "workspaces_deleted": deleted,
                    "codex_homes_deleted": codex_homes_deleted,
                    "workspaces_retained": bool(keep_workspaces),
                },
            )
            self._save_state_locked()
            return self._run_summary_locked(run)

    def continue_from_agent(
        self,
        run_id: str,
        agent: int,
        prompt: str,
        num_agents: int | None = None,
        max_parallel: int | None = None,
        config_overrides: Optional[Dict[str, Any]] = None,
        confirm_large_run: bool = False,
        submission_command: Optional[Dict[str, str]] = None,
        require_unstarted_continuation: bool = False,
    ) -> Dict[str, Any]:
        # Adoption uses the ordinary run lock in its worker. Keep this distinct
        # lock across proof and child creation so all continuation entry points
        # serialize without preventing that worker from finishing its save.
        key = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
        try:
            with RunOperationLock(self.locks_dir, f"continuation-{key}", "continue"):
                if require_unstarted_continuation and not self.can_retry_unstarted_continuation(run_id, agent):
                    raise PluginRunError("The original continuation can no longer be proven unstarted")
                return self._continue_from_agent(
                    run_id, agent, prompt, num_agents=num_agents, max_parallel=max_parallel,
                    config_overrides=config_overrides, confirm_large_run=confirm_large_run,
                    submission_command=submission_command,
                )
        except RunOperationBusyError as exc:
            raise PluginRunPendingError(str(exc)) from exc

    def _continue_from_agent(
        self,
        run_id: str,
        agent: int,
        prompt: str,
        num_agents: int | None = None,
        max_parallel: int | None = None,
        config_overrides: Optional[Dict[str, Any]] = None,
        confirm_large_run: bool = False,
        submission_command: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        prompt = prompt.strip()
        if not prompt:
            raise PluginRunError("Prompt must not be empty")
        with self._lock:
            run = self._require_run_locked(run_id)
            if not bool(run.config.get("sync_back", True)):
                raise PluginRunError(
                    "Cannot continue from an Agent when sync-back is disabled"
                )
            result = run.results.get(int(agent))
            if not isinstance(result, dict) or result.get("status") != "success":
                raise PluginRunError(
                    f"AGENT-{int(agent):03d} has not finished successfully"
                )
            if (run.finalizing_agent is not None and run.status not in {"finalizing", "finalized"}
                    and not self._worker_active_locked(run)):
                raise PluginRunNotAppliedError(
                    run.error or "The previous result could not finish saving safely",
                    error_code="finalization_blocked",
                )
            if self._result_operation_pending_locked(run):
                saving_agent = run.finalizing_agent or run.finalized_agent
                if saving_agent is not None and saving_agent != int(agent):
                    raise PluginRunNotAppliedError(
                        "A different Agent is being saved for this conversation",
                        error_code="continuation_source_changed",
                    )
                raise PluginRunPendingError(
                    "Agent finalization is still running. The continuation can be retried after it finishes."
                )
            already_finalized = run.finalized_agent is not None
            resume_codex_home = None
            if already_finalized:
                if run.finalized_agent != int(agent):
                    raise PluginRunNotAppliedError(
                        f"PCR run {run_id} already finalized "
                        f"AGENT-{run.finalized_agent:03d}",
                        error_code="continuation_source_changed",
                    )
                if run.status == "finalizing" or (
                    run.worker_operation == "finalize" and self._worker_active_locked(run)
                ):
                    raise PluginRunPendingError(
                        "Agent finalization is still running. The continuation "
                        "can be retried after it finishes."
                    )
                if run.status != "finalized":
                    raise PluginRunNotAppliedError(
                        "Resolve the previous finalization before continuing",
                        error_code="finalization_blocked",
                        user_message="The previous result has not finished saving safely. Your message is retained.",
                    )
                promotion = (
                    run.session_promotion
                    if isinstance(run.session_promotion, dict)
                    else {}
                )
                session_id = str(promotion.get("session_id") or "").strip() or None
                if not run.synced_back or not session_id:
                    raise PluginRunNotAppliedError(
                        f"AGENT-{int(agent):03d} does not have a promoted Codex "
                        "session to continue", error_code="finalization_blocked",
                    )
                workspace = str(self._workspace_for_run(run))
            else:
                self._require_agent_workspace_for_action(
                    run,
                    int(agent),
                    str(result.get("workspace_dir") or "") or None,
                )
                if (
                    self._uses_persistent_worker(run)
                    and not self._completed_result_recovery_available_locked(run)
                ):
                    self._require_persistent_worker_locked(run)
                session_id = str(result.get("codex_thread_id") or "").strip() or None
                if session_id:
                    # This session is still private to the candidate until
                    # adoption; searching the user's Codex home cannot find it.
                    resume_codex_home = self._agent_meta_file(run, int(agent), "codex_home")
                workspace = run.workspace
            config = dict(run.config)
            overrides = dict(config_overrides or {})
            allowed_overrides = {
                "supports_agent_questions",
                "max_parallel",
                "serial",
                "recommend_by",
                "model",
                "effort",
                "fast",
                "synthesis_agents",
                "synthesis_model",
                "synthesis_effort",
                "synthesis_fast",
                "follow_up_delay",
                "runs_dir",
                "codex_bin",
                "sync_back",
                "keep_workspaces",
                "subagents",
                "subagents_limit",
            }
            unknown = sorted(set(overrides).difference(allowed_overrides))
            if unknown:
                raise PluginRunError(
                    "Unsupported continuation settings: " + ", ".join(unknown)
                )
            config.update(overrides)
            target_agents = int(
                num_agents or config.get("num_agents") or DEFAULT_NUM_AGENTS
            )
            target_serial = bool(config.get("serial"))
            target_max_parallel = (
                1
                if target_serial
                else (
                    config.get("max_parallel")
                    if "max_parallel" in config
                    else max_parallel
                )
            )
            if max_parallel is not None and "max_parallel" not in overrides:
                target_max_parallel = max_parallel
            conversation_id = (
                run.conversation_id
                or str(run.config.get("resume_session_id") or "").strip()
                or session_id
                or run.run_id
            )
            turn_index = max(0, int(run.turn_index)) + 1
            source_prompt = run.prompt
        validation_config = dict(config)
        validation_config.update(
            {
                "num_agents": target_agents,
                "max_parallel": target_max_parallel,
                "serial": target_serial,
                "resume_session_id": session_id,
            }
        )
        validation_run = ManagedRun(
            run_id=f"{run_id}-continuation-validation",
            prompt=prompt,
            workspace=workspace,
            config=validation_config,
        )
        validation_context = _RunContext(
            cancel_event=threading.Event(),
            agent_cancel_events={},
        )
        try:
            validate_args(self._build_args(validation_run, validation_context))
        except SystemExit as exc:
            raise PluginRunError(str(exc)) from exc
        storage = self.estimate(
            workspace,
            target_agents,
            config.get("runs_dir"),
            session_id,
            int(config.get("synthesis_agents") or 0),
            resume_codex_home=resume_codex_home,
        )
        if storage["confirmation_required"] and not confirm_large_run:
            return {
                "status": "confirmation_required",
                "message": (
                    f"The continuation is estimated to use {storage['total']}. "
                    + (
                        "Confirm before starting it."
                        if already_finalized
                        else "Confirm before finalizing the current Agent and starting it."
                    )
                ),
                "storage": storage,
            }
        if already_finalized:
            with self._lock:
                current_run = self._require_run_locked(run_id)
                if (
                    current_run.status != "finalized"
                    or current_run.finalized_agent != int(agent)
                ):
                    raise PluginRunNotAppliedError(
                        "The finalized Agent changed before the continuation started",
                        error_code="continuation_source_changed",
                    )
                promotion = (
                    current_run.session_promotion
                    if isinstance(current_run.session_promotion, dict)
                    else {}
                )
                promoted_session = str(
                    promotion.get("session_id") or ""
                ).strip()
                if not current_run.synced_back or not promoted_session:
                    raise PluginRunNotAppliedError(
                        f"AGENT-{int(agent):03d} does not have a promoted Codex "
                        "session to continue", error_code="finalization_blocked",
                    )
        else:
            with self._lock:
                current_run = self._require_run_locked(run_id)
                current_run.config["keep_workspaces"] = bool(
                    config.get("keep_workspaces", False)
                )
                current_run.config["sync_back"] = bool(
                    config.get("sync_back", True)
                )
                current_run.updated_at = _utc_now()
                self._save_state_locked()
            finalized = self.accept_agent(run_id, int(agent))
            if finalized.get("status") != "finalized":
                raise PluginRunPendingError(
                    "Agent finalization is still running in its persistent worker. "
                    "The continuation can be retried after it finishes."
                )
            promotion = finalized.get("session_promotion") or {}
            promoted_session = str(promotion.get("session_id") or "").strip()
            if not promoted_session:
                promoted_session = session_id or ""
        started = self.start_run(
            prompt=prompt,
            workspace=workspace,
            num_agents=target_agents,
            max_parallel=target_max_parallel,
            serial=target_serial,
            recommend_by=str(config.get("recommend_by") or "reasoning_tokens"),
            model=config.get("model"),
            effort=config.get("effort"),
            resume_session_id=promoted_session,
            runs_dir=config.get("runs_dir"),
            codex_bin=str(config.get("codex_bin") or "codex"),
            sync_back=bool(config.get("sync_back", True)),
            keep_workspaces=bool(config.get("keep_workspaces", False)),
            confirm_large_run=True,
            fast=config.get("fast"),
            synthesis_agents=int(config.get("synthesis_agents") or 0),
            follow_up_delay=int(
                config.get("follow_up_delay")
                if config.get("follow_up_delay") is not None
                else DEFAULT_FOLLOW_UP_DELAY_SECONDS
            ),
            synthesis_model=config.get("synthesis_model"),
            synthesis_effort=config.get("synthesis_effort"),
            synthesis_fast=config.get("synthesis_fast"),
            subagents=bool(config.get("subagents", False)),
            subagents_limit=int(
                config.get("subagents_limit") or DEFAULT_SUBAGENTS_LIMIT
            ),
            conversation_id=conversation_id,
            continued_from_run_id=run_id,
            continued_from_agent=int(agent),
            turn_index=turn_index,
            submission_command=submission_command,
            **({"supports_agent_questions": True} if config.get("supports_agent_questions") is True else {}),
        )
        started["continued_from"] = {
            "run_id": run_id,
            "agent": int(agent),
            "session_id": promoted_session,
            "prompt": source_prompt,
        }
        return started

    def prompt_command_result(self, identity: Dict[str, str]) -> Dict[str, Any] | None:
        # Only committed business state proves acceptance. Do not refresh
        # events, wait for preparation, or treat an unsaved in-memory Run as proof.
        with self._state_store.locked():
            payload = self._state_store.load_payload()
        if payload is None:
            return None
        if not isinstance(payload, dict) or not isinstance(payload.get("runs"), list):
            raise PluginRunError("Prompt acceptance history is invalid")
        matches = [ManagedRun.from_dict(value) for value in payload["runs"]
                   if isinstance(value, dict) and isinstance(value.get("config"), dict)
                   and value["config"].get("submission_command") == identity]
        if not matches:
            return None
        if len(matches) != 1 or not RUN_ID_PATTERN.fullmatch(matches[0].run_id):
            raise PluginRunError("Prompt acceptance identity is ambiguous")
        run = matches[0]
        result: Dict[str, Any] = {"status": "started", "run_id": run.run_id}
        if run.continued_from_run_id:
            result["continued_from"] = {
                "run_id": run.continued_from_run_id,
                "agent": run.continued_from_agent,
                "session_id": run.config.get("resume_session_id"),
            }
        return result

    def can_retry_unstarted_continuation(self, run_id: str, agent: int) -> bool:
        with self._state_store.locked():
            payload = self._state_store.load_payload()
        if not isinstance(payload, dict) or not isinstance(payload.get("runs"), list):
            return False
        runs = [value for value in payload["runs"] if isinstance(value, dict)]
        sources = [value for value in runs if value.get("run_id") == run_id]
        if len(sources) != 1 or any(value.get("continued_from_run_id") == run_id for value in runs):
            return False
        source = sources[0]
        promotion = source.get("session_promotion") or {}
        return (source.get("status") == "finalized" and source.get("finalized_agent") == agent
                and source.get("synced_back") is True and isinstance(promotion, dict)
                and bool(str(promotion.get("session_id") or "").strip()))

    def _resume_sessions_cached(
        self,
        source: Path,
        include_non_interactive: bool,
    ) -> list[Any]:
        codex_home = get_codex_home()
        sessions_root = codex_home / "sessions"
        try:
            sessions_stamp = sessions_root.stat().st_mtime_ns
        except OSError:
            sessions_stamp = 0
        key = (
            "sessions",
            str(source),
            bool(include_non_interactive),
            file_fingerprint(codex_home / "state_5.sqlite"),
            sessions_stamp,
        )
        with self._resume_condition:
            while True:
                cached = self._resume_cache.get(key)
                if cached is not None:
                    expires_at = cached[0]
                    if not isinstance(expires_at, (int, float)) or (
                        float(expires_at) > time.monotonic()
                    ):
                        return list(cached[1])
                if key not in self._resume_inflight:
                    self._resume_inflight.add(key)
                    break
                self._resume_condition.wait()

        try:
            discovered = list_resume_sessions(
                source,
                include_non_interactive=include_non_interactive,
            )
            sessions = list(discovered)
            ttl = (
                RESUME_COMPLETE_CACHE_SECONDS
                if bool(getattr(discovered, "complete", True))
                else RESUME_INCOMPLETE_CACHE_SECONDS
            )
            with self._resume_condition:
                self._resume_cache.put(
                    key,
                    (time.monotonic() + ttl, sessions),
                )
            return list(sessions)
        finally:
            with self._resume_condition:
                self._resume_inflight.discard(key)
                self._resume_condition.notify_all()

    def resume_sessions(
        self,
        workspace: str = "",
        include_non_interactive: bool = False,
        limit: int = 20,
    ) -> Dict[str, Any]:
        source = self._resolve_workspace(workspace)
        limit = min(max(1, int(limit)), 100)
        sessions = self._resume_sessions_cached(source, include_non_interactive)
        return {
            "workspace": str(source),
            "sessions": [
                asdict(session) for session in sessions[:limit]
            ],
            "total": len(sessions),
        }

    def resume_history(
        self,
        workspace: str,
        session_id: str,
        cursor: int = 0,
        limit: int = 50,
        max_bytes: int = DEFAULT_HISTORY_PAGE_BYTES,
        conversation_items_only: bool = False,
    ) -> Dict[str, Any]:
        source = self._resolve_workspace(workspace)
        with self._lock:
            self._sync_state_from_disk_locked()
            path_mappings = self._workspace_path_mappings_locked(source)
        return self._resume_history_page(
            source,
            session_id,
            cursor=cursor,
            limit=limit,
            max_bytes=max_bytes,
            path_mappings=path_mappings,
            conversation_items_only=conversation_items_only,
        )

    def run_history(
        self,
        run_id: str,
        cursor: int = 0,
        limit: int = 50,
        max_bytes: int = DEFAULT_HISTORY_PAGE_BYTES,
        conversation_items_only: bool = False,
    ) -> Dict[str, Any]:
        with self._lock:
            self._sync_state_from_disk_locked()
            run = self._require_run_locked(run_id)
            workspace = run.workspace
            session_id = str(run.config.get("resume_session_id") or "").strip()
            raw_line_count = run.config.get("resume_rollout_line_count")
            line_count = (
                max(0, int(raw_line_count))
                if isinstance(raw_line_count, int) and not isinstance(raw_line_count, bool)
                else None
            )
            prompt = run.prompt
        source = self._resolve_workspace(workspace)
        with self._lock:
            path_mappings = self._workspace_path_mappings_locked(source)
        if not session_id:
            return {
                "run_id": run_id,
                "workspace": str(source),
                "session": None,
                "cursor": 0,
                "next_cursor": 0,
                "has_more": False,
                "total_entries": 0,
                "entries": [],
                "history_boundary": "new_conversation",
            }
        result = self._resume_history_page(
            source,
            session_id,
            cursor=cursor,
            limit=limit,
            max_bytes=max_bytes,
            max_lines=line_count,
            legacy_current_prompt=(prompt if line_count is None else ""),
            path_mappings=path_mappings,
            conversation_items_only=conversation_items_only,
        )
        result["run_id"] = run_id
        result["history_boundary"] = (
            "rollout_line" if line_count is not None else "legacy_prompt"
        )
        return result

    def _resume_history_page(
        self,
        source: Path,
        session_id: str,
        *,
        cursor: int,
        limit: int,
        max_bytes: int = DEFAULT_HISTORY_PAGE_BYTES,
        max_lines: Optional[int] = None,
        legacy_current_prompt: str = "",
        path_mappings: Sequence[WorkspacePathMapping] = (),
        conversation_items_only: bool = False,
    ) -> Dict[str, Any]:
        session_id = session_id.strip()
        if not session_id:
            raise PluginRunError("session_id must not be empty")
        cursor = max(0, int(cursor))
        limit = min(max(1, int(limit)), 100)
        max_bytes = min(
            max(4 * 1024, int(max_bytes)),
            MAX_HISTORY_PAGE_BYTES,
        )
        sessions = self._resume_sessions_cached(source, True)
        session = next(
            (item for item in sessions if item.session_id == session_id),
            None,
        )
        if session is None:
            raise PluginRunError(
                f"Codex session {session_id} is not resumable from {source}"
            )
        cache_key = (
            "history",
            str(source),
            session_id,
            file_fingerprint(session.rollout_path or None),
            max_lines,
        )
        with self._resume_condition:
            cached = self._resume_cache.get(cache_key)
        if cached is None:
            resume_error = subagent_resume_error(get_codex_home(), session_id)
            if resume_error:
                raise PluginRunError(resume_error)
            try:
                history = load_codex_session_history(
                    get_codex_home(),
                    session_id,
                    session.rollout_path or None,
                    max_lines=max_lines,
                )
            except Exception as exc:
                raise PluginRunError(
                    f"Cannot load Codex history for {session_id}: {exc}"
                ) from exc
            with self._resume_condition:
                self._resume_cache.put(cache_key, (session, list(history)))
        else:
            _cached_session, history = cached
        if legacy_current_prompt:
            normalized_prompt = " ".join(legacy_current_prompt.split())
            matching_turns = [
                index
                for index, entry in enumerate(history)
                if entry.category == "user"
                and " ".join(entry.text.split()) == normalized_prompt
            ]
            if matching_turns:
                history = history[: matching_turns[-1]]
        source_entry_count = len(history)
        if conversation_items_only:
            history = [
                entry
                for entry in history
                if entry.category in {"user", "output"}
            ]
        history_payloads = _history_payloads(
            history,
            path_mappings=path_mappings,
        )
        cursor = min(cursor, len(history_payloads))
        entries: list[Dict[str, Any]] = []
        serialized_bytes = 2
        oversized_entry = False
        for payload in history_payloads[cursor : cursor + limit]:
            encoded_size = len(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            separator = 1 if entries else 0
            if entries and serialized_bytes + separator + encoded_size > max_bytes:
                break
            if not entries and serialized_bytes + encoded_size > max_bytes:
                # Always make cursor progress. The protocol response ceiling is
                # larger than this page budget, so one unusually long message can
                # still be delivered rather than trapping clients in a retry loop.
                oversized_entry = True
            entries.append(payload)
            serialized_bytes += separator + encoded_size
        next_cursor = cursor + len(entries)
        return {
            "workspace": str(source),
            "session": asdict(session),
            "cursor": cursor,
            "next_cursor": next_cursor,
            "has_more": next_cursor < len(history_payloads),
            "total_entries": len(history_payloads),
            "source_entries": source_entry_count,
            "serialized_bytes": serialized_bytes,
            "byte_limit": max_bytes,
            "oversized_entry": oversized_entry,
            "entries": entries,
        }

    def account_execution_status(self) -> Dict[str, Any]:
        """Bounded, read-only bindings for active Agents, not a billing estimate."""
        from .codex_auth import file_auth, selected_home

        selected = file_auth(selected_home())
        result = {"current": 0, "waiting": 0, "legacy": 0, "unverified": 0}
        with self._lock:
            targets = []
            for run in self._runs.values():
                if not run.run_root or run.codex_homes_deleted:
                    continue
                indices = [idx for idx, status in run.agent_statuses.items() if status in {"running", "starting"}]
                if indices:
                    targets.append((self._validated_run_root(run), indices))
        for root, indices in targets:
            for idx in indices:
                path = root / "meta" / f"agent_{idx:03d}" / "execution_recovery.json"
                try:
                    if path.is_symlink():
                        raise ValueError("Untrusted execution receipt")
                    with path.open("rb") as stream:
                        raw = stream.read(32 * 1024 + 1)
                    if len(raw) > 32 * 1024:
                        raise ValueError("Oversized execution receipt")
                    state = json.loads(raw)
                    if state.get("stage") in {"completed", "stopped"}:
                        continue
                    auth = state.get("auth") or {}
                    pid = state.get("native_pid")
                    if type(pid) is not int or pid <= 0 or not pid_is_alive(pid):
                        key = "unverified"
                    elif not auth:
                        key = "legacy"
                    elif auth.get("native_pid") != pid or not selected:
                        key = "unverified"
                    elif auth.get("identity") == selected.identity and auth.get("status") == "current":
                        key = "current"
                    elif auth.get("status") in {"waiting_for_tools", "waiting_for_answers", "waiting_for_terminals", "interrupting", "switching"}:
                        key = "waiting"
                    else:
                        key = "unverified"
                except (OSError, ValueError, TypeError, AttributeError):
                    key = "unverified"
                result[key] += 1
        return {**result, "observed_at": time.time()}

    def model_options(
        self,
        model: Optional[str] = None,
        effort: Optional[str] = None,
    ) -> Dict[str, Any]:
        home = get_codex_home()
        with self._lock:
            if self._model_catalog is None or self._model_catalog.real_codex_home != home:
                self._model_catalog = CodexModelCacheManager(
                    "codex", home, copy_codex_support_entries,
                    prewarm_timeout_seconds=10,
                )
            catalog = self._model_catalog
        # Other Codex installations also write the shared home. Read the
        # execution CLI's own snapshot so their menus cannot replace ours.
        snapshot = catalog.catalog_path()
        if snapshot is None:
            raise PluginRunError("Codex model options are temporarily unavailable. Please try again later.")
        registry = CodexModelRegistry.load(home, model_cache_path=snapshot)
        if not registry.models:
            raise PluginRunError("Codex model options are temporarily unavailable. Please try again later.")
        selected_model = str(model or "")
        selected_effort = str(effort or "")
        return {
            "configured_model": registry.configured_model,
            "configured_effort": registry.configured_effort,
            "configured_service_tier": registry.configured_service_tier,
            "effective_model": registry.effective_model(model),
            "effective_effort": registry.effort_display(model, effort),
            "effective_fast": format_fast_mode(
                None,
                registry.configured_service_tier,
            ),
            "models": [
                {
                    "label": label,
                    "value": value,
                    "selected": value == selected_model,
                }
                for label, value in registry.model_options(model)
            ],
            "efforts": [
                {
                    "label": label,
                    "value": value,
                    "selected": value == selected_effort,
                }
                for label, value in registry.effort_options(model, effort)
            ],
        }

    def health(self) -> Dict[str, Any]:
        with self._lock:
            self._sync_state_from_disk_locked()
            self._run_maintenance_locked()
            active_runs = [
                run.run_id
                for run in self._runs.values()
                if self._worker_active_locked(run)
            ]
            persistent_workers = [
                run.run_id
                for run in self._runs.values()
                if self._worker_available_locked(run)
            ]
            operation = next(iter(self._run_operations.items()), None)
            return {
                "ok": not self._closed,
                "state_dir": str(self.state_dir),
                "recorded_runs": len(self._runs),
                "active_run": active_runs[0] if active_runs else None,
                "active_runs": active_runs,
                "persistent_workers": persistent_workers,
                "active_operation": (
                    {"run_id": operation[0], "operation": operation[1]}
                    if operation is not None
                    else None
                ),
                "startup_warnings": list(self._startup_warnings),
                "detached_workers": self._detached_workers,
                "run_ttl_seconds": self.run_ttl_seconds,
                "retention_seconds": self.artifact_retention_seconds,
                "history_retained": self.retains_history,
                "storage_quota_bytes": self.storage_quota_bytes,
                "storage_quota_enforced": self.enforce_storage_quota,
                "reserved_storage_bytes": self._reserved_storage_locked(),
            }

    def close(self, wait_seconds: float = 10.0) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._cleanup_stop.set()
            contexts = list(self._contexts.items())
            detached_processes = list(self._worker_processes.values())
            self._worker_processes.clear()
            # Persistent run workers intentionally survive MCP server recycling.
            # Explicit discard, finalization, TTL, and retention own their lifecycle.
            for _run_id, context in contexts:
                if context.thread is not None and context.thread.is_alive():
                    context.cancel_event.set()
        deadline = dt.datetime.now().timestamp() + max(0.0, wait_seconds)
        cleanup_thread = self._cleanup_thread
        if cleanup_thread is not None:
            cleanup_thread.join(timeout=max(0.0, deadline - dt.datetime.now().timestamp()))
        for _run_id, context in contexts:
            thread = context.thread
            if thread is not None and thread.is_alive():
                remaining = max(0.0, deadline - dt.datetime.now().timestamp())
                thread.join(timeout=remaining)
        for process in detached_processes:
            if process.poll() is None:
                threading.Thread(
                    target=process.wait,
                    name=f"pcr-plugin-reaper-{process.pid}",
                    daemon=True,
                ).start()
            else:
                process.wait()
        with self._lock:
            self._finish_shutdown_locked()

    def __enter__(self) -> "PluginRunManager":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()
