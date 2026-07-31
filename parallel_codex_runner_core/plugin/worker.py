from __future__ import annotations

import argparse
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Sequence

from ..app import run_additional_agents, run_once, validate_args
from ..runtime_pinning import preload_plugin_worker_runtime
from ..workspace import cleanup_workspace_copies
from .artifacts import ArtifactStore
from .events import EventLog
from .lifecycle import (
    WORKER_PROTOCOL_VERSION,
    WORKER_PACKAGE_ROOT_ENV,
    WORKER_PARENT_PYTHONPATH_ENV,
    WORKER_PARENT_PYTHONPATH_PRESENT_ENV,
    FileSignal,
    deadline_timestamp,
    installed_signal_handlers,
    normalized_indices,
    read_json,
    worker_response_path,
    worker_status_path,
    write_json_atomic,
)
from .state import ManagedRun, utc_now


WORKER_POLL_SECONDS = 0.25
RUN_ID_PATTERN = re.compile(r"^pcr-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")


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
        resume=False,
        resume_session_id=config.get("resume_session_id"),
        resume_include_non_interactive=False,
        no_sync_back=True,
        keep_workspaces=True,
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
    selected = indices or list(range(1, int(run.config["num_agents"]) + 1))
    control_dir = state_dir / "control" / run.run_id
    cancel = FileSignal(control_dir / "stop", run.worker_deadline)
    agent_signals = {
        idx: FileSignal(control_dir / f"kill-agent-{idx:03d}", run.worker_deadline)
        for idx in selected
    }
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
            else:
                args = _namespace(run, cancel, agent_signals, len(selected))
                if operation == "initial":
                    exit_code = int(
                        run_once(
                            args,
                            run.prompt,
                            progress_callback=report,
                            print_output=False,
                        )
                    )
                elif operation in {"more", "retry"}:
                    if not run.run_root:
                        raise RuntimeError("Additional Agent worker has no run root")
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
    workspaces_deleted = False
    codex_homes_deleted = False
    if expired and run.run_root and not bool(run.config.get("keep_workspaces")):
        cleanup_attempted = True
        try:
            artifacts.persist_successful_diffs(run)
            workspaces_root = artifacts.workspaces_root(run)
            if workspaces_root.exists():
                cleanup_workspace_copies(artifacts.workspace(run), workspaces_root)
            workspaces_deleted = not workspaces_root.exists()
            codex_homes_deleted = artifacts.remove_codex_homes(run)
        except Exception as exc:  # noqa: BLE001
            cleanup_error = str(exc)
    elif expired and not run.run_root:
        workspaces_deleted = True
        codex_homes_deleted = True
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
    return exit_code, should_exit or expired, run.expires_at


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
    expires_at = ""
    with installed_signal_handlers(shutdown, shutdown):
        while not shutdown.is_set():
            requests = _request_paths(state_dir, run_id)
            if not requests:
                expiry = deadline_timestamp(expires_at)
                if expiry is not None and time.time() >= expiry:
                    stop_reason = "expired"
                    break
                time.sleep(WORKER_POLL_SECONDS)
                continue
            request_path = requests[0]
            try:
                exit_code, should_exit, request_expiry = _execute_request(
                    state_dir,
                    request_path,
                    expected_run_id=run_id,
                    shutdown=shutdown,
                )
                if request_expiry:
                    expires_at = request_expiry
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
