import copy
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from parallel_codex_runner_core import app, plugin_runtime
from parallel_codex_runner_core.models import AgentResult
from parallel_codex_runner_core.plugin import worker
from parallel_codex_runner_core.plugin.lifecycle import WORKER_PROTOCOL_VERSION
from parallel_codex_runner_core.plugin.state import ManagedRun
from parallel_codex_runner_core.synthesis import create_synthesis_context, failed_synthesis_preparation_fingerprint
from .test_plugin_runtime import make_result


@pytest.fixture
def failed_synthesis(tmp_path):
    root = tmp_path / "runs/20261016_010000"
    root.mkdir(parents=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    prompt = "original synthetic task"
    (root / "prompt.txt").write_text(prompt)
    manager = plugin_runtime.PluginRunManager(tmp_path / "state", detached_workers=True, run_maintenance=False)
    run = ManagedRun("pcr-20261016T010000Z-11111111", prompt, str(workspace),
        {"num_agents": 8, "synthesis_agents": 3, "workspace_mode": "isolated", "run_base": str(root.parent)},
        run_root=str(root), status="completed", artifact_token="synthesis-schema-fixture",
        worker_pid=os.getpid(), worker_protocol=WORKER_PROTOCOL_VERSION, worker_state="idle")
    manager._runs[run.run_id] = run
    manager._write_run_marker_locked(run)
    for idx in range(1, 9):
        result = make_result(root, idx, tokens=100)
        (Path(result.workspace_dir) / "preserved.bin").write_bytes(b"original candidate result")
        manager._append_event_locked(run, {"type": "agent_finished", "idx": idx, "result": result.__dict__})
    create_synthesis_context(root, prompt, [AgentResult(**value) for value in run.results.values()])
    manager._append_event_locked(run, {"type": "synthesis_started", "indices": [9, 10, 11], "source_agents": list(range(1, 9))})
    for idx in (9, 10, 11):
        result = make_result(root, idx, tokens=0)
        # Match the actual failure boundary: no native command, workspace or history.
        for path in Path(result.workspace_dir).iterdir():
            path.unlink()
        Path(result.workspace_dir).rmdir()
        for path in Path(result.meta_dir).iterdir():
            if path.is_file():
                path.unlink()
        Path(result.codex_home).mkdir(exist_ok=True)
        result.role, result.status, result.command = "synthesis", "error", []
        result.returncode, result.seconds, result.codex_thread_id = None, 0.0, None
        result.error = "synthesis preparation failed: Unsupported Codex history schema; no global database was copied: guardian_review_feedback, thread_read_receipts"
        value = json.loads(json.dumps(result.__dict__, default=str))
        (Path(result.meta_dir) / "status.json").write_text(json.dumps(value))
        manager._append_event_locked(run, {"type": "agent_finished", "idx": idx, "result": value})
    run.status, run.worker_state, run.worker_pid = "completed", "idle", os.getpid()
    checkpoint = {"protocol": WORKER_PROTOCOL_VERSION, "run_id": run.run_id, "pid": run.worker_pid,
        "alive": True, "active": False, "operation": "", "operation_id": "", "last_operation": "initial",
        "finished_at": "2026-10-16T01:10:00Z"}
    path = manager.state_dir / "workers" / f"{run.run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(checkpoint))
    manager._save_state_locked()
    try:
        yield manager, run, checkpoint, path
    finally:
        manager._worker_processes.clear()
        manager.close(wait_seconds=0)


def retiring(manager, run, checkpoint, path, mutation=None):
    def retire(signal):
        assert signal.path == manager.control_dir / run.run_id / "shutdown"
        manager._events.append(run.run_id, {"type": "plugin_worker_stopped", "pid": run.worker_pid, "reason": "shutdown"})
        path.write_text(json.dumps({**checkpoint, "alive": False, "stop_reason": "shutdown"}))
        if mutation:
            mutation()
    return retire


def test_idle_handoff_dispatches_only_three_syntheses_and_preserves_candidates(failed_synthesis):
    manager, run, checkpoint, path = failed_synthesis
    before = copy.deepcopy(run.results)
    files = {p: (p.stat().st_ino, p.read_bytes()) for idx in range(1, 9)
        for p in Path(before[idx]["workspace_dir"]).iterdir() if p.is_file()}
    observed = []

    def observer():
        assert run.worker_pid is None and run.worker_state == "stopped"
        observed.append(True)

    with patch.object(plugin_runtime.FileSignal, "set", retiring(*failed_synthesis)), \
         patch.object(plugin_runtime, "validate_args"), \
         patch.object(manager, "_build_args", return_value=SimpleNamespace()), \
         patch.object(plugin_runtime, "spawn_worker", return_value=(SimpleNamespace(pid=54321, poll=lambda: None), "op")) as spawn:
        result = manager.recover_synthesis_preparation(run.run_id, [9, 10, 11], owner_retired=observer)
        assert result["status"] == "running"
        assert observed == [True]
        request = spawn.call_args.args[3]
        assert request["indices"] == request["retry_indices"] == [9, 10, 11]
        assert set(request["synthesis_preparation_fingerprints"]) == {"9", "10", "11"}
        assert request["agent_role"] == "synthesis" and not request["refresh_synthesis"]
        assert spawn.call_args.kwargs["wait_for_publication"] is True
        assert request["run"]["config"] == run.config
        assert {idx: value for idx, value in run.results.items()} == {idx: before[idx] for idx in range(1, 9)}
        for p, identity in files.items():
            assert (p.stat().st_ino, p.read_bytes()) == identity
        with pytest.raises(plugin_runtime.PluginRunError):
            manager.recover_synthesis_preparation(run.run_id, [9, 10, 11])
        assert spawn.call_count == 1


def test_late_prepublication_reader_cannot_erase_new_owner_with_same_event_timestamp(failed_synthesis):
    manager, run, checkpoint, path = failed_synthesis
    observers = []

    def spawn(*_, **__):
        observer = plugin_runtime.PluginRunManager(manager.state_dir, detached_workers=True, run_maintenance=False)
        observed = observer._require_run_locked(run.run_id)
        assert observed.worker_pid is None and observed.worker_state == "stopped"
        observers.append(observer)
        return SimpleNamespace(pid=54321, poll=lambda: None), "new-operation"

    try:
        with patch.object(plugin_runtime.FileSignal, "set", retiring(*failed_synthesis)), \
             patch.object(plugin_runtime, "validate_args"), \
             patch.object(manager, "_build_args", return_value=SimpleNamespace()), \
             patch.object(plugin_runtime, "spawn_worker", side_effect=spawn):
            result = manager.recover_synthesis_preparation(run.run_id, [9, 10, 11])
        # This reader is still holding the stopped record from the batch event.
        observers[0]._save_state_locked()
        disk = json.loads((manager.state_dir / "runs.json").read_text())["runs"][0]
        assert disk["worker_pid"] == 54321 and disk["worker_state"] == "busy"
        assert disk["worker_operation_id"] == result["recovery_operation_id"]
    finally:
        for observer in observers:
            observer._worker_processes.clear()
            observer.close(wait_seconds=0)


@pytest.mark.parametrize("change", ["command", "receipt", "workspace", "home", "thread", "context", "stop", "agent-stop", "source", "busy", "queue", "newer"])
def test_unsafe_or_changed_failure_never_retires_owner_or_dispatches(failed_synthesis, change):
    manager, run, checkpoint, path = failed_synthesis
    root = Path(run.run_root)
    meta = root / "meta/agent_009"
    if change == "command":
        (meta / "command.json").write_text("{}")
    elif change == "receipt":
        (meta / "execution_recovery.json").write_text('{"stage":"submitting"}')
    elif change == "workspace":
        (root / "workspaces/agent_009").mkdir()
    elif change == "home":
        (meta / "codex_home/state_5.sqlite").write_bytes(b"possibly accepted native state")
    elif change == "thread":
        run.results[9]["codex_thread_id"] = "original-thread"
    elif change == "context":
        (root / "prompt.txt").write_text("not the original request")
    elif change in {"stop", "agent-stop"}:
        control = manager.control_dir / run.run_id
        control.mkdir(parents=True, exist_ok=True)
        (control / ("stop" if change == "stop" else "kill-agent-009")).touch()
    elif change == "source":
        run.results[1]["seconds"] += 1
    elif change == "busy":
        run.worker_operation = "more"
    elif change == "queue":
        requests = manager.state_dir / "workers/requests"
        requests.mkdir(parents=True)
        (requests / f"{run.run_id}-next.json").write_text("{}")
    else:
        other = ManagedRun("pcr-20261016T020000Z-22222222", "later task", run.workspace, {}, created_at="9999-01-01T00:00:00Z")
        manager._runs[other.run_id] = other
        manager._save_state_locked()
    with patch.object(plugin_runtime.FileSignal, "set") as retire, \
         patch.object(plugin_runtime, "validate_args"), \
         patch.object(manager, "_build_args", return_value=SimpleNamespace()), \
         patch.object(plugin_runtime, "spawn_worker") as spawn:
        with pytest.raises((ValueError, plugin_runtime.PluginRunError)):
            manager.recover_synthesis_preparation(run.run_id, [9, 10, 11])
        retire.assert_not_called()
        spawn.assert_not_called()


@pytest.mark.parametrize("change", ["stop", "context", "observer-failed"])
def test_late_changes_and_observer_failure_cannot_trigger_a_new_submission(failed_synthesis, change):
    manager, run, checkpoint, path = failed_synthesis
    original = copy.deepcopy(run.results)

    def observer():
        if change == "stop":
            (manager.control_dir / run.run_id / "stop").touch()
        elif change == "context":
            (Path(run.run_root) / "synthesis_context.md").write_text("changed")
        else:
            raise OSError("daemon observation unavailable")

    with patch.object(plugin_runtime.FileSignal, "set", retiring(*failed_synthesis)), \
         patch.object(plugin_runtime, "validate_args"), \
         patch.object(manager, "_build_args", return_value=SimpleNamespace()), \
         patch.object(plugin_runtime, "spawn_worker") as spawn:
        with pytest.raises((OSError, ValueError, plugin_runtime.PluginRunError)):
            manager.recover_synthesis_preparation(run.run_id, [9, 10, 11], owner_retired=observer)
        spawn.assert_not_called()
    assert run.results == original


def test_worker_rejects_wrong_role_or_incomplete_preparation_binding(failed_synthesis):
    manager, run, _, _ = failed_synthesis
    for change in ({"agent_role": "candidate"}, {"indices": [9]}, {"operation": "initial"}, {"refresh_synthesis": True}):
        request = {"protocol": WORKER_PROTOCOL_VERSION, "run": run.to_dict(), "operation": "retry", "indices": [9, 10, 11],
            "retry_indices": [9, 10, 11], "agent_role": "synthesis", "synthesis_preparation_fingerprints": {str(idx): "a" * 64 for idx in (9, 10, 11)}, **change}
        target = manager.state_dir / "fixture-request.json"
        target.write_text(json.dumps(request, default=str))
        with patch.object(worker, "run_synthesis_refresh") as execute:
            with pytest.raises(RuntimeError, match="Synthesis preparation recovery"):
                worker._execute_request(manager.state_dir, target, expected_run_id=run.run_id)
            execute.assert_not_called()


def test_preparation_changed_after_queueing_is_rejected_before_context_or_workspace_writes(failed_synthesis):
    _, run, _, _ = failed_synthesis
    root = Path(run.run_root)
    fingerprints = {idx: failed_synthesis_preparation_fingerprint(root, idx, run.prompt) for idx in (9, 10, 11)}
    (root / "meta/agent_010/execution_recovery.json").write_text('{"stage":"accepted"}')
    original_context = (root / "synthesis_context.md").read_bytes()
    with patch.object(app, "run_additional_agents") as execute:
        with pytest.raises(ValueError, match="native preparation"):
            app.run_synthesis_refresh(args=SimpleNamespace(num_agents=8, synthesis_agents=3), prompt=run.prompt,
                run_root=root, workspace=Path(run.workspace), recorded_results=[AgentResult(**value) for value in run.results.values()],
                synthesis_indices=[9, 10, 11], retry_synthesis_indices={9, 10, 11}, synthesis_preparation_fingerprints=fingerprints)
        execute.assert_not_called()
    assert (root / "synthesis_context.md").read_bytes() == original_context


@pytest.mark.parametrize("alive", [True, False])
def test_old_daemon_observer_waits_for_exit_and_reaps_cached_owner_before_publication(alive):
    script = Path(__file__).resolve().parents[1] / "scripts/recover_synthesis_preparation.py"
    spec = importlib.util.spec_from_file_location("synthesis_recovery_cli", script)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    observed = []

    def request(*_):
        observed.append(True)
        return {"worker_pid": None, "worker_state": "stopped", "worker_active": False}

    with patch.object(cli, "pid_is_alive", return_value=alive):
        if alive:
            with pytest.raises(RuntimeError, match="has not exited"):
                cli.observe_retired_owner(SimpleNamespace(request=request), "original-run", 123, timeout=0)
            assert len(observed) == 1
        else:
            cli.observe_retired_owner(SimpleNamespace(request=request), "original-run", 123, timeout=0)
            assert len(observed) == 2
