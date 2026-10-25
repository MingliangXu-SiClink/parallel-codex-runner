from __future__ import annotations

import argparse
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Sequence

from ..app import (
    LiveCandidateCoordinator,
    LiveCandidateRequest,
    LiveSynthesisConfigCoordinator,
    run_additional_agents,
    run_once,
    run_synthesis_refresh,
    validate_args,
)
from ..models import AgentResult, AGENT_ROLE_SYNTHESIS, normalize_agent_role
from ..runtime_pinning import preload_plugin_worker_runtime
from .artifacts import ArtifactStore
from .events import EventLog
from .lifecycle import (
    WORKER_PROTOCOL_VERSION,
    WORKER_PACKAGE_ROOT_ENV,
    WORKER_PARENT_PYTHONPATH_ENV,
    WORKER_PARENT_PYTHONPATH_PRESENT_ENV,
    FileSignal,
    initialize_candidate_requests,
    locked_candidate_requests,
    locked_synthesis_config,
    deadline_timestamp,
    installed_signal_handlers,
    pid_is_alive,
    normalized_indices,
    read_json,
    worker_response_path,
    worker_candidate_requests_path,
    worker_status_path,
    worker_synthesis_config_path,
    write_json_atomic,
)
from .state import ManagedRun, utc_now


WORKER_POLL_SECONDS = 0.25
RUN_ID_PATTERN = re.compile(r"^pcr-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")


class FileSignalMap(dict[int, FileSignal]):
    """Create file-backed cancellation signals for dynamically added Agents."""

    def __init__(
        self,
        control_dir: Path,
        deadline: str,
        indices: Sequence[int] = (),
    ) -> None:
        self.control_dir = control_dir
        self.deadline = deadline
        super().__init__((idx, self._signal(idx)) for idx in indices)

    def _signal(self, idx: int) -> FileSignal:
        return FileSignal(
            self.control_dir / f"kill-agent-{idx:03d}",
            self.deadline,
        )

    def get(  # type: ignore[override]
        self,
        key: int,
        default: FileSignal | None = None,
    ) -> FileSignal:
        del default
        return self.setdefault(key)

    def setdefault(  # type: ignore[override]
        self,
        key: int,
        default: FileSignal | None = None,
    ) -> FileSignal:
        del default
        idx = int(key)
        value = super().get(idx)
        if value is None:
            value = self._signal(idx)
            super().__setitem__(idx, value)
        return value


class FileBackedCandidateCoordinator(LiveCandidateCoordinator):
    """Bridge live More/Retry requests into a busy detached worker."""

    def __init__(
        self,
        path: Path,
        agent_signals: Dict[int, FileSignal],
        control_dir: Path,
        deadline: str,
    ) -> None:
        super().__init__()
        self.path = path
        self.agent_signals = agent_signals
        self.control_dir = control_dir
        self.deadline = deadline
        if not path.exists():
            initialize_candidate_requests(path)

    @staticmethod
    def _requests(payload: Dict[str, Any]) -> list[LiveCandidateRequest]:
        requests: list[LiveCandidateRequest] = []
        seen: set[int] = set()
        raw_requests = payload.get("requests")
        if not isinstance(raw_requests, list):
            return requests
        for item in raw_requests:
            if not isinstance(item, dict):
                continue
            try:
                idx = int(item.get("idx") or 0)
            except (TypeError, ValueError):
                continue
            if idx <= 0 or idx in seen:
                continue
            seen.add(idx)
            requests.append(
                LiveCandidateRequest(idx=idx, retry=bool(item.get("retry")))
            )
        return requests

    def _prepare_signals(
        self,
        requests: Sequence[LiveCandidateRequest],
    ) -> None:
        for request in requests:
            self.agent_signals.setdefault(
                request.idx,
                FileSignal(
                    self.control_dir / f"kill-agent-{request.idx:03d}",
                    self.deadline,
                ),
            )

    def drain(self) -> list[LiveCandidateRequest]:
        with locked_candidate_requests(self.path):
            payload = read_json(self.path)
            requests = self._requests(payload)
            if requests:
                write_json_atomic(
                    self.path,
                    {
                        "accepting": bool(payload.get("accepting")),
                        "requests": [],
                    },
                )
        self._prepare_signals(requests)
        return requests

    def close_if_idle(self) -> list[LiveCandidateRequest] | None:
        with locked_candidate_requests(self.path):
            payload = read_json(self.path)
            requests = self._requests(payload)
            if requests:
                write_json_atomic(
                    self.path,
                    {"accepting": True, "requests": []},
                )
            else:
                write_json_atomic(
                    self.path,
                    {"accepting": False, "requests": []},
                )
        if requests:
            self._prepare_signals(requests)
            return requests
        super().deactivate()
        return None

    def deactivate(self) -> None:
        with locked_candidate_requests(self.path):
            write_json_atomic(
                self.path,
                {"accepting": False, "requests": []},
            )
        super().deactivate()


