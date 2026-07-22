from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from platformdirs import user_state_dir

from .app import (
    LARGE_RUN_STORAGE_WARNING_BYTES,
    available_storage_bytes,
    estimate_run_storage,
    format_storage_bytes,
    get_codex_home,
    load_codex_session_history,
    list_resume_sessions,
    normalize_recommend_by,
    promote_best_codex_session_to_workspace,
    run_additional_agents,
    run_once,
    scrub_agent_codex_homes,
    select_best_result,
    subagent_resume_error,
    validate_args,
)
from .codex_models import CodexModelRegistry
from .models import AgentResult, DEFAULT_NUM_AGENTS
from .paths import choose_run_base, default_run_anchor, is_relative_to
from .workspace import cleanup_workspace_copies, sync_best_workspace_back
from .plugin.artifacts import (
    ArtifactError,
    ArtifactStore,
    write_json_atomic,
    write_text_atomic,
)
from .plugin.cache import LruCache, file_fingerprint
from .plugin.events import EventLog
from .plugin.lifecycle import (
    DEFAULT_ARTIFACT_RETENTION_SECONDS,
    DEFAULT_RUN_TTL_SECONDS,
    DEFAULT_STORAGE_QUOTA_BYTES,
    FileSignal,
    RunOperationLock,
    configured_positive_int,
    deadline_timestamp,
    pid_is_alive,
    read_json,
    spawn_worker,
    utc_deadline,
)
from .plugin.state import (
    STATE_VERSION,
    JsonStateStore,
    ManagedRun,
    int_key_dict as _int_key_dict,
    positive_ints as _positive_ints,
    safe_int as _safe_int,
    utc_now as _utc_now,
)


ACTIVE_RUN_STATES = {"preparing", "running", "stopping"}
FINAL_RUN_STATES = {"finalized", "discarded"}
MAX_EVENT_PAGE = 250
MAX_DIFF_PAGE_CHARS = 100_000
RUN_ID_PATTERN = re.compile(r"^pcr-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")
RESUME_CACHE_SIZE = 8


class PluginRunError(RuntimeError):
    """Raised when a plugin operation cannot be completed safely."""


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


