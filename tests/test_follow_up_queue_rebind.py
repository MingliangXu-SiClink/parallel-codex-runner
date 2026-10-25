"""Tests for follow-up queue rebind functionality."""

from __future__ import annotations

import pytest
from unittest.mock import Mock

from parallel_codex_runner_core.remote.controller import RemoteController, RemoteControllerError
from parallel_codex_runner_core.remote.protocol import RemoteRequest
from parallel_codex_runner_core.remote.follow_up_queue import FollowUpQueueStore


@pytest.fixture
def mock_controller(tmp_path):
    """Create a mock controller with minimal setup."""
    controller = object.__new__(RemoteController)
    queue_path = tmp_path / "queues" / "follow_up_queues.json"
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    controller.follow_up_queues = FollowUpQueueStore(path=queue_path)
    controller.registry = Mock()
    controller.manager = Mock()
    controller._text = lambda body, key, required=True: body.get(key) or ""
    return controller


def test_rebind_paused_queue_to_new_run(mock_controller):
    """Rebind successfully moves paused queue items to a new run."""
    workspace_id = "ws1"
    run1_id = "run1"
    run2_id = "run2"

    # Setup mocks
    workspace_mock = Mock()
    workspace_mock.last_run_id = run2_id
    mock_controller.registry.require.return_value = workspace_mock

    mock_controller.manager.get_run = lambda run_id, **kw: {
        "id": run_id,
        "status": "success",
        "worker_active": False,
        "agents": [{"agent": 1, "status": "success", "can_continue": True}],
    }

    # Enqueue and pause
    mock_controller.follow_up_queues.enqueue(
        workspace_id=workspace_id,
        run_id=run1_id,
        conversation_id="conv1",
        item={"id": "msg-1", "prompt": "Follow-up 1"},
        selected_agent=1,
    )

    def pause_queue(queue):
        queue["state"] = "paused"
        for item in queue.get("items", []):
            item["paused"] = True
        return True

    mock_controller.follow_up_queues.mutate(workspace_id, pause_queue)
    result = mock_controller.follow_up_queues.get(workspace_id)
    queue = result["queue"]
    assert queue["state"] == "paused"

    # Rebind to run2
    request = RemoteRequest(
        action="followups.rebind",
        body={"workspace_id": workspace_id, "new_run_id": run2_id, "conversation_id": "conv1"},
    )
    result = mock_controller._followups_rebind(request)

    # Verify success - result has same structure as get()
    queue = result["queue"]
    assert queue["state"] == "waiting"
    assert queue["run_id"] == run2_id
    assert not queue["items"][0].get("paused")


def test_rebind_blocked_when_target_not_current(mock_controller):
    """Cannot rebind to a run that is not current."""
    workspace_id = "ws1"

    workspace_mock = Mock()
    workspace_mock.last_run_id = "run2"
    mock_controller.registry.require.return_value = workspace_mock

    mock_controller.follow_up_queues.enqueue(
        workspace_id=workspace_id,
        run_id="run1",
        conversation_id="conv1",
        item={"id": "msg-1", "prompt": "Follow-up"},
        selected_agent=1,
    )

    def pause_queue(queue):
        queue["state"] = "paused"
        for item in queue.get("items", []):
            item["paused"] = True
        return True

    mock_controller.follow_up_queues.mutate(workspace_id, pause_queue)

    request = RemoteRequest(
        action="followups.rebind",
        body={"workspace_id": workspace_id, "new_run_id": "run1", "conversation_id": "conv1"},
    )

    with pytest.raises(RemoteControllerError) as exc_info:
        mock_controller._followups_rebind(request)

    assert exc_info.value.code == "follow_up_stale_target"


def test_rebind_blocked_when_target_active(mock_controller):
    """Cannot rebind to a run that is still running."""
    workspace_id = "ws1"

    workspace_mock = Mock()
    workspace_mock.last_run_id = "run_active"
    mock_controller.registry.require.return_value = workspace_mock

    mock_controller.manager.get_run = lambda run_id, **kw: {
        "id": run_id,
        "status": "running",
        "worker_active": True,
        "agents": [],
    }

    mock_controller.follow_up_queues.enqueue(
        workspace_id=workspace_id,
        run_id="run1",
        conversation_id="conv1",
        item={"id": "msg-1", "prompt": "Follow-up"},
        selected_agent=1,
    )

    def pause_queue(queue):
        queue["state"] = "paused"
        for item in queue.get("items", []):
            item["paused"] = True
        return True

    mock_controller.follow_up_queues.mutate(workspace_id, pause_queue)

    request = RemoteRequest(
        action="followups.rebind",
        body={"workspace_id": workspace_id, "new_run_id": "run_active", "conversation_id": "conv1"},
    )

    with pytest.raises(RemoteControllerError) as exc_info:
        mock_controller._followups_rebind(request)

    assert exc_info.value.code == "follow_up_target_busy"


def test_rebind_blocked_when_no_eligible_agent(mock_controller):
    """Cannot rebind to a run with no successful agents."""
    workspace_id = "ws1"

    workspace_mock = Mock()
    workspace_mock.last_run_id = "run_failed"
    mock_controller.registry.require.return_value = workspace_mock

    mock_controller.manager.get_run = lambda run_id, **kw: {
        "id": run_id,
        "status": "failed",
        "worker_active": False,
        "agents": [{"agent": 1, "status": "failed"}],
    }

    mock_controller.follow_up_queues.enqueue(
        workspace_id=workspace_id,
        run_id="run1",
        conversation_id="conv1",
        item={"id": "msg-1", "prompt": "Follow-up"},
        selected_agent=1,
    )

    def pause_queue(queue):
        queue["state"] = "paused"
        for item in queue.get("items", []):
            item["paused"] = True
        return True

    mock_controller.follow_up_queues.mutate(workspace_id, pause_queue)

    request = RemoteRequest(
        action="followups.rebind",
        body={"workspace_id": workspace_id, "new_run_id": "run_failed", "conversation_id": "conv1"},
    )

    with pytest.raises(RemoteControllerError) as exc_info:
        mock_controller._followups_rebind(request)

    assert exc_info.value.code == "follow_up_no_eligible_agent"
