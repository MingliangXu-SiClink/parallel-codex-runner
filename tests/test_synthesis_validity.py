from dataclasses import asdict
from pathlib import Path
from unittest import mock
import pytest

from parallel_codex_runner_core.plugin_runtime import ManagedRun, PluginRunManager
from tests.test_plugin_runtime import make_result


@pytest.fixture
def stopped_more(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    manager = PluginRunManager(tmp_path / "state")
    root = tmp_path / "runs" / "20261016_120000"
    run = ManagedRun(
        run_id="pcr-20261016T120000Z-1234abcd", prompt="offline fixture",
        workspace=str(workspace), config={"run_base": str(root.parent),
            "num_agents": 10, "synthesis_agents": 3, "sync_back": True},
        run_root=str(root), status="cancelled", artifact_token="fixture-token",
        stale_synthesis_agents=[9, 10, 11],
    )
    history = []
    for idx in range(1, 12):
        result = make_result(root, idx)
        result.reasoning_token_counts = {64: idx, 1024: 2}
        result.role = "candidate" if idx <= 8 else "synthesis"
        if idx == 9:
            history.append({"type": "synthesis_started", "indices": [9, 10, 11],
                "source_agents": list(range(1, 9))})
        run.results[idx] = asdict(result)
        run.agent_statuses[idx] = "success"
        run.agent_roles[idx] = result.role
        history.append({"type": "agent_finished", "idx": idx, "result": asdict(result)})
    history.extend([
        {"type": "synthesis_finished", "indices": [9, 10, 11],
            "source_agents": list(range(1, 9))},
        {"type": "batch_created", "indices": [12, 13], "retry_indices": [],
            "role": "candidate", "refresh_synthesis": True},
    ])
    for idx in (12, 13):
        result = make_result(root, idx, status="cancelled")
        run.results[idx] = asdict(result)
        run.agent_statuses[idx] = "cancelled"
        run.agent_roles[idx] = "candidate"
        history.append({"type": "agent_finished", "idx": idx, "result": asdict(result)})
    history.append({"type": "batch_finished", "cancelled": True})
    for event in history:
        manager._events.append(run.run_id, event)
    run.event_count = len(history)
    manager._runs[run.run_id] = run
    manager._write_run_marker_locked(run)
    manager._save_state_locked()
    yield manager, run
    manager.close()


def test_cancelled_more_restores_only_proven_unchanged_synthesis(stopped_more):
    manager, run = stopped_more
    original = run.to_dict()
    summary = manager.get_run(run.run_id)
    synthesis = [a for a in summary["agents"] if a["role"] == "synthesis"]
    assert len(synthesis) == 3
    assert all(not a["stale"] and a["can_continue"] for a in synthesis)
    assert run.status == "cancelled"
    assert run.results == original["results"]
    assert run.agent_statuses[12] == run.agent_statuses[13] == "cancelled"
    assert run.finalized_agent is None and not run.synced_back
    events = manager.get_events(run.run_id, limit=100)["events"]
    repairs = [e for e in events if e["type"] == "synthesis_revalidated"]
    assert len(repairs) == 1 and repairs[0]["indices"] == [9, 10, 11]
    assert repairs[0]["source_agents"] == list(range(1, 9))
    manager.get_run(run.run_id)
    assert run.event_count == len(events)
    reloaded = PluginRunManager(manager.state_dir)
    try:
        assert all(not a["stale"] for a in reloaded.get_run(run.run_id)["agents"])
    finally:
        reloaded.close()


@pytest.mark.parametrize("change", ["new_success", "changed_source", "replaced_synthesis", "missing_history", "working"])
def test_revalidation_requires_exact_retained_inputs_and_result(stopped_more, change):
    manager, run = stopped_more
    if change == "missing_history":
        manager._events.delete(run.run_id)
        run.event_count = 0
    elif change == "working":
        run.status = "running"
    else:
        idx = {"new_success": 12, "changed_source": 1, "replaced_synthesis": 9}[change]
        value = dict(run.results[idx], status="success", codex_thread_id="different-attempt")
        run.results[idx] = value
        run.agent_statuses[idx] = "success"
        if change != "replaced_synthesis":
            manager._events.append(run.run_id, {"type": "agent_finished", "idx": idx, "result": value})
    manager._save_state_locked()
    summary = manager.get_run(run.run_id)
    synthesis = {a["agent"]: a for a in summary["agents"] if a["role"] == "synthesis"}
    if change == "replaced_synthesis":
        assert synthesis[9]["stale"]
        assert not synthesis[10]["stale"] and not synthesis[11]["stale"]
    else:
        assert all(a["stale"] for a in synthesis.values())


def test_failed_more_with_no_new_success_preserves_original_synthesis(stopped_more):
    manager, run = stopped_more
    result = dict(run.results[12], status="error")
    manager._events.append(run.run_id, {"type": "agent_finished", "idx": 12, "result": result})
    manager._events.append(run.run_id, {"type": "batch_failed", "message": "offline failure"})
    assert all(not a["stale"] for a in manager.get_run(run.run_id)["agents"])


def test_a_success_then_removal_does_not_recertify_the_old_synthesis(stopped_more):
    manager, run = stopped_more
    for status in ("success", "cancelled"):
        value = dict(run.results[12], status=status)
        manager._events.append(run.run_id, {"type": "agent_finished", "idx": 12, "result": value})
    assert all(a["stale"] for a in manager.get_run(run.run_id)["agents"] if a["role"] == "synthesis")


def test_started_replacement_does_not_restore_the_previous_attempt(stopped_more):
    manager, run = stopped_more
    manager._events.append(run.run_id, {"type": "synthesis_started", "indices": [9],
        "source_agents": list(range(1, 9))})
    manager._events.append(run.run_id, {"type": "batch_finished", "cancelled": True})
    summary = manager.get_run(run.run_id)
    first = next(a for a in summary["agents"] if a["agent"] == 9)
    assert first["stale"] and not first["can_continue"]
    assert 9 not in run.results


@pytest.mark.parametrize("agent", [9, 10, 11])
def test_revalidated_continuation_keeps_the_selected_original_session(stopped_more, agent):
    manager, run = stopped_more
    Path(run.results[agent]["codex_home"]).mkdir()
    original_session = run.results[agent]["codex_thread_id"]
    with mock.patch.object(manager, "estimate", return_value={"confirmation_required": False}) as estimate, \
        mock.patch.object(manager, "accept_agent", return_value={"status": "finalized",
            "session_promotion": {"session_id": original_session}}) as accept, \
        mock.patch.object(manager, "start_run", return_value={"run_id": "offline-next-turn"}) as start:
        result = manager.continue_from_agent(run.run_id, agent, "offline follow-up", num_agents=1)
    accept.assert_called_once_with(run.run_id, agent)
    start.assert_called_once()
    assert start.call_args.kwargs["resume_session_id"] == original_session
    assert start.call_args.kwargs["continued_from_agent"] == agent
    assert start.call_args.kwargs["continued_from_run_id"] == run.run_id
    assert estimate.call_args.kwargs["resume_codex_home"] == Path(run.results[agent]["codex_home"])
    assert result["continued_from"]["session_id"] == original_session
    assert run.agent_statuses[12] == run.agent_statuses[13] == "cancelled"