class FileBackedSynthesisConfigCoordinator(LiveSynthesisConfigCoordinator):
    """Read the latest desktop synthesis settings at the stage boundary."""

    def __init__(self, path: Path, config: Dict[str, Any]) -> None:
        self.path = path
        super().__init__(
            int(config.get("synthesis_agents") or 0),
            config.get("synthesis_model"),
            config.get("synthesis_effort"),
            config.get("synthesis_fast"),
        )
        with locked_synthesis_config(path):
            if not path.exists():
                write_json_atomic(
                    path,
                    {
                        "agents": int(config.get("synthesis_agents") or 0),
                        "model": config.get("synthesis_model"),
                        "effort": config.get("synthesis_effort"),
                        "fast": config.get("synthesis_fast"),
                        "locked": False,
                    },
                )

    def close_and_snapshot(self):  # type: ignore[no-untyped-def]
        with locked_synthesis_config(self.path):
            payload = read_json(self.path)
            if not bool(payload.get("locked")):
                self.update(
                    int(payload.get("agents") or 0),
                    payload.get("model"),
                    payload.get("effort"),
                    payload.get("fast"),
                )
            configuration = super().close_and_snapshot()
            write_json_atomic(
                self.path,
                {
                    "agents": configuration.agents,
                    "model": configuration.model,
                    "effort": configuration.effort,
                    "fast": configuration.fast,
                    "locked": True,
                },
            )
            return configuration

    def deactivate(self) -> None:
        with locked_synthesis_config(self.path):
            super().deactivate()
            payload = read_json(self.path)
            payload["locked"] = True
            write_json_atomic(self.path, payload)


def _namespace(
    run: ManagedRun,
    cancel: FileSignal,
    agent_signals: Dict[int, FileSignal],
    num_agents: int,
) -> argparse.Namespace:
    config = run.config
    args = argparse.Namespace(
        prompt=run.prompt,
        prompt_file=None,
        num_agents=num_agents,
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
            else 60
        ),
        subagents=bool(config.get("subagents", False)),
        subagents_limit=int(config.get("subagents_limit") or 8),
        resume=False,
        resume_session_id=config.get("resume_session_id"),
        resume_include_non_interactive=False,
        no_sync_back=True,
        keep_workspaces=True,
        workspace_mode=config.get("workspace_mode", "isolated"),
        cancel_event=cancel,
        agent_cancel_events=agent_signals,
    )
    validate_args(args)
    return args


def _write_response(
    state_dir: Path,
    operation_id: str,
    *,
    run_id: str,
    operation: str,
    exit_code: int,
    error: str,
    result: Dict[str, Any] | None,
) -> None:
    write_json_atomic(
        worker_response_path(state_dir, operation_id),
        {
            "protocol": WORKER_PROTOCOL_VERSION,
            "run_id": run_id,
            "operation": operation,
            "operation_id": operation_id,
            "ok": exit_code == 0,
            "exit_code": exit_code,
            "error": error or None,
            "result": result,
            "finished_at": utc_now(),
        },
    )


def _manager_operation(
    state_dir: Path,
    operation: str,
    request: Dict[str, Any],
    run: ManagedRun,
) -> Dict[str, Any]:
    # Import before any task can edit PCR, then keep this process's module objects.
    from ..plugin_runtime import PluginRunManager

    manager = PluginRunManager(state_dir, detached_workers=False)
    try:
        if operation == "finalize":
            return manager._accept_agent(
                run.run_id,
                int(request.get("agent") or 0),
                0.0,
                stop_active=False,
            )
        if operation == "discard":
            return manager._discard_run(
                run.run_id,
                bool(request.get("keep_workspaces")),
                0.0,
                stop_active=False,
            )
        if operation == "recover_finalization":
            return manager._recover_finalization(
                run.run_id,
                bool(request.get("sync_was_applied")),
            )
        raise RuntimeError(f"Unsupported PCR manager operation: {operation}")
    finally:
        manager.close(wait_seconds=0)


