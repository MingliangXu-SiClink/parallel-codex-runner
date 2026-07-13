from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any, Dict, Sequence

from ..app import run_additional_agents, run_once, validate_args
from ..workspace import cleanup_workspace_copies
from .artifacts import ArtifactStore
from .events import EventLog
from .lifecycle import (
    FileSignal,
    installed_signal_handlers,
    normalized_indices,
    read_json,
    worker_status_path,
    write_json_atomic,
)
from .state import ManagedRun, utc_now


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


def execute_request(state_dir: Path, request_path: Path) -> int:
    request = read_json(request_path)
    run_data = request.get("run")
    if not isinstance(run_data, dict):
        raise RuntimeError(f"Invalid PCR worker request: {request_path}")
    run = ManagedRun.from_dict(run_data)
    operation = str(request.get("operation") or "initial")
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
            "run_id": run.run_id,
            "operation": operation,
            "pid": os.getpid(),
            "active": True,
            "started_at": utc_now(),
            "finished_at": None,
            "error": None,
        }
    )
    write_json_atomic(status_path, status)

    def report(payload: Dict[str, Any]) -> None:
        if payload.get("type") == "run_prepared":
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
        events.append(run.run_id, payload)

    report(
        {
            "type": "plugin_worker_started",
            "operation": operation,
            "pid": os.getpid(),
        }
    )
    exit_code = 1
    error = ""
    with installed_signal_handlers(cancel):
        try:
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
                raise RuntimeError(f"Unsupported PCR worker operation: {operation}")
        except BaseException as exc:  # noqa: BLE001
            if isinstance(exc, SystemExit):
                code = exc.code
                exit_code = int(code) if isinstance(code, int) else 1
                error = str(code or "")
            else:
                error = f"{type(exc).__name__}: {exc}"
                exit_code = 1
            report(
                {
                    "type": "run_failed" if operation == "initial" else "batch_failed",
                    "message": error or "PCR worker failed",
                }
            )

    expired = cancel.deadline_elapsed
    cleanup_error = ""
    if expired and run.run_root and not bool(run.config.get("keep_workspaces")):
        try:
            workspaces_root = artifacts.workspaces_root(run)
            cleanup_workspace_copies(artifacts.workspace(run), workspaces_root)
            artifacts.remove_codex_homes(run)
        except Exception as exc:  # noqa: BLE001
            cleanup_error = str(exc)
    report(
        {
            "type": "plugin_worker_finished",
            "operation": operation,
            "exit_code": exit_code,
            "expired": expired,
            "cleanup_error": cleanup_error or None,
        }
    )
    status.update(
        {
            "active": False,
            "finished_at": utc_now(),
            "exit_code": exit_code,
            "expired": expired,
            "error": error or None,
            "cleanup_error": cleanup_error or None,
        }
    )
    write_json_atomic(status_path, status)
    return exit_code


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Execute one detached Parallel Codex Runner plugin operation."
    )
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--request", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    return execute_request(
        Path(args.state_dir).expanduser().resolve(),
        Path(args.request).expanduser().resolve(),
    )


if __name__ == "__main__":
    raise SystemExit(main())

