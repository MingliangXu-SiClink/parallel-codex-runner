from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import pytest
from unittest.mock import patch

from parallel_codex_runner_core.remote.follow_up_queue import (
    FollowUpCommandError,
    FollowUpQueueError,
    FollowUpQueueCoordinator,
    FollowUpQueueStore,
    canonical_follow_up_settings,
)
from parallel_codex_runner_core.remote.journal import CommandJournal
from parallel_codex_runner_core.remote.protocol import RemoteRequest


def _settings(*, delay: int = 0) -> dict[str, object]:
    return {
        "agents": 4,
        "synthesis_agents": 2,
        "follow_up_delay": delay,
        "execution": "parallel",
        "automatic_max_parallel": True,
        "max_parallel": 4,
        "recommendation": "reasoning_tokens",
        "fast": "automatic",
        "synthesis_fast": "automatic",
        "sync_back": True,
    }


def _item(*, delay: int = 0) -> dict[str, object]:
    return {
        "id": "1e8d6af3-e87f-4a37-a682-114de08bc570",
        "prompt": "continue in the background",
        "settings": _settings(delay=delay),
        "attempt": 0,
    }


class _Registry:
    def __init__(self, run_id: str) -> None:
        self.record = SimpleNamespace(last_run_id=run_id)

    def require(self, _workspace_id: str) -> SimpleNamespace:
        return self.record


class _Manager:
    def __init__(self, run: dict[str, object]) -> None:
        self.runs = {str(run["run_id"]): run}

    def get_run(self, run_id: str, *, include_events: bool) -> dict[str, object]:
        del include_events
        return self.runs[run_id]