def _execute_request(
    state_dir: Path,
    request_path: Path,
    *,
    expected_run_id: str | None = None,
    shutdown: FileSignal | None = None,
) -> tuple[int, bool, str]:
    request = read_json(request_path)
    if int(request.get("protocol") or 0) != WORKER_PROTOCOL_VERSION:
        raise RuntimeError(f"Unsupported PCR worker request: {request_path}")
    run_data = request.get("run")
    if not isinstance(run_data, dict):
        raise RuntimeError(f"Invalid PCR worker request: {request_path}")
    run = ManagedRun.from_dict(run_data)
    if expected_run_id is not None and run.run_id != expected_run_id:
        raise RuntimeError(
            f"Worker for {expected_run_id} rejected a request for {run.run_id}"
        )
    operation = str(request.get("operation") or "initial")
    operation_id = str(request.get("operation_id") or request_path.stem)
    indices = normalized_indices(request.get("indices") or [])
    retry_indices = set(normalized_indices(request.get("retry_indices") or []))
    agent_role = normalize_agent_role(request.get("agent_role"))
    fingerprint = request.get("retained_startup_fingerprint")
    execution_fingerprints = request.get("retained_execution_fingerprints")
    synthesis_preparations = request.get("synthesis_preparation_fingerprints")
    if synthesis_preparations is not None and (
        operation != "retry" or fingerprint is not None or execution_fingerprints is not None
        or agent_role != AGENT_ROLE_SYNTHESIS or bool(request.get("refresh_synthesis"))
        or not isinstance(synthesis_preparations, dict) or not indices
        or set(synthesis_preparations) != {str(idx) for idx in indices} or retry_indices != set(indices)
        or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
               for value in synthesis_preparations.values())
    ):
        raise RuntimeError("Synthesis preparation recovery must match the exact unsubmitted synthesis slots")
    if execution_fingerprints is not None and (
        operation != "retry" or fingerprint is not None or agent_role != "candidate"
        or not isinstance(execution_fingerprints, dict)
        or set(execution_fingerprints) != {str(idx) for idx in indices}
        or retry_indices != set(indices) or not indices
        or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
               for value in execution_fingerprints.values())
    ):
        raise RuntimeError("Retained execution recovery must match the exact candidate retry request")
    if fingerprint is not None and (
        operation != "retry" or not isinstance(fingerprint, str) or len(fingerprint) != 64
        or len(indices) != 1 or retry_indices != set(indices)
        or agent_role != "candidate" or bool(request.get("refresh_synthesis"))
    ):
        raise RuntimeError("Retained startup recovery cannot create an initial run or a different operation")
    refresh_synthesis = bool(request.get("refresh_synthesis"))
    selected = indices or list(range(1, int(run.config["num_agents"]) + 1))
    control_dir = state_dir / "control" / run.run_id
    cancel = FileSignal(control_dir / "stop", run.worker_deadline)
    agent_signals = FileSignalMap(control_dir, run.worker_deadline, selected)
    events = EventLog(state_dir / "events")
    artifacts = ArtifactStore()
    status_path = worker_status_path(state_dir, run.run_id)
    status = read_json(status_path)
    status.update(
        {
            "protocol": WORKER_PROTOCOL_VERSION,
            "run_id": run.run_id,
            "operation": operation,
            "operation_id": operation_id,
            "pid": os.getpid(),
            "alive": True,
            "active": True,
            "started_at": utc_now(),
            "finished_at": None,
            "error": None,
        }
    )
    write_json_atomic(status_path, status)

    def report(payload: Dict[str, Any]) -> None:
        kind = str(payload.get("type") or "")
        if kind == "run_prepared":
            rows = payload.get("rows")
            if isinstance(rows, list):
                values = {
                    str(row[0]): str(row[1])
                    for row in rows
                    if isinstance(row, (list, tuple)) and len(row) == 2
                }
                run.run_root = values.get("RUNS_ROOT", run.run_root)
                if run.run_root:
                    artifacts.write_marker(run)
        elif kind == "agent_finished":
            try:
                idx = int(payload.get("idx") or 0)
            except (TypeError, ValueError):
                idx = 0
            result = payload.get("result")
            if idx > 0 and isinstance(result, dict):
                run.results[idx] = dict(result)
        events.append(run.run_id, payload)

    report(
        {
            "type": "plugin_operation_started",
            "operation": operation,
            "operation_id": operation_id,
            "pid": os.getpid(),
        }
    )
    exit_code = 1
    error = ""
    result_payload: Dict[str, Any] | None = None
    terminal_operation = operation in {"finalize", "discard"}
    with installed_signal_handlers(cancel, shutdown):
        try:
            if operation in {"finalize", "discard", "recover_finalization"}:
                result_payload = _manager_operation(
                    state_dir,
                    operation,
                    request,
                    run,
                )
                exit_code = 0
            elif operation == "async_answer":
                from ..async_answers import run_idle_answer

                if len(indices) != 1 or retry_indices or not isinstance(request.get("previous_result"), dict):
                    raise RuntimeError("An async answer must target one unchanged original Agent")
                result = run_idle_answer(run, indices[0], request["request_id"], request["previous_result"],
                    report, cancel, agent_signals[indices[0]])
                report({"type": "batch_finished", "cancelled": cancel.is_set()})
                exit_code = 0 if result.status == "success" else 1
            else:
                args = _namespace(run, cancel, agent_signals, len(selected))
                if operation == "initial":
                    live_candidates = FileBackedCandidateCoordinator(
                        worker_candidate_requests_path(state_dir, run.run_id),
                        agent_signals,
                        control_dir,
                        run.worker_deadline,
                    )
                    args.live_candidate_coordinator = live_candidates
                    args.live_synthesis_config_coordinator = (
                        FileBackedSynthesisConfigCoordinator(
                            worker_synthesis_config_path(state_dir, run.run_id),
                            run.config,
                        )
                    )
                    try:
                        exit_code = int(
                            run_once(
                                args,
                                run.prompt,
                                progress_callback=report,
                                print_output=False,
                            )
                        )
                    finally:
                        live_candidates.deactivate()
                elif operation in {"more", "retry"}:
                    if not run.run_root:
                        raise RuntimeError("Additional Agent worker has no run root")
                    recorded_results: list[AgentResult] = []
                    for value in run.results.values():
                        try:
                            recorded_results.append(AgentResult(**value))
                        except (TypeError, ValueError):
                            continue
                    rejected = {
                        int(value)
                        for value in run.rejected_agents
                        if int(value) > 0
                    }
                    stale_synthesis = {
                        int(value)
                        for value in run.stale_synthesis_agents
                        if int(value) > 0
                    }
                    if agent_role == AGENT_ROLE_SYNTHESIS:
                        run_synthesis_refresh(
                            args=args,
                            prompt=run.prompt,
                            run_root=Path(run.run_root),
                            workspace=artifacts.workspace(run),
                            recorded_results=recorded_results,
                            resume_session_id=run.config.get("resume_session_id"),
                            progress_callback=report,
                            cancel_event=cancel,
                            agent_cancel_events=agent_signals,
                            synthesis_indices=selected,
                            retry_synthesis_indices=retry_indices,
                            recommendation_excluded_indices=rejected,
                            synthesis_preparation_fingerprints=({int(idx): value for idx, value in synthesis_preparations.items()}
                                if synthesis_preparations is not None else None),
                        )
                    else:
                        run_additional_agents(
                            args=args,
                            prompt=run.prompt,
                            agent_indices=selected,
                            run_root=Path(run.run_root),
                            workspace=artifacts.workspace(run),
                            resume_session_id=run.config.get("resume_session_id"),
                            retry_indices=retry_indices,
                            progress_callback=report,
                            cancel_event=cancel,
                            agent_cancel_events=agent_signals,
                            recommendation_excluded_indices=(
                                rejected | stale_synthesis
                            ),
                            retained_startup_fingerprint=request.get("retained_startup_fingerprint"),
                            retained_execution_fingerprints=({int(idx): value for idx, value in execution_fingerprints.items()}
                                if execution_fingerprints is not None else None),
                        )
                        if refresh_synthesis and not cancel.is_set():
                            recorded_results = []
                            for value in run.results.values():
                                try:
                                    recorded_results.append(AgentResult(**value))
                                except (TypeError, ValueError):
                                    continue
                            run_synthesis_refresh(
                                args=args,
                                prompt=run.prompt,
                                run_root=Path(run.run_root),
                                workspace=artifacts.workspace(run),
                                recorded_results=recorded_results,
                                resume_session_id=run.config.get("resume_session_id"),
                                progress_callback=report,
                                cancel_event=cancel,
                                agent_cancel_events=agent_signals,
                                recommendation_excluded_indices=rejected,
                            )
                    report(
                        {
                            "type": "batch_finished",
                            "cancelled": cancel.is_set(),
                        }
                    )
                    exit_code = 130 if cancel.is_set() else 0
                else:
                    raise RuntimeError(
                        f"Unsupported PCR worker operation: {operation}"
                    )
        except BaseException as exc:  # noqa: BLE001
            if isinstance(exc, SystemExit):
                code = exc.code
                exit_code = int(code) if isinstance(code, int) else 1
                error = str(code or "")
            else:
                error = f"{type(exc).__name__}: {exc}"
                exit_code = 1
            if operation == "async_answer" and isinstance(request.get("previous_result"), dict):
                meta = Path(run.run_root) / "meta" / f"agent_{indices[0]:03d}"
                previous_answer = meta / "answers" / request["request_id"] / "final_message.md"
                if previous_answer.is_file() and not (meta / "final_message.md").exists():
                    from .artifacts import write_text_atomic
                    write_text_atomic(meta / "final_message.md", previous_answer.read_text(encoding="utf-8"))
                report({"type": "agent_finished", "idx": indices[0], "result": request["previous_result"]})
                report({"type": "batch_finished", "cancelled": cancel.is_set()})
            if operation == "initial":
                report({"type": "run_failed", "message": error or "PCR worker failed"})
            elif operation in {"more", "retry"}:
                report(
                    {
                        "type": "batch_failed",
                        "message": error or "PCR worker failed",
                    }
                )
            else:
                report(
                    {
                        "type": "plugin_operation_failed",
                        "operation": operation,
                        "operation_id": operation_id,
                        "message": error or "PCR worker operation failed",
                    }
                )

    expired = cancel.deadline_elapsed
    cleanup_error = ""
    cleanup_attempted = False
    # An execution timeout is not permission to discard partial work or its
    # session. Explicit cleanup and the separate retention policy own deletion.
    workspaces_deleted = run.workspaces_deleted
    codex_homes_deleted = run.codex_homes_deleted
    report(
        {
            "type": "plugin_operation_finished",
            "operation": operation,
            "operation_id": operation_id,
            "exit_code": exit_code,
            "expired": expired,
            "cleanup_attempted": cleanup_attempted,
            "workspaces_deleted": workspaces_deleted,
            "codex_homes_deleted": codex_homes_deleted,
            "cleanup_error": cleanup_error or None,
        }
    )
    status.update(
        {
            "active": False,
            "operation": "",
            "operation_id": "",
            "finished_at": utc_now(),
            "last_operation": operation,
            "last_operation_id": operation_id,
            "exit_code": exit_code,
            "expired": expired,
            "cleanup_attempted": cleanup_attempted,
            "workspaces_deleted": workspaces_deleted,
            "codex_homes_deleted": codex_homes_deleted,
            "error": error or None,
            "cleanup_error": cleanup_error or None,
        }
    )
    write_json_atomic(status_path, status)
    if bool(request.get("expect_response")):
        _write_response(
            state_dir,
            operation_id,
            run_id=run.run_id,
            operation=operation,
            exit_code=exit_code,
            error=error,
            result=result_payload,
        )
    should_exit = terminal_operation and exit_code == 0
    # The deadline limits this operation, not the time the completed run may wait
    # for the user to accept, continue, or discard it. A successful operation
    # leaves the version-pinned worker alive until a terminal operation shuts it
    # down explicitly.
    return exit_code, should_exit or expired, run.worker_deadline


