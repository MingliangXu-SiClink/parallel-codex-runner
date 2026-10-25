from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import pytest

from parallel_codex_runner_core.remote.controller import RemoteController
from parallel_codex_runner_core.remote.follow_up_queue import FollowUpQueueCoordinator
from parallel_codex_runner_core.remote.ipc import PcrdServer
from parallel_codex_runner_core.remote.protocol import RemoteRequest
from .test_remote import FakeRunManager


@pytest.fixture
def queue_context(tmp_path):
    temporary = TemporaryDirectory(prefix="pcr-queue-", dir="/tmp")
    tmp_path = Path(temporary.name)
    workspace = tmp_path / "project"
    workspace.mkdir()
    manager = FakeRunManager()
    runs = {}
    for name, parent in (("a", ""), ("b", "a"), ("d", "b")):
        runs[name] = {"run_id": name, "workspace": str(workspace), "conversation_id": "conversation",
                      "continued_from_run_id": parent, "status": "finalized", "worker_active": False,
                      "finalized_agent": 7, "recommended_agent": 7,
                      "agents": [{"agent": 7, "status": "success", "can_continue": True}]}
    runs["d"].update(status="running", worker_active=True, finalized_agent=None, recommended_agent=8,
                     agents=[{"agent": i, "status": "success" if i < 6 else "running",
                              "role": "candidate" if i < 6 else "synthesis", "can_continue": True}
                             for i in range(1, 9)])
    manager.get_run = Mock(side_effect=lambda run_id, **_: deepcopy(runs[run_id]))
    controller = RemoteController(state_dir=tmp_path / "state", manager=manager)
    workspace_id = controller.registry.register(str(workspace))["workspace"]["workspace_id"]
    controller.registry.update_context(workspace_id, run_id="d")
    store = controller.follow_up_queues
    old = {"id": "consumed", "prompt": "previous question", "settings": {"follow_up_delay": 0}}
    store.enqueue(workspace_id=workspace_id, run_id="a", conversation_id="conversation", item=old,
                  selected_agent=None)
    def empty(queue):
        queue.update(items=[], last_completed_item_id="consumed", state="idle", selected_agent=7,
                     selection_source="manual")
        return True
    store.mutate(workspace_id, empty)
    request = RemoteRequest(action="followups.enqueue", client_id="fixture-mac",
                            command_id="follow-up-enqueue-original",
                            body={"workspace_id": workspace_id, "run_id": "d",
                                  "item": {"id": "original", "prompt": "Keep the original question",
                                           "attempt": 0, "settings": {"follow_up_delay": 0}}})
    try:
        yield controller, runs, workspace_id, request, tmp_path
    finally:
        controller.close()
        temporary.cleanup()


def test_new_enqueue_does_not_inherit_consumed_turn_or_selected_agent(queue_context):
    controller, runs, workspace_id, request, _ = queue_context
    queue = controller.dispatch(request)["queue"]
    assert queue["run_id"] == "d"
    assert queue["items"][0]["enqueued_run_id"] == "d"
    assert queue["selected_agent"] is queue["selection_source"] is None
    execute = Mock(return_value={"run_id": "next"})
    coordinator = FollowUpQueueCoordinator(controller.follow_up_queues, controller, execute)
    for status, worker_active in (("running", True), ("completed", True), ("finalizing", False)):
        runs["d"].update(status=status, worker_active=worker_active)
        coordinator.process_workspace(workspace_id)
        waiting = controller.follow_up_queues.get_internal(workspace_id)
        assert waiting["state"] == "waiting" and not waiting["items"][0]["paused"]
        execute.assert_not_called()
    runs["d"].update(status="finalized", worker_active=False, finalized_agent=8)
    for agent in runs["d"]["agents"]:
        agent["status"] = "success"
    coordinator.process_workspace(workspace_id)
    coordinator.process_workspace(workspace_id)
    execute.assert_called_once()
    assert execute.call_args.args[1]["run_id"] == "d"
    assert execute.call_args.args[1]["agent"] == 8
    assert execute.call_args.args[2] == "follow-up-original-attempt-0"
    assert controller.follow_up_queues.get_internal(workspace_id)["items"] == []
    coordinator.stop()