def _controller(run: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(
        registry=_Registry(str(run["run_id"])),
        manager=_Manager(run),
    )


def _completed_run() -> dict[str, object]:
    return {
        "run_id": "run-source",
        "status": "completed",
        "worker_active": False,
        "recommended_agent": 2,
        "agents": [
            {"agent": 1, "status": "success", "can_continue": True},
            {"agent": 2, "status": "success", "can_continue": True},
        ],
    }


def test_stop_pauses_durable_queue_and_invalidates_dispatch_claim(tmp_path):
    store = FollowUpQueueStore(tmp_path / "queue.json")
    store.replace(workspace_id="ws", run_id="run-source", items=[_item()], selected_agent=None, reset_countdown=False)
    old = store.get_internal("ws")
    store.pause_for_run("unrelated")
    assert store.get_internal("ws") == old
    store.pause_for_run("run-source")
    paused = FollowUpQueueStore(store.path).get_internal("ws")
    assert paused["items"][0]["paused"]
    assert not FollowUpQueueCoordinator._head_matches(paused, old, old["items"][0])
    calls = []
    coordinator = FollowUpQueueCoordinator(store, _controller(_completed_run()), lambda *a: calls.append(a))
    coordinator.process_once()
    coordinator.stop()
    assert not calls


def test_queue_edit_compare_and_swap_preserves_other_client_changes(tmp_path):
    store = FollowUpQueueStore(tmp_path / "queue.json")
    result = store.replace(workspace_id="ws", run_id="run-source", items=[_item()], selected_agent=None, reset_countdown=False)
    generation = result["queue"]["generation"]
    store.enqueue(workspace_id="ws", run_id="run-source", item={**_item(), "id": "another"}, selected_agent=None)
    for items in [[], [{**_item(), "prompt": "edited"}]]:
        with pytest.raises(FollowUpQueueError, match="queue changed"):
            store.replace(workspace_id="ws", run_id="run-source", items=items, selected_agent=None,
                          reset_countdown=True, expected_generation=generation)
    assert len(store.get_internal("ws")["items"]) == 2


def test_late_second_enqueue_follows_consumed_turn_in_same_conversation(tmp_path):
    store = FollowUpQueueStore(tmp_path / "queue.json")
    store.enqueue(workspace_id="ws", run_id="run-source", conversation_id="conversation-a",
                  item=_item(), selected_agent=None)
    def advance(queue):
        queue.update(items=[], run_id="run-next", last_completed_item_id=_item()["id"], state="idle")
        return True
    store.mutate("ws", advance)
    second = store.enqueue(workspace_id="ws", run_id="run-source", conversation_id="conversation-a",
                           item={**_item(), "id": "second"}, selected_agent=None)["queue"]
    assert second["run_id"] == "run-next"
    assert [item["id"] for item in second["items"]] == ["second"]
    with pytest.raises(FollowUpQueueError, match="conversation"):
        store.enqueue(workspace_id="ws", run_id="different-run", conversation_id="conversation-b",
                      item={**_item(), "id": "third"}, selected_agent=None)


def test_stop_does_not_retarget_already_claimed_next_turn(tmp_path):
    store = FollowUpQueueStore(tmp_path / "queue.json")
    store.replace(workspace_id="ws", run_id="run-source", items=[_item()], selected_agent=None, reset_countdown=False)
    def claim(queue):
        queue["state"] = "dispatching"
        return True
    store.mutate("ws", claim)
    with pytest.raises(FollowUpQueueError, match="already being sent"):
        store.pause_for_run("run-source")
    assert store.get_internal("ws")["state"] == "dispatching"


def test_settings_are_normalized_for_native_clients() -> None:
    settings = canonical_follow_up_settings(
        {
            "num_agents": 7,
            "serial": True,
            "fast_mode": True,
            "synthesis_fast_mode": False,
            "recommend_by": "duration",
            "runs_dir": "/tmp/runs",
            "codex_bin": "/usr/local/bin/codex",
        }
    )

    assert settings["agents"] == 7
    assert settings["execution"] == "serial"
    assert settings["fast"] == "on"
    assert settings["synthesis_fast"] == "off"
    assert settings["recommendation"] == "duration"
    assert settings["runs_directory"] == "/tmp/runs"
    assert settings["codex_executable"] == "/usr/local/bin/codex"


def test_delay_edit_reschedules_persisted_countdown_without_changing_prompts(tmp_path: Path) -> None:
    store = FollowUpQueueStore(tmp_path / "follow-ups.json")
    second = {**_item(delay=60), "id": "next", "prompt": "second"}
    store.replace(workspace_id="ws", run_id="run-source", items=[_item(delay=60), second],
                  selected_agent=2, reset_countdown=False)
    coordinator = FollowUpQueueCoordinator(store, _controller(_completed_run()),
                                          lambda *_args: {"run_id": "run-next"})
    with patch("parallel_codex_runner_core.remote.follow_up_queue.time.time", return_value=100):
        coordinator.process_once()
    old = store.get_internal("ws")
    with patch("parallel_codex_runner_core.remote.follow_up_queue.time.time", return_value=110):
        store.update_delay("ws", 120)
    queue = FollowUpQueueStore(store.path).get_internal("ws")
    assert queue["deadline"] == 230
    assert queue["state"] == "countdown"
    assert [item["settings"]["follow_up_delay"] for item in queue["items"]] == [120, 120]
    assert [item["prompt"] for item in queue["items"]] == [old["items"][0]["prompt"], "second"]
    assert not coordinator._head_matches(queue, old, old["items"][0])
    coordinator.stop()


def test_zero_delay_edit_dispatches_without_a_client_and_preserves_retry_state(tmp_path: Path) -> None:
    store = FollowUpQueueStore(tmp_path / "follow-ups.json")
    store.replace(workspace_id="ws", run_id="run-source", items=[_item(delay=60)],
                  selected_agent=2, reset_countdown=False)
    calls = []
    coordinator = FollowUpQueueCoordinator(store, _controller(_completed_run()),
                                          lambda *args: calls.append(args) or {"run_id": "run-next"})
    coordinator.process_once()
    store.update_delay("ws", 0)
    coordinator.process_once()
    assert len(calls) == 1
    assert calls[0][1]["follow_up_delay"] == 0
    assert store.get_internal("ws")["items"] == []
    coordinator.stop()


def test_delay_edit_keeps_claimed_head_and_failure_backoff_intact(tmp_path: Path) -> None:
    store = FollowUpQueueStore(tmp_path / "follow-ups.json")
    second = {**_item(delay=60), "id": "next"}
    store.replace(workspace_id="ws", run_id="run-source", items=[_item(delay=60), second],
                  selected_agent=2, reset_countdown=False)
    def claim(queue):
        queue["state"] = "dispatching"
        queue["items"][0]["dispatch"] = {"command_id": "in-flight"}
        queue["items"][1].update(paused=True, retry_at=900, failure_count=3)
        return True
    store.mutate("ws", claim)
    before = store.get_internal("ws")
    store.update_delay("ws", 5)
    after = store.get_internal("ws")
    assert after["items"][0] == before["items"][0]
    assert after["state"] == "dispatching"
    assert after["items"][1]["settings"]["follow_up_delay"] == 5
    assert after["items"][1]["paused"] is True
    assert after["items"][1]["retry_at"] == 900
    assert after["items"][1]["failure_count"] == 3


def test_completed_run_dispatches_queue_without_a_client(tmp_path: Path) -> None:
    store = FollowUpQueueStore(tmp_path / "follow-ups.json")
    store.replace(
        workspace_id="workspace-1",
        run_id="run-source",
        items=[_item()],
        selected_agent=None,
        reset_countdown=False,
    )
    controller = _controller(_completed_run())
    calls: list[tuple[str, dict[str, object], str]] = []

    def execute(
        action: str, body: dict[str, object], command_id: str
    ) -> dict[str, object]:
        calls.append((action, body, command_id))
        return {"run_id": "run-next"}

    coordinator = FollowUpQueueCoordinator(store, controller, execute)
    coordinator.process_once()
    coordinator.stop()

    assert len(calls) == 1
    assert calls[0][0] == "turns.continue"
    assert calls[0][1]["agent"] == 2
    assert calls[0][1]["prompt"] == "continue in the background"
    assert calls[0][1]["confirm_large_run"] is True
    queue = store.get("workspace-1")["queue"]
    assert queue["items"] == []
    assert queue["run_id"] == "run-next"
    assert queue["last_completed_item_id"] == _item()["id"]


def test_remaining_messages_move_to_the_new_run(tmp_path: Path) -> None:
    store = FollowUpQueueStore(tmp_path / "follow-ups.json")
    second = {
        **_item(),
        "id": "b969c1f2-3110-4c25-9295-aa48fce31416",
        "prompt": "continue once more",
    }
    store.replace(
        workspace_id="workspace-1",
        run_id="run-source",
        items=[_item(), second],
        selected_agent=None,
        reset_countdown=False,
    )
    coordinator = FollowUpQueueCoordinator(
        store,
        _controller(_completed_run()),
        lambda _action, _body, _command_id: {"run_id": "run-next"},
    )

    coordinator.process_once()
    coordinator.stop()

    queue = store.get("workspace-1")["queue"]
    assert queue["run_id"] == "run-next"
    assert [item["id"] for item in queue["items"]] == [second["id"]]
    assert queue["state"] == "waiting"


def test_synthesis_refresh_then_idle_worker_dispatches_once_without_a_client(tmp_path: Path) -> None:
    store = FollowUpQueueStore(tmp_path / "follow-ups.json")
    store.replace(workspace_id="workspace-1", run_id="run-source", items=[_item(delay=1)],
                  selected_agent=None, reset_countdown=False)
    run = _completed_run()
    run.update(status="running", worker_active=True)
    run["agents"][1].update(status="running", role="synthesis", can_continue=False)
    controller = _controller(run)
    calls = []

    def execute(action, body, command_id):
        calls.append((action, body, command_id))
        controller.registry.record.last_run_id = "run-next"
        return {"run_id": "run-next"}

    coordinator = FollowUpQueueCoordinator(store, controller, execute)
    try:
        with patch("parallel_codex_runner_core.remote.follow_up_queue.time.time", return_value=100):
            coordinator.process_once()
            assert store.get("workspace-1")["queue"]["state"] == "waiting"
            run["agents"][1].update(status="success", can_continue=True)
            coordinator.process_once()
            assert calls == []  # Worker still owns the result files.
            run.update(status="completed", worker_active=False)
            coordinator.process_once()
            assert store.get("workspace-1")["queue"]["state"] == "countdown"
        with patch("parallel_codex_runner_core.remote.follow_up_queue.time.time", return_value=101):
            coordinator.process_once()
            coordinator.process_once()
        assert len(calls) == 1
        assert calls[0][1]["agent"] == 2
        assert store.get("workspace-1")["queue"]["items"] == []
    finally:
        coordinator.stop()


def test_active_run_keeps_queue_waiting(tmp_path: Path) -> None:
    store = FollowUpQueueStore(tmp_path / "follow-ups.json")
    store.replace(
        workspace_id="workspace-1",
        run_id="run-source",
        items=[_item()],
        selected_agent=None,
        reset_countdown=False,
    )
    run = _completed_run()
    run.update(status="running", worker_active=True)
    calls: list[str] = []
    coordinator = FollowUpQueueCoordinator(
        store,
        _controller(run),
        lambda action, _body, _command_id: calls.append(action) or {},
    )

    coordinator.process_once()
    coordinator.stop()

    assert calls == []
    assert store.get("workspace-1")["queue"]["state"] == "waiting"


def test_saved_dispatch_replays_after_daemon_restart(tmp_path: Path) -> None:
    store = FollowUpQueueStore(tmp_path / "follow-ups.json")
    store.replace(
        workspace_id="workspace-1",
        run_id="run-source",
        items=[_item(delay=60)],
        selected_agent=1,
        reset_countdown=False,
    )

    def mark_dispatching(queue: dict[str, object]) -> bool:
        queue["state"] = "dispatching"
        items = queue["items"]
        assert isinstance(items, list)
        items[0]["dispatch"] = {
            "action": "turns.continue",
            "body": {
                "run_id": "run-source",
                "agent": 1,
                "prompt": "continue in the background",
            },
            "command_id": "follow-up-stable",
        }
        return True

    store.mutate("workspace-1", mark_dispatching)
    calls: list[str] = []
    coordinator = FollowUpQueueCoordinator(
        store,
        _controller(_completed_run()),
        lambda _action, _body, command_id: calls.append(command_id)
        or {"run_id": "run-next"},
    )

    coordinator.process_once()
    coordinator.stop()

    assert calls == ["follow-up-stable"]
    assert store.get("workspace-1")["queue"]["items"] == []


def test_dispatching_head_rejects_setting_or_agent_edits(tmp_path: Path) -> None:
    store = FollowUpQueueStore(tmp_path / "follow-ups.json")
    store.replace(
        workspace_id="workspace-1",
        run_id="run-source",
        items=[_item()],
        selected_agent=1,
        reset_countdown=False,
    )

    def mark_dispatching(queue: dict[str, object]) -> bool:
        queue["state"] = "dispatching"
        items = queue["items"]
        assert isinstance(items, list)
        items[0]["dispatch"] = {
            "action": "turns.continue",
            "body": {"run_id": "run-source"},
            "command_id": "follow-up-in-flight",
        }
        return True

    store.mutate("workspace-1", mark_dispatching)
    changed = _item()
    changed_settings = dict(changed["settings"])
    changed_settings["agents"] = 8
    changed["settings"] = changed_settings

    with unittest.TestCase().assertRaises(FollowUpQueueError):
        store.replace(
            workspace_id="workspace-1",
            run_id="run-source",
            items=[changed],
            selected_agent=1,
            reset_countdown=False,
        )
    with unittest.TestCase().assertRaises(FollowUpQueueError):
        store.select_agent(workspace_id="workspace-1", run_id="run-source",
                           conversation_id="", agent=2)


def test_edit_racing_with_dispatch_never_sends_stale_prompt(tmp_path: Path) -> None:
    class RacingStore(FollowUpQueueStore):
        raced = False

        def mutate(self, workspace_id, callback):
            if callback.__name__ == "claim" and not self.raced:
                self.raced = True
                edited = _item()
                edited["prompt"] = "edited before dispatch"
                self.replace(
                    workspace_id=workspace_id,
                    run_id="run-source",
                    items=[edited],
                    selected_agent=2,
                    reset_countdown=True,
                )
            return super().mutate(workspace_id, callback)

    store = RacingStore(tmp_path / "follow-ups.json")
    store.replace(
        workspace_id="workspace-1",
        run_id="run-source",
        items=[_item()],
        selected_agent=2,
        reset_countdown=False,
    )
    calls: list[str] = []
    coordinator = FollowUpQueueCoordinator(
        store,
        _controller(_completed_run()),
        lambda _action, body, _command_id: calls.append(str(body["prompt"]))
        or {"run_id": "run-next"},
    )

    coordinator.process_once()
    coordinator.stop()

    assert calls == []
    queue = store.get("workspace-1")["queue"]
    assert queue["items"][0]["prompt"] == "edited before dispatch"
    assert queue["state"] == "waiting"


def test_transient_dispatch_failure_retries_without_user_error(tmp_path: Path) -> None:
    store = FollowUpQueueStore(tmp_path / "follow-ups.json")
    store.replace(
        workspace_id="workspace-1",
        run_id="run-source",
        items=[_item()],
        selected_agent=None,
        reset_countdown=False,
    )

    def fail(
        _action: str, _body: dict[str, object], _command_id: str
    ) -> dict[str, object]:
        raise FollowUpCommandError(
            "pcr_operation_pending",
            "finalization is still finishing",
            retryable=True,
        )

    coordinator = FollowUpQueueCoordinator(store, _controller(_completed_run()), fail)
    coordinator.process_once()
    coordinator.stop()

    queue = store.get("workspace-1")["queue"]
    assert queue["state"] == "dispatching"
    assert queue["last_error"] == ""
    assert queue["items"][0]["last_error"] == ""
    assert queue["items"][0]["retry_at_epoch_ms"] is not None


def test_matching_external_follow_up_completes_without_duplicate_send(
    tmp_path: Path,
) -> None:
    store = FollowUpQueueStore(tmp_path / "follow-ups.json")
    item = _item()
    store.replace(
        workspace_id="workspace-1",
        run_id="run-source",
        items=[item],
        selected_agent=2,
        reset_countdown=False,
    )
    source = _completed_run()
    continued = {
        "run_id": "run-next",
        "status": "running",
        "worker_active": True,
        "prompt": item["prompt"],
        "continued_from_run_id": "run-source",
        "continued_from_agent": 2,
        "config": {"submission_command": CommandJournal.identity(RemoteRequest(
            action="turns.continue", client_id="pcrd-follow-up",
            command_id=f"follow-up-{item['id']}-attempt-0",
            body=FollowUpQueueCoordinator._continue_body(store.get_internal("workspace-1"), item, 2),
        ))},
        "agents": [{"agent": 1, "status": "running"}],
    }
    controller = _controller(source)
    controller.registry.record.last_run_id = "run-next"
    controller.manager.runs["run-next"] = continued
    calls: list[str] = []
    coordinator = FollowUpQueueCoordinator(
        store,
        controller,
        lambda action, _body, _command_id: calls.append(action) or {},
    )

    coordinator.process_once()
    coordinator.stop()

    queue = store.get("workspace-1")["queue"]
    assert calls == []
    assert queue["items"] == []
    assert queue["run_id"] == "run-next"
    assert queue["last_completed_item_id"] == item["id"]


def test_clear_keeps_an_authoritative_empty_record(tmp_path: Path) -> None:
    store = FollowUpQueueStore(tmp_path / "follow-ups.json")
    store.replace(
        workspace_id="workspace-1",
        run_id="run-source",
        items=[_item()],
        selected_agent=None,
        reset_countdown=False,
    )

    result = store.clear("workspace-1", run_id="run-source")

    assert result["queue"]["state"] == "idle"
    assert result["queue"]["items"] == []
    assert store.get("workspace-1")["queue"] is not None


def test_stale_client_cannot_requeue_a_completed_head(tmp_path: Path) -> None:
    store = FollowUpQueueStore(tmp_path / "follow-ups.json")
    original = _item()
    store.replace(
        workspace_id="workspace-1",
        run_id="run-source",
        items=[original],
        selected_agent=None,
        reset_countdown=False,
    )

    def complete(queue: dict[str, object]) -> bool:
        queue["items"] = []
        queue["run_id"] = "run-next"
        queue["state"] = "idle"
        queue["last_completed_item_id"] = original["id"]
        return True

    store.mutate("workspace-1", complete)
    result = store.replace(
        workspace_id="workspace-1",
        run_id="run-source",
        items=[original],
        selected_agent=None,
        reset_countdown=False,
    )

    assert result["queue"]["items"] == []
    assert result["queue"]["run_id"] == "run-next"


def test_enqueue_appends_same_conversation_without_replacing_another_client(
    tmp_path: Path,
) -> None:
    store = FollowUpQueueStore(tmp_path / "follow-ups.json")
    first = _item()
    second = {
        **_item(),
        "id": "b969c1f2-3110-4c25-9295-aa48fce31416",
        "prompt": "second client message",
    }

    store.enqueue(
        workspace_id="workspace-1",
        run_id="run-source",
        conversation_id="conversation-1",
        item=first,
        selected_agent=1,
    )
    result = store.enqueue(
        workspace_id="workspace-1",
        run_id="stale-run-id",
        conversation_id="conversation-1",
        item=second,
        selected_agent=2,
    )

    assert result["queue"]["run_id"] == "run-source"
    assert result["queue"]["selected_agent"] is None
    assert [item["id"] for item in result["queue"]["items"]] == [
        first["id"],
        second["id"],
    ]


def test_enqueue_rejects_a_different_conversation(tmp_path: Path) -> None:
    store = FollowUpQueueStore(tmp_path / "follow-ups.json")
    first = _item()
    store.enqueue(
        workspace_id="workspace-1",
        run_id="run-source",
        conversation_id="conversation-1",
        item=first,
        selected_agent=1,
    )

    with unittest.TestCase().assertRaisesRegex(
        FollowUpQueueError,
        "another conversation",
    ):
        store.enqueue(
            workspace_id="workspace-1",
            run_id="run-unrelated",
            conversation_id="conversation-2",
            item={
                **_item(),
                "id": "b969c1f2-3110-4c25-9295-aa48fce31416",
            },
            selected_agent=2,
        )

    queue = store.get("workspace-1")["queue"]
    assert queue["run_id"] == "run-source"
    assert queue["conversation_id"] == "conversation-1"
    assert [item["id"] for item in queue["items"]] == [first["id"]]


def test_replace_and_clear_reject_a_different_conversation(tmp_path: Path) -> None:
    store = FollowUpQueueStore(tmp_path / "follow-ups.json")
    store.replace(
        workspace_id="workspace-1",
        run_id="run-source",
        conversation_id="conversation-1",
        items=[_item()],
        selected_agent=1,
        reset_countdown=False,
    )

    for operation in (
        lambda: store.replace(
            workspace_id="workspace-1",
            run_id="run-unrelated",
            conversation_id="conversation-2",
            items=[_item()],
            selected_agent=2,
            reset_countdown=True,
        ),
        lambda: store.clear(
            "workspace-1",
            run_id="run-unrelated",
            conversation_id="conversation-2",
        ),
    ):
        with unittest.TestCase().assertRaises(FollowUpQueueError) as raised:
            operation()
        assert raised.exception.code == "follow_up_context_conflict"

    queue = store.get("workspace-1")["queue"]
    assert queue["run_id"] == "run-source"
    assert queue["conversation_id"] == "conversation-1"
    assert len(queue["items"]) == 1


def test_enqueue_is_idempotent_for_a_retried_client_request(tmp_path: Path) -> None:
    store = FollowUpQueueStore(tmp_path / "follow-ups.json")
    item = _item()
    first = store.enqueue(
        workspace_id="workspace-1",
        run_id="run-source",
        item=item,
        selected_agent=None,
    )
    repeated = store.enqueue(
        workspace_id="workspace-1",
        run_id="run-source",
        item=item,
        selected_agent=None,
    )

    assert repeated["revision"] == first["revision"]
    assert [queued["id"] for queued in repeated["queue"]["items"]] == [
        item["id"]
    ]


def test_concurrent_enqueue_keeps_every_clients_message(tmp_path: Path) -> None:
    store = FollowUpQueueStore(tmp_path / "follow-ups.json")

    def append(index: int) -> None:
        store.enqueue(
            workspace_id="workspace-1",
            run_id="run-source",
            item={
                **_item(),
                "id": f"00000000-0000-4000-8000-{index:012d}",
                "prompt": f"message {index}",
            },
            selected_agent=1,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(append, range(24)))

    queued = store.get("workspace-1")["queue"]["items"]
    assert len(queued) == 24
    assert {item["prompt"] for item in queued} == {
        f"message {index}" for index in range(24)
    }


class ContinuationSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = FollowUpQueueStore(Path(self.directory.name) / "queue.json")
        self.run = {**_completed_run(), "continuation_agent": 2}
        self.controller = _controller(self.run)
        self.calls = []
        self.coordinator = FollowUpQueueCoordinator(
            self.store, self.controller,
            lambda action, body, command: self.calls.append(dict(body)) or {"run_id": "run-next"},
        )
        self.addCleanup(self.coordinator.stop)
        self.store.replace(workspace_id="ws", run_id="run-source", items=[_item(delay=60)],
                           selected_agent=1, reset_countdown=False)

    def choose(self, agent: int) -> None:
        self.store.select_agent(workspace_id="ws", run_id="run-source", conversation_id="", agent=agent)

    def test_browsing_during_execution_does_not_pin_the_source(self) -> None:
        self.run.update(status="running", worker_active=True)
        self.coordinator.process_once()
        self.assertIsNone(self.store.get_internal("ws")["selected_agent"])
        self.run.update(status="completed", worker_active=False)
        self.coordinator.process_once()
        queue = self.store.get_internal("ws")
        self.assertEqual((queue["selected_agent"], queue["selection_source"]), (2, "automatic"))

    def test_legacy_countdown_is_migrated_without_trusting_an_old_view(self) -> None:
        def legacy(queue):
            queue.update(selected_agent=1, state="countdown", deadline=1, selection_source=None)
            return True
        self.store.mutate("ws", legacy)
        self.coordinator.process_once()
        queue = self.store.get_internal("ws")
        self.assertEqual(queue["selected_agent"], 2)
        self.assertGreater(queue["deadline"], 1)
        self.assertEqual(self.calls, [])

    def test_manual_override_survives_restart_and_stale_client_replace(self) -> None:
        self.coordinator.process_once()
        self.choose(1)
        self.store.replace(workspace_id="ws", run_id="run-source", items=[_item(delay=60)],
                           selected_agent=2, reset_countdown=False)
        restored = FollowUpQueueStore(self.store.path)
        self.assertEqual(restored.get_internal("ws")["selection_source"], "manual")
        self.assertEqual(self.coordinator._source_agent(restored.get_internal("ws"), self.run)["agent"], 1)
        self.store.update_delay("ws", 0)
        self.coordinator.process_once()
        self.assertEqual(self.calls[0]["agent"], 1)
        next_queue = self.store.get_internal("ws")
        self.assertIsNone(next_queue["selected_agent"])
        self.assertIsNone(next_queue["selection_source"])

    def test_manual_selection_can_precede_enqueuing_without_becoming_a_view(self) -> None:
        self.store.clear("ws", run_id="run-source")
        self.choose(1)
        self.store.enqueue(workspace_id="ws", run_id="run-source", item=_item(), selected_agent=2)
        self.coordinator.process_once()
        self.assertEqual(self.calls[0]["agent"], 1)

    def test_clearing_messages_does_not_erase_a_manual_review_choice(self) -> None:
        self.choose(1)
        self.store.clear("ws", run_id="run-source")
        self.store.enqueue(workspace_id="ws", run_id="run-source", item=_item(), selected_agent=2)
        self.coordinator.process_once()
        self.assertEqual(self.calls[0]["agent"], 1)

    def test_automatic_selection_skips_excluded_results_but_manual_choice_can_use_them(self) -> None:
        self.coordinator.process_once()
        self.run["agents"][1]["rejected"] = True
        self.assertEqual(self.coordinator._source_agent(self.store.get_internal("ws"), self.run)["agent"], 1)
        self.run["agents"][0]["rejected"] = True
        self.assertIsNone(self.coordinator._source_agent(self.store.get_internal("ws"), self.run))
        self.choose(2)
        self.assertEqual(self.coordinator._source_agent(self.store.get_internal("ws"), self.run)["agent"], 2)

    def test_finalized_result_wins_and_new_execution_resets_review_choice(self) -> None:
        self.choose(1)
        self.run["finalized_agent"] = 2
        self.assertEqual(self.coordinator._source_agent(self.store.get_internal("ws"), self.run)["agent"], 2)
        self.run.update(finalized_agent=None, status="running", worker_active=True)
        self.coordinator.process_once()
        self.assertIsNone(self.store.get_internal("ws")["selection_source"])
        self.run.update(status="completed", worker_active=False)
        self.coordinator.process_once()
        self.assertEqual(self.store.get_internal("ws")["selected_agent"], 2)

    def test_explicit_endpoint_rejects_running_stale_or_finalized_other_branch(self) -> None:
        from parallel_codex_runner_core.remote.controller import RemoteController, RemoteControllerError
        from parallel_codex_runner_core.remote.protocol import RemoteRequest
        controller = object.__new__(RemoteController)
        controller.follow_up_queues = self.store
        controller.manager = self.controller.manager
        controller.registry = self.controller.registry
        controller.registry.record.path = self.directory.name
        self.run["workspace"] = self.directory.name
        request = RemoteRequest("followups.select_agent", {"workspace_id": "ws", "run_id": "run-source", "agent": 1})
        self.run.update(status="running", worker_active=True)
        with self.assertRaises(RemoteControllerError):
            controller._followups_select_agent(request)
        self.run.update(status="completed", worker_active=False, finalized_agent=2)
        with self.assertRaises(RemoteControllerError):
            controller._followups_select_agent(request)
        self.run["finalized_agent"] = None
        controller.registry.record.last_run_id = "new-run"
        with self.assertRaises(RemoteControllerError):
            controller._followups_select_agent(request)
        controller.registry.record.last_run_id = "run-source"
        self.run["agents"][1]["status"] = "running"
        with self.assertRaises(RemoteControllerError):
            controller._followups_select_agent(request)
        self.run["agents"][1]["status"] = "success"
        response = controller._followups_select_agent(request)
        self.assertEqual(response["queue"]["selection_source"], "manual")
        self.assertEqual(response["queue"]["selected_agent"], 1)


class FollowUpQueueTests(unittest.TestCase):
    def test_retry_preserves_claim_and_does_not_restart_countdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "queue.json"
            store = FollowUpQueueStore(path)
            store.replace(workspace_id="workspace-1", run_id="run-source",
                          items=[_item(delay=60)], selected_agent=None, reset_countdown=False)
            controller = _controller(_completed_run())
            calls = []

            def execute(action, body, command_id):
                calls.append((action, dict(body), command_id))
                if len(calls) == 1:
                    raise FollowUpCommandError("pcr_operation_pending", "saving", retryable=True)
                return {"run_id": "run-next"}

            coordinator = FollowUpQueueCoordinator(store, controller, execute)
            with patch("parallel_codex_runner_core.remote.follow_up_queue.time.time", return_value=100):
                coordinator.process_once()
            with patch("parallel_codex_runner_core.remote.follow_up_queue.time.time", return_value=160):
                coordinator.process_once()
            claimed = store.get_internal("workspace-1")
            self.assertEqual(claimed["state"], "dispatching")
            self.assertEqual(claimed["items"][0]["dispatch"]["body"], calls[0][1])
            coordinator.stop()

            store = FollowUpQueueStore(path)
            restarted = FollowUpQueueCoordinator(store, controller, execute)
            source = controller.manager.runs["run-source"]
            source.update(status="finalizing", worker_active=True)
            with patch("parallel_codex_runner_core.remote.follow_up_queue.time.time", return_value=166):
                restarted.process_once()
            self.assertEqual(len(calls), 1)
            self.assertEqual(store.get_internal("workspace-1")["state"], "dispatching")
            source.update(status="finalized", worker_active=False, finalized_agent=2)
            with patch("parallel_codex_runner_core.remote.follow_up_queue.time.time", return_value=170):
                restarted.process_once()
            restarted.stop()
            self.assertEqual(calls, [calls[0], calls[0]])
            self.assertEqual(store.get_internal("workspace-1")["items"], [])

    def test_same_text_in_another_command_is_not_acceptance_proof(self) -> None:
        for matching in (False, True):
            with self.subTest(matching=matching), tempfile.TemporaryDirectory() as directory:
                store = FollowUpQueueStore(Path(directory) / "queue.json")
                store.replace(workspace_id="workspace-1", run_id="run-source",
                              items=[_item()], selected_agent=None, reset_countdown=False)
                controller = _controller(_completed_run())
                calls = []

                def execute(action, body, command_id):
                    calls.append(command_id)
                    request = RemoteRequest(action=action, body=dict(body),
                        command_id=command_id if matching else "another-message", client_id="pcrd-follow-up")
                    controller.manager.runs["run-next"] = dict(_completed_run(), run_id="run-next",
                        continued_from_run_id="run-source", continued_from_agent=body["agent"],
                        prompt=body["prompt"], config={"submission_command": CommandJournal.identity(request)})
                    controller.registry.record.last_run_id = "run-next"
                    raise FollowUpCommandError("relay_timeout", "response lost", retryable=True)

                coordinator = FollowUpQueueCoordinator(store, controller, execute)
                coordinator.process_once()
                coordinator.stop()
                queue = store.get_internal("workspace-1")
                self.assertEqual(len(calls), 1)
                self.assertEqual(queue["items"] == [], matching)
                if not matching:
                    self.assertTrue(queue["items"][0]["paused"])

    def test_settings_are_normalized_for_native_clients(self) -> None:
        test_settings_are_normalized_for_native_clients()

    def _with_temp_path(self, test: object) -> None:
        with tempfile.TemporaryDirectory() as directory:
            test(Path(directory))  # type: ignore[operator]

    def test_completed_run_dispatches_queue_without_a_client(self) -> None:
        self._with_temp_path(test_completed_run_dispatches_queue_without_a_client)

    def test_active_run_keeps_queue_waiting(self) -> None:
        self._with_temp_path(test_active_run_keeps_queue_waiting)

    def test_remaining_messages_move_to_the_new_run(self) -> None:
        self._with_temp_path(test_remaining_messages_move_to_the_new_run)

    def test_saved_dispatch_replays_after_daemon_restart(self) -> None:
        self._with_temp_path(test_saved_dispatch_replays_after_daemon_restart)

    def test_dispatching_head_rejects_setting_or_agent_edits(self) -> None:
        self._with_temp_path(
            test_dispatching_head_rejects_setting_or_agent_edits
        )

    def test_edit_racing_with_dispatch_never_sends_stale_prompt(self) -> None:
        self._with_temp_path(
            test_edit_racing_with_dispatch_never_sends_stale_prompt
        )

    def test_transient_dispatch_failure_retries_without_user_error(self) -> None:
        self._with_temp_path(test_transient_dispatch_failure_retries_without_user_error)

    def test_matching_external_follow_up_completes_without_duplicate_send(self) -> None:
        self._with_temp_path(
            test_matching_external_follow_up_completes_without_duplicate_send
        )

    def test_clear_keeps_an_authoritative_empty_record(self) -> None:
        self._with_temp_path(test_clear_keeps_an_authoritative_empty_record)

    def test_stale_client_cannot_requeue_a_completed_head(self) -> None:
        self._with_temp_path(test_stale_client_cannot_requeue_a_completed_head)

    def test_enqueue_appends_same_conversation_without_replacing_another_client(
        self,
    ) -> None:
        self._with_temp_path(
            test_enqueue_appends_same_conversation_without_replacing_another_client
        )

    def test_enqueue_rejects_a_different_conversation(self) -> None:
        self._with_temp_path(test_enqueue_rejects_a_different_conversation)

    def test_replace_and_clear_reject_a_different_conversation(self) -> None:
        self._with_temp_path(test_replace_and_clear_reject_a_different_conversation)

    def test_enqueue_is_idempotent_for_a_retried_client_request(self) -> None:
        self._with_temp_path(test_enqueue_is_idempotent_for_a_retried_client_request)

    def test_concurrent_enqueue_keeps_every_clients_message(self) -> None:
        self._with_temp_path(test_concurrent_enqueue_keeps_every_clients_message)