def execute_request(state_dir: Path, request_path: Path) -> int:
    exit_code, _should_exit, _expires_at = _execute_request(
        state_dir,
        request_path,
    )
    return exit_code


def _request_paths(state_dir: Path, run_id: str) -> list[Path]:
    requests_dir = state_dir / "workers" / "requests"
    return sorted(requests_dir.glob(f"{run_id}-*.json"))


def _restore_parent_pythonpath() -> None:
    if WORKER_PACKAGE_ROOT_ENV not in os.environ:
        return
    os.environ.pop(WORKER_PACKAGE_ROOT_ENV, None)
    parent = os.environ.pop(WORKER_PARENT_PYTHONPATH_ENV, "")
    present = os.environ.pop(WORKER_PARENT_PYTHONPATH_PRESENT_ENV, "") == "1"
    if present:
        os.environ["PYTHONPATH"] = parent
    else:
        os.environ.pop("PYTHONPATH", None)


def serve_run(state_dir: Path, run_id: str) -> int:
    if RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise RuntimeError(f"Invalid PCR worker run id: {run_id}")
    _restore_parent_pythonpath()
    preload_plugin_worker_runtime()
    publication = os.environ.pop("PCR_WORKER_PUBLICATION_OPERATION_ID", "")
    publisher = int(os.environ.pop("PCR_WORKER_PUBLICATION_PARENT_PID", "0"))
    if publication:
        # Do not emit events or touch private history until the real owner PID
        # is durable. Older schedulers may otherwise race the handoff commit.
        while True:
            if (state_dir / "control" / run_id / "stop").exists():
                return 130
            values = read_json(state_dir / "runs.json").get("runs", [])
            owner = next((value for value in values if value.get("run_id") == run_id), {})
            if (owner.get("worker_pid") == os.getpid() and owner.get("worker_operation_id") == publication
                and owner.get("worker_state") == "busy" and owner.get("status") == "running"):
                break
            if not pid_is_alive(publisher):
                return 1
            time.sleep(WORKER_POLL_SECONDS)
    events = EventLog(state_dir / "events")
    status_path = worker_status_path(state_dir, run_id)
    shutdown = FileSignal(state_dir / "control" / run_id / "shutdown")
    shutdown.clear()
    status = read_json(status_path)
    status.update(
        {
            "protocol": WORKER_PROTOCOL_VERSION,
            "run_id": run_id,
            "pid": os.getpid(),
            "alive": True,
            "active": False,
            "started_at": utc_now(),
            "stopped_at": None,
            "stop_reason": None,
        }
    )
    write_json_atomic(status_path, status)
    events.append(
        run_id,
        {
            "type": "plugin_worker_started",
            "protocol": WORKER_PROTOCOL_VERSION,
            "pid": os.getpid(),
        },
    )

    exit_code = 0
    stop_reason = "shutdown"
    with installed_signal_handlers(shutdown, shutdown):
        while not shutdown.is_set():
            requests = _request_paths(state_dir, run_id)
            if not requests:
                time.sleep(WORKER_POLL_SECONDS)
                continue
            request_path = requests[0]
            try:
                exit_code, should_exit, _request_expiry = _execute_request(
                    state_dir,
                    request_path,
                    expected_run_id=run_id,
                    shutdown=shutdown,
                )
                if should_exit:
                    request = read_json(request_path)
                    stop_reason = str(request.get("operation") or "completed")
                    break
            except BaseException as exc:  # noqa: BLE001
                exit_code = 1
                request = read_json(request_path)
                operation = str(request.get("operation") or "unknown")
                operation_id = str(
                    request.get("operation_id") or request_path.stem
                )
                message = f"{type(exc).__name__}: {exc}"
                events.append(
                    run_id,
                    {
                        "type": "plugin_operation_failed",
                        "operation": operation,
                        "operation_id": operation_id,
                        "message": message,
                    },
                )
                events.append(
                    run_id,
                    {
                        "type": "plugin_operation_finished",
                        "operation": operation,
                        "operation_id": operation_id,
                        "exit_code": exit_code,
                        "expired": False,
                    },
                )
                status = read_json(status_path)
                status.update(
                    {
                        "active": False,
                        "operation": "",
                        "operation_id": "",
                        "finished_at": utc_now(),
                        "error": message,
                    }
                )
                write_json_atomic(status_path, status)
                if bool(request.get("expect_response")):
                    _write_response(
                        state_dir,
                        operation_id,
                        run_id=run_id,
                        operation=operation,
                        exit_code=exit_code,
                        error=message,
                        result=None,
                    )
            finally:
                try:
                    request_path.unlink()
                except OSError:
                    pass

    events.append(
        run_id,
        {
            "type": "plugin_worker_stopped",
            "pid": os.getpid(),
            "reason": stop_reason,
            "exit_code": exit_code,
        },
    )
    status = read_json(status_path)
    status.update(
        {
            "alive": False,
            "active": False,
            "operation": "",
            "operation_id": "",
            "stopped_at": utc_now(),
            "stop_reason": stop_reason,
            "exit_code": exit_code,
        }
    )
    write_json_atomic(status_path, status)
    return exit_code


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Serve one persistent Parallel Codex Runner plugin run."
    )
    parser.add_argument("--state-dir", required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run-id")
    group.add_argument("--request")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    state_dir = Path(args.state_dir).expanduser().resolve()
    if args.run_id:
        return serve_run(state_dir, str(args.run_id))
    _restore_parent_pythonpath()
    request_path = Path(args.request).expanduser().resolve()
    try:
        return execute_request(state_dir, request_path)
    finally:
        expected_parent = (state_dir / "workers" / "requests").resolve()
        if request_path.parent == expected_parent and not request_path.is_dir():
            try:
                request_path.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