def test_late_enqueue_cannot_regress_an_already_consumed_turn(queue_context):
    controller, _, workspace_id, request, _ = queue_context
    controller.follow_up_queues.mutate(workspace_id, lambda queue: queue.update(run_id="d") or True)
    late = RemoteRequest(action="followups.enqueue", body={**request.body, "run_id": "a"})
    assert controller.dispatch(late)["queue"]["run_id"] == "d"


def test_pending_messages_keep_their_original_wait_anchor(queue_context):
    controller, _, workspace_id, request, _ = queue_context
    controller.follow_up_queues.enqueue(workspace_id=workspace_id, run_id="a", conversation_id="conversation",
                                       item={"id": "pending", "prompt": "Do not retarget this"}, selected_agent=None)
    queue = controller.dispatch(request)["queue"]
    assert queue["run_id"] == "a" and len(queue["items"]) == 2


@pytest.mark.parametrize("broken", ["conversation", "workspace", "cycle", "missing"])
def test_unproven_lineage_does_not_move_the_queue(queue_context, broken):
    controller, runs, workspace_id, request, _ = queue_context
    if broken == "conversation":
        runs["b"]["conversation_id"] = "other"
    elif broken == "workspace":
        runs["b"]["workspace"] = "/different-workspace"
    elif broken == "cycle":
        runs["b"]["continued_from_run_id"] = "d"
    else:
        runs.pop("b")
    assert controller.dispatch(request)["queue"]["run_id"] == "a"


def admit_legacy_pause(controller, workspace_id, request, tmp_path):
    server = PcrdServer(tmp_path / "socket", controller)
    with patch.object(controller, "_follow_up_ancestors", return_value=frozenset()):
        response = server.execute(request.to_mapping())
    assert response["ok"] and response["body"]["queue"]["run_id"] == "a"
    # Old persisted items did not yet contain admission provenance.
    controller.follow_up_queues.mutate(workspace_id, lambda queue: (
        queue["items"][0].pop("enqueued_run_id", None) is not None))
    # The fixture migration must not introduce an extra generation.
    admitted = response["body"]["queue"]["generation"]
    controller.follow_up_queues.mutate(workspace_id, lambda queue: (
        queue.update(generation=admitted - 1) or True))
    execute = Mock()
    coordinator = FollowUpQueueCoordinator(controller.follow_up_queues, controller, execute)
    coordinator.process_workspace(workspace_id)
    execute.assert_not_called()
    coordinator.stop()
    assert controller.follow_up_queues.get_internal(workspace_id)["items"][0]["paused"]
    return server


def reconcile_request(original, *, command_id="reconcile-original"):
    return RemoteRequest(action="followups.reconcile_enqueue", client_id=original.client_id,
                         command_id=command_id,
                         body={"enqueue_command_id": original.command_id, "enqueue": original.body})


def test_original_receipt_repairs_only_the_wait_binding_and_keeps_identity(queue_context):
    controller, _, workspace_id, request, tmp_path = queue_context
    server = admit_legacy_pause(controller, workspace_id, request, tmp_path)
    try:
        before = controller.follow_up_queues.get_internal(workspace_id)
        recovered = server.execute(reconcile_request(request).to_mapping())
        assert recovered["ok"]
        queue = recovered["body"]["queue"]
        assert queue["run_id"] == "d" and queue["state"] == "waiting"
        assert queue["selected_agent"] is queue["selection_source"] is None
        assert queue["items"][0]["id"] == before["items"][0]["id"]
        assert queue["items"][0]["attempt"] == 0 and not queue["items"][0]["paused"]
        assert not controller.manager.started and not controller.manager.continued
        assert server.execute(reconcile_request(request).to_mapping())["body"]["queue"] == queue
        controller.follow_up_queues.clear(workspace_id, run_id="d", conversation_id="conversation")
        assert not server.execute(reconcile_request(request).to_mapping())["body"]["queue"]["items"]
        assert not controller.follow_up_queues.get_internal(workspace_id)["items"]
    finally:
        server.server_close()