class PluginRunManager:
    """Thread-safe headless PCR controller used by the Codex plugin."""

    def __init__(
        self,
        state_dir: Optional[Path] = None,
        *,
        detached_workers: bool | None = None,
    ) -> None:
        self.state_dir = (state_dir or default_plugin_state_dir()).expanduser().resolve()
        self.events_dir = self.state_dir / "events"
        self.state_path = self.state_dir / "runs.json"
        self.locks_dir = self.state_dir / "locks"
        self.control_dir = self.state_dir / "control"
        self._state_store = JsonStateStore(self.state_dir)
        self._events = EventLog(self.events_dir)
        self._artifacts = ArtifactStore()
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
        self._startup_warnings: list[str] = []
        self._run_operations: Dict[str, str] = {}
        self._closed = False
        self.run_ttl_seconds = configured_positive_int(
            "PCR_PLUGIN_RUN_TTL_SECONDS", DEFAULT_RUN_TTL_SECONDS
        )
        self.artifact_retention_seconds = configured_positive_int(
            "PCR_PLUGIN_RETENTION_SECONDS", DEFAULT_ARTIFACT_RETENTION_SECONDS
        )
        self.storage_quota_bytes = configured_positive_int(
            "PCR_PLUGIN_STORAGE_QUOTA_BYTES", DEFAULT_STORAGE_QUOTA_BYTES
        )
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.events_dir.mkdir(parents=True, exist_ok=True)
        self.locks_dir.mkdir(parents=True, exist_ok=True)
        self.control_dir.mkdir(parents=True, exist_ok=True)
        self._harden_path(self.state_dir, 0o700)
        self._harden_path(self.events_dir, 0o700)
        self._load_state()
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
        if version > STATE_VERSION:
            raise PluginRunError(
                f"Plugin state version {version} is newer than supported version "
                f"{STATE_VERSION}"
            )
        values = payload.get("runs", [])
        if not isinstance(values, list):
            self._preserve_invalid_state(
                "Plugin state does not contain a run list"
            )
            return
        changed = False
        for value in values:
            if not isinstance(value, dict):
                continue
            run = ManagedRun.from_dict(value)
            if not RUN_ID_PATTERN.fullmatch(run.run_id):
                continue
            self._runs[run.run_id] = run
            if run.status in ACTIVE_RUN_STATES | {"finalizing"}:
                self._refresh_events_locked(run, save=False)
            if run.status in ACTIVE_RUN_STATES and not pid_is_alive(run.worker_pid):
                run.status = "interrupted"
                run.error = (
                    "The detached PCR worker ended before this run completed. "
                    "Inspect retained artifacts or discard the run."
                )
                for idx, status in list(run.agent_statuses.items()):
                    if status in {"copying", "queued", "running", "stopping"}:
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
            self._state_store.write_runs(self._runs)
        self._harden_path(self.state_path, 0o600)

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

    def _sync_state_from_disk_locked(self) -> None:
        try:
            with self._state_store.locked():
                payload = self._state_store.load_payload()
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
        self._refresh_events_locked(run, save=False)
        return run

    def _active_context_thread_locked(
        self,
    ) -> Optional[tuple[str, threading.Thread]]:
        for run_id, context in self._contexts.items():
            thread = context.thread
            if thread is not None and thread.is_alive():
                return run_id, thread
        return None

    def _worker_active_locked(self, run: ManagedRun) -> bool:
        context = self._contexts.get(run.run_id)
        if context is not None and context.thread is not None:
            return context.thread.is_alive()
        return pid_is_alive(run.worker_pid)

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
        self._finish_shutdown_locked()

    def _finish_shutdown_locked(self) -> None:
        if not self._closed or self._run_operations:
            return
        self._sync_state_from_disk_locked()
        self._save_state_locked()

    def _recompute_recommendation_locked(self, run: ManagedRun) -> None:
        candidates: list[AgentResult] = []
        rejected = set(run.rejected_agents)
        for idx, value in run.results.items():
            if idx in rejected or value.get("status") != "success":
                continue
            try:
                candidates.append(AgentResult(**value))
            except (TypeError, ValueError):
                continue
        best = select_best_result(
            candidates,
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
            if run.status == "preparing":
                run.status = "running"
        elif kind == "agent_status" and idx > 0:
            run.agent_statuses[idx] = str(
                payload.get("status") or run.agent_statuses.get(idx, "queued")
            )
        elif kind == "agent_started" and idx > 0:
            run.agent_statuses[idx] = "running"
        elif kind == "agent_tokens" and idx > 0:
            value = payload.get("reasoning_tokens")
            if isinstance(value, int):
                run.agent_tokens[idx] = value
        elif kind == "agent_finished" and idx > 0:
            result = payload.get("result")
            if isinstance(result, dict):
                run.results[idx] = dict(result)
                run.agent_statuses[idx] = str(result.get("status") or "finished")
                token_value = result.get("reasoning_tokens")
                run.agent_tokens[idx] = (
                    int(token_value) if isinstance(token_value, int) else None
                )
                self._recompute_recommendation_locked(run)
        elif kind == "run_finished":
            run.status = "cancelled" if payload.get("cancelled") else "completed"
            root = payload.get("run_root")
            if isinstance(root, str) and root:
                if run.run_root and Path(root).resolve() != Path(run.run_root).resolve():
                    raise PluginRunError("PCR reported a different run root at completion")
                if not run.run_root:
                    run.run_root = root
                    self._write_run_marker_locked(run)
            self._recompute_recommendation_locked(run)
        elif kind == "run_failed":
            run.status = "failed"
            run.error = str(payload.get("message") or "PCR run failed")
        elif kind == "batch_finished":
            run.status = "cancelled" if payload.get("cancelled") else "completed"
            self._recompute_recommendation_locked(run)
        elif kind == "batch_failed":
            run.status = "failed"
            run.error = str(
                payload.get("message") or "Additional PCR candidates failed"
            )
        elif kind == "plugin_worker_finished":
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
        run.updated_at = str(payload.get("timestamp") or _utc_now())

    def _refresh_events_locked(
        self,
        run: ManagedRun,
        *,
        save: bool,
    ) -> None:
        changed = False
        while True:
            page = self._events.read(
                run.run_id,
                cursor=run.event_count,
                limit=1000,
            )
            for payload in page["events"]:
                self._apply_progress_locked(run, payload)
            if page["next_cursor"] != run.event_count:
                changed = True
                run.event_count = page["next_cursor"]
            if not page["has_more"]:
                break
        if changed and save:
            self._save_state_locked()

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
        module_dir = Path(__file__).resolve().parent
        anchor = default_run_anchor(module_dir, workspace)
        try:
            return choose_run_base(anchor, workspace, runs_dir)
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

    def _run_maintenance_locked(self) -> None:
        now = time.time()
        changed = False
        removable: list[str] = []
        for run in list(self._runs.values()):
            if run.status in ACTIVE_RUN_STATES | {"finalizing"} or run.worker_pid:
                self._refresh_events_locked(run, save=False)
            active = self._worker_active_locked(run)
            deadline = deadline_timestamp(run.worker_deadline)
            if active and deadline is not None and now >= deadline:
                FileSignal(self.control_dir / run.run_id / "stop").set()
            if run.status in ACTIVE_RUN_STATES and not active:
                run.status = "interrupted"
                run.error = "The detached PCR worker is no longer running"
                run.worker_pid = None
                run.updated_at = _utc_now()
                changed = True
            expires = deadline_timestamp(run.expires_at)
            if (
                expires is not None
                and now >= expires
                and not active
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
        elif phase in {"sync_complete", "cleanup_started"}:
            run.finalized_agent = agent
            run.synced_back = bool(journal.get("synced_back", True))
            run.status = "cleanup_failed"
            run.error = "Sync-back is recorded; retry candidate cleanup without syncing again."
        elif phase == "complete":
            run.finalized_agent = agent
            run.synced_back = bool(journal.get("synced_back"))
            run.workspaces_deleted = bool(journal.get("workspaces_deleted"))
            run.codex_homes_deleted = bool(journal.get("codex_homes_deleted"))
            run.status = "finalized"

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
    ) -> Dict[str, Any]:
        source = self._resolve_workspace(workspace)
        if num_agents <= 0:
            raise PluginRunError("num_agents must be greater than zero")
        estimate = estimate_run_storage(
            source,
            num_agents,
            resume_session_id=resume_session_id,
            codex_home=get_codex_home(),
        )
        run_base = self._run_base(source, runs_dir)
        free_bytes = available_storage_bytes(run_base)
        return {
            "workspace": str(source),
            "run_base": str(run_base),
            "num_agents": num_agents,
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
            resume=False,
            resume_session_id=config.get("resume_session_id"),
            resume_include_non_interactive=False,
            # The plugin owns review, finalization, and cleanup.
            no_sync_back=True,
            keep_workspaces=True,
            cancel_event=context.cancel_event,
            agent_cancel_events=context.agent_cancel_events,
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
    ) -> Dict[str, Any]:
        prompt = prompt.strip()
        if not prompt:
            raise PluginRunError("Prompt must not be empty")
        source = self._resolve_workspace(workspace)
        if num_agents <= 0:
            raise PluginRunError("num_agents must be greater than zero")
        if max_parallel is not None and max_parallel <= 0:
            raise PluginRunError("max_parallel must be greater than zero")
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
            if projected > self.storage_quota_bytes:
                raise PluginRunError(
                    "PCR plugin storage quota exceeded: "
                    f"{format_storage_bytes(projected)} projected, "
                    f"{format_storage_bytes(self.storage_quota_bytes)} allowed. "
                    "Accept, discard, or clean older runs before starting another."
                )

        config: Dict[str, Any] = {
            "num_agents": int(num_agents),
            "max_parallel": int(max_parallel) if max_parallel is not None else None,
            "serial": bool(serial),
            "recommend_by": recommend_by,
            "model": model or None,
            "effort": effort or None,
            "resume_session_id": resume_session_id or None,
            "runs_dir": (
                str(Path(runs_dir).expanduser().resolve()) if runs_dir else None
            ),
            "codex_bin": codex_bin,
            "sync_back": bool(sync_back),
            "keep_workspaces": bool(keep_workspaces),
            "run_base": str(storage["run_base"]),
        }
        run_id = self._new_run_id()
        run = ManagedRun(
            run_id=run_id,
            prompt=prompt,
            workspace=str(source),
            config=config,
            agent_statuses={idx: "queued" for idx in range(1, num_agents + 1)},
            agent_tokens={idx: None for idx in range(1, num_agents + 1)},
            artifact_token=uuid.uuid4().hex,
            worker_operation="initial",
            worker_started_at=_utc_now(),
            worker_deadline=utc_deadline(self.run_ttl_seconds),
            estimated_bytes=int(storage["total_bytes"]),
            expires_at=utc_deadline(self.artifact_retention_seconds),
        )
        with self._lock:
            if self._closed:
                raise PluginRunError("PCR plugin runtime is shutting down")
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
                process, _operation_id = spawn_worker(
                    self.state_dir,
                    run_id,
                    "initial",
                    {"run": run.to_dict(), "indices": []},
                )
                run.worker_pid = process.pid
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

    def _agent_summary_locked(
        self,
        run: ManagedRun,
        idx: int,
    ) -> Dict[str, Any]:
        result = run.results.get(idx) or {}
        token_value = result.get("reasoning_tokens", run.agent_tokens.get(idx))
        return {
            "agent": idx,
            "name": f"AGENT-{idx:03d}",
            "status": str(
                result.get("status")
                or run.agent_statuses.get(idx)
                or "unknown"
            ),
            "seconds": result.get("seconds"),
            "reasoning_tokens": token_value,
            "reasoning_token_counts": result.get("reasoning_token_counts") or {},
            "codex_thread_id": result.get("codex_thread_id"),
            "error": result.get("error"),
            "rejected": idx in set(run.rejected_agents),
            "recommended": idx == run.recommended_agent,
            "finalized": idx == run.finalized_agent,
        }

    def _run_summary_locked(self, run: ManagedRun) -> Dict[str, Any]:
        indices = sorted(
            set(run.agent_statuses)
            | set(run.results)
            | set(run.agent_tokens)
        )
        return {
            "run_id": run.run_id,
            "status": run.status,
            "workspace": run.workspace,
            "prompt": run.prompt,
            "created_at": run.created_at,
            "updated_at": run.updated_at,
            "run_root": run.run_root or None,
            "config": dict(run.config),
            "recommended_agent": run.recommended_agent,
            "finalized_agent": run.finalized_agent,
            "synced_back": run.synced_back,
            "workspaces_deleted": run.workspaces_deleted,
            "codex_homes_deleted": run.codex_homes_deleted,
            "worker_active": self._worker_active_locked(run),
            "worker_pid": run.worker_pid,
            "worker_deadline": run.worker_deadline or None,
            "session_promotion": run.session_promotion,
            "error": run.error or None,
            "event_count": run.event_count,
            "agents": [
                self._agent_summary_locked(run, idx)
                for idx in indices
            ],
        }

    def get_run(
        self,
        run_id: str,
        include_events: bool = False,
        cursor: int = 0,
        event_limit: int = 50,
    ) -> Dict[str, Any]:
        with self._lock:
            run = self._require_run_locked(run_id)
            result = self._run_summary_locked(run)
        if include_events:
            result["event_page"] = self.get_events(
                run_id,
                cursor=cursor,
                limit=event_limit,
            )
        return result

    def list_runs(self, limit: int = 20) -> Dict[str, Any]:
        limit = min(max(1, int(limit)), 100)
        with self._lock:
            self._sync_state_from_disk_locked()
            self._run_maintenance_locked()
            values = sorted(
                self._runs.values(),
                key=lambda run: run.created_at,
                reverse=True,
            )[:limit]
            return {
                "runs": [self._run_summary_locked(run) for run in values],
                "total": len(self._runs),
            }

    def get_events(
        self,
        run_id: str,
        cursor: int = 0,
        limit: int = 100,
        agent: Optional[int] = None,
    ) -> Dict[str, Any]:
        cursor = max(0, int(cursor))
        limit = min(max(1, int(limit)), MAX_EVENT_PAGE)
        with self._lock:
            self._require_run_locked(run_id)
        try:
            return self._events.read(
                run_id,
                cursor=cursor,
                limit=limit,
                agent=agent,
            )
        except OSError as exc:
            raise PluginRunError(f"Cannot read PCR events: {exc}") from exc

    def get_agent(
        self,
        run_id: str,
        agent: int,
        include_diff: bool = False,
        include_events: bool = True,
        cursor: int = 0,
        event_limit: int = 100,
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
            response["stdout_tail"] = result_data.get("stdout_tail") or ""
            response["stderr_tail"] = result_data.get("stderr_tail") or ""
            try:
                candidate = self._agent_workspace_for_run(
                    run,
                    agent,
                    result_data.get("workspace_dir"),
                )
                stdout_log = self._agent_meta_file(run, agent, "stdout.log")
                stderr_log = self._agent_meta_file(run, agent, "stderr.log")
                final_message_path = self._agent_meta_file(
                    run, agent, "final_message.md"
                )
                response["workspace_dir"] = (
                    str(candidate) if candidate.is_dir() else None
                )
                response["stdout_log"] = (
                    str(stdout_log) if stdout_log.is_file() else None
                )
                response["stderr_log"] = (
                    str(stderr_log) if stderr_log.is_file() else None
                )
                artifact_error = None
            except PluginRunError as exc:
                final_message_path = None
                response["workspace_dir"] = None
                response["stdout_log"] = None
                response["stderr_log"] = None
                artifact_error = str(exc)
        final_message = ""
        if final_message_path is not None and final_message_path.is_file():
            try:
                final_message = final_message_path.read_text(
                    encoding="utf-8"
                ).strip()
            except OSError:
                final_message = ""
        response["final_message"] = final_message
        response["artifact_error"] = artifact_error
        if include_events:
            response["event_page"] = self.get_events(
                run_id,
                cursor=cursor,
                limit=event_limit,
                agent=agent,
            )
        if include_diff:
            if artifact_error:
                response["diff"] = None
                response["diff_error"] = artifact_error
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
            if not result_data:
                raise PluginRunError(
                    f"AGENT-{agent:03d} has not produced a reviewable result yet"
                )
            try:
                path = self._artifacts.persist_diff(
                    run,
                    agent,
                    result_data.get("workspace_dir"),
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
            "persisted_path": str(path),
            "text": text,
        }

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
            if current != "running":
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
        )
        result["still_running"] = bool(result.get("worker_active"))
        return result

    def _start_additional_batch(
        self,
        run: ManagedRun,
        indices: Sequence[int],
        retry_indices: set[int],
    ) -> Dict[str, Any]:
        run_root = self._validated_run_root(run)
        if self._worker_active_locked(run):
            raise PluginRunError(
                f"PCR run {run.run_id} is still active; wait or stop it first"
            )
        context = _RunContext(
            cancel_event=threading.Event(),
            agent_cancel_events={idx: threading.Event() for idx in indices},
        )
        args = self._build_args(run, context, num_agents=len(indices))
        try:
            validate_args(args)
        except SystemExit as exc:
            raise PluginRunError(str(exc)) from exc
        run.status = "running"
        run.error = ""
        for idx in indices:
            run.agent_statuses[idx] = "queued"
            run.agent_tokens[idx] = None
            if idx in retry_indices:
                run.results.pop(idx, None)
                try:
                    self._artifacts.diff_path(run, idx).unlink()
                except (ArtifactError, FileNotFoundError):
                    pass
                if idx in run.rejected_agents:
                    run.rejected_agents.remove(idx)
            FileSignal(
                self.control_dir / run.run_id / f"kill-agent-{idx:03d}"
            ).clear()
        FileSignal(self.control_dir / run.run_id / "stop").clear()
        run.worker_operation = "retry" if retry_indices else "more"
        run.worker_started_at = _utc_now()
        run.worker_deadline = utc_deadline(self.run_ttl_seconds)
        self._recompute_recommendation_locked(run)
        run.updated_at = _utc_now()
        self._append_event_locked(
            run,
            {
                "type": "batch_created",
                "indices": list(indices),
                "retry_indices": sorted(retry_indices),
            },
        )
        self._save_state_locked()

        if self._detached_workers:
            process, _operation_id = spawn_worker(
                self.state_dir,
                run.run_id,
                run.worker_operation,
                {
                    "run": run.to_dict(),
                    "indices": list(indices),
                    "retry_indices": sorted(retry_indices),
                },
            )
            run.worker_pid = process.pid
            self._worker_processes[run.run_id] = process
            self._save_state_locked()
            return self._run_summary_locked(run)

        self._contexts[run.run_id] = context

        def target() -> None:
            try:
                run_additional_agents(
                    args=args,
                    prompt=run.prompt,
                    agent_indices=indices,
                    run_root=run_root,
                    workspace=self._workspace_for_run(run),
                    resume_session_id=run.config.get("resume_session_id"),
                    retry_indices=retry_indices,
                    progress_callback=lambda payload: self._record_progress(
                        run.run_id, payload
                    ),
                    cancel_event=context.cancel_event,
                    agent_cancel_events=context.agent_cancel_events,
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

    def _add_agents(
        self,
        run_id: str,
        count: int,
        confirm_large_run: bool,
    ) -> Dict[str, Any]:
        count = int(count)
        if count <= 0:
            raise PluginRunError("count must be greater than zero")
        with self._lock:
            run = self._require_run_locked(run_id)
            if run.status in FINAL_RUN_STATES or run.finalized_agent is not None:
                raise PluginRunError(f"PCR run {run_id} is already {run.status}")
            if self._worker_active_locked(run):
                raise PluginRunError(
                    "Wait for the active candidate batch to stop before adding Agents"
                )
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
            if projected > self.storage_quota_bytes:
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
            run.config["num_agents"] = highest + count
            run.estimated_bytes += int(storage["total_bytes"])
            try:
                response = self._start_additional_batch(run, indices, set())
                response["additional_storage"] = storage
                return response
            except BaseException:
                run.config["num_agents"] = previous_count
                run.estimated_bytes = previous_estimate
                raise

    def retry_agent(self, run_id: str, agent: int) -> Dict[str, Any]:
        with self._run_operation_guard(run_id, f"retry AGENT-{int(agent):03d}"):
            return self._retry_agent(run_id, agent)

    def _retry_agent(self, run_id: str, agent: int) -> Dict[str, Any]:
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
            return self._start_additional_batch(run, [agent], {agent})

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
    ) -> Dict[str, Any]:
        operation = f"finalization of AGENT-{int(agent):03d}"
        try:
            with RunOperationLock(self.locks_dir, run_id, operation):
                with self._lock:
                    self._require_run_locked(run_id)
                    self._claim_run_operation_locked(run_id, operation)
                try:
                    return self._accept_agent(run_id, agent, wait_seconds)
                finally:
                    with self._lock:
                        self._release_run_operation_locked(run_id, operation)
        except RuntimeError as exc:
            raise PluginRunError(str(exc)) from exc

    def recover_finalization(
        self,
        run_id: str,
        sync_was_applied: bool,
    ) -> Dict[str, Any]:
        operation = "finalization recovery"
        try:
            with RunOperationLock(self.locks_dir, run_id, operation):
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
                    self._save_state_locked()
                    return self._run_summary_locked(run)
        except RuntimeError as exc:
            raise PluginRunError(str(exc)) from exc

    def _persist_completed_diffs_locked(self, run: ManagedRun) -> None:
        try:
            self._artifacts.persist_successful_diffs(run)
        except ArtifactError as exc:
            raise PluginRunError(
                f"Cannot preserve completed Agent diffs before cleanup: {exc}"
            ) from exc

    def _mark_cleanup_failure(
        self,
        run_id: str,
        agent: int,
        result: AgentResult,
        sync_back: bool,
        promotion_data: Dict[str, Any] | None,
        exc: BaseException,
    ) -> None:
        with self._lock:
            run = self._require_run_locked(run_id)
            run.finalized_agent = agent
            run.synced_back = sync_back
            run.session_promotion = promotion_data
            run.workspaces_deleted = False
            run.codex_homes_deleted = False
            run.status = "cleanup_failed"
            run.error = str(exc)
            run.updated_at = _utc_now()
            self._append_event_locked(
                run,
                {
                    "type": "workspace_cleanup_failed",
                    "idx": agent,
                    "message": str(exc),
                },
            )
            try:
                self._record_finalization(run, result)
            except Exception:
                pass
            self._save_state_locked()

    def _accept_agent(
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
                if run.status not in {"cleanup_failed", "finalizing"}:
                    return self._run_summary_locked(run)
            value = run.results.get(agent)
            if not isinstance(value, dict) or value.get("status") != "success":
                raise PluginRunError(
                    f"AGENT-{agent:03d} has not finished successfully"
                )
            worker_active = self._worker_active_locked(run)
        if worker_active:
            self.stop_run(run_id, wait_seconds=wait_seconds)
            with self._lock:
                run = self._require_run_locked(run_id)
                if self._worker_active_locked(run):
                    raise PluginRunError(
                        "Timed out while stopping remaining Agents; try again after the run stops"
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
                candidate = self._agent_workspace_for_run(
                    run,
                    agent,
                    result.workspace_dir,
                )
                if not candidate.exists() or not candidate.is_dir():
                    raise PluginRunError(
                        f"AGENT-{agent:03d} workspace is not available: {candidate}"
                    )
            else:
                candidate = None
            self._persist_completed_diffs_locked(run)

        if not already_synced:
            self._write_finalization_journal(
                run,
                phase="sync_started" if sync_back else "sync_complete",
                agent=agent,
                synced_back=False,
            )
            with self._lock:
                run.finalized_agent = agent
                run.status = "finalizing"
                run.error = ""
                run.updated_at = _utc_now()
                self._save_state_locked()

        if sync_back and not already_synced:
            assert candidate is not None
            try:
                sync_best_workspace_back(candidate, workspace)
            except Exception as exc:
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

            self._write_finalization_journal(
                run,
                phase="sync_complete",
                agent=agent,
                synced_back=True,
            )

            try:
                promotion = promote_best_codex_session_to_workspace(
                    result,
                    workspace,
                )
                if promotion is not None:
                    promotion_data = asdict(promotion)
            except Exception as exc:
                promotion_data = {
                    "session_id": result.codex_thread_id or "",
                    "workspace": str(workspace),
                    "error": str(exc),
                }

        if not already_synced:
            with self._lock:
                run = self._require_run_locked(run_id)
                run.finalized_agent = agent
                run.synced_back = sync_back
                run.session_promotion = promotion_data
                run.status = "finalizing"
                run.error = ""
                run.updated_at = _utc_now()
                self._append_event_locked(
                    run,
                    {
                        "type": "agent_selected_for_finalization",
                        "idx": agent,
                        "synced_back": sync_back,
                        "session_promotion": promotion_data,
                    },
                )
                self._save_state_locked()

        deleted = bool(not keep_workspaces and not workspaces_root.exists())
        codex_homes_deleted = False
        if not keep_workspaces:
            self._write_finalization_journal(
                run,
                phase="cleanup_started",
                agent=agent,
                synced_back=sync_back,
            )
            try:
                if workspaces_root.exists():
                    cleanup_workspace_copies(workspace, workspaces_root)
                deleted = not workspaces_root.exists()
                codex_homes_deleted = self._artifacts.remove_codex_homes(run)
            except Exception as exc:
                self._mark_cleanup_failure(
                    run_id,
                    agent,
                    result,
                    sync_back,
                    promotion_data,
                    exc,
                )
                raise PluginRunError(
                    f"AGENT-{agent:03d} was finalized, but candidate cleanup "
                    f"failed: {exc}. Call pcr_accept_agent again to retry cleanup."
                ) from exc

        with self._lock:
            run = self._require_run_locked(run_id)
            run.finalized_agent = agent
            run.synced_back = sync_back
            run.workspaces_deleted = deleted
            run.codex_homes_deleted = codex_homes_deleted
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
            )
            self._save_state_locked()
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
                    self._require_run_locked(run_id)
                    self._claim_run_operation_locked(run_id, operation)
                try:
                    return self._discard_run(run_id, keep_workspaces, wait_seconds)
                finally:
                    with self._lock:
                        self._release_run_operation_locked(run_id, operation)
        except RuntimeError as exc:
            raise PluginRunError(str(exc)) from exc

    def _discard_run(
        self,
        run_id: str,
        keep_workspaces: bool,
        wait_seconds: float,
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
        self.stop_run(run_id, wait_seconds=wait_seconds)
        with self._lock:
            run = self._require_run_locked(run_id)
            if self._worker_active_locked(run):
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
        confirm_large_run: bool = False,
    ) -> Dict[str, Any]:
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
            config = dict(run.config)
            target_agents = int(
                num_agents or config.get("num_agents") or DEFAULT_NUM_AGENTS
            )
            session_id = str(result.get("codex_thread_id") or "") or None
            workspace = run.workspace
        storage = self.estimate(
            workspace,
            target_agents,
            config.get("runs_dir"),
            session_id,
        )
        if storage["confirmation_required"] and not confirm_large_run:
            return {
                "status": "confirmation_required",
                "message": (
                    f"The continuation is estimated to use {storage['total']}. "
                    "Confirm before finalizing the current Agent and starting it."
                ),
                "storage": storage,
            }
        finalized = self.accept_agent(run_id, int(agent))
        promotion = finalized.get("session_promotion") or {}
        promoted_session = str(promotion.get("session_id") or "") or session_id
        started = self.start_run(
            prompt=prompt,
            workspace=workspace,
            num_agents=target_agents,
            max_parallel=(
                max_parallel
                if max_parallel is not None
                else config.get("max_parallel")
            ),
            serial=bool(config.get("serial")),
            recommend_by=str(config.get("recommend_by") or "reasoning_tokens"),
            model=config.get("model"),
            effort=config.get("effort"),
            resume_session_id=promoted_session,
            runs_dir=config.get("runs_dir"),
            codex_bin=str(config.get("codex_bin") or "codex"),
            sync_back=bool(config.get("sync_back", True)),
            keep_workspaces=bool(config.get("keep_workspaces", False)),
            confirm_large_run=True,
        )
        started["continued_from"] = {
            "run_id": run_id,
            "agent": int(agent),
            "session_id": promoted_session,
        }
        return started

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
        cached = self._resume_cache.get(key)
        if cached is not None:
            return list(cached[1])
        sessions = list_resume_sessions(
            source,
            include_non_interactive=include_non_interactive,
        )
        self._resume_cache.put(key, (None, list(sessions)))
        return list(sessions)

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
    ) -> Dict[str, Any]:
        source = self._resolve_workspace(workspace)
        session_id = session_id.strip()
        if not session_id:
            raise PluginRunError("session_id must not be empty")
        cursor = max(0, int(cursor))
        limit = min(max(1, int(limit)), 100)
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
        )
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
                )
            except Exception as exc:
                raise PluginRunError(
                    f"Cannot load Codex history for {session_id}: {exc}"
                ) from exc
            self._resume_cache.put(cache_key, (session, list(history)))
        else:
            _cached_session, history = cached
        cursor = min(cursor, len(history))
        next_cursor = min(len(history), cursor + limit)
        return {
            "workspace": str(source),
            "session": asdict(session),
            "cursor": cursor,
            "next_cursor": next_cursor,
            "has_more": next_cursor < len(history),
            "total_entries": len(history),
            "entries": [
                asdict(entry) for entry in history[cursor:next_cursor]
            ],
        }

    def model_options(
        self,
        model: Optional[str] = None,
        effort: Optional[str] = None,
    ) -> Dict[str, Any]:
        registry = CodexModelRegistry.load(get_codex_home())
        selected_model = str(model or "")
        selected_effort = str(effort or "")
        return {
            "configured_model": registry.configured_model,
            "configured_effort": registry.configured_effort,
            "effective_model": registry.effective_model(model),
            "effective_effort": registry.effort_display(model, effort),
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
            operation = next(iter(self._run_operations.items()), None)
            return {
                "ok": not self._closed,
                "state_dir": str(self.state_dir),
                "recorded_runs": len(self._runs),
                "active_run": active_runs[0] if active_runs else None,
                "active_runs": active_runs,
                "active_operation": (
                    {"run_id": operation[0], "operation": operation[1]}
                    if operation is not None
                    else None
                ),
                "startup_warnings": list(self._startup_warnings),
                "detached_workers": self._detached_workers,
                "run_ttl_seconds": self.run_ttl_seconds,
                "retention_seconds": self.artifact_retention_seconds,
                "storage_quota_bytes": self.storage_quota_bytes,
                "reserved_storage_bytes": self._reserved_storage_locked(),
            }

    def close(self, wait_seconds: float = 10.0) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            contexts = list(self._contexts.items())
            detached_processes = list(self._worker_processes.values())
            self._worker_processes.clear()
            # Detached workers intentionally survive MCP server recycling. Explicit
            # stop, discard, TTL, and retention controls own their lifecycle.
            for _run_id, context in contexts:
                if context.thread is not None and context.thread.is_alive():
                    context.cancel_event.set()
        deadline = dt.datetime.now().timestamp() + max(0.0, wait_seconds)
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