@pytest.mark.parametrize("changed", ["fingerprint", "edited", "stopped", "dispatch", "other_turn", "extra_item"])
def test_legacy_recovery_does_not_override_user_or_execution_state(queue_context, changed):
    controller, _, workspace_id, request, tmp_path = queue_context
    server = admit_legacy_pause(controller, workspace_id, request, tmp_path)
    try:
        replay = request
        if changed == "fingerprint":
            replay = RemoteRequest(action=request.action, client_id=request.client_id, command_id=request.command_id,
                                   body={**request.body, "item": {**request.body["item"], "prompt": "Different"}})
        elif changed == "other_turn":
            controller.registry.update_context(workspace_id, run_id="b")
        else:
            def update(queue):
                if changed == "edited":
                    queue["items"][0]["prompt"] = "Changed by the user"
                elif changed == "stopped":
                    queue["items"][0]["last_error"] = "Paused by the user"
                elif changed == "dispatch":
                    queue["items"][0]["dispatch"] = {"command_id": "already-claimed"}
                else:
                    queue["items"].append({"id": "another", "prompt": "Another intent"})
                return True
            controller.follow_up_queues.mutate(workspace_id, update)
        before = controller.follow_up_queues.get_internal(workspace_id)
        response = server.execute(reconcile_request(replay).to_mapping())
        if changed == "fingerprint":
            assert not response["ok"] and response["error"]["code"] == "command_conflict"
        else:
            assert response["ok"]
        assert controller.follow_up_queues.get_internal(workspace_id) == before
    finally:
        server.server_close()


@pytest.mark.parametrize("missing", ["receipt", "item", "client"])
def test_reconciliation_never_recreates_a_missing_receipt_or_message(queue_context, missing):
    controller, _, workspace_id, request, tmp_path = queue_context
    server = admit_legacy_pause(controller, workspace_id, request, tmp_path)
    try:
        if missing == "receipt":
            (server.journal.directory / f"{server.journal._key(request)}.json").unlink()
        elif missing == "item":
            controller.follow_up_queues.clear(workspace_id, run_id="a", conversation_id="conversation")
        repair = reconcile_request(request)
        if missing == "client":
            repair = RemoteRequest(action=repair.action, command_id=repair.command_id,
                                   client_id="another-client", body=repair.body)
        before = controller.follow_up_queues.get_internal(workspace_id)
        response = server.execute(repair.to_mapping())
        if missing != "item":
            assert not response["ok"] and response["error"]["code"] == "follow_up_receipt_unavailable"
        else:
            assert response["ok"] and not response["body"]["queue"]["items"]
        assert controller.follow_up_queues.get_internal(workspace_id) == before
        assert not controller.manager.started and not controller.manager.continued
    finally:
        server.server_close()


def test_item_admission_provenance_cannot_be_forged_by_enqueue_or_edits(queue_context):
    controller, _, workspace_id, request, _ = queue_context
    item = {**request.body["item"], "enqueued_run_id": "forged"}
    queue = controller.dispatch(RemoteRequest(action=request.action, body={**request.body, "item": item}))["queue"]
    assert queue["items"][0]["enqueued_run_id"] == "d"
    edited = {**queue["items"][0], "prompt": "Edited", "enqueued_run_id": "forged"}
    result = controller.follow_up_queues.replace(workspace_id=workspace_id, run_id="d",
        conversation_id="conversation", items=[edited], selected_agent=None, reset_countdown=True)
    assert result["queue"]["items"][0]["enqueued_run_id"] == "d"
